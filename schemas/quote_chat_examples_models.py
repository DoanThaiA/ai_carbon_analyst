"""Data models cho tính năng ví dụ mẫu (few-shot) của Quote Chat — xem
db.models.QuoteChatExample và services/quote_chat_examples.py.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class QuoteChatExampleCreateRequest(BaseModel):
    session_id: int
    answer_message_id: int


class QuoteChatExample(BaseModel):
    id: int
    question: str
    answer: str
    source_session_id: Optional[int] = None
    # Join từ chat_sessions tại thời điểm đọc — None nếu phiên gốc đã bị xoá,
    # chỉ để admin biết bối cảnh ví dụ đến từ báo cáo/đoạn trích nào.
    source_report_date: Optional[str] = None
    source_quote: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
