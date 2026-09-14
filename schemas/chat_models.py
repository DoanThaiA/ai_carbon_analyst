"""Data models cho tính năng Quote Chat — hỏi đáp về 1 đoạn bôi đen trong báo cáo.

Lịch sử hội thoại KHÔNG còn do client gửi lại mỗi request — được lưu trong
Postgres (`db.models.ChatSession` / `ChatMessage`) và nạp lại phía server làm
bộ nhớ ngắn hạn (xem `services/chat_history.py`). Client chỉ cần giữ `session_id`.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Định dạng file đính kèm cho phép trong Quote Chat — khớp NGUYÊN VĂN với
# services/minio_service.py (nơi thực sự chặn upload theo content-type) và
# services/quote_chat.py (nơi map media_type -> content block gửi Claude/parse
# text). Sửa 1 nơi mà không sửa 2 nơi còn lại sẽ gây lệch hành vi validate.
ALLOWED_ATTACHMENT_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    }
)

# Tối đa 3 file/lượt hỏi — chặn phình prompt/chi phí LLM khi gửi kèm nhiều
# ảnh/PDF lớn cùng lúc.
MAX_ATTACHMENTS_PER_TURN = 3


class Attachment(BaseModel):
    """1 file đính kèm đã upload thành công lên MinIO (xem
    services/minio_service.py::generate_presigned_upload_url) — client chỉ gửi
    lại `file_key` (không gửi lại nội dung file), backend tự tải file về khi
    cần build content block cho Claude (services/quote_chat.py)."""

    file_name: str = Field(..., min_length=1, max_length=255)
    file_key: str = Field(..., min_length=1, max_length=500)
    media_type: str

    @model_validator(mode="after")
    def _validate_media_type(self) -> "Attachment":
        if self.media_type not in ALLOWED_ATTACHMENT_MEDIA_TYPES:
            raise ValueError(f"media_type không được hỗ trợ: {self.media_type}")
        return self


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)
    attachments: Optional[List[Attachment]] = Field(None, max_length=MAX_ATTACHMENTS_PER_TURN)


class QuoteChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    # None -> tạo phiên mới (bắt buộc phải kèm `quote`). Có giá trị -> hỏi tiếp
    # trong phiên đã có, quote/lịch sử được lấy lại từ Postgres, không cần gửi lại.
    session_id: Optional[int] = None
    quote: Optional[str] = Field(None, min_length=1, max_length=4000)
    # File đính kèm CỦA CÂU HỎI NÀY (ảnh/PDF/Word đã upload lên MinIO trước đó
    # qua POST /api/upload/presigned-url) — chỉ áp dụng cho lượt hỏi hiện tại,
    # KHÔNG được resend cho các lượt sau (đã lưu vào ChatMessage.attachments,
    # nhưng bộ nhớ ngắn hạn nạp lại qua load_recent_turns() chỉ lấy lại text).
    attachments: Optional[List[Attachment]] = Field(None, max_length=MAX_ATTACHMENTS_PER_TURN)


class SuggestedQuestionsRequest(BaseModel):
    quote: str = Field(..., min_length=1, max_length=4000)


class SuggestedQuestionsResponse(BaseModel):
    questions: List[str]


class ChatSessionSummary(BaseModel):
    id: int
    quote: str
    created_at: datetime
    updated_at: datetime
    rating: Optional[Literal["good", "bad"]] = None
    rating_reason: Optional[str] = None


class ChatSessionDetail(BaseModel):
    id: int
    quote: str
    created_at: datetime
    messages: List[ChatTurn]
    rating: Optional[Literal["good", "bad"]] = None
    rating_reason: Optional[str] = None


class ChatSessionRatingRequest(BaseModel):
    """Đánh giá 1 phiên Quote Chat. `reason` bắt buộc khi rating='bad' để admin
    biết vì sao — không bắt buộc khi rating='good'."""

    rating: Literal["good", "bad"]
    reason: Optional[str] = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _require_reason_when_bad(self) -> "ChatSessionRatingRequest":
        if self.rating == "bad" and not (self.reason or "").strip():
            raise ValueError("reason là bắt buộc khi đánh giá 'không tốt'")
        return self


class AdminChatSessionSummary(BaseModel):
    """Danh sách phiên chat cho màn hình admin quản lý đánh giá — không scope
    theo user, kèm số tin nhắn để hiện trên bảng mà không phải load hết nội dung."""

    id: int
    user_email: str
    report_date: str
    quote: str
    rating: Optional[Literal["good", "bad"]] = None
    rating_reason: Optional[str] = None
    message_count: int
    created_at: datetime
    updated_at: datetime


class AdminChatMessage(BaseModel):
    """Như ChatTurn nhưng kèm `id` (cần để admin chọn đúng 1 câu trả lời làm
    ví dụ mẫu — xem POST /api/admin/quote-chat-examples) và `example_id`: None
    nếu câu trả lời này CHƯA được thêm làm ví dụ mẫu, ngược lại là id của
    QuoteChatExample tương ứng (FE dùng để hiện trạng thái đã thêm + xoá)."""

    id: int
    role: Literal["user", "assistant"]
    content: str
    example_id: Optional[int] = None


class AdminChatSessionDetail(BaseModel):
    id: int
    user_email: str
    report_date: str
    quote: str
    rating: Optional[Literal["good", "bad"]] = None
    rating_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    messages: List[AdminChatMessage]


class AdminChatSessionListResponse(BaseModel):
    items: List[AdminChatSessionSummary]
    total: int
