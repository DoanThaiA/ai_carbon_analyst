"""
Gửi email (OTP đăng nhập + digest Hot News) qua Resend HTTP API (https://resend.com)
bằng httpx.AsyncClient — khớp thiết kế asyncio-first của toàn bộ codebase
(không dùng SDK `resend` vì SDK là sync, sẽ chặn event loop).

Cần set trong .env: RESEND_API_KEY, EMAIL_FROM. EMAIL_FROM phải thuộc 1 domain
đã verify trong Resend (Resend > Domains, thêm bản ghi SPF/DKIM vào DNS) —
KHÔNG dùng được địa chỉ @gmail.com làm người gửi. Chưa có domain thì dùng tạm
`onboarding@resend.dev` (chỉ gửi được tới email chủ tài khoản Resend).
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Protocol, Sequence

import httpx

from core.config import Settings

logger = logging.getLogger(__name__)

RESEND_API_BASE = "https://api.resend.com"
# Giới hạn của Resend: tối đa 100 email / 1 request POST /emails/batch.
RESEND_BATCH_MAX = 100
_MAX_ATTEMPTS = 3


class EmailSendError(RuntimeError):
    pass


def email_configured(settings: Settings) -> bool:
    return bool(settings.resend_api_key and settings.email_from)


def _require_configured(settings: Settings, purpose: str) -> None:
    if not email_configured(settings):
        raise EmailSendError(
            "Resend chưa được cấu hình (RESEND_API_KEY/EMAIL_FROM trong .env) — "
            f"không thể gửi email {purpose}."
        )


def _http_client(settings: Settings) -> httpx.AsyncClient:
    """Tách riêng để test thay bằng client dùng httpx.MockTransport."""
    return httpx.AsyncClient(
        base_url=RESEND_API_BASE,
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        timeout=15.0,
    )


async def _post(
    client: httpx.AsyncClient, path: str, payload: Any, idempotency_key: Optional[str] = None
) -> Any:
    """POST tới Resend, retry khi 429 (rate limit) / 5xx / lỗi mạng. Idempotency-Key
    giúp retry (kể cả ở đợt crawl sau, trong 24h) không tạo email trùng."""
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await client.post(path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            if attempt == _MAX_ATTEMPTS:
                raise EmailSendError(f"Không kết nối được Resend: {exc}") from exc
            await asyncio.sleep(attempt)
            continue
        if resp.status_code < 300:
            return resp.json()
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_ATTEMPTS:
            try:
                delay = min(float(resp.headers.get("retry-after", attempt)), 10.0)
            except ValueError:
                delay = float(attempt)
            await asyncio.sleep(delay)
            continue
        raise EmailSendError(f"Resend trả lỗi {resp.status_code}: {resp.text[:500]}")
    raise EmailSendError("Resend: hết số lần thử.")  # không tới được — vòng lặp luôn return/raise


def _sender_fields(settings: Settings) -> dict:
    """from + reply_to chung cho mọi email. Gmail chấm điểm spam cao hơn với email
    "noreply" không có Reply-To thật."""
    fields: dict = {"from": settings.email_from}
    if settings.email_reply_to:
        fields["reply_to"] = settings.email_reply_to
    return fields


async def send_otp_email(to_email: str, code: str, settings: Settings) -> None:
    _require_configured(settings, "OTP")
    payload = {
        **_sender_fields(settings),
        "to": [to_email],
        "subject": "Mã đăng nhập Carbon Analyst",
        "text": (
            f"Mã đăng nhập của bạn là: {code}\n\n"
            f"Mã có hiệu lực trong {settings.otp_expire_minutes} phút. "
            "Nếu bạn không yêu cầu mã này, vui lòng bỏ qua email."
        ),
    }
    try:
        async with _http_client(settings) as client:
            await _post(client, "/emails", payload)
    except EmailSendError:
        logger.exception("Gửi email OTP thất bại cho %s", to_email)
        raise


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


# Tiêu đề tránh "[HOT NEWS]" viết hoa trong ngoặc — kiểu tiêu đề quảng cáo mà bộ
# lọc spam của Gmail chấm điểm cao.
def build_hot_news_subject(articles: Sequence[HotNewsEmailItem]) -> str:
    if len(articles) == 1:
        title = _one_line(articles[0].title) or articles[0].url
        if len(title) > _SUBJECT_TITLE_MAX_CHARS:
            title = title[: _SUBJECT_TITLE_MAX_CHARS - 1].rstrip() + "…"
        return f"Tin nổi bật: {title}"
    return f"Carbon Analyst: {len(articles)} tin nổi bật mới"


_FOOTER_NOTE = (
    "Bạn nhận email này vì tài khoản của bạn được cấp quyền truy cập Carbon Analyst. "
    "Muốn ngừng nhận, hãy trả lời email này hoặc liên hệ quản trị viên."
)


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
    lines.append("")
    lines.append(_FOOTER_NOTE)
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
            # Link chữ thay vì nút màu — email toàn nút CTA trỏ ra nhiều domain lạ
            # trông giống email marketing với bộ lọc spam.
            f'<p style="margin:12px 0 0;font-size:14px;"><a href="{esc(_safe_href(a.url), quote=True)}" '
            'style="color:#1d4ed8;">Đọc bài gốc</a></p>'
            "</td></tr></table></td></tr>"
        )
    footer_link = (
        f'<a href="{esc(app_base_url, quote=True)}" style="color:#1d4ed8;">Mở Carbon Analyst</a><br>'
        if app_base_url
        else ""
    )
    heading = f"{len(articles)} tin nổi bật mới"
    # Preheader: dòng xem trước trong hộp thư (ẩn trong thân email) — thiếu thì
    # Gmail lấy chữ đầu tiên của HTML, trông cẩu thả.
    preheader = esc(_one_line(articles[0].title) or heading)
    return (
        '<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(heading)}</title></head>"
        '<body style="margin:0;padding:0;background:#f3f4f6;">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{preheader}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;font-family:Arial,Helvetica,sans-serif;">'
        '<tr><td style="padding:0 0 16px;">'
        '<p style="margin:0;font-size:13px;font-weight:600;color:#6b7280;">Carbon Analyst</p>'
        f'<p style="margin:4px 0 0;font-size:20px;font-weight:700;color:#111827;">{esc(heading)}</p>'
        "</td></tr>"
        + "".join(cards)
        + '<tr><td style="padding:8px 0 0;font-size:12px;color:#9ca3af;line-height:1.6;">'
        f"{footer_link}"
        f"{esc(_FOOTER_NOTE)}"
        "</td></tr></table></td></tr></table></body></html>"
    )


@dataclass(frozen=True)
class HotNewsDigest:
    subject: str
    text: str
    html: str


def build_hot_news_digest_message(
    articles: Sequence[HotNewsEmailItem], settings: Settings
) -> HotNewsDigest:
    """Dựng nội dung email (subject + text + HTML) — hàm thuần, không gửi."""
    if not articles:
        raise ValueError("Cần ít nhất 1 bài để dựng email hot news.")
    return HotNewsDigest(
        subject=build_hot_news_subject(articles),
        text=_render_text(articles, settings.app_base_url),
        html=_render_html(articles, settings.app_base_url),
    )


def _batches(items: Sequence[str], size: int) -> List[List[str]]:
    size = max(1, size)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _idempotency_key(articles: Sequence[HotNewsEmailItem], batch: Sequence[str]) -> str:
    """Cùng tập bài + cùng lô người nhận → cùng key, nên nếu request trước đã tới
    Resend nhưng response bị mất (timeout), gửi lại không tạo email trùng."""
    raw = "|".join(sorted(a.url for a in articles)) + "#" + "|".join(batch)
    return "hot-news/" + hashlib.sha256(raw.encode()).hexdigest()


async def send_hot_news_digest_email(
    articles: Sequence[HotNewsEmailItem], recipients: Sequence[str], settings: Settings
) -> int:
    """Gửi digest cho toàn bộ `recipients` qua POST /emails/batch — mỗi người nhận
    1 email riêng (`to` chỉ có đúng người đó, nên không ai thấy email người khác),
    chia lô `hot_news_email_batch_size` email/request (tối đa 100).

    Trả về số người nhận gửi thành công. Lỗi 1 lô chỉ log rồi gửi tiếp lô sau;
    chỉ raise EmailSendError khi KHÔNG lô nào gửi được (để caller không đánh
    dấu "đã gửi" và lần crawl sau thử lại)."""
    _require_configured(settings, "hot news")
    if not recipients:
        return 0

    digest = build_hot_news_digest_message(articles, settings)
    batch_size = min(settings.hot_news_email_batch_size, RESEND_BATCH_MAX)
    # List-Unsubscribe: Gmail ưu tiên email gửi hàng loạt có cách huỷ nhận rõ ràng.
    # Chỉ gắn khi có Reply-To thật — mailto tới hộp "noreply" còn tệ hơn không có.
    headers = (
        {"List-Unsubscribe": f"<mailto:{settings.email_reply_to}?subject=unsubscribe>"}
        if settings.email_reply_to
        else {}
    )
    sent = 0
    last_error: Optional[Exception] = None
    async with _http_client(settings) as client:
        for batch in _batches(recipients, batch_size):
            payload = [
                {
                    **_sender_fields(settings),
                    "to": [email],
                    "subject": digest.subject,
                    "text": digest.text,
                    "html": digest.html,
                    **({"headers": headers} if headers else {}),
                }
                for email in batch
            ]
            try:
                resp = await _post(client, "/emails/batch", payload, _idempotency_key(articles, batch))
                sent += len(batch)
                # Log id Resend trả về cho từng người nhận — tra trạng thái giao thật
                # (delivered/bounced/suppressed) tại resend.com/emails/<id>.
                ids = [d.get("id") for d in (resp or {}).get("data") or []]
                for email, email_id in zip(batch, ids):
                    logger.info("[HOT-NEWS-EMAIL] Resend chấp nhận %s — id=%s", email, email_id)
                if len(ids) != len(batch):
                    logger.warning(
                        "[HOT-NEWS-EMAIL] Resend trả %d id cho lô %d người nhận: %s",
                        len(ids), len(batch), str(resp)[:500],
                    )
            except EmailSendError as exc:
                last_error = exc
                logger.exception("[HOT-NEWS-EMAIL] Gửi lô %d người nhận thất bại", len(batch))

    if sent == 0:
        raise EmailSendError(f"Không gửi được email hot news tới lô người nhận nào: {last_error}")
    return sent
