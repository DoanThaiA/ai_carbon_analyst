"""Xin presigned URL để upload file đính kèm (Quote Chat) thẳng lên MinIO —
backend KHÔNG nhận file qua request body (tránh tốn băng thông/RAM server cho
file có thể lên tới 10MB), chỉ ký URL rồi để FE PUT trực tiếp.
"""
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_user
from services.minio_service import (
    InvalidAttachmentError,
    generate_presigned_get_url,
    generate_presigned_upload_url,
)

router = APIRouter(prefix="/api/upload", tags=["upload"])


class PresignedUrlRequest(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=255)
    content_type: str
    size: int = Field(..., gt=0)


class PresignedUrlResponse(BaseModel):
    upload_url: str
    file_key: str


class ViewUrlResponse(BaseModel):
    url: str


@router.post("/presigned-url", response_model=PresignedUrlResponse)
async def get_presigned_upload_url(
    body: PresignedUrlRequest,
    _payload: dict = Depends(get_current_user),
):
    try:
        result = generate_presigned_upload_url(body.file_name, body.content_type, body.size)
    except InvalidAttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PresignedUrlResponse(upload_url=result.upload_url, file_key=result.file_key)


@router.get("/view-url", response_model=ViewUrlResponse)
async def get_view_url(
    file_key: str = Query(..., min_length=1, max_length=500),
    _payload: dict = Depends(get_current_user),
):
    """URL tạm (10 phút) để FE hiện thumbnail ảnh / mở PDF-Word đã đính kèm
    trong lịch sử chat. KHÔNG kiểm tra file_key thuộc đúng session/user nào —
    file_key là UUID ngẫu nhiên (services/minio_service.py), không đoán được
    nếu chưa từng thấy qua 1 session/message của chính user đó."""
    return ViewUrlResponse(url=generate_presigned_get_url(file_key))
