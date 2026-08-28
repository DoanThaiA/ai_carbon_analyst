"""
Chuỗi nhân quả CHUẨN (single source of truth) mô tả các yếu tố ảnh hưởng đến
cung/cầu và giá EUA.

Dùng chung cho cả report_generator.py (EUA_ANALYSIS_FRAMEWORK — báo cáo Mục 3/5)
và quote_chat.py (_DOMAIN_KNOWLEDGE — chat). Trước đây 2 nơi có 2 bản sao độc lập
của cùng 1 chuỗi logic (vd địa chính trị → EUA) và bị lệch nhau khi 1 bên được
sửa còn bên kia thì không — mọi chuỗi nhân quả xuất hiện ở cả 2 nơi PHẢI import
từ đây, KHÔNG hard-code lại, để sửa 1 chỗ là đồng bộ toàn hệ thống.

Quy tắc bắt buộc cho mọi chuỗi trong file này: PHẢI kết thúc bằng tác động rõ
ràng lên cung/cầu hoặc giá EUA (hoặc nêu rõ đây là kênh phụ củng cố 1 chuỗi
khác) — không để chuỗi dangling (dừng ở 1 thị trường trung gian mà không quay
lại kết luận cung/cầu/giá EUA).
"""

FUEL_SWITCHING = (
    "Gas↑ → nhà máy điện chuyển sang than → phát thải↑ → cầu EUA↑ → EUA↑\n"
    "Gas↓ hoặc Than↑ → chuyển ngược → phát thải↓ → cầu EUA↓ → EUA↓\n"
    "Hạ tầng điện sự cố hoặc Gas gián đoạn → thiếu điện → huy động than → cầu EUA↑ → EUA↑\n"
    "RES (gió/mặt trời)↑ → dispatch than/gas↓ → phát thải↓ → cầu EUA↓ → EUA↓"
)

POWER_EUA_TWO_WAY = (
    "Điện Đức (DEBY1) ↔ EUA — quan hệ HAI CHIỀU, PHẢI đọc cùng gas/than/RES, không kết luận một chiều:\n"
    "a) Power → EUA: Điện Đức↑ do huy động thêm than/gas (không phải do RES thấp) → utility hedge "
    "(mua thêm nhiên liệu + EUA tương ứng sản lượng đã bán) → cầu EUA↑ → EUA↑. Ngược lại điện thấp → "
    "giảm hedge, có thể unwind (bán EUA) → cầu EUA yếu đi.\n"
    "b) EUA → Power (chiều ngược lại, hay bị bỏ sót): EUA↑ → nhà máy đưa chi phí EUA vào giá chào bán "
    "điện (carbon cost pass-through) → giá điện Đức↑."
)

OIL_GASOIL = (
    "Dầu thường tương quan thuận Gas → nếu kéo Gas↑ mạnh → fuel switching than → cầu EUA↑ → EUA↑.\n"
    "Crack spread Gasoil rộng → nhu cầu diesel↑ (vận tải/công nghiệp) → tiêu thụ điện CN↑ → phát thải "
    "ngành điện + CN nặng trong EU ETS (thép, xi măng, hóa chất, lọc dầu)↑ → cầu EUA↑ → EUA↑.\n"
    "Dầu↑ mạnh & kéo dài → chi phí vận tải & sản xuất công nghiệp↑ → biên lợi nhuận ngành thâm dụng "
    "năng lượng (thuộc EU ETS)↓ → CÓ THỂ cắt giảm sản lượng/công suất → phát thải ngành đó↓ → cầu EUA↓ "
    "— đây là kênh NGƯỢC CHIỀU với 2 chuỗi trên, CHỈ nêu khi tin tức xác nhận cắt giảm sản lượng thực "
    "tế, KHÔNG suy diễn ngay từ việc giá dầu tăng."
)

METALS = (
    "Gas/Điện châu Âu↑ → chi phí smelter nhôm/kẽm (ngành thâm dụng năng lượng, nhiều nhà máy thuộc "
    "phạm vi EU ETS)↑ → cắt giảm công suất smelter → (i) giá kim loại↑ do nguồn cung giảm, ĐỒNG THỜI "
    "(ii) phát thải ngành này↓ → cầu EUA↓ — tín hiệu (ii) thường CỦNG CỐ cùng chiều với chuỗi fuel "
    "switching gas/điện chứ không phải tín hiệu EUA độc lập mới."
)

CBAM_ETS = (
    "EUA↑ → CBAM certificate cost↑ → nhập khẩu thép/nhôm/xi măng vào EU đắt hơn → dịch chuyển cầu "
    "sang sản phẩm phát thải thấp.\n"
    "Bổ sung ngành vào ETS (hàng hải, hàng không, đường bộ, xây dựng) → nhu cầu EUA↑ → EUA↑."
)

POLICY_MSR = (
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

GEOPOLITICS_SUPPLY_CHAIN = (
    "Xung đột quốc tế/xung đột thương mại/sanctions → gián đoạn hoặc đe dọa gián đoạn nguồn cung "
    "gas/dầu (Nga, Trung Đông, eo biển vận chuyển...) → CUNG gas/dầu↓ (hoặc rủi ro cung↓) → GIÁ gas "
    "(TTF)/dầu (Brent, WTI)↑ → hệ thống điện buộc tăng huy động nguồn phát thải cao (than) để bù đắp "
    "(fuel switching) → phát thải↑ → cầu EUA↑ → EUA↑.\n"
    "Chiều ngược lại: căng thẳng hạ nhiệt/nguồn cung nối lại → CUNG gas/dầu↑ → GIÁ gas/dầu↓ → giảm áp "
    "lực fuel switching → cầu EUA↓ → EUA↓."
)

FINANCE_SPECULATION = (
    "EUA được giao dịch như tài sản tài chính → dòng vốn đầu tư/đầu cơ, thanh khoản và kỳ vọng thị "
    "trường có thể tạo biến động giá NGẮN HẠN theo cả hai chiều. Biến động do dòng vốn/đầu cơ thường "
    "CHỈ LÀ TẠM THỜI và không bền vững nếu KHÔNG đi kèm thay đổi trong yếu tố cơ bản (cung/cầu, giá "
    "năng lượng, chính sách)."
)

MACRO = (
    "USD↑ / Lãi suất thực↑ → áp lực giảm đồng thời vàng, dầu, kim loại cơ bản → có thể lan sang EUA "
    "(CHỈ nêu khi có số liệu cụ thể, vd DXY, lợi suất trái phiếu).\n"
    "GDP / sản xuất CN↑ → nhu cầu điện & phát thải (đặc biệt ngành thâm dụng phát thải: thép, xi măng, "
    "hóa chất)↑ → cầu EUA↑ → EUA↑.\n"
    "Suy thoái kinh tế → phát thải↓ → cầu EUA↓ → EUA↓ (hiệu ứng đang suy yếu dần do chuyển dịch năng "
    "lượng và cải thiện hiệu quả sử dụng năng lượng — không mặc định tăng trưởng kinh tế luôn kéo EUA "
    "tăng mạnh)."
)

POSITIONING_TECHNICALS = (
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

WEATHER_POWER_SYSTEM = (
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

RELATIVE_FUEL_ECONOMICS = (
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

HYDROGEN_DECARBONIZATION = (
    "Hydrogen tác động EUA qua kênh DÀI HẠN/CHẬM, KHÔNG dùng để giải thích biến động giá ngắn hạn:\n"
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
