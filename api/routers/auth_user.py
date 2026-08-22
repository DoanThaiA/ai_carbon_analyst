import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_settings
from core.config import Settings
from core.security import create_access_token, create_refresh_token, decode_refresh_token
from services.email_sender import EmailSendError
from services.otp_service import OtpError, request_otp, verify_otp

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"

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


class OtpRequestBody(BaseModel):
    email: EmailStr


class OtpVerifyBody(BaseModel):
    email: EmailStr
    code: str


@router.post("/otp/request")
async def otp_request(
    body: OtpRequestBody,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        await request_otp(session, body.email, settings)
    except OtpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except EmailSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"message": "Mã OTP đã được gửi đến email của bạn."}


@router.post("/otp/verify")
async def otp_verify(
    body: OtpVerifyBody,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        await verify_otp(session, body.email, body.code, settings)
    except OtpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    subject = body.email.strip().lower()
    access_token = create_access_token(subject=subject, role="user", settings=settings)
    refresh_token = create_refresh_token(subject=subject, role="user", settings=settings)
    _set_tokens_cookies(response, access_token, refresh_token, settings, refresh_path="/api/auth/refresh")
    return {"email": body.email}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth/refresh")
    return {"ok": True}


@router.post("/refresh")
async def user_refresh(
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
    
    if payload.get("role") != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Không có quyền truy cập.")
    
    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ.")
        
    access_token = create_access_token(subject=email, role="user", settings=settings)
    new_refresh_token = create_refresh_token(subject=email, role="user", settings=settings)
    _set_tokens_cookies(response, access_token, new_refresh_token, settings, refresh_path="/api/auth/refresh")
    return {"ok": True}


@router.get("/me")
async def me(payload: dict = Depends(get_current_user)):
    return {"email": payload["sub"], "role": payload["role"]}
