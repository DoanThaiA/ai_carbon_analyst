from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from api.deps import get_current_admin, get_settings
from core.config import Settings
from core.security import create_access_token, verify_password

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])

COOKIE_NAME = "access_token"


class AdminLoginRequest(BaseModel):
    username: str
    password: str


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
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

    token = create_access_token(subject=body.username, role="admin", settings=settings)
    _set_session_cookie(response, token, settings)
    return {"username": body.username}


@router.post("/logout")
async def admin_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def admin_me(payload: dict = Depends(get_current_admin)):
    return {"username": payload["sub"]}
