
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List
import cohere

from core.config import Settings

logger = logging.getLogger(__name__)

settings = Settings.from_env()
EMBEDDING_DIM = settings.vector_dimension


class Embedder(ABC):
    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Sinh embedding cho danh sách text, đồng bộ (chạy trong thread riêng khi gọi từ async)."""
        ...

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self.embed_texts, texts)


class CohereEmbedder(Embedder):
    """Embedder sử dụng Cohere API."""

    def __init__(self, api_key: str = None, model_name: str = None):
        self.api_key = api_key or settings.cohere_api_key
        self.model_name = model_name or settings.embedding_model
        if not self.api_key:
            raise ValueError("Cohere API key chưa được cấu hình.")
        self._client = cohere.AsyncClient(api_key=self.api_key)
        logger.info("[EMBEDDING] Sử dụng Cohere model '%s'", self.model_name)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError("CohereEmbedder là async-first, hãy dùng await embed().")

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        
        response = await self._client.embed(
            texts=texts,
            model=self.model_name,
            input_type="search_document",
        )
        return response.embeddings
