
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

Nhiệm vụ: đọc TITLE + ARTICLE CONTENT, xác định bài có tác động tới giá/cung/cầu EUA (trực tiếp, hoặc qua 1 yếu tố dẫn dắt EUA) hay không, nêu kênh tác động và gắn 1–3 topic phù hợp nhất.

XÁC ĐỊNH is_relevant (QUAN TRỌNG — quyết định bài có được lưu vào hệ thống hay không):
NGUYÊN TẮC GỐC: hệ thống phục vụ bàn giao dịch EUA. CHỈ GIỮ bài tác động TRỰC TIẾP tới GIÁ, CUNG hoặc CẦU EUA, hoặc tác động
GIÁN TIẾP nhưng bài phải nêu tác động TRỰC TIẾP lên MỘT YẾU TỐ DẪN DẮT GIÁ EUA trong danh sách "KÊNH" bên dưới. Không chỉ ra
được kênh nào → is_relevant=false (thà bỏ sót tin mờ còn hơn lưu tin không tác động tới EUA). "Liên quan năng lượng/khí hậu
nói chung" KHÔNG đủ.

KÊNH TÁC ĐỘNG TỚI EUA ("eua_channel" — BẮT BUỘC chọn đúng 1 mã khi is_relevant=true):
- "eua_direct": bài nói thẳng về EUA/EU ETS — giá, giao dịch, futures/options, đấu giá (lịch, khối lượng, kết quả), MSR,
  cap/hệ số giảm tuyến tính, phân bổ miễn phí, phát thải đã xác minh/nghĩa vụ tuân thủ, vị thế quỹ (COT), dòng vốn vào/ra
  carbon EU, chính sách/sửa đổi EU ETS (rà soát ETS, ETS2, mở rộng phạm vi, loại bỏ carbon vào ETS, đề xuất nới/tạm dừng/áp
  trần của nước thành viên hay doanh nghiệp, lập trường Hội đồng/Nghị viện/Ủy ban), liên kết EU ETS với hệ thống khác (UK,
  Thụy Sĩ), tín chỉ quốc tế trong mục tiêu khí hậu EU.
- "gas_eu": giá/cung khí cho CHÂU ÂU — TTF, LNG nhập vào châu Âu, đường ống tới châu Âu, tồn kho/lấp kho khí EU, quy định thị
  trường khí EU (kênh fuel switching khí ↔ than của ngành điện).
- "coal_eu": than cho điện châu Âu — API2, xuất khẩu than sang châu Âu, đóng/mở/huy động nhà máy điện than ở EU.
- "power_eu": điện châu Âu — giá điện, nhu cầu điện, sản lượng gió/mặt trời/thủy điện/hạt nhân TẠI EU, sự cố/đóng nhà máy
  điện lớn ở EU, thời tiết châu Âu (nhiệt độ, gió, nắng, mưa/hạn) làm đổi nhu cầu hoặc sản lượng điện (→ phát thải ngành điện).
- "industry_eu": sản lượng/hoạt động của ngành THUỘC EU ETS tại EU (thép, xi măng, nhôm, hóa chất, phân bón, lọc dầu, giấy,
  hàng không/hàng hải nội EU) — cắt giảm, đóng cửa, khởi động lại, đơn hàng; PMI/sản xuất công nghiệp khu vực euro khi bài nêu
  đích danh tác động tới các ngành này.
- "oil_shock": dầu CHỈ khi (a) quyết định/gián đoạn nguồn cung lớn có quy mô cụ thể (quyết định sản lượng OPEC+, cấm vận/trừng
  phạt nước xuất khẩu lớn, đóng/đe dọa cụ thể tuyến vận tải chủ chốt như Hormuz/Biển Đỏ), hoặc (b) bài nêu thẳng tác động tới giá
  khí/LNG/điện châu Âu hay lọc dầu EU. Bản tin giá dầu hằng ngày, tồn kho dầu Mỹ, chính trị nội bộ Mỹ về giấy phép nhiên liệu,
  cước tàu dầu → KHÔNG đạt.
- "geopolitics_energy_eu": xung đột/trừng phạt/ngoại giao làm GIÁN ĐOẠN hoặc ĐE DỌA CỤ THỂ nguồn cung khí/than/điện tới châu Âu
  (Nga, Na Uy, Qatar, Bắc Phi, tuyến Baltic...) — phải nêu nguồn cung/tuyến cụ thể bị ảnh hưởng.
- "cbam": CBAM của EU/UK — giá chứng chỉ, nghĩa vụ khai báo, phạm vi hàng hóa, lộ trình cắt phân bổ miễn phí, tác động tới nhà
  xuất khẩu (kể cả doanh nghiệp Việt Nam). (Giữ vì CBAM quyết định cầu EUA qua phân bổ miễn phí và giá CBAM = VWAP đấu giá EUA.)
- "vn_carbon": NGOẠI LỆ HẸP — chính sách/thị trường carbon VIỆT NAM có quyết định, văn bản hoặc số liệu cụ thể (Nghị định, sàn
  VETS, phân bổ hạn ngạch, kiểm kê KNK bắt buộc, giao dịch/phát hành tín chỉ của dự án tại VN có số liệu). Hội thảo, định hướng,
  hợp tác địa phương, kêu gọi đầu tư chung chung → KHÔNG đạt.
- "carbon_link": NGOẠI LỆ HẸP — thị trường carbon tự nguyện (VCM) hoặc ETS ngoài EU (Trung Quốc, Hàn Quốc, California, CORSIA...)
  CHỈ khi bài nêu thẳng liên kết sang EU ETS/EUA hoặc CBAM (vd công nhận tín chỉ trong hệ thống EU, ETS Trung Quốc dùng để khấu trừ
  CBAM). Thương vụ mua bán tín chỉ/loại bỏ carbon tự nguyện, sổ tay/hướng dẫn Article 6, tài liệu MRV → KHÔNG đạt.

ĐIỀU KIỆN CHO KÊNH GIÁN TIẾP (mọi mã trừ "eua_direct"/"cbam"/"vn_carbon"): CHÍNH BÀI phải nêu (1) đích danh yếu tố trong kênh
(vd "giá TTF", "sản lượng gió Đức", "nhà máy thép ở Đức tạm dừng"), (2) diễn biến/sự kiện CỤ THỂ đã xảy ra hoặc đã công bố có
quy mô/ngày (không phải dự báo dài hạn chung chung hay khả năng xa), và (3) chiều tác động lên yếu tố đó (tăng/giảm cung, cầu,
giá). Phải tự suy luận thêm nhiều bước mới nối được tới EUA → is_relevant=false.
Ví dụ ĐẠT: "Na Uy dừng mỏ Troll 5 ngày, TTF tăng 8%" (gas_eu); "ArcelorMittal tạm dừng 2 lò cao ở Pháp" (industry_eu); "Ý và
Séc đề nghị tạm dừng chuyển hạn ngạch vào MSR" (eua_direct); "Estonia muốn đóng tuyến ống cấp khí Kaliningrad" chỉ đạt khi bài
nêu tác động tới nguồn cung/giá khí EU.
Ví dụ KHÔNG ĐẠT: dự án điện gió/hydrogen dự kiến vận hành sau nhiều năm; nhà máy lọc dầu đồng xử lý SAF; thỏa thuận mua tín chỉ
loại bỏ carbon tự nguyện; sổ tay tài chính khí hậu trước COP; hợp tác công nghiệp xanh của 1 tỉnh Việt Nam; nghị sĩ Mỹ đòi thu hồi
giấy phép diesel Nga; Mỹ cân nhắc gỡ trừng phạt tàu dầu để hạ cước; tin ESG/phát triển bền vững chung chung; kinh tế vĩ mô không nêu
ngành ETS cụ thể; mọi tin chỉ NHẮC TÊN từ khóa mà không có sự kiện tác động.
Khi is_relevant=true: PHẢI gắn ít nhất 1 topic sát nhất trong danh sách bên dưới VÀ đúng 1 "eua_channel". Khi is_relevant=false:
"eua_channel" = null.

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
   - Bài chỉ về kinh tế vĩ mô/năng lượng nội địa/ngoại giao/đầu tư VN, hoặc tin quốc tế đăng lại trên báo VN mà không có kênh tác động tới EUA → is_relevant=false, topics=[] — KHÔNG được gắn "vietnam_carbon_policy" hay bất kỳ topic nào chỉ vì bài nói về Việt Nam.

QUY TẮC PHÂN LOẠI NGHIÊM NGẶT:
1. ƯU TIÊN TRỌNG TÂM, KHÔNG LOẠI BỎ TÁC ĐỘNG GIÁN TIẾP RÕ RÀNG: Ưu tiên gắn topic mà bài dành ≥30% nội dung phân tích, xếp lên đầu; nhưng nếu bài có tác động GIÁN TIẾP mà CHÍNH BÀI NÊU RÕ (chuỗi nhân quả cụ thể, theo đúng định nghĩa is_relevant ở trên) tới 1 topic dù không đạt 30% nội dung → vẫn gắn topic sát nhất đó. Tác động không rõ ràng → KHÔNG gắn, đánh is_relevant=false.
2. TỐI ĐA 3 TOPIC: Xếp topic quan trọng/sát nhất lên đầu.
3. BÀI KHÔNG LIÊN QUAN: Trả về topics=[], eua_channel=null và is_relevant=false khi bài KHÔNG chỉ ra được 1 "eua_channel" hợp lệ theo đúng định nghĩa ở trên — liên quan năng lượng/khí hậu/hàng hóa nói chung, hay chỉ nhắc tên/đề cập thoáng qua, KHÔNG tính là liên quan.


HOT NEWS — CHỈ xét khi is_relevant=true (bài không liên quan EUA không được lưu nên cũng không là tin nóng). Tin nóng được GỬI EMAIL NGAY tới toàn bộ khách hàng nên MẶC ĐỊNH is_hot_news=false; chỉ đánh true khi bài đưa tin 1 sự kiện MỚI, CỤ THỂ, CÓ NGUỒN XÁC NHẬN thuộc ĐÚNG 1 trong 4 nhóm sau (thường chỉ 0–2 bài/ngày đạt — còn phân vân → false):
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
{"topics": ["<topic1>", "<topic2_optional>", "<topic3_optional>"], "confidence": <0.0-1.0>, "is_relevant": <true/false>, "eua_channel": <"eua_direct"|"gas_eu"|"coal_eu"|"power_eu"|"industry_eu"|"oil_shock"|"geopolitics_energy_eu"|"cbam"|"vn_carbon"|"carbon_link"|null>, "is_hot_news": <true/false>, "hot_news_category": <"political_shock"|"supply_cut"|"price_reversal"|"unprecedented_policy"|null>, "hot_news_reason": <string hoặc null>}
Nếu bài không liên quan: {"topics": [], "confidence": 1.0, "is_relevant": false, "eua_channel": null, "is_hot_news": false, "hot_news_category": null, "hot_news_reason": null}"""

# Kênh tác động tới EUA mà bài phải chỉ ra được để is_relevant=true (xem _SYSTEM_PROMPT). "vn_carbon"/"carbon_link" là ngoại
# lệ hẹp giữ tin cho Mục 4 (Tín chỉ carbon & CBAM) và gợi ý kinh doanh SIM.
EUA_CHANNELS = {
    "eua_direct", "gas_eu", "coal_eu", "power_eu", "industry_eu", "oil_shock", "geopolitics_energy_eu", "cbam",
    "vn_carbon", "carbon_link",
}

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
    # Chỉ lưu bài chỉ ra được KÊNH tác động tới EUA — model trả is_relevant=true mà không nêu kênh hợp lệ thì coi là
    # không liên quan (kiểm cứng, không chỉ dựa vào prompt).
    channel = str(data.get("eua_channel") or "").strip().lower() or None
    if is_relevant and channel not in EUA_CHANNELS:
        logger.info("[CLASSIFY] is_relevant=true nhưng không có kênh tác động EUA hợp lệ (%r) — coi là không liên quan.", channel)
        is_relevant = False
    if not is_relevant:
        channel = None

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

    # Hot news CHỈ cho bài liên quan EUA — bài không liên quan không được lưu (crawl_pipeline.py) nên không còn ngoại lệ
    # "tin nóng nhưng không khớp topic nào vẫn lưu".
    # Tin nóng được gửi email cho mọi khách hàng → bắt buộc model chọn ĐÚNG 1 trong 4 nhóm; không có
    # nhóm hợp lệ (bỏ trống/bịa nhóm khác) thì KHÔNG coi là tin nóng. Nhãn nhóm đặt đầu lý do để email/
    # chuông thông báo hiện rõ vì sao bài được gửi.
    is_hot_news = bool(data.get("is_hot_news", False)) and is_relevant
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
        eua_channel=channel,
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
