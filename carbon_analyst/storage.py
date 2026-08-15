"""
Lưu trữ bài viết đã xử lý vào bảng `news` trong Postgres, dùng asyncpg (async,
khớp với phần còn lại của codebase vốn asyncio-first).

Schema chuẩn nằm ở db/schema.sql (dùng cho production migration). DDL ở đây
là bản inline giống hệt, chạy idempotent qua ensure_schema() để dev/test tự
khởi tạo DB mà không cần chạy `psql -f` thủ công.
"""
import logging
from datetime import datetime
from typing import Optional

import asyncpg

from carbon_analyst.models import NewsCategory, Tier

logger = logging.getLogger(__name__)

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS news (
    id BIGSERIAL PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    source_domain TEXT NOT NULL,
    tier CHAR(1) NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    published_at TIMESTAMPTZ,
    category TEXT NOT NULL CHECK (category IN ('energy_fossil_fuels', 'carbon_credits', 'policy')),
    category_confidence REAL,
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS news_content_hash_idx ON news (content_hash);
CREATE INDEX IF NOT EXISTS news_category_idx ON news (category);
CREATE INDEX IF NOT EXISTS news_published_at_idx ON news (published_at DESC);
"""


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_DDL)


async def exists(pool: asyncpg.Pool, url: str, content_hash: str) -> bool:
    """Kiểm tra trước khi gọi LLM phân loại — tránh tốn tiền API cho bài đã lưu."""
    row = await pool.fetchrow(
        "SELECT 1 FROM news WHERE url = $1 OR content_hash = $2 LIMIT 1",
        url, content_hash,
    )
    return row is not None


async def insert_news(
    pool: asyncpg.Pool,
    *,
    url: str,
    source_domain: str,
    tier: Tier,
    title: Optional[str],
    content: str,
    content_hash: str,
    published_at: Optional[datetime],
    category: NewsCategory,
    category_confidence: float,
) -> Optional[int]:
    """
    Insert 1 bài viết. Dùng ON CONFLICT DO NOTHING trên (url) và unique index
    trên content_hash làm lớp bảo vệ chống trùng thứ 2 (atomic, chống race
    condition khi nhiều crawler chạy song song) — lớp thứ nhất là exists()
    gọi trước khi tốn tiền phân loại.

    Trả về id nếu insert thành công, None nếu bị conflict (đã tồn tại).
    """
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO news (
                url, source_domain, tier, title, content, content_hash,
                published_at, category, category_confidence
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
            """,
            url, source_domain, tier, title, content, content_hash,
            published_at, category.value, category_confidence,
        )
    except asyncpg.UniqueViolationError:
        # url mới nhưng content_hash trùng với bài đã lưu (vd nguồn khác đăng
        # lại y hệt) — ON CONFLICT (url) không bắt được trường hợp này vì
        # unique index trên content_hash là một constraint riêng.
        logger.info("[DUP] content_hash đã tồn tại (url khác), bỏ qua: %s", url)
        return None

    if row is None:
        logger.info("[DUP] Bỏ qua insert, đã tồn tại: %s", url)
        return None
    return row["id"]
