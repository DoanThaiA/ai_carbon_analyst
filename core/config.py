
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

    # Quote Chat (hỏi đáp đoạn bôi đen) — tạm dùng Cohere (đã có key/config sẵn),
    # đổi sang "anthropic" khi lên production bằng ENV, không cần sửa code.
    quote_chat_backend: str
    quote_chat_model: str

    # Auth — để trống nếu không dùng API/admin panel (vd script crawl/report
    # generator độc lập không cần các giá trị này). core/security.py và các
    # router auth tự raise lỗi rõ ràng nếu thiếu khi thực sự cần dùng.
    jwt_secret: str
    jwt_algorithm: str
    jwt_expire_minutes: int
    admin_username: str
    admin_password_hash: str
    cookie_secure: bool

    # OTP email (Gmail SMTP + App Password)
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    otp_expire_minutes: int
    otp_max_attempts: int

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL chưa được set. Copy .env.example -> .env và điền connection string."
            )

        backend = os.environ.get("CLASSIFIER_BACKEND", "cohere").lower()

        cohere_api_key = os.environ.get("COHERE_API_KEY", "")
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        eia_api_key = os.environ.get("EIA_API_KEY", "")

        # Chọn model mặc định hợp lý theo backend
        default_model = (
            "command-a-03-2025" if backend == "cohere" else "claude-haiku-4-5"
        )

        quote_chat_backend = os.environ.get("QUOTE_CHAT_BACKEND", "cohere").lower()
        default_quote_chat_model = (
            "command-a-03-2025" if quote_chat_backend == "cohere" else "claude-haiku-4-5"
        )

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
            quote_chat_backend=quote_chat_backend,
            quote_chat_model=os.environ.get("QUOTE_CHAT_MODEL", default_quote_chat_model),
            jwt_secret=os.environ.get("JWT_SECRET", ""),
            jwt_algorithm=os.environ.get("JWT_ALGORITHM", "HS256"),
            jwt_expire_minutes=int(os.environ.get("JWT_EXPIRE_MINUTES", str(60 * 24 * 7))),
            admin_username=os.environ.get("ADMIN_USERNAME", ""),
            admin_password_hash=os.environ.get("ADMIN_PASSWORD_HASH", ""),
            cookie_secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_from=os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")),
            otp_expire_minutes=int(os.environ.get("OTP_EXPIRE_MINUTES", "5")),
            otp_max_attempts=int(os.environ.get("OTP_MAX_ATTEMPTS", "5")),
        )
