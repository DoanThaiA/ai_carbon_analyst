"""Gửi email digest Hot News cho user đã đăng ký SAU MỖI đợt crawl.

Tại sao gom theo đợt crawl thay vì debounce timer trong API server:
- Crawl chạy theo lô (06:00 toàn bộ nguồn, 12:00 nhóm is_noon_crawl — xem
  scheduler.py), nên "hết đợt crawl" chính là ranh giới gom nhóm tự nhiên:
  1 đợt = tối đa 1 email, dù đợt đó kéo dài bao lâu. Timer cố định (vd 10 phút)
  vẫn tách 1 đợt crawl dài thành nhiều email.
- Trạng thái "đã gửi" nằm ở DB (`articles.hot_news_emailed_at`), không phải
  buffer in-memory — restart/deploy giữa chừng không mất email, chạy nhiều
  process/worker không gửi trùng (claim bằng `FOR UPDATE SKIP LOCKED`), SMTP lỗi
  thì bài vẫn NULL và được gửi lại ở đợt crawl sau.

Chuông đỏ real-time trên UI vẫn đi qua Postgres NOTIFY + SSE như cũ
(services/hot_news_broadcast.py) — email chỉ là kênh bổ sung cho user offline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from core.config import Settings
from db.models import Article, User
from services.email_sender import EmailSendError, send_hot_news_digest_email

logger = logging.getLogger(__name__)


@dataclass
class HotNewsDigestResult:
    article_ids: List[int] = field(default_factory=list)
    recipients_sent: int = 0
    skipped_reason: str = ""


async def send_pending_hot_news_digest(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> HotNewsDigestResult:
    """Gom mọi bài hot news chưa gửi (crawl trong `hot_news_email_max_age_hours`
    giờ gần nhất) thành 1 email, gửi Bcc tới toàn bộ user is_active, rồi đánh dấu
    `hot_news_emailed_at`. KHÔNG raise — lỗi chỉ log, để không làm hỏng job crawl
    gọi nó (bài chưa gửi sẽ được thử lại ở đợt crawl sau)."""
    result = HotNewsDigestResult()
    if not settings.hot_news_email_enabled:
        result.skipped_reason = "disabled"
        return result
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        logger.warning("[HOT-NEWS-EMAIL] SMTP chưa cấu hình — bỏ qua gửi digest.")
        result.skipped_reason = "smtp_not_configured"
        return result

    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.hot_news_email_max_age_hours)
    try:
        async with session_factory() as session:
            async with session.begin():
                # Khoá các dòng trong suốt transaction (kể cả lúc chờ SMTP) — 1
                # process khác chạy song song sẽ bỏ qua các dòng này thay vì gửi trùng.
                articles = (
                    await session.execute(
                        select(Article)
                        .options(defer(Article.content))
                        .where(
                            Article.is_hot_news.is_(True),
                            Article.hot_news_emailed_at.is_(None),
                            Article.crawled_at >= cutoff,
                        )
                        .order_by(Article.crawled_at)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars().all()
                if not articles:
                    result.skipped_reason = "no_pending_articles"
                    return result

                recipients = (
                    await session.execute(
                        select(User.email).where(User.is_active.is_(True)).order_by(User.id)
                    )
                ).scalars().all()
                if not recipients:
                    # Không đánh dấu đã gửi — bài tự hết hạn theo max_age_hours.
                    logger.warning("[HOT-NEWS-EMAIL] Không có user is_active nào — bỏ qua.")
                    result.skipped_reason = "no_recipients"
                    return result

                result.recipients_sent = await send_hot_news_digest_email(articles, recipients, settings)
                result.article_ids = [a.id for a in articles]
                await session.execute(
                    update(Article)
                    .where(Article.id.in_(result.article_ids))
                    .values(hot_news_emailed_at=func.now())
                )
            # session.begin() commit ở đây
    except EmailSendError:
        logger.exception("[HOT-NEWS-EMAIL] Gửi digest thất bại — sẽ thử lại ở đợt crawl sau.")
        result.skipped_reason = "send_failed"
        return result
    except Exception:  # noqa: BLE001 — lỗi DB, không được làm hỏng job crawl
        logger.exception("[HOT-NEWS-EMAIL] Lỗi không mong đợi khi gửi digest.")
        result.skipped_reason = "unexpected_error"
        return result

    logger.info(
        "[HOT-NEWS-EMAIL] Đã gửi digest %d bài tới %d/%d người nhận.",
        len(result.article_ids), result.recipients_sent, len(recipients),
    )
    return result
