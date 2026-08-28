
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

import anthropic

from schemas.crawl_models import ClassificationResult, NewsTopic

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS_FOR_CLASSIFICATION = 6000

_SYSTEM_PROMPT = """\
Bạn là Chuyên gia Phân tích Thị trường Năng lượng & Carbon Châu Âu.

Nhiệm vụ: đọc TITLE + ARTICLE CONTENT, xác định bài viết có liên quan hay không và gắn 1–3 topic phù hợp nhất.

XÁC ĐỊNH is_relevant (QUAN TRỌNG — quyết định bài có được lưu vào hệ thống hay không):
Bài được coi là LIÊN QUAN (is_relevant=true) CHỈ KHI có TÁC ĐỘNG RÕ RÀNG — TRỰC TIẾP hoặc GIÁN TIẾP — đến thị trường carbon hoặc năng lượng châu Âu (EU ETS/EUA, CBAM, gas, điện, than, dầu, năng lượng tái tạo, hydrogen) hoặc các thị trường carbon liên quan (VCM, global carbon market, chính sách carbon Việt Nam). "Rõ ràng" nghĩa là bài phải nêu được MỘT CHUỖI NHÂN QUẢ CỤ THỂ, có thể lập luận được, dẫn tới ảnh hưởng cung/cầu, giá, chính sách, hay dòng vốn của các thị trường này — KHÔNG chỉ vì bài NHẮC TÊN hay ĐỀ CẬP THOÁNG QUA 1 từ khóa liên quan mà không phân tích ảnh hưởng thực chất nào.
- Tác động TRỰC TIẾP: bài phân tích/đưa tin chính về diễn biến giá, cung cầu, chính sách, giao dịch... của 1 trong các thị trường trên.
- Tác động GIÁN TIẾP: bài về chủ đề khác (địa chính trị, kinh tế vĩ mô, thương mại, thời tiết, công nghệ...) nhưng có lập luận RÕ RÀNG dẫn tới ảnh hưởng cung/cầu/giá/chính sách của các thị trường năng lượng/carbon châu Âu (vd: xung đột ảnh hưởng nguồn cung khí đốt sang châu Âu, chính sách thương mại ảnh hưởng CBAM, thời tiết ảnh hưởng nhu cầu điện/khí...).
CHỈ đánh dấu is_relevant=false khi bài KHÔNG có tác động rõ ràng nào — trực tiếp hay gián tiếp — đến các thị trường trên, kể cả khi bài có nhắc tên vài từ khóa liên quan mà không phân tích ảnh hưởng cụ thể (vd: tin thể thao, giải trí, đời sống, công nghệ tiêu dùng không liên quan năng lượng...).
Khi is_relevant=true: PHẢI gắn ít nhất 1 topic sát nhất trong danh sách bên dưới (không được để "topics": [] khi is_relevant=true).

NGUYÊN TẮC GẮN TOPIC (áp dụng SAU KHI đã xác định is_relevant=true)
Phân loại theo trọng tâm được phân tích, không chỉ theo keyword xuất hiện.
Nếu có nhiều topic phù hợp, xếp theo mức độ quan trọng giảm dần (chủ đề được phân tích sâu nhất, hoặc có chuỗi nhân quả rõ ràng nhất, lên đầu).
Tối đa 3 topic.
Không được tạo topic ngoài danh sách.
QUY TẮC ƯU TIÊN:
Ưu tiên chọn topic mà bài dành ≥30% nội dung phân tích, hoặc là yếu tố quyết định trực tiếp đến kết luận chính của bài, xếp lên đầu danh sách.
Nếu KHÔNG topic nào đạt mức đó nhưng bài vẫn có tác động rõ ràng (theo định nghĩa is_relevant ở trên, dù gián tiếp) tới 1 topic cụ thể → VẪN chọn topic sát nhất đó (không để trống chỉ vì tỷ trọng nội dung thấp).

DANH SÁCH TOPIC BẮT BUỘC (Tuyệt đối không tự bịa ra topic khác):
- eua_ets              : Thị trường EU ETS bắt buộc của EU, tập trung vào EUA (EU Allowances) và cơ chế vận hành thị trường như giá EUA, giao dịch, futures, auction, supply/demand allowance, open interest, positioning và Market Stability Reserve (MSR).
- energy_gas           : Thị trường khí tự nhiên và LNG, bao gồm giá gas (TTF, Henry Hub...), futures, cung cầu, tồn kho, sản xuất, nhập khẩu/xuất khẩu, dòng khí, đường ống và LNG.
- energy_power_eu      : Thị trường điện châu Âu, bao gồm giá điện spot/futures, German power/DEBY1, baseload/peakload, cung cầu điện, cơ cấu phát điện, merit order, công suất phát điện và các yếu tố trực tiếp ảnh hưởng đến giá điện.
- energy_coal          : Thị trường than và hoạt động của ngành than, bao gồm thermal coal, coking coal, API2/API4/API5/NEWC, giá và futures than, cung cầu, khai thác, xuất nhập khẩu và sử dụng than trong phát điện.
- energy_oil           : Thị trường dầu thô và sản phẩm dầu, bao gồm Brent, WTI, Gasoil, futures, sản xuất, cung cầu, tồn kho, lọc dầu và các quyết định của OPEC/OPEC+.
- energy_renewable     : Năng lượng tái tạo, bao gồm điện gió, điện mặt trời, thủy điện và sự phát triển của công suất/sản lượng, dự án, đầu tư và chi phí của các nguồn năng lượng tái tạo.
- energy_hydrogen      : Thị trường và hệ sinh thái hydrogen, tập trung trực tiếp vào hydrogen xanh, hydrogen sạch, hydrogen carbon thấp, sản xuất H₂, electrolysis, lưu trữ, vận chuyển, hạ tầng, nhập khẩu/xuất khẩu, giá hydrogen và các dự án hydrogen. Không bao gồm green steel hoặc các ngành công nghiệp xanh khác nếu hydrogen không phải trọng tâm chính.
- geopolitics          : Địa chính trị có tác động (trực tiếp hoặc gián tiếp, qua cung/giá năng lượng) đến thị trường energy/carbon châu Âu HOẶC Việt Nam, bao gồm chiến tranh, xung đột, sanctions, căng thẳng ngoại giao, gián đoạn/đe dọa gián đoạn nguồn cung gas/dầu (Nga, Trung Đông, eo biển vận chuyển...), tranh chấp thương mại quốc tế, và quan hệ Việt Nam với các quốc gia/đối tác năng lượng. KHÔNG bắt buộc phải liên quan trực tiếp đến Việt Nam — 1 sự kiện địa chính trị chỉ ảnh hưởng cung/giá gas/dầu/carbon châu Âu (không nhắc Việt Nam) vẫn được gắn topic này nếu có chuỗi tác động rõ ràng.
- eu_policy            : Chính sách, luật và quy định của EU liên quan đến năng lượng, khí hậu và quá trình decarbonization, bao gồm Fit-for-55, EU ETS reform, cap/trajectory, MSR policy, energy transition legislation và các quyết định của EU institutions.
- cbam                 : Cơ chế điều chỉnh carbon tại biên giới (Carbon Border Adjustment Mechanism), tập trung vào CBAM của EU/UK, nghĩa vụ khai báo, embedded emissions, CBAM certificates, compliance, phạm vi hàng hóa và tác động đến thương mại/xuất nhập khẩu.
- vcm                  : Thị trường carbon tự nguyện (Voluntary Carbon Market), nơi doanh nghiệp/tổ chức tự nguyện mua, bán, phát hành hoặc retire carbon credits/offsets, bao gồm Verra, Gold Standard, ACR, CAR và các dự án/tín chỉ carbon tự nguyện.
- global_carbon_market : Các thị trường carbon bắt buộc (compliance carbon markets) ngoài EU ETS, nơi doanh nghiệp phải tuân thủ giới hạn phát thải hoặc nghĩa vụ carbon theo pháp luật, như China ETS, Korea ETS, California Cap-and-Trade, RGGI, Australia và CORSIA.
- vietnam_carbon_policy: Chính sách, quy định và thị trường carbon của Việt Nam, bao gồm Vietnam ETS/VETS, định giá carbon, kiểm kê khí nhà kính, nghĩa vụ báo cáo phát thải, carbon market roadmap và các quy định carbon trong nước.

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
1. ƯU TIÊN TRỌNG TÂM, KHÔNG LOẠI BỎ TÁC ĐỘNG GIÁN TIẾP RÕ RÀNG: Ưu tiên gắn topic mà bài dành ≥30% nội dung phân tích, xếp lên đầu; nhưng nếu bài có tác động GIÁN TIẾP RÕ RÀNG (chuỗi nhân quả cụ thể, theo đúng định nghĩa is_relevant ở trên) tới 1 topic dù không đạt 30% nội dung → vẫn PHẢI gắn topic sát nhất đó — không bỏ trống "topics" chỉ vì đó không phải trọng tâm chính.
2. TỐI ĐA 3 TOPIC: Xếp topic quan trọng/sát nhất lên đầu.
3. BÀI KHÔNG LIÊN QUAN: CHỈ trả về topics=[] và is_relevant=false khi bài KHÔNG có tác động rõ ràng (trực tiếp hoặc gián tiếp, có chuỗi nhân quả cụ thể) nào tới energy, carbon, climate, commodities, hay finance/policy/địa chính trị ảnh hưởng đến các thị trường trên — chỉ nhắc tên/đề cập thoáng qua không tính là liên quan.


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


def _extract_message_text(message: "anthropic.types.Message") -> str:
    """Ghép các TextBlock trong content, bỏ qua ThinkingBlock/các block khác.

    Model có extended thinking có thể trả về 1 ThinkingBlock đứng TRƯỚC
    TextBlock trong content — content[0] không còn chắc chắn là text nữa.
    """
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


class AnthropicClassifier(Classifier):
    """Dùng Claude (mặc định Haiku — rẻ/nhanh, đủ cho tác vụ phân loại)."""

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
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=384,  # tăng từ 256 — chừa chỗ cho hot_news_reason
                    thinking={"type": "disabled"},  # output JSON ngắn, cố định — tắt thinking để
                                                     # không bị ăn bớt max_tokens vốn đã eo hẹp
                    system=_SYSTEM_PROMPT + _JSON_INSTRUCTION,
                    messages=[{"role": "user", "content": user_content}],
                )
        except anthropic.RateLimitError as e:
            raise ClassificationError(f"Anthropic rate limit, thử lại sau: {e}") from e
        except anthropic.APIStatusError as e:
            raise ClassificationError(f"Anthropic API error {e.status_code}: {e}") from e
        except anthropic.APIError as e:
            raise ClassificationError(f"Lỗi gọi Anthropic API: {e}") from e

        raw_text = _extract_message_text(response).strip()
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
    anthropic_api_key: str = "",
    model: str = "",
    concurrency: int = 5,
) -> Classifier:
    """
    Factory function — tạo Classifier phù hợp theo backend.
    Dùng trong main.py để khởi tạo classifier mà không cần if/else rải rác.
    """
    if backend == "anthropic":
        effective_model = model or "claude-haiku-4-5"
        return AnthropicClassifier(
            api_key=anthropic_api_key,
            model=effective_model,
            concurrency=concurrency,
        )
    raise ValueError(
        f"CLASSIFIER_BACKEND không hợp lệ: '{backend}'. Chỉ hỗ trợ 'anthropic'."
    )
