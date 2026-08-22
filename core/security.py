"""
Băm mật khẩu (bcrypt) + phát hành/giải mã JWT session cho 2 role: admin, user.
Không phụ thuộc framework — dùng chung được cho API và các script CLI (vd
scripts/generate_admin_hash.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt

from core.config import Settings

Role = Literal["admin", "user"]


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(subject: str, role: Role, settings: Settings) -> str:
    """subject = username (admin) hoặc email (user)."""
    if not settings.jwt_secret:
        raise RuntimeError(
            "JWT_SECRET chưa được set trong .env — cần thiết để phát hành session token."
        )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, role: Role, settings: Settings) -> str:
    if not settings.jwt_secret:
        raise RuntimeError(
            "JWT_SECRET chưa được set trong .env — cần thiết để phát hành session token."
        )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_refresh_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict:
    """Raise jwt.PyJWTError nếu token thiếu hợp lệ/hết hạn/sai chữ ký."""
    if not settings.jwt_secret:
        raise RuntimeError(
            "JWT_SECRET chưa được set trong .env — cần thiết để xác thực session token."
        )
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type", "access") != "access": # support old tokens with no type as access
        raise jwt.InvalidTokenError("Không phải là access token.")
    return payload


def decode_refresh_token(token: str, settings: Settings) -> dict:
    if not settings.jwt_secret:
        raise RuntimeError(
            "JWT_SECRET chưa được set trong .env — cần thiết để xác thực session token."
        )
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "refresh":
        raise jwt.InvalidTokenError("Không phải là refresh token.")
    return payload
