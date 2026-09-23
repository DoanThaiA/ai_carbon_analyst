"""
Gửi thủ công email digest Hot News (bình thường tự chạy cuối mỗi đợt crawl —
xem main.py::main và services/hot_news_email.py). Dùng để test SMTP/template,
hoặc gửi bù khi đợt crawl trước gửi lỗi.

Usage:
    python -m scripts.send_hot_news_digest --dry-run    # chỉ liệt kê + ghi preview HTML, KHÔNG gửi
    python -m scripts.send_hot_news_digest              # gửi thật + đánh dấu hot_news_emailed_at
"""
import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core.config import Settings
from db.models import Article, User
from db.session import build_sessionmaker, create_engine
from services.email_sender import build_hot_news_digest_message
from services.hot_news_email import send_pending_hot_news_digest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _dry_run(session_factory, settings: Settings, preview_path: str) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.hot_news_email_max_age_hours)
    async with session_factory() as session:
        articles = (
            await session.execute(
                select(Article)
                .where(
                    Article.is_hot_news.is_(True),
                    Article.hot_news_emailed_at.is_(None),
                    Article.crawled_at >= cutoff,
                )
                .order_by(Article.crawled_at)
            )
        ).scalars().all()
        recipients = (await session.execute(select(User.email).where(User.is_active.is_(True)))).scalars().all()

    logger.info("Bài chờ gửi: %d | Người nhận is_active: %d", len(articles), len(recipients))
    for a in articles:
        logger.info("  [ID:%d] %s — %s", a.id, a.title, a.url)
    if articles:
        message = build_hot_news_digest_message(articles, settings)
        logger.info("Tiêu đề: %s", message["Subject"])
        html_part = message.get_body(preferencelist=("html",))
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html_part.get_content())
        logger.info("Đã ghi preview HTML: %s", preview_path)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Không gửi, chỉ liệt kê bài/người nhận + ghi preview HTML")
    parser.add_argument("--preview-path", default="hot_news_preview.html")
    args = parser.parse_args()

    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        if args.dry_run:
            await _dry_run(session_factory, settings, args.preview_path)
        else:
            result = await send_pending_hot_news_digest(session_factory, settings)
            logger.info("Kết quả: %s", result)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
