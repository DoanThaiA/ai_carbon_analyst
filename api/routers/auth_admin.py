import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel

from api.deps import get_current_admin, get_settings
from core.config import Settings
from core.security import create_access_token, create_refresh_token, decode_refresh_token, verify_password

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])

COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"


class AdminLoginRequest(BaseModel):
    username: str
    password: str


def _set_tokens_cookies(response: Response, access_token: str, refresh_token: str, settings: Settings, refresh_path: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_refresh_expire_minutes * 60,
        path=refresh_path,
    )


@router.post("/login")
async def admin_login(
    body: AdminLoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    if not settings.admin_username or not settings.admin_password_hash:
        raise HTTPException(
            status_code=500,
            detail="Chưa cấu hình ADMIN_USERNAME/ADMIN_PASSWORD_HASH trong .env.",
        )

    if body.username != settings.admin_username or not verify_password(
        body.password, settings.admin_password_hash
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai tài khoản hoặc mật khẩu.")

    access_token = create_access_token(subject=body.username, role="admin", settings=settings)
    refresh_token = create_refresh_token(subject=body.username, role="admin", settings=settings)
    _set_tokens_cookies(response, access_token, refresh_token, settings, refresh_path="/api/admin/auth/refresh")
    return {"username": body.username}


@router.post("/logout")
async def admin_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/admin/auth/refresh")
    return {"ok": True}


@router.post("/refresh")
async def admin_refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    settings: Settings = Depends(get_settings),
):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập.")
    try:
        payload = decode_refresh_token(refresh_token, settings)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token không hợp lệ hoặc đã hết hạn.")
    
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Yêu cầu quyền admin.")
    
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ.")
        
    access_token = create_access_token(subject=username, role="admin", settings=settings)
    new_refresh_token = create_refresh_token(subject=username, role="admin", settings=settings)
    _set_tokens_cookies(response, access_token, new_refresh_token, settings, refresh_path="/api/admin/auth/refresh")
    return {"ok": True}


@router.get("/me")
async def admin_me(payload: dict = Depends(get_current_admin)):
    return {"username": payload["sub"]}
