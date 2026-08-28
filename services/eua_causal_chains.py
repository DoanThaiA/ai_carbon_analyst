"""
Chuỗi nhân quả CHUẨN (single source of truth) mô tả các yếu tố ảnh hưởng đến
cung/cầu và giá EUA — đây là "khung phân tích chuẩn" mà mọi phân tích EUA
trong hệ thống (báo cáo lẫn chat) PHẢI bám sát, không được tự suy diễn lệch.

DÙNG CHUNG cho:
  - services/report_generator.py -> EUA_ANALYSIS_FRAMEWORK (báo cáo Mục 3/5)
  - services/quote_chat.py       -> _DOMAIN_KNOWLEDGE (chat)
Trước đây 2 nơi có 2 bản sao độc lập của cùng 1 chuỗi logic (vd địa chính trị
→ EUA) và bị lệch nhau khi 1 bên được sửa còn bên kia thì không. Mọi chuỗi
nhân quả xuất hiện ở cả 2 nơi PHẢI import từ đây, KHÔNG hard-code lại — sửa
1 chỗ trong file này là đồng bộ toàn hệ thống.

QUY TẮC BẮT BUỘC cho mọi chuỗi trong file này:
  1. PHẢI kết thúc bằng tác động rõ ràng lên cung/cầu hoặc giá EUA (hoặc nêu
     rõ đây là kênh phụ củng cố 1 chuỗi khác) — KHÔNG được dangling (dừng ở
     1 thị trường trung gian mà không quay lại kết luận cung/cầu/giá EUA).
  2. Chỉ áp dụng khi có dữ liệu giá / tin tức thực sự hỗ trợ — KHÔNG suy diễn
     gượng ép khi thiếu căn cứ.
  3. Chuỗi nào có ghi "LƯU Ý: hệ thống KHÔNG theo dõi ..." nghĩa là hệ thống
     không có instrument/dữ liệu giá riêng cho biến số đó — CHỈ được viện dẫn
     khi tin tức trích dẫn nêu rõ số liệu cụ thể, không tự suy đoán con số.

BẢN ĐỒ TOPIC -> CHUỖI ÁP DỤNG (đối chiếu 13 NewsTopic trong
crawl_news/classification.py để biết chuỗi nào nên dùng khi tin thuộc topic
nào — xem thêm "B. DANH MỤC THEO DÕI" trong EUA_ANALYSIS_FRAMEWORK):
  eua_ets               -> POLICY_MSR (cơ chế lõi EU ETS/MSR/đấu giá)
  energy_gas            -> FUEL_SWITCHING, RELATIVE_FUEL_ECONOMICS, GEOPOLITICS_SUPPLY_CHAIN
  energy_power_eu       -> POWER_EUA_TWO_WAY, WEATHER_POWER_SYSTEM
  energy_coal           -> FUEL_SWITCHING, RELATIVE_FUEL_ECONOMICS
  energy_oil            -> OIL_GASOIL
  energy_renewable      -> WEATHER_POWER_SYSTEM (nhánh RES), FUEL_SWITCHING (dòng RES)
  energy_hydrogen       -> HYDROGEN_DECARBONIZATION (kênh dài hạn/chậm — KHÔNG dùng cho biến động ngắn hạn)
  geopolitics           -> GEOPOLITICS_SUPPLY_CHAIN
  eu_policy             -> POLICY_MSR, CBAM_ETS
  cbam                  -> CBAM_ETS
  vcm                   -> KHÔNG có chuỗi riêng (chủ đích — VCM không thay thế EUA trong
                           EU ETS, tín chỉ tự nguyện là thị trường tách biệt; chỉ phục vụ Mục 4)
  global_carbon_market  -> KHÔNG có chuỗi riêng (chủ đích — hệ thống compliance khác EU
                           ETS, không fungible với EUA; chỉ phục vụ Mục 4)
  vietnam_carbon_policy -> KHÔNG có chuỗi riêng (chủ đích — chính sách carbon VN không
                           tác động trực tiếp cung/cầu EUA; chỉ phục vụ Mục 4/8)
  (dòng vốn/macro/technical không gắn 1 topic cụ thể) -> FINANCE_SPECULATION, POSITIONING_TECHNICALS, MACRO
  (kim loại cơ bản — không có topic/instrument riêng) -> METALS (chỉ dùng khi tin có số liệu cụ thể)

QUAN HỆ GIỮA CÁC CHUỖI (chuỗi nào tinh chỉnh/mở rộng chuỗi nào — áp dụng
chuỗi "refinement" khi đủ dữ liệu, fallback về chuỗi cơ bản khi thiếu):
  - RELATIVE_FUEL_ECONOMICS tinh chỉnh FUEL_SWITCHING: ưu tiên đọc chênh lệch
    CDS/CSS thay vì giá Gas/Than tuyệt đối khi tin tức có đủ số liệu spread;
    nếu không có, fallback về logic giá tuyệt đối đơn giản trong FUEL_SWITCHING.
  - WEATHER_POWER_SYSTEM mở rộng nhánh RES/thời tiết trong FUEL_SWITCHING và
    nhánh (a) của POWER_EUA_TWO_WAY, thêm đủ các nguồn phát (gió/solar/thủy
    điện/hạt nhân/interconnector) và phân biệt rõ nguyên nhân giá điện tăng.
  - POSITIONING_TECHNICALS chi tiết hoá FINANCE_SPECULATION bằng các chỉ báo
    kỹ thuật cụ thể (open interest, volume, calendar spreads, options skew, COT).
  - METALS là tín hiệu PHỤ củng cố FUEL_SWITCHING (gas/điện↑ → smelter cắt
    công suất) — KHÔNG dùng làm căn cứ EUA độc lập một mình.
  - GEOPOLITICS_SUPPLY_CHAIN là "đường dẫn" tới FUEL_SWITCHING: sự kiện địa
    chính trị PHẢI đi qua đủ bước cung→giá gas/dầu trước khi tới fuel switching,
    không được nhảy thẳng từ sự kiện chính trị sang kết luận EUA.
"""

# Cơ chế lõi quan trọng nhất của toàn khung: Gas/Than/RES quyết định cơ cấu
# phát điện (dispatch) → phát thải → cầu EUA. Hầu hết chuỗi khác trong file
# này (dầu, kim loại, địa chính trị, thời tiết) cuối cùng đều dẫn về đây.
# Áp dụng cho topic: energy_gas, energy_coal, energy_renewable, eua_ets.
FUEL_SWITCHING = (
    "Mô tả: cơ chế FUEL SWITCHING — chuỗi logic quan trọng nhất trong toàn bộ khung phân tích. "
    "Biến động giá Gas/Than/RES làm thay đổi cơ cấu phát điện (dispatch: nhà máy điện chọn đốt "
    "gas, than, hay ưu tiên năng lượng tái tạo), từ đó thay đổi mức phát thải và cầu EUA.\n"
    "Gas↑ → nhà máy điện chuyển sang than → phát thải↑ → cầu EUA↑ → EUA↑\n"
    "Gas↓ hoặc Than↑ → chuyển ngược → phát thải↓ → cầu EUA↓ → EUA↓\n"
    "Hạ tầng điện sự cố hoặc Gas gián đoạn → thiếu điện → huy động than → cầu EUA↑ → EUA↑\n"
    "RES (gió/mặt trời)↑ → dispatch than/gas↓ → phát thải↓ → cầu EUA↓ → EUA↓"
)

# Quan hệ NHÂN QUẢ HAI CHIỀU giữa giá Điện Đức (DEBY1) và EUA — dễ bị hiểu
# nhầm là 1 chiều (chỉ Power→EUA) nên phải tách rõ 2 nhánh (a)/(b) và luôn đọc
# cùng gas/than/RES trước khi kết luận. Áp dụng cho topic: energy_power_eu.
POWER_EUA_TWO_WAY = (
    "Mô tả: quan hệ nhân quả HAI CHIỀU giữa giá Điện Đức (DEBY1) và EUA — không chỉ điện tác động "
    "EUA (qua utility hedge) mà EUA cũng tác động ngược lại giá điện (qua carbon cost pass-through). "
    "Dễ bị hiểu nhầm là quan hệ 1 chiều nên PHẢI đọc cùng gas/than/RES trước khi kết luận:\n"
    "a) Power → EUA: Điện Đức↑ do huy động thêm than/gas (không phải do RES thấp) → utility hedge "
    "(mua thêm nhiên liệu + EUA tương ứng sản lượng đã bán) → cầu EUA↑ → EUA↑. Ngược lại điện thấp → "
    "giảm hedge, có thể unwind (bán EUA) → cầu EUA yếu đi.\n"
    "b) EUA → Power (chiều ngược lại, hay bị bỏ sót): EUA↑ → nhà máy đưa chi phí EUA vào giá chào bán "
    "điện (carbon cost pass-through) → giá điện Đức↑."
)

# Dầu/Gasoil tác động EUA qua 2 kênh THUẬN (tương quan với Gas → fuel
# switching; crack spread rộng → nhu cầu diesel/công nghiệp → phát thải EU
# ETS) và 1 kênh NGHỊCH (giá dầu cao kéo dài bóp margin công nghiệp → cắt
# giảm sản lượng → phát thải giảm) — 3 kênh có thể cho tín hiệu TRÁI CHIỀU
# nhau, phải xét đủ điều kiện của từng kênh trước khi kết luận.
# Áp dụng cho topic: energy_oil.
OIL_GASOIL = (
    "Mô tả: Dầu và Gasoil tác động EUA qua 2 kênh THUẬN CHIỀU (tương quan giá với Gas thúc đẩy fuel "
    "switching; crack spread rộng thúc đẩy nhu cầu công nghiệp) và 1 kênh NGƯỢC CHIỀU (giá dầu cao "
    "kéo dài bóp nghẹt margin công nghiệp, có thể giảm sản lượng) — 3 kênh có thể cho tín hiệu trái "
    "chiều nhau, phải xét đủ điều kiện từng kênh trước khi kết luận.\n"
    "Dầu thường tương quan thuận Gas → nếu kéo Gas↑ mạnh → fuel switching than → cầu EUA↑ → EUA↑.\n"
    "Crack spread Gasoil rộng → nhu cầu diesel↑ (vận tải/công nghiệp) → tiêu thụ điện CN↑ → phát thải "
    "ngành điện + CN nặng trong EU ETS (thép, xi măng, hóa chất, lọc dầu)↑ → cầu EUA↑ → EUA↑.\n"
    "Dầu↑ mạnh & kéo dài → chi phí vận tải & sản xuất công nghiệp↑ → biên lợi nhuận ngành thâm dụng "
    "năng lượng (thuộc EU ETS)↓ → CÓ THỂ cắt giảm sản lượng/công suất → phát thải ngành đó↓ → cầu EUA↓ "
    "— đây là kênh NGƯỢC CHIỀU với 2 chuỗi trên, CHỈ nêu khi tin tức xác nhận cắt giảm sản lượng thực "
    "tế, KHÔNG suy diễn ngay từ việc giá dầu tăng."
)

# Kim loại cơ bản (nhôm/kẽm) là TÍN HIỆU PHỤ, không phải kênh EUA độc lập:
# chi phí năng lượng tăng khiến smelter cắt công suất, vừa đẩy giá kim loại
# lên vừa giảm phát thải của chính ngành đó — luôn CỦNG CỐ cùng chiều với
# FUEL_SWITCHING chứ không tạo thêm 1 luận điểm EUA mới.
# LƯU Ý: hệ thống không có instrument giá kim loại — chỉ dùng khi tin tức có
# số liệu cụ thể.
METALS = (
    "Mô tả: kim loại cơ bản (nhôm/kẽm) là TÍN HIỆU PHỤ, KHÔNG PHẢI kênh EUA độc lập — chi phí năng "
    "lượng tăng khiến smelter cắt công suất, vừa đẩy giá kim loại lên vừa giảm phát thải chính ngành "
    "đó, luôn củng cố cùng chiều với fuel switching chứ không tạo thêm 1 luận điểm EUA mới.\n"
    "Gas/Điện châu Âu↑ → chi phí smelter nhôm/kẽm (ngành thâm dụng năng lượng, nhiều nhà máy thuộc "
    "phạm vi EU ETS)↑ → cắt giảm công suất smelter → (i) giá kim loại↑ do nguồn cung giảm, ĐỒNG THỜI "
    "(ii) phát thải ngành này↓ → cầu EUA↓ — tín hiệu (ii) thường CỦNG CỐ cùng chiều với chuỗi fuel "
    "switching gas/điện chứ không phải tín hiệu EUA độc lập mới."
)

# CBAM và việc mở rộng phạm vi EU ETS sang ngành mới (hàng hải, hàng không,
# đường bộ, xây dựng...) — 1 chiều là hệ quả của giá EUA cao (CBAM cost), 1
# chiều là driver trực tiếp làm tăng cầu EUA (ETS mở rộng phạm vi).
# Áp dụng cho topic: cbam, eu_policy (khi chính sách liên quan mở rộng ETS).
CBAM_ETS = (
    "Mô tả: CBAM (thuế carbon biên giới) và việc mở rộng phạm vi EU ETS sang ngành mới — 1 chiều là "
    "HỆ QUẢ của giá EUA cao (CBAM certificate cost tăng theo), 1 chiều là DRIVER trực tiếp làm tăng "
    "cầu EUA (khi ETS mở rộng phạm vi ngành phải mua EUA).\n"
    "EUA↑ → CBAM certificate cost↑ → nhập khẩu thép/nhôm/xi măng vào EU đắt hơn → dịch chuyển cầu "
    "sang sản phẩm phát thải thấp.\n"
    "Bổ sung ngành vào ETS (hàng hải, hàng không, đường bộ, xây dựng) → nhu cầu EUA↑ → EUA↑."
)

# Cơ chế LÕI của chính thị trường EU ETS (không qua trung gian ngành khác):
# cap/lộ trình giảm phát thải, Market Stability Reserve (MSR), lịch đấu giá,
# free allocation, và deadline compliance cycle — đây là các đòn bẩy CUNG/CẦU
# EUA trực tiếp nhất, tách biệt với các kênh liên thị trường (gas/than/dầu...).
# Áp dụng cho topic: eua_ets, eu_policy.
POLICY_MSR = (
    "Mô tả: cơ chế LÕI của chính thị trường EU ETS (không qua trung gian ngành khác) — cap/lộ trình "
    "giảm phát thải, Market Stability Reserve (MSR), lịch đấu giá, tỷ lệ phân bổ miễn phí, và deadline "
    "compliance cycle. Đây là các đòn bẩy tác động CUNG/CẦU EUA trực tiếp nhất trong toàn khung.\n"
    "Trần phát thải (cap) & lộ trình giảm siết chặt hơn (giảm nhanh hơn) → kỳ vọng thiếu hụt allowance "
    "tương lai → cầu/giá EUA kỳ hạn↑ → EUA↑. Giãn/nới lộ trình giảm cap → kỳ vọng dư cung tương lai → "
    "EUA↓.\n"
    "MSR rút thêm cap (tăng tỷ lệ rút vốn) → cung EUA↓ → EUA↑ (ngược lại nếu giải phóng MSR/giảm tỷ lệ "
    "rút).\n"
    "Lịch đấu giá EUA dồn/tăng khối lượng trong tháng/quý → cung ngắn hạn↑ → áp lực giảm giá; trì "
    "hoãn/rút khỏi lịch đấu giá → cung ngắn hạn↓ → áp lực tăng giá.\n"
    "Giảm tỷ lệ phân bổ miễn phí (free allocation) → doanh nghiệp phải mua thêm EUA → cầu↑ → EUA↑.\n"
    "Deadline compliance cycle (nộp trả EUA hàng năm) → nhu cầu mua gom EUA tăng trước hạn → áp lực "
    "tăng giá ngắn hạn quanh mốc deadline."
)

# "Đường dẫn" bắt buộc từ 1 sự kiện địa chính trị tới EUA: xung đột/sanctions
# PHẢI đi qua đủ 2 bước trung gian — tác động CUNG nhiên liệu rồi tác động
# GIÁ gas/dầu — trước khi nối vào FUEL_SWITCHING; TUYỆT ĐỐI KHÔNG nhảy thẳng
# từ sự kiện chính trị sang kết luận EUA (lỗi hay gặp nhất khi phân tích tin
# địa chính trị). Áp dụng cho topic: geopolitics.
GEOPOLITICS_SUPPLY_CHAIN = (
    "Mô tả: đường dẫn BẮT BUỘC từ 1 sự kiện địa chính trị tới EUA — PHẢI đi qua đủ 2 bước trung gian "
    "(tác động CUNG nhiên liệu, rồi tác động GIÁ gas/dầu) trước khi nối vào fuel switching. Lỗi hay "
    "gặp nhất là nhảy thẳng từ sự kiện chính trị sang kết luận EUA mà bỏ qua 2 bước này.\n"
    "Xung đột quốc tế/xung đột thương mại/sanctions → gián đoạn hoặc đe dọa gián đoạn nguồn cung "
    "gas/dầu (Nga, Trung Đông, eo biển vận chuyển...) → CUNG gas/dầu↓ (hoặc rủi ro cung↓) → GIÁ gas "
    "(TTF)/dầu (Brent, WTI)↑ → hệ thống điện buộc tăng huy động nguồn phát thải cao (than) để bù đắp "
    "(fuel switching) → phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "Chiều ngược lại: căng thẳng hạ nhiệt/nguồn cung nối lại → CUNG gas/dầu↑ → GIÁ gas/dầu↓ → giảm áp "
    "lực fuel switching → cầu EUA↓ → EUA↓."
)

# EUA là tài sản tài chính có thanh khoản riêng — dòng vốn/đầu cơ có thể tạo
# biến động giá KHÔNG dựa trên cung/cầu thực (fundamentals). Đây là "van an
# toàn" bắt buộc đọc trước khi kết luận 1 biến động giá là xu hướng nền tảng
# hay chỉ là nhiễu positioning ngắn hạn — xem thêm bản chi tiết ở
# POSITIONING_TECHNICALS.
FINANCE_SPECULATION = (
    "Mô tả: EUA là tài sản tài chính có thanh khoản riêng — dòng vốn/đầu cơ có thể tạo biến động giá "
    "KHÔNG dựa trên cung/cầu thực (fundamentals). Đây là \"van an toàn\" bắt buộc đọc trước khi kết "
    "luận 1 biến động giá là xu hướng nền tảng hay chỉ là nhiễu positioning ngắn hạn.\n"
    "EUA được giao dịch như tài sản tài chính → dòng vốn đầu tư/đầu cơ, thanh khoản và kỳ vọng thị "
    "trường có thể tạo biến động giá NGẮN HẠN theo cả hai chiều. Biến động do dòng vốn/đầu cơ thường "
    "CHỈ LÀ TẠM THỜI và không bền vững nếu KHÔNG đi kèm thay đổi trong yếu tố cơ bản (cung/cầu, giá "
    "năng lượng, chính sách)."
)

# Kênh liên thị trường CHÉO (cross-asset) qua USD/lãi suất và chu kỳ kinh tế
# vĩ mô (GDP, suy thoái) — tác động GIÁN TIẾP và ngày càng suy yếu do quá
# trình chuyển dịch năng lượng, KHÔNG dùng làm căn cứ chính nếu không có số
# liệu vĩ mô cụ thể đi kèm.
MACRO = (
    "Mô tả: kênh liên thị trường CHÉO (cross-asset) qua USD/lãi suất và chu kỳ kinh tế vĩ mô (GDP, "
    "suy thoái) — tác động GIÁN TIẾP, và hiệu ứng này đang suy yếu dần theo thời gian do chuyển dịch "
    "năng lượng; KHÔNG dùng làm căn cứ chính nếu thiếu số liệu vĩ mô cụ thể.\n"
    "USD↑ / Lãi suất thực↑ → áp lực giảm đồng thời vàng, dầu, kim loại cơ bản → có thể lan sang EUA "
    "(CHỈ nêu khi có số liệu cụ thể, vd DXY, lợi suất trái phiếu).\n"
    "GDP / sản xuất CN↑ → nhu cầu điện & phát thải (đặc biệt ngành thâm dụng phát thải: thép, xi măng, "
    "hóa chất)↑ → cầu EUA↑ → EUA↑.\n"
    "Suy thoái kinh tế → phát thải↓ → cầu EUA↓ → EUA↓ (hiệu ứng đang suy yếu dần do chuyển dịch năng "
    "lượng và cải thiện hiệu quả sử dụng năng lượng — không mặc định tăng trưởng kinh tế luôn kéo EUA "
    "tăng mạnh)."
)

# Phiên bản CHI TIẾT/KỸ THUẬT của FINANCE_SPECULATION: các chỉ báo positioning
# cụ thể (open interest, volume, calendar spread, options skew, COT) giúp
# PHÂN BIỆT biến động giá do dòng tiền mới (xu hướng) với biến động do
# short-covering/roll kỳ hạn (nhiễu kỹ thuật, không phải xu hướng thật) —
# dùng khi tin tức có đủ số liệu, nếu không thì dùng bản tổng quát ở
# FINANCE_SPECULATION.
POSITIONING_TECHNICALS = (
    "Mô tả: phiên bản CHI TIẾT/KỸ THUẬT của yếu tố tài chính & đầu cơ (xem FINANCE_SPECULATION) — các "
    "chỉ báo positioning cụ thể (open interest, volume, calendar spread, options skew, COT) giúp PHÂN "
    "BIỆT biến động do dòng tiền mới (xu hướng thật) với biến động do short-covering/roll kỳ hạn "
    "(nhiễu kỹ thuật, không phải xu hướng).\n"
    "EUA là công cụ tài chính thanh khoản; positioning có thể khuếch đại biến động ngắn hạn:\n"
    "Short covering, stop-loss, option expiry, roll kỳ hạn, fund/CTA flow hoặc thanh khoản mỏng → EUA "
    "có thể tăng/giảm mạnh dù fundamentals chưa đổi.\n"
    "Open interest↑ cùng giá↑ và volume↑ có thể xác nhận dòng tiền mới; giá↑ nhưng open interest↓ có thể "
    "chỉ là short covering. Cần đọc cùng volume, calendar spreads, options skew và COT/positioning nếu có.\n"
    "Không gọi biến động là 'fundamental bull/bear trend' nếu không có xác nhận từ power, fuel, auction, "
    "policy hoặc compliance.\n"
    "LƯU Ý: hệ thống KHÔNG theo dõi open interest/volume/options skew/COT theo dữ liệu giá — CHỈ nêu "
    "liên kết này khi tin tức trích dẫn có SỐ LIỆU CỤ THỂ về các chỉ báo trên; TUYỆT ĐỐI KHÔNG tự bịa "
    "số liệu positioning khi không có nguồn."
)

# BIẾN XÁC NHẬN bắt buộc cho mọi luận điểm dựa trên điện/RES: thời tiết
# (gió/solar/nhiệt độ) và tình trạng hệ thống điện (thủy điện, hạt nhân,
# interconnector) quyết định cơ cấu phát điện thực tế — mở rộng và chi tiết
# hoá nhánh RES/thời tiết trong FUEL_SWITCHING và nhánh (a) của
# POWER_EUA_TWO_WAY. Áp dụng cho topic: energy_power_eu, energy_renewable.
WEATHER_POWER_SYSTEM = (
    "Mô tả: BIẾN XÁC NHẬN bắt buộc cho mọi luận điểm dựa trên điện/RES — mở rộng và chi tiết hoá nhánh "
    "RES/thời tiết trong FUEL_SWITCHING. Thời tiết và tình trạng hệ thống điện (thủy điện, hạt nhân, "
    "interconnector) quyết định cơ cấu phát điện thực tế, không chỉ riêng gió/mặt trời.\n"
    "Thời tiết và cấu trúc hệ thống điện là biến xác nhận bắt buộc cho luận điểm EUA:\n"
    "Gió thấp / solar thấp / thủy điện suy giảm / sự cố hạt nhân / interconnector hạn chế → thermal "
    "generation↑ → phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "Gió cao / solar cao / thủy điện phục hồi / hạt nhân khả dụng cao / nhập khẩu điện tăng → thermal "
    "generation↓ → phát thải↓ → cầu EUA↓ → EUA↓.\n"
    "Nhiệt độ lạnh/nóng cực đoan có thể làm phụ tải điện & sưởi/làm mát↑; tác động EUA chỉ mạnh khi "
    "phần phụ tải tăng thêm được đáp ứng bằng nguồn phát điện thuộc EU ETS.\n"
    "Phải phân biệt giá điện↑ do supply tightness/thermal dispatch (tín hiệu EUA mạnh) với giá điện↑ chỉ "
    "do EUA pass-through (không phải tín hiệu EUA mới, xem POWER_EUA_TWO_WAY nhánh b)."
)

# Bản REFINEMENT (nâng cấp độ chính xác) của FUEL_SWITCHING: thay vì kết
# luận từ giá TUYỆT ĐỐI của Gas/Than, dùng chênh lệch chi phí phát điện sau
# carbon (Clean Dark Spread vs Clean Spark Spread) — chuẩn xác hơn vì phản
# ánh đúng động lực kinh tế của utility khi chọn nhiên liệu. CHỈ áp dụng khi
# tin tức có đủ số liệu spread/generation; nếu không, fallback về
# FUEL_SWITCHING (vẫn hợp lệ, chỉ là tín hiệu yếu hơn).
RELATIVE_FUEL_ECONOMICS = (
    "Mô tả: bản REFINEMENT (tinh chỉnh nâng cao độ chính xác) của FUEL_SWITCHING — thay vì kết luận từ "
    "giá tuyệt đối Gas/Than, dùng chênh lệch chi phí phát điện sau carbon (CDS vs CSS), phản ánh đúng "
    "hơn động lực kinh tế thực sự của utility khi chọn nhiên liệu. Chỉ dùng khi có đủ dữ liệu spread; "
    "nếu không, fallback về FUEL_SWITCHING.\n"
    "KHÔNG kết luận chỉ từ giá TUYỆT ĐỐI của Gas hoặc Than — PHẢI đọc chênh lệch chi phí phát điện sau "
    "carbon: Clean Dark Spread (CDS), Clean Spark Spread (CSS), carbon switching price và merit order.\n"
    "CDS cải thiện tương đối so với CSS → than cạnh tranh hơn khí → coal burn có thể↑ → phát thải/MWh↑ "
    "→ utility hedge & cầu EUA↑ → EUA↑.\n"
    "CSS cải thiện tương đối so với CDS → khí cạnh tranh hơn than → phát thải/MWh↓ → cầu EUA↓ → EUA↓.\n"
    "Chỉ coi fuel switching là tín hiệu MẠNH nếu spread, dữ liệu generation và điều kiện hệ thống điện "
    "cùng xác nhận; không suy ra chỉ từ TTF↑ hoặc API2↑ đơn lẻ.\n"
    "LƯU Ý: hệ thống KHÔNG tính sẵn CDS/CSS (không có instrument riêng) — CHỈ áp dụng cơ chế spread này "
    "khi tin tức trích dẫn có nêu rõ số liệu spread/generation cụ thể; nếu không, dùng đúng chuỗi Gas/"
    "Than tuyệt đối đơn giản ở FUEL_SWITCHING (vẫn hợp lệ, chỉ là tín hiệu yếu hơn khi thiếu spread)."
)

# Kênh DÀI HẠN/CHẬM duy nhất trong file này — khác hẳn các chuỗi khác (vốn là
# tín hiệu ngắn hạn/theo phiên), hydrogen chỉ tác động EUA qua quá trình
# decarbonization công nghiệp diễn ra trong nhiều năm. Vì vậy topic
# energy_hydrogen bị loại khỏi SECTION_TOPICS["5"] (tín hiệu liên thị trường
# theo phiên) trong report_generator.py — CHỈ xuất hiện ở Mục 3 (driver tổng
# quan), KHÔNG dùng để giải thích biến động giá EUA ngày/tuần.
HYDROGEN_DECARBONIZATION = (
    "Mô tả: kênh DÀI HẠN/CHẬM duy nhất trong khung phân tích này — khác hẳn các chuỗi khác (vốn là tín "
    "hiệu ngắn hạn/theo phiên), hydrogen chỉ tác động EUA qua quá trình decarbonization công nghiệp "
    "diễn ra trong nhiều năm — KHÔNG dùng để giải thích biến động giá ngắn hạn:\n"
    "Hydrogen xanh thay thế than cốc/khí trong luyện thép & hoá chất (ngành thuộc EU ETS) → dự án/công "
    "suất H2 xanh mở rộng → giảm dần phát thải trực tiếp ngành đó theo thời gian → cầu EUA dài hạn↓ "
    "(hiệu ứng CHẬM, chỉ rõ nét sau nhiều năm).\n"
    "Chính sách trợ cấp/hạ tầng hydrogen (EU Hydrogen Bank, RePowerEU, mở rộng CBAM sang hydro) → tín "
    "hiệu decarbonization mạnh hơn dự kiến → có thể củng cố kỳ vọng cap/MSR thắt chặt hơn trong tương "
    "lai (xem POLICY_MSR) → hỗ trợ nhẹ cho EUA dài hạn.\n"
    "Sản xuất H2 bằng điện phân (electrolysis) quy mô lớn → tăng thêm nhu cầu điện → nếu đáp ứng bằng "
    "nguồn phát thải cao → phát thải↑ → cầu EUA↑ (hiệu ứng nhỏ, quy mô electrolysis ở EU hiện còn thấp).\n"
    "TUYỆT ĐỐI KHÔNG dùng tin hydrogen để kết luận biến động giá EUA trong ngày/tuần, trừ khi tin tức "
    "nêu rõ 1 sự kiện/chính sách cụ thể có tác động tức thời (vd thay đổi cap/quy định liên quan trực "
    "tiếp) — mặc định đây là driver cấu trúc dài hạn, không phải tín hiệu giao dịch ngắn hạn."
)
