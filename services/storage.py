import logging
from datetime import datetime
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.crawl_models import DateConfidence, NewsCategory, Tier
from db.models import Article, Chunk

logger = logging.getLogger(__name__)


async def exists(session: AsyncSession, *, url: str, content_hash: str) -> bool:
    """Kiểm tra trước khi gọi LLM phân loại — tránh tốn tiền API cho bài đã lưu."""
    stmt = (
        select(Article.id)
        .where((Article.url == url) | (Article.content_hash == content_hash))
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.first() is not None


async def insert_article(
    session: AsyncSession,
    *,
    url: str,
    source_domain: str,
    tier: Tier,
    title: Optional[str],
    content: str,
    content_hash: str,
    published_at: Optional[datetime],
    date_confidence: DateConfidence,
    is_relevant: bool,
    category: List[NewsCategory],
) -> Optional[int]:
    """
    Insert 1 bài viết. `ON CONFLICT (url) DO NOTHING RETURNING id` là lớp
    bảo vệ atomic thứ 2 (chống race condition khi nhiều crawler chạy song
    song) — lớp thứ nhất là exists() gọi trước khi tốn tiền phân loại.
    content_hash có unique constraint riêng (không nằm trong ON CONFLICT
    target) để bắt trường hợp 2 URL khác nhau nhưng nội dung giống hệt —
    vi phạm constraint đó raise IntegrityError, bắt riêng ở đây.

    Trả về id nếu insert thành công, None nếu bị conflict (đã tồn tại).
    """
    stmt = (
        pg_insert(Article)
        .values(
            url=url,
            source=source_domain,
            source_tier=tier,
            title=title,
            content=content,
            content_hash=content_hash,
            published_at=published_at,
            date_confidence=date_confidence,
            is_relevant=is_relevant,
            category=[c.value for c in category],
        )
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(Article.id)
    )
    try:
        result = await session.execute(stmt)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.info("[DUP] content_hash đã tồn tại (url khác), bỏ qua: %s", url)
        return None

    row = result.first()
    if row is None:
        logger.info("[DUP] Bỏ qua insert, đã tồn tại: %s", url)
        return None
    return row[0]


async def insert_chunks(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: int,
    chunks: Sequence[str],
    embeddings: Sequence[Sequence[float]],
) -> None:
    """Insert các chunk (+ embedding) của 1 bài viết/report. Bỏ qua chunk
    nào đã tồn tại theo (source_type, source_id, chunk_index) — idempotent
    khi chạy lại. Không tự commit/rollback — người gọi (pipeline) chịu
    trách nhiệm, vì bước này thường nối tiếp ngay sau insert_article trong
    cùng 1 transaction logic."""
    if not chunks:
        return
    rows = [
        {
            "source_type": source_type,
            "source_id": source_id,
            "chunk_index": idx,
            "content": chunk_content,
            "embedding": embedding,
        }
        for idx, (chunk_content, embedding) in enumerate(zip(chunks, embeddings))
    ]
    stmt = (
        pg_insert(Chunk)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["source_type", "source_id", "chunk_index"])
    )
    await session.execute(stmt)
    await session.commit()
