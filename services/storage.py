import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Set

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.crawl_models import DateConfidence, NewsTopic, Tier
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


async def load_recent_urls(session: AsyncSession, days: int = 7) -> Set[str]:
    """Load tập URL đã crawl trong `days` ngày gần nhất.

    Dùng để seed seen_urls khi khởi động, tránh fetch lại HTML của bài đã biết.
    Với lịch chạy hàng ngày, 7 ngày đủ để bỏ qua toàn bộ bài cũ trong listing page
    (thường hiển thị 1–4 tuần gần nhất).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(Article.url).where(Article.crawled_at >= cutoff)
    result = await session.execute(stmt)
    urls = {row[0] for row in result.fetchall()}
    logger.info("[SEEN-URLS] Load %d URL đã biết từ DB (%d ngày gần nhất)", len(urls), days)
    return urls


async def load_recent_content_hashes(session: AsyncSession, days: int = 7) -> Set[str]:
    """Load tập content_hash đã crawl trong `days` ngày gần nhất.
    
    Dùng để check trùng lặp in-memory (O(1)) thay vì gọi query DB cho từng bài viết.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(Article.content_hash).where(Article.crawled_at >= cutoff)
    result = await session.execute(stmt)
    hashes = {row[0] for row in result.fetchall() if row[0] is not None}
    logger.info("[SEEN-HASHES] Load %d content_hash đã biết từ DB (%d ngày gần nhất)", len(hashes), days)
    return hashes


async def insert_article_no_commit(
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
    topics: List[NewsTopic],
    is_hot_news: bool = False,
    hot_news_reason: Optional[str] = None,
    region: str = "international",
) -> Optional[int]:
    """Insert 1 bài viết — KHÔNG tự commit/rollback, người gọi chịu trách nhiệm transaction.

    Dùng trong cặp với `insert_chunks_no_commit` bên trong `async with session.begin()` để
    đảm bảo atomicity: nếu chunk/embed lỗi thì cả article cũng bị rollback.

    Trả về id nếu insert thành công, None nếu đã tồn tại (ON CONFLICT DO NOTHING).
    IntegrityError cho content_hash unique constraint được để propagate lên caller.
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
            topic=[t.value for t in topics],
            is_hot_news=is_hot_news,
            hot_news_reason=hot_news_reason,
            region=region,
        )
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(Article.id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        logger.info("[DUP] Bỏ qua insert, đã tồn tại (url): %s", url)
        return None
    return row[0]


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
    topics: List[NewsTopic],
    is_hot_news: bool = False,
    hot_news_reason: Optional[str] = None,
    region: str = "international",
) -> Optional[int]:
    """Insert 1 bài viết và tự commit.

    Wrapper của insert_article_no_commit + commit, giữ cho backward-compat
    với process_url() và các caller không cần ghép với insert_chunks.
    """
    try:
        article_id = await insert_article_no_commit(
            session,
            url=url,
            source_domain=source_domain,
            tier=tier,
            title=title,
            content=content,
            content_hash=content_hash,
            published_at=published_at,
            date_confidence=date_confidence,
            is_relevant=is_relevant,
            topics=topics,
            is_hot_news=is_hot_news,
            hot_news_reason=hot_news_reason,
            region=region,
        )
        await session.commit()
        return article_id
    except IntegrityError:
        await session.rollback()
        logger.info("[DUP] content_hash đã tồn tại (url khác), bỏ qua: %s", url)
        return None


async def insert_chunks_no_commit(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: int,
    chunks: Sequence[str],
    embeddings: Sequence[Sequence[float]],
) -> None:
    """Insert các chunk (+ embedding) — KHÔNG tự commit, người gọi chịu trách nhiệm transaction."""
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


async def insert_chunks(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: int,
    chunks: Sequence[str],
    embeddings: Sequence[Sequence[float]],
) -> None:
    """Insert các chunk (+ embedding) và commit. Backward-compat wrapper."""
    await insert_chunks_no_commit(
        session,
        source_type=source_type,
        source_id=source_id,
        chunks=chunks,
        embeddings=embeddings,
    )
    await session.commit()
