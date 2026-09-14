"""Lưu trữ file đính kèm Quote Chat (ảnh/PDF/Word) trên MinIO (S3-compatible).

Luồng: FE xin presigned URL ở đây (`generate_presigned_upload_url`) -> PUT
thẳng file lên MinIO (không qua backend) -> gửi lại `file_key` khi hỏi Quote
Chat -> backend tải file về bằng `get_file_as_bytes` khi cần build content
block gửi Claude (xem services/quote_chat.py).

Content-type/dung lượng được chặn 2 LỚP: (1) ở đây lúc xin presigned URL —
chặn sớm, dựa vào `content_type`/`size` do CLIENT tự khai báo, có thể bị giả
mạo; (2) `check_uploaded_object` gọi lại SAU khi file đã thật sự nằm trên
MinIO (dựa vào `stat_object`, dung lượng THẬT — không thể giả mạo) trước khi
đưa vào prompt — đây mới là lớp chặn thật sự quan trọng, vì lớp (1) chỉ có tác
dụng UX (báo lỗi sớm), không ngăn được client PUT thẳng lên MinIO bằng
presigned URL với 1 file khác/nặng hơn khai báo.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from core.config import Settings

logger = logging.getLogger(__name__)

IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"
DOCUMENT_MEDIA_TYPES = frozenset({PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE})
ALLOWED_MEDIA_TYPES = IMAGE_MEDIA_TYPES | DOCUMENT_MEDIA_TYPES

# .doc (định dạng OLE nhị phân cũ) KHÔNG nằm trong ALLOWED_MEDIA_TYPES — chỉ
# python-docx (đọc .docx dạng XML) được dùng để trích chữ, .doc cần
# LibreOffice/antiword (chưa cài trong image, xem Dockerfile.backend) nên
# nhận diện riêng ở đây CHỈ để trả lỗi rõ ràng thay vì "định dạng không hỗ trợ"
# chung chung.
LEGACY_DOC_MEDIA_TYPE = "application/msword"

IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

_EXTENSION_BY_MEDIA_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    PDF_MEDIA_TYPE: ".pdf",
    DOCX_MEDIA_TYPE: ".docx",
}

PRESIGNED_URL_EXPIRY = timedelta(minutes=10)


class InvalidAttachmentError(ValueError):
    """File không hợp lệ (sai định dạng/vượt dung lượng) — router map sang HTTP 400."""


@dataclass(frozen=True)
class PresignedUpload:
    upload_url: str
    file_key: str


def max_bytes_for(media_type: str) -> int:
    return IMAGE_MAX_BYTES if media_type in IMAGE_MEDIA_TYPES else DOCUMENT_MAX_BYTES


def validate_attachment(file_name: str, content_type: str, size: int) -> None:
    """Kiểm tra định dạng + dung lượng THUẦN (không đụng tới MinIO) — tách
    riêng để test được (tests/test_minio_service.py) mà không cần MinIO/DB
    thật, và để `generate_presigned_upload_url` fail-fast trước khi tốn 1
    round-trip mạng tới MinIO cho request chắc chắn sẽ bị từ chối."""
    if content_type == LEGACY_DOC_MEDIA_TYPE:
        raise InvalidAttachmentError(
            "Hệ thống chưa hỗ trợ file .doc (định dạng Word cũ) — vui lòng lưu lại dưới dạng .docx rồi thử lại."
        )
    if content_type not in ALLOWED_MEDIA_TYPES:
        raise InvalidAttachmentError(
            f"Định dạng file không được hỗ trợ ({content_type or 'không xác định'}) — chỉ nhận Ảnh "
            "(JPEG/PNG/WEBP), PDF, hoặc Word (.docx)."
        )
    limit = max_bytes_for(content_type)
    if size > limit:
        raise InvalidAttachmentError(
            f"File \"{file_name}\" vượt quá dung lượng cho phép ({limit // (1024 * 1024)}MB)."
        )


# Lazy singleton — tránh crash khi import module lúc chưa có .env (giống pattern
# quote_chat.py::_get_anthropic_client). Bucket được đảm bảo tồn tại đúng 1 lần
# ở lần gọi đầu tiên, không kiểm tra lại mỗi request.
_client = None
_bucket_ready = False


def _get_client():
    global _client
    if _client is None:
        from minio import Minio

        settings = Settings.from_env()
        if not settings.minio_endpoint:
            raise RuntimeError("MINIO_ENDPOINT chưa được cấu hình.")
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def _get_bucket() -> str:
    global _bucket_ready
    settings = Settings.from_env()
    client = _get_client()
    if not _bucket_ready:
        if not client.bucket_exists(settings.minio_bucket):
            client.make_bucket(settings.minio_bucket)
            logger.info("[MINIO] Đã tạo bucket %s", settings.minio_bucket)
        _bucket_ready = True
    return settings.minio_bucket


def generate_presigned_upload_url(file_name: str, content_type: str, size: int) -> PresignedUpload:
    """Cấp URL để FE PUT thẳng 1 file lên MinIO. Raise `InvalidAttachmentError`
    (router trả 400) nếu sai định dạng/vượt dung lượng cho phép — xem
    docstring module để hiểu vì sao `size` ở đây chỉ là kiểm tra UX, không phải
    lớp chặn thật sự."""
    validate_attachment(file_name, content_type, size)

    bucket = _get_bucket()
    ext = _EXTENSION_BY_MEDIA_TYPE.get(content_type, "")
    file_key = f"quote-chat/{uuid.uuid4().hex}{ext}"
    upload_url = _get_client().presigned_put_object(bucket, file_key, expires=PRESIGNED_URL_EXPIRY)
    return PresignedUpload(upload_url=upload_url, file_key=file_key)


def check_uploaded_object(file_key: str, expected_media_type: str) -> Optional[str]:
    """Kiểm tra LẠI (dựa trên dữ liệu thật trên MinIO, không phải client khai
    báo) dung lượng file đã upload trước khi đưa vào prompt — trả về thông báo
    lỗi (str) nếu vượt giới hạn/không tồn tại, None nếu hợp lệ."""
    try:
        stat = _get_client().stat_object(_get_bucket(), file_key)
    except Exception:
        logger.warning("[MINIO] Không tìm thấy object %s khi kiểm tra trước khi dùng.", file_key)
        return "không tìm thấy file trên hệ thống lưu trữ (có thể đã hết hạn hoặc chưa upload xong)"

    limit = max_bytes_for(expected_media_type)
    if stat.size > limit:
        return f"vượt quá dung lượng cho phép ({limit // (1024 * 1024)}MB)"
    return None


def generate_presigned_get_url(file_key: str) -> str:
    """URL tạm để FE xem lại 1 file đã đính kèm (thumbnail ảnh trong lịch sử
    chat / mở PDF-Word trong tab mới) — KHÔNG dùng để backend tự tải file (đó
    là `get_file_as_bytes`, đọc thẳng qua client, không cần ký URL)."""
    return _get_client().presigned_get_object(_get_bucket(), file_key, expires=PRESIGNED_URL_EXPIRY)


def get_file_as_bytes(file_key: str) -> bytes:
    """Tải nguyên 1 file từ MinIO về RAM — chỉ dùng cho file đã qua
    `check_uploaded_object`, và chỉ với file nhỏ (giới hạn ở
    IMAGE_MAX_BYTES/DOCUMENT_MAX_BYTES) nên load thẳng vào RAM là an toàn,
    không cần streaming."""
    response = _get_client().get_object(_get_bucket(), file_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
