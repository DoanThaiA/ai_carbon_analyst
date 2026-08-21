"""
Dependencies dùng chung cho toàn bộ API: DB session + xác thực qua cookie
JWT (access_token). Đặt engine/session_maker ở đây (thay vì api/main.py) để
các router con (api/routers/*.py) import được mà không tạo circular import.
"""
from __future__ import annotations

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from core.security import decode_access_token
from db.session import build_sessionmaker, create_engine

settings = Settings.from_env()
engine = create_engine(settings.database_url)
async_session_maker = build_sessionmaker(engine)


async def get_db():
    async with async_session_maker() as session:
        yield session


def get_settings() -> Settings:
    return settings


def _decode_cookie(access_token: str | None) -> dict:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập.")
    try:
        return decode_access_token(access_token, settings)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.")


async def get_current_admin(access_token: str | None = Cookie(default=None)) -> dict:
    payload = _decode_cookie(access_token)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Yêu cầu quyền admin.")
    return payload


async def get_current_user(access_token: str | None = Cookie(default=None)) -> dict:
    payload = _decode_cookie(access_token)
    if payload.get("role") not in ("admin", "user"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Không có quyền truy cập.")
    return payload
