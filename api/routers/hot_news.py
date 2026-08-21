"""Chuông thông báo Hot News trên header.

- GET /hot: danh sách hot news gần nhất — dùng để nạp danh sách ban đầu khi FE
  vừa mở trang (trước khi có event SSE nào tới).
- GET /hot/stream: SSE — chỉ nhận được event khi crawl pipeline THỰC SỰ phát
  hiện + lưu 1 bài is_hot_news=true (Postgres NOTIFY, xem
  services/hot_news_broadcast.py và pipeline/crawl_pipeline.py). KHÔNG polling
  định kỳ — vì crawl chạy theo lịch cron riêng biệt với API, phần lớn thời gian
  sẽ không có gì mới, polling liên tục là lãng phí.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import async_session_maker, get_current_user, get_db
from db.models import Article
from schemas.news_models import HotNewsItem, HotNewsResponse
from services.hot_news_broadcast import subscribe, unsubscribe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news", tags=["hot-news"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
KEEPALIVE_SECONDS = 15  # giữ kết nối SSE sống qua các proxy/load balancer có idle timeout


def _to_item(a: Article) -> HotNewsItem:
    return HotNewsItem(
        id=a.id,
        title=a.title,
        url=a.url,
        source=a.source,
        hot_news_reason=a.hot_news_reason,
        published_at=a.published_at,
        crawled_at=a.crawled_at,
    )


@router.get("/hot", response_model=HotNewsResponse)
async def get_hot_news(
    limit: int = DEFAULT_LIMIT,
    session: AsyncSession = Depends(get_db),
    _payload: dict = Depends(get_current_user),
):
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = (
        select(Article)
        .where(Article.is_hot_news.is_(True))
        .order_by(desc(Article.crawled_at))
        .limit(limit)
    )
    result = await session.execute(stmt)
    articles = result.scalars().all()
    return HotNewsResponse(items=[_to_item(a) for a in articles])


@router.get("/hot/stream")
async def stream_hot_news(
    _payload: dict = Depends(get_current_user),
):
    """SSE — mỗi tab trình duyệt là 1 subscriber (services/hot_news_broadcast.py).

    KHÔNG tự gọi `request.is_disconnected()` — `StreamingResponse` của Starlette
    đã tự chạy 1 task riêng lắng nghe disconnect qua ASGI `receive()` và huỷ
    generator này khi client ngắt kết nối; gọi thêm `is_disconnected()` ở đây sẽ
    đọc trùng cùng 1 kênh `receive()` với task đó, gây deadlock (đã bị dính khi
    test) — cứ để `finally` lo việc dọn dẹp subscriber là đủ.

    Không dùng `Depends(get_db)` vì kết nối này sống rất lâu (cả phiên làm việc)
    — giữ 1 AsyncSession suốt thời gian đó sẽ chiếm 1 connection trong pool
    không cần thiết; thay vào đó mở session ngắn hạn mỗi khi có event mới tới.
    """
    queue = subscribe()

    async def event_stream():
        try:
            while True:
                try:
                    article_id_str = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                async with async_session_maker() as session:
                    article = await session.get(Article, int(article_id_str))
                if article is None or not article.is_hot_news:
                    continue

                yield f"event: hot_news\ndata: {_to_item(article).model_dump_json()}\n\n"
        finally:
            unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
