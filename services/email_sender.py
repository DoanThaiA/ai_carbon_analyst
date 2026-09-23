"""
Gửi email (OTP đăng nhập + digest Hot News) qua Gmail SMTP (App Password) bằng aiosmtplib — khớp thiết kế
asyncio-first của toàn bộ codebase (crawl_news, embedding, ...).

Cần set trong .env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
SMTP_USER/SMTP_PASSWORD là Gmail App Password (Google Account > Security >
App Passwords) — KHÔNG dùng mật khẩu Gmail thường vì Google chặn SMTP đăng
nhập trực tiếp bằng mật khẩu tài khoản.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from typing import List, Optional, Protocol, Sequence

import aiosmtplib

from core.config import Settings

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    pass


async def send_otp_email(to_email: str, code: str, settings: Settings) -> None:
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        raise EmailSendError(
            "SMTP chưa được cấu hình (SMTP_HOST/SMTP_USER/SMTP_PASSWORD trong .env) — "
            "không thể gửi email OTP."
        )

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message["Subject"] = "Mã đăng nhập Carbon Analyst"
    message.set_content(
        f"Mã đăng nhập của bạn là: {code}\n\n"
        f"Mã có hiệu lực trong {settings.otp_expire_minutes} phút. "
        "Nếu bạn không yêu cầu mã này, vui lòng bỏ qua email."
    )

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        )
    except Exception as exc:  # noqa: BLE001 — bọc lại thành lỗi rõ nghĩa cho tầng API
        logger.exception("Gửi email OTP thất bại cho %s", to_email)
        raise EmailSendError(f"Không gửi được email OTP: {exc}") from exc


# ─── Digest Hot News ─────────────────────────────────────────────────────────

TZ_VN = timezone(timedelta(hours=7))
_SUBJECT_TITLE_MAX_CHARS = 150


class HotNewsEmailItem(Protocol):
    """Các field cần để render 1 bài trong email — `db.models.Article` thoả mãn
    sẵn; test truyền SimpleNamespace, không cần DB."""

    title: Optional[str]
    url: str
    source: str
    hot_news_reason: Optional[str]
    published_at: Optional[datetime]


def _one_line(value: Optional[str]) -> str:
    return " ".join((value or "").split())


def _safe_href(url: str) -> str:
    """Chỉ cho phép link http(s) — URL crawl từ web là dữ liệu không tin cậy."""
    return url if url.lower().startswith(("http://", "https://")) else "#"


def _format_published(published_at: Optional[datetime]) -> str:
    if published_at is None:
        return ""
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at.astimezone(TZ_VN).strftime("%H:%M %d/%m/%Y")


def build_hot_news_subject(articles: Sequence[HotNewsEmailItem]) -> str:
    if len(articles) == 1:
        title = _one_line(articles[0].title) or articles[0].url
        if len(title) > _SUBJECT_TITLE_MAX_CHARS:
            title = title[: _SUBJECT_TITLE_MAX_CHARS - 1].rstrip() + "…"
        return f"[HOT NEWS] {title}"
    return f"[HOT NEWS] {len(articles)} tin tức quan trọng vừa được cập nhật"


def _render_text(articles: Sequence[HotNewsEmailItem], app_base_url: str) -> str:
    lines = [f"Carbon Analyst — {len(articles)} tin Hot News mới:", ""]
    for i, a in enumerate(articles, 1):
        meta = " · ".join(p for p in (a.source, _format_published(a.published_at)) if p)
        lines.append(f"{i}. {_one_line(a.title) or a.url}")
        lines.append(f"   {meta}")
        if a.hot_news_reason:
            lines.append(f"   Lý do: {_one_line(a.hot_news_reason)}")
        lines.append(f"   {a.url}")
        lines.append("")
    if app_base_url:
        lines.append(f"Mở Carbon Analyst: {app_base_url}")
    return "\n".join(lines)


def _render_html(articles: Sequence[HotNewsEmailItem], app_base_url: str) -> str:
    """Layout bảng + inline CSS — Gmail/Outlook bỏ qua <style>/flexbox."""
    esc = html.escape
    cards = []
    for a in articles:
        meta = " · ".join(esc(p) for p in (a.source, _format_published(a.published_at)) if p)
        reason = (
            f'<p style="margin:10px 0 0;padding:10px 12px;background:#fff7ed;border-left:3px solid #f97316;'
            f'color:#7c2d12;font-size:14px;line-height:1.5;">{esc(_one_line(a.hot_news_reason))}</p>'
            if a.hot_news_reason
            else ""
        )
        cards.append(
            '<tr><td style="padding:0 0 16px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border:1px solid #e5e7eb;border-radius:8px;background:#ffffff;">'
            '<tr><td style="padding:16px 18px;">'
            f'<p style="margin:0;font-size:16px;font-weight:700;line-height:1.4;color:#111827;">'
            f"{esc(_one_line(a.title) or a.url)}</p>"
            f'<p style="margin:6px 0 0;font-size:13px;color:#6b7280;">{meta}</p>'
            f"{reason}"
            f'<p style="margin:14px 0 0;"><a href="{esc(_safe_href(a.url), quote=True)}" '
            'style="display:inline-block;padding:8px 16px;background:#b91c1c;color:#ffffff;'
            'text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;">Đọc bài gốc</a></p>'
            "</td></tr></table></td></tr>"
        )
    footer_link = (
        f'<a href="{esc(app_base_url, quote=True)}" style="color:#b91c1c;">Mở Carbon Analyst</a><br>'
        if app_base_url
        else ""
    )
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f3f4f6;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;font-family:Arial,Helvetica,sans-serif;">'
        '<tr><td style="padding:0 0 16px;">'
        '<p style="margin:0;font-size:12px;font-weight:700;letter-spacing:1px;color:#b91c1c;">HOT NEWS</p>'
        f'<p style="margin:4px 0 0;font-size:20px;font-weight:700;color:#111827;">'
        f"{len(articles)} tin tức quan trọng vừa được cập nhật</p>"
        "</td></tr>"
        + "".join(cards)
        + '<tr><td style="padding:8px 0 0;font-size:12px;color:#9ca3af;line-height:1.6;">'
        f"{footer_link}"
        "Bạn nhận email này vì tài khoản của bạn được cấp quyền truy cập Carbon Analyst."
        "</td></tr></table></td></tr></table></body></html>"
    )


def build_hot_news_digest_message(
    articles: Sequence[HotNewsEmailItem], settings: Settings
) -> EmailMessage:
    """Dựng email (text + HTML). KHÔNG set header To/Bcc chứa người nhận — người
    nhận chỉ nằm trong SMTP envelope (xem send_hot_news_digest_email), nên không
    ai thấy được email của người khác."""
    if not articles:
        raise ValueError("Cần ít nhất 1 bài để dựng email hot news.")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = settings.smtp_from  # người nhận thật đi qua envelope (Bcc)
    message["Subject"] = build_hot_news_subject(articles)
    message["Message-ID"] = make_msgid(domain=(settings.smtp_from.rpartition("@")[2] or None))
    message.set_content(_render_text(articles, settings.app_base_url))
    message.add_alternative(_render_html(articles, settings.app_base_url), subtype="html")
    return message


def _batches(items: Sequence[str], size: int) -> List[List[str]]:
    size = max(1, size)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


async def send_hot_news_digest_email(
    articles: Sequence[HotNewsEmailItem], recipients: Sequence[str], settings: Settings
) -> int:
    """Gửi 1 email digest cho toàn bộ `recipients` (Bcc, chia lô
    `hot_news_email_bcc_batch_size` người/thư, dùng chung 1 kết nối SMTP).

    Trả về số người nhận gửi thành công. Lỗi 1 lô chỉ log rồi gửi tiếp lô sau;
    chỉ raise EmailSendError khi KHÔNG lô nào gửi được (để caller không đánh
    dấu "đã gửi" và lần crawl sau thử lại)."""
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        raise EmailSendError(
            "SMTP chưa được cấu hình (SMTP_HOST/SMTP_USER/SMTP_PASSWORD trong .env) — "
            "không thể gửi email hot news."
        )
    if not recipients:
        return 0

    message = build_hot_news_digest_message(articles, settings)
    sent = 0
    try:
        async with aiosmtplib.SMTP(
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        ) as smtp:
            for batch in _batches(recipients, settings.hot_news_email_bcc_batch_size):
                try:
                    await smtp.send_message(message, sender=settings.smtp_user, recipients=batch)
                    sent += len(batch)
                except aiosmtplib.SMTPException:
                    logger.exception("[HOT-NEWS-EMAIL] Gửi lô %d người nhận thất bại", len(batch))
    except Exception as exc:  # noqa: BLE001 — lỗi kết nối/đăng nhập SMTP
        if sent == 0:
            raise EmailSendError(f"Không gửi được email hot news: {exc}") from exc
        logger.exception("[HOT-NEWS-EMAIL] Kết nối SMTP lỗi giữa chừng (đã gửi %d người)", sent)

    if sent == 0:
        raise EmailSendError("Không gửi được email hot news tới lô người nhận nào.")
    return sent
