"""
Data models for the Retrieval Service.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class RetrievedDocument:
    chunk_id: int
    source_type: str
    source_id: int
    content: str
    score: float  # Điểm số RRF hoặc relevance score sau reranking
    # Chỉ có giá trị khi source_type == "article" (join từ bảng articles) —
    # dùng để trích dẫn nguồn + thời gian phát hành trong câu trả lời Quote Chat,
    # và để FE render "Danh sách tin tức tham khảo" (link + ngày) dưới câu trả lời.
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    url: Optional[str] = None
    title: Optional[str] = None
