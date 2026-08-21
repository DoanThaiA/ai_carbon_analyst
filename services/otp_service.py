"""
Logic OTP cho luồng đăng nhập user: sinh mã, gửi email, xác thực.
Không lưu mã gốc trong DB — chỉ lưu sha256(code) + hạn dùng + số lần thử sai.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from db.models import OtpCode, User
from services.email_sender import send_otp_email

logger = logging.getLogger(__name__)

RESEND_COOLDOWN_SECONDS = 60


class OtpError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def request_otp(session: AsyncSession, email: str, settings: Settings) -> None:
    email = email.strip().lower()

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise OtpError("Email này chưa được cấp quyền truy cập hệ thống.", status_code=403)

    latest = (
        await session.execute(
            select(OtpCode)
            .where(OtpCode.email == email)
            .order_by(OtpCode.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if latest is not None and (now - latest.created_at) < timedelta(seconds=RESEND_COOLDOWN_SECONDS):
        raise OtpError("Vui lòng đợi ít nhất 60 giây trước khi yêu cầu mã mới.", status_code=429)

    code = _generate_code()
    otp = OtpCode(
        email=email,
        code_hash=_hash_code(code),
        expires_at=now + timedelta(minutes=settings.otp_expire_minutes),
    )
    session.add(otp)
    await session.commit()

    if settings.smtp_host and settings.smtp_user and settings.smtp_password:
        await send_otp_email(email, code, settings)
    else:
        # Dev fallback — KHÔNG bao giờ chạy nhánh này khi đã cấu hình đủ SMTP thật.
        logger.warning("[DEV] SMTP chưa cấu hình đầy đủ — mã OTP cho %s là: %s", email, code)


async def verify_otp(session: AsyncSession, email: str, code: str, settings: Settings) -> None:
    email = email.strip().lower()

    otp = (
        await session.execute(
            select(OtpCode)
            .where(OtpCode.email == email, OtpCode.consumed_at.is_(None))
            .order_by(OtpCode.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if otp is None:
        raise OtpError("Không tìm thấy mã OTP nào đang hiệu lực, vui lòng yêu cầu mã mới.", status_code=400)

    now = datetime.now(timezone.utc)
    if now > otp.expires_at:
        raise OtpError("Mã OTP đã hết hạn, vui lòng yêu cầu mã mới.", status_code=400)

    if otp.attempt_count >= settings.otp_max_attempts:
        raise OtpError("Đã nhập sai quá số lần cho phép, vui lòng yêu cầu mã mới.", status_code=429)

    if _hash_code(code.strip()) != otp.code_hash:
        otp.attempt_count += 1
        await session.commit()
        raise OtpError("Mã OTP không đúng.", status_code=401)

    otp.consumed_at = now
    await session.commit()
