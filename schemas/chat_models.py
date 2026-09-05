"""Data models cho tính năng Quote Chat — hỏi đáp về 1 đoạn bôi đen trong báo cáo.

Lịch sử hội thoại KHÔNG còn do client gửi lại mỗi request — được lưu trong
Postgres (`db.models.ChatSession` / `ChatMessage`) và nạp lại phía server làm
bộ nhớ ngắn hạn (xem `services/chat_history.py`). Client chỉ cần giữ `session_id`.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class QuoteChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    # None -> tạo phiên mới (bắt buộc phải kèm `quote`). Có giá trị -> hỏi tiếp
    # trong phiên đã có, quote/lịch sử được lấy lại từ Postgres, không cần gửi lại.
    session_id: Optional[int] = None
    quote: Optional[str] = Field(None, min_length=1, max_length=4000)


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


class AdminChatSessionDetail(BaseModel):
    id: int
    user_email: str
    report_date: str
    quote: str
    rating: Optional[Literal["good", "bad"]] = None
    rating_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    messages: List[ChatTurn]


class AdminChatSessionListResponse(BaseModel):
    items: List[AdminChatSessionSummary]
    total: int
