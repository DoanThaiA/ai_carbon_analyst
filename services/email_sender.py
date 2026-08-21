"""
Gửi email OTP qua Gmail SMTP (App Password) bằng aiosmtplib — khớp thiết kế
asyncio-first của toàn bộ codebase (crawl_news, embedding, ...).

Cần set trong .env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
SMTP_USER/SMTP_PASSWORD là Gmail App Password (Google Account > Security >
App Passwords) — KHÔNG dùng mật khẩu Gmail thường vì Google chặn SMTP đăng
nhập trực tiếp bằng mật khẩu tài khoản.
"""
from __future__ import annotations

import logging
from email.message import EmailMessage

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
