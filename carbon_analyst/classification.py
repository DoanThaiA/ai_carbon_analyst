"""
Phân loại bài viết vào 1 trong 3 category bằng Claude API (structured output).

Category (theo yêu cầu JD):
  1. energy_fossil_fuels — Năng lượng & nhiên liệu hóa thạch
  2. carbon_credits       — Hạn ngạch & Tín chỉ carbon
  3. policy               — Chính sách
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from carbon_analyst.models import ClassificationResult, NewsCategory

logger = logging.getLogger(__name__)

# Chỉ cần vài nghìn ký tự đầu là đủ context để phân loại — giảm token/chi phí
# cho 1 tác vụ đơn giản (3-way classification), không cần gửi cả bài dài.
MAX_TEXT_CHARS_FOR_CLASSIFICATION = 4000

SYSTEM_PROMPT = """\
Bạn là một chuyên gia phân loại tin tức cho một bàn phân tích năng lượng &
carbon. Với mỗi bài viết (tiêu đề + nội dung), hãy phân loại vào ĐÚNG 1 trong
3 category sau:

1. energy_fossil_fuels — Năng lượng & nhiên liệu hóa thạch: tin về dầu thô,
   khí đốt, than đá, LNG, giá năng lượng, sản lượng khai thác, OPEC, EIA,
   IEA, thị trường điện truyền thống.
2. carbon_credits — Hạn ngạch & Tín chỉ carbon: tin về EU ETS, EUA, tín chỉ
   carbon, carbon offset, cap-and-trade, carbon trading, compliance market,
   voluntary carbon market.
3. policy — Chính sách: tin về luật, quy định, chính sách của chính phủ/cơ
   quan quản lý (EU Commission, ESMA...) liên quan đến năng lượng hoặc
   carbon, không thuộc hẳn 2 nhóm trên (vd: quy định thuế carbon, chính sách
   trợ giá năng lượng, cam kết khí hậu quốc gia).

Nếu bài viết có thể thuộc nhiều category, chọn category phản ánh chủ đề
CHÍNH của bài viết. Trả về confidence (0.0-1.0) thể hiện mức độ chắc chắn."""


class CategoryClassification(BaseModel):
    category: Literal["energy_fossil_fuels", "carbon_credits", "policy"]
    confidence: float = Field(ge=0.0, le=1.0)


class ClassificationError(Exception):
    """Lỗi khi gọi LLM để phân loại — pipeline sẽ bỏ qua bài viết, thử lại lần crawl sau."""


class Classifier(ABC):
    @abstractmethod
    async def classify(self, title: Optional[str], text: str) -> ClassificationResult:
        ...


class AnthropicClassifier(Classifier):
    """Dùng Claude (mặc định Haiku — rẻ/nhanh, đủ cho tác vụ phân loại 3 lớp)."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", concurrency: int = 5):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._semaphore = asyncio.Semaphore(concurrency)

    async def classify(self, title: Optional[str], text: str) -> ClassificationResult:
        user_content = (
            f"Tiêu đề: {title or '(không có tiêu đề)'}\n\n"
            f"Nội dung:\n{text[:MAX_TEXT_CHARS_FOR_CLASSIFICATION]}"
        )
        try:
            async with self._semaphore:
                response = await self._client.messages.parse(
                    model=self._model,
                    max_tokens=256,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_content}],
                    output_format=CategoryClassification,
                )
        except anthropic.APIError as e:
            raise ClassificationError(f"Lỗi gọi Anthropic API: {e}") from e

        parsed = response.parsed_output
        if parsed is None:
            raise ClassificationError(
                f"Claude không trả về structured output hợp lệ (stop_reason={response.stop_reason})"
            )
        return ClassificationResult(
            category=NewsCategory(parsed.category),
            confidence=parsed.confidence,
        )
