"""
Phân loại bài viết vào 1 trong 3 category bằng LLM (structured output).

Backend hiện tại: Cohere (command-r-plus) — có thể chuyển sang Anthropic
bằng cách đặt CLASSIFIER_BACKEND=anthropic trong .env.

Category (theo yêu cầu JD):
  1. energy_fossil_fuels — Năng lượng & nhiên liệu hóa thạch
  2. carbon_credits       — Hạn ngạch & Tín chỉ carbon
  3. policy               — Chính sách
"""
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Literal, Optional

import cohere

from schemas.crawl_models import ClassificationResult, NewsCategory

logger = logging.getLogger(__name__)

# Chỉ cần vài nghìn ký tự đầu là đủ context để phân loại — giảm token/chi phí
MAX_TEXT_CHARS_FOR_CLASSIFICATION = 4000

_SYSTEM_PROMPT = """\
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
   carbon, không thuộc hẳn 2 nhóm trên.

Nếu bài viết có thể thuộc nhiều category, chọn category phản ánh chủ đề
CHÍNH của bài viết."""

_JSON_INSTRUCTION = """\

Trả lời CHỈ bằng JSON hợp lệ theo format sau, không có text ngoài JSON:
{"category": "<energy_fossil_fuels|carbon_credits|policy>", "confidence": <0.0-1.0>}"""

VALID_CATEGORIES = {"energy_fossil_fuels", "carbon_credits", "policy"}


class ClassificationError(Exception):
    """Lỗi khi gọi LLM để phân loại — pipeline sẽ bỏ qua bài viết, thử lại lần crawl sau."""


class Classifier(ABC):
    @abstractmethod
    async def classify(self, title: Optional[str], text: str) -> ClassificationResult:
        ...


class CohereClassifier(Classifier):
    """
    Dùng Cohere command-r-plus để phân loại.
    Dùng preamble (system prompt) + message, parse JSON từ response text.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "command-r-plus-08-2024",
        concurrency: int = 5,
    ):
        if not api_key:
            raise ValueError(
                "COHERE_API_KEY chưa được set. Điền vào file .env trước khi chạy."
            )
        self._client = cohere.AsyncClientV2(api_key=api_key)
        self._model = model
        self._semaphore = asyncio.Semaphore(concurrency)

    async def classify(self, title: Optional[str], text: str) -> ClassificationResult:
        user_content = (
            f"Tiêu đề: {title or '(không có tiêu đề)'}\n\n"
            f"Nội dung:\n{text[:MAX_TEXT_CHARS_FOR_CLASSIFICATION]}"
        )
        try:
            async with self._semaphore:
                response = await self._client.chat(
                    model=self._model,
                    messages=[
                        {
                            "role": "system",
                            "content": _SYSTEM_PROMPT + _JSON_INSTRUCTION,
                        },
                        {"role": "user", "content": user_content},
                    ],
                )
        except cohere.CohereAPIError as e:
            raise ClassificationError(f"Lỗi gọi Cohere API: {e}") from e

        raw_text = response.message.content[0].text.strip()
        return _parse_json_response(raw_text)


class AnthropicClassifier(Classifier):
    """Dùng Claude (mặc định Haiku — rẻ/nhanh, đủ cho tác vụ phân loại 3 lớp)."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", concurrency: int = 5):
        import anthropic  # import trễ để không yêu cầu anthropic khi dùng Cohere
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._semaphore = asyncio.Semaphore(concurrency)

    async def classify(self, title: Optional[str], text: str) -> ClassificationResult:
        import anthropic
        user_content = (
            f"Tiêu đề: {title or '(không có tiêu đề)'}\n\n"
            f"Nội dung:\n{text[:MAX_TEXT_CHARS_FOR_CLASSIFICATION]}"
        )
        try:
            async with self._semaphore:
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=256,
                    system=_SYSTEM_PROMPT + _JSON_INSTRUCTION,
                    messages=[{"role": "user", "content": user_content}],
                )
        except anthropic.APIError as e:
            raise ClassificationError(f"Lỗi gọi Anthropic API: {e}") from e

        raw_text = response.content[0].text.strip()
        return _parse_json_response(raw_text)


def _parse_json_response(raw_text: str) -> ClassificationResult:
    """Parse chuỗi JSON trả về từ LLM thành ClassificationResult."""
    # Một số model bọc JSON trong code fence ```json ... ```
    text = raw_text
    if "```" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        text = text[start:end] if start != -1 else text

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClassificationError(
            f"LLM không trả về JSON hợp lệ. raw='{raw_text[:200]}' err={e}"
        ) from e

    category_str = data.get("category", "")
    if category_str not in VALID_CATEGORIES:
        raise ClassificationError(
            f"Category không hợp lệ: '{category_str}'. Mong đợi: {VALID_CATEGORIES}"
        )

    confidence = float(data.get("confidence", 0.8))
    confidence = max(0.0, min(1.0, confidence))  # clamp về [0, 1]

    return ClassificationResult(
        category=NewsCategory(category_str),
        confidence=confidence,
    )


def build_classifier(
    backend: str,
    cohere_api_key: str = "",
    anthropic_api_key: str = "",
    model: str = "",
    concurrency: int = 5,
) -> Classifier:
    """
    Factory function — tạo Classifier phù hợp theo backend.
    Dùng trong main.py để khởi tạo classifier mà không cần if/else rải rác.
    """
    if backend == "cohere":
        effective_model = model or "command-r-plus-08-2024"
        return CohereClassifier(
            api_key=cohere_api_key,
            model=effective_model,
            concurrency=concurrency,
        )
    if backend == "anthropic":
        effective_model = model or "claude-haiku-4-5"
        return AnthropicClassifier(
            api_key=anthropic_api_key,
            model=effective_model,
            concurrency=concurrency,
        )
    raise ValueError(
        f"CLASSIFIER_BACKEND không hợp lệ: '{backend}'. Dùng 'cohere' hoặc 'anthropic'."
    )
