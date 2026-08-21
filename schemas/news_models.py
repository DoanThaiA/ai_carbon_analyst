"""Data models cho chuông thông báo Hot News trên header."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class HotNewsItem(BaseModel):
    id: int
    title: Optional[str]
    url: str
    source: str
    hot_news_reason: Optional[str]
    published_at: Optional[datetime]
    crawled_at: datetime


class HotNewsResponse(BaseModel):
    items: List[HotNewsItem]
