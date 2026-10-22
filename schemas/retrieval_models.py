"""
Data models for the Retrieval Service.
"""
from dataclasses import dataclass
from datetime import date, datetime
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


@dataclass
class KnowledgeHit:
    """1 đoạn tài liệu tri thức (chunks.source_type='knowledge') trả về từ RetrievalService.retrieve_knowledge —
    kèm metadata tài liệu để trích dẫn "(Nguồn: <tên>, tr. N)" (xem services/knowledge.py)."""
    chunk_id: int
    doc_id: int
    chunk_index: int
    content: str
    score: float  # relevance_score sau rerank
    title: str
    publisher: Optional[str] = None
    issued_on: Optional[date] = None
    verified: bool = False
    doc_kind: str = "reference"
    page_from: Optional[int] = None
    page_to: Optional[int] = None
    heading: Optional[str] = None
