"""Pub/sub in-process cho Hot News real-time.

Crawl (`scripts/run_daily_crawl.py`) chạy như 1 process/cron job RIÊNG BIỆT với
API server — không thể gọi thẳng hàm Python để "đẩy" thông báo. Thay vào đó,
pipeline bắn 1 Postgres NOTIFY ngay trong transaction lưu bài hot news (xem
`pipeline/crawl_pipeline.py`); Postgres đảm bảo NOTIFY chỉ thực sự gửi đi nếu
transaction đó COMMIT thành công — insert rollback thì NOTIFY cũng tự huỷ theo,
không cần xử lý bù trừ thủ công.

API server giữ 1 kết nối asyncpg LISTEN kênh này (khởi tạo lúc startup), nhận
được NOTIFY thì fan-out cho mọi client SSE đang mở (mỗi tab trình duyệt là 1
subscriber, không phải polling định kỳ).
"""
import asyncio
import logging
from typing import Optional, Set

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

HOT_NEWS_CHANNEL = "hot_news"

_subscribers: Set[asyncio.Queue] = set()
_listener_conn: Optional[asyncpg.Connection] = None


def _to_asyncpg_dsn(database_url: str) -> str:
    """SQLAlchemy dùng 'postgresql+asyncpg://...', asyncpg.connect() cần scheme trần 'postgresql://...'."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _on_notify(connection, pid, channel, payload: str) -> None:
    for queue in list(_subscribers):
        queue.put_nowait(payload)


async def start_listening(database_url: str) -> None:
    """Gọi 1 lần lúc API server khởi động (xem lifespan trong api/main.py)."""
    global _listener_conn
    if _listener_conn is not None:
        return
    _listener_conn = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    await _listener_conn.add_listener(HOT_NEWS_CHANNEL, _on_notify)
    logger.info("[HOT-NEWS] Đang lắng nghe Postgres NOTIFY kênh '%s'.", HOT_NEWS_CHANNEL)


async def stop_listening() -> None:
    global _listener_conn
    if _listener_conn is not None:
        await _listener_conn.close()
        _listener_conn = None


def subscribe() -> asyncio.Queue:
    """Gọi khi 1 client mở kết nối SSE mới — trả về queue riêng của client đó."""
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    """Gọi khi client SSE ngắt kết nối — luôn đặt trong `finally`."""
    _subscribers.discard(queue)


async def notify_hot_news(session: AsyncSession, article_id: int) -> None:
    """Bắn NOTIFY — PHẢI gọi bên trong CÙNG transaction với insert bài viết
    (trước khi `session.begin()` commit). Postgres chỉ gửi NOTIFY tới các
    LISTENer khác sau khi transaction phát ra nó COMMIT thành công, nên nếu
    insert rollback thì NOTIFY cũng tự huỷ theo — không tự bắn sai khi lỗi."""
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": HOT_NEWS_CHANNEL, "payload": str(article_id)},
    )
