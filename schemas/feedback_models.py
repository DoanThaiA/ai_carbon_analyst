"""Data models cho tính năng phản ánh thái độ AI assistant (Jenny) — chỉ gửi
được khi đã đăng nhập, luôn gắn với email tài khoản đang đăng nhập."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class FeedbackCreateRequest(BaseModel):
    reporter_name: Optional[str] = Field(None, max_length=200)
    content: str = Field(..., min_length=1, max_length=4000)


class FeedbackResponse(BaseModel):
    id: int
    user_email: str
    reporter_name: Optional[str] = None
    reporter_role: str
    content: str
    created_at: datetime


class FeedbackListResponse(BaseModel):
    items: List[FeedbackResponse]
    total: int
