from datetime import date, datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
    # Thời điểm đã gửi email digest hot news cho bài này (NULL = chưa gửi) — xem
    # services/hot_news_email.py. Lưu ở DB (không phải buffer in-memory) để mỗi
    # bài chỉ gửi đúng 1 lần, và gửi mail lỗi thì lần crawl sau tự gửi lại.
    hot_news_emailed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"Article(id={self.id!r}, url={self.url!r})"


class Chunk(Base):
    """`source_type='knowledge'`: đoạn của tài liệu admin tải lên (source_id = knowledge_documents.id, xem
    services/knowledge.py). Tài liệu chỉ là phần nhỏ so với chunk bài báo, nên có index vector/full-text MỘT PHẦN
    riêng: HNSW chung lọc SAU khi lấy ứng viên (lọc source_type trên index chung dễ ra 0 kết quả), và cấu hình
    'english' của content_tsv không hợp tiếng Việt → tài liệu tìm full-text bằng to_tsvector('simple', content)."""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("source_type IN ('report', 'article', 'knowledge')", name="ck_chunks_source_type"),
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
        Index(
            "idx_chunks_knowledge_embedding", "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("source_type = 'knowledge'"),
        ),
        Index(
            "idx_chunks_knowledge_tsv_simple", text("to_tsvector('simple', content)"),
            postgresql_using="gin",
            postgresql_where=text("source_type = 'knowledge'"),
        ),
    )

    chunk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # articles.id / daily_reports.id / knowledge_documents.id
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)  # thứ tự đoạn trong bài
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 1 đoạn văn
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(EMBEDDING_DIM))
    content_tsv: Mapped[Optional[str]] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    # Chỉ dùng cho source_type='knowledge': {"page_from", "page_to", "heading"} — trích dẫn "tr. N" + đề mục.
    meta: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
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


class NewsCrawlSource(Base):
    """Cấu hình 1 nguồn tin để crawl_news/crawler.py::crawl_source() lấy bài — thay thế
    cho sources.yaml hardcode trước đây, admin CRUD qua /api/admin/news-sources.
    `domain` KHÔNG unique (vd eia.gov, carbonbrief.org có nhiều listing_url/nguồn con
    khác nhau trong cùng domain, y hệt sources.yaml cũ) — unique key thực tế là `name`.
    `source_type` ứng với field `type` của schemas.crawl_models.SourceConfig (đổi tên
    tránh nhầm với builtin `type`, cùng quy ước với Chunk.source_type).
    is_noon_crawl: trước đây chọn nhóm nguồn cho đợt crawl phụ 12:00; scheduler.py giờ crawl
    nguồn theo `region` mỗi giờ trong giờ hành chính nên cờ này chỉ còn dùng khi chạy tay
    main.main(noon_only=True). `region` quyết định khung giờ crawl: 'vietnam' 06h + 08–17h giờ VN,
    'international' 08–17h giờ New York (xem scheduler.py::NEWS_CRAWL_SCHEDULES)."""

    __tablename__ = "news_crawl_sources"
    __table_args__ = (
        CheckConstraint("tier IN ('A', 'B', 'C')", name="ck_news_crawl_sources_tier"),
        CheckConstraint(
            "region IN ('vietnam', 'international')", name="ck_news_crawl_sources_region"
        ),
        CheckConstraint(
            "source_type IN ('html', 'rss', 'bloomberg_rss')",
            name="ck_news_crawl_sources_source_type",
        ),
        Index("idx_news_crawl_sources_domain", "domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False, server_default="international")
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="html")
    listing_url: Mapped[Optional[str]] = mapped_column(Text)
    rss_url: Mapped[Optional[str]] = mapped_column(Text)
    link_pattern: Mapped[Optional[str]] = mapped_column(Text)
    # NULL = dùng default list trong SourceConfig.exclude_path_patterns (xem
    # main.py::_source_config_from_row), chỉ set khi admin override thủ công.
    exclude_path_patterns: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    group: Mapped[Optional[List[int]]] = mapped_column(ARRAY(Integer))
    bloomberg_feeds: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    confidence: Mapped[Optional[str]] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)
    max_articles: Mapped[Optional[int]] = mapped_column(Integer)
    use_playwright: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_noon_crawl: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"NewsCrawlSource(id={self.id!r}, domain={self.domain!r}, name={self.name!r})"


class User(Base):
    """Email (mọi tên miền) được admin cho phép đăng nhập vào màn hình daily report (đăng nhập
    bằng email + mã OTP, không có mật khẩu)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("relation_type IN ('superior', 'peer')", name="ck_users_relation_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Quan hệ với Jenny: 'superior' (cấp trên: quyền admin + chỉnh sửa/đánh giá) hoặc
    # 'peer' (đồng cấp: xem, chat, hỏi tư vấn, đánh giá — không được sửa).
    relation_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="peer")
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
    # Thời điểm đã gửi email PDF báo cáo cho user (services/report_email.py) — NULL = chưa gửi.
    pdf_emailed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Bản tiếng Anh của `content` (services/report_translation.py). `content_en_source_hash` =
    # hash của `content` lúc dịch — lệch với hash hiện tại nghĩa là bản dịch đã cũ (admin sửa sau đó).
    content_en: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    content_en_source_hash: Mapped[Optional[str]] = mapped_column(Text)

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
        CheckConstraint("rating IN ('good', 'bad')", name="ck_chat_sessions_rating"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    report_date: Mapped[str] = mapped_column(Text, nullable=False)  # trùng Report.report_date, không FK cứng vì report có thể chưa publish khi lưu draft session
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    # Đánh giá phiên chat của user: 'good'/'bad'/None (chưa đánh giá).
    # rating_reason bắt buộc (validate ở API) khi rating='bad' để admin biết vì sao.
    rating: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rating_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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
    # Danh sách file đính kèm (ảnh/PDF/Word) — mảng [{file_name, file_key,
    # media_type}], xem schemas/chat_models.py::Attachment. NULL cho hầu hết
    # tin nhắn (không đính kèm gì) và luôn NULL cho role='assistant'.
    attachments: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ChatMessage(id={self.id!r}, session_id={self.session_id!r}, role={self.role!r})"


class EuaFrameworkOverride(Base):
    """Nội dung admin tự custom cho 1 khối tri thức của khung nhân quả EUA
    (xem services/eua_causal_chains.py::BLOCK_DEFAULTS) — ghi đè bản mặc định
    trong code khi sinh báo cáo/chat. Bảng THƯA: 1 block chỉ có row ở đây nếu
    đã bị admin custom; không có row -> dùng bản mặc định trong code."""

    __tablename__ = "eua_framework_overrides"

    block_id: Mapped[str] = mapped_column(Text, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"EuaFrameworkOverride(block_id={self.block_id!r})"


class QuoteChatExample(Base):
    """Ví dụ mẫu (few-shot) cho Quote Chat — admin chọn 1 cặp hỏi-đáp trong lịch
    sử chat (xem api/routers/admin_chat_reviews.py) mà mình đánh giá là phân
    tích hợp lý; hệ thống tiêm lại các cặp này vào system prompt của MỌI phiên
    Quote Chat sau đó làm chuẩn tham khảo về cách suy luận/văn phong (xem
    services/quote_chat_examples.py::build_few_shot_prompt_block) — KHÔNG phải
    để model chép lại số liệu/sự kiện cụ thể, vì các cặp này có thể thuộc
    quote/report_date khác với phiên đang chạy.

    `question`/`answer` là BẢN SAO nội dung tại thời điểm thêm (không tham
    chiếu sống) — ví dụ vẫn còn dùng được dù `chat_sessions`/`chat_messages`
    gốc sau này bị xoá (source_*_id chỉ để truy vết, ON DELETE SET NULL).
    Unique trên source_answer_message_id để bấm "thêm" nhiều lần không tạo
    trùng — xem services/quote_chat_examples.py::create_example_from_message.
    """

    __tablename__ = "quote_chat_examples"
    __table_args__ = (
        UniqueConstraint("source_answer_message_id", name="uq_quote_chat_examples_answer_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_session_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True
    )
    source_answer_message_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"QuoteChatExample(id={self.id!r})"


class AssistantFeedback(Base):
    """Phản ánh của người dùng về thái độ/hiệu quả phục vụ của AI assistant
    (hiện đặt tên hiển thị là "Jenny" ở UI — tên bảng để trung lập, tránh phải
    migrate nếu sau này đổi tên nhân vật). Chỉ gửi được khi đã đăng nhập —
    user_email/reporter_role lấy từ JWT payload (api/routers/feedback.py),
    không nhận từ client để tránh giả mạo danh tính."""

    __tablename__ = "assistant_feedbacks"
    __table_args__ = (
        CheckConstraint(
            "reporter_role IN ('user', 'admin')", name="ck_assistant_feedbacks_reporter_role"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    reporter_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reporter_role: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"AssistantFeedback(id={self.id!r}, user_email={self.user_email!r})"


class ReleaseNote(Base):
    """1 dòng = 1 mục trong bảng theo dõi yêu cầu khách hàng / release note,
    hiển thị ở /admin/release-notes — admin CRUD trực tiếp qua
    /api/admin/release-notes (xem api/routers/admin_release_notes.py).
    UI sort theo note_date giảm dần (mới nhất lên đầu) — thay cho cột STT cũ
    dựa trên order_index (cột order_index vẫn giữ lại trong schema nhưng
    không còn dùng để quyết định thứ tự hiển thị)."""

    __tablename__ = "release_notes"
    __table_args__ = (
        CheckConstraint("status IN ('dat', 'chua_dat')", name="ck_release_notes_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    note_date: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())  # NGÀY — thay cho cột STT, dùng để sort mới nhất lên đầu
    customer_request: Mapped[str] = mapped_column(Text, nullable=False)  # YÊU CẦU KHÁCH HÀNG
    change_description: Mapped[str] = mapped_column(Text, nullable=False)  # NỘI DUNG ĐÃ CHỈNH SỬA
    test_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # KẾT QUẢ KIỂM TRA THỰC TẾ TRÊN BÁO CÁO
    # Ảnh minh chứng cho "Kết quả kiểm tra thực tế" — mảng [{file_name, file_key,
    # media_type}], cùng dạng dữ liệu với ChatMessage.attachments (xem
    # services/minio_service.py + schemas/chat_models.py::Attachment). NULL nếu
    # không đính kèm ảnh nào.
    evidence_images: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="chua_dat")  # KẾT LUẬN: 'dat' | 'chua_dat'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"ReleaseNote(id={self.id!r}, status={self.status!r})"


class BizSuggestion(Base):
    """Bộ nhớ gợi ý kinh doanh ngắn hạn của Jenny (mục "Gợi ý kinh doanh & giải
    pháp cho SIM"). Mỗi gợi ý ngắn hạn sinh ra được lưu lại để các báo cáo sau:
    - kiểm tra "tình huống kích hoạt" đã xảy ra chưa → nếu có thì NHẮC LẠI trong
      báo cáo ngày đó ("Ngày 18/09 Jenny đã đề xuất...") rồi thôi theo dõi;
    - thực tế diễn ra NGƯỢC với giả định của đề xuất (status='contradicted') → báo
      cập nhật trong báo cáo ngày đó rồi thôi theo dõi;
    - chưa xảy ra → chỉ nằm trong bộ nhớ Jenny, không hiện trong báo cáo ngày.
    Chỉ nhớ trong BIZ_SUGGESTION_MEMORY_DAYS ngày kể từ first_report_date (xem
    services/biz_memory.py). first_report_date/triggered_report_date cùng định
    dạng "YYYY-MM-DD" với reports.report_date."""

    __tablename__ = "biz_suggestions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'triggered', 'contradicted', 'dismissed')", name="ck_biz_suggestions_status"
        ),
        CheckConstraint("kind IN ('short', 'long')", name="ck_biz_suggestions_kind"),
        Index("ix_biz_suggestions_status_date", "status", "first_report_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_report_date: Mapped[str] = mapped_column(Text, nullable=False)  # báo cáo đã đề xuất
    # 'short' = gợi ý ngắn hạn (có tình huống kích hoạt, được theo dõi/nhắc lại);
    # 'long' = gợi ý dài hạn — chỉ lưu để admin gỡ được + LLM không đề xuất lại ý đã
    # gỡ; trigger/action/reason chứa opportunity/solution/expectation.
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="short")
    trigger: Mapped[str] = mapped_column(Text, nullable=False)  # điều kiện "Nếu/Khi ..." hướng tới tương lai
    # Quy tắc ngưỡng giá để backend tự kiểm tra (không cần LLM): {"code": "BRENT", "op": ">", "value": 100}
    trigger_rule: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    triggered_report_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trigger_evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_source_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Admin gỡ khỏi báo cáo (status='dismissed') — không theo dõi/nhắc lại nữa,
    # và được báo cho LLM để không đề xuất lại ý tương tự trong thời gian nhớ.
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dismiss_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"BizSuggestion(id={self.id!r}, date={self.first_report_date!r}, status={self.status!r})"


class ReportView(Base):
    """1 dòng = 1 lượt MỞ báo cáo đã phát hành (GET /api/reports/{date}) — nguồn
    số liệu "lượt xem" cho trang Thống kê hiệu suất (api/routers/admin_stats.py).
    Cùng 1 người mở lại cùng báo cáo trong REPORT_VIEW_DEDUPE_MINUTES phút chỉ tính
    1 lượt (tránh F5/chuyển tab làm phồng số liệu — xem api/main.py)."""

    __tablename__ = "report_views"
    __table_args__ = (
        Index("ix_report_views_report_date", "report_date"),
        Index("ix_report_views_viewer_date", "viewer", "report_date", "viewed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(Text, nullable=False)  # trùng Report.report_date
    viewer: Mapped[str] = mapped_column(Text, nullable=False)  # email user / username admin (JWT "sub")
    role: Mapped[str] = mapped_column(Text, nullable=False)  # 'user' | 'admin' — thống kê chỉ đếm 'user'
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ReportView(id={self.id!r}, report_date={self.report_date!r}, viewer={self.viewer!r})"


class ApiToken(Base):
    """Token cá nhân để MCP server cục bộ trên máy user (Claude Desktop) gọi
    /api/mcp-gateway/* — thay cho cookie JWT (Claude Desktop không có cookie
    trình duyệt). Chỉ lưu SHA-256 của token (token gốc hiện đúng 1 lần lúc tạo);
    `token_prefix` là vài ký tự đầu để user nhận ra token nào trong danh sách."""

    __tablename__ = "api_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
        Index("idx_api_tokens_user_email", "user_email"),
        CheckConstraint("scope IN ('user', 'qc_agent')", name="ck_api_tokens_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 'user': token cá nhân (chỉ đọc, chỉ báo cáo published). 'qc_agent': admin tạo cho lịch Claude QC 08:00 —
    # thêm quyền đọc bản NHÁP để QC và ghi biên bản (get_qc_assignment / submit_qc_review); không gắn với bảng users.
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ApiToken(id={self.id!r}, user_email={self.user_email!r}, prefix={self.token_prefix!r})"


class ClaudeHandoff(Base):
    """1 dòng = 1 lần user bấm "Hỏi Claude" từ quote trong báo cáo: gói quote +
    câu hỏi + loại tác vụ để Claude Desktop lấy về qua MCP tool `get_handoff`.
    Chỉ là gói ngữ cảnh đi MỘT CHIỀU — câu trả lời/file do Claude sinh ra ở
    Claude Desktop KHÔNG được lưu ngược lại hệ thống."""

    __tablename__ = "claude_handoffs"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('pdf', 'excel', 'strategy', 'other')",
            name="ck_claude_handoffs_task_type",
        ),
        UniqueConstraint("handoff_id", name="uq_claude_handoffs_handoff_id"),
        Index("idx_claude_handoffs_user_email", "user_email"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    handoff_id: Mapped[str] = mapped_column(Text, nullable=False)  # chuỗi ngắn ngẫu nhiên, dán vào prompt
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    report_date: Mapped[str] = mapped_column(Text, nullable=False)  # trùng Report.report_date
    quote: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="other")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # lần ĐẦU Claude lấy gói
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ClaudeHandoff(id={self.id!r}, handoff_id={self.handoff_id!r}, user_email={self.user_email!r})"


class ReportQCResult(Base):
    """KHÔNG CÒN ĐƯỢC GHI — Jack QC giờ là tool `jack_qc` của Jenny chat, trả kết quả thẳng
    trong hội thoại, không lưu bảng này. Giữ model + bảng để không mất lịch sử và không phá
    chuỗi migration đã chạy; xoá bằng 1 migration mới khi chắc chắn không cần nữa.

    (Thiết kế cũ) 1 dòng = 1 lần Jack QC báo cáo `report_date` trước khi duyệt.
    Giữ lịch sử mọi lần chạy — GET /api/admin/reports/{date}/qc-results chỉ lấy dòng MỚI NHẤT.
    status='running': job nền đang chạy (điểm = None); 'done': có điểm + issues;
    'failed': job lỗi (error_message). Mỗi phần tử `issues`:
    {"check", "section", "severity": "error"|"warning"|"info", "message", "field_path"}."""

    __tablename__ = "report_qc_results"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'done', 'failed')", name="ck_report_qc_results_status"),
        Index("ix_report_qc_results_report_date", "report_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(
        Text, ForeignKey("reports.report_date", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    overall_score: Mapped[Optional[int]] = mapped_column(Integer)
    price_accuracy_score: Mapped[Optional[int]] = mapped_column(Integer)
    scenario_score: Mapped[Optional[int]] = mapped_column(Integer)
    source_score: Mapped[Optional[int]] = mapped_column(Integer)
    calendar_score: Mapped[Optional[int]] = mapped_column(Integer)
    biz_score: Mapped[Optional[int]] = mapped_column(Integer)
    # 2 check LLM (services/report_qc_llm.py) — None nếu chạy QC nhanh (llm=false) hoặc gọi LLM lỗi.
    consistency_score: Mapped[Optional[int]] = mapped_column(Integer)
    causal_score: Mapped[Optional[int]] = mapped_column(Integer)
    issues: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    checked_by: Mapped[Optional[str]] = mapped_column(Text)  # JWT "sub" của admin bấm QC

    def __repr__(self) -> str:
        return f"ReportQCResult(id={self.id!r}, date={self.report_date!r}, status={self.status!r})"


class ReportQcReview(Base):
    """1 dòng = 1 biên bản QC báo cáo do Claude (vai Jack) chấm rồi gửi qua MCP (tool `submit_qc_review`, token
    `scope='qc_agent'`) — luồng tự động: sinh nháp 07:00 → Claude QC 08:00 → tự xuất bản 08:10 (chặn khi
    `verdict='fail'`). Xem services/report_qc_review.py (rubric, chuẩn hóa, tính điểm).

    `overall_score` (thang 10) do SERVER tính lại từ `criteria` theo trọng số rubric, không lấy số Claude tự cộng.
    `content_hash` = hash `reports.content` lúc Claude đọc bản nháp — lệch hash hiện tại nghĩa là báo cáo đã bị sửa
    sau lần QC (API trả `is_stale`). `run_no` tăng dần theo từng báo cáo (QC lại không ghi đè lần trước)."""

    __tablename__ = "report_qc_reviews"
    __table_args__ = (
        CheckConstraint("verdict IN ('pass', 'pass_with_fixes', 'fail')", name="ck_report_qc_reviews_verdict"),
        CheckConstraint("source IN ('claude_mcp', 'admin_manual')", name="ck_report_qc_reviews_source"),
        UniqueConstraint("report_date", "run_no", name="uq_report_qc_reviews_date_run"),
        Index("ix_report_qc_reviews_report_date", "report_date"),
        Index("ix_report_qc_reviews_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(
        Text, ForeignKey("reports.report_date", ondelete="CASCADE"), nullable=False
    )
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="claude_mcp")
    reviewer: Mapped[Optional[str]] = mapped_column(Text)  # vd "Jack (claude-opus-5-5)"
    token_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("api_tokens.id", ondelete="SET NULL"))
    report_status: Mapped[str] = mapped_column(Text, nullable=False)  # trạng thái báo cáo lúc QC
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    claude_verdict: Mapped[Optional[str]] = mapped_column(Text)  # kết luận Claude tự đề xuất (trước khi server chuẩn hóa)
    overall_score: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    # [{"key", "label", "weight", "score" (0–10), "comment"}] — đúng thứ tự rubric
    criteria: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    critical_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    major_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    minor_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Jack rule-based (services/report_qc.py) chạy cùng lúc trên đúng bản nháp — để đối chiếu.
    jack_rule_score: Mapped[Optional[int]] = mapped_column(Integer)
    jack_rule_issue_count: Mapped[Optional[int]] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # [{"rank", "text", "expected_gain"}]
    priorities: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    body_markdown: Mapped[Optional[str]] = mapped_column(Text)  # biên bản đầy đủ Claude viết (hiển thị nguyên văn)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ReportQcReview(id={self.id!r}, date={self.report_date!r}, run={self.run_no!r}, verdict={self.verdict!r})"


class ReportQcFinding(Base):
    """1 lỗi/nhận xét trong 1 biên bản QC. `fingerprint` (tiêu chí + mục + từ khóa tiêu đề đã chuẩn hóa) dùng để
    đếm `repeat_count` — số báo cáo liên tiếp gần đây có cùng lỗi (bản chấm: "lỗi nhắc lần thứ 3")."""

    __tablename__ = "report_qc_findings"
    __table_args__ = (
        CheckConstraint("severity IN ('critical', 'major', 'minor')", name="ck_report_qc_findings_severity"),
        CheckConstraint(
            "verification IN ('verified', 'partially_verified', 'unverified')", name="ck_report_qc_findings_verification"
        ),
        CheckConstraint("status IN ('open', 'fixed', 'wont_fix')", name="ck_report_qc_findings_status"),
        Index("ix_report_qc_findings_review_id", "review_id"),
        Index("ix_report_qc_findings_fingerprint", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("report_qc_reviews.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    criterion: Mapped[str] = mapped_column(Text, nullable=False)  # khoá rubric (report_qc_review.QC_CRITERIA)
    section: Mapped[Optional[str]] = mapped_column(Text)  # "1" | "2" | "3" | "4" | "6" | "8" | "9" | "biz" | "forecast" | "cbam" | "general"
    location: Mapped[Optional[str]] = mapped_column(Text)  # vị trí người đọc thấy, vd "Cần theo dõi #1", "trang 7"
    field_path: Mapped[Optional[str]] = mapped_column(Text)  # đường dẫn JSON trong reports.content nếu xác định được
    title: Mapped[str] = mapped_column(Text, nullable=False)
    current_text: Mapped[Optional[str]] = mapped_column(Text)  # báo cáo đang viết
    expected_text: Mapped[Optional[str]] = mapped_column(Text)  # nên sửa thành
    evidence: Mapped[Optional[str]] = mapped_column(Text)  # phép tính / số đối chiếu DB / trích nguồn
    verification: Mapped[str] = mapped_column(Text, nullable=False, server_default="unverified")
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))  # [{"label","url"}]
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    repeat_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    status_note: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"ReportQcFinding(id={self.id!r}, review={self.review_id!r}, severity={self.severity!r})"


class CrawlSeenUrl(Base):
    """URL crawler đã fetch/xử lý nhưng KHÔNG thành 1 dòng `articles` (bị LLM loại,
    ngoài cửa sổ ngày, extract lỗi, ...). `articles` chỉ chứa bài đã lưu, nên nếu không
    nhớ các URL này thì mỗi đợt crawl (chạy MỖI GIỜ — xem scheduler.py) lại fetch +
    gọi LLM phân loại lại đúng những bài đó. main.py gộp các URL này vào `seen_urls`
    (services/storage.py::load_recent_urls) để crawler bỏ qua ngay từ listing page.

    status (xem services/storage.py::SEEN_PERMANENT_STATUSES / SEEN_RETRY_STATUSES):
      - irrelevant / skipped_old / duplicate / bloomberg_resolved: bỏ qua luôn.
      - extraction_failed / classification_failed / bloomberg_unresolved: lỗi có thể
        tạm thời — vẫn thử lại, chỉ bỏ qua khi `attempts` đạt ngưỡng.
    Chỉ giữ tác dụng trong cửa sổ `last_seen_at` gần đây (cùng 7 ngày như `articles`)."""

    __tablename__ = "crawl_seen_urls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('irrelevant', 'skipped_old', 'duplicate', 'extraction_failed', "
            "'classification_failed', 'bloomberg_resolved', 'bloomberg_unresolved')",
            name="ck_crawl_seen_urls_status",
        ),
        Index("idx_crawl_seen_urls_last_seen_at", "last_seen_at"),
    )

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"CrawlSeenUrl(url={self.url!r}, status={self.status!r}, attempts={self.attempts!r})"


class EuaAuction(Base):
    """1 phiên đấu giá sơ cấp EUA trên EEX (crawl_prices/crawl_eex_auctions.py).

    Cùng bảng chứa cả LỊCH (status='scheduled', chưa có giá — từ file Auction Calendar) lẫn
    KẾT QUẢ (status='successful'/'cancelled' — từ file Primary Market Auction Report); kết quả
    ghi đè dòng lịch cùng (auction_date, auction_name). Giá CBAM 2026 EC công bố theo quý = VWAP
    (gia quyền khối lượng) giá khớp của mọi phiên trong quý — xem services/cbam_estimate.py."""

    __tablename__ = "eua_auctions"
    __table_args__ = (
        UniqueConstraint("auction_date", "auction_name", name="uq_eua_auctions_date_name"),
        CheckConstraint("status IN ('scheduled', 'successful', 'cancelled')", name="ck_eua_auctions_status"),
        Index("idx_eua_auctions_auction_date", "auction_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auction_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Tên chuẩn hoá theo nền tảng: "EU" / "DE" / "PL" / "NIR" (file lịch và file kết quả đặt tên khác nhau).
    auction_name: Mapped[str] = mapped_column(Text, nullable=False)
    contract: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)  # tCO2 — kế hoạch (lịch) hoặc thực đấu giá
    auction_price: Mapped[Optional[float]] = mapped_column(Float)  # EUR/tCO2, giá khớp
    cover_ratio: Mapped[Optional[float]] = mapped_column(Float)
    total_bidders: Mapped[Optional[int]] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"EuaAuction({self.auction_date!r}, {self.auction_name!r}, {self.status!r}, {self.auction_price!r})"


class ReportLesson(Base):
    """Bài học rút ra từ góp ý của người dùng trong Jenny chat (services/report_lessons.py).
    Jenny chỉ ghi SAU KHI đã kiểm tra bằng tool và xác nhận báo cáo sai/thiếu THẬT (tool
    record_report_lesson); mỗi lần sinh báo cáo, các bài học còn hiệu lực được đưa vào prompt
    của mục tương ứng để không lặp lại lỗi. `section` là khoá mục của bộ sinh ("1", "2", "dev",
    "3", "4", "8", "biz") hoặc "general" (áp dụng mọi mục). Admin tắt bằng status='dismissed'."""

    __tablename__ = "report_lessons"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'dismissed')", name="ck_report_lessons_status"),
        CheckConstraint(
            "section IN ('1', '2', 'dev', '3', '4', '8', 'biz', 'general')", name="ck_report_lessons_section"
        ),
        Index("ix_report_lessons_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(Text, nullable=False)  # báo cáo bị góp ý, "YYYY-MM-DD"
    section: Mapped[str] = mapped_column(Text, nullable=False, server_default="general")
    mistake: Mapped[str] = mapped_column(Text, nullable=False)  # báo cáo đã viết sai gì
    correction: Mapped[str] = mapped_column(Text, nullable=False)  # nội dung/số đúng sau khi đối chiếu
    lesson: Mapped[str] = mapped_column(Text, nullable=False)  # quy tắc áp dụng cho các báo cáo sau
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # nguồn/tool đã đối chiếu
    reported_by: Mapped[str] = mapped_column(Text, nullable=False)
    chat_session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ReportLesson(id={self.id!r}, date={self.report_date!r}, section={self.section!r}, status={self.status!r})"


KNOWLEDGE_SECTIONS = ("1", "2", "dev", "3", "4", "8", "biz")


class KnowledgeDocument(Base):
    """Tài liệu tri thức (.pdf/.docx) admin tải lên cho Jenny (services/knowledge.py): trích chữ → chia đoạn → nhúng
    vector vào `chunks` (source_type='knowledge', source_id=id) → dùng khi sinh báo cáo (mục trong `report_sections`)
    và Jenny chat (tool search_knowledge). `content` = toàn văn đã trích (xem trước, chia lại không cần parse lại, Jack
    đối chiếu số). Xoá = xoá mềm (status='deleted', xoá chunks + file MinIO) để báo cáo cũ đã trích vẫn tra được tên.
    `valid_from/valid_to` so với NGÀY DỮ LIỆU của báo cáo / hôm nay (chat)."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'deleted')", name="ck_knowledge_documents_status"
        ),
        CheckConstraint("media_type IN ('pdf', 'docx')", name="ck_knowledge_documents_media_type"),
        CheckConstraint(
            "doc_kind IN ('regulation', 'research', 'market_report', 'internal', 'reference')",
            name="ck_knowledge_documents_doc_kind",
        ),
        CheckConstraint("language IN ('vi', 'en')", name="ck_knowledge_documents_language"),
        CheckConstraint(
            "report_sections IS NULL OR report_sections <@ ARRAY["
            + ",".join(f"'{s}'" for s in KNOWLEDGE_SECTIONS) + "]::text[]",
            name="ck_knowledge_documents_report_sections",
        ),
        UniqueConstraint("sha256", name="uq_knowledge_documents_sha256"),
        Index("ix_knowledge_documents_status_active", "status", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)  # tên hiển thị + nhãn "(Nguồn: …)"
    publisher: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # tổ chức ban hành / tác giả
    issued_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # ngày ban hành
    doc_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="reference")
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default="vi")
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'pdf' | 'docx'
    file_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # MinIO knowledge/<uuid>.<ext>; NULL sau khi xoá
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(CHAR(64), nullable=True)  # NULL sau khi xoá → tải lại được
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)  # cảnh báo trích xuất (trang scan, bảng...)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Trang/đề mục đã trích: [{"heading", "page_from", "page_to", "text"}] — xem trước theo trang và chia lại.
    sections: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    use_in_report: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    report_sections: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text), nullable=True)  # NULL = mọi mục
    use_in_chat: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"KnowledgeDocument(id={self.id!r}, title={self.title!r}, status={self.status!r})"
