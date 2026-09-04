# -*- coding: utf-8 -*-
"""
KHUNG TRI THỨC NHÂN QUẢ — PHÂN TÍCH GIÁ EUA (EU ETS)
=====================================================

Chuỗi nhân quả CHUẨN (single source of truth) mô tả các yếu tố ảnh hưởng đến
cung/cầu và giá EUA — đây là "khung phân tích chuẩn" mà mọi phân tích EUA
trong hệ thống (báo cáo lẫn chat) PHẢI bám sát, không được tự suy diễn lệch.

DÙNG CHUNG cho:
  - services/report_generator.py -> EUA_ANALYSIS_FRAMEWORK / EUA_FRAMEWORK_COMPACT (báo cáo Mục 2/3/5)
  - services/quote_chat.py       -> _DOMAIN_KNOWLEDGE (chat)
Mọi chuỗi nhân quả xuất hiện ở cả 2 nơi PHẢI import từ đây, KHÔNG hard-code
lại — sửa 1 chỗ trong file này là đồng bộ toàn hệ thống.

PHẠM VI TOPIC — 3 topic CHỦ ĐÍCH KHÔNG có cơ chế/chuỗi nào trong file này:
  vcm (thị trường carbon tự nguyện), global_carbon_market (thị trường carbon
  compliance NGOÀI EU ETS — China ETS, Korea ETS, California Cap-and-Trade,
  RGGI, CORSIA...), vietnam_carbon_policy (chính sách/thị trường carbon VN).
  Lý do: các thị trường này KHÔNG fungible với EUA (tín chỉ/allowance không
  quy đổi/thay thế lẫn nhau, hệ thống compliance tách biệt) nên KHÔNG tạo
  cầu/cung EUA trực tiếp — xem NON_EUA_CARBON_MARKETS bên dưới và luật L11.
  3 topic này CHỈ phục vụ Mục 4 (thông tin thị trường carbon khác) và Mục 8,
  KHÔNG được dùng để kết luận tác động cung/cầu/giá EUA ở Mục 3/5/chat trừ
  khi tin tức nêu rõ 1 cơ chế cụ thể nối sang EUA (vd CBAM, dòng vốn chuyển
  đổi) — đây là điều dễ bị bỏ sót nhất khi thêm nội dung mới vào file này,
  vì các cơ chế khác đều mô tả "CÓ chuỗi nhân quả", còn 3 topic trên phải
  được nêu ĐÍCH DANH là "KHÔNG CÓ", nếu không người tiêm prompt (hoặc chính
  model) dễ tự suy diễn ra 1 liên kết không có thật.

Thiết kế lại từ bản gốc với 3 thay đổi cấu trúc:

1. Thêm INFERENCE_RULES — bộ luật suy luận DÙNG CHUNG, đặt trước mọi cơ chế.
   Trước đây các quy tắc chống-suy-diễn bị lặp rải rác trong từng biến
   ("CHỈ nêu khi tin tức xác nhận...") -> gom về một chỗ, mỗi cơ chế chỉ giữ
   phần bác bỏ ĐẶC THÙ của nó.

2. Chuẩn hoá TEMPLATE 8 trường cho mọi cơ chế:
   [ID] [TOPIC] [HORIZON] [ĐỘ MẠNH] [KÍCH HOẠT] [CHUỖI] [BÁC BỎ] [DỮ LIỆU]
   Nhờ đó model đọc được: khi nào ĐƯỢC dùng, khi nào PHẢI ngừng dùng, và cần
   dữ liệu gì — thay vì chỉ đọc một chuỗi mũi tên.

3. Thêm HORIZON + ĐỘ MẠNH cho từng cơ chế và CONFLICT_RESOLUTION để xử lý
   trường hợp nhiều cơ chế cho tín hiệu TRÁI CHIỀU (bản gốc chưa có luật
   phân xử, dẫn tới kết luận tuỳ hứng).

LƯU Ý CHO NGƯỜI TIÊM PROMPT (report_generator.py / quote_chat.py):
- CẤM TUYỆT ĐỐI để lộ trong text trả về cho người đọc: tên biến nội bộ
  (FUEL_SWITCHING, POLICY_MSR...), nhãn "nhánh (a)/(b)", các thẻ ngoặc vuông
  ([ID], [TOPIC], [HORIZON], [ĐỘ MẠNH], [KÍCH HOẠT], [CHUỖI], [BÁC BỎ],
  [DỮ LIỆU]), hay mã luật (L1, L2, ..., L10). Các thẻ/mã này CHỈ là công cụ
  nội bộ giúp model tự tổ chức suy luận — đầu ra chỉ được viết bằng ngôn ngữ
  phân tích tự nhiên.
- INFERENCE_RULES nên được đặt Ở ĐẦU prompt (đọc luật trước khi đọc cơ chế).
- CONFLICT_RESOLUTION nên đặt Ở CUỐI phần khung (đọc ngay trước khi kết luận).

Các lỗi logic đã sửa so với bản gốc — xem CHANGELOG ở cuối file.
"""

# =============================================================================
# PHẦN 0 — LUẬT SUY LUẬN DÙNG CHUNG (đọc TRƯỚC mọi cơ chế bên dưới)
# =============================================================================

INFERENCE_RULES = (
    "LUẬT SUY LUẬN BẮT BUỘC — áp dụng cho MỌI cơ chế trong khung phân tích này. "
    "Nếu một suy luận vi phạm bất kỳ luật nào dưới đây, PHẢI hạ cấp thành 'không kết luận' "
    "thay vì đưa ra nhận định về hướng giá EUA.\n"
    "\n"
    "L1 — LUẬT NÚT CUỐI (terminal node): mọi chuỗi nhân quả BẮT BUỘC phải kết thúc ở đúng 1 "
    "trong 4 nút trước khi nói về giá: (a) CẦU EUA↑, (b) CẦU EUA↓, (c) CUNG EUA↑, (d) CUNG EUA↓. "
    "Giá EUA = f(cung allowance do chính sách đặt ra, cầu allowance do phát thải thực tế + kỳ vọng). "
    "Nếu không nối được sự kiện về 1 trong 4 nút này → KHÔNG kết luận hướng giá.\n"
    "\n"
    "L2 — LUẬT KHÔNG NHẢY BƯỚC (no-skip): không được rút gọn chuỗi. Sự kiện càng xa EUA thì càng "
    "phải đi qua đủ các bước trung gian đã định nghĩa trong cơ chế tương ứng. Lỗi hay gặp nhất: "
    "nhảy thẳng từ tin địa chính trị / tin vĩ mô / giá một loại nhiên liệu sang kết luận EUA.\n"
    "\n"
    "L3 — LUẬT GIÁ TƯƠNG ĐỐI (relative, not absolute): với mọi suy luận liên quan tới lựa chọn "
    "nhiên liệu phát điện, chỉ giá TƯƠNG ĐỐI giữa các nhiên liệu (sau chi phí carbon) mới có ý "
    "nghĩa. Giá tuyệt đối của MỘT nhiên liệu tăng/giảm, tự nó, KHÔNG xác định được hướng dispatch.\n"
    "\n"
    "L4 — LUẬT BẤT NGỜ (surprise, not level): thị trường phản ứng với phần LỆCH so với kỳ vọng, "
    "không phải với mức tuyệt đối hay với thông tin đã được công bố từ trước. Lịch đấu giá đã "
    "công bố, lộ trình cap đã lập trình sẵn, deadline compliance cố định hàng năm — những thứ này "
    "ĐÃ nằm trong giá; chúng chỉ trở thành tín hiệu khi có THAY ĐỔI so với kế hoạch/kỳ vọng "
    "(hoãn phiên đấu giá, sửa khối lượng, kết quả đấu giá lệch mạnh so với giá thứ cấp, số liệu "
    "phát thải công bố lệch dự báo).\n"
    "\n"
    "L5 — LUẬT XÁC NHẬN CHÉO (≥2 kênh): chỉ gán độ tin cậy MẠNH khi có tối thiểu 2 kênh ĐỘC LẬP "
    "cùng chiều (vd: giá nhiên liệu tương đối + dữ liệu phát điện thực tế; hoặc chính sách + "
    "khối lượng đấu giá). Một kênh đơn lẻ → tối đa TRUNG BÌNH. Kênh suy đoán (không có số liệu "
    "trong tin) → YẾU hoặc bỏ.\n"
    "\n"
    "L6 — LUẬT TRÁI CHIỀU (conflict): khi các cơ chế cho tín hiệu ngược nhau, KHÔNG được chọn "
    "tuỳ tiện một chiều. Phải (i) nêu rõ cả hai chiều, (ii) phân xử theo CONFLICT_RESOLUTION "
    "bên dưới, (iii) nếu không phân xử được thì kết luận 'tín hiệu hỗn hợp / trung tính'.\n"
    "\n"
    "L7 — LUẬT KHÔNG BỊA SỐ (no-fabrication): hệ thống CÓ dữ liệu giá cho: EUA, TTF (gas), "
    "than (API2), Brent/WTI, Gasoil, điện Đức baseload (DEBY1). Hệ thống KHÔNG có: Clean Dark/"
    "Spark Spread, open interest, volume, options skew, COT positioning, sản lượng RES, dữ liệu "
    "thời tiết, giá kim loại, khối lượng đấu giá theo phiên. Các đại lượng thuộc nhóm KHÔNG CÓ "
    "chỉ được dùng khi bản tin trích dẫn nêu SỐ LIỆU CỤ THỂ; tuyệt đối không tự suy ra, không "
    "ước lượng, không mô tả định tính thay cho số liệu.\n"
    "\n"
    "L8 — LUẬT ĐỘ LỚN & ĐỘ BỀN (magnitude & persistence): phân biệt (i) biến động một phiên do "
    "dòng tiền/kỹ thuật, (ii) thay đổi kéo dài nhiều tuần của biến nền tảng. Chỉ nhóm (ii) mới "
    "được gọi là 'xu hướng'. Một cú biến động giá đơn lẻ KHÔNG đủ để kết luận thay đổi fundamentals.\n"
    "\n"
    "L9 — LUẬT PHẠM VI HỆ THỐNG (scope): chỉ phát thải thuộc PHẠM VI EU ETS 1 mới tạo cầu EUA. "
    "Phát thải ngoài phạm vi (giao thông đường bộ, sưởi ấm dân dụng — thuộc ETS2 với allowance "
    "RIÊNG; hoạt động ngoài EU/EEA; ngành không thuộc Annex I) KHÔNG trực tiếp tạo cầu EUA. "
    "Trước khi nối 'phát thải↑ → cầu EUA↑', phải kiểm tra nguồn phát thải đó có nằm trong ETS1 không.\n"
    "\n"
    "L10 — LUẬT MẶC ĐỊNH THẬN TRỌNG: 'không đủ căn cứ để kết luận' luôn là đáp án hợp lệ và "
    "được ưu tiên hơn một kết luận có hướng nhưng thiếu bước trung gian hoặc thiếu dữ liệu.\n"
    "\n"
    "L11 — LUẬT THỊ TRƯỜNG KHÁC (non-fungible markets): tin tức về VCM (thị trường carbon tự "
    "nguyện — Verra, Gold Standard, ACR, CAR...), thị trường carbon compliance NGOÀI EU ETS "
    "(China ETS, Korea ETS, California Cap-and-Trade, RGGI, CORSIA...), hay chính sách/thị "
    "trường carbon Việt Nam (VETS, Nghị định 06...) KHÔNG được mặc định suy ra tác động cung/"
    "cầu/giá EUA — các thị trường này KHÔNG fungible với EUA (tín chỉ/allowance không quy đổi/"
    "thay thế lẫn nhau), nên TUYỆT ĐỐI KHÔNG áp bất kỳ cơ chế nào trong PHẦN 1 cho tin thuộc "
    "nhóm này. CHỈ phân tích tác động EUA nếu chính bản tin nêu RÕ 1 cơ chế cụ thể nối sang EUA "
    "(vd CBAM certificate cost neo theo giá EUA, hay dòng vốn/doanh nghiệp chuyển từ thị trường "
    "đó sang mua EUA compliance) — không suy diễn liên kết khi bản tin không nêu rõ."
)


CONFLICT_RESOLUTION = (
    "PHÂN XỬ KHI CÁC CƠ CHẾ CHO TÍN HIỆU TRÁI CHIỀU — xếp theo KHUNG THỜI GIAN của nhận định "
    "đang cần đưa ra. Cơ chế thuộc đúng khung thời gian của câu hỏi có trọng số cao hơn.\n"
    "\n"
    "A) Nhận định NGÀY / TRONG PHIÊN — thứ tự ưu tiên giảm dần:\n"
    "   1. Tin chính sách/pháp lý BẤT NGỜ (POLICY_MSR) — tác động tức thời, ghi đè mọi kênh khác.\n"
    "   2. Kết quả đấu giá bất thường & sự kiện lịch đấu giá (POLICY_MSR).\n"
    "   3. Cú sốc giá gas/điện đủ mạnh (FUEL_SWITCHING, POWER_EUA_TWO_WAY).\n"
    "   4. Positioning/kỹ thuật (POSITIONING_TECHNICALS) — chỉ khi tin có số liệu.\n"
    "   5. Macro, hydrogen, kim loại — thường KHÔNG dùng cho khung ngày.\n"
    "\n"
    "B) Nhận định TUẦN / THÁNG — thứ tự ưu tiên giảm dần:\n"
    "   1. Kinh tế nhiên liệu tương đối + hệ thống điện (RELATIVE_FUEL_ECONOMICS > FUEL_SWITCHING, "
    "      xác nhận bởi WEATHER_POWER_SYSTEM).\n"
    "   2. Cung sơ cấp: lịch/khối lượng đấu giá, chu kỳ compliance (POLICY_MSR).\n"
    "   3. Địa chính trị qua kênh cung nhiên liệu (GEOPOLITICS_SUPPLY_CHAIN nhánh a).\n"
    "   4. Positioning & term structure (POSITIONING_TECHNICALS, TERM_STRUCTURE_CARRY).\n"
    "   5. Macro (MACRO) — chỉ khi có số liệu vĩ mô cụ thể.\n"
    "\n"
    "C) Nhận định QUÝ / NĂM TRỞ LÊN — thứ tự ưu tiên giảm dần:\n"
    "   1. Thiết kế thị trường: cap/LRF, MSR, free allocation, mở rộng phạm vi (POLICY_MSR, CBAM_ETS).\n"
    "   2. Kỳ vọng chính sách & rủi ro pháp lý (POLICY_MSR, GEOPOLITICS_SUPPLY_CHAIN nhánh b).\n"
    "   3. Chuyển dịch cơ cấu năng lượng & decarbonization (HYDROGEN_DECARBONIZATION, RES).\n"
    "   4. Chu kỳ kinh tế (MACRO).\n"
    "   5. Positioning — KHÔNG dùng cho khung này.\n"
    "\n"
    "QUY TẮC BỔ SUNG:\n"
    "- Kênh CUNG (chính sách, đấu giá, MSR) thắng kênh CẦU khi cả hai cùng độ tin cậy, vì cung "
    "  EUA là biến ngoại sinh do quy định đặt ra, không phản ứng với giá trong ngắn hạn.\n"
    "- Kênh có SỐ LIỆU trong tin thắng kênh chỉ có mô tả định tính.\n"
    "- Kênh NGƯỢC CHIỀU dạng 'cắt giảm sản lượng do chi phí năng lượng cao' (trong FUEL_SWITCHING, "
    "  OIL_GASOIL, METALS) chỉ được đưa vào cân nhắc khi tin xác nhận cắt giảm THỰC TẾ; nếu không, "
    "  coi như không tồn tại, không dùng để 'trung hoà' tín hiệu chính.\n"
    "- Nếu sau khi phân xử vẫn còn hai chiều tương đương → kết luận TRUNG TÍNH và nêu rõ điều kiện "
    "  nào sẽ phá thế cân bằng (biến cần theo dõi tiếp)."
)


# Xem thêm luật L11 ở trên. Tách thành constant riêng (thay vì chỉ nằm trong
# INFERENCE_RULES) để report_generator.py/quote_chat.py có thể tiêm ĐÍCH DANH
# ngay tại vị trí liệt kê VCM/thị trường carbon khác/chính sách carbon VN
# (thay vì trông chờ model tự nhớ lại L11 từ đầu prompt) — đây chính là kẽ hở
# đã gây lỗi thực tế: 1 báo cáo đã viết "Kết luận: trung lập" cho tin CORSIA
# thay vì bỏ hẳn khỏi phần phân tích, vì lúc đó chưa có constant này để tiêm
# ngay tại chỗ, hệ thống phải trông chờ model tự suy luận gián tiếp.
NON_EUA_CARBON_MARKETS = (
    "VCM (thị trường carbon tự nguyện — Verra, Gold Standard, ACR, CAR...), thị trường carbon "
    "compliance NGOÀI EU ETS (China ETS, Korea ETS, California Cap-and-Trade, RGGI, CORSIA...), "
    "và chính sách/thị trường carbon Việt Nam (VETS, Nghị định 06...) KHÔNG fungible với EUA — "
    "tín chỉ/allowance của các thị trường này không quy đổi/thay thế được cho EUA, hệ thống "
    "compliance hoàn toàn tách biệt. TIN VỀ CÁC THỊ TRƯỜNG NÀY KHÔNG ĐƯỢC DÙNG để kết luận tác "
    "động cung/cầu/giá EUA — kể cả kết luận 'trung lập' — CHỈ nêu mang tính thông tin, TRỪ KHI "
    "chính bản tin nêu RÕ 1 cơ chế cụ thể nối sang EUA (vd CBAM certificate cost neo theo giá "
    "EUA, hay dòng vốn/doanh nghiệp chuyển từ thị trường đó sang mua EUA compliance) thì mới "
    "phân tích, và phải nêu rõ đúng cơ chế đó — không suy diễn liên kết khi bản tin không nêu rõ."
)


# =============================================================================
# PHẦN 1 — CÁC CƠ CHẾ NHÂN QUẢ
# Template: [ID] [TOPIC] [HORIZON] [ĐỘ MẠNH] [KÍCH HOẠT] [CHUỖI] [BÁC BỎ] [DỮ LIỆU]
# =============================================================================

# -----------------------------------------------------------------------------
# 1.1 FUEL SWITCHING — chuỗi lõi của toàn khung.
# Sửa lỗi bản gốc: bỏ hẳn suy luận "giá than tăng → đốt than nhiều hơn" và làm rõ
# Gas↓ và Than↑ là hai tín hiệu CÙNG CHIỀU (cùng làm gas rẻ hơn tương đối).
# -----------------------------------------------------------------------------
FUEL_SWITCHING = (
    "[ID] FUEL_SWITCHING — Chuyển đổi nhiên liệu phát điện (chuỗi LÕI của toàn khung phân tích).\n"
    "[TOPIC] energy_gas, energy_coal, energy_power_eu, eua_ets.\n"
    "[HORIZON] Ngày → tháng. Là kênh giải thích biến động EUA ngắn/trung hạn quan trọng nhất.\n"
    "[ĐỘ MẠNH] Mạnh KHI có xác nhận từ dữ liệu phát điện hoặc spread; Trung bình khi chỉ có giá "
    "nhiên liệu; Yếu khi chỉ có biến động một phiên.\n"
    "\n"
    "[KÍCH HOẠT] Khi có thay đổi đáng kể ở giá gas (TTF), giá than (API2), sản lượng RES, hoặc "
    "phụ tải điện — và thay đổi đó đủ lớn/đủ bền để có thể làm đổi thứ tự huy động (merit order).\n"
    "\n"
    "[CHUỖI]\n"
    "Nguyên lý nền: nhà máy điện chọn đốt nhiên liệu nào RẺ HƠN SAU KHI cộng chi phí carbon. "
    "Vì than phát thải khoảng gấp 2–2,5 lần khí trên mỗi MWh, mỗi lần dispatch dịch chuyển giữa "
    "than và khí sẽ làm thay đổi mạnh tổng phát thải ngành điện — tức thay đổi cầu EUA.\n"
    "\n"
    "  (1) Gas↑ trong khi than không đổi hoặc tăng ít hơn → than RẺ hơn tương đối → dispatch "
    "chuyển sang than → phát thải/MWh↑ → cầu EUA↑ → EUA↑.\n"
    "  (2) Gas↓ HOẶC Than↑ → gas RẺ hơn tương đối → dispatch chuyển sang gas → phát thải/MWh↓ → "
    "cầu EUA↓ → EUA↓.\n"
    "      LƯU Ý QUAN TRỌNG: (2) gộp hai tín hiệu 'Gas↓' và 'Than↑' vì chúng CÙNG CHIỀU — cả hai "
    "đều làm gas rẻ hơn tương đối so với than. Đây KHÔNG phải hai tín hiệu trái chiều.\n"
    "  (3) RES (gió/mặt trời)↑ → nhu cầu phát điện nhiệt (than+gas)↓ → phát thải↓ → cầu EUA↓ → EUA↓. "
    "Chiều ngược lại: RES↓ (lặng gió kéo dài, ít nắng) → phát điện nhiệt↑ → cầu EUA↑ → EUA↑.\n"
    "  (4) Phụ tải điện↑ (nhiệt độ cực đoan, hoạt động kinh tế) mà phần tăng thêm được đáp ứng bằng "
    "nguồn hoá thạch trong ETS → phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "  (5) Gián đoạn nguồn cung gas hoặc sự cố hạ tầng điện → hệ thống buộc huy động than để bù → "
    "phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "      Ở nhánh (5), giá than tăng là HỆ QUẢ của cầu than tăng do gián đoạn, KHÔNG phải nguyên "
    "nhân. Chỉ dùng nhánh này khi tin tức xác nhận rõ sự cố/gián đoạn.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - TUYỆT ĐỐI KHÔNG suy luận 'giá than tăng → nhà máy đốt than nhiều hơn'. Than tăng giá một "
    "mình làm than ĐẮT hơn tương đối → dispatch chuyển SANG gas → tín hiệu EUA GIẢM (nhánh 2).\n"
    "  - Cơ chế chỉ vận hành khi hệ thống còn công suất dự phòng ở CẢ hai loại nhiên liệu và giá "
    "tương đối nằm trong 'vùng chuyển đổi'. Khi than đã chạy hết công suất khả dụng, hoặc khi "
    "chênh lệch quá lớn khiến một loại nhiên liệu luôn thắng, gas↑ thêm KHÔNG tạo thêm coal burn.\n"
    "  - Không kết luận từ biến động một phiên: cần thay đổi đủ bền để utility đổi kế hoạch dispatch/hedge.\n"
    "  - Nếu giá điện tăng chỉ do EUA pass-through (xem POWER_EUA_TWO_WAY nhánh b) thì đó KHÔNG "
    "phải tín hiệu cầu EUA mới — tránh đếm trùng.\n"
    "\n"
    "[KÊNH PHỤ NGƯỢC CHIỀU] Gas/than/điện tăng RẤT MẠNH & kéo dài có thể vượt ngưỡng chịu đựng chi "
    "phí của ngành thâm dụng năng lượng → cắt giảm sản lượng → phát thải ngành đó↓ → cầu EUA↓, dù "
    "dispatch điện vẫn nghiêng về than. CHỈ nêu khi tin tức xác nhận CẮT GIẢM SẢN LƯỢNG THỰC TẾ "
    "(số liệu, thông báo doanh nghiệp). KHÔNG suy diễn từ riêng việc giá năng lượng tăng.\n"
    "\n"
    "[DỮ LIỆU] Có sẵn: TTF, API2, EUA, DEBY1. Không có sẵn: sản lượng RES, coal/gas burn thực tế, "
    "spread — chỉ dùng khi tin tức nêu số liệu (xem RELATIVE_FUEL_ECONOMICS)."
)


# -----------------------------------------------------------------------------
# 1.2 Quan hệ HAI CHIỀU điện Đức (DEBY1) <-> EUA.
# Bổ sung so với bản gốc: luật chống ĐẾM TRÙNG giữa hai nhánh (a) và (b).
# -----------------------------------------------------------------------------
POWER_EUA_TWO_WAY = (
    "[ID] POWER_EUA_TWO_WAY — Quan hệ nhân quả HAI CHIỀU giữa giá điện Đức (DEBY1) và EUA.\n"
    "[TOPIC] energy_power_eu, eua_ets.\n"
    "[HORIZON] Ngày → tháng.\n"
    "[ĐỘ MẠNH] Trung bình → Mạnh khi xác định được NGUYÊN NHÂN làm giá điện thay đổi.\n"
    "\n"
    "[BỐI CẢNH] Ngành điện là nguồn phát thải lớn nhất trong EU ETS và gần như không được phân bổ "
    "miễn phí, nên nhu cầu mua EUA của ngành điện lớn hơn hẳn phần lớn ngành khác. Đức là hệ thống "
    "điện lớn nhất EU và có cơ cấu công suất cho phép chuyển đổi than/khí/RES linh hoạt, nên biến "
    "động dispatch tại Đức tạo cú sốc cầu EUA mà thị trường cảm nhận rõ.\n"
    "\n"
    "[CHUỖI]\n"
    "  a) POWER → EUA (kênh hedging): giá điện forward↑ do phải huy động thêm than/gas → utility "
    "khoá biên lợi nhuận bằng cách hedge (mua nhiên liệu + mua EUA tương ứng sản lượng đã bán) → "
    "cầu EUA↑ → EUA↑. Chiều ngược lại: giá điện thấp → giảm động lực hedge, có thể unwind hedge "
    "(bán lại EUA) → cầu EUA yếu đi → EUA↓.\n"
    "  b) EUA → POWER (chiều hay bị bỏ sót): EUA↑ → nhà máy cộng chi phí EUA vào chi phí biên và "
    "đưa vào giá chào bán điện (carbon cost pass-through) → giá điện Đức↑.\n"
    "\n"
    "[BÁC BỎ / CHỐNG ĐẾM TRÙNG] — đây là bước bắt buộc trước khi kết luận:\n"
    "  - Phải xác định giá điện tăng VÌ LÝ DO GÌ trước khi dùng nhánh (a). Nếu giá điện tăng chủ "
    "yếu do EUA đã tăng (nhánh b), thì KHÔNG được quay lại dùng nó làm bằng chứng cho cầu EUA tăng "
    "— đó là lập luận vòng tròn (circular).\n"
    "  - Tương quan lịch sử dương giữa giá điện Đức và EUA (tài liệu tham khảo ghi nhận mức khoảng "
    "75% trên dữ liệu bình quân tháng giai đoạn trước 2020) là TƯƠNG QUAN, không tự động là nhân "
    "quả theo chiều power → EUA; nó phản ánh cả hai chiều cùng lúc.\n"
    "  - Giá điện↑ do RES thấp/căng cung nhiệt điện = tín hiệu EUA MẠNH. Giá điện↑ thuần do "
    "pass-through = KHÔNG phải tín hiệu EUA mới.\n"
    "  - Luôn đọc cùng gas, than và RES: giá điện tăng không luôn đồng nghĩa EUA tăng; tác động "
    "phụ thuộc hệ thống đang chuyển từ gas sang than hay ngược lại.\n"
    "\n"
    "[DỮ LIỆU] Có sẵn: DEBY1, EUA, TTF, API2. Không có sẵn: cơ cấu phát điện thực tế, khối lượng "
    "hedge của utility."
)


# -----------------------------------------------------------------------------
# 1.3 Dầu / Gasoil — 3 kênh, trong đó 1 kênh ngược chiều.
# -----------------------------------------------------------------------------
OIL_GASOIL = (
    "[ID] OIL_GASOIL — Dầu thô và Gasoil tác động EUA qua 3 kênh, trong đó 2 thuận chiều và 1 ngược chiều.\n"
    "[TOPIC] energy_oil.\n"
    "[HORIZON] Tuần → tháng.\n"
    "[ĐỘ MẠNH] Yếu → Trung bình. Đây là kênh GIÁN TIẾP: dầu không trực tiếp quyết định dispatch "
    "điện ở EU. Không dùng dầu làm căn cứ chính nếu không có kênh khác xác nhận.\n"
    "\n"
    "[KÍCH HOẠT] Biến động lớn của Brent/WTI/Gasoil, hoặc crack spread thay đổi rõ rệt, VÀ có dấu "
    "hiệu lan sang giá gas hoặc sang hoạt động công nghiệp EU.\n"
    "\n"
    "[CHUỖI]\n"
    "  (1) THUẬN — kênh liên thông giá nhiên liệu: dầu thường tương quan dương với gas (hợp đồng "
    "LNG chỉ số dầu, thay thế lẫn nhau ở một số khu vực). Nếu dầu↑ kéo TTF↑ đáng kể → nối vào "
    "FUEL_SWITCHING nhánh (1) → dispatch sang than → cầu EUA↑ → EUA↑.\n"
    "      Điều kiện bắt buộc: phải QUAN SÁT ĐƯỢC gas thực sự tăng theo. Nếu dầu tăng mà TTF không "
    "tăng, kênh này KHÔNG kích hoạt.\n"
    "  (2) THUẬN — kênh nhu cầu công nghiệp: crack spread gasoil rộng → nhu cầu diesel↑ (vận tải, "
    "công nghiệp) → hoạt động sản xuất & tiêu thụ điện công nghiệp↑ → phát thải của ngành trong EU "
    "ETS (điện, thép, xi măng, hoá chất, lọc dầu)↑ → cầu EUA↑ → EUA↑.\n"
    "  (3) NGƯỢC — kênh bào mòn biên lợi nhuận: dầu↑ mạnh & kéo dài → chi phí vận tải và sản xuất↑ "
    "→ biên lợi nhuận ngành thâm dụng năng lượng thuộc EU ETS↓ → CÓ THỂ cắt giảm sản lượng/công "
    "suất → phát thải ngành đó↓ → cầu EUA↓ → EUA↓.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - Ba kênh có thể cho tín hiệu trái chiều nhau: PHẢI kiểm tra điều kiện kích hoạt của từng "
    "kênh riêng, không cộng dồn.\n"
    "  - Kênh (3) CHỈ được nêu khi tin tức xác nhận cắt giảm sản lượng THỰC TẾ. Không suy diễn từ "
    "riêng việc giá dầu tăng.\n"
    "  - Phát thải từ giao thông đường bộ KHÔNG thuộc EU ETS1 (thuộc ETS2 với allowance riêng — "
    "xem L9). Vì vậy kênh (2) chỉ hợp lệ khi đi qua HOẠT ĐỘNG CÔNG NGHIỆP/PHÁT ĐIỆN trong ETS1, "
    "không phải qua lượng diesel tiêu thụ cho xe cộ.\n"
    "\n"
    "[DỮ LIỆU] Có sẵn: Brent, WTI, Gasoil, TTF, EUA. Không có sẵn: crack spread tính sẵn, số liệu "
    "sản lượng công nghiệp theo ngành."
)


# -----------------------------------------------------------------------------
# 1.4 Kim loại cơ bản — tín hiệu PHỤ, không phải kênh độc lập.
# -----------------------------------------------------------------------------
METALS = (
    "[ID] METALS — Kim loại cơ bản (nhôm/kẽm) là TÍN HIỆU XÁC NHẬN PHỤ, KHÔNG phải kênh EUA độc lập.\n"
    "[TOPIC] metals, energy_power_eu.\n"
    "[HORIZON] Tuần → quý.\n"
    "[ĐỘ MẠNH] Yếu. Chỉ dùng để CỦNG CỐ một luận điểm đã có, không bao giờ đứng một mình.\n"
    "\n"
    "[KÍCH HOẠT] Tin tức nêu số liệu cụ thể về giá nhôm/kẽm hoặc về việc smelter châu Âu cắt/khôi "
    "phục công suất.\n"
    "\n"
    "[CHUỖI]\n"
    "Gas/điện châu Âu↑ → chi phí smelter nhôm/kẽm (ngành thâm dụng điện, nhiều nhà máy thuộc phạm "
    "vi EU ETS)↑ → cắt giảm công suất smelter → ĐỒNG THỜI hai hệ quả: (i) giá kim loại↑ do nguồn "
    "cung giảm, và (ii) phát thải ngành này↓ → cầu EUA↓.\n"
    "Chiều ngược lại: chi phí năng lượng hạ nhiệt → smelter khởi động lại → phát thải ngành↑ → cầu EUA↑.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - Tín hiệu (ii) là HỆ QUẢ CÙNG GỐC với chuỗi fuel switching (cùng do chi phí năng lượng), "
    "KHÔNG phải một luận điểm EUA mới. Không được đếm thành một kênh xác nhận độc lập khi áp dụng L5.\n"
    "  - Giá kim loại tăng vì lý do khác (cầu Trung Quốc, tồn kho LME, gián đoạn mỏ) KHÔNG liên "
    "quan tới EUA — phải xác định nguyên nhân trước.\n"
    "\n"
    "[DỮ LIỆU] Hệ thống KHÔNG có instrument giá kim loại. Chỉ dùng khi tin tức có số liệu cụ thể."
)


# -----------------------------------------------------------------------------
# 1.5 CBAM & mở rộng phạm vi ETS.
# SỬA LỖI QUAN TRỌNG so với bản gốc: đường bộ và xây dựng thuộc ETS2 (allowance
# riêng), KHÔNG làm tăng cầu EUA của ETS1. Chỉ hàng hải & hàng không mới nằm trong ETS1.
# -----------------------------------------------------------------------------
CBAM_ETS = (
    "[ID] CBAM_ETS — Cơ chế điều chỉnh biên giới carbon (CBAM) và mở rộng phạm vi EU ETS.\n"
    "[TOPIC] cbam, eu_policy, eua_ets.\n"
    "[HORIZON] Quý → nhiều năm (kênh cấu trúc). Chỉ tạo phản ứng ngắn hạn khi có TIN CHÍNH SÁCH MỚI.\n"
    "[ĐỘ MẠNH] Trung bình → Mạnh khi có quyết định lập pháp cụ thể; Yếu khi chỉ là thảo luận.\n"
    "\n"
    "[CHUỖI]\n"
    "  a) EUA → CBAM (chiều hệ quả): EUA↑ → chi phí chứng chỉ CBAM tăng theo (giá CBAM neo vào giá "
    "đấu giá EUA) → nhập khẩu thép/nhôm/xi măng/phân bón vào EU đắt hơn → dịch chuyển cầu sang sản "
    "phẩm phát thải thấp và sang nhà sản xuất nội khối. Đây là HỆ QUẢ của giá EUA, KHÔNG phải "
    "nguyên nhân làm EUA tăng — không dùng chiều này để lập luận về hướng giá EUA.\n"
    "  b) FREE ALLOCATION → EUA (chiều driver, quan trọng nhất): CBAM đi kèm lộ trình cắt giảm dần "
    "phân bổ miễn phí cho chính các ngành được CBAM bảo hộ. Giảm phân bổ miễn phí → doanh nghiệp "
    "phải MUA thêm EUA trên thị trường → cầu EUA↑ → EUA↑. Đẩy nhanh lộ trình cắt giảm → tín hiệu "
    "tăng; trì hoãn/nới lỏng → tín hiệu giảm.\n"
    "  c) MỞ RỘNG PHẠM VI → EUA: đưa thêm ngành vào ETS1 (hàng hải đã được đưa vào theo lộ trình "
    "từ 2024; hàng không thu hẹp dần phần miễn trừ) → thêm đối tượng phải nộp trả EUA → cầu EUA1↑ "
    "→ EUA↑.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU — SỬA LỖI THƯỜNG GẶP]\n"
    "  - GIAO THÔNG ĐƯỜNG BỘ và TOÀ NHÀ/XÂY DỰNG thuộc ETS2 — một hệ thống RIÊNG BIỆT với "
    "allowance riêng, cap riêng, cơ chế ổn định giá riêng. Tin tức về ETS2 KHÔNG được suy ra trực "
    "tiếp thành 'cầu EUA (ETS1)↑'. Nếu bản tin nói về đường bộ/xây dựng, phải nêu rõ đó là ETS2 và "
    "tác động lên EUA1 (nếu có) chỉ là gián tiếp qua kỳ vọng chính sách khí hậu chung.\n"
    "  - Không suy diễn tác động số lượng cụ thể nếu tin không nêu khối lượng allowance liên quan.\n"
    "  - Các mốc thời gian và tham số lộ trình (năm bắt đầu, tỷ lệ cắt giảm free allocation) có thể "
    "đã thay đổi — nếu bản tin nêu mốc khác với hiểu biết sẵn có, ƯU TIÊN số liệu trong bản tin.\n"
    "\n"
    "[DỮ LIỆU] Có sẵn: EUA. Không có sẵn: khối lượng free allocation theo ngành, giá chứng chỉ CBAM."
)


# -----------------------------------------------------------------------------
# 1.6 Cơ chế LÕI của thị trường: cap, MSR, đấu giá, free allocation, compliance.
# -----------------------------------------------------------------------------
POLICY_MSR = (
    "[ID] POLICY_MSR — Cơ chế LÕI của chính thị trường EU ETS: cap/lộ trình giảm, Market Stability "
    "Reserve (MSR), lịch đấu giá, phân bổ miễn phí, chu kỳ compliance. Đây là nhóm đòn bẩy tác "
    "động CUNG/CẦU EUA TRỰC TIẾP nhất, không đi qua trung gian ngành khác.\n"
    "[TOPIC] eua_ets, eu_policy.\n"
    "[HORIZON] Toàn dải: tin bất ngờ tác động trong ngày; thiết kế thị trường tác động nhiều năm.\n"
    "[ĐỘ MẠNH] Mạnh nhất trong khung — theo CONFLICT_RESOLUTION, kênh cung chính sách ghi đè kênh "
    "cầu khi hai bên cùng độ tin cậy.\n"
    "\n"
    "[CHUỖI — NHÓM CUNG]\n"
    "  (1) Cap & hệ số giảm tuyến tính (LRF): siết chặt hơn (giảm nhanh hơn) → kỳ vọng thiếu hụt "
    "allowance tương lai → cầu/giá kỳ hạn↑ → EUA↑. Giãn/nới lộ trình → kỳ vọng dư cung → EUA↓.\n"
    "  (2) MSR: MSR hấp thụ allowance dư thừa khi lượng allowance lưu hành (TNAC) vượt ngưỡng trên, "
    "và giải phóng khi xuống dưới ngưỡng dưới. Tăng tỷ lệ HẤP THỤ (intake rate) hoặc siết ngưỡng → "
    "cung EUA↓ → EUA↑. Giảm tỷ lệ hấp thụ / giải phóng allowance từ MSR → cung EUA↑ → EUA↓.\n"
    "      (Lưu ý thuật ngữ: đây là 'tỷ lệ hấp thụ/rút khỏi lịch đấu giá', không phải 'tỷ lệ rút vốn'.)\n"
    "  (3) Công bố TNAC hàng năm là SỰ KIỆN GIAO DỊCH ĐƯỢC: con số này quyết định lượng MSR hấp thụ "
    "trong 12 tháng kế tiếp, nên TNAC lệch so với dự báo thị trường → điều chỉnh kỳ vọng cung → "
    "phản ứng giá ngay khi công bố.\n"
    "  (4) Lịch & khối lượng đấu giá: khối lượng dồn/tăng trong tháng-quý → cung ngắn hạn↑ → áp lực "
    "GIẢM giá. Hoãn phiên, huỷ phiên, rút EUA khỏi lịch đấu giá → cung ngắn hạn↓ → áp lực TĂNG giá. "
    "Kết quả đấu giá yếu (tỷ lệ đăng ký thấp, giá thanh toán chiết khấu sâu so với thị trường thứ "
    "cấp) → tín hiệu cầu yếu → áp lực giảm; và ngược lại.\n"
    "  (5) Giảm tỷ lệ phân bổ miễn phí → doanh nghiệp phải mua thêm EUA → cầu↑ → EUA↑.\n"
    "\n"
    "[CHUỖI — NHÓM CẦU & KỲ VỌNG]\n"
    "  (6) Chu kỳ compliance (nộp trả EUA hàng năm cho phát thải năm trước): nhu cầu mua gom tăng "
    "trước hạn → áp lực tăng giá ngắn hạn quanh mốc deadline. LƯU Ý theo L4: lịch này CỐ ĐỊNH và "
    "ai cũng biết trước nên phần lớn đã nằm trong giá — chỉ coi là tín hiệu khi có bằng chứng vị "
    "thế compliance năm nay khác thường (hedge thiếu, mua muộn).\n"
    "  (7) Công bố dữ liệu phát thải đã kiểm chứng (verified emissions) / báo cáo cơ quan quản lý "
    "→ so với dự báo, cho biết thị trường đang thắt chặt hay nới lỏng hơn kỳ vọng → điều chỉnh giá "
    "theo đúng chiều BẤT NGỜ (không theo chiều mức tuyệt đối).\n"
    "  (8) Kỳ vọng chính sách tương lai: giá EUA phản ánh cả dự đoán về thay đổi chính sách sắp "
    "tới. Tin về đề xuất sửa luật, bỏ phiếu, phán quyết toà án, hay tranh chấp pháp lý có thể làm "
    "giá phản ứng NGAY khi công bố, không cần đợi chính sách có hiệu lực. Trì hoãn hoặc bất định "
    "pháp lý tự nó cũng là nguồn biến động.\n"
    "  (9) Cơ chế can thiệp giá & mean-reversion: khi giá tăng quá mạnh/quá nhanh, có thể kích "
    "hoạt (i) phản ứng tự điều chỉnh của thị trường (chốt lời, bán kỹ thuật) và (ii) điều khoản "
    "can thiệp cho phép giải phóng thêm allowance từ MSR khi giá vượt ngưỡng trong một khoảng thời "
    "gian nhất định → áp lực điều chỉnh giảm ngắn hạn dù cung/cầu nền tảng chưa đổi.\n"
    "      CHỈ nêu khi biến động thực sự bất thường về biên độ/tốc độ; KHÔNG áp dụng cho biến động "
    "thông thường, và KHÔNG khẳng định ngưỡng/điều kiện kích hoạt cụ thể trừ khi bản tin nêu rõ.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - Áp dụng L4 nghiêm ngặt: lộ trình cap và lịch đấu giá đã công bố KHÔNG phải tin mới. Chỉ "
    "phần THAY ĐỔI so với kế hoạch mới tạo tín hiệu.\n"
    "  - Phân biệt đề xuất (proposal) — thoả thuận chính trị — văn bản có hiệu lực. Mức độ phản ứng "
    "giá tỷ lệ với xác suất và độ gần của việc thực thi; một đề xuất sơ bộ không tương đương luật đã thông qua.\n"
    "  - Không khẳng định các tham số định lượng (tỷ lệ intake, ngưỡng TNAC, ngày deadline, năm bắt "
    "đầu) từ trí nhớ — các tham số này đã được sửa đổi nhiều lần. Nếu bản tin nêu số, dùng số của "
    "bản tin; nếu không, mô tả định tính theo chiều tác động.\n"
    "\n"
    "[DỮ LIỆU] Có sẵn: EUA. Không có sẵn: TNAC, khối lượng/kết quả từng phiên đấu giá, dữ liệu "
    "registry — chỉ dùng khi tin tức nêu."
)


# -----------------------------------------------------------------------------
# 1.7 Địa chính trị — 2 kênh tách biệt.
# -----------------------------------------------------------------------------
GEOPOLITICS_SUPPLY_CHAIN = (
    "[ID] GEOPOLITICS_SUPPLY_CHAIN — Địa chính trị tác động EUA qua 2 KÊNH TÁCH BIỆT. Phải xác "
    "định sự kiện thuộc kênh nào TRƯỚC khi suy luận; KHÔNG áp cả 2 kênh cùng lúc cho 1 sự kiện.\n"
    "[TOPIC] geopolitics, energy_gas, energy_oil, eu_policy.\n"
    "[HORIZON] Kênh (a): ngày → tháng. Kênh (b): tháng → nhiều năm.\n"
    "[ĐỘ MẠNH] Kênh (a) có thể Mạnh khi gián đoạn được xác nhận; kênh (b) thường Yếu → Trung bình.\n"
    "\n"
    "[CHUỖI]\n"
    "  a) KÊNH CUNG NHIÊN LIỆU VẬT LÝ (xung đột, cấm vận, gián đoạn hạ tầng, rủi ro tuyến vận tải):\n"
    "     BẮT BUỘC đi qua đủ 2 bước trung gian trước khi nối vào FUEL_SWITCHING:\n"
    "       bước 1 — tác động tới CUNG nhiên liệu (thực tế hoặc rủi ro);\n"
    "       bước 2 — tác động tới GIÁ gas (TTF) / dầu (Brent, WTI).\n"
    "     Chuỗi đầy đủ: xung đột/cấm vận → cung gas-dầu↓ (hoặc rủi ro cung↑) → giá gas/dầu↑ → hệ "
    "thống điện tăng huy động nguồn phát thải cao (than) để bù → phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "     Chiều ngược: căng thẳng hạ nhiệt, nguồn cung nối lại → giá gas/dầu↓ → giảm áp lực chuyển "
    "sang than → cầu EUA↓ → EUA↓.\n"
    "     Lỗi hay gặp nhất: nhảy thẳng từ 'có xung đột' sang 'EUA tăng' mà bỏ qua 2 bước trên. "
    "Nếu giá gas KHÔNG thực sự phản ứng, kênh này KHÔNG kích hoạt dù tin tức nghe nghiêm trọng.\n"
    "\n"
    "  b) KÊNH KỲ VỌNG CHÍNH SÁCH / THƯƠNG MẠI (thay đổi chính phủ, định hướng chính sách khí hậu, "
    "quyết định thương mại quốc tế — KHÔNG phải gián đoạn vật lý, nên KHÔNG bắt buộc đi qua bước "
    "cung/giá nhiên liệu ở nhánh a):\n"
    "     Tác động gián tiếp qua kỳ vọng thị trường và triển vọng sản xuất công nghiệp. Ví dụ: "
    "chính phủ mới nới lỏng mục tiêu khí hậu → hạ kỳ vọng độ chặt của cap tương lai → cầu EUA dài "
    "hạn↓ → EUA↓. Căng thẳng thương mại leo thang → lo ngại sản xuất công nghiệp EU chậm lại → kỳ "
    "vọng phát thải↓ → cầu EUA↓. Chiều ngược lại nếu chính sách siết chặt hơn hoặc thương mại cải thiện.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - CHỈ suy luận khi tin tức có SỰ KIỆN CỤ THỂ với tác động nêu rõ. Tuyệt đối không suy ra "
    "hướng giá EUA từ một tin chính trị chung chung.\n"
    "  - Không dùng đồng thời cả hai kênh cho một sự kiện để 'khuếch đại' kết luận.\n"
    "  - Rủi ro địa chính trị thường được định giá TRƯỚC (risk premium): nếu thị trường đã phản ứng "
    "từ các phiên trước, tin nhắc lại KHÔNG phải tín hiệu mới (L4).\n"
    "\n"
    "[DỮ LIỆU] Có sẵn: TTF, Brent, WTI, EUA, DEBY1 — dùng chính các chuỗi giá này để KIỂM CHỨNG "
    "xem kênh (a) có thực sự kích hoạt hay không."
)


# -----------------------------------------------------------------------------
# 1.8 Tài chính & đầu cơ (bản tổng quát) — "van an toàn".
# -----------------------------------------------------------------------------
FINANCE_SPECULATION = (
    "[ID] FINANCE_SPECULATION — EUA là tài sản tài chính có thanh khoản riêng; dòng vốn và đầu cơ "
    "có thể tạo biến động giá KHÔNG dựa trên cung/cầu thực. Đây là 'van an toàn' bắt buộc đọc "
    "TRƯỚC khi kết luận một biến động giá là xu hướng nền tảng.\n"
    "[TOPIC] eua_ets, market_structure. (Bản chi tiết kỹ thuật: POSITIONING_TECHNICALS.)\n"
    "[HORIZON] Trong phiên → vài tuần.\n"
    "[ĐỘ MẠNH] Dùng như BỘ LỌC hạ cấp độ tin cậy của các kết luận khác, hơn là một luận điểm độc lập.\n"
    "\n"
    "[CHUỖI]\n"
    "Dòng vốn đầu tư/đầu cơ, mức thanh khoản và kỳ vọng thị trường → biến động giá EUA ngắn hạn "
    "theo cả hai chiều, độc lập với phát thải thực tế.\n"
    "\n"
    "[QUY TẮC SỬ DỤNG]\n"
    "  - Biến động do dòng vốn/đầu cơ thường CHỈ TẠM THỜI và không bền vững nếu KHÔNG đi kèm thay "
    "đổi ở yếu tố cơ bản (cung/cầu allowance, giá năng lượng tương đối, chính sách).\n"
    "  - Khi một biến động giá KHÔNG giải thích được bằng bất kỳ cơ chế nền tảng nào trong khung "
    "này, kết luận đúng là 'biến động chưa có nguyên nhân nền tảng rõ ràng, nhiều khả năng do dòng "
    "tiền/kỹ thuật' — KHÔNG phải đi tìm một câu chuyện fundamentals cho vừa với biến động đó "
    "(narrative fitting).\n"
    "\n"
    "[DỮ LIỆU] Không có dữ liệu dòng vốn. Không định lượng kênh này."
)


# -----------------------------------------------------------------------------
# 1.9 Positioning & kỹ thuật (bản chi tiết của 1.8).
# -----------------------------------------------------------------------------
POSITIONING_TECHNICALS = (
    "[ID] POSITIONING_TECHNICALS — Phiên bản CHI TIẾT/KỸ THUẬT của FINANCE_SPECULATION: các chỉ báo "
    "positioning cụ thể giúp PHÂN BIỆT biến động do dòng tiền mới (xu hướng thật) với biến động do "
    "short-covering/roll kỳ hạn (nhiễu kỹ thuật).\n"
    "[TOPIC] eua_ets, market_structure.\n"
    "[HORIZON] Trong phiên → vài tuần.\n"
    "[ĐỘ MẠNH] Chỉ dùng khi bản tin có SỐ LIỆU; nếu không, dùng bản tổng quát FINANCE_SPECULATION.\n"
    "\n"
    "[CHUỖI]\n"
    "  - Short covering, kích hoạt stop-loss, đáo hạn quyền chọn, roll kỳ hạn, dòng tiền quỹ/CTA, "
    "hoặc thanh khoản mỏng → EUA có thể tăng/giảm mạnh dù fundamentals chưa đổi.\n"
    "  - Open interest↑ đi kèm giá↑ và volume↑ → xác nhận DÒNG TIỀN MỚI vào vị thế mua (tín hiệu "
    "xu hướng đáng tin hơn).\n"
    "  - Giá↑ nhưng open interest↓ → nhiều khả năng chỉ là ĐÓNG VỊ THẾ BÁN (short covering), không "
    "phải xu hướng mới.\n"
    "  - Cần đọc kèm volume, calendar spread, options skew và dữ liệu COT/positioning nếu có.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - KHÔNG gọi một biến động là 'xu hướng tăng/giảm nền tảng' nếu không có xác nhận từ ít nhất "
    "một trong các nhóm: điện, nhiên liệu, đấu giá, chính sách, compliance.\n"
    "  - Hệ thống KHÔNG theo dõi open interest / volume / options skew / COT. CHỈ nêu các liên kết "
    "này khi tin tức trích dẫn có SỐ LIỆU CỤ THỂ. TUYỆT ĐỐI KHÔNG tự bịa số liệu positioning.\n"
    "\n"
    "[DỮ LIỆU] Không có sẵn bất kỳ chỉ báo positioning nào. Phụ thuộc hoàn toàn vào nội dung bản tin."
)


# -----------------------------------------------------------------------------
# 1.10 Vĩ mô — thu hẹp lại đúng phần có cơ sở nhân quả.
# -----------------------------------------------------------------------------
MACRO = (
    "[ID] MACRO — Kênh vĩ mô & chu kỳ kinh tế. Tác động GIÁN TIẾP và đang suy yếu dần theo thời "
    "gian do chuyển dịch năng lượng và cải thiện hiệu quả sử dụng năng lượng.\n"
    "[TOPIC] macro, eua_ets.\n"
    "[HORIZON] Quý → năm. KHÔNG dùng để giải thích biến động ngày/tuần.\n"
    "[ĐỘ MẠNH] Yếu. Không dùng làm căn cứ chính nếu thiếu số liệu vĩ mô cụ thể.\n"
    "\n"
    "[CHUỖI]\n"
    "  (1) Hoạt động kinh tế: GDP / sản xuất công nghiệp↑ → nhu cầu điện và sản lượng ngành thâm "
    "dụng phát thải trong ETS (thép, xi măng, hoá chất)↑ → phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "  (2) Suy thoái → sản lượng công nghiệp & nhu cầu điện↓ → phát thải↓ → cầu EUA↓ → EUA↓.\n"
    "  (3) Chi phí nắm giữ (cost of carry) — kênh lãi suất CÓ cơ sở nhân quả rõ nhất: EUA có thể "
    "được tích trữ (banking) qua các năm, nên giá phản ánh kỳ vọng khan hiếm dài hạn chiết khấu về "
    "hiện tại. Lãi suất↑ → chi phí nắm giữ/tài trợ vị thế hedge dài hạn↑ → giảm động lực tích trữ "
    "sớm → áp lực giảm lên giá giao ngay và làm dốc hơn cấu trúc kỳ hạn (xem TERM_STRUCTURE_CARRY).\n"
    "  (4) Kênh USD/khẩu vị rủi ro chung: USD↑ / lãi suất thực↑ thường tạo áp lực giảm đồng thời "
    "lên hàng hoá (vàng, dầu, kim loại) và CÓ THỂ lan sang EUA qua khẩu vị rủi ro. Đây là kênh "
    "TƯƠNG QUAN, không phải nhân quả trực tiếp — độ tin cậy thấp nhất trong nhóm.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - Hiệu ứng (1)/(2) ĐANG SUY YẾU: bằng chứng là GDP EU tiếp tục tăng trong khi phát thải khí "
    "nhà kính đi ngang/giảm — tăng trưởng kinh tế KHÔNG mặc định kéo EUA tăng mạnh. Luôn nêu kèm "
    "giới hạn này khi dùng kênh vĩ mô.\n"
    "  - Kênh (4) CHỈ nêu khi có số liệu cụ thể (DXY, lợi suất trái phiếu) trong bản tin; không "
    "dùng làm luận điểm chính.\n"
    "  - Không dùng số liệu vĩ mô công bố định kỳ đã được dự báo rộng rãi làm tín hiệu, trừ phần "
    "lệch so với đồng thuận (L4).\n"
    "\n"
    "[DỮ LIỆU] Không có sẵn số liệu vĩ mô. Chỉ dùng khi bản tin nêu."
)


# -----------------------------------------------------------------------------
# 1.11 MỚI — Cấu trúc kỳ hạn & carry. Bổ sung để giải thích nhóm biến động mà
# bản gốc không có cơ chế nào phủ: chênh lệch giữa các kỳ hạn và hành vi banking.
# -----------------------------------------------------------------------------
TERM_STRUCTURE_CARRY = (
    "[ID] TERM_STRUCTURE_CARRY — Cấu trúc kỳ hạn EUA và hành vi tích trữ (banking). Giải thích vì "
    "sao EUA hành xử như một TÀI SẢN CÓ THỂ TÍCH TRỮ chứ không như hàng hoá tiêu hao theo phiên.\n"
    "[TOPIC] eua_ets, market_structure, macro.\n"
    "[HORIZON] Tuần → năm.\n"
    "[ĐỘ MẠNH] Trung bình khi có dữ liệu spread kỳ hạn; Yếu khi chỉ suy luận định tính.\n"
    "\n"
    "[CHUỖI]\n"
    "  - EUA được phép chuyển tiếp (banking) sang các năm sau, nên người nắm giữ so sánh giữa mua "
    "ngay và mua sau. Hệ quả: giá kỳ hạn gần thường neo vào kỳ vọng khan hiếm DÀI HẠN cộng chi phí "
    "nắm giữ, chứ không chỉ phản ánh cân bằng cung/cầu của riêng năm hiện tại.\n"
    "  - Vì có banking, một cú sốc cầu ngắn hạn (một tháng phát thải cao) tác động lên giá NHỎ hơn "
    "nhiều so với một thay đổi kỳ vọng về cap/MSR dài hạn. Đây là lý do kênh chính sách thường "
    "thắng kênh thời tiết khi hai bên trái chiều.\n"
    "  - Lãi suất / chi phí tài trợ↑ → chi phí carry↑ → giảm động lực nắm giữ tồn kho EUA → áp lực "
    "giảm ngắn hạn và cấu trúc kỳ hạn dốc lên hơn (contango rộng hơn).\n"
    "  - Contango thu hẹp bất thường hoặc chuyển sang backwardation → tín hiệu THẮT CHẶT ở kỳ hạn "
    "gần (nhu cầu compliance gấp, thiếu allowance sẵn có).\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - Không suy ra tín hiệu chỉ từ biến động giá của một kỳ hạn duy nhất mà không so sánh giữa "
    "các kỳ hạn.\n"
    "  - Nếu bản tin không nêu chênh lệch kỳ hạn, dùng cơ chế này ở mức ĐỊNH TÍNH (giải thích vì "
    "sao tin dài hạn quan trọng hơn tin ngắn hạn), không đưa ra kết luận giá.\n"
    "\n"
    "[DỮ LIỆU] Hệ thống có giá EUA nhưng KHÔNG có sẵn spread giữa các kỳ hạn. Chỉ định lượng khi "
    "bản tin nêu."
)


# -----------------------------------------------------------------------------
# 1.12 Thời tiết & hệ thống điện — biến XÁC NHẬN bắt buộc.
# -----------------------------------------------------------------------------
WEATHER_POWER_SYSTEM = (
    "[ID] WEATHER_POWER_SYSTEM — Thời tiết và tình trạng hệ thống điện là BIẾN XÁC NHẬN BẮT BUỘC "
    "cho mọi luận điểm dựa trên điện/RES. Mở rộng và chi tiết hoá nhánh RES/thời tiết của "
    "FUEL_SWITCHING: cơ cấu phát điện thực tế không chỉ do gió/mặt trời quyết định.\n"
    "[TOPIC] energy_power_eu, energy_renewable, weather.\n"
    "[HORIZON] Ngày → mùa.\n"
    "[ĐỘ MẠNH] Mạnh khi là điều kiện cực đoan kéo dài; Yếu với biến động thời tiết thông thường.\n"
    "\n"
    "[CHUỖI]\n"
    "  - Gió thấp / bức xạ mặt trời thấp / thuỷ điện suy giảm (hạn hán, mực nước hồ thấp) / sự cố "
    "hoặc bảo dưỡng hạt nhân / interconnector hạn chế → phát điện nhiệt↑ → phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "  - Gió cao / nắng nhiều / thuỷ điện phục hồi / hạt nhân khả dụng cao / nhập khẩu điện tăng → "
    "phát điện nhiệt↓ → phát thải↓ → cầu EUA↓ → EUA↓.\n"
    "  - Nhiệt độ cực đoan (rét đậm → sưởi; nắng nóng → làm mát) làm phụ tải điện↑; tác động EUA "
    "CHỈ mạnh khi phần phụ tải tăng thêm được đáp ứng bằng nguồn phát điện thuộc EU ETS.\n"
    "  - Xu hướng dài hạn: tỷ trọng điện gió và mặt trời trong cơ cấu sản xuất điện EU đã tăng mạnh "
    "trong khi tỷ trọng than và khí giảm → nền phát thải ngành điện có xu hướng giảm cấu trúc → "
    "làm YẾU dần biên độ của các cú sốc cầu EUA từ phía điện qua thời gian.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - PHẢI phân biệt giá điện↑ do căng cung/huy động nhiệt điện (tín hiệu EUA mạnh) với giá "
    "điện↑ chỉ do EUA pass-through (KHÔNG phải tín hiệu EUA mới — xem POWER_EUA_TWO_WAY nhánh b).\n"
    "  - Rét đậm làm tăng nhu cầu sưởi bằng KHÍ ĐỐT trực tiếp tại hộ gia đình: phần này KHÔNG thuộc "
    "EU ETS1 (xem L9) — nó tác động EUA gián tiếp qua việc kéo giá TTF lên (nối vào FUEL_SWITCHING), "
    "chứ không trực tiếp tạo cầu EUA.\n"
    "  - Dự báo thời tiết thay đổi liên tục: một bản dự báo đơn lẻ không đủ để kết luận xu hướng (L8).\n"
    "\n"
    "[DỮ LIỆU] Hệ thống KHÔNG có dữ liệu thời tiết, sản lượng RES, hay tình trạng khả dụng nhà máy. "
    "Chỉ dùng khi bản tin nêu."
)


# -----------------------------------------------------------------------------
# 1.13 Kinh tế nhiên liệu tương đối — bản refinement của FUEL_SWITCHING.
# -----------------------------------------------------------------------------
RELATIVE_FUEL_ECONOMICS = (
    "[ID] RELATIVE_FUEL_ECONOMICS — Bản REFINEMENT (nâng độ chính xác) của FUEL_SWITCHING: thay vì "
    "kết luận từ giá tuyệt đối gas/than, dùng chênh lệch chi phí phát điện SAU carbon. Phản ánh "
    "đúng động lực kinh tế thực của utility khi chọn nhiên liệu.\n"
    "[TOPIC] energy_gas, energy_coal, energy_power_eu.\n"
    "[HORIZON] Ngày → tháng.\n"
    "[ĐỘ MẠNH] Mạnh nhất trong nhóm nhiên liệu KHI có đủ dữ liệu spread; nếu không có, fallback về "
    "FUEL_SWITCHING (vẫn hợp lệ, chỉ là tín hiệu yếu hơn).\n"
    "\n"
    "[CHUỖI]\n"
    "  - Các đại lượng chuẩn: Clean Dark Spread (CDS — biên lợi nhuận phát điện than sau chi phí "
    "carbon), Clean Spark Spread (CSS — tương ứng cho khí), carbon switching price (mức giá EUA "
    "làm CDS = CSS), và merit order.\n"
    "  - CDS cải thiện tương đối so với CSS → than cạnh tranh hơn khí → coal burn↑ → phát thải/MWh↑ "
    "→ utility hedge & cầu EUA↑ → EUA↑.\n"
    "  - CSS cải thiện tương đối so với CDS → khí cạnh tranh hơn than → phát thải/MWh↓ → cầu EUA↓ → EUA↓.\n"
    "  - Vòng phản hồi TỰ ỔN ĐỊNH (quan trọng, hay bị bỏ sót): EUA↑ làm chi phí carbon của than "
    "tăng nhanh hơn của khí (than phát thải nhiều hơn/MWh) → CDS xấu đi tương đối so với CSS → "
    "dispatch quay lại khí → phát thải↓ → cầu EUA↓. Tức chính đà tăng của EUA tạo ra lực hãm nội "
    "sinh; đây là lý do các cú tăng do fuel switching thường có giới hạn trên tự nhiên.\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - Chỉ coi fuel switching là tín hiệu MẠNH khi spread, dữ liệu phát điện và điều kiện hệ "
    "thống điện CÙNG xác nhận; không suy ra chỉ từ TTF↑ hoặc API2↑ đơn lẻ.\n"
    "  - Vùng chuyển đổi có giới hạn: khi giá EUA hoặc chênh lệch nhiên liệu ra ngoài vùng này, "
    "thay đổi thêm KHÔNG tạo thêm chuyển dịch dispatch.\n"
    "\n"
    "[DỮ LIỆU] Hệ thống KHÔNG tính sẵn CDS/CSS/switching price (không có instrument riêng). CHỈ áp "
    "dụng cơ chế spread khi bản tin nêu rõ số liệu spread/generation; nếu không, dùng chuỗi giá "
    "tuyệt đối ở FUEL_SWITCHING."
)


# -----------------------------------------------------------------------------
# 1.14 Hydrogen & decarbonization — kênh dài hạn duy nhất.
# -----------------------------------------------------------------------------
HYDROGEN_DECARBONIZATION = (
    "[ID] HYDROGEN_DECARBONIZATION — Kênh DÀI HẠN/CHẬM duy nhất trong khung. Khác hẳn các chuỗi "
    "khác (vốn là tín hiệu ngắn hạn/theo phiên), hydrogen chỉ tác động EUA qua quá trình "
    "decarbonization công nghiệp diễn ra trong nhiều năm.\n"
    "[TOPIC] energy_hydrogen. GHI CHÚ TÍCH HỢP: topic này bị loại khỏi mục 'tín hiệu liên thị "
    "trường theo phiên' — chỉ xuất hiện ở phần driver tổng quan, KHÔNG dùng giải thích biến động "
    "giá EUA theo ngày/tuần.\n"
    "[HORIZON] Nhiều năm.\n"
    "[ĐỘ MẠNH] Yếu về mặt tín hiệu giao dịch; Trung bình về mặt định hình kỳ vọng cấu trúc.\n"
    "\n"
    "[CHUỖI]\n"
    "  - Hydrogen xanh thay thế than cốc/khí trong luyện thép và hoá chất (ngành thuộc EU ETS) → "
    "dự án/công suất H2 xanh mở rộng → phát thải trực tiếp ngành đó giảm dần theo thời gian → cầu "
    "EUA dài hạn↓ (hiệu ứng CHẬM, chỉ rõ nét sau nhiều năm).\n"
    "  - Chính sách trợ cấp/hạ tầng hydrogen → tín hiệu decarbonization mạnh hơn dự kiến → có thể "
    "củng cố kỳ vọng cap/MSR siết chặt hơn trong tương lai (nối sang POLICY_MSR) → hỗ trợ nhẹ cho "
    "EUA kỳ hạn dài. Lưu ý đây là chiều TĂNG, ngược với chiều giảm ở trên — hai hiệu ứng này bù trừ "
    "nhau và không nên kết luận ròng nếu tin không nêu rõ.\n"
    "  - Điện phân quy mô lớn → nhu cầu điện tăng thêm → nếu đáp ứng bằng nguồn phát thải cao → "
    "phát thải↑ → cầu EUA↑ (hiệu ứng nhỏ, quy mô electrolysis ở EU hiện còn thấp).\n"
    "\n"
    "[BÁC BỎ / VÔ HIỆU]\n"
    "  - TUYỆT ĐỐI KHÔNG dùng tin hydrogen để kết luận biến động giá EUA trong ngày/tuần, trừ khi "
    "tin nêu rõ một sự kiện/chính sách có tác động tức thời (vd thay đổi cap hoặc quy định liên "
    "quan trực tiếp). Mặc định đây là driver cấu trúc dài hạn, không phải tín hiệu giao dịch.\n"
    "  - Thông báo dự án hydrogen (MOU, kế hoạch, ý định đầu tư) có tỷ lệ chậm tiến độ/huỷ bỏ cao "
    "→ chiết khấu mạnh khi đánh giá tác động.\n"
    "\n"
    "[DỮ LIỆU] Không có instrument liên quan. Chỉ dùng khi bản tin nêu."
)


# =============================================================================
# PHẦN 2 — ĐĂNG KÝ CƠ CHẾ THEO TOPIC & KHUNG THỜI GIAN
# (Chưa dùng để tiêm prompt động — hiện report_generator.py/quote_chat.py vẫn
# tiêm TĨNH toàn bộ các hằng số ở trên. Registry + build_context() dưới đây
# sẵn sàng cho một lần tối ưu sau: tiêm prompt ĐỘNG theo đúng topic ngày đó
# thay vì luôn nạp toàn bộ khung — xem ghi chú ở report_generator.py.)
# =============================================================================

# CHỈ chứa 13 topic THẬT của hệ thống (khớp NewsTopic trong
# crawl_news/classification.py) — không dùng pseudo-topic (vd "metals",
# "weather", "macro", "market_structure") làm khoá nữa: pseudo-topic không
# bao giờ khớp topic thật của 1 bài báo nên trước đây METALS/MACRO/
# WEATHER_POWER_SYSTEM/TERM_STRUCTURE_CARRY/POSITIONING_TECHNICALS/
# FINANCE_SPECULATION gắn qua các khoá đó sẽ KHÔNG BAO GIỜ được build_context()
# nạp — regression âm thầm nếu dùng registry để tiêm động. Các cơ chế không
# gắn được với 1 topic cụ thể (cross-cutting) được xử lý riêng bên dưới qua
# SUPPLEMENTARY_SIGNALS/MANDATORY_CONFIRMERS/FILTERS thay vì qua registry.
MECHANISM_REGISTRY = {
    "energy_gas":        [FUEL_SWITCHING, RELATIVE_FUEL_ECONOMICS, GEOPOLITICS_SUPPLY_CHAIN],
    "energy_coal":       [FUEL_SWITCHING, RELATIVE_FUEL_ECONOMICS],
    "energy_oil":        [OIL_GASOIL, FUEL_SWITCHING, GEOPOLITICS_SUPPLY_CHAIN],
    "energy_power_eu":   [POWER_EUA_TWO_WAY, FUEL_SWITCHING, WEATHER_POWER_SYSTEM,
                          RELATIVE_FUEL_ECONOMICS],
    "energy_renewable":  [WEATHER_POWER_SYSTEM, FUEL_SWITCHING],
    "eua_ets":           [POLICY_MSR, FUEL_SWITCHING, TERM_STRUCTURE_CARRY,
                          FINANCE_SPECULATION, POSITIONING_TECHNICALS],
    "eu_policy":         [POLICY_MSR, CBAM_ETS, GEOPOLITICS_SUPPLY_CHAIN],
    "cbam":              [CBAM_ETS, POLICY_MSR],
    "geopolitics":       [GEOPOLITICS_SUPPLY_CHAIN, FUEL_SWITCHING],
    "energy_hydrogen":   [HYDROGEN_DECARBONIZATION],
    # 3 topic dưới đây CHỦ ĐÍCH map về [] — KHÔNG phải thiếu sót. Xem "PHẠM VI
    # TOPIC" ở docstring đầu file và luật L11: các thị trường này không
    # fungible với EUA nên không có cơ chế nào trong PHẦN 1 áp dụng cho chúng
    # — build_context() bù lại bằng NON_EUA_CARBON_MARKETS (xem bên dưới).
    "vcm":                [],
    "global_carbon_market": [],
    "vietnam_carbon_policy": [],
}

# Cơ chế KHÔNG được dùng để giải thích biến động giá trong ngày/tuần — hiện
# chỉ HYDROGEN_DECARBONIZATION còn nằm trong registry theo topic thật
# ("energy_hydrogen"); MACRO đã chuyển sang SUPPLEMENTARY_SIGNALS (luôn nạp,
# không qua registry) nên không cần liệt kê ở đây nữa.
LONG_HORIZON_ONLY = [HYDROGEN_DECARBONIZATION]

# Cơ chế luôn phải đọc kèm khi có bất kỳ luận điểm nào về điện/RES.
MANDATORY_CONFIRMERS = [WEATHER_POWER_SYSTEM]

# Cơ chế đóng vai trò BỘ LỌC: đọc sau cùng để hạ cấp độ tin cậy nếu cần.
FILTERS = [FINANCE_SPECULATION, POSITIONING_TECHNICALS]

# Tín hiệu PHỤ, cross-cutting — không gắn được với đúng 1 topic thật nào (kim
# loại/macro có thể xuất hiện trong tin thuộc BẤT KỲ topic năng lượng/chính
# sách nào) nên KHÔNG dùng MECHANISM_REGISTRY để gate theo topic — luôn nạp
# (tương tự FILTERS), phần [BÁC BỎ]/[ĐỘ MẠNH] của chính 2 cơ chế này đã tự
# giới hạn việc dùng khi thiếu số liệu, nên chi phí token thấp mà không mất
# tín hiệu khi tin thực sự có.
SUPPLEMENTARY_SIGNALS = [METALS, MACRO]

# 3 topic không fungible với EUA (xem MECHANISM_REGISTRY) — khi 1 trong 3 topic
# này thực sự có tin trong ngày, build_context() tiêm thêm NON_EUA_CARBON_MARKETS
# NGAY TẠI ĐIỂM ÁP DỤNG (không chỉ trông chờ L11 ở đầu prompt) — đây chính là
# kẽ hở từng gây lỗi thực tế (1 báo cáo viết "Kết luận: trung lập" cho tin
# CORSIA thay vì bỏ hẳn khỏi phân tích).
_NON_EUA_TOPICS = {"vcm", "global_carbon_market", "vietnam_carbon_policy"}


def build_context(topics, horizon="short"):
    """
    Ghép phần tri thức cần đưa vào prompt cho một tập topic.

    topics  : iterable tên topic (khớp khoá của MECHANISM_REGISTRY) — CHỈ nên
              truyền topic THỰC SỰ có tin trong ngày/phiên đang xử lý (không
              phải toàn bộ 13 topic khả dĩ), để tối ưu token đúng mục đích.
    horizon : "short" (ngày–tuần) | bất kỳ giá trị khác (vd "medium") sẽ KHÔNG
              loại HYDROGEN_DECARBONIZATION dù topic "energy_hydrogen" có tin.

    Luôn đặt INFERENCE_RULES lên đầu và CONFLICT_RESOLUTION xuống cuối, để model
    đọc luật trước khi đọc cơ chế, và đọc luật phân xử ngay trước khi kết luận.
    """
    topics = list(topics)
    blocks, seen = [], set()
    for topic in topics:
        for mech in MECHANISM_REGISTRY.get(topic, []):
            if horizon == "short" and mech in LONG_HORIZON_ONLY:
                continue
            if id(mech) not in seen:
                seen.add(id(mech))
                blocks.append(mech)

    if any(m in blocks for m in (POWER_EUA_TWO_WAY, FUEL_SWITCHING)):
        for c in MANDATORY_CONFIRMERS:
            if id(c) not in seen:
                seen.add(id(c))
                blocks.append(c)

    for s in SUPPLEMENTARY_SIGNALS:
        if id(s) not in seen:
            seen.add(id(s))
            blocks.append(s)

    for f in FILTERS:
        if id(f) not in seen:
            seen.add(id(f))
            blocks.append(f)

    tail = [CONFLICT_RESOLUTION]
    if _NON_EUA_TOPICS.intersection(topics):
        tail.insert(0, NON_EUA_CARBON_MARKETS)

    return "\n\n".join([INFERENCE_RULES] + blocks + tail)


# =============================================================================
# CHANGELOG — các sửa đổi so với bản gốc
# =============================================================================
"""
LỖI LOGIC ĐÃ SỬA
----------------
1. CBAM_ETS: bản gốc viết "Bổ sung ngành vào ETS (hàng hải, hàng không, đường bộ,
   xây dựng) → nhu cầu EUA↑". Đường bộ và toà nhà thuộc ETS2 — hệ thống riêng với
   allowance riêng — nên KHÔNG làm tăng cầu EUA của ETS1. Đã tách rõ và thêm L9
   (luật phạm vi hệ thống) để chặn lỗi này ở cấp toàn khung.

2. POWER_EUA_TWO_WAY: bản gốc mô tả đủ hai chiều nhưng thiếu luật chống ĐẾM TRÙNG.
   Nếu dùng nhánh (b) rồi quay lại dùng giá điện làm bằng chứng cho nhánh (a), lập
   luận thành vòng tròn. Đã thêm bước bắt buộc xác định nguyên nhân giá điện tăng.

3. MACRO: kênh "USD↑ → EUA↓" trong bản gốc chỉ là tương quan hàng hoá chung, không
   có cơ chế nhân quả với EUA. Đã hạ xuống độ tin cậy thấp nhất và bổ sung kênh
   lãi suất CÓ cơ sở thật (cost of carry, qua khả năng banking của EUA).

4. RELATIVE_FUEL_ECONOMICS: bổ sung vòng phản hồi tự ổn định (EUA↑ làm than xấu đi
   tương đối so với khí → dispatch quay về khí → cầu EUA↓). Thiếu vòng này thì mọi
   suy luận fuel switching đều đơn điệu một chiều và phóng đại biên độ.

5. WEATHER_POWER_SYSTEM: bổ sung phân biệt sưởi bằng khí trực tiếp tại hộ gia đình
   (ngoài ETS1) với phụ tải điện (trong ETS1) — bản gốc gộp chung "nhiệt độ lạnh
   làm tăng nhu cầu sưởi → phát thải↑ → cầu EUA↑" là bỏ qua ranh giới phạm vi.

6. POLICY_MSR: (a) sửa "tỷ lệ rút vốn của MSR" thành "tỷ lệ hấp thụ (intake rate)";
   (b) thêm công bố TNAC như một sự kiện giao dịch được; (c) thêm kết quả đấu giá
   như tín hiệu cầu; (d) áp L4 vào chu kỳ compliance (lịch cố định → phần lớn đã
   nằm trong giá); (e) thêm cảnh báo không khẳng định tham số định lượng từ trí nhớ.

7. OIL_GASOIL: kênh crack spread trong bản gốc đi qua "nhu cầu diesel vận tải" —
   nhưng giao thông đường bộ không thuộc ETS1. Đã buộc kênh này phải đi qua hoạt
   động công nghiệp/phát điện trong ETS1 mới hợp lệ.

BỔ SUNG MỚI
-----------
8. INFERENCE_RULES (L1–L10): gom toàn bộ quy tắc chống suy diễn rải rác thành bộ
   luật dùng chung, thêm các luật bản gốc chưa có — nút cuối bắt buộc (L1), luật
   bất ngờ vs mức tuyệt đối (L4), xác nhận chéo ≥2 kênh (L5), phạm vi ETS1/ETS2 (L9),
   mặc định thận trọng (L10).

9. CONFLICT_RESOLUTION: bản gốc có nhiều cơ chế cho tín hiệu trái chiều (kênh ngược
   chiều ở FUEL_SWITCHING/OIL_GASOIL/METALS, mean-reversion ở POLICY_MSR) nhưng
   không có luật phân xử. Đã thêm thứ tự ưu tiên theo khung thời gian.

10. TERM_STRUCTURE_CARRY (cơ chế mới): giải thích vì sao EUA hành xử như tài sản
    tích trữ được, và vì sao cú sốc cầu ngắn hạn tác động yếu hơn thay đổi kỳ vọng
    cap/MSR. Đây là mảnh còn thiếu để phân xử đúng khi thời tiết và chính sách
    trái chiều nhau.

11. Chuẩn hoá template 8 trường + gắn HORIZON và ĐỘ MẠNH cho từng cơ chế, thêm
    MECHANISM_REGISTRY và build_context() để chỉ nạp đúng cơ chế liên quan thay vì
    đổ toàn bộ tri thức vào prompt.

CẦN KIỂM CHỨNG
--------------
Các tham số định lượng của EU ETS (tỷ lệ intake của MSR, ngưỡng TNAC, hệ số LRF,
mốc thời gian ETS2/CBAM, ngày deadline nộp trả) đã được sửa đổi nhiều lần qua các
gói lập pháp. File này cố tình mô tả chúng theo CHIỀU TÁC ĐỘNG thay vì con số cụ
thể. Nếu cần đưa số vào hệ thống, hãy đối chiếu văn bản pháp lý hiện hành thay vì
lấy từ file này.
"""
