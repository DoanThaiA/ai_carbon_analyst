"""Test việc chặn upload file đính kèm Quote Chat theo định dạng/dung lượng —
xem services/minio_service.py::validate_attachment. Hàm này THUẦN (không đụng
MinIO/network) nên test chạy được mà không cần MinIO/DB thật.
"""
import pytest

from services.minio_service import (
    DOCUMENT_MAX_BYTES,
    DOCX_MEDIA_TYPE,
    IMAGE_MAX_BYTES,
    InvalidAttachmentError,
    validate_attachment,
)


@pytest.mark.parametrize(
    "file_name,content_type",
    [
        ("virus.exe", "application/x-msdownload"),
        ("clip.mp4", "video/mp4"),
        ("archive.zip", "application/zip"),
    ],
)
def test_rejects_disallowed_content_type(file_name, content_type):
    with pytest.raises(InvalidAttachmentError):
        validate_attachment(file_name, content_type, 1024)


def test_rejects_legacy_doc_with_specific_guidance():
    """`.doc` (định dạng Word cũ) phải bị từ chối với thông báo rõ ràng gợi ý
    dùng `.docx` — khác thông báo "định dạng không hỗ trợ" chung chung."""
    with pytest.raises(InvalidAttachmentError, match=r"\.docx"):
        validate_attachment("bao-cao.doc", "application/msword", 1024)


def test_accepts_image_within_limit():
    validate_attachment("photo.jpg", "image/jpeg", IMAGE_MAX_BYTES)  # không raise


def test_rejects_oversize_image():
    with pytest.raises(InvalidAttachmentError, match="dung lượng"):
        validate_attachment("photo.png", "image/png", IMAGE_MAX_BYTES + 1)


def test_accepts_pdf_within_limit():
    validate_attachment("report.pdf", "application/pdf", DOCUMENT_MAX_BYTES)  # không raise


def test_rejects_oversize_pdf():
    with pytest.raises(InvalidAttachmentError, match="dung lượng"):
        validate_attachment("report.pdf", "application/pdf", DOCUMENT_MAX_BYTES + 1)


def test_rejects_oversize_docx():
    with pytest.raises(InvalidAttachmentError, match="dung lượng"):
        validate_attachment("report.docx", DOCX_MEDIA_TYPE, DOCUMENT_MAX_BYTES + 1)
