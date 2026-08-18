from datetime import datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from core.config import Settings
from db.base import Base

settings = Settings.from_env()
EMBEDDING_DIM = settings.vector_dimension


class Article(Base):
    """1 dòng = 1 bài viết đầy đủ. Là nơi dedup (qua content_hash) và nơi
    đọc nguyên bài khi cần (viết báo cáo, audit lại sau này)."""

    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint("source_tier IN ('A', 'B', 'C')", name="ck_articles_source_tier"),
        CheckConstraint(
            "date_confidence IN ('metadata', 'url', 'unknown')",
            name="ck_articles_date_confidence",
        ),
        CheckConstraint(
            "category <@ ARRAY['energy_fossil_fuels', 'carbon_credits', 'policy']::text[]",
            name="ck_articles_category",
        ),
        Index(
            "idx_articles_published_relevant", "published_at",
            postgresql_where=text("is_relevant = true"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # domain, vd "reuters.com"
    source_tier: Mapped[Optional[str]] = mapped_column(CHAR(1))
    title: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # toàn văn đã làm sạch
    content_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    date_confidence: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_relevant: Mapped[Optional[bool]] = mapped_column(Boolean)  # từ classify.py
    category: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))  # có thể nhiều nhóm

    def __repr__(self) -> str:
        return f"Article(id={self.id!r}, url={self.url!r})"


class Chunk(Base):
    """Nhiều dòng cho 1 bài/report - phục vụ hybrid search (semantic +
    full-text). Ở pipeline tin tức, source_type luôn là 'article' và
    source_id = articles.id."""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("source_type IN ('report', 'article')", name="ck_chunks_source_type"),
        UniqueConstraint(
            "source_type", "source_id", "chunk_index", name="uq_chunks_source_chunk_index"
        ),
        Index("idx_chunks_source", "source_type", "source_id"),
        Index(
            "idx_chunks_embedding", "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_chunks_tsv", "content_tsv", postgresql_using="gin"),
    )

    chunk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # articles.id / daily_reports.id
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)  # thứ tự đoạn trong bài
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 1 đoạn văn
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(EMBEDDING_DIM))
    content_tsv: Mapped[Optional[str]] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"Chunk(chunk_id={self.chunk_id!r}, source_type={self.source_type!r}, source_id={self.source_id!r})"
