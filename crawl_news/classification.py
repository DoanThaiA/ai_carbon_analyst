
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

import anthropic

from schemas.crawl_models import ClassificationResult, NewsTopic

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS_FOR_CLASSIFICATION = 6000

# Haiku 5.5: rẻ hơn Haiku 4.5 ~10 lần ($0,10/$0,50 vs $1/$5 mỗi 1M token, prompt ≤100K token).
DEFAULT_CLASSIFIER_MODEL = "claude-haiku-5-5"
# Haiku 5.5 có bộ lọc an toàn (stop_reason="refusal") KHÔNG có fallback phía server — tin địa chính
# trị/xung đột đôi khi bị từ chối → gọi lại 1 lần bằng model này thay vì bỏ bài.
REFUSAL_FALLBACK_MODEL = "claude-haiku-4-5"

# 4 nhóm tin nóng (tin được gửi email ngay tới mọi khách hàng) — mã do LLM trả → nhãn hiển thị.
HOT_NEWS_CATEGORIES = {
    "political_shock": "Chính trị gây sốc thị trường",
    "supply_cut": "Cắt nguồn cung",
    "price_reversal": "Đảo chiều giá",
    "unprecedented_policy": "Chính sách chưa từng có",
}

_SYSTEM_PROMPT = """\
Bạn là Chuyên gia Phân tích Thị trường Năng lượng & Carbon Châu Âu.

Nhiệm vụ: đọc TITLE + ARTICLE CONTENT, xác định bài viết có liên quan hay không và gắn 1–3 topic phù hợp nhất.

XÁC ĐỊNH is_relevant (QUAN TRỌNG — quyết định bài có được lưu vào hệ thống hay không):
NGUYÊN TẮC GỐC: CHỈ GIỮ tin LIÊN QUAN TRỰC TIẾP. Tin có tác động còn mơ hồ/phải suy đoán/phải đi qua nhiều bước mới nối được tới thị trường → is_relevant=false (thà bỏ sót tin mờ còn hơn giữ tin không rõ tác động).
Bài được coi là LIÊN QUAN (is_relevant=true) CHỈ KHI có TÁC ĐỘNG RÕ RÀNG — TRỰC TIẾP, hoặc GIÁN TIẾP nhưng chính bài đã NÊU THẲNG tác động đó (theo chuẩn bên dưới) — đến thị trường carbon hoặc năng lượng châu Âu (EU ETS/EUA, CBAM, gas, điện, than, dầu, năng lượng tái tạo, hydrogen) hoặc các thị trường carbon liên quan (VCM, global carbon market, chính sách carbon Việt Nam). "Rõ ràng" nghĩa là bài phải nêu được MỘT CHUỖI NHÂN QUẢ CỤ THỂ, có thể lập luận được, dẫn tới ảnh hưởng cung/cầu, giá, chính sách, hay dòng vốn của các thị trường này — KHÔNG chỉ vì bài NHẮC TÊN hay ĐỀ CẬP THOÁNG QUA 1 từ khóa liên quan mà không phân tích ảnh hưởng thực chất nào.
- Tác động TRỰC TIẾP: bài phân tích/đưa tin chính về diễn biến giá, cung cầu, chính sách, giao dịch... của 1 trong các thị trường trên.
- Tác động GIÁN TIẾP (chuẩn RẤT SIẾT CHẶT — đây là nơi hay bị lọt bài mờ nhạt): bài về chủ đề khác (địa chính trị, kinh tế vĩ mô, thương mại, thời tiết, công nghệ...) CHỈ được tính khi ĐỦ CẢ 3 điều kiện sau, VÀ cả 3 đều do CHÍNH BÀI VIẾT nêu ra (không phải do bạn tự suy luận thêm); nếu phải tự suy đoán để nối được bài với thị trường → is_relevant=false:
  (1) NÊU ĐÍCH DANH ít nhất 1 thị trường/công cụ cụ thể trong danh sách trên (vd "giá TTF", "sản lượng gió Đức", "EU ETS", "CBAM", "xuất khẩu than Nga sang châu Âu"...) — KHÔNG chỉ nói "năng lượng" hay "biến đổi khí hậu" chung chung không gắn với 1 thị trường cụ thể nào.
  (2) NÊU ĐƯỢC CƠ CHẾ TRUYỀN DẪN cụ thể, có tên gọi/lập luận rõ (vd fuel switching, gián đoạn nguồn cung, carbon cost pass-through, utility hedging, CBAM certificate cost, MSR, cắt giảm sản lượng công nghiệp do chi phí năng lượng...) — KHÔNG chấp nhận lập luận mơ hồ kiểu "có thể tác động đến năng lượng nói chung" hay suy diễn xa vời qua nhiều bước không có căn cứ trong bài.
  (3) CHIỀU TÁC ĐỘNG có thể suy ra được (tăng/giảm cung, cầu, hay giá) — không phải chỉ mô tả bối cảnh/xu hướng dài hạn định tính mà không có kết luận chiều nào (vd 1 bài chỉ nói "biến đổi khí hậu ngày càng nghiêm trọng" hoặc "EU cam kết net-zero" mà không gắn với 1 sự kiện/số liệu cụ thể tác động cung/cầu/giá trong ngắn-trung hạn → KHÔNG đạt, đánh is_relevant=false).
  Ví dụ ĐẠT: "xung đột tại eo biển X đe dọa tuyến vận chuyển LNG sang châu Âu" (nêu đích danh LNG/châu Âu, cơ chế gián đoạn cung cụ thể, chiều tác động: cung↓ → giá gas có thể tăng). Ví dụ KHÔNG ĐẠT: "hội nghị thượng đỉnh khí hậu quốc tế kêu gọi các nước hành động mạnh mẽ hơn" (không nêu cơ chế truyền dẫn cụ thể, không có chiều tác động tới thị trường nào trong danh sách).
Đánh dấu is_relevant=false khi bài KHÔNG liên quan trực tiếp, hoặc tác động tới các thị trường trên còn không rõ ràng/chỉ là khả năng xa/phải suy diễn (bài về nước/khu vực không thuộc EU ETS, kinh tế–chính trị chung, dự báo vĩ mô không nêu tác động cụ thể tới 1 thị trường đích danh...), kể cả khi bài có nhắc tên vài từ khóa liên quan mà không phân tích ảnh hưởng cụ thể (vd: tin thể thao, giải trí, đời sống, công nghệ tiêu dùng không liên quan năng lượng, tin ESG/phát triển bền vững chung chung không gắn cơ chế thị trường cụ thể...).
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
- geopolitics          : Địa chính trị có tác động (trực tiếp hoặc gián tiếp, qua cung/giá năng lượng) đến thị trường energy/carbon châu Âu HOẶC Việt Nam, bao gồm chiến tranh, xung đột, sanctions, căng thẳng ngoại giao, gián đoạn/đe dọa gián đoạn nguồn cung gas/dầu (Nga, Trung Đông, eo biển vận chuyển...), tranh chấp thương mại quốc tế, và quan hệ ngoại giao/thương mại Việt Nam với đối tác năng lượng CHỈ KHI bài nêu rõ tác động cụ thể tới nguồn cung/giá năng lượng hoặc cơ chế carbon (vd đàm phán VN mua LNG/than từ 1 nước cụ thể, thỏa thuận CBAM/carbon giữa VN và EU) — KHÔNG gắn topic này cho tin ngoại giao/thương mại VN chung chung không nêu tác động năng lượng/carbon cụ thể. KHÔNG bắt buộc phải liên quan trực tiếp đến Việt Nam — 1 sự kiện địa chính trị chỉ ảnh hưởng cung/giá gas/dầu/carbon châu Âu (không nhắc Việt Nam) vẫn được gắn topic này nếu có chuỗi tác động rõ ràng.
- eu_policy            : Chính sách, luật và quy định của EU liên quan đến năng lượng, khí hậu và quá trình decarbonization, bao gồm Fit-for-55, EU ETS reform, cap/trajectory, MSR policy, energy transition legislation và các quyết định của EU institutions.
- cbam                 : Cơ chế điều chỉnh carbon tại biên giới (Carbon Border Adjustment Mechanism), tập trung vào CBAM của EU/UK, nghĩa vụ khai báo, embedded emissions, CBAM certificates, compliance, phạm vi hàng hóa và tác động đến thương mại/xuất nhập khẩu.
- vcm                  : Thị trường carbon tự nguyện (Voluntary Carbon Market), nơi doanh nghiệp/tổ chức tự nguyện mua, bán, phát hành hoặc retire carbon credits/offsets, bao gồm Verra, Gold Standard, ACR, CAR và các dự án/tín chỉ carbon tự nguyện.
- global_carbon_market : Các thị trường carbon bắt buộc (compliance carbon markets) ngoài EU ETS, nơi doanh nghiệp phải tuân thủ giới hạn phát thải hoặc nghĩa vụ carbon theo pháp luật, như China ETS, Korea ETS, California Cap-and-Trade, RGGI, Australia và CORSIA.
- vietnam_carbon_policy: CHỈ gắn khi bài nói TRỰC TIẾP về cơ chế thị trường/chính sách carbon của Việt Nam — Nghị định 06/2022/NĐ-CP và sửa đổi, VN ETS/VETS (sàn giao dịch tín chỉ carbon), định giá carbon nội địa, hạn ngạch phát thải cho DN VN, kiểm kê khí nhà kính bắt buộc, nghĩa vụ báo cáo phát thải, dự án/tín chỉ carbon tại VN (nội địa hoặc theo Điều 6 Paris Agreement có VN tham gia), carbon market roadmap VN. KHÔNG gắn topic này (và is_relevant=false nếu không khớp topic nào khác) cho tin tức Việt Nam KHÔNG đề cập cơ chế carbon market cụ thể — vd tin kinh tế vĩ mô VN chung chung, dự án điện gió/điện mặt trời/điện than VN không nhắc tín chỉ carbon hay hạn ngạch phát thải, ngoại giao/đầu tư VN không gắn carbon, chính sách năng lượng nội địa VN thuần túy (giá điện, quy hoạch điện) không đề cập nghĩa vụ/thị trường carbon.

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
6. TIN TỨC VIỆT NAM (áp dụng cho MỌI bài liên quan Việt Nam, kể cả từ nguồn báo VN) — chỉ vì bài đến từ nguồn tin Việt Nam KHÔNG có nghĩa bài đó liên quan (is_relevant KHÔNG phụ thuộc nguồn tin, chỉ phụ thuộc nội dung):
   - Bài về carbon market/chính sách carbon VN cụ thể (VETS, Nghị định 06, kiểm kê KNK, hạn ngạch phát thải, tín chỉ carbon VN) → "vietnam_carbon_policy".
   - Bài về CBAM tác động DN xuất khẩu VN (thép/nhôm/xi măng/phân bón) → "cbam" (KHÔNG phải "vietnam_carbon_policy").
   - Bài chỉ về kinh tế vĩ mô/năng lượng nội địa/ngoại giao/đầu tư VN mà KHÔNG đề cập cơ chế carbon market hay tác động cung/cầu/giá 1 trong các thị trường ở trên → is_relevant=false, topics=[] — KHÔNG được gắn "vietnam_carbon_policy" hay bất kỳ topic nào chỉ vì bài nói về Việt Nam.

QUY TẮC PHÂN LOẠI NGHIÊM NGẶT:
1. ƯU TIÊN TRỌNG TÂM, KHÔNG LOẠI BỎ TÁC ĐỘNG GIÁN TIẾP RÕ RÀNG: Ưu tiên gắn topic mà bài dành ≥30% nội dung phân tích, xếp lên đầu; nhưng nếu bài có tác động GIÁN TIẾP mà CHÍNH BÀI NÊU RÕ (chuỗi nhân quả cụ thể, theo đúng định nghĩa is_relevant ở trên) tới 1 topic dù không đạt 30% nội dung → vẫn gắn topic sát nhất đó. Tác động không rõ ràng → KHÔNG gắn, đánh is_relevant=false.
2. TỐI ĐA 3 TOPIC: Xếp topic quan trọng/sát nhất lên đầu.
3. BÀI KHÔNG LIÊN QUAN: Trả về topics=[] và is_relevant=false khi bài KHÔNG có tác động rõ ràng (trực tiếp, hoặc gián tiếp có chuỗi nhân quả cụ thể do chính bài nêu) nào tới energy, carbon, climate, commodities, hay finance/policy/địa chính trị ảnh hưởng đến các thị trường trên — chỉ nhắc tên/đề cập thoáng qua không tính là liên quan.


HOT NEWS — đánh giá ĐỘC LẬP với việc gắn topic ở trên. Tin nóng được GỬI EMAIL NGAY tới toàn bộ khách hàng nên MẶC ĐỊNH is_hot_news=false; chỉ đánh true khi bài đưa tin 1 sự kiện MỚI, CỤ THỂ, CÓ NGUỒN XÁC NHẬN thuộc ĐÚNG 1 trong 4 nhóm sau (thường chỉ 0–2 bài/ngày đạt — còn phân vân → false):
1. political_shock — CHÍNH TRỊ GÂY SỐC THỊ TRƯỜNG: sự kiện chính trị/địa chính trị ĐÃ XẢY RA, bất ngờ, đủ sức làm giá năng lượng/carbon nhảy mạnh ngay: chiến tranh/tấn công quân sự nổ ra hoặc leo thang nhắm vào hạ tầng năng lượng hay tuyến vận tải năng lượng (Hormuz, Biển Đỏ, đường ống...); lệnh trừng phạt/cấm vận năng lượng MỚI; đảo chính/sụp đổ chính phủ ở nước cung ứng năng lượng lớn; nước lớn (G20, thành viên lớn EU, Mỹ, Trung Quốc) CHÍNH THỨC rút khỏi Thỏa thuận Paris/EU ETS/cam kết khí hậu.
   KHÔNG tính: đàm phán, phát biểu, đe doạ, cảnh báo "nguy cơ"/"tiềm ẩn" xung đột, bầu cử theo lịch, căng thẳng kéo dài không có diễn biến mới.
2. supply_cut — CẮT/GIÁN ĐOẠN NGUỒN CUNG: nguồn cung dầu, khí/LNG, than, điện châu Âu hoặc EUA bị CẮT hay gián đoạn THỰC TẾ (đã xảy ra, hoặc đã công bố chính thức có ngày) ở quy mô đủ dịch chuyển giá khu vực/toàn cầu: dừng đường ống/nhà máy LNG lớn cấp châu Âu (Nauy, Nga, Qatar, Mỹ), OPEC+ cắt sản lượng NGOÀI dự kiến (cỡ ≥1 triệu thùng/ngày), đóng tuyến vận tải chủ chốt, nước xuất khẩu lớn cấm/hạn chế xuất khẩu than hoặc khí, hàng loạt lò hạt nhân/nhà máy điện lớn dừng đột ngột, cắt đột ngột lượng EUA đưa ra đấu giá.
   KHÔNG tính: báo cáo định kỳ (tồn kho EIA, Baker Hughes, tồn kho khí EU), bảo trì theo kế hoạch, sự cố nhỏ/cục bộ, dự báo/cảnh báo thiếu hụt chưa xảy ra, TĂNG nguồn cung (xả kho dự trữ, OPEC+ tăng sản lượng, khôi phục dòng khí).
3. price_reversal — ĐẢO CHIỀU GIÁ: bài XÁC NHẬN giá EUA (hoặc TTF/Brent đang dẫn dắt EUA) đảo chiều xu hướng rõ rệt — phá vỡ xu hướng nhiều tuần bằng 1 phiên biến động mạnh (cỡ ≥4–5% với EUA/TTF) và nêu nguyên nhân cụ thể.
   KHÔNG tính: biến động hằng ngày, điều chỉnh nhỏ trong xu hướng đang có, nhận định "có thể đảo chiều", bình luận kỹ thuật.
4. unprecedented_policy — CHÍNH SÁCH CHƯA TỪNG CÓ: quyết định hoặc đề xuất CHÍNH THỨC của cơ quan có thẩm quyền (Ủy ban/Hội đồng/Nghị viện EU, chính phủ nước lớn) mà TRƯỚC ĐÓ CHƯA TỪNG được công bố hay nằm trong lộ trình: thay đổi đột ngột cap/MSR/lịch đấu giá EU ETS ngoài lộ trình, áp trần hoặc can thiệp giá EUA, hoãn/huỷ ETS2, quy định CBAM mới hoặc thay đổi đột ngột lộ trình CBAM, thuế carbon/cơ chế carbon mới ở nền kinh tế lớn.
   KHÔNG tính: bước thủ tục tiếp theo của đề xuất đã công bố (bỏ phiếu, tham vấn, thông qua đúng lộ trình), cập nhật tiến độ, hướng dẫn kỹ thuật, phát biểu ủng hộ/phản đối, đề xuất của 1 nước thành viên/doanh nghiệp/tổ chức không có thẩm quyền.
Không thuộc 4 nhóm trên → is_hot_news=false, hot_news_category=null, hot_news_reason=null. Nếu true: hot_news_category là ĐÚNG 1 mã nhóm ở trên (political_shock | supply_cut | price_reversal | unprecedented_policy), hot_news_reason là 1 câu ngắn tiếng Việt nêu sự kiện cụ thể và vì sao nó thuộc nhóm đó.\
"""

_JSON_INSTRUCTION = """

Trả lời CHỈ bằng JSON hợp lệ, không có text ngoài JSON:
{"topics": ["<topic1>", "<topic2_optional>", "<topic3_optional>"], "confidence": <0.0-1.0>, "is_relevant": <true/false>, "is_hot_news": <true/false>, "hot_news_category": <"political_shock"|"supply_cut"|"price_reversal"|"unprecedented_policy"|null>, "hot_news_reason": <string hoặc null>}
Nếu bài không liên quan: {"topics": [], "confidence": 1.0, "is_relevant": false, "is_hot_news": false, "hot_news_category": null, "hot_news_reason": null}"""

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

    def __init__(self, api_key: str, model: str = DEFAULT_CLASSIFIER_MODEL, concurrency: int = 5):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _create(self, model: str, user_content: str) -> "anthropic.types.Message":
        kwargs: dict = {}
        if not model.startswith("claude-haiku-4"):
            # Haiku 5.5 chạy adaptive thinking mặc định — tắt (được phép ở effort ≤ high) và hạ
            # effort: output là JSON ngắn cố định. Haiku 4.5 không nhận tham số effort.
            kwargs["output_config"] = {"effort": "low"}
        return await self._client.messages.create(
            model=model,
            # 512 (từ 384): tokenizer Haiku 5.5 tốn hơn ~30% token cho cùng văn bản — chừa chỗ
            # cho hot_news_reason, tránh cụt JSON.
            max_tokens=512,
            thinking={"type": "disabled"},
            # Prompt hệ thống GIỐNG HỆT nhau cho mọi bài → cache (đọc lại ~0,1× giá) — mỗi đợt
            # crawl phân loại hàng chục bài liên tiếp trong vài phút nên cache 5 phút luôn ấm.
            # Ngắn hơn ngưỡng tối thiểu của model thì API tự bỏ qua, không lỗi, không phụ phí.
            system=[{"type": "text", "text": _SYSTEM_PROMPT + _JSON_INSTRUCTION,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            **kwargs,
        )

    async def classify(self, title: Optional[str], text: str) -> ClassificationResult:
        user_content = (
            f"Tiêu đề: {title or '(không có tiêu đề)'}\n\n"
            f"Nội dung:\n{text[:MAX_TEXT_CHARS_FOR_CLASSIFICATION]}"
        )
        try:
            async with self._semaphore:
                response = await self._create(self._model, user_content)
                if response.stop_reason == "refusal" and self._model != REFUSAL_FALLBACK_MODEL:
                    logger.warning(
                        "[CLASSIFY] %s từ chối phân loại (category=%s) — gọi lại bằng %s: %.80s",
                        self._model, getattr(getattr(response, "stop_details", None), "category", None),
                        REFUSAL_FALLBACK_MODEL, title or "",
                    )
                    response = await self._create(REFUSAL_FALLBACK_MODEL, user_content)
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
    # Tin nóng được gửi email cho mọi khách hàng → bắt buộc model chọn ĐÚNG 1 trong 4 nhóm; không có
    # nhóm hợp lệ (bỏ trống/bịa nhóm khác) thì KHÔNG coi là tin nóng. Nhãn nhóm đặt đầu lý do để email/
    # chuông thông báo hiện rõ vì sao bài được gửi.
    is_hot_news = bool(data.get("is_hot_news", False))
    category = str(data.get("hot_news_category") or "").strip().lower()
    if is_hot_news and category not in HOT_NEWS_CATEGORIES:
        logger.info("[CLASSIFY] is_hot_news=true nhưng nhóm '%s' không hợp lệ — bỏ cờ tin nóng.", category)
        is_hot_news = False
    hot_news_reason = data.get("hot_news_reason") if is_hot_news else None
    if hot_news_reason is not None and not isinstance(hot_news_reason, str):
        hot_news_reason = None
    if is_hot_news:
        label = HOT_NEWS_CATEGORIES[category]
        hot_news_reason = f"[{label}] {(hot_news_reason or '').strip()}".strip()

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
        effective_model = model or DEFAULT_CLASSIFIER_MODEL
        return AnthropicClassifier(
            api_key=anthropic_api_key,
            model=effective_model,
            concurrency=concurrency,
        )
    raise ValueError(
        f"CLASSIFIER_BACKEND không hợp lệ: '{backend}'. Chỉ hỗ trợ 'anthropic'."
    )
