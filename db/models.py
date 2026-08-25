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
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from core.config import Settings
from db.base import Base


def _get_embedding_dim() -> int:
    """Lazy load để tránh import error khi test mà không có .env."""
    return Settings.from_env().vector_dimension


EMBEDDING_DIM = _get_embedding_dim()


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
            "region IN ('vietnam', 'international')",
            name="ck_articles_region",
        ),
        CheckConstraint(
            "topic <@ ARRAY["
            "'eua_ets','energy_gas','energy_power_eu','energy_coal','energy_oil',"
            "'energy_renewable','energy_hydrogen','geopolitics','eu_policy',"
            "'cbam','vcm','global_carbon_market','vietnam_carbon_policy'"
            "]::text[]",
            name="ck_articles_topic",
        ),
        Index(
            "idx_articles_published_relevant", "published_at",
            postgresql_where=text("is_relevant = true"),
        ),
        Index(
            "idx_articles_hot_news_crawled", "crawled_at",
            postgresql_where=text("is_hot_news = true"),
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
    topic: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))  # 1–3 topic từ NewsTopic
    # Phạm vi nguồn tin — 'vietnam' hay 'international', lấy từ SourceConfig.region
    # (sources.yaml). Dùng để tách Mục 6 báo cáo thành 2 nhóm Quốc tế / Việt Nam.
    region: Mapped[str] = mapped_column(Text, nullable=False, server_default="international")
    # Mục 8 HOT NEWS (xem crawl_news/classification.py) — đẩy lên chuông thông
    # báo trên header khi true. hot_news_reason: LLM giải thích ngắn khớp tiêu chí nào.
    is_hot_news: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    hot_news_reason: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"Article(id={self.id!r}, url={self.url!r})"


class Chunk(Base):
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


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[Optional[str]] = mapped_column(Text)
    unit: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"Instrument(id={self.id!r}, code={self.code!r})"


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint("instrument_id", "price_date", name="uq_prices_instrument_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, ForeignKey("instruments.id"), nullable=False)
    price_date: Mapped[str] = mapped_column(Text, nullable=False)
    price_time: Mapped[str] = mapped_column(Text, nullable=False)
    open_price: Mapped[Optional[float]] = mapped_column(Float)
    high_price: Mapped[Optional[float]] = mapped_column(Float)
    low_price: Mapped[Optional[float]] = mapped_column(Float)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    day_change_pct: Mapped[Optional[float]] = mapped_column(Float)
    week_change_pct: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[float]] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"Price(id={self.id!r}, instrument_id={self.instrument_id!r}, date={self.price_date!r})"


class PriceCrawlSource(Base):
    """Cấu hình 1 hợp đồng để crawl_prices/crawl_barchart.py lấy giá — thay thế
    cho BARCHART_SPECS hardcode trước đây, admin CRUD qua /api/admin/price-sources."""

    __tablename__ = "price_crawl_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)  # ký hiệu Barchart, vd "NG*0"
    instrument_code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    instrument_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"PriceCrawlSource(id={self.id!r}, instrument_code={self.instrument_code!r})"


class User(Base):
    """Gmail được admin cho phép đăng nhập vào màn hình daily report (đăng nhập
    bằng email + mã OTP, không có mật khẩu)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, email={self.email!r})"


class OtpCode(Base):
    """Mã OTP ngắn hạn gửi qua email cho luồng đăng nhập user. Lưu hash, không
    lưu mã gốc; attempt_count để khoá sau N lần nhập sai."""

    __tablename__ = "otp_codes"
    __table_args__ = (
        Index("idx_otp_codes_email_created", "email", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"OtpCode(id={self.id!r}, email={self.email!r})"


class Report(Base):
    """status='generating': job nền đang chạy (content=None); 'failed': job nền
    lỗi (error_message giữ lại chi tiết); 'draft'/'published' như trước —
    xem POST /api/admin/reports/generate."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'generating', 'failed')", name="ck_reports_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(Text, unique=True, nullable=False) # format YYYY-MM-DD
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    content: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True) # Chứa cục JSON 9 section — None khi đang generating
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"Report(id={self.id!r}, date={self.report_date!r}, status={self.status!r})"


class ChatSession(Base):
    """1 phiên Quote Chat = 1 đoạn (quote) người dùng bôi đen trong báo cáo +
    toàn bộ hội thoại hỏi-đáp xoay quanh đoạn đó. `chat_messages` con của phiên
    này là bộ nhớ ngắn hạn của chatbot — nạp lại N tin nhắn gần nhất làm context
    cho LLM thay vì client phải gửi lại lịch sử mỗi request."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("idx_chat_sessions_user_report", "user_email", "report_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    report_date: Mapped[str] = mapped_column(Text, nullable=False)  # trùng Report.report_date, không FK cứng vì report có thể chưa publish khi lưu draft session
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"ChatSession(id={self.id!r}, user_email={self.user_email!r}, report_date={self.report_date!r})"


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        Index("idx_chat_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ChatMessage(id={self.id!r}, session_id={self.session_id!r}, role={self.role!r})"
