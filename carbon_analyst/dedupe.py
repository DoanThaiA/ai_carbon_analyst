"""
Fingerprinting để dedupe bài viết trước khi lưu DB.

MVP dùng content hash (SHA-256 trên text đã normalize) - nhanh, miễn phí,
bắt được trùng chính xác/gần chính xác (khác whitespace, khác case). Để dạng
interface (Fingerprinter) giống pattern PriceProvider/ManualOrVendorProvider
trong market_data.py, để sau này cắm embedding-based dedupe (bắt cả bài
paraphrase) mà không cần đổi code gọi.
"""
import hashlib
import re
from abc import ABC, abstractmethod


class Fingerprinter(ABC):
    @abstractmethod
    def fingerprint(self, text: str) -> str:
        """Trả về 1 string định danh nội dung, dùng làm content_hash trong DB."""
        ...


class Sha256Fingerprinter(Fingerprinter):
    """Normalize text (lowercase, gộp khoảng trắng) rồi hash SHA-256."""

    def fingerprint(self, text: str) -> str:
        normalized = self._normalize(text)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize(text: str) -> str:
        lowered = text.lower()
        return re.sub(r"\s+", " ", lowered).strip()


class EmbeddingFingerprinter(Fingerprinter):
    """
    TODO: cắm embedding-based dedupe khi cần bắt bài paraphrase/rewrite giữa
    các nguồn (vd cùng 1 tin Reuters được nhiều báo đăng lại với văn phong
    khác nhau). Cần chọn embedding model (OpenAI/Voyage/local) + ngưỡng
    similarity + nơi lưu vector (pgvector/Faiss) trước khi implement.
    """

    def fingerprint(self, text: str) -> str:
        raise NotImplementedError(
            "Embedding-based dedupe chưa được cấu hình — dùng Sha256Fingerprinter "
            "cho tới khi có yêu cầu bắt trùng lặp dạng paraphrase."
        )
