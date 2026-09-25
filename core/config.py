
from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str
    classifier_backend: str
    cohere_api_key: str
    anthropic_api_key: str
    eia_api_key: str
    classifier_model: str
    classify_concurrency: int
    embedding_model: str
    vector_dimension: int
    rerank_model: str

    # Throttle SAU MỖI lần gọi Cohere Embed API thành công (xem
    # services/embedding.py::CohereEmbedder.embed) — cộng thêm ngay cả khi call
    # không hề bị rate limit, ban đầu để né giới hạn 40 calls/phút của gói Free
    # Cohere. Đổi qua ENV (không cần sửa code) một khi đã nâng cấp lên gói trả
    # phí — kiểm tra dashboard Cohere trước khi hạ về 0, vì hàm này DÙNG CHUNG
    # cho cả embed lúc index bài báo (crawl, có thể gọi dồn dập) lẫn embed query
    # (Quote Chat RAG, mỗi câu hỏi 1 lần) — hạ sai khi vẫn ở gói Free có thể gây
    # 429 hàng loạt lúc crawl dù Quote Chat trông vẫn ổn.
    cohere_embed_throttle_seconds: float

    # Quote Chat (hỏi đáp đoạn bôi đen) — luôn dùng Anthropic (client tools +
    # web_search cần backend Anthropic, xem services/quote_chat.py). Model có
    # thể đổi qua ENV nếu cần, không cần sửa code.
    quote_chat_model: str

    # File đính kèm Quote Chat (ảnh/PDF/Word) lưu trên MinIO (S3-compatible).
    # QUAN TRỌNG: `minio_endpoint` PHẢI là host:port PUBLIC-reachable từ trình
    # duyệt (vd "storage.mcv.network" hoặc "sim.mcv.network:9000") — KHÔNG
    # phải hostname nội bộ docker (vd "minio:9000") — vì frontend PUT thẳng
    # file lên MinIO qua presigned URL do backend cấp (services/minio_service.py).
    # Backend cũng dùng CHÍNH endpoint này (không phải hostname nội bộ) để tự
    # tải file về lúc chat — tránh lệch host giữa lúc ký presigned URL và lúc
    # dùng, đổi lại backend gọi MinIO qua internet thay vì mạng nội bộ docker
    # (chấp nhận được, traffic này không lớn/không cần độ trễ thấp).
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool

    # Auth — để trống nếu không dùng API/admin panel (vd script crawl/report
    # generator độc lập không cần các giá trị này). core/security.py và các
    # router auth tự raise lỗi rõ ràng nếu thiếu khi thực sự cần dùng.
    jwt_secret: str
    jwt_algorithm: str
    jwt_expire_minutes: int
    jwt_refresh_expire_minutes: int
    admin_username: str
    admin_password_hash: str
    cookie_secure: bool

    # Email (OTP + digest Hot News) qua Resend — services/email_sender.py.
    # email_from phải thuộc domain đã verify trong Resend, vd
    # "Carbon Analyst <noreply@mcv.network>".
    resend_api_key: str
    email_from: str
    # Địa chỉ thật nhận phản hồi (Reply-To) — trống = không gửi header. Nên đặt:
    # email "noreply" không có Reply-To + không có List-Unsubscribe là tín hiệu
    # spam với Gmail. Khi có, digest cũng kèm List-Unsubscribe (mailto) tới địa chỉ này.
    email_reply_to: str
    otp_expire_minutes: int
    otp_max_attempts: int

    # Email digest Hot News (services/hot_news_email.py) — gửi 1 email tổng hợp
    # SAU MỖI đợt crawl (06:00/12:00), không gửi từng bài. max_age_hours: chỉ gửi
    # bài crawl trong N giờ gần nhất (tránh "xả" tồn đọng cũ khi gửi mail hỏng lâu
    # ngày). batch_size: số email tối đa/1 request Resend batch (mỗi người nhận
    # 1 email riêng; Resend giới hạn 100). app_base_url: link "Mở Carbon Analyst" trong email (trống = ẩn).
    hot_news_email_enabled: bool
    hot_news_email_max_age_hours: int
    hot_news_email_batch_size: int
    app_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL chưa được set. Copy .env.example -> .env và điền connection string."
            )

        backend = os.environ.get("CLASSIFIER_BACKEND", "anthropic").lower()

        cohere_api_key = os.environ.get("COHERE_API_KEY", "")
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        eia_api_key = os.environ.get("EIA_API_KEY", "")

        # Haiku 4.5: siêu rẻ, siêu nhanh — đủ cho tác vụ phân loại JSON.
        default_model = "claude-haiku-4-5"

        return cls(
            database_url=database_url,
            classifier_backend=backend,
            cohere_api_key=cohere_api_key,
            anthropic_api_key=anthropic_api_key,
            eia_api_key=eia_api_key,
            classifier_model=os.environ.get("CLASSIFIER_MODEL", default_model),
            classify_concurrency=int(os.environ.get("CLASSIFY_CONCURRENCY", "5")),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "embed-v4.0"),
            vector_dimension=int(os.environ.get("VECTOR_DIMENSION", "1536")),
            rerank_model=os.environ.get("RERANK_MODEL", "rerank-v3.5"),
            cohere_embed_throttle_seconds=float(os.environ.get("COHERE_EMBED_THROTTLE_SECONDS", "1.5")),
            quote_chat_model=os.environ.get("QUOTE_CHAT_MODEL", "claude-sonnet-5"),
            minio_endpoint=os.environ.get("MINIO_ENDPOINT", ""),
            minio_access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
            minio_secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
            minio_bucket=os.environ.get("MINIO_BUCKET", "quote-chat-attachments"),
            minio_secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
            jwt_secret=os.environ.get("JWT_SECRET", ""),
            jwt_algorithm=os.environ.get("JWT_ALGORITHM", "HS256"),
            jwt_expire_minutes=int(os.environ.get("JWT_EXPIRE_MINUTES", str(60 * 24 * 7))),
            jwt_refresh_expire_minutes=int(os.environ.get("JWT_REFRESH_EXPIRE_MINUTES", str(60 * 24 * 30))),
            admin_username=os.environ.get("ADMIN_USERNAME", ""),
            admin_password_hash=os.environ.get("ADMIN_PASSWORD_HASH", ""),
            cookie_secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
            resend_api_key=os.environ.get("RESEND_API_KEY", ""),
            email_from=os.environ.get("EMAIL_FROM", ""),
            email_reply_to=os.environ.get("EMAIL_REPLY_TO", "").strip(),
            otp_expire_minutes=int(os.environ.get("OTP_EXPIRE_MINUTES", "5")),
            otp_max_attempts=int(os.environ.get("OTP_MAX_ATTEMPTS", "5")),
            hot_news_email_enabled=os.environ.get("HOT_NEWS_EMAIL_ENABLED", "true").lower() == "true",
            hot_news_email_max_age_hours=int(os.environ.get("HOT_NEWS_EMAIL_MAX_AGE_HOURS", "24")),
            hot_news_email_batch_size=int(os.environ.get("HOT_NEWS_EMAIL_BATCH_SIZE", "100")),
            app_base_url=os.environ.get("APP_BASE_URL", "").rstrip("/"),
        )
