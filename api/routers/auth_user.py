from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_settings
from core.config import Settings
from core.security import create_access_token
from services.email_sender import EmailSendError
from services.otp_service import OtpError, request_otp, verify_otp

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "access_token"


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

    token = create_access_token(subject=body.email.strip().lower(), role="user", settings=settings)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return {"email": body.email}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(payload: dict = Depends(get_current_user)):
    return {"email": payload["sub"], "role": payload["role"]}
