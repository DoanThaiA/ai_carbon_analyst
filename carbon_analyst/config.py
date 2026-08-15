"""
Cấu hình đọc từ biến môi trường (.env khi chạy local).
"""
from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv
import os

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str
    anthropic_api_key: str
    classifier_model: str
    classify_concurrency: int

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL chưa được set. Copy .env.example -> .env và điền connection string."
            )
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return cls(
            database_url=database_url,
            anthropic_api_key=anthropic_api_key,
            classifier_model=os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5"),
            classify_concurrency=int(os.environ.get("CLASSIFY_CONCURRENCY", "5")),
        )
