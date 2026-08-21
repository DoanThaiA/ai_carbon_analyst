"""
Embedding service — sinh vector cho text chunks dùng Cohere API.

Thiết kế:
- Embedder là abstract class → dễ swap provider (Cohere, OpenAI, local...)
- CohereEmbedder là async-first: gọi trực tiếp Cohere async client
- Retry với exponential backoff cho 429 / server error để tránh mất chunk
  khi rate limit trong lúc crawl nhiều nguồn song song
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

import cohere

logger = logging.getLogger(__name__)

# Retry config cho Cohere Embed API
_EMBED_MAX_RETRIES = 3
_EMBED_RETRY_BASE_DELAY = 2.0  # giây, tăng gấp đôi mỗi lần retry


class Embedder(ABC):
    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Sinh embedding cho danh sách text, đồng bộ (chạy trong thread riêng khi gọi từ async)."""
        ...

    async def embed(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self.embed_texts, texts)


class CohereEmbedder(Embedder):
    """Embedder sử dụng Cohere API với retry exponential backoff.

    BUG-5 fix: Settings được khởi tạo trong __init__, không phải module-level,
    để tránh import error khi test mà không có .env.
    OPT-5 fix: Retry tối đa 3 lần với delay 2s/4s/8s khi gặp 429 / server error.
    """

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        # Import config lazily — không trigger khi import module
        from core.config import Settings
        _settings = Settings.from_env()
        self.api_key = api_key or _settings.cohere_api_key
        self.model_name = model_name or _settings.embedding_model
        if not self.api_key:
            raise ValueError("Cohere API key chưa được cấu hình.")
        self._client = cohere.AsyncClient(api_key=self.api_key)
        logger.info("[EMBEDDING] Sử dụng Cohere model '%s'", self.model_name)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError("CohereEmbedder là async-first, hãy dùng await embed().")

    async def embed(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        """Gọi Cohere Embed API với retry exponential backoff.

        `input_type`: "search_document" khi embed nội dung để lưu/index (mặc định,
        giữ nguyên hành vi cũ cho pipeline chunk+embed), "search_query" khi embed
        câu hỏi/query để đi tìm kiếm (bất đối xứng — Cohere tối ưu riêng 2 chiều này).

        Retry khi gặp:
        - cohere.TooManyRequestsError (429)
        - cohere.ServiceUnavailableError / InternalServerError (5xx)
        Không retry khi gặp lỗi xác thực (401) hoặc input không hợp lệ (400).
        """
        if not texts:
            return []

        last_exc: Optional[Exception] = None
        for attempt in range(1, _EMBED_MAX_RETRIES + 2):
            try:
                response = await self._client.embed(
                    texts=texts,
                    model=self.model_name,
                    input_type=input_type,
                )
                return response.embeddings

            except (cohere.TooManyRequestsError, cohere.ServiceUnavailableError) as e:
                last_exc = e
                if attempt <= _EMBED_MAX_RETRIES:
                    delay = _EMBED_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "[EMBED-RETRY %d/%d] %s — chờ %.0fs",
                        attempt, _EMBED_MAX_RETRIES, type(e).__name__, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    break

            except Exception as e:
                # Lỗi không retry được (401, 400, network...) — raise ngay
                raise

        raise RuntimeError(
            f"Cohere embed thất bại sau {_EMBED_MAX_RETRIES} lần retry: {last_exc}"
        ) from last_exc
