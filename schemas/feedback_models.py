"""Data models cho tính năng phản ánh thái độ AI assistant (Jenny) — gửi công
khai từ landing page, không yêu cầu đăng nhập."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class FeedbackCreateRequest(BaseModel):
    reporter_name: Optional[str] = Field(None, max_length=200)
    reporter_role: Literal["user", "admin", "guest"] = "guest"
    content: str = Field(..., min_length=1, max_length=4000)


class FeedbackResponse(BaseModel):
    id: int
    reporter_name: Optional[str] = None
    reporter_role: str
    content: str
    created_at: datetime
