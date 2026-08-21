"""
Data models for the Retrieval Service.
"""
from dataclasses import dataclass

@dataclass
class RetrievedDocument:
    chunk_id: int
    source_type: str
    source_id: int
    content: str
    score: float  # Điểm số RRF hoặc relevance score sau reranking
