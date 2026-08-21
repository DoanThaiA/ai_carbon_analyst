"""Data models cho tính năng Quote Chat — hỏi đáp về 1 đoạn bôi đen trong báo cáo.

Lịch sử hội thoại KHÔNG còn do client gửi lại mỗi request — được lưu trong
Postgres (`db.models.ChatSession` / `ChatMessage`) và nạp lại phía server làm
bộ nhớ ngắn hạn (xem `services/chat_history.py`). Client chỉ cần giữ `session_id`.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


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


class ChatSessionDetail(BaseModel):
    id: int
    quote: str
    created_at: datetime
    messages: List[ChatTurn]
