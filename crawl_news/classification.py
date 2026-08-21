
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

import cohere

from schemas.crawl_models import ClassificationResult, NewsTopic

logger = logging.getLogger(__name__)

# Tăng lên 6000 để bắt được phần phân tích chính của bài dài (Reuters/FT ~800-1500 từ).
# Vẫn rẻ hơn nhiều so với full-text — classify chỉ cần context, không cần toàn văn.
MAX_TEXT_CHARS_FOR_CLASSIFICATION = 6000

_SYSTEM_PROMPT = """\
Bạn là Chuyên gia Phân tích Thị trường Năng lượng & Carbon Châu Âu cao cấp.
Nhiệm vụ của bạn là đọc Tiêu đề và Nội dung bài viết, sau đó gắn 1 đến 3 topic (chủ đề) phù hợp nhất, mô tả chính xác TRỌNG TÂM của bài viết.

DANH SÁCH TOPIC BẮT BUỘC (Tuyệt đối không tự bịa ra topic khác):
- eua_ets              : Biến động giá EUA, hoạt động giao dịch EU ETS, đấu giá (auction), open interest, Market Stability Reserve (MSR). Áp dụng khi EUA/EU ETS là CHỦ ĐỀ CHÍNH, không chỉ được nhắc lướt qua.
- energy_gas           : Thị trường khí đốt tự nhiên, giá TTF, Henry Hub, LNG, cung cầu khí đốt toàn cầu.
- energy_power_eu      : Thị trường điện châu Âu, giá điện Đức (DEBY1, baseload), merit order, năng lực phát điện.
- energy_coal          : Thị trường than nhiệt (API2, API4, NEWC), than cốc, hoạt động khai thác than.
- energy_oil           : Dầu thô và sản phẩm lọc dầu (WTI, Brent, Gasoil), quyết định của OPEC+, tồn kho dầu.
- energy_renewable     : Năng lượng tái tạo (gió, mặt trời), thủy điện, tăng trưởng công suất xanh. KHÔNG dùng cho hydrogen — xem energy_hydrogen.
- energy_hydrogen      : Hydrogen xanh/sạch (H2), thép xanh, sản xuất kim loại/công nghiệp xanh dùng hydrogen. Bài về hydrogen + RE → chọn energy_hydrogen (không phải energy_renewable).
- geopolitics          : Địa chính trị ảnh hưởng đến năng lượng/carbon (chiến sự Nga-Ukraine, Trung Đông, bầu cử Mỹ, căng thẳng thương mại). KHÔNG dùng cho chính sách EU thuần túy — xem eu_policy.
- eu_policy            : Chính sách vĩ mô của EU, Fit-for-55, quyết định từ EU Commission/ESMA, luật chuyển dịch năng lượng, ETS reform, MSR. KHÔNG bao gồm CBAM (xem cbam). KHÔNG dùng cho địa chính trị (xem geopolitics).
- cbam                 : Thuế biên giới carbon (EU CBAM, UK CBAM), tác động đến xuất nhập khẩu, quy trình kê khai, lộ trình CBAM. Khi có CBAM → LUÔN chọn cbam thay vì eu_policy.
- vcm                  : Thị trường carbon TỰ NGUYỆN (Voluntary Carbon Market): Verra, Gold Standard, ACR, CAR, tín chỉ tự nguyện, Article 6 Paris Agreement. KHÔNG dùng cho thị trường bắt buộc ngoài EU (xem global_carbon_market).
- global_carbon_market : Các thị trường carbon TUÂN THỦ (bắt buộc) NGOÀI EU: Korea ETS, China ETS, California Cap-and-Trade, RGGI, Australia ERF, CORSIA hàng không. KHÔNG dùng nếu là tín chỉ tự nguyện (xem vcm).
- vietnam_carbon_policy: Chính sách carbon tại Việt Nam, quy định kiểm kê khí nhà kính, định giá carbon nội địa (VETS), lộ trình thị trường carbon VN.

PHÂN BIỆT BẮT BUỘC — CÁC TRƯỜNG HỢP DỄ NHẦM:
1. eua_ets vs eu_policy:
   - Bài cập nhật giá/giao dịch/auction EUA → PHẢI chọn "eua_ets".
   - Bài về thay đổi luật EU ETS, cap trajectory, MSR thay đổi → chọn CẢ "eu_policy" VÀ "eua_ets".
2. cbam vs eu_policy:
   - Nội dung chuyên sâu về thuế biên giới carbon → PHẢI chọn "cbam", KHÔNG chọn "eu_policy".
3. vcm vs global_carbon_market:
   - Thị trường TỰ NGUYỆN (Verra, Gold Standard, Article 6) → "vcm".
   - Thị trường BẮT BUỘC ngoài EU (China ETS, California, CORSIA) → "global_carbon_market".
   - Một bài có thể có CẢ HAI nếu đề cập cả 2 loại.
4. energy_renewable vs energy_hydrogen:
   - Bài về điện gió/mặt trời/thủy điện → "energy_renewable".
   - Bài về hydrogen xanh, thép xanh, công nghiệp dùng H2 → "energy_hydrogen".
   - Bài về RE để sản xuất hydrogen → "energy_hydrogen" là ưu tiên.
5. geopolitics vs eu_policy:
   - Bầu cử EU, ngoại giao EU → "geopolitics".
   - Luật/quy định EU ban hành → "eu_policy".

QUY TẮC PHÂN LOẠI NGHIÊM NGẶT:
1. CHỈ CHỌN TRỌNG TÂM: Gắn topic khi bài dành ≥30% nội dung phân tích về chủ đề đó. KHÔNG gắn nếu từ khóa chỉ nhắc lướt qua để so sánh.
2. TỐI ĐA 3 TOPIC: Xếp topic quan trọng/sát nhất lên đầu.
3. BÀI KHÔNG LIÊN QUAN: Nếu bài không liên quan đến energy, carbon, climate, commodities, hay finance/policy ảnh hưởng đến các thị trường trên → trả về topics=[] và is_relevant=false.

HOT NEWS — đánh giá ĐỘC LẬP với việc gắn topic ở trên, chỉ đánh dấu is_hot_news=true khi bài khớp ĐÚNG 1 trong 4 tiêu chí sau (nghiêm ngặt, KHÔNG suy diễn rộng ra ngoài định nghĩa):
1. Đảo chiều giá EUA: bài xác nhận một sự đảo chiều xu hướng giá EUA (từ tăng sang giảm hoặc ngược lại), không phải biến động thông thường trong xu hướng đang có.
2. CBAM thay đổi quy định đột ngột: đưa ra quy định CBAM MỚI mà trước đó không hề được nhắc đến, hoặc thay đổi ĐỘT NGỘT so với lộ trình CBAM đã công bố trước đó — không phải cập nhật tiến độ thông thường theo đúng lộ trình.
3. Địa chính trị tiềm ẩn xung đột: nguy cơ chiến tranh/xung đột giữa khu vực tiêu thụ nhiều khí gas và khu vực cung cấp khí gas, giữa khu vực phát thải mạnh và khu vực đánh thuế phát thải (CBAM/carbon tax), hoặc xung đột lợi ích cấp quốc gia rõ ràng liên quan năng lượng/carbon.
4. Quốc gia lớn rút khỏi decarbonization: một nước lớn (G20, EU member lớn, Mỹ, Trung Quốc...) tuyên bố CHÍNH THỨC không tham gia/rút khỏi chương trình giảm biến đổi khí hậu toàn cầu.
Nếu không rơi vào 1 trong 4 trường hợp trên → is_hot_news=false, hot_news_reason=null. Nếu true, hot_news_reason là 1 câu ngắn (tiếng Việt) nêu rõ bài khớp tiêu chí nào.\
"""

_JSON_INSTRUCTION = """

Trả lời CHỈ bằng JSON hợp lệ, không có text ngoài JSON:
{"topics": ["<topic1>", "<topic2_optional>", "<topic3_optional>"], "confidence": <0.0-1.0>, "is_relevant": <true/false>, "is_hot_news": <true/false>, "hot_news_reason": <string hoặc null>}
Nếu bài không liên quan: {"topics": [], "confidence": 1.0, "is_relevant": false, "is_hot_news": false, "hot_news_reason": null}"""

VALID_TOPICS = {t.value for t in NewsTopic}
MAX_TOPICS_PER_ARTICLE = 3


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
        except cohere.TooManyRequestsError as e:
            raise ClassificationError(f"Cohere rate limit, thử lại sau: {e}") from e
        except cohere.ServiceUnavailableError as e:
            raise ClassificationError(f"Cohere service unavailable: {e}") from e
        except cohere.BadRequestError as e:
            raise ClassificationError(f"Cohere bad request (không retry): {e}") from e
        except Exception as e:
            raise ClassificationError(f"Lỗi gọi Cohere API: {e}") from e

        raw_text = response.message.content[0].text.strip()
        return _parse_json_response(raw_text)


class AnthropicClassifier(Classifier):
    """Dùng Claude (mặc định Haiku — rẻ/nhanh, đủ cho tác vụ phân loại)."""

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
                    max_tokens=384,  # tăng từ 256 — chừa chỗ cho hot_news_reason
                    system=_SYSTEM_PROMPT + _JSON_INSTRUCTION,
                    messages=[{"role": "user", "content": user_content}],
                )
        except anthropic.RateLimitError as e:
            raise ClassificationError(f"Anthropic rate limit, thử lại sau: {e}") from e
        except anthropic.APIStatusError as e:
            raise ClassificationError(f"Anthropic API error {e.status_code}: {e}") from e
        except anthropic.APIError as e:
            raise ClassificationError(f"Lỗi gọi Anthropic API: {e}") from e

        raw_text = response.content[0].text.strip()
        return _parse_json_response(raw_text)


def _parse_json_response(raw_text: str) -> ClassificationResult:
    """Parse chuỗi JSON trả về từ LLM thành ClassificationResult.

    Lenient parsing:
    - Strip markdown code blocks nếu có.
    - Strip/lowercase từng topic trước khi validate — tránh drop bài vì whitespace/casing.
    - Topic không hợp lệ bị bỏ qua (log warning), KHÔNG raise → bài không bị mất.
    - Chỉ raise nếu JSON không parse được hoặc topics không phải list.
    """
    text = raw_text
    # Bóc markdown code block nếu LLM bọc JSON trong ```json ... ```
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

    # is_relevant: mặc định True (backward compat với bài không có field này)
    is_relevant: bool = bool(data.get("is_relevant", True))

    raw_topics = data.get("topics", [])
    if not isinstance(raw_topics, list):
        raise ClassificationError(
            f"Trường 'topics' phải là list. raw='{raw_text[:200]}'"
        )

    # Lenient: strip + lowercase trước validate; skip topic sai thay vì raise
    valid: List[NewsTopic] = []
    for t in raw_topics[:MAX_TOPICS_PER_ARTICLE]:
        if not isinstance(t, str):
            continue
        t_clean = t.strip().lower()
        if t_clean not in VALID_TOPICS:
            logger.warning("[CLASSIFY] Topic không hợp lệ, bỏ qua: '%s'", t)
            continue
        topic_enum = NewsTopic(t_clean)
        if topic_enum not in valid:  # tránh trùng lặp
            valid.append(topic_enum)

    # Nếu is_relevant=false mà LLM vẫn gán topic, tôn trọng is_relevant flag
    if not is_relevant:
        valid = []

    confidence = float(data.get("confidence", 0.8))
    confidence = max(0.0, min(1.0, confidence))

    # Hot news: ĐỘC LẬP với is_relevant/topics — 1 bài có thể không khớp bất kỳ
    # topic thị trường nào (vd: 1 nước lớn tuyên bố rút khỏi decarbonization,
    # không nhắc trực tiếp EUA/gas/CBAM) nhưng vẫn là hot news hợp lệ theo tiêu
    # chí ở _SYSTEM_PROMPT. KHÔNG ép is_hot_news=false theo is_relevant ở đây —
    # pipeline (crawl_pipeline.py) mới là nơi quyết định có lưu bài hay không.
    is_hot_news = bool(data.get("is_hot_news", False))
    hot_news_reason = data.get("hot_news_reason") if is_hot_news else None
    if hot_news_reason is not None and not isinstance(hot_news_reason, str):
        hot_news_reason = None

    return ClassificationResult(
        topics=valid,
        confidence=confidence,
        is_relevant=is_relevant,
        is_hot_news=is_hot_news,
        hot_news_reason=hot_news_reason,
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
