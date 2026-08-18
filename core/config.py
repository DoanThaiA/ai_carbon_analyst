
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
    classifier_model: str
    classify_concurrency: int
    embedding_model: str
    vector_dimension: int

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

        # Chọn model mặc định hợp lý theo backend
        default_model = (
            "command-a-03-2025" if backend == "cohere" else "claude-haiku-4-5"
        )

        return cls(
            database_url=database_url,
            classifier_backend=backend,
            cohere_api_key=cohere_api_key,
            anthropic_api_key=anthropic_api_key,
            classifier_model=os.environ.get("CLASSIFIER_MODEL", default_model),
            classify_concurrency=int(os.environ.get("CLASSIFY_CONCURRENCY", "5")),
            embedding_model=os.environ.get("EMBEDDING_MODEL", "embed-v4.0"),
            vector_dimension=int(os.environ.get("VECTOR_DIMENSION", "1536")),
        )
