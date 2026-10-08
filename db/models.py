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


class NewsCrawlSource(Base):
    """Cấu hình 1 nguồn tin để crawl_news/crawler.py::crawl_source() lấy bài — thay thế
    cho sources.yaml hardcode trước đây, admin CRUD qua /api/admin/news-sources.
    `domain` KHÔNG unique (vd eia.gov, carbonbrief.org có nhiều listing_url/nguồn con
    khác nhau trong cùng domain, y hệt sources.yaml cũ) — unique key thực tế là `name`.
    `source_type` ứng với field `type` của schemas.crawl_models.SourceConfig (đổi tên
    tránh nhầm với builtin `type`, cùng quy ước với Chunk.source_type).
    is_noon_crawl: trước đây chọn nhóm nguồn cho đợt crawl phụ 12:00; scheduler.py giờ crawl
    nguồn theo `region` mỗi giờ trong giờ hành chính nên cờ này chỉ còn dùng khi chạy tay
    main.main(noon_only=True). `region` quyết định khung giờ crawl: 'vietnam' 08–17h giờ VN,
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
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
    """KHÔNG CÒN ĐƯỢC GHI — Lucy QC giờ là tool `lucy_qc` của Jenny chat, trả kết quả thẳng
    trong hội thoại, không lưu bảng này. Giữ model + bảng để không mất lịch sử và không phá
    chuỗi migration đã chạy; xoá bằng 1 migration mới khi chắc chắn không cần nữa.

    (Thiết kế cũ) 1 dòng = 1 lần Lucy QC báo cáo `report_date` trước khi duyệt.
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
