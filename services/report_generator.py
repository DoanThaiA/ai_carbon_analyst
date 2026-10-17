import json
import re
import logging
from datetime import datetime, timedelta, timezone, date, time as dtime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from urllib.parse import quote, urlparse
import asyncio
from pathlib import Path

import anthropic
import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Article, EuaAuction, Price, Instrument, PriceCrawlSource, Report
from core.config import Settings
from services import biz_memory
from services import cbam_estimate
from services import forecast_review
from services.report_consistency import (
    GAP_MIN_ATR, apply_consistency_fixes, atr_true_range, derived_target_issues, describe_range_position, eua_streak,
    session_signals, streak_momentum,
)
from services.title_similarity import is_similar_title, title_tokens
from services import eua_causal_chains as chains
from services.eua_framework_admin import get_overrides_map
from crawl_prices.crawl_ice import ice_product_url, is_ice_symbol
from crawl_prices.market_events_fetcher import (
    fetch_eia_weekly_inventory,
    fetch_baker_hughes_rig_count,
    format_eia_outcome,
    format_bh_outcome,
)

logger = logging.getLogger(__name__)

# Mục 1-4, 8, biz (phân tích chuyên sâu, chuỗi nhân quả, chiến lược) dùng Opus;
# Mục 6 (tóm tắt ngắn từng bài) dùng Haiku — rẻ hơn nhiều, đủ cho việc tóm tắt.
REPORT_MODEL_OPUS = "claude-opus-5"
# Haiku 5.5: rẻ hơn Haiku 4.5 ~10 lần. Bị bộ lọc an toàn từ chối (không có fallback phía server)
# → _call_llm gọi lại 1 lần bằng REFUSAL_FALLBACK_MODELS.
REPORT_MODEL_HAIKU = "claude-haiku-5-5"
REFUSAL_FALLBACK_MODELS = {"claude-haiku-5-5": "claude-haiku-4-5"}

# Meta-instruction chống dài dòng — đặt ở đầu system message mọi section,
# LLM anchor vào instruction đầu tiên mạnh nhất.
CONCISENESS_RULE = (
    "QUY TẮC VIẾT BẮT BUỘC: Mỗi ý tối đa 1–2 câu ngắn. Không câu dẫn dắt/đệm/chuyển tiếp thừa. "
    "Không lặp số liệu đã nêu ở trường/mục khác. Ưu tiên SỐ LIỆU → KẾT LUẬN, bỏ diễn giải thừa. "
    "Cấm bịa số liệu/sự kiện/source không có trong dữ liệu đã cung cấp.\n"
    "QUY TẮC CHÍNH XÁC BẮT BUỘC: "
    "(1) Tuyên bố/con số do MỘT BÊN nêu (quan chức, chính phủ, công ty, nhà phân tích) phải ghi rõ chủ thể "
    "phát ngôn đúng chức danh như bài viết ('theo ...', '... cho biết') — KHÔNG trình bày như sự thật đã xác nhận, "
    "và KHÔNG gắn tag FACT cho tuyên bố của một bên. "
    "(2) Cùng 1 con số/sự kiện (vd mức nâng dự báo nhu cầu của OPEC, vốn cam kết của một tổ chức) PHẢI giống nhau "
    "ở mọi mục; nếu các bài nêu số khác nhau thì chỉ dùng số của bài được trích dẫn ở chính bullet đó, "
    "nêu rõ 'theo [nguồn]', không tự chọn số khác ở mục khác. "
    "(3) KHÔNG đưa vào phân tích/tóm tắt tin mà chính bạn nhận định là 'không liên quan trực tiếp' tới carbon/năng lượng EU "
    "— loại hẳn thay vì giữ lại kèm lời rào đón. "
    "(4) Tương quan ngắn hạn KHÔNG phải nhân quả: không viết 'X kéo Y' chỉ vì cùng chiều trong vài phiên; "
    "khi thiếu số liệu định lượng (spark/dark spread, giá chuyển đổi nhiên liệu, vị thế quỹ, đấu giá EEX, MSR) "
    "phải nêu rõ là chưa kiểm chứng được và hạ mức tự tin tương ứng (spark/dark spread và giá chuyển đổi nhiên liệu ĐÃ "
    "có sẵn khi prompt có khối 'SPREAD THAN–KHÍ' — khi đó dùng số đó, không gọi là chưa kiểm chứng). "
    "(5) Không lặp một tin ở nhiều mục/bullet cho cùng 1 ý. "
    "(6) Viết đúng chính tả tiếng Việt, đọc lại để tránh lỗi gõ (vd 'tiêu tụ', 'khuyến hướng', 'hôm tháng 9'). "
    "(7) CHỈ dùng tiếng Việt và ký tự Latin — TUYỆT ĐỐI KHÔNG chèn chữ Hán/Nhật/Hàn. Thuật ngữ chuẩn: EUA = hạn ngạch phát thải EU "
    "(EU Allowance); open interest = lượng vị thế mở; carbon allowances = hạn ngạch carbon; không dịch word-by-word. "
    "(8) Cùng 1 sự kiện có cả nguồn gốc (Bloomberg, Reuters, Argus, cơ quan chính thức) và nguồn thứ cấp/tổng hợp (vd cafef.vn) → "
    "ưu tiên trích nguồn gốc. "
    "(9) Liên hệ tới EUA CHỈ khi bài/dữ liệu nêu cơ chế rõ; KHÔNG viết lại tin 'không liên quan' thành 'có liên quan' và "
    "KHÔNG đưa dự báo EUA dài hạn không có căn cứ. "
    "(10) NGÀY ĐĂNG: mỗi tin có ghi 'đăng DD/MM/YYYY' — chỉ trình bày là diễn biến hôm nay/mới khi tin đăng trong 2 "
    "ngày gần nhất; tin cũ hơn chỉ dùng làm bối cảnh và PHẢI nêu ngày (vd 'từ tháng 4/2026'). Sự việc/phát ngôn đã có "
    "từ trước thì không gọi là mới. KHÔNG quy nguyên nhân biến động giá của 1 phiên cho 1 tin khi không rõ tin ra "
    "trước/trong phiên đó — chỉ viết 'có thể góp phần' và nêu rõ là nhận định. "
    "(11) ĐỊNH DẠNG SỐ thống nhất kiểu Việt Nam ở MỌI mục: dấu phẩy thập phân, dấu chấm phân cách nghìn (87,14; "
    "-1,03%; 424.134). Dữ liệu đầu vào ghi '87.14' thì khi viết thành '87,14'. "
    "(12) Viết 'rủi ro hai chiều'/'hai chiều' thì PHẢI nêu cả chiều tăng lẫn chiều giảm; chỉ có 1 chiều thì ghi đúng 1 chiều. "
    "(13) Mọi chính sách/văn bản/quy định nêu ra (vd hướng dẫn ESMA, cắt giảm phân bổ miễn phí, ngày hiệu lực) PHẢI có "
    "trong tin đã cung cấp, đúng như tin viết — tin chỉ nêu ngày thông qua thì không tự suy ra ngày hiệu lực. "
    "(14) TRUNG THỰC VỚI NGUỒN: mọi con số/tuyên bố gắn với 1 bài PHẢI có trong chính bài đó — KHÔNG thêm chi tiết bài "
    "không viết (bài nói Mỹ 'vận động không chính thức' thì KHÔNG viết 'Mỹ đề nghị bổ sung ít nhất X'); KHÔNG dùng "
    "'dường như/có vẻ/được cho là' để đưa vào thông tin bài không nêu. Giữ ĐÚNG đơn vị, KỲ (mỗi năm hay tổng) và PHẠM VI "
    "của con số (vd '660 tỷ EUR MỖI NĂM cho phát điện sạch, lưới, hiệu suất' — không rút thành '660 tỷ EUR cho điện khí hóa'). "
    "(15) Số liệu trong CÙNG 1 bài không khớp nhau (vd 'đã rút 330/400 triệu thùng' nhưng 'còn lại 100 triệu'): nếu 1 bài KHÁC "
    "trong dữ liệu nêu rõ hơn đúng chỉ tiêu đó (vd Argus: 'còn khoảng 96 triệu thùng, trong đó 46 triệu ở châu Âu') → DÙNG số "
    "của bài rõ hơn, ghi nguồn, và chú thích ngắn độ lệch với bài kia (vd 'khoảng 96 triệu thùng theo Argus; con số 100 "
    "triệu trong tiêu đề The Edge là số làm tròn'); số trong TIÊU ĐỀ thường là số làm tròn — ưu tiên số chi tiết trong thân bài/"
    "nguồn chuyên ngành. Chỉ khi KHÔNG có nguồn nào rõ hơn mới ghi 'số liệu trong bài chưa khớp (400 − 330 = 70)'. TUYỆT ĐỐI "
    "KHÔNG để 2 con số mâu thuẫn (vd 'đã rút 330/400' và 'còn 100') cùng nằm trong báo cáo mà không chú thích — bỏ chú "
    "thích nhưng giữ cả 2 số là lỗi nặng hơn. "
    "(16) CHÍNH SÁCH: ghi rõ GIAI ĐOẠN (đề xuất/dự thảo/đã thông qua/có hiệu lực), MỐC áp dụng và QUY MÔ nếu bài nêu; "
    "dự thảo có hiệu lực sau hơn 12 tháng KHÔNG phải động lực giá ngắn hạn. KHÔNG suy diễn tác động mạnh hơn nguồn — "
    "nguồn nói biện pháp 'không làm giảm giá' thì không xếp là 'hỗ trợ tăng giá'; tác động lên doanh nghiệp phải "
    "tương xứng quy mô thật (vd tổng phí vài trăm triệu EUR/năm chia cho cả nền kinh tế thì không viết 'làm tăng mạnh chi phí'). "
    "Sức ép từ nước thành viên/doanh nghiệp đòi nới, hoãn, áp trần giá EU ETS (vd Italy, Czechia, Eni) là RỦI RO CHÍNH SÁCH "
    "GIẢM GIÁ EUA — không gộp thành 'trung lập' nếu không có yếu tố ngược chiều cụ thể. "
    "(17) SỰ KIỆN CŨ: sự kiện đã xảy ra trước ngày dữ liệu hoặc đã nêu ở 'BÁO CÁO TRƯỚC' → PHẢI ghi ngày diễn ra và chỉ "
    "nhắc lại khi có diễn biến MỚI; nếu cơ quan chính thức/nguồn nói 'quy mô hạn chế', 'hoạt động bình thường' → PHẢI nêu "
    "lời đó và hạ mức tác động (không viết 'nguy cơ ảnh hưởng cung dầu toàn cầu'). "
    "(18) Cùng 1 chỉ tiêu mà các nguồn/ngày nêu số KHÁC nhau (vd xuất khẩu qua Hormuz 'còn ~50%' theo Eni hôm trước vs "
    "'gần mức trước chiến tranh' theo Argus hôm nay) → nêu CẢ 2 kèm nguồn và ngày, không lặng lẽ chọn 1. "
    "(19) CHUỖI NGUỒN: thông tin bài dẫn lại từ hãng/cơ quan khác (phỏng vấn với Financial Times, 'told Reuters', 'theo "
    "Bloomberg', thông cáo của IEA...) → ghi theo chuỗi GỐC → NƠI ĐĂNG LẠI (vd 'theo phỏng vấn với Financial Times, Politico "
    "đăng lại'); KHÔNG quy phát biểu cho nơi đăng lại như nguồn gốc. "
    "(20) TÍN HIỆU CUNG TRUNG–DÀI HẠN: tin chính sách có nhắc đề xuất thay đổi cap, phạm vi hoặc nguồn cung EU ETS (đưa "
    "loại bỏ carbon vĩnh viễn — BECCS/bio-CCS, DAC — vào ETS, rà soát MSR, mở rộng ngành, cap sau 2030, nối ETS với thị "
    "trường khác) → PHẢI ghi nhận là yếu tố CUNG TRUNG–DÀI HẠN (kèm giai đoạn: đề xuất/tham vấn/dự thảo), kể cả khi không "
    "có tác động ngắn hạn; KHÔNG viết 'không chạm tới cap/phạm vi ETS' khi bài có nhắc đề xuất như vậy. "
    "(21) MỨC TÁC ĐỘNG TƯƠNG XỨNG: kênh BIÊN lên tổng cầu EUA — crack spread → lọc dầu giảm/tăng công suất, hàng không, "
    "hàng hải, 1 nhà máy/1 doanh nghiệp — có cơ chế đúng (lọc dầu thuộc EU ETS) nhưng chỉ là phần nhỏ phát thải ETS và biến "
    "động công suất vài % không đổi đáng kể tổng cầu hạn ngạch → PHẢI ghi rõ 'tác động Thấp', KHÔNG xếp thành động lực "
    "chính và KHÔNG dùng làm căn cứ chính cho kịch bản giao dịch. "
    "SỐ TỰ TÍNH (chia/nhân từ số trong bài, vd giá/tấn = tổng giá trị / khối lượng) PHẢI ghi phép tính ngay trong câu, "
    "vd '≈ 559 USD/t (11,7 triệu USD / 20.932 t)' — người đọc và hệ thống kiểm tra phải lần lại được. "
    "(22) TÊN HÃNG TIN TRONG CÂU: KHÔNG tự ghi \"theo TASS\", \"(Reuters 16/10)\"... — hệ thống tự gắn nhãn nguồn đúng nhà xuất "
    "bản và hãng gốc của bài được trích; chỉ ghi chủ thể phát ngôn (người/cơ quan). Tin truyền thông nhà nước (TASS, RIA, Tân "
    "Hoa Xã) không phải nguồn sơ cấp — khi có thông cáo/phát biểu chính thức (Hội đồng/EC/chính phủ) thì dẫn theo đó. "
    "(23) MỖI TIN PHÂN TÍCH ĐẦY ĐỦ ĐÚNG 1 LẦN: Tóm tắt điều hành chỉ 1 câu sự kiện + chiều tác động; phân tích cơ chế nằm ở "
    "Mục 3 \"Phân tích\"; \"Diễn biến chính\" chỉ nêu sự kiện + chiều; \"Cần theo dõi\" chỉ nêu ĐIỀU KIỆN TƯƠNG LAI, KHÔNG tóm tắt "
    "lại tin. Tin có tác động lên EUA \"Thấp\"/gián tiếp (hợp tác địa phương, sổ tay/hướng dẫn tín chỉ tự nguyện, thương vụ "
    "loại bỏ carbon tự nguyện, SAF) → KHÔNG đưa vào Tóm tắt điều hành, Diễn biến chính hay \"Cần theo dõi\"."
)

# Văn bản đã luật hoá được phép dùng làm luận điểm chính sách dài hạn KÈM trích dẫn văn bản (ngoài tin trong ngày).
LEGISLATED_POLICY_FACTS = (
    "- Lộ trình loại bỏ phân bổ miễn phí EU ETS cho các ngành thuộc CBAM (Chỉ thị (EU) 2023/959, Điều 10a(1a); "
    "Quy định CBAM (EU) 2023/956): tỷ lệ phân bổ miễn phí còn lại 97,5% (2026), 95% (2027), 90% (2028), 77,5% (2029), "
    "51,5% (2030), 39% (2031), 26,5% (2032), 14% (2033), 0% (2034). Khi dùng PHẢI ghi trích dẫn văn bản trong câu, "
    "vd \"(Chỉ thị (EU) 2023/959)\"; nếu tin trong ngày nêu đề xuất sửa đổi lộ trình thì nêu theo tin. KHÔNG rút gọn thành "
    "\"cắt phân bổ miễn phí tới 2029–2030\": lộ trình chạy 2026–2034 (về 0% năm 2034), 2029–2030 chỉ là bước giảm mạnh nhất. "
    "Rủi ro chính sách (vd \"nước thành viên đẩy sửa đổi nới lỏng ETS\") chỉ được nêu khi có tin cụ thể trong dữ liệu, kèm nguồn."
)

_anthropic_client: anthropic.AsyncAnthropic | None = None
_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def _get_anthropic_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=_get_settings().anthropic_api_key)
    return _anthropic_client

SECTION_TOPICS: Dict[str, List[str]] = {
    "1": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "geopolitics", "eu_policy", "cbam"],
    "2": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "geopolitics", "eu_policy", "cbam"],
    "3": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "energy_renewable",
          "energy_hydrogen", "geopolitics", "eu_policy", "cbam", "vcm", "global_carbon_market",
          "vietnam_carbon_policy"],
    "4": ["cbam", "vcm", "global_carbon_market", "vietnam_carbon_policy"],
    # "Diễn biến chính" (ngay dưới Bảng giá nhanh): tin tác động cung/cầu EUA trực
    # tiếp hoặc gián tiếp — bỏ vcm/vietnam_carbon_policy vì gần như không ảnh
    # hưởng cung cầu hạn ngạch EU ETS.
    "dev": ["eua_ets", "eu_policy", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil",
            "energy_renewable", "energy_hydrogen", "geopolitics", "cbam", "global_carbon_market"],
    "8": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "eu_policy", "cbam"],
    "biz": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil",
            "energy_renewable", "geopolitics", "eu_policy", "cbam", "vcm",
            "global_carbon_market", "vietnam_carbon_policy"],
}

SECTION_MAX_TOKENS: Dict[str, int] = {
    "1": 2048,    # 5 bullet × 2 câu
    "2": 4096,    # bảng bullish/bearish
    "3": 8192,    # mục phân tích sâu nhất — JSON lồng sâu nhất, giữ nguyên để tránh cắt cụt
    "4": 2048,    # 3 bullet ngắn
    "dev": 4096,  # "Diễn biến chính": tối đa 5 tin × (tiêu đề + 1–2 câu)
    "8": 2048,    # danh sách events
    # 4096 (tăng từ 3072) — 3072 từng không đủ vào ngày có nhiều tin CBAM/VCM/
    # chính sách, khiến LLM sinh nhiều gợi ý hơn dự kiến rồi bị cắt giữa chừng
    # (response.stop_reason="max_tokens"), làm JSON lỗi và cả mục rơi về fallback
    # rỗng. Đi kèm giới hạn số lượng gợi ý tối đa trong _prompt_biz_recommendation
    # để output không phình to không kiểm soát được nữa.
    "biz": 4096,
}

# Nhãn hiển thị cho từng topic (khớp NewsTopic trong schemas/crawl_models.py) —
# dùng để gắn thẻ topic cạnh tiêu đề bài viết ở Mục 6 (Chi tiết các tin tức
# chính, xem get_news_for_report bên dưới). Giữ nguyên dạng chữ thường/hoa tự
# nhiên ở đây — frontend tự viết hoa bằng CSS (uppercase), không cần format lại.
TOPIC_DISPLAY_LABELS: Dict[str, str] = {
    "eua_ets": "EUA/ETS",
    "energy_gas": "Khí gas",
    "energy_power_eu": "Điện châu Âu",
    "energy_coal": "Than",
    "energy_oil": "Dầu",
    "energy_renewable": "Năng lượng tái tạo",
    "energy_hydrogen": "Hydrogen",
    "geopolitics": "Địa chính trị",
    "eu_policy": "Chính sách EU",
    "cbam": "CBAM",
    "vcm": "VCM",
    "global_carbon_market": "Thị trường carbon toàn cầu",
    "vietnam_carbon_policy": "Chính sách carbon VN",
}

# ─────────────────────────────────────────────────────────────────────
# Khung phân tích giá EUA — tiêm ĐỘNG vào prompt Mục 2/3
#
# TRƯỚC ĐÂY đây là 2 hằng số module-level (EUA_ANALYSIS_FRAMEWORK/COMPACT)
# luôn nạp TĨNH toàn bộ 14 cơ chế của eua_causal_chains.py cho mọi báo cáo,
# bất kể ngày đó có tin thuộc topic tương ứng hay không (vd không có tin dầu
# vẫn nạp nguyên OIL_GASOIL, không có tin kim loại vẫn nạp METALS...). Giờ
# chuyển thành HÀM, gọi `chains.build_context(topics_present, horizon=...)`
# để chỉ nạp đúng cơ chế khớp topic THỰC SỰ có tin trong ngày — tiết kiệm
# input token đáng kể vào những ngày tin tức không phủ hết mọi nhóm, mà vẫn
# giữ đúng cơ chế cần thiết khi có tin (topics_present tính theo đúng
# SECTION_TOPICS[section_key], xem `_topics_present`).
# ─────────────────────────────────────────────────────────────────────

_EUA_FRAMEWORK_PREAMBLE = (
    "QUY TẮC GỐC: MỌI chuỗi nhân quả bên dưới là KHUNG CHUẨN DUY NHẤT của hệ thống — PHẢI bám sát "
    "đúng chiều mũi tên đã cho, TUYỆT ĐỐI KHÔNG tự sinh thêm bước trung gian khác hay đảo chiều so "
    "với khung này. Mọi yếu tố khi phân tích PHẢI đi tới kết luận cuối cùng về tác động lên CUNG/CẦU "
    "và GIÁ EUA (hoặc nêu rõ đây là kênh phụ củng cố 1 chuỗi khác) — KHÔNG được dừng phân tích giữa "
    "chừng ở 1 thị trường trung gian (gas/dầu/điện/kim loại...) mà không kết luận tác động lên EUA.\n"
    "CẤM TUYỆT ĐỐI nhắc tên biến/nhãn NỘI BỘ của khung này trong bất kỳ text nào trả về cho người đọc "
    "— các tên như \"FUEL_SWITCHING\", \"POWER_EUA_TWO_WAY\", \"OIL_GASOIL\", \"GEOPOLITICS_SUPPLY_CHAIN\", "
    "\"RELATIVE_FUEL_ECONOMICS\", \"WEATHER_POWER_SYSTEM\", \"CBAM_ETS\", \"POLICY_MSR\", \"FINANCE_SPECULATION\", "
    "\"POSITIONING_TECHNICALS\", \"MACRO\", \"METALS\", \"HYDROGEN_DECARBONIZATION\", \"TERM_STRUCTURE_CARRY\", "
    "hay nhãn \"nhánh (a)\"/\"nhánh (b)\", \"theo khung...\", \"áp dụng đúng chuỗi...\", hay các THẺ NGOẶC VUÔNG "
    "dùng để tổ chức nội dung bên dưới (\"[ID]\", \"[TOPIC]\", \"[HORIZON]\", \"[ĐỘ MẠNH]\", \"[KÍCH HOẠT]\", "
    "\"[CHUỖI]\", \"[BÁC BỎ / VÔ HIỆU]\", \"[DỮ LIỆU]\"), hay MÃ LUẬT (\"L1\"–\"L11\") — tất cả CHỈ là công cụ nội "
    "bộ giúp AI suy luận đúng logic, KHÔNG BAO GIỜ được xuất hiện nguyên văn trong \"content\"/text đầu ra. "
    "Đầu ra chỉ được viết bằng ngôn ngữ phân tích tự nhiên, thể hiện ĐÚNG logic nhân quả của khung đó "
    "(số liệu → cơ chế → tác động cung/cầu → kết luận EUA) mà không gọi tên hay viện dẫn framework/luật."
)

# Ghi chú áp dụng — rẻ (vài câu) nên giữ tĩnh thay vì gate theo topic cho đơn
# giản; chỉ có ý nghĩa khi model THỰC SỰ dùng tới OIL_GASOIL/GEOPOLITICS
# nhánh (a) (đã được build_context() nạp hay không tuỳ topics_present).
_EUA_FRAMEWORK_NOTES = (
    "GHI CHÚ ÁP DỤNG (chỉ liên quan nếu bạn thực sự dùng tới cơ chế tương ứng ở trên):\n"
    "- Nếu dùng tới crack spread Gasoil: con số đã tính sẵn (quy đổi cùng đơn vị USD/bbl) trong mục "
    "\"GASOIL CRACK SPREAD\" bên dưới — LUÔN dùng đúng con số đó, TUYỆT ĐỐI KHÔNG tự suy luận crack "
    "spread từ 2 số liệu khác đơn vị (Gasoil USD/MT vs Brent/WTI USD/bbl).\n"
    "- Nếu dùng tới kênh cung nhiên liệu của địa chính trị: PHẢI nêu đủ bước \"sự kiện địa chính trị "
    "→ tác động cung → tác động giá gas/dầu → tác động EUA\" (dùng đúng số liệu giá gas/dầu đã có "
    "trong DỮ LIỆU GIÁ nếu sự kiện đã phản ánh vào giá; nếu tin mới xảy ra, giá chưa kịp phản ánh thì "
    "ghi rõ \"rủi ro cung/giá trong ngắn hạn\" thay vì bịa số) — KHÔNG bỏ qua bước cung/giá để suy "
    "diễn thẳng từ sự kiện chính trị sang EUA."
)

_EUA_FRAMEWORK_CATALOG = """B. DANH MỤC THEO DÕI (phạm vi "liên quan trực tiếp đến giá EUA" — CHỈ nội dung khớp
danh mục này mới được đưa vào phân tích Mục 3; tin ngoài phạm vi này bỏ qua):

NHÓM 1 — NĂNG LƯỢNG & NHIÊN LIỆU HÓA THẠCH:
- Khí tự nhiên: Henry Hub (NG), TTF châu Âu (Dutch TTF Natural Gas Calendar Month Futures — TT1!)
- Điện Đức: German Power Base Year Futures (DEBY1)
- Dầu thô: WTI (NYMEX CL), Brent (ICE B)
- Sản phẩm lọc dầu: ICE Gasoil/LSGO khi có tín hiệu crack spread đáng chú ý
- Than nhiệt: NEWC Index (GlobalCOAL Newcastle), API 5/API 2/API 4 Index; xu hướng đầu tư & khai thác mỏ than nhiệt
- Coking coal: chỉ số than cốc & tương tự; xu hướng giá, tiêu dùng, đầu tư & khai thác mỏ than cốc
- Khí Hydrogen: dùng khử oxy trong sản xuất thép xanh
- Năng lượng tái tạo: quy mô sản xuất & dự báo tăng trưởng
- Khủng hoảng năng lượng / gián đoạn nguồn cung (xung đột quốc tế, xung đột thương mại...)
- Yếu tố dẫn dắt cần bám: OPEC+, tồn kho EIA/API (thứ Tư hàng tuần), rig count Baker Hughes (thứ Sáu), địa chính trị Trung Đông/Nga/Mỹ/Trung Quốc, nhu cầu Trung Quốc–Ấn Độ, thời tiết (mùa bão Mỹ, mùa đông châu Âu)

NHÓM 2 — HẠN NGẠCH & TÍN CHỈ CARBON:
- EUA Futures Dec'26 (CKZ26); dự báo giá carbon từ Refinitiv (Reuters), BloombergNEF, ICIS, Enerdata, PIK, CAKE/KOBiZE, FastMarket
- Thị trường tuân thủ: EU ETS (EUA futures ICE), UK ETS, California Cap-and-Trade, CORSIA
- Thị trường tự nguyện (VCM): xu hướng giá theo loại tín chỉ (nature-based, tech-based), chuẩn Verra/Gold Standard/ACR/CAR
- Market Stability Reserve (MSR)
- Xu hướng dòng vốn đầu tư & hoạt động đầu cơ vào EUA
- Động thái mua/bán của big players: RWE, EDF, Enel, Uniper, PGE, EnBW, Macquarie, Morgan Stanley, Citigroup, BNP Paribas, Société Générale, UniCredit, BOA; trading houses: Trafigura, Vitol, Glencore, Mercuria, Gunvor

NHÓM 3 — CHÍNH SÁCH (QUAN TRỌNG HƠN TIN GIÁ — xem QUY TẮC ƯU TIÊN bên dưới):
- CBAM của EU (đặc biệt quan trọng — ảnh hưởng trực tiếp DN xuất khẩu Việt Nam ngành thép, nhôm, xi măng, phân bón, khí hydro, điện), CBAM của UK, lộ trình tương tự ở nước khác, Article 6 Paris Agreement
- "Fit-for-55", chính sách đánh thuế phát thải (tiến trình & tốc độ), ngành bổ sung vào CBAM/ETS, deadline nộp thuế phát thải
- Chính sách thị trường tự nguyện VCM: dự án mới, methodology mới
- Chính sách/quy định pháp luật mới về hạn ngạch phát thải, tín chỉ carbon tại thị trường Việt Nam
- Chính sách năng lượng tái tạo, khí gas, khí Hydrogen, than & các nhiên liệu hóa thạch, chính sách khí hậu
- Chính sách hạ tầng & bất động sản Trung Quốc, nhu cầu chuyển dịch năng lượng (đồng cho EV và lưới điện)

QUY TẮC ƯU TIÊN: NHÓM 3 (CHÍNH SÁCH) quan trọng hơn tin giá ở NHÓM 1/2 — 1 thay đổi quy
định CBAM có giá trị hơn 10 bài bình luận giá EUA. Khi cả tin chính sách và tin giá cùng
xuất hiện trong ngày, PHẢI ưu tiên nêu bật tin chính sách trước."""

_EUA_FRAMEWORK_RULES_FULL = (
    "C. QUY TẮC NHẬN ĐỊNH BẮT BUỘC:\n"
    "- Luôn bắt đầu bằng số liệu thực tế (giá đóng cửa, % thay đổi) TRƯỚC khi phân tích nguyên nhân.\n"
    "- Xây dựng chuỗi nhân quả rõ ràng, không gán nhãn cảm tính.\n"
    "- Kết luận chiều giá EUA chỉ khi ≥2 yếu tố xác nhận cùng hướng.\n"
    "- Tín hiệu mâu thuẫn nhau → ghi \"tín hiệu hỗn hợp\" + nêu 2 chiều + điều kiện kích hoạt mỗi chiều.\n"
    "- Nếu không có liên kết chéo đáng chú ý: ghi \"Không có tín hiệu liên thị trường mới\" — KHÔNG bịa."
)
_EUA_FRAMEWORK_RULES_COMPACT = (
    "C. QUY TẮC NHẬN ĐỊNH:\n"
    "- Bắt đầu bằng số liệu thực tế TRƯỚC phân tích. Kết luận chiều giá chỉ khi ≥2 yếu tố cùng hướng.\n"
    "- Mâu thuẫn → \"tín hiệu hỗn hợp\" + 2 chiều + điều kiện. Không có liên kết chéo → ghi rõ, KHÔNG bịa."
)

_EUA_FRAMEWORK_TIMEFRAME_FULL = (
    "D. KHUNG THỜI GIAN PHÂN TÍCH — BẮT BUỘC ĐỐI CHIẾU CẢ \"Δ NGÀY\" VÀ \"Δ TUẦN\" (để nhận định khách "
    "quan nhất, không bị nhiễu/thổi phồng bởi biến động của riêng 1 phiên):\n"
    "1. GHI RÕ KHUNG THỜI GIAN: mọi số liệu %Δ trích dẫn PHẢI ghi rõ là \"Δ ngày\" (so với phiên liền "
    "trước) hay \"Δ tuần\" (so với 7 ngày trước, lấy đúng trường \"Δ tuần\" trong DỮ LIỆU GIÁ) — TUYỆT "
    "ĐỐI KHÔNG viết \"%Δ\" trống không rõ khung thời gian nào, và TUYỆT ĐỐI KHÔNG tự suy ra Δ tuần từ "
    "Δ ngày hay ngược lại — chỉ dùng đúng 2 con số đã cho sẵn.\n"
    "2. ĐỐI CHIẾU TRƯỚC KHI KẾT LUẬN CHIỀU: trước khi kết luận chiều biến động của bất kỳ instrument "
    "nào (EUA, Gas, Than, Điện Đức, Dầu, Gasoil...), PHẢI đối chiếu CẢ Δ ngày VÀ Δ tuần của chính "
    "instrument đó:\n"
    "   - CÙNG CHIỀU (Δ ngày và Δ tuần cùng tăng hoặc cùng giảm) → xu hướng nhất quán, có độ tin cậy "
    "CAO, được phép kết luận dứt khoát và dùng làm căn cứ chính cho hướng giá EUA.\n"
    "   - TRÁI CHIỀU (vd Δ ngày tăng nhưng Δ tuần vẫn đang giảm, hoặc ngược lại) → đây là biến động "
    "của RIÊNG 1 phiên, CHƯA đủ cơ sở kết luận đảo chiều xu hướng — PHẢI nêu rõ cả 2 con số và ghi "
    "nhận dạng \"biến động trong ngày đi ngược xu hướng tuần, cần thêm phiên xác nhận\" thay vì khẳng "
    "định đảo chiều ngay.\n"
    "3. ÁP DỤNG VÀO ĐỘ TIN CẬY: \"probability\" trong trading_scenarios và mức độ chắc chắn của "
    "\"eua_conclusion\"/kết luận chiều giá PHẢI phản ánh đúng mức đồng thuận ngày/tuần nói trên — 1 "
    "driver có cả Δ ngày và Δ tuần cùng chiều được xếp probability/độ tin cậy cao hơn 1 driver chỉ có "
    "tín hiệu của riêng 1 phiên."
)
_EUA_FRAMEWORK_TIMEFRAME_COMPACT = (
    "D. KHUNG THỜI GIAN: mọi %Δ ghi rõ \"Δ ngày\" hay \"Δ tuần\" (dùng đúng từ DỮ LIỆU GIÁ, KHÔNG tự "
    "tính). Đối chiếu CẢ 2: cùng chiều → tin cậy cao; trái chiều → biến động phiên đơn lẻ, cần thêm "
    "xác nhận."
)


# Báo cáo KHÔNG có phiên giá (xem is_no_price_report) — thay phần C/D (vốn bắt đầu
# từ giá đóng cửa, đối chiếu Δ ngày/Δ tuần) bằng quy tắc chỉ dựa trên tin tức.
_EUA_FRAMEWORK_RULES_NEWS_ONLY = (
    "C. QUY TẮC NHẬN ĐỊNH — BÁO CÁO KHÔNG CÓ PHIÊN GIAO DỊCH (cuối tuần, KHÔNG có dữ liệu giá):\n"
    "- CHỈ phân tích TIN TỨC: sự kiện/số liệu do bài báo nêu → tác động tới CUNG/CẦU EUA theo đúng chuỗi "
    "nhân quả ở phần A.\n"
    "- TUYỆT ĐỐI KHÔNG nêu giá đóng cửa, %Δ ngày/Δ tuần, biên độ phiên, khối lượng giao dịch, vùng hỗ trợ/"
    "kháng cự, mức giá mục tiêu hay bất kỳ con số giá nào — kể cả giá phiên trước đó; KHÔNG suy đoán giá "
    "đã/sẽ tăng giảm bao nhiêu. Các bước \"đối chiếu giá gas/dầu\" của phần A coi như CHƯA có dữ liệu xác "
    "nhận → ghi nhận là \"rủi ro/tác động tiềm năng chờ phiên giao dịch tới xác nhận\".\n"
    "- Kết luận chiều tác động lên EUA chỉ khi ≥2 tin cùng hướng; mâu thuẫn → \"tín hiệu hỗn hợp\" + 2 chiều.\n"
    "- Không có tin liên quan → ghi rõ, KHÔNG bịa."
)


def _eua_framework(
    topics_present: List[str], *, full: bool, overrides: Optional[Dict[str, str]] = None,
    news_only: bool = False,
) -> str:
    """Dựng khung phân tích giá EUA CHO ĐÚNG topics_present của ngày báo cáo.

    `full=True` (Mục 3): kèm phần B — DANH MỤC THEO DÕI, dùng horizon="medium"
    khi gọi build_context() (KHÔNG "short") vì Mục 3 vẫn cần
    HYDROGEN_DECARBONIZATION khi topic "energy_hydrogen" có tin trong ngày
    (đúng thiết kế "instrument_notes" NHÓM 1 mục (c) — hydrogen là driver tổng
    quan, không phải tín hiệu ngày/tuần, nhưng Mục 3 vẫn cần nhắc tới khi có
    tin) — "short" sẽ loại cơ chế này dù đang có tin, sai với thiết kế đó.
    `full=False` (Mục 2): bỏ phần B, horizon="short" (đúng bản chất mục
    này — bảng động lực theo phiên, không cần phần driver dài hạn).

    `overrides`: nội dung admin đã custom cho từng khối (xem
    services/eua_framework_admin.py::get_overrides_map), lấy 1 lần ở đầu
    `generate_report_content()` rồi truyền xuống đây — None = dùng toàn bộ
    bản mặc định trong code.

    `news_only=True`: báo cáo ngày không có phiên giá (Chủ Nhật/Thứ Hai — xem
    is_no_price_report) — bỏ ghi chú crack spread + phần C/D dựa trên giá, thay
    bằng _EUA_FRAMEWORK_RULES_NEWS_ONLY.
    """
    horizon = "medium" if full else "short"
    dynamic = chains.build_context(topics_present, horizon=horizon, overrides=overrides)

    parts = [
        "=== KHUNG PHÂN TÍCH GIÁ CARBON (EUA) — BẮT BUỘC ÁP DỤNG ===",
        _EUA_FRAMEWORK_PREAMBLE,
        "A. CÁC MỐI LIÊN HỆ LIÊN THỊ TRƯỜNG (cross-market signals) — CHỈ áp dụng cơ chế nào có dữ "
        "liệu/tin tức thực sự hỗ trợ, KHÔNG suy diễn gượng ép:",
        dynamic,
    ]
    if news_only:
        if full:
            parts.append(_EUA_FRAMEWORK_CATALOG)
        parts.append(_EUA_FRAMEWORK_RULES_NEWS_ONLY)
        parts.append("=== KẾT THÚC KHUNG PHÂN TÍCH ===")
        return "\n\n".join(parts)
    parts.append(_EUA_FRAMEWORK_NOTES)
    if full:
        parts.append(_EUA_FRAMEWORK_CATALOG)
        parts.append(_EUA_FRAMEWORK_RULES_FULL)
        parts.append(_EUA_FRAMEWORK_TIMEFRAME_FULL)
    else:
        parts.append(_EUA_FRAMEWORK_RULES_COMPACT)
        parts.append(_EUA_FRAMEWORK_TIMEFRAME_COMPACT)
    parts.append("=== KẾT THÚC KHUNG PHÂN TÍCH ===")

    return "\n\n".join(parts)

# ─────────────────────────────────────────────────────────────────────
# Data fetching
# ─────────────────────────────────────────────────────────────────────

# URL trang quote Barchart cho từng hợp đồng, dựng từ symbol trong
# price_crawl_sources (vd "CK*0" -> .../futures/quotes/CK%2A0/overview) — cùng
# domain crawl_prices/crawl_barchart.py dùng để lấy giá, chỉ khác đường dẫn
# (overview thay vì price-history) vì đây là link cho người dùng bấm xem, không
# phải endpoint crawl dữ liệu.
BARCHART_QUOTE_URL = "https://www.barchart.com/futures/quotes/{symbol}/overview"
CBAM_PRICE_PAGE_URL = 'https://taxation-customs.ec.europa.eu/carbon-border-adjustment-mechanism/price-cbam-certificates_en'


def _barchart_url(symbol: str) -> Optional[str]:
    """Link xem giá cho người đọc. Symbol "ICE:..." (lấy thẳng từ ICE — crawl_prices/crawl_ice.py)
    → trang sản phẩm ICE (None nếu symbol không khai pageId), còn lại → trang quote Barchart."""
    if is_ice_symbol(symbol):
        return ice_product_url(symbol)
    return BARCHART_QUOTE_URL.format(symbol=quote(symbol, safe=""))


async def _get_barchart_symbols(session: AsyncSession) -> Dict[str, str]:
    """Map instrument_code -> symbol Barchart (vd "EUA" -> "CK*0"), dùng để dựng
    link bảng giá Barchart cho người dùng bấm xem trực tiếp trong Mục 2."""
    rows = (await session.execute(select(PriceCrawlSource))).scalars().all()
    return {row.instrument_code: row.symbol for row in rows}


def _parse_decimal_price(raw: str) -> float:
    """Parse giá dạng '82.32', '82,32', '1,234.56' hoặc '1.234,56' → float.
    Dấu xuất hiện CUỐI CÙNG là dấu thập phân; dấu còn lại là phân cách nghìn.
    Nếu chỉ có một loại dấu và lặp lại nhiều lần thì đó là phân cách nghìn."""
    s = raw.replace("\xa0", "").replace(" ", "").replace("€", "")
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot != -1 and last_comma != -1:
        dec, thou = ("." , ",") if last_dot > last_comma else (",", ".")
        return float(s.replace(thou, "").replace(dec, "."))
    sep = "." if last_dot != -1 else "," if last_comma != -1 else None
    if sep is None:
        return float(s)
    if s.count(sep) > 1:
        return float(s.replace(sep, ""))
    return float(s.replace(",", "."))


async def _fetch_cbam_price() -> Optional[Dict]:
    url = CBAM_PRICE_PAGE_URL
    try:
        from selectolax.lexbor import LexborHTMLParser as HTMLParser
        import httpx
        
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            resp = await client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            tree = HTMLParser(resp.text)
            
            latest_quarter = ""
            latest_date = ""
            latest_price = ""
            next_date = ""
            
            for table in tree.css('table'):
                rows = table.css('tr')
                
                for i, row in enumerate(rows):
                    cells = [c.text(strip=True).replace('\xa0', '') for c in row.css('td, th')]
                    if len(cells) >= 3 and cells[0].startswith("Q") and "202" in cells[0]:
                        if cells[2] and cells[2] not in ["", "nbsp;", "&nbsp;", "N/A"]:
                            latest_quarter = cells[0]
                            latest_date = cells[1]
                            latest_price = cells[2]
                            
                            if i + 1 < len(rows):
                                next_cells = [c.text(strip=True) for c in rows[i+1].css('td, th')]
                                if len(next_cells) >= 3 and next_cells[0].startswith("Q"):
                                    next_date = next_cells[1]
                                    
            if latest_price:
                cbam_close = _parse_decimal_price(latest_price)
                note = f"Giá chốt theo quý, tại ngày {latest_date}"
                if next_date:
                    note += f", ngày chốt giá tiếp theo {next_date}"
                    
                return {
                    "name": "CBAM Certificate",
                    "code": "CBAM",
                    "price": f"{cbam_close:,.2f} EUR/tCO2",
                    "dday": "-",
                    "dweek": "-",
                    "up": True,
                    "note": note,
                    "close": cbam_close,
                    "day_change_pct": None,
                    "week_change_pct": None,
                    "category": "carbon",
                    "source_url": CBAM_PRICE_PAGE_URL,
                }
    except Exception as e:
        logger.error(f"Error fetching CBAM price: {e}")
        
    return None

CBAM_CODE = "CBAM"
CBAM_UNIT = "EUR/tCO2"
CBAM_SOURCE_NAME = "European Commission - CBAM certificate price"


async def save_cbam_price(session_factory) -> bool:
    """Crawl giá CBAM Certificate từ trang EC rồi upsert vào `instruments`/`prices` (mã CBAM)
    để mọi nơi đọc DB (báo cáo, tool giá của Jenny) đều tra được — trước đây giá chỉ fetch
    trực tiếp lúc sinh báo cáo nên Jenny không trả lời được "giá CBAM hôm nay". Giá chốt theo
    quý nên KHÔNG có Δ ngày/Δ tuần (day/week_change_pct = NULL). Khoá theo NGÀY DỮ LIỆU
    (hôm qua, giống crawl_barchart) — idempotent, chạy lại cùng ngày chỉ cập nhật dòng cũ.
    Trả về True nếu đã lưu."""
    cbam = await _fetch_cbam_price()
    if not cbam:
        return False
    now_vn = datetime.now(timezone(timedelta(hours=7)))
    data_date = (now_vn.date() - timedelta(days=1)).isoformat()
    async with session_factory() as session:
        instrument = (await session.execute(select(Instrument).where(Instrument.code == CBAM_CODE))).scalar_one_or_none()
        if instrument is None:
            instrument = Instrument(
                code=CBAM_CODE, name=cbam["name"], category="carbon",
                exchange="European Commission", unit=CBAM_UNIT,
            )
            session.add(instrument)
            await session.flush()
        stmt = pg_insert(Price).values(
            instrument_id=instrument.id, price_date=data_date, price_time=now_vn.strftime("%H:%M:%S"),
            close_price=cbam["close"], day_change_pct=None, week_change_pct=None,
            note=cbam["note"], source_name=CBAM_SOURCE_NAME,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "price_date"],
            set_={"price_time": stmt.excluded.price_time, "close_price": stmt.excluded.close_price,
                  "note": stmt.excluded.note, "source_name": stmt.excluded.source_name},
        )
        await session.execute(stmt)
        await session.commit()
    logger.info("[CBAM] Đã lưu giá CBAM %.2f %s (ngày dữ liệu %s)", cbam["close"], CBAM_UNIT, data_date)
    return True


async def _get_stored_cbam_price(session: AsyncSession, target_date_str: str) -> Optional[Dict]:
    """Giá CBAM gần nhất đã lưu với price_date <= target_date_str. CBAM chốt theo quý nên lấy
    dòng MỚI NHẤT tính đến ngày đó (không đòi trùng đúng ngày như các mã giao dịch hàng ngày)."""
    row = (await session.execute(
        select(Price, Instrument)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Instrument.code == CBAM_CODE, Price.price_date <= target_date_str)
        .order_by(desc(Price.price_date))
        .limit(1)
    )).first()
    if row is None:
        return None
    price, instrument = row
    return {
        "name": instrument.name,
        "code": CBAM_CODE,
        "price": f"{price.close_price:,.2f} {instrument.unit or CBAM_UNIT}",
        "dday": "-",
        "dweek": "-",
        "up": True,
        "note": price.note or "",
        "close": price.close_price,
        "day_change_pct": None,
        "week_change_pct": None,
        "category": instrument.category,
        "source_url": CBAM_PRICE_PAGE_URL,
        "price_date": price.price_date,
    }


def _format_pct_with_abs(pct: Optional[float], close_price: float, base_close: Optional[float] = None) -> str:
    """Format % kèm số tuyệt đối tăng/giảm, vd '+2.34% (+1.05)'. `base_close` = giá đóng cửa kỳ so sánh
    lấy thẳng từ DB (xem _comparison_closes) → số tuyệt đối chính xác; thiếu thì suy ngược từ pct — pct
    lưu làm tròn 3 số lẻ nên số suy ngược có thể lệch 0,01 (Gasoil 1.386,75 − 1.404,00 = −17,25 bị in −17,26)."""
    if pct is None:
        return "-"
    digits = 3 if abs(close_price) < 10 else 2  # Henry Hub ~3,2: "−0,10" không đủ để tự kiểm "−3,23%"
    if base_close:
        return f"{pct:+.2f}% ({close_price - base_close:+.{digits}f})"
    denom = 1 + pct / 100
    if denom == 0:
        return f"{pct:+.2f}%"
    prev_close = close_price / denom
    abs_change = close_price - prev_close
    return f"{pct:+.2f}% ({abs_change:+.{digits}f})"


def _matching_base(close: float, pct: Optional[float], candidates: List[tuple]) -> Optional[float]:
    """Giá kỳ so sánh trong `candidates` [(ngày, giá)] khớp đúng pct đã lưu (crawler tính pct từ chính giá này) —
    không khớp (nguồn điều chỉnh hồi tố, thiếu phiên) thì None để lùi về suy ngược từ pct."""
    if pct is None:
        return None
    for _, base in candidates:
        if base and abs((close / base - 1) * 100 - pct) < 0.002:
            return base
    return None


async def _comparison_closes(session: AsyncSession, instrument_ids: List[int], max_date: str) -> Dict[int, List[tuple]]:
    """{instrument_id: [(ngày, giá đóng cửa)] mới → cũ} của 21 ngày trước `max_date` — để tính Δ tuyệt đối."""
    start = (date.fromisoformat(max_date) - timedelta(days=21)).isoformat()
    rows = (await session.execute(
        select(Price.instrument_id, Price.price_date, Price.close_price)
        .where(Price.instrument_id.in_(instrument_ids), Price.price_date < max_date, Price.price_date >= start)
        .order_by(desc(Price.price_date))
    )).all()
    out: Dict[int, List[tuple]] = {}
    for iid, d, c in rows:
        out.setdefault(iid, []).append((d, c))
    return out


def report_data_date(report_date_str: str) -> str:
    """report_date (ngày TẠO/phát hành báo cáo, lưu trong DB và hiển thị trên UI)
    -> ngày DỮ LIỆU của báo cáo = report_date - 1 ngày.

    Báo cáo sinh lúc 07:00 ngày T được lưu report_date = T, nhưng vẫn dùng giá
    phiên đóng cửa <= T-1 và tin tức crawl trong khung 07:00 (VN) T-1 → 07:00
    (VN) T (xem get_news_for_report) — tức mọi logic lấy dữ liệu bên dưới vẫn
    chạy theo ngày dữ liệu T-1 như trước, chỉ đổi ngày LƯU của báo cáo.
    Quote Chat (services/quote_chat.py, services/retrieval.py) cũng quy đổi
    qua hàm này để tra đúng dữ liệu của báo cáo đang xem.
    """
    return (datetime.strptime(report_date_str, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()


# report_date Chủ Nhật / Thứ Hai = ngày dữ liệu Thứ Bảy / Chủ Nhật (xem report_data_date)
# — không có phiên giao dịch nên báo cáo CHỈ phân tích tin tức: không bảng giá, biểu đồ,
# động lực thị trường, ghi chú giá, kịch bản giao dịch; không đưa giá vào bất kỳ prompt nào.
NO_PRICE_REPORT_WEEKDAYS = (6, 0)  # date.weekday(): 6 = Chủ Nhật, 0 = Thứ Hai
NO_PRICE_NOTICE = (
    "Ngày dữ liệu {data_date} là cuối tuần, không có phiên giao dịch — báo cáo hôm nay "
    "chỉ phân tích tin tức, không phân tích giá."
)


def is_no_price_report(report_date_str: str) -> bool:
    """True nếu báo cáo `report_date_str` (ngày TẠO) rơi vào Chủ Nhật/Thứ Hai."""
    return datetime.strptime(report_date_str, "%Y-%m-%d").weekday() in NO_PRICE_REPORT_WEEKDAYS


async def get_prices_for_report(session: AsyncSession, target_date_str: str) -> tuple[List[Dict], str]:
    """Lấy dữ liệu giá của ngày gần nhất có dữ liệu (<= target_date_str)."""
    # Tìm ngày gần nhất có dữ liệu
    # Loại CBAM khỏi phép tìm "ngày gần nhất": giá CBAM chốt theo quý, có thể mang ngày không
    # có phiên (cuối tuần) và sẽ kéo max_date lệch khỏi ngày giá thật của các mã còn lại.
    max_date_stmt = (
        select(func.max(Price.price_date))
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date <= target_date_str, Instrument.code != CBAM_CODE)
    )
    max_date = await session.scalar(max_date_stmt)

    if not max_date:
        return [], None

    stmt = (
        select(Price, Instrument)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date == max_date, Instrument.code != CBAM_CODE)
    )
    result = await session.execute(stmt)
    rows = result.all()

    barchart_symbols = await _get_barchart_symbols(session)
    base_closes = await _comparison_closes(session, [instrument.id for _, instrument in rows], max_date)

    prices = []
    for price, instrument in rows:
        history = base_closes.get(instrument.id, [])
        day_base = _matching_base(price.close_price, price.day_change_pct, history[:1])
        week_base = _matching_base(price.close_price, price.week_change_pct, [
            (d, c) for d, c in history if (date.fromisoformat(max_date) - date.fromisoformat(d)).days >= 7
        ][:1])
        is_up = price.day_change_pct is not None and price.day_change_pct > 0
        # Ghi chú bất thường: volume đột biến hoặc note thủ công
        note = price.note or ""
        symbol = barchart_symbols.get(instrument.code)
        prices.append({
            "name": instrument.name,
            "code": instrument.code,
            "price": f"{price.close_price:,.4f} {instrument.unit}",
            "dday": _format_pct_with_abs(price.day_change_pct, price.close_price, day_base),
            "dweek": _format_pct_with_abs(price.week_change_pct, price.close_price, week_base),
            "up": is_up,
            "note": note,
            "close": price.close_price,
            "day_change_pct": price.day_change_pct,
            "week_change_pct": price.week_change_pct,
            "volume": price.volume,
            "category": instrument.category,
            "source_url": _barchart_url(symbol) if symbol else None,
            "no_trade": _is_no_trade_session(price),
        })

    # EUA lũy kế quý — giá CBAM 2026 chốt theo quý = VWAP đấu giá EEX của quý, nên điều kiện liên
    # quan chi phí CBAM so với số này chứ không với giá đóng cửa 1 phiên (biz_memory.check_price_rule).
    # Ưu tiên VWAP đấu giá thật (eua_auctions); chưa có dữ liệu đấu giá thì lùi về TB giá đóng cửa
    # futures — chỉ là XẤP XỈ, qtd_basis ghi rõ để prompt/nhãn không trình bày như giá CBAM thật.
    eua = next((p for p in prices if p["code"] == "EUA"), None)
    if eua is not None:
        qtd_avg, qtd_sessions = await cbam_estimate.quarter_to_date_auction_vwap(session, max_date)
        qtd_basis = "auction_vwap"
        if qtd_avg is None:
            qtd_avg, qtd_sessions = await _get_quarter_to_date_avg(session, "EUA", max_date)
            qtd_basis = "futures_close"
        if qtd_avg is not None:
            eua["qtd_avg"] = qtd_avg
            eua["qtd_sessions"] = qtd_sessions
            eua["qtd_basis"] = qtd_basis

    # Ưu tiên giá CBAM đã lưu DB (as-of target date); chưa có dòng nào (chưa chạy crawl lần đầu)
    # thì fallback fetch trực tiếp như trước.
    cbam_price = await _get_stored_cbam_price(session, target_date_str) or await _fetch_cbam_price()
    if cbam_price:
        # Chèn ngay sau EUA trong bảng giá thay vì luôn để cuối danh sách —
        # CBAM và EUA cùng nhóm "carbon", đặt cạnh nhau dễ so sánh hơn.
        eua_idx = next((i for i, p in enumerate(prices) if p["code"] == "EUA"), None)
        if eua_idx is not None:
            prices.insert(eua_idx + 1, cbam_price)
        else:
            prices.append(cbam_price)

    return prices, max_date


def _is_no_trade_session(price: Any) -> bool:
    """Phiên không có giao dịch thật: mở = cao = thấp = đóng (giá danh nghĩa/settlement mang sang),
    kèm khối lượng 0 hoặc không có. Δ ngày của phiên này không phải biến động thị trường."""
    ohlc = (price.open_price, price.high_price, price.low_price, price.close_price)
    if any(v is None for v in ohlc):
        return False
    return len(set(ohlc)) == 1 and not price.volume


def quarter_start(date_str: str) -> str:
    """'2026-11-14' -> '2026-10-01' (ngày đầu quý chứa ngày đó)."""
    d = date.fromisoformat(date_str)
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1).isoformat()


async def _get_quarter_to_date_avg(
    session: AsyncSession, code: str, as_of: str
) -> tuple[Optional[float], int]:
    """Trung bình giá đóng cửa của `code` từ đầu quý tới `as_of` (gồm cả ngày đó)."""
    row = (await session.execute(
        select(func.avg(Price.close_price), func.count(Price.id))
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(
            Instrument.code == code,
            Price.price_date >= quarter_start(as_of),
            Price.price_date <= as_of,
            Price.close_price.isnot(None),
        )
    )).one()
    avg, count = row
    return (round(float(avg), 2) if avg is not None else None), int(count or 0)


async def find_missing_price_codes(session: AsyncSession, target_date_str: str) -> List[str]:
    """Mã đang is_active trong price_crawl_sources nhưng KHÔNG có dòng giá nào ở đúng ngày
    `max(price_date) <= target_date_str` mà báo cáo sẽ dùng (cùng ngày với
    get_prices_for_report). Dùng để phát hiện đợt crawl giá lỗi từng phần trước khi sinh báo cáo."""
    max_date = await session.scalar(
        select(func.max(Price.price_date))
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date <= target_date_str, Instrument.code != CBAM_CODE)
    )
    if not max_date:
        return []
    active_codes = (
        await session.scalars(
            select(PriceCrawlSource.instrument_code).where(PriceCrawlSource.is_active.is_(True))
        )
    ).all()
    have_codes = set(
        (
            await session.scalars(
                select(Instrument.code)
                .join(Price, Price.instrument_id == Instrument.id)
                .where(Price.price_date == max_date)
            )
        ).all()
    )
    return sorted(c for c in active_codes if c != CBAM_CODE and c not in have_codes)


async def get_historical_ohlc_for_report(
    session: AsyncSession, instrument_code: str, target_date_str: str, limit: int = 30
) -> List[Dict]:
    """Lấy dữ liệu OHLC của `limit` ngày gần nhất (mặc định 30, đúng cỡ biểu đồ
    Mục 2). `limit` lớn hơn dùng bởi services/quote_chat.py::_tool_eua_volume_history_text
    để có thêm phiên đệm phía trước, tính TB khối lượng cho cả những phiên cũ
    nhất trong 30 phiên hiển thị (xem `_eua_volume_history_text`) — KHÔNG đổi
    hành vi ở đây, các lời gọi khác vẫn dùng mặc định 30."""
    stmt = (
        select(Price)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(
            and_(
                Instrument.code == instrument_code,
                Price.price_date <= target_date_str
            )
        )
        .order_by(desc(Price.price_date))
        .limit(limit)
    )
    result = await session.execute(stmt)
    prices = result.scalars().all()

    # Nguồn của mỗi phiên giá — dùng chung symbol Barchart của cả instrument
    # (không đổi theo ngày) để dựng link, giống cách get_prices_for_report() làm
    # cho bảng giá nhanh.
    barchart_symbols = await _get_barchart_symbols(session)
    symbol = barchart_symbols.get(instrument_code)
    source_url = _barchart_url(symbol) if symbol else None

    chart_data = []
    for p in reversed(prices):
        open_p = p.open_price if p.open_price is not None else p.close_price
        high_p = p.high_price if p.high_price is not None else p.close_price
        low_p = p.low_price if p.low_price is not None else p.close_price
        chart_data.append({
            "date": p.price_date,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": p.close_price,
            "volume": p.volume,
            "source_name": p.source_name,
            "source_url": source_url,
        })
    return chart_data


# Bài có ngày đăng cũ hơn đầu khung tin quá số ngày này KHÔNG đưa vào báo cáo dù vừa crawl
# (vd bài đăng lại/kết quả search cũ 5 tháng-2 năm vẫn lọt vào crawl) — báo cáo ngày chỉ dùng tin mới.
STALE_NEWS_MAX_AGE_DAYS = 3


def _host(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def news_source_label(source: str, url: str) -> str:
    """Nhãn nguồn hiển thị = NHÀ XUẤT BẢN thật (domain của URL). Nguồn cấu hình crawl khác domain
    (vd crawl Bloomberg qua bản đăng lại trên Politico/Business Standard...) chỉ là KÊNH THU TIN của hệ
    thống, không phải nguồn tin → KHÔNG ghi kèm (trước đây "politico.com (theo bloomberg.com)" khiến người
    đọc tưởng Bloomberg là nguồn gốc). Nguồn gốc thật do bài dẫn lại (FT, Reuters...) được ghi thành chuỗi
    "Gốc (gốc) → nơi đăng" bởi `source_chain_label` từ tóm tắt Mục 6."""
    host = _host(url)
    src = (source or "").lower()
    src = src[4:] if src.startswith("www.") else src
    if not host or not src or host == src or host.endswith("." + src):
        return source or host
    return host


def source_chain_label(origin: Optional[str], publisher: str) -> str:
    """"Financial Times" + "politico.com" → "Financial Times (gốc) → politico.com". Không có nguồn gốc
    khác nơi đăng → chỉ nơi đăng."""
    origin = (origin or "").strip()
    if not origin or origin.lower() in publisher.lower() or publisher.lower().split(".")[0] in origin.lower():
        return publisher
    return f"{origin} (gốc) → {publisher}"


def apply_source_chains(node: Any, chain_by_url: Dict[str, str]) -> None:
    """Đổi nhãn nguồn (source_name/source) của MỌI chỗ trích bài có trong `chain_by_url` sang chuỗi
    gốc → nơi đăng — để Tóm tắt, Diễn biến chính, Gợi ý, Mục 6 và Mục 9 ghi cùng 1 nhãn."""
    if isinstance(node, dict):
        url = node.get("source_url") or node.get("url")
        if isinstance(url, str) and url in chain_by_url:
            for key in ("source_name", "source"):
                if isinstance(node.get(key), str) and node[key]:
                    node[key] = chain_by_url[url]
        for v in node.values():
            apply_source_chains(v, chain_by_url)
    elif isinstance(node, list):
        for v in node:
            apply_source_chains(v, chain_by_url)


async def get_news_for_report(session: AsyncSession, target_date_str: str) -> tuple[Dict[str, List[Dict]], List[str]]:
    """Lấy tin tức trong khung 07:00 (VN) ngày báo cáo → 07:00 (VN) ngày hôm sau.

    Khớp với lịch tự động: news_crawl chạy mỗi giờ 08:00–17:00 (nguồn VN theo giờ VN, nguồn
    quốc tế theo giờ New York ≈ 19h–5h VN — đều xong trước 07:00 VN), report cho ngày T được
    auto-generate lúc 07:00 (VN) ngày T+1 — nên tin tức đưa vào báo cáo ngày T là tin
    thu thập từ 07:00 (VN) ngày T đến 07:00 (VN) ngày T+1 (bao gồm cả đợt crawl 06:00
    của ngày T+1, chạy ngay trước khi report được sinh).
    Ví dụ: dữ liệu ngày 23/08 (báo cáo sinh lúc 07:00 ngày 24/08, lưu report_date =
    24/08 — xem report_data_date) lấy tin từ 07:00 ngày 23/08 đến 07:00 ngày 24/08.
    `target_date_str` ở đây là NGÀY DỮ LIỆU, không phải report_date.

    DB lưu UTC, VN = UTC+7 → 07:00 (VN) ngày T chính là 00:00 (UTC) ngày T.
    """
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")

    # 07:00 (VN) ngày T == 00:00 (UTC) ngày T
    start_utc = target_date
    end_utc = start_utc + timedelta(days=1)

    stmt = (
        select(Article)
        .where(
            and_(
                Article.crawled_at >= start_utc,
                Article.crawled_at < end_utc
            )
        )
        .order_by(desc(Article.crawled_at))
        .limit(100)
    )
    result = await session.execute(stmt)
    articles = result.scalars().all()

    news_by_topic: Dict[str, List[Dict]] = {}
    sources: set = set()
    stale_before = start_utc.replace(tzinfo=timezone.utc) - timedelta(days=STALE_NEWS_MAX_AGE_DAYS)
    for article in articles:
        if not article.topic:
            continue
        published = article.published_at
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published is not None and published < stale_before:
            logger.warning(
                "[REPORT] Bỏ tin cũ (đăng %s, crawl %s): %s",
                published.date(), article.crawled_at, article.url,
            )
            continue
        sources.add(article.source)
        # Nhãn topic hiển thị (Mục 6) — TOÀN BỘ topic đã gắn cho bài (tối đa 3,
        # xem NewsTopic), không chỉ topic đang lặp ở vòng for bên dưới.
        topic_labels = [TOPIC_DISPLAY_LABELS.get(t, t) for t in article.topic]
        for topic in article.topic:
            if topic not in news_by_topic:
                news_by_topic[topic] = []
            news_by_topic[topic].append({
                "title": article.title,
                "summary": article.content[:500] + "...",
                "content_excerpt": article.content[:2000],
                "source": news_source_label(article.source, article.url),
                "url": article.url,
                "region": article.region,
                "topics": topic_labels,
                # Ngày đăng bài (fallback ngày crawl) — dùng trích dẫn "(Nguồn, ngày)" ở "Diễn biến chính"
                "published_date": (article.published_at or article.crawled_at).strftime("%d/%m/%Y"),
            })

    return news_by_topic, list(sources)


# ── Theo dõi chính sách ETS/MSR (khối cố định Mục 3) ───────────────────────
# Bản 17/10 bỏ sót tranh luận MSR tại Hội nghị thượng đỉnh EU 15–16/10 (Ý + Séc đề nghị tạm dừng chuyển ~190 triệu hạn ngạch
# vào MSR; Hội đồng chốt lập trường đàm phán MSR) vì tin đăng 05/10 nằm ngoài khung tin 1 ngày. Chính sách nguồn cung là
# động lực chi phối giá 2026 → luôn quét tin chính sách ETS trong ETS_POLICY_LOOKBACK_DAYS ngày, đưa vào Mục 3.
ETS_POLICY_LOOKBACK_DAYS = 21
ETS_POLICY_MAX_ARTICLES = 8
ETS_POLICY_RE = re.compile(
    r"\bMSR\b|Market Stability Reserve|dự trữ bình ổn|ETS review|review of the (?:EU )?ETS|rà soát (?:EU )?ETS|\bETS2\b|"
    r"\bETS 2\b|linear reduction factor|\bLRF\b|frontload|front-load|price corridor|price cap|giá trần|Article 29a|Điều 29a|"
    r"invalidat|vô hiệu hóa|allowance supply|nguồn cung hạn ngạch|cap trajectory|lộ trình cap|backloading|"
    r"suspend[^.]{0,40}(?:ETS|MSR|allowance)|tạm dừng[^.]{0,40}(?:ETS|MSR|hạn ngạch)|"
    r"(?:European Council|Environment Council|Hội đồng (?:châu Âu|EU|Môi trường))[^.]{0,80}(?:ETS|carbon|MSR|hạn ngạch)",
    re.IGNORECASE,
)


async def get_ets_policy_news(session: AsyncSession, target_date_str: str) -> List[Dict]:
    """Tin chính sách nguồn cung/cấu trúc EU ETS (MSR, cap, rà soát ETS, ETS2, đề xuất nới/tạm dừng của nước thành
    viên) trong ETS_POLICY_LOOKBACK_DAYS ngày tới hết khung tin của báo cáo — mới nhất trước, bỏ tiêu đề trùng sự kiện."""
    end_utc = datetime.strptime(target_date_str, "%Y-%m-%d") + timedelta(days=1)
    start_utc = end_utc - timedelta(days=ETS_POLICY_LOOKBACK_DAYS)
    rows = (await session.execute(
        select(Article)
        .where(
            Article.crawled_at >= start_utc, Article.crawled_at < end_utc,
            Article.topic.overlap(["eua_ets", "eu_policy"]),
        )
        .order_by(desc(func.coalesce(Article.published_at, Article.crawled_at)))
        .limit(300)
    )).scalars().all()
    out: List[Dict] = []
    for a in rows:
        if not ETS_POLICY_RE.search(f"{a.title or ''}\n{(a.content or '')[:4000]}"):
            continue
        if is_similar_title(title_tokens(a.title or ""), [title_tokens(o["title"]) for o in out]):
            continue
        published = (a.published_at or a.crawled_at)
        out.append({
            "title": a.title or "",
            "summary": (a.content or "")[:700] + "...",
            "content_excerpt": (a.content or "")[:2000],
            "source": news_source_label(a.source, a.url),
            "url": a.url,
            "region": a.region,
            "topics": [TOPIC_DISPLAY_LABELS.get(t, t) for t in a.topic or []],
            "published_date": published.strftime("%d/%m/%Y"),
            "policy_tracker": True,
        })
        if len(out) >= ETS_POLICY_MAX_ARTICLES:
            break
    return out


def _merge_policy_articles(index_lookup: Dict[int, Dict], policy_articles: List[Dict]) -> List[int]:
    """Gắn bài chính sách vào bảng [N] của Mục 3 (tiếp số sau bài trong ngày; bài đã có thì dùng lại số cũ).
    -> danh sách số [N] của các bài chính sách (theo thứ tự mới nhất trước)."""
    by_url = {a["url"]: n for n, a in index_lookup.items()}
    nxt = max(index_lookup, default=0)
    nums = []
    for a in policy_articles:
        n = by_url.get(a["url"])
        if n is None:
            nxt += 1
            n = nxt
            index_lookup[n] = a
            by_url[a["url"]] = n
        nums.append(n)
    return nums


ETS_POLICY_IMPACTS = ("tăng", "giảm", "trung lập")


def _ets_policy_prompt_block(nums: List[int], index_lookup: Dict[int, Dict]) -> str:
    """Khối thêm vào cuối prompt Mục 3 (cả bản có giá lẫn bản chỉ tin tức)."""
    if nums:
        listing = "\n".join(
            f"[{n}] [{index_lookup[n]['source']}, đăng {index_lookup[n]['published_date']}] {index_lookup[n]['title']}\n"
            f"   Tóm tắt: {index_lookup[n]['summary']}"
            for n in nums
        )
    else:
        listing = f"(Không có tin chính sách ETS/MSR nào trong {ETS_POLICY_LOOKBACK_DAYS} ngày qua.)"
    return f"""

THEO DÕI CHÍNH SÁCH ETS/MSR — TIN {ETS_POLICY_LOOKBACK_DAYS} NGÀY QUA (khối CỐ ĐỊNH; chính sách nguồn cung là động lực chi phối giá
EUA 2026, mạnh hơn nhiên liệu — dùng chung số [N] với TIN TỨC LIÊN QUAN, trích dẫn như các tin khác):
{listing}

THÊM trường "ets_policy" ở cấp mục 3 (BẮT BUỘC khi khối trên có tin; khối trống → "ets_policy": []): mảng tối đa 5 hồ sơ chính
sách ĐANG MỞ, mỗi hồ sơ 1 object (gộp nhiều bài cùng hồ sơ thành 1, dùng bài MỚI NHẤT làm nguồn):
  - "item": tên hồ sơ ngắn (vd "Sửa đổi MSR — ngưỡng vô hiệu hóa", "Đề nghị tạm dừng chuyển hạn ngạch vào MSR (Ý, Séc)", "Rà soát EU ETS 2026", "ETS2");
  - "status": lập trường/tiến độ HIỆN TẠI của từng cơ quan có trong bài (Ủy ban / Hội đồng / Nghị viện / nước thành viên) — chỉ ghi điều bài nêu, số liệu (triệu hạn ngạch, %, mốc ngày) phải có trong bài;
  - "next_step": bước kế tiếp + mốc thời gian nếu bài nêu (vd "đàm phán ba bên, mục tiêu xong trước cuối 2026"); bài không nêu → "Chưa có lịch xác nhận";
  - "impact": chiều tác động lên NGUỒN CUNG → giá EUA nếu được thông qua: "tăng" (siết cung) / "giảm" (nới cung) / "trung lập" (chưa rõ);
  - "source_index": số [N] có thật.
QUY TẮC: (1) hồ sơ đang chờ quyết định trong khung kịch bản PHẢI có 1 watchpoint riêng ở "Cần theo dõi" (ghi "chưa có kết quả" nếu bài
chưa xác nhận); (2) điều kiện kịch bản dạng "không có quyết định chính sách EU mới về nguồn cung" PHẢI nêu ĐÚNG hồ sơ cụ thể ở đây;
(3) nhóm "Chính sách:" ở "Phân tích" PHẢI đánh giá các hồ sơ này (không chỉ tin trong ngày) — tin cũ hơn khung ngày thì ghi rõ ngày đăng;
(4) KHÔNG suy đoán kết luận hội nghị chưa có trong bài."""


def _resolve_ets_policy(items: Any, index_lookup: Dict[int, Dict]) -> List[Dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not str(it.get("item") or "").strip():
            continue
        art = index_lookup.get(_first_source_index(it.get("source_index")))
        if not art:
            logger.warning("[REPORT] Mục 3: hồ sơ chính sách ETS không có nguồn hợp lệ — bỏ: %s", str(it.get("item"))[:100])
            continue
        impact = str(it.get("impact") or "").strip().lower()
        out.append({
            "item": str(it["item"]).strip(),
            "status": str(it.get("status") or "").strip(),
            "next_step": str(it.get("next_step") or "").strip() or "Chưa có lịch xác nhận",
            "impact": impact if impact in ETS_POLICY_IMPACTS else "trung lập",
            "source_name": art["source"],
            "source_url": art["url"],
            "source_date": art.get("published_date"),
        })
    return out[:5]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _topics_present(news_by_topic: Dict[str, List[Dict]], section_key: str) -> List[str]:
    """Danh sách topic thuộc SECTION_TOPICS[section_key] mà THỰC SỰ có tin trong
    ngày — dùng để dựng khung phân tích ĐỘNG qua `chains.build_context()`
    (xem `_eua_framework`), chỉ nạp đúng cơ chế liên quan thay vì luôn nạp
    tĩnh toàn bộ khung."""
    return [t for t in SECTION_TOPICS.get(section_key, []) if news_by_topic.get(t)]


def _filter_news_for_section(news_by_topic: Dict[str, List[Dict]], section_key: str) -> str:
    """Lọc tin tức chỉ lấy các topic liên quan đến mục báo cáo."""
    relevant_topics = SECTION_TOPICS.get(section_key, [])
    text_parts = []
    for topic in relevant_topics:
        articles = news_by_topic.get(topic, [])
        if not articles:
            continue
        text_parts.append(f"\n--- TOPIC: {topic.upper()} ---")
        for i, art in enumerate(articles[:4]):  # tối đa 4 tin/topic để tiết kiệm token
            text_parts.append(
                f"{i+1}. [{art['source']}, đăng {art['published_date']}] {art['title']}\n   Tóm tắt: {art['summary']}"
            )
    return "\n".join(text_parts) if text_parts else "Không có tin tức liên quan."


def _filter_news_with_index(
    news_by_topic: Dict[str, List[Dict]], section_key: str, max_articles: int = 10, per_topic: Optional[int] = None,
) -> tuple[str, Dict[int, Dict]]:
    """Giống _filter_news_for_section nhưng đánh số [N] cho từng bài (dedup theo
    url) và trả kèm bảng tra N -> bài viết gốc — dùng cho các mục cần trích dẫn
    nguồn có thể bấm link (Mục 7). LLM chỉ được chọn số thứ tự có sẵn, backend
    tự map sang URL thật — tránh để LLM tự bịa URL không tồn tại.
    """
    relevant_topics = SECTION_TOPICS.get(section_key, [])
    seen_urls: set = set()
    numbered: List[Dict] = []
    for topic in relevant_topics:
        taken = 0
        for art in news_by_topic.get(topic, []):
            if per_topic is not None and taken >= per_topic:
                break
            if art["url"] in seen_urls:
                continue
            seen_urls.add(art["url"])
            numbered.append(art)
            taken += 1
            if len(numbered) >= max_articles:
                break
        if len(numbered) >= max_articles:
            break

    if not numbered:
        return "Không có tin tức liên quan.", {}

    index_lookup = {i + 1: art for i, art in enumerate(numbered)}
    lines = [
        f"[{i}] [{art['source']}, đăng {art['published_date']}] {art['title']}\n   Tóm tắt: {art['summary']}"
        for i, art in index_lookup.items()
    ]
    return "\n".join(lines), index_lookup



_CITE_RE = re.compile(r"\s*\[(\d{1,2}|giá)\]")
_WATCH_LINE_RE = re.compile(r"^\s*\d+[.)]\s*")
WATCH_HEADING = "Cần theo dõi"


def _resolve_section3_citations(sec3: Dict[str, Any], index_lookup: Dict[int, Dict]) -> List[Dict[str, str]]:
    """Đổi dấu trích "[N]" trong văn bản Mục 3 thành "(Nguồn: <nhà xuất bản>)", trả danh sách nguồn đã dùng
    (để có mặt ở Mục 9). Watchpoint "Cần theo dõi" không có [N] hợp lệ hay [giá] bị XOÁ và đánh số lại."""
    used: Dict[str, Dict[str, str]] = {}

    def _sub(text: str) -> tuple:
        cited = False

        def _one(m: re.Match) -> str:
            nonlocal cited
            if m.group(1) == "giá":
                cited = True
                return ""
            art = index_lookup.get(int(m.group(1)))
            if not art:
                return ""
            cited = True
            used[art["url"]] = {"source_name": art["source"], "source_url": art["url"]}
            return f" (Nguồn: {art['source']})"

        out = _CITE_RE.sub(_one, text)
        out = re.sub(r"(\(Nguồn: [^)]+\))(\s*\(Nguồn: ([^)]+)\))+", lambda m: m.group(0).replace(") (Nguồn: ", "; ").replace(")(Nguồn: ", "; "), out)
        return out, cited

    for blk in sec3.get("analysis_blocks") or []:
        if not isinstance(blk, dict) or not isinstance(blk.get("content"), str):
            continue
        if str(blk.get("heading") or "").strip() != WATCH_HEADING:
            blk["content"] = _sub(blk["content"])[0]
            continue
        kept: List[str] = []
        n = 0
        for line in blk["content"].split("\n"):
            new, cited = _sub(line)
            if _WATCH_LINE_RE.match(line):
                if not cited:
                    logger.warning("[REPORT] Mục 3: bỏ watchpoint không có nguồn — %s", line.strip()[:160])
                    continue
                n += 1
                new = _WATCH_LINE_RE.sub(f"{n}. ", new, count=1)
            kept.append(new)
        blk["content"] = "\n".join(kept).strip()
    if isinstance(sec3.get("vs_previous"), str):
        sec3["vs_previous"] = _sub(sec3["vs_previous"])[0]
    return list(used.values())


HEDGE_ACTIONS = ("increase", "hold", "reduce")


def hedge_core_text(cbam_est: Optional[Dict[str, Any]]) -> str:
    """Câu phần LÕI phòng hộ (Python dựng — không để LLM tự đặt): rủi ro + cấu trúc khóa chi phí bằng futures
    (mua trước, bán rải theo lịch đấu giá còn lại) — xem cbam_estimate.hedge_structure_text."""
    if not cbam_est:
        return ""
    if cbam_est.get("open_share_pct") is None:  # ước tính lưu từ bản cũ, chưa có các trường % theo khối lượng quý
        open_pct = 100 - float(cbam_est.get("fixed_share_pct") or 0)
        core = 100 - int(cbam_est.get("tactical_max_pct") or cbam_estimate.TACTICAL_MAX_PCT)
        cbam_est = {**cbam_est, "open_share_pct": open_pct, "core_share_of_quarter_pct": open_pct * core / 100}
    return cbam_estimate.hedge_structure_text(cbam_est)


def _finalize_hedge_fields(
    sec3: Dict[str, Any], open_trade_list: List[Dict[str, Any]], cbam_est: Optional[Dict[str, Any]] = None,
) -> None:
    """1 nhãn phòng hộ duy nhất ("hedge_action", nhãn = tín hiệu thẻ CBAM do Python tính) + đối chiếu lệnh đang mở."""
    for sc in sec3.get("trading_scenarios") or []:
        if isinstance(sc, dict):
            sc.pop("hedge_note", None)  # hàm ý phòng hộ theo từng khung từng tạo 4 tín hiệu trái nhau
    ha = sec3.get("hedge_action") if isinstance(sec3.get("hedge_action"), dict) else {}
    action = (cbam_est or {}).get("signal") or ha.get("action")
    if ha.get("action") and cbam_est and ha.get("action") != action:
        logger.warning("[REPORT] Mục 3: LLM đặt nhãn phòng hộ %r khác nhãn hệ thống %r — dùng nhãn hệ thống.", ha.get("action"), action)
    if action in HEDGE_ACTIONS:
        sec3["hedge_action"] = {
            "action": action,
            "core": hedge_core_text(cbam_est),
            "text": str(ha.get("text") or "").strip(),
            "tactical_max_pct": (cbam_est or {}).get("tactical_max_pct", cbam_estimate.TACTICAL_MAX_PCT),
        }
    else:
        logger.warning("[REPORT] Mục 3: không có nhãn phòng hộ (thiếu ước tính CBAM và hedge_action): %r", ha)
        sec3.pop("hedge_action", None)
    sec3["open_positions"] = forecast_review.merge_open_positions(open_trade_list, sec3.get("open_positions"))
    for pos in sec3["open_positions"]:
        if pos["decision"] is None:
            logger.warning("[REPORT] Mục 3: lệnh đang mở của báo cáo %s chưa được đối chiếu.", pos["report_date"])


def _first_source_index(raw: Any) -> Optional[int]:
    """Chuẩn hoá "source_index" LLM trả về thành 1 int hợp lệ hoặc None, PHÒNG
    TRƯỜNG HỢP LLM lỡ trả về dạng khác spec (vd 1 list số [1, 2] thay vì 1 số
    duy nhất/null) — dict.get() với key là list sẽ raise "TypeError: unhashable
    type: 'list'" nếu dùng thẳng, làm hỏng cả lần sinh báo cáo. Lấy phần tử ĐẦU
    TIÊN nếu là list, bỏ qua nếu không phải int hợp lệ (kể cả bool, vì bool là
    subclass của int trong Python nhưng không phải giá trị source_index thật)."""
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _resolve_bullet_sources(items: Optional[List[Any]], index_lookup: Dict[int, Dict]) -> List[Dict]:
    """Chuẩn hoá mảng "bullets" (mỗi phần tử {"text", "source_index"} do LLM trả
    về, dùng chung cơ chế đánh số [N] với Mục 2/7 — LLM chỉ chọn số có thật,
    backend tự map sang tên/URL nguồn thật, tránh bịa nguồn) thành
    {"text", "source_name", "source_url"} để hiển thị nguồn cạnh mỗi bullet,
    giống cách Mục 6 hiện "Nguồn: {source}". Chấp nhận cả string thô (fallback
    khi LLM lỗi) — trả về không kèm nguồn cho trường hợp đó."""
    resolved = []
    for it in items or []:
        if isinstance(it, str):
            resolved.append({"text": it, "source_name": None, "source_url": None})
            continue
        src_art = index_lookup.get(_first_source_index(it.get("source_index")))
        resolved.append({
            "text": it.get("text", ""),
            "source_name": src_art["source"] if src_art else None,
            "source_url": src_art["url"] if src_art else None,
        })
    return resolved


def _collect_cited_articles(news_by_topic: Dict[str, List[Dict]], limit: int = 40) -> List[Dict]:
    """Danh sách bài viết thật (title/source/url) đã đưa vào các prompt — dùng
    cho Mục 9 để trích dẫn URL cụ thể thay vì chỉ tên domain, dedup theo url."""
    seen_urls: set = set()
    articles: List[Dict] = []
    for topic_articles in news_by_topic.values():
        for art in topic_articles:
            if art["url"] in seen_urls:
                continue
            seen_urls.add(art["url"])
            articles.append(art)
    return articles[:limit]


def _build_section6_news(news_by_topic: Dict[str, List[Dict]], limit_per_region: int = 30) -> Dict[str, List[Dict]]:
    """Gom toàn bộ tin tức đã crawl trong ngày, dedup theo url, tách theo
    region ('vietnam' / 'international') cho Mục 6 — mỗi tin giữ title/summary/
    source/url để hiển thị dạng danh sách có thể bấm link tới bài gốc."""
    articles = _dedupe_similar_articles(_collect_cited_articles(news_by_topic, limit=1000))
    international = [a for a in articles if a.get("region") != "vietnam"][:limit_per_region]
    vietnam = [a for a in articles if a.get("region") == "vietnam"][:limit_per_region]
    return {"international": international, "vietnam": vietnam}


def _content_source_urls(node: Any) -> set:
    """Mọi source_url trong nội dung báo cáo (đệ quy)."""
    out: set = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "source_url" and isinstance(v, str):
                out.add(v)
            else:
                out |= _content_source_urls(v)
    elif isinstance(node, list):
        for v in node:
            out |= _content_source_urls(v)
    return out


_VN_SUBJECT_RE = re.compile(
    r"Việt Nam|\bVN\b|(?i:tỉnh)|Hà Nội|TP\.?\s?HCM|Hồ Chí Minh|Bộ (?:Công Thương|Tài nguyên|TN&MT|Nông nghiệp|Tài chính)|"
    r"EVN|Petrovietnam|PVN"
)


def _split_section6_by_subject(articles: List[Dict]) -> tuple:
    """Nhóm VIỆT NAM / QUỐC TẾ của Mục 6 theo quốc gia của CHỦ ĐỀ bài (LLM tóm tắt trả "about_vietnam"), không
    theo region của nguồn crawl — tin phí quỹ khí của Đức đăng trên cafef.vn từng bị xếp vào "VIỆT NAM".
    LLM không trả cờ → giữ region của nguồn."""
    international, vietnam = [], []
    for a in articles:
        flag = a.pop("about_vietnam", None)
        topics = set(a.get("topics") or [])
        if topics & {"vietnam_carbon_policy", TOPIC_DISPLAY_LABELS["vietnam_carbon_policy"]}:
            is_vn = True  # tin đã phân loại chính sách carbon VN — không để LLM tóm tắt đẩy sang Quốc tế
        elif a.get("region") == "vietnam" and _VN_SUBJECT_RE.search(f"{a.get('title') or ''} {a.get('summary') or ''}"):
            # Nguồn VN nói về địa phương/cơ quan VN (vd tỉnh Nghệ An) — giữ nhóm Việt Nam dù LLM trả false. CHỈ xét tiêu đề
            # + tóm tắt (chủ đề chính): trước quét cả 2.500 ký tự nội dung nên tin Reuters về trừng phạt tàu dầu của Mỹ
            # trên vnexpress (nhắc "Việt Nam" ở đoạn cuối) bị xếp vào nhóm Việt Nam.
            is_vn = True
        else:
            is_vn = flag if isinstance(flag, bool) else a.get("region") == "vietnam"
        (vietnam if is_vn else international).append(a)
    return international, vietnam


def _prompt_section6_summary(article: Dict, target_date: str) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường carbon & năng lượng châu Âu.
Ngày báo cáo: {target_date}

BÀI VIẾT CẦN TÓM TẮT (CHỈ bài này, không liên quan bài nào khác):
Nguồn: {article['source']}
Tiêu đề: {article['title']}
Nội dung (trích): {article.get('content_excerpt') or article['summary']}

YÊU CẦU: Viết đúng 1 đoạn tóm tắt bằng TIẾNG VIỆT, ĐÚNG 2 CÂU (không hơn) CHO RIÊNG bài viết này, ngắn gọn tối đa, không dài dòng:
- Câu 1: mô tả ngắn gọn sự kiện/nội dung chính của bài (fact, số liệu nếu bài có nêu) — TUYỆT ĐỐI KHÔNG bịa thêm thông tin ngoài nội dung đã cho, KHÔNG trộn với thông tin của bài viết khác.
- Câu 2: CHỈ nêu tác động tới thị trường carbon/năng lượng châu Âu hoặc giá EUA khi CHÍNH BÀI nêu tác động đó. KHÔNG tự suy diễn/gán liên hệ với EUA (cấm kiểu "giảm áp lực cạnh tranh... đối với carbon allowances", "EUA sẽ tăng trong các năm tới" nếu bài không nói). Bài không nêu tác động → Câu 2 là 1 fact khác của bài (vẫn đúng 2 câu).
- Thuật ngữ chuẩn: EUA = hạn ngạch phát thải EU (EU Allowance); open interest = lượng vị thế mở; carbon allowances = hạn ngạch carbon; spread giữ nguyên "spread". CHỈ dùng tiếng Việt và ký tự Latin — TUYỆT ĐỐI KHÔNG chèn chữ Hán/Nhật/Hàn.
- Nếu bài viết bằng tiếng Anh hoặc ngôn ngữ khác: dịch ý sang tiếng Việt tự nhiên, không dịch máy móc từng từ.
- Văn phong khách quan. Mỗi câu ngắn, đi thẳng vào trọng tâm. KHÔNG dùng markdown (không **, không gạch đầu dòng).
- Tuyên bố/số liệu do 1 bên nêu (quan chức, công ty, nhà phân tích) PHẢI ghi rõ chủ thể phát ngôn đúng như bài ("theo Phó Thủ tướng...", "đặc phái viên...") — KHÔNG viết như sự thật đã xác nhận. Giữ nguyên chức danh đúng như bài, không tự đổi.
- KHÔNG chép giá/%Δ của Brent, WTI, khí, EUA... nêu trong bài (giá trong bài là giá tại 1 thời điểm khác, sẽ lệch với Bảng giá nhanh của báo cáo) — chỉ nêu giá nếu đó là nội dung chính của sự kiện (vd mức trần giá, giá đấu giá).
- Viết đúng chính tả tiếng Việt.
- Giữ ĐÚNG mức độ của bài: bài/cơ quan chính thức nói "quy mô hạn chế", "hoạt động bình thường", "không ảnh hưởng nguồn cung" → PHẢI nêu lời đó, KHÔNG viết "có nguy cơ ảnh hưởng cung toàn cầu". Sự kiện xảy ra trước ngày báo cáo → ghi ngày diễn ra.
- "relevant": false nếu bài KHÔNG có diễn biến mới (vd bài giới thiệu/nhắc lại dự án đã vận hành từ nhiều năm trước, bài tổng quan/bối cảnh không có sự kiện, số liệu hay quyết định mới).
- "relevant": true CHỈ KHI bài liên quan TRỰC TIẾP tới thị trường carbon (EU ETS/EUA, CBAM, VCM, chính sách carbon) hoặc năng lượng (gas, điện, than, dầu, tái tạo, hydrogen) VÀ nêu rõ tác động tới cung/cầu/giá/chính sách. Tác động không rõ ràng, phải suy đoán, hoặc chỉ là bối cảnh chính trị/kinh tế chung → false (vẫn viết "summary" như bình thường).

- "origin": hãng tin/cơ quan GỐC của thông tin chính khi bài DẪN LẠI rõ ràng từ nơi khác (vd bài Politico viết "Zelenskyy told the Financial Times" → "Financial Times"; "according to a Reuters report" → "Reuters"; số liệu từ thông cáo IEA → "IEA"); trong "summary" cũng ghi theo chuỗi đó ("theo phỏng vấn với Financial Times..."). Bài tự đưa tin/phỏng vấn trực tiếp hoặc chỉ trích dẫn ngắn nhiều nguồn → null. KHÔNG đoán.

- "about_vietnam": true CHỈ KHI chủ thể/sự kiện chính của bài diễn ra tại hoặc trực tiếp liên quan Việt Nam (chính sách, doanh nghiệp, thị trường Việt Nam; doanh nghiệp Việt chịu CBAM...). Xét theo NỘI DUNG, KHÔNG theo trang đăng: bài trên báo Việt Nam nói về phí quỹ khí của Đức → false.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"relevant": true, "summary": "...", "origin": null, "about_vietnam": <true hoặc false theo quy tắc trên>}}"""


SECTION6_SUMMARY_CONCURRENCY = 4


async def _summarize_section6_articles(
    articles: List[Dict], target_date: str, concurrency: int = SECTION6_SUMMARY_CONCURRENCY,
    keep_urls: Optional[set] = None,
) -> List[Dict]:
    """Sinh tóm tắt bằng LLM CHO RIÊNG TỪNG bài đã lọt vào Mục 6 — mỗi bài 1
    lần gọi LLM ĐỘC LẬP, prompt CHỈ chứa đúng 1 bài (không gộp nhiều bài vào
    chung 1 prompt) để đảm bảo tóm tắt của bài nào chỉ dựa trên đúng nội dung
    bài đó, không bị trộn/tổng hợp chéo với các bài khác. Các lệnh gọi này độc
    lập với nhau nên chạy song song có giới hạn (Semaphore) thay vì tuần tự
    từng bài — giảm đáng kể thời gian sinh báo cáo khi Mục 6 có tới 60 bài, mà
    vẫn không vi phạm yêu cầu "mỗi bài 1 prompt riêng". Chỉ chạy cho các bài đã
    lọt vào Mục 6 (không tóm tắt toàn bộ tin trong ngày — tốn kém không cần thiết).

    Trả về danh sách bài MỚI (không mutate input gốc — các dict này còn được
    share với news_by_topic dùng cho prompt các mục khác) với "summary" đã
    thay bằng bản LLM viết riêng cho bài đó; bài nào LLM lỗi → fallback dùng
    đúng đoạn cắt content gốc của chính bài đó, không ảnh hưởng các bài khác.
    """
    if not articles:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def _summarize_one(art: Dict) -> Optional[Dict]:
        async with sem:
            await asyncio.sleep(1)  # giãn nhịp nhẹ trong mỗi slot, tránh dồn request tức thời
            raw = await _call_llm(
                _prompt_section6_summary(art, target_date),
                model=REPORT_MODEL_HAIKU,
                max_tokens=768,  # tokenizer Haiku 5.5 tốn hơn ~30% token cho cùng văn bản
            )
            parsed = _extract_json(raw) if raw else None
            if parsed and _has_cjk(parsed.get("summary") or ""):
                logger.warning("[REPORT] Mục 6: tóm tắt lẫn ký tự CJK, gọi lại: %s", art["url"])
                raw = await _call_llm(
                    _prompt_section6_summary(art, target_date),
                    model=REPORT_MODEL_HAIKU,
                    max_tokens=768,
                )
                parsed = _extract_json(raw) if raw else None
                if parsed and _has_cjk(parsed.get("summary") or ""):
                    parsed = None  # vẫn lỗi → rơi xuống fallback
            summary = (parsed.get("summary") or "").strip() if parsed else ""

            if parsed and parsed.get("relevant") is False and summary and art["url"] not in (keep_urls or set()):
                logger.info("[REPORT] Mục 6: loại bài không liên quan: %s", art["url"])
                return None

            if not summary:
                logger.warning("[REPORT] Mục 6: tóm tắt LLM thất bại cho bài %s, dùng fallback.", art["url"])
                summary = art["summary"]

            origin = parsed.get("origin") if parsed and isinstance(parsed.get("origin"), str) else None
            about_vn = parsed.get("about_vietnam") if parsed and isinstance(parsed.get("about_vietnam"), bool) else None
            return {**art, "summary": summary, "origin": (origin or "").strip() or None, "about_vietnam": about_vn}

    summarized = await asyncio.gather(*[_summarize_one(art) for art in articles])
    return [a for a in summarized if a is not None]


def _summarize_prices(prices: List[Dict]) -> str:
    """Tạo dòng tóm tắt số liệu giá để đưa vào prompt."""
    lines = []
    for p in prices:
        if p.get("day_change_pct") is None and p.get("week_change_pct") is None:
            # Giá tham chiếu không có Δ ngày/Δ tuần thật (vd CBAM Certificate — chốt
            # theo quý, không khớp lệnh hàng ngày) — "-" ở "dday"/"dweek" dễ bị hiểu
            # nhầm là có dữ liệu biến động. Đánh dấu rõ NGAY TẠI DÒNG DỮ LIỆU để mọi
            # mục dùng chung hàm này (1, 2, 3, 5) đều chỉ báo giá, không cố phân tích
            # nhân quả/xu hướng cho mã này.
            lines.append(
                f"  - {p['name']} ({p['code']}): {p['price']} — giá tham chiếu, KHÔNG có dữ liệu Δ ngày/Δ tuần "
                f"(chỉ báo cáo giá hiện tại, KHÔNG phân tích biến động/nhân quả cho mã này)."
            )
        else:
            # Số viết kiểu tiếng Việt (1.386,75) — LLM chép nguyên số vào văn bản, để kiểu "85.84" thì báo
            # cáo lẫn 2 kiểu dấu thập phân.
            unit = " ".join(str(p.get("price") or "").split(" ")[1:])
            price_vn = f"{_vn_num(p['close'])} {unit}".strip() if isinstance(p.get("close"), (int, float)) else p["price"]
            dday, dweek = (re.sub(r"(\d)\.(\d)", r"\1,\2", str(p.get(k) or "-")) for k in ("dday", "dweek"))
            line = f"  - {p['name']} ({p['code']}): {price_vn} | Δ ngày {dday} | Δ tuần {dweek}"
            if p.get("no_trade"):
                line += (
                    " | ⚠ phiên KHÔNG có giao dịch (mở = cao = thấp = đóng, khối lượng 0) — giá danh nghĩa: KHÔNG dùng "
                    "Δ ngày của mã này làm căn cứ (không viết \"tăng/nhích lên\"), nếu nhắc phải ghi rõ \"không có giao dịch\""
                )
            if p.get("revision"):
                line += f" | ⚠ {p['revision']}"
            if p.get("qtd_avg") is not None:
                if p.get("qtd_basis") == "auction_vwap":
                    line += (
                        f" | EUA lũy kế quý = VWAP đấu giá EEX ({p.get('qtd_sessions') or 0} phiên — đúng phương pháp EC "
                        f"tính giá CBAM): {_vn_num(p['qtd_avg'])} — dùng số này (không phải giá đóng cửa 1 phiên) khi so với giá CBAM"
                    )
                else:
                    line += (
                        f" | EUA lũy kế quý XẤP XỈ = TB giá đóng cửa futures ({p.get('qtd_sessions') or 0} phiên; chưa có dữ liệu "
                        f"đấu giá EEX — giá CBAM thật tính theo VWAP đấu giá): {_vn_num(p['qtd_avg'])} — khi nhắc PHẢI ghi \"xấp xỉ\""
                    )
            lines.append(line)
    return "\n".join(lines) if lines else "Chưa có dữ liệu giá."


def _eua_trend_summary(chart_data: List[Dict]) -> str:
    """Tóm tắt xu hướng EUA 30 ngày từ OHLC."""
    if not chart_data:
        return "Không có dữ liệu lịch sử EUA."
    first = chart_data[0]["close"]
    last = chart_data[-1]["close"]
    high_30 = max(c["high"] for c in chart_data)
    low_30 = min(c["low"] for c in chart_data)
    change_30 = ((last - first) / first * 100) if first else 0
    return (
        f"EUA 30 ngày: mở {first:.2f} → đóng gần nhất {last:.2f} "
        f"({change_30:+.1f}%); 30-ngày-cao {high_30:.2f}, 30-ngày-thấp {low_30:.2f}. "
        + describe_range_position(last, low_30, high_30, "biên 30 phiên") + " "
        + _eua_streak_summary(chart_data)
    ).strip()


def _eua_session_range_summary(chart_data: List[Dict]) -> str:
    """Biên độ dao động PHIÊN LIỀN TRƯỚC của EUA — tính trực tiếp từ OHLC thật.

    Mục 1 yêu cầu LLM nêu "biên độ biến động trong phiên", nhưng LLM không có
    số liệu intraday nếu không được truyền vào rõ ràng — hàm này tính sẵn để
    tránh LLM tự bịa ra 1 con số biên độ nghe hợp lý.
    """
    if not chart_data:
        return "Không có dữ liệu biên độ phiên liền trước."
    latest = chart_data[-1]
    open_p, high, low, close = latest["open"], latest["high"], latest["low"], latest["close"]
    range_pct = ((high - low) / close * 100) if close else 0
    text = (
        f"Biên độ phiên liền trước ({latest['date']}): mở {open_p:.2f} — cao {high:.2f} — thấp {low:.2f} "
        f"— đóng cửa {close:.2f} EUR/tCO2 (biên độ {high - low:.2f}, ~{range_pct:.1f}% so với giá đóng cửa)."
    )
    return f"{text} {_eua_session_shape(chart_data)}".strip()


SESSION_RANGE_WIDE_RATIO = 1.2    # biên độ phiên ≥ 1,2 × ATR 14 phiên → "rộng hơn bình thường"
SESSION_RANGE_NARROW_RATIO = 0.8  # ≤ 0,8 × ATR → "hẹp hơn bình thường"


def _eua_session_shape(chart_data: List[Dict]) -> str:
    """Nhận định HÌNH DẠNG phiên tính sẵn (so ATR, gap mở cửa, đảo chiều hay đi 1 chiều) — LLM từng viết
    "biên độ rộng hơn thường thấy" khi biên độ 1,50 < ATR 1,67, và "đảo chiều giảm trong phiên" khi phiên mở
    gap giảm, mở = cao rồi đi 1 chiều xuống."""
    if len(chart_data) < 2:
        return ""
    latest, prev = chart_data[-1], chart_data[-2]
    open_p, high, low, close = latest["open"], latest["high"], latest["low"], latest["close"]
    if None in (open_p, high, low, close) or high <= low:
        return ""
    parts = []
    atr = atr_true_range(chart_data[:-1])  # ATR của 14 phiên TRƯỚC phiên này (không lấy chính nó làm chuẩn)
    if atr:
        ratio = (high - low) / atr
        width = ("RỘNG hơn bình thường" if ratio >= SESSION_RANGE_WIDE_RATIO
                 else "HẸP hơn bình thường" if ratio <= SESSION_RANGE_NARROW_RATIO
                 else "xấp xỉ bình thường, nhỏ hơn ATR — KHÔNG được gọi là rộng" if ratio < 1
                 else "xấp xỉ bình thường — không phải đột biến")
        parts.append(f"Biên độ {_vn_num(high - low)} so với ATR 14 phiên trước (True Range) {_vn_num(atr)} → {width} (×{_vn_num(ratio)}).")
    prev_close = prev.get("close")
    sig = session_signals(chart_data)
    if prev_close:
        gap = open_p - prev_close
        if atr and abs(gap) >= 0.1 and abs(gap) < GAP_MIN_ATR * atr:
            parts.append(
                f"Phiên mở lệch {_vn_num(gap, sign=True)} so với đóng cửa trước (= ×{_vn_num(abs(gap) / atr)} ATR, dưới "
                f"{_vn_num(GAP_MIN_ATR)} ATR) → gap NHỎ, là nhiễu: KHÔNG gọi là tín hiệu mua/bán, KHÔNG gắn FACT tín hiệu."
            )
        elif abs(gap) >= 0.1:
            parts.append(
                f"Phiên mở GAP {'TĂNG' if gap > 0 else 'GIẢM'} {_vn_num(abs(gap))} (đóng cửa trước {_vn_num(prev_close)} → mở {_vn_num(open_p)}) "
                f"— gap {'tăng là tín hiệu MUA' if gap > 0 else 'giảm là tín hiệu BÁN'} lúc mở cửa, KHÔNG phải bằng chứng lực "
                f"{'mua' if gap > 0 else 'bán'} cạn dần; chỉ nhận định lực {'mua' if gap > 0 else 'bán'} yếu dần khi biên độ HẸP "
                "VÀ khối lượng THẤP hơn TB (xem khối khối lượng)."
            )
    tick = 0.05 * (high - low)
    if abs(open_p - high) <= tick and close < open_p:
        parts.append("Mở cửa ≈ giá cao nhất rồi đi xuống: giảm MỘT CHIỀU trong phiên — KHÔNG gọi là \"đảo chiều\".")
    elif abs(open_p - low) <= tick and close > open_p:
        parts.append("Mở cửa ≈ giá thấp nhất rồi đi lên: tăng MỘT CHIỀU trong phiên — KHÔNG gọi là \"đảo chiều\".")
    elif close < open_p and high - max(open_p, prev_close or open_p) > 0.3 * (high - low):
        parts.append("Phiên tăng lên đỉnh rồi quay xuống đóng dưới giá mở: ĐẢO CHIỀU GIẢM trong phiên.")
    elif close > open_p and min(open_p, prev_close or open_p) - low > 0.3 * (high - low):
        parts.append("Phiên giảm xuống đáy rồi hồi lên đóng trên giá mở: ĐẢO CHIỀU TĂNG trong phiên.")
    if sig and sig["vol_pct"] is not None:
        side = "mua" if sig["trend"] == "tăng" else "bán"
        if sig["exhausted"]:
            parts.append(
                f"CẠN LỰC: phiên cuối ĐỦ 2 điều kiện (biên độ ×{_vn_num(sig['range_ratio'])} ATR ≤ 0,8 VÀ khối lượng "
                f"{_vn_num(sig['vol_pct'], 1, sign=True)}% ≤ −20% so TB) → viết \"lực {side} chững lại ở phiên cuối, cần phiên xác nhận\"; "
                f"TUYỆT ĐỐI KHÔNG viết \"chưa có bằng chứng cạn lực\" hay \"lực {side} vẫn chiếm ưu thế\" ở BẤT KỲ chỗ nào (Phân tích, "
                "động lực thị trường, phòng hộ) — các mục phải dùng CÙNG 1 nhận định này."
            )
        else:
            parts.append(
                f"Phiên cuối CHƯA đủ 2 điều kiện cạn lực (biên độ ×{_vn_num(sig['range_ratio'])} ATR, khối lượng "
                f"{_vn_num(sig['vol_pct'], 1, sign=True)}% so TB) → KHÔNG viết \"nhịp {sig['trend'] or 'giá'} đang chững lại/cạn lực\"."
            )
    parts.append("Mô tả biên độ/hình dạng phiên PHẢI theo đúng nhận định trên.")
    return " ".join(parts)


def _eua_technical_levels_summary(chart_data: List[Dict]) -> str:
    """Mốc hỗ trợ/kháng cự kỹ thuật của EUA — tính trực tiếp từ đỉnh/đáy 30
    phiên gần nhất trong OHLC thật (KHÔNG để LLM tự bịa mốc), dùng cho Quote
    Chat khi người dùng hỏi về "điểm chốt lời kỹ thuật" (kháng cự) và "điểm
    bắt đáy" (hỗ trợ) — xem services/quote_chat.py::_tool_eua_details_text.

    Mục tiêu giá nếu phá kháng cự dùng kỹ thuật "đo biên độ" (measured move)
    chuẩn trong phân tích kỹ thuật: mục tiêu = kháng cự + (kháng cự - hỗ trợ).
    Đây CHỈ LÀ ước lượng kỹ thuật tham khảo dựa trên biên độ dao động lịch sử,
    KHÔNG PHẢI dự đoán chắc chắn hay khuyến nghị đầu tư — system prompt của
    Quote Chat (rule 7) yêu cầu model nhắc rõ điều này khi trả lời.
    """
    if not chart_data:
        return "Không có đủ dữ liệu lịch sử EUA để xác định mốc kỹ thuật."

    resistance = max(c["high"] for c in chart_data)
    support = min(c["low"] for c in chart_data)
    last_close = chart_data[-1]["close"]
    breakout_target = resistance + (resistance - support)
    breakdown_target = support - (resistance - support)
    # ATR = TB True Range 14 phiên — làm đệm tối thiểu cho mức cắt lỗ (0,5 × ATR) ở Mục 3, tránh cắt lỗ
    # sát hỗ trợ đến mức nhiễu phiên cũng quét.
    atr = atr_true_range(chart_data) or 0.0
    # Mốc gần (10 phiên) cho kịch bản NGẮN HẠN — biên 30 phiên thường quá xa cho 1–2 tuần.
    near = chart_data[-10:]
    near_res, near_sup = max(c["high"] for c in near), min(c["low"] for c in near)

    return (
        f"ATR 14 phiên (TB True Range = max(cao − thấp, |cao − đóng cửa trước|, |thấp − đóng cửa trước|)): ~{atr:.2f} EUR/tCO2 → đệm cắt lỗ tối thiểu ngoài hỗ trợ/kháng cự "
        f"~{0.5 * atr:.2f}. "
        f"Kháng cự/điểm chốt lời kỹ thuật (đỉnh 30 phiên gần nhất): ~{resistance:.2f} EUR/tCO2 — "
        f"nếu giá phá vỡ mốc này, mục tiêu kỹ thuật tham khảo (đo biên độ dao động 30 phiên) "
        f"~{breakout_target:.2f} EUR/tCO2. "
        f"Hỗ trợ/điểm giảm kỹ thuật (đáy 30 phiên gần nhất): ~{support:.2f} EUR/tCO2 — vùng thường "
        f"xuất hiện lực mua bắt đáy về mặt kỹ thuật; nếu giá thủng mốc này, mục tiêu kỹ thuật chiều xuống (đo biên độ) "
        f"~{breakdown_target:.2f} EUR/tCO2. Mục tiêu trung/dài hạn khi phá/thủng biên 30 phiên PHẢI dùng phép đo biên độ này "
        f"(cùng phương pháp cho cả 2 chiều), không tự lấy % tròn. "
        f"Mốc gần cho kịch bản NGẮN HẠN (10 phiên gần nhất): kháng cự ~{near_res:.2f}, hỗ trợ ~{near_sup:.2f} EUR/tCO2. "
        f"Giá đóng cửa gần nhất: {last_close:.2f} EUR/tCO2."
    )


# Ngưỡng phân loại % lệch khối lượng phiên liền trước so với TB — dùng để gắn
# nhãn factual (KHÔNG phải kết luận hướng giá, chỉ mô tả mức độ bất thường của
# khối lượng) cho LLM viết market_drivers ở Mục 2 dựa vào, thay vì tự đặt
# ngưỡng khác nhau giữa các lần sinh báo cáo.
EUA_VOLUME_SESSIONS_FOR_AVG = 20
EUA_VOLUME_SPIKE_PCT = 20.0
EUA_VOLUME_DROP_PCT = -20.0


def _has_valid_volume(c: Dict) -> bool:
    """Phiên có khối lượng hợp lệ: volume 0/âm là dữ liệu lỗi hoặc phiên chưa chốt
    (Barchart trả 0 khi chưa có số) — nếu đưa vào sẽ kéo TB về 0 và sinh ra câu
    "cao hơn TB (0) 0.0%" vô nghĩa."""
    v = c.get("volume")
    return v is not None and v > 0


def _eua_volume_summary(chart_data: List[Dict]) -> str:
    with_volume = [c for c in chart_data if _has_valid_volume(c)]
    if not with_volume:
        return (
            "Không có dữ liệu khối lượng giao dịch EUA hợp lệ cho phiên này — KHÔNG nêu bất kỳ nhận định "
            "nào về khối lượng (không suy ra lực mua/bán/chốt lời từ khối lượng)."
        )

    latest = with_volume[-1]
    latest_volume = latest["volume"]
    prior = with_volume[:-1][-EUA_VOLUME_SESSIONS_FOR_AVG:]

    price_direction = "đi ngang"
    if latest["close"] is not None and latest.get("open") is not None and latest["close"] != latest["open"]:
        price_direction = "tăng" if latest["close"] > latest["open"] else "giảm"

    if not prior:
        return (
            f"Khối lượng giao dịch EUA phiên liền trước ({latest['date']}): {latest_volume:,.0f} hợp đồng "
            f"— chưa đủ dữ liệu các phiên trước đó để so sánh với trung bình."
        )

    avg_volume = sum(c["volume"] for c in prior) / len(prior)
    pct_diff = (latest_volume - avg_volume) / avg_volume * 100  # avg > 0: prior đã lọc volume <= 0

    if pct_diff >= EUA_VOLUME_SPIKE_PCT:
        level = "tăng đột biến"
    elif pct_diff <= EUA_VOLUME_DROP_PCT:
        level = "giảm mạnh"
    else:
        level = "ở mức bình thường"

    return (
        f"Khối lượng giao dịch EUA phiên liền trước ({latest['date']}): {latest_volume:,.0f} hợp đồng, "
        f"so với TB {len(prior)} phiên gần nhất ({avg_volume:,.0f} hợp đồng) — {level} ({pct_diff:+.1f}%). "
        f"Giá phiên đó {price_direction} so với giá mở cửa cùng phiên. "
        + _eua_streak_summary(chart_data, avg_volume)
    ).strip()


def _eua_streak_summary(chart_data: List[Dict], avg_volume: Optional[float] = None) -> str:
    """Số phiên tăng/giảm LIÊN TIẾP + khối lượng của TỪNG phiên trong nhịp so với TB 20 phiên — để LLM không
    viết "giảm phiên thứ hai liên tiếp" khi đã giảm 4 phiên, và không kết luận "nhịp giảm thiếu xác nhận dòng
    tiền" chỉ từ phiên cuối (bản 17/10: 3 phiên giảm chính 13–15/10 đều trên TB, chỉ 16/10 thấp)."""
    streak = eua_streak(chart_data)
    if not streak:
        return ""
    n, d = streak["count"], streak["direction"]
    path = " → ".join(f"{_vn_num(c)} ({date.fromisoformat(dt).strftime('%d/%m')})" if dt else _vn_num(c) for dt, c in streak["closes"])
    text = f"CHUỖI PHIÊN: EUA {d.upper()} {n} phiên liên tiếp: {path}. Nêu số phiên liên tiếp PHẢI dùng đúng số {n}."
    mom = streak_momentum(streak)
    if mom:
        text += (
            f" {mom['text']} Mô tả nhịp (\"mức {d} thu hẹp/mở rộng dần\") PHẢI đúng mẫu hình này"
            + (" — KHÔNG viết \"thu hẹp dần\"/\"chậm lại dần\"." if mom["pattern"] != "thu hẹp dần" else ".")
        )
    if avg_volume and n >= 2:
        by_date = {c.get("date"): c for c in chart_data}
        vols = [(dt, by_date.get(dt, {}).get("volume")) for dt, _ in streak["closes"][1:]]
        vols = [(dt, v) for dt, v in vols if isinstance(v, (int, float)) and v > 0]
        if vols:
            above = sum(1 for _, v in vols if v > avg_volume)
            detail = "; ".join(f"{date.fromisoformat(dt).strftime('%d/%m')} {_vn_num(v, 0)}" for dt, v in vols)
            confirmed = above * 2 > len(vols)
            text += (
                f" Khối lượng các phiên trong nhịp ({detail}) — {above}/{len(vols)} phiên TRÊN TB {_vn_num(avg_volume, 0)}"
                + (f" → nhịp {d} CÓ xác nhận khối lượng; phiên cuối thấp (nếu có) chỉ là chững lại, KHÔNG được kết luận "
                   f"cả nhịp \"thiếu xác nhận dòng tiền\"." if confirmed else
                   f" → nhịp {d} THIẾU xác nhận khối lượng.")
            )
    return text


# ── Ước tính spread chuyển đổi nhiên liệu than ↔ khí (tính sẵn bằng Python) ──
# Giả định CHUẨN NGÀNH (mô hình chi phí biên, không phải spread thị trường) — ghi kèm trong output.
FUEL_COAL_EFF = 0.38          # hiệu suất nhà máy than
FUEL_GAS_EFF = 0.55           # hiệu suất CCGT
FUEL_COAL_EF = 0.34           # tCO2/MWh nhiệt của than
FUEL_GAS_EF = 0.202           # tCO2/MWh nhiệt của khí
FUEL_COAL_MWH_PER_TONNE = 6.978  # than 6.000 kcal/kg ≈ 25,1 GJ/t
FUEL_EURUSD = 1.15            # giả định tỷ giá EUR→USD (hệ thống không có giá EURUSD)


FUEL_NEAR_BREAKEVEN = 10.0  # |chênh khí − than| dưới mức này (EUR/MWh điện) = sát điểm hòa vốn, dispatch nhạy với giá
# Dải hiệu suất ĐỘI nhà máy (CCGT cũ → mới nhất; than kém → tốt nhất) — 1 nhà máy chuẩn 55%/38% không chứng minh được
# "đã bão hòa": phải xét cả cặp CCGT hiệu suất cao nhất vs nhà máy than kém nhất (nơi chuyển đổi xảy ra ĐẦU TIÊN).
FUEL_FLEET_GAS_EFF = (0.49, 0.60)
FUEL_FLEET_COAL_EFF = (0.35, 0.44)
# Giá hòa vốn thấp nhất của dải còn cao hơn EUA ≥ mức này → kết luận "bão hòa" vững với mọi hiệu suất trong dải.
FUEL_SATURATION_MARGIN = 1.25


def _fuel_costs(ttf_p: float, api2_p: float, eua_p: float) -> tuple:
    """-> (nhiên liệu khí, nhiên liệu than, tổng chi phí khí, tổng chi phí than) — EUR/MWh điện."""
    gas_f = ttf_p / FUEL_GAS_EFF
    coal_f = api2_p / FUEL_EURUSD / FUEL_COAL_MWH_PER_TONNE / FUEL_COAL_EFF
    return (gas_f, coal_f, gas_f + eua_p * FUEL_GAS_EF / FUEL_GAS_EFF,
            coal_f + eua_p * FUEL_COAL_EF / FUEL_COAL_EFF)


def _switch_price(ttf_p: float, api2_p: float, gas_eff: float, coal_eff: float) -> Optional[float]:
    """Giá EUA hòa vốn khí ↔ than cho 1 cặp hiệu suất (EUR/t)."""
    denom = FUEL_COAL_EF / coal_eff - FUEL_GAS_EF / gas_eff
    if denom <= 0:
        return None
    return (ttf_p / gas_eff - api2_p / FUEL_EURUSD / FUEL_COAL_MWH_PER_TONNE / coal_eff) / denom


def fuel_fleet_thresholds(ttf_p: float, api2_p: float, eua_p: float) -> Dict[str, Any]:
    """Dải giá EUA hòa vốn theo hiệu suất đội nhà máy + NGƯỠNG THẬT để chuyển đổi bắt đầu ảnh hưởng cầu EUA: mức TTF
    (API2 giữ nguyên) hoặc API2 (TTF giữ nguyên) làm giá hòa vốn ở ĐẦU THẤP của dải (CCGT tốt nhất vs than kém nhất)
    về đúng giá EUA hiện tại. Bản 17/10 dùng "chênh ±16 EUR/MWh (= TTF ±10%)" làm ngưỡng — TTF −10% thì giá hòa vốn vẫn
    ≈ 177 EUR/t, gấp đôi EUA, không có chuyển đổi nào xảy ra."""
    corners = [_switch_price(ttf_p, api2_p, g, c) for g in FUEL_FLEET_GAS_EFF for c in FUEL_FLEET_COAL_EFF]
    corners = [v for v in corners if v is not None]
    g_hi, c_lo = max(FUEL_FLEET_GAS_EFF), min(FUEL_FLEET_COAL_EFF)
    # Cặp chuyển đổi sớm nhất: (TTF/g − than/c) = EUA × (EFthan/c − EFkhí/g)
    coal_th = api2_p / FUEL_EURUSD / FUEL_COAL_MWH_PER_TONNE
    k = FUEL_COAL_EF / c_lo - FUEL_GAS_EF / g_hi
    ttf_first = g_hi * (coal_th / c_lo + eua_p * k)
    api2_first = (ttf_p / g_hi - eua_p * k) * c_lo * FUEL_EURUSD * FUEL_COAL_MWH_PER_TONNE
    lo = min(corners) if corners else None
    return {
        "switch_range": (lo, max(corners)) if corners else None,
        "ttf_first_switch": ttf_first,
        "api2_first_switch": api2_first,
        "ttf_first_move_pct": (ttf_first / ttf_p - 1) * 100 if ttf_p else None,
        "api2_first_move_pct": (api2_first / api2_p - 1) * 100 if api2_p else None,
        "robust_saturation": bool(lo and eua_p and lo >= eua_p * FUEL_SATURATION_MARGIN),
    }


def fuel_spread_state(prices: List[Dict]) -> Optional[Dict[str, Any]]:
    """Chênh chi phí phát điện khí − than (dương = than rẻ hơn), giá EUA hòa vốn và thay đổi trong TUẦN (giá 1 tuần
    trước suy từ Δ tuần). Dùng chung cho prompt (_fuel_switching_spread_summary) và Jack (check_fuel_direction)."""
    rows = {p.get("code"): p for p in prices if isinstance(p, dict)}
    close = {c: (rows.get(c) or {}).get("close") for c in ("TTF", "API2", "EUA")}
    if not all(isinstance(v, (int, float)) and v > 0 for v in close.values()):
        return None
    gas_f, coal_f, gas_cost, coal_cost = _fuel_costs(close["TTF"], close["API2"], close["EUA"])
    denom = FUEL_COAL_EF / FUEL_COAL_EFF - FUEL_GAS_EF / FUEL_GAS_EFF
    state: Dict[str, Any] = {
        **{c.lower(): v for c, v in close.items()}, "gas_cost": gas_cost, "coal_cost": coal_cost,
        "spread": gas_cost - coal_cost, "switch": (gas_f - coal_f) / denom if denom else None,
        "spread_prev": None, "delta": None,
        # TTF làm chênh khí − than = 0 (đổi thứ tự huy động) với API2/EUA hiện tại, và độ nhạy chênh theo 10% TTF.
        "ttf_breakeven": coal_cost * FUEL_GAS_EFF - close["EUA"] * FUEL_GAS_EF,
        "spread_per_10pct_ttf": close["TTF"] * 0.10 / FUEL_GAS_EFF,
        "week_pct": {c: (rows.get(c) or {}).get("week_change_pct") for c in close},
        **fuel_fleet_thresholds(close["TTF"], close["API2"], close["EUA"]),
    }
    wk = state["week_pct"]
    if all(isinstance(v, (int, float)) and v > -100 for v in wk.values()):
        prev = {c: close[c] / (1 + wk[c] / 100) for c in close}
        g, c_ = _fuel_costs(prev["TTF"], prev["API2"], prev["EUA"])[2:]
        state["spread_prev"] = g - c_
        state["delta"] = state["spread"] - state["spread_prev"]
    return state


def fuel_model_note(state: Optional[Dict[str, Any]]) -> str:
    """Chú thích giả định của mô hình (hiển thị cho người đọc dưới Phân tích) — giá EUA hòa vốn phụ thuộc mạnh vào
    các giả định này (hiệu suất 50%/36% thay vì 55%/38% cho kết quả khác vài chục EUR/t), nên phải công bố để kiểm chứng."""
    if not state:
        return ""
    sw = state.get("switch")
    return (
        "Giả định mô hình chuyển đổi nhiên liệu (chi phí biên): CCGT hiệu suất "
        f"{FUEL_GAS_EFF:.0%}, nhà máy than {FUEL_COAL_EFF:.0%}; hệ số phát thải khí {_vn_num(FUEL_GAS_EF, 3)}, than "
        f"{_vn_num(FUEL_COAL_EF, 2)} tCO2/MWh nhiệt; than API2 6.000 kcal/kg ({_vn_num(FUEL_COAL_MWH_PER_TONNE, 3)} MWh/t); "
        f"1 EUR = {_vn_num(FUEL_EURUSD)} USD; TTF coi như giá nhiệt. Giá EUA hòa vốn = (chi phí nhiên liệu khí − than) / "
        f"(cường độ phát thải than − khí)"
        + (f" ≈ {_vn_num(sw, 0)} EUR/t" if isinstance(sw, (int, float)) and sw > 0 else "")
        + _fleet_range_text(state)
        + " — kết quả thay đổi đáng kể theo hiệu suất nhà máy và tỷ giá giả định."
    )


def _fleet_range_text(state: Dict[str, Any]) -> str:
    rng = state.get("switch_range")
    if not rng or rng[0] is None:
        return ""
    return (
        f"; dải theo hiệu suất đội nhà máy (CCGT {FUEL_FLEET_GAS_EFF[0]:.0%}–{FUEL_FLEET_GAS_EFF[1]:.0%}, than "
        f"{FUEL_FLEET_COAL_EFF[0]:.0%}–{FUEL_FLEET_COAL_EFF[1]:.0%}) ≈ {_vn_num(rng[0], 0)}–{_vn_num(rng[1], 0)} EUR/t"
    )


FUEL_THRESHOLD_FAR_PCT = 25.0  # TTF/API2 phải đi xa hơn mức này mới chạm ngưỡng → không khả thi trong 1–2 tuần


def fuel_threshold_far(state: Optional[Dict[str, Any]]) -> bool:
    """True khi ngưỡng chuyển đổi đầu tiên (đầu thấp của dải hòa vốn) còn quá xa cho kịch bản 1–2 tuần/1–3 tháng."""
    if not state:
        return False
    moves = [state.get("ttf_first_move_pct"), state.get("api2_first_move_pct")]
    moves = [abs(m) for m in moves if isinstance(m, (int, float))]
    return bool(moves) and min(moves) > FUEL_THRESHOLD_FAR_PCT


def _fleet_threshold_line(st: Dict[str, Any]) -> str:
    rng = st.get("switch_range")
    if not rng or rng[0] is None:
        return ""
    lo, hi = rng
    gap_pct = (lo / st["eua"] - 1) * 100 if st.get("eua") else 0
    text = (
        f"  - Dải giá EUA hòa vốn theo hiệu suất ĐỘI nhà máy (CCGT {FUEL_FLEET_GAS_EFF[0]:.0%}–{FUEL_FLEET_GAS_EFF[1]:.0%}, than "
        f"{FUEL_FLEET_COAL_EFF[0]:.0%}–{FUEL_FLEET_COAL_EFF[1]:.0%}) ≈ {_vn_num(lo, 0)}–{_vn_num(hi, 0)} EUR/t; đầu thấp của dải "
        f"(CCGT tốt nhất vs than kém nhất — nơi chuyển đổi xảy ra ĐẦU TIÊN) cao hơn giá EUA {_vn_num(st['eua'])} là "
        f"{_vn_num(gap_pct, 0, sign=True)}%"
        + (" → kết luận \"bão hòa\" VỮNG với mọi hiệu suất trong dải (khi nêu kết luận này nên dẫn dải hòa vốn làm bằng chứng)."
           if st.get("robust_saturation") else " → kết luận nhạy với giả định hiệu suất, KHÔNG viết dứt khoát \"đã bão hòa\".")
        + f"\n  - NGƯỠNG THẬT (đầu thấp dải hòa vốn = giá EUA hiện tại): TTF ≈ {_vn_num(st['ttf_first_switch'], 1)} EUR/MWh "
        f"({_vn_num(st['ttf_first_move_pct'], 0, sign=True)}%, API2 giữ nguyên) HOẶC API2 ≈ {_vn_num(st['api2_first_switch'], 0)} "
        f"USD/t ({_vn_num(st['api2_first_move_pct'], 0, sign=True)}%, TTF giữ nguyên)."
    )
    if fuel_threshold_far(st):
        text += (
            " Cả 2 mức đều ngoài tầm biến động 1–2 tuần/1–3 tháng → KHÔNG đặt điều kiện fuel switching cho kịch bản ngắn/trung "
            "hạn và KHÔNG viết watchpoint kiểu \"chênh thu hẹp → khí cạnh tranh hơn → áp lực giảm EUA\" (sai với chính mô hình); "
            "nếu cần 1 watchpoint nhiên liệu thì chỉ dùng đúng mức TTF/API2 ở trên."
        )
    return text


def _fuel_switching_spread_summary(prices: List[Dict]) -> str:
    """Chi phí phát điện biên (EUR/MWh điện) của than và khí gồm cả chi phí EUA, và giá EUA
    hòa vốn chuyển đổi (switching price) — tính từ TTF, API2, EUA có trong bảng giá.
    CHỈ dùng API2 (CIF ARA — giá than nhà máy điện EU thực mua); API4 (Richards Bay) là chuẩn
    châu Á/Ấn Độ nên KHÔNG dùng cho fuel switching tại EU.
    Mục đích: MỌI mục (1/2/3) dùng chung 1 kết luận ("KẾT LUẬN DÙNG CHUNG" ở cuối khối) để biết
    than đã thắng khí chưa (chuyển đổi nhiên liệu đã bão hòa chưa), tránh mục này nói ngược mục kia.
    Chiều tác động xếp theo THAY ĐỔI RÒNG của chênh khí − than, ĐỐI XỨNG cho cả 2 nhiên liệu — bản 17/10 từng
    coi "TTF tăng" là trung lập (đã bão hòa) nhưng "API2 tăng" là áp lực giảm, trong khi chênh còn mở rộng +54 → +64."""
    by_code = {p["code"]: p.get("close") for p in prices}
    if not by_code.get("TTF") or not by_code.get("EUA"):
        return "Thiếu giá TTF hoặc EUA — không tính được spread than–khí; KHÔNG tự suy ra."
    if not by_code.get("API2"):
        return (
            "Thiếu giá than API2 (CIF ARA) — không tính được spread than–khí cho EU; KHÔNG dùng API4/NEWC "
            "(chuẩn châu Á) thay thế và KHÔNG tự suy ra. Chỉ nêu chiều tác động theo %Δ, ghi rõ \"chưa tính spread\"."
        )
    st = fuel_spread_state(prices)
    if not st:
        return "Thiếu dữ liệu giá để tính spread than–khí; KHÔNG tự suy ra."
    api2_no_trade = any(p.get("no_trade") for p in prices if p.get("code") == "API2")
    spread, switch = st["spread"], st["switch"]
    switch_txt = (
        f"giá EUA hòa vốn chuyển đổi ≈ {switch:.0f} EUR/t" if switch is not None and switch > 0
        else "than rẻ hơn khí ở MỌI mức giá EUA hợp lý (không có điểm hòa vốn dương)"
    )
    ttf_be, sens = st["ttf_breakeven"], st["spread_per_10pct_ttf"]
    ttf_move = (ttf_be / st["ttf"] - 1) * 100 if st["ttf"] else 0
    if spread > FUEL_NEAR_BREAKEVEN:
        conclusion = (
            "chuyển đổi khí→than ĐÃ BÃO HÒA theo mô hình (than có lợi thế chi phí rõ rệt). Đây là SUY LUẬN TỪ MÔ HÌNH CHI PHÍ — "
            "hệ thống KHÔNG có dữ liệu sản lượng/huy động điện (ENTSO-E, Energy-Charts), nên PHẢI viết \"mô hình cho thấy than "
            "có lợi thế chi phí\", TUYỆT ĐỐI KHÔNG viết như dữ kiện \"điện than chạy gần tối đa/huy động tối đa\". Lợi thế chi "
            "phí này đỡ MỨC cầu EUA hiện tại. Biến động giá MỘT nhiên liệu chưa đủ đổi thứ tự huy động: \"TTF tăng\" KHÔNG phải "
            "động lực tăng VÀ \"API2 tăng\" KHÔNG phải động lực giảm; \"chênh khí − than mở rộng\" cũng chỉ GIỮ MỨC, KHÔNG xếp "
            "vào cột động lực tăng (cả 3 TRUNG LẬP qua kênh fuel switching — áp dụng ĐỐI XỨNG). Tin làm TTF có rủi ro tăng "
            "KHÔNG xếp vào động lực tăng EUA qua kênh này; TTF tăng chỉ tác động qua chi phí/hedge và kênh ngược "
            "(công nghiệp cắt sản lượng → cầu EUA giảm)."
        )
    elif spread >= -FUEL_NEAR_BREAKEVEN:
        conclusion = (
            "chênh khí − than SÁT điểm hòa vốn → dispatch NHẠY với giá: TTF tăng / API2 giảm (chênh mở rộng) → than cạnh tranh "
            "hơn → cầu EUA tăng; TTF giảm / API2 tăng (chênh thu hẹp) → cầu EUA giảm."
        )
    else:
        conclusion = (
            "khí rẻ hơn than rõ rệt → dispatch nghiêng về khí; chỉ khi chênh khí − than tiến về 0 (TTF tăng mạnh hoặc API2 "
            "giảm mạnh) mới thành hỗ trợ cầu EUA; API2 tăng / TTF giảm thêm KHÔNG đổi thứ tự (trung lập)."
        )
    lines = [
        f"Ước tính chi phí phát điện biên (EUR/MWh điện, gồm chi phí EUA {st['eua']:.2f} EUR/t):",
        f"  - Chạy khí (TTF {st['ttf']:.2f} EUR/MWh, CCGT hiệu suất {FUEL_GAS_EFF:.0%}) ≈ {st['gas_cost']:.0f}.",
        f"  - Chạy than (API2 CIF ARA {st['api2']:.1f} USD/t, hiệu suất {FUEL_COAL_EFF:.0%}) ≈ {st['coal_cost']:.0f}"
        + (" (API2 phiên này KHÔNG có giao dịch — giá danh nghĩa; ảnh hưởng nhỏ tới kết luận)" if api2_no_trade else "") + ".",
        f"  - Chênh khí − than ≈ {spread:+.0f} EUR/MWh; {switch_txt}.",
        f"  - Độ nhạy (chỉ để mô tả, KHÔNG phải ngưỡng): mỗi 10% biến động TTF ≈ {sens:.0f} EUR/MWh chênh. Với nhà máy chuẩn, "
        f"để đổi thứ tự huy động (chênh = 0) với API2/EUA hiện tại, TTF phải về ≈ {ttf_be:.1f} EUR/MWh ({ttf_move:+.0f}%).",
        _fleet_threshold_line(st),
        "  - Khi đặt ngưỡng kích hoạt liên quan chuyển đổi nhiên liệu PHẢI dùng ĐÚNG mức TTF/API2 ở dòng NGƯỠNG THẬT trên "
        "(khoảng cách giá hòa vốn − giá EUA), TUYỆT ĐỐI KHÔNG dùng \"chênh khí − than thay đổi ±X EUR/MWh\" hay \"TTF ±10%\" "
        "làm ngưỡng: khi giá hòa vốn còn xa giá EUA, chênh thu hẹp vài chục EUR/MWh vẫn KHÔNG đổi thứ tự huy động.",
        fuel_model_note(st) + " Khi nêu giá EUA hòa vốn PHẢI ghi \"ước tính theo mô hình chi phí biên, giả định ở chú thích\" "
        "(giả định đã hiển thị cho người đọc) — KHÔNG tự tính lại; KHÔNG dùng API4/NEWC cho fuel switching EU.",
        f"KẾT LUẬN DÙNG CHUNG (Mục 1, 2, 3 PHẢI cùng kết luận này, không mục nào được nói ngược): {conclusion}",
    ]
    if st["delta"] is not None:
        wk, delta = st["week_pct"], st["delta"]
        sat = spread > FUEL_NEAR_BREAKEVEN
        api2_note = (" → câu kiểu \"API2 tăng thu hẹp lợi thế điện than\" là SAI với số liệu này"
                     if wk["API2"] > 0 and spread > 0 else "")
        if abs(delta) < 1:
            verdict = "gần như không đổi → không có dịch chuyển tương đối đáng kể giữa than và khí"
        elif delta > 0 and spread > 0:
            verdict = (f"MỞ RỘNG → lợi thế của than CỦNG CỐ{api2_note}; "
                       + ("không tạo thêm lực tăng vì đã bão hòa, giữ mức cầu EUA" if sat else "hỗ trợ cầu EUA"))
        elif delta > 0:
            verdict = ("khoảng cách khí rẻ hơn than THU HẸP → than cạnh tranh hơn; "
                       + ("hỗ trợ cầu EUA" if spread >= -FUEL_NEAR_BREAKEVEN else "khí vẫn rẻ hơn rõ rệt nên chưa đổi thứ tự (trung lập)"))
        elif spread > 0:
            verdict = ("THU HẸP → lợi thế than giảm bớt; " + ("than vẫn rẻ hơn khí rõ rệt nên chưa đổi thứ tự huy động (áp lực rất nhẹ)"
                                                              if sat else "áp lực giảm cầu EUA"))
        else:
            verdict = ("khí càng rẻ hơn than; " + ("áp lực giảm cầu EUA" if spread >= -FUEL_NEAR_BREAKEVEN
                                                   else "đã nghiêng hẳn về khí nên không đổi thứ tự (trung lập)"))
        lines.append(
            f"Trong TUẦN: TTF {_vn_num(wk['TTF'], sign=True)}%, API2 {_vn_num(wk['API2'], sign=True)}%, EUA "
            f"{_vn_num(wk['EUA'], sign=True)}% → chênh khí − than từ ≈ {st['spread_prev']:+.0f} lên ≈ {spread:+.0f} EUR/MWh "
            f"({delta:+.0f}): {verdict}. Tác động RÒNG của nhiên liệu lên EUA tuần này dùng ĐÚNG kết luận này."
        )
    lines.append(
        "QUY TẮC XẾP CHIỀU (BẮT BUỘC ở động lực thị trường/instrument_notes/Diễn biến chính/Phân tích): xếp theo THAY ĐỔI RÒNG "
        "của chênh khí − than ở trên, KHÔNG theo giá riêng 1 nhiên liệu. \"API2 tăng\" không bao giờ là yếu tố hỗ trợ tăng; "
        "chỉ được xếp gây áp lực giảm khi chênh THU HẸP và đang sát/dưới điểm hòa vốn."
    )
    return "\n".join(lines)


_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def _has_cjk(text: Any) -> bool:
    """True nếu chuỗi (hoặc JSON dump của dict/list) chứa ký tự Hán/Nhật/Hàn — LLM đôi khi
    'lẫn' ký tự này vào văn bản tiếng Việt (vd 'chiến l略c'); báo cáo chỉ gồm tiếng Việt/Latin."""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    return bool(_CJK_RE.search(text))


def _dedupe_similar_articles(articles: List[Dict]) -> List[Dict]:
    """Gộp các bài cùng 1 sự kiện (tiêu đề gần giống nhau) cho Mục 6 — giữ bài đầu tiên
    (ngưỡng ở services/title_similarity.is_similar_title)."""
    kept: List[Dict] = []
    kept_tokens: List[set] = []
    for art in articles:
        tokens = title_tokens(art.get("title", ""))
        if not is_similar_title(tokens, kept_tokens):
            kept.append(art)
            kept_tokens.append(tokens)
    return kept


# Hệ số quy đổi ICE Gasoil (niêm yết USD/tấn) sang USD/thùng để so được cùng đơn
# vị với Brent/WTI (USD/bbl) khi tính crack spread — ~7.45 thùng/tấn là hệ số quy
# đổi chuẩn ngành cho gasoil/diesel (EIA/API), không phải số chính xác tuyệt đối
# cho mọi lô hàng cụ thể — luôn gắn nhãn "ước tính" khi đưa vào prompt.
GASOIL_BBL_PER_TONNE = 7.45


async def _crack_spread_history(session: AsyncSession, max_date: str, sessions: int = 60) -> Optional[Dict[str, float]]:
    """TB / thấp / cao của crack spread gasoil–Brent các phiên gần nhất có đủ 2 giá — để đặt mức hôm nay vào bối cảnh."""
    rows = (await session.execute(
        select(Instrument.code, Price.price_date, Price.close_price)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Instrument.code.in_(["GASOIL", "BRENT"]), Price.price_date <= max_date)
        .order_by(desc(Price.price_date))
        .limit(sessions * 3)
    )).all()
    by_date: Dict[str, Dict[str, float]] = {}
    for code, d, c in rows:
        by_date.setdefault(d, {})[code] = c
    cracks = [v["GASOIL"] / GASOIL_BBL_PER_TONNE - v["BRENT"] for d, v in sorted(by_date.items(), reverse=True)
              if v.get("GASOIL") and v.get("BRENT")][:sessions]
    if len(cracks) < 5:
        return None
    return {"avg": sum(cracks) / len(cracks), "min": min(cracks), "max": max(cracks), "n": len(cracks)}


def _gasoil_crack_level_text(spread_today: float, history: Optional[Dict[str, float]]) -> str:
    """Đặt mức hôm nay vào lịch sử CỦA CHÍNH HỆ THỐNG (TB/thấp/cao các phiên gần nhất) — không dùng mốc "thường
    15–25 USD/bbl" không có nguồn (bản chấm 17/10)."""
    if not history:
        return (" Không có đủ lịch sử crack spread trong hệ thống để so — KHÔNG gọi mức này là cao/thấp và KHÔNG suy luận "
                "về công suất lọc dầu.")
    avg, lo, hi = history["avg"], history["min"], history["max"]
    pos = (spread_today - lo) / (hi - lo) * 100 if hi > lo else 50
    level = "CAO hơn" if spread_today > avg * 1.1 else "THẤP hơn" if spread_today < avg * 0.9 else "XẤP XỈ"
    rule = (
        " Biên lọc dầu vẫn trên mức trung bình gần đây → thu hẹp từ mức này KHÔNG phải lý do hạ công suất lọc dầu; KHÔNG suy "
        "luận \"biên giảm → cắt công suất lọc dầu → giảm phát thải/cầu EUA\"."
        if spread_today >= avg else
        " Chỉ khi crack spread thấp kéo dài (dưới đáy lịch sử gần đây) mới có cơ sở nói tới hạ công suất lọc dầu."
    )
    return (f" MỨC so với lịch sử {history['n']} phiên của hệ thống (nguồn: giá Gasoil/Brent đã crawl): TB {avg:+.2f}, thấp "
            f"{lo:+.2f}, cao {hi:+.2f} USD/bbl → hôm nay {level} TB, ở {pos:.0f}% biên lịch sử. Khi nhắc mức cao/thấp PHẢI nêu "
            f"mốc so sánh này, KHÔNG dùng mốc ngoài không có nguồn.{rule}")


def _gasoil_crack_spread_summary(
    prices: List[Dict], prev_report_close: Optional[Dict[str, float]] = None,
    history: Optional[Dict[str, float]] = None,
) -> Optional[str]:
    """Ước tính crack spread Gasoil vs Brent — tính bằng Python (quy đổi đơn vị),
    thay vì để LLM tự suy luận từ 2 số liệu khác đơn vị (Gasoil USD/MT, Brent
    USD/bbl), việc rất dễ cho ra kết luận sai vì lệch đơn vị.
    """
    gasoil = next((p for p in prices if p["code"] == "GASOIL"), None)
    brent = next((p for p in prices if p["code"] == "BRENT"), None)
    if not gasoil or not brent:
        return None

    def _prev_close(p: Dict) -> Optional[float]:
        pct = p.get("day_change_pct")
        if pct is None or p.get("close") is None:
            return None
        return p["close"] / (1 + pct / 100)

    gasoil_bbl = gasoil["close"] / GASOIL_BBL_PER_TONNE
    spread_today = gasoil_bbl - brent["close"]

    prev_gasoil_close = _prev_close(gasoil)
    prev_brent_close = _prev_close(brent)
    trend = ""
    if prev_gasoil_close is not None and prev_brent_close is not None:
        spread_prev = (prev_gasoil_close / GASOIL_BBL_PER_TONNE) - prev_brent_close
        delta = spread_today - spread_prev
        direction = "mở rộng" if delta > 0.01 else "thu hẹp" if delta < -0.01 else "gần như đi ngang"
        trend = (
            f" So với phiên trước, spread {direction} {abs(delta):.2f} USD/bbl "
            f"({spread_prev:+.2f} → {spread_today:+.2f})."
        )

    prev_report_close = prev_report_close or {}
    if (gasoil.get("revision") or brent.get("revision")) and prev_report_close.get("GASOIL") and prev_report_close.get("BRENT"):
        spread_prev_report = prev_report_close["GASOIL"] / GASOIL_BBL_PER_TONNE - prev_report_close["BRENT"]
        trend += (
            f" LƯU Ý: giá phiên trước đã được nguồn điều chỉnh hồi tố — mức thay đổi trên tính theo chuỗi ĐÃ điều chỉnh; "
            f"so với số liệu đã in ở báo cáo trước ({spread_prev_report:+.2f} USD/bbl) spread thực thay đổi "
            f"{spread_today - spread_prev_report:+.2f} USD/bbl. Khi nhắc tới thay đổi crack spread PHẢI nêu cả 2 con số và ghi rõ dữ liệu đã điều chỉnh."
        )
    return (
        f"Gasoil crack spread vs Brent (ƯỚC TÍNH, quy đổi {GASOIL_BBL_PER_TONNE} thùng/tấn — "
        f"KHÔNG phải số liệu chính thức từ sàn): {spread_today:+.2f} USD/bbl "
        f"(Gasoil {gasoil_bbl:.2f} USD/bbl từ {gasoil['close']:.2f} USD/MT; Brent {brent['close']:.2f} USD/bbl)."
        f"{trend}{_gasoil_crack_level_text(spread_today, history)}"
    )


async def get_previous_report_events(session: AsyncSession, report_date_str: str) -> List[Dict]:
    """Lấy danh sách sự kiện Mục 8 từ báo cáo gần nhất TRƯỚC báo cáo `report_date_str`
    (so theo report_date — ngày tạo, KHÔNG phải ngày dữ liệu) — để Mục 8 hôm nay có
    thể cập nhật lại kết quả thực tế của các sự kiện kỳ trước đã qua."""
    stmt = (
        select(Report)
        .where(
            Report.report_date < report_date_str,
            Report.status.in_(["draft", "published"]),  # bỏ qua report đang 'generating'/'failed' — content=None
        )
        .order_by(desc(Report.report_date))
        .limit(1)
    )
    result = await session.execute(stmt)
    prev_report = result.scalars().first()
    if not prev_report or not prev_report.content:
        return []
    return prev_report.content.get("8", {}).get("events", [])


PRICE_REVISION_MIN_PCT = 0.1  # lệch < 0,1% coi là làm tròn, không phải điều chỉnh
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


async def annotate_price_revisions(
    session: AsyncSession, report_date_str: str, prices: List[Dict]
) -> Dict[str, float]:
    """So giá đóng cửa báo cáo GẦN NHẤT trước đó đã in với giá hiện có trong DB cho CÙNG phiên —
    nguồn (Barchart) đôi khi điều chỉnh hồi tố (vd Gasoil 14/10: 1.448,25 → 1.404,00), làm Δ ngày/Δ tuần
    và crack spread hôm nay lệch với số người đọc thấy hôm qua. Mã bị điều chỉnh được gắn
    p["revision"] (vào prompt) + nối vào p["note"] (hiển thị ở Bảng giá nhanh).
    Trả về {code: giá đóng cửa báo cáo trước đã in} để tính thay đổi "so với báo cáo trước"."""
    prev = (await session.execute(
        select(Report)
        .where(Report.report_date < report_date_str, Report.status.in_(["draft", "published"]))
        .order_by(desc(Report.report_date))
        .limit(5)
    )).scalars()
    for rep_ in prev:
        sec2 = (rep_.content or {}).get("2") or {}
        prev_prices = [p for p in sec2.get("prices") or [] if isinstance(p, dict) and isinstance(p.get("close"), (int, float))]
        m = _ISO_DATE_RE.search(str(sec2.get("price_timestamp") or ""))
        if prev_prices and m:
            break
    else:
        return {}
    prev_date = m.group(0)
    prev_close = {str(p["code"]).upper(): float(p["close"]) for p in prev_prices if p.get("code") != CBAM_CODE}
    rows = (await session.execute(
        select(Instrument.code, Price.close_price)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date == prev_date, Instrument.code.in_(list(prev_close)))
    )).all()
    by_code = {p["code"]: p for p in prices}
    for code, now_close in rows:
        old = prev_close.get(code)
        if not old or now_close is None or code not in by_code:
            continue
        pct = (now_close / old - 1) * 100
        if abs(pct) < PRICE_REVISION_MIN_PCT:
            continue
        d = date.fromisoformat(prev_date).strftime("%d/%m")
        text = (
            f"Dữ liệu phiên {d} đã được nguồn điều chỉnh: {_vn_num(old)} → {_vn_num(now_close)} ({_vn_num(pct, sign=True)}%) "
            f"— Δ ngày/Δ tuần hôm nay tính trên chuỗi đã điều chỉnh"
        )
        p = by_code[code]
        p["revision"] = text
        p["note"] = f"{p['note']} {text}.".strip() if p.get("note") else f"{text}."
        logger.warning("[REPORT] %s: %s", code, text)
    return prev_close


def _vn_num(x: float, digits: int = 2, sign: bool = False) -> str:
    """1386.75 -> '1.386,75' (kiểu số tiếng Việt, dùng trong văn bản báo cáo)."""
    s = f"{x:+,.{digits}f}" if sign else f"{x:,.{digits}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


async def get_previous_scenarios_text(session: AsyncSession, report_date_str: str, eua_close: Optional[float]) -> str:
    """Kịch bản giao dịch của báo cáo GẦN NHẤT có kịch bản (trước `report_date_str`) + EUA đã đi
    thế nào từ đó — để Mục 3 hôm nay nhìn thấy nhận định cũ, thừa nhận khi đi ngược và giải thích
    khi đổi chiều/mục tiêu (người đọc so sánh các ngày với nhau)."""
    stmt = (
        select(Report)
        .where(Report.report_date < report_date_str, Report.status.in_(["draft", "published"]))
        .order_by(desc(Report.report_date))
        .limit(5)
    )
    for prev in (await session.execute(stmt)).scalars():
        scenarios = ((prev.content or {}).get("3") or {}).get("trading_scenarios") or []
        if not scenarios:
            continue  # báo cáo CN/T2 không có kịch bản → lùi tiếp
        prev_close = next(
            (p.get("close") for p in ((prev.content.get("2") or {}).get("prices") or [])
             if isinstance(p, dict) and str(p.get("code", "")).upper() == "EUA"),
            None,
        )
        lines = [f"Báo cáo {prev.report_date}" + (f" — EUA khi đó đóng cửa {prev_close:.2f}" if isinstance(prev_close, (int, float)) else "")]
        if isinstance(prev_close, (int, float)) and prev_close and eua_close:
            lines.append(f"EUA hiện tại {eua_close:.2f} ({(eua_close / prev_close - 1) * 100:+.2f}% so với khi đó)")
        for sc in scenarios:
            if not isinstance(sc, dict):
                continue
            strategy = " | ".join(l.strip() for l in str(sc.get("trading_strategy") or "").splitlines() if l.strip())
            lines.append(
                f"- {sc.get('horizon')}: {sc.get('direction')} (xác suất {sc.get('probability')}); "
                f"điều kiện: {sc.get('condition')}; {strategy}"
            )
        return "\n".join(lines)
    return ""


PREV_DIGEST_MAX_CHARS = 3000


async def get_previous_report_digest(session: AsyncSession, report_date_str: str) -> str:
    """Tóm lược báo cáo gần nhất trước đó (Mục 1, Diễn biến chính, Động lực thị trường) — nối SAU danh
    sách tin của Mục 1/2/3 để LLM biết sự kiện nào đã nêu (chỉ nhắc lại khi có diễn biến mới, ghi ngày)
    và số liệu nào hôm nay lệch với hôm trước (nêu cả 2). KHÔNG phải nguồn trích dẫn."""
    prev = (await session.execute(
        select(Report)
        .where(Report.report_date < report_date_str, Report.status.in_(["draft", "published"]))
        .order_by(desc(Report.report_date))
        .limit(1)
    )).scalar_one_or_none()
    if prev is None or not prev.content:
        return ""
    c = prev.content
    lines: List[str] = []

    def _src(it: Dict) -> str:
        return f" [{it['source_name']}]" if it.get("source_name") else ""

    for b in (c.get("1") or {}).get("bullets") or []:
        if isinstance(b, dict) and b.get("text"):
            lines.append(f"- (Tóm tắt) {b['text']}{_src(b)}")
    for d in (c.get("2") or {}).get("key_developments") or []:
        if isinstance(d, dict) and d.get("title"):
            lines.append(f"- (Diễn biến) {d['title']}: {d.get('summary') or ''}{_src(d)}")
    drivers = (c.get("2") or {}).get("market_drivers") or {}
    for side in ("bullish", "bearish"):
        for it in drivers.get(side) or []:
            if isinstance(it, dict) and it.get("text"):
                lines.append(f"- (Động lực {'tăng' if side == 'bullish' else 'giảm'}) {it['text']}{_src(it)}")
    if not lines:
        return ""
    body = "\n".join(lines)[:PREV_DIGEST_MAX_CHARS]
    return (
        f"\n\nBÁO CÁO TRƯỚC ({date.fromisoformat(prev.report_date).strftime('%d/%m')}) ĐÃ NÊU — CHỈ để đối chiếu, "
        f"KHÔNG phải nguồn trích dẫn (không có số [N]): sự kiện đã nêu ở đây thì chỉ nhắc lại khi tin hôm nay có diễn biến "
        f"MỚI và PHẢI ghi ngày diễn ra; số liệu hôm nay khác với ở đây cho CÙNG chỉ tiêu → nêu cả 2 kèm nguồn/ngày.\n{body}"
    )


def _parse_event_date(ev: Dict) -> Optional[date]:
    """Parse field 'date' (YYYY-MM-DD) của 1 event Mục 8; None nếu thiếu/sai định dạng."""
    raw = ev.get("date")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _split_prev_events(events: List[Dict], target_date_str: str) -> tuple[List[Dict], List[Dict]]:
    """Lọc sự kiện Mục 8 của báo cáo trước theo cửa sổ 7 ngày tính từ target_date —
    đây là chỗ CHẶN CỨNG (không chỉ dựa vào prompt) để Mục 8 không tồn đọng sự kiện
    cũ vô thời hạn:
    - "pending_outcome": ngày diễn ra CÙNG NGÀY hoặc TRƯỚC target_date (date <=
      target_date) mà CHƯA có "outcome" — đưa cho LLM cập nhật kết quả ĐÚNG 1 LẦN
      trong báo cáo này (kể cả sự kiện diễn ra ĐÚNG NGÀY báo cáo — vd báo cáo ngày
      01/09 có nêu sự kiện ngày 03/09 thì báo cáo ngày 03/09 phải cố cập nhật kết
      quả luôn, không đợi thêm 1 ngày nữa). Sau khi có outcome (kể cả placeholder
      "chưa xác nhận"), lần gọi kế tiếp sẽ không còn thấy nó thiếu outcome nữa ->
      tự động rơi khỏi cả 2 nhóm và bị loại vĩnh viễn — không lặp lại lần 2.
    - "still_upcoming": còn ở tương lai trong cửa sổ 7 ngày tới (target_date <
      date <= target_date+6) — giữ lại để không mất các sự kiện đơn lẻ (không định
      kỳ) mà tin tức hôm nay không nhắc lại.
    Sự kiện đã có outcome, đã quá hạn quá lâu, vượt quá 7 ngày tới, hoặc thiếu "date"
    hợp lệ đều bị loại khỏi cả 2 nhóm (không mang sang báo cáo mới)."""
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    window_end = target + timedelta(days=CALENDAR_WINDOW_DAYS)
    pending_outcome, still_upcoming = [], []
    for ev in events:
        ev_date = _parse_event_date(ev)
        if ev_date is None:
            continue
        if ev_date <= target and not ev.get("outcome"):
            pending_outcome.append(ev)
        elif target < ev_date <= window_end:
            still_upcoming.append(ev)
    return pending_outcome, still_upcoming


def _format_prev_events(pending_outcome: List[Dict], still_upcoming: List[Dict]) -> str:
    def _fmt(ev: Dict) -> str:
        return f"- {ev.get('date', '?')} ({ev.get('datetime_vn', '?')}) | {ev.get('event', '?')} | Tác động: {ev.get('impact', '?')}"

    parts = []
    if pending_outcome:
        parts.append(
            "Đã diễn ra hoặc diễn ra đúng hôm nay, CẦN cập nhật kết quả (thêm field \"outcome\"):\n"
            + "\n".join(_fmt(ev) for ev in pending_outcome)
        )
    if still_upcoming:
        parts.append(
            "Vẫn còn sắp tới trong cửa sổ 7 ngày, giữ nguyên \"date\" (KHÔNG cần \"outcome\"):\n"
            + "\n".join(_fmt(ev) for ev in still_upcoming)
        )
    if not parts:
        return "(Không có sự kiện nào cần mang sang từ báo cáo trước.)"
    return "\n\n".join(parts)


def _finalize_section8_events(
    events: Optional[List[Dict]], target_date_str: str, recurring_events: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Chặn cứng lần cuối ở tầng code trên chính output LLM vừa trả về: chỉ giữ sự
    kiện có "date" trong cửa sổ 7 ngày tính từ target_date, hoặc sự kiện quá hạn
    nhưng vừa được điền "outcome" trong lượt này — đảm bảo Mục 8 không bao giờ tồn
    đọng sự kiện cũ dù prompt có bị LLM làm sai.

    `recurring_events`: cùng danh sách đã tính sẵn bằng Python truyền cho prompt
    (xem `_compute_recurring_calendar_events`) — merge cứng lại ở đây (khớp đúng
    theo "date" + "event") để đảm bảo các sự kiện định kỳ (EIA/API/Baker
    Hughes/đấu giá EUA) LUÔN có mặt trong output cuối, kể cả khi LLM lỡ bỏ sót
    hay parse JSON lỗi ở mục này — không phụ thuộc hoàn toàn vào việc LLM chép
    đúng theo hướng dẫn trong prompt."""
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    window_end = target + timedelta(days=CALENDAR_WINDOW_DAYS)
    kept = []
    for ev in events or []:
        ev_date = _parse_event_date(ev)
        if ev_date is None:
            # Thiếu/lỗi "date" — không đủ căn cứ để lọc, giữ nguyên để tránh mất dữ
            # liệu do LLM quên field, chỉ log để theo dõi.
            logger.warning("[REPORT] Mục 8: event thiếu/lỗi field 'date', giữ nguyên: %s", ev.get("event"))
            kept.append(ev)
        elif target <= ev_date <= window_end:
            kept.append(ev)
        elif ev_date < target and ev.get("outcome"):
            kept.append(ev)
        # else: quá hạn chưa có outcome, hoặc vượt quá 7 ngày tới -> loại bỏ hẳn.

    # LLM chép sự kiện định kỳ mà làm rơi kết quả đã công bố → lấy lại từ bản Python (không để nằm ở "sắp tới").
    by_key = {(ev.get("date"), ev.get("event")): ev for ev in recurring_events or []}
    for ev in kept:
        src = by_key.get((ev.get("date"), ev.get("event")))
        if src and src.get("outcome") and not ev.get("outcome"):
            ev["outcome"] = src["outcome"]
    existing_keys = {(ev.get("date"), ev.get("event")) for ev in kept}
    for ev in recurring_events or []:
        key = (ev.get("date"), ev.get("event"))
        if key not in existing_keys:
            kept.append(dict(ev))
            existing_keys.add(key)

    kept.sort(key=lambda ev: ev.get("date") or "")
    return kept


# Cửa sổ "7 ngày tới" của Mục 8 tính từ NGÀY DỮ LIỆU (= report_date − 1) → +7 để phủ đủ 7 ngày
# kể từ ngày phát hành báo cáo (trước là +6 nên lỡ EIA thứ Tư tuần sau, vd báo cáo 15/10 thiếu EIA 21/10).
CALENDAR_WINDOW_DAYS = 7


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Ngày thứ `weekday` (0=Thứ Hai) lần thứ n của tháng; n=-1 = lần cuối."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def us_federal_holidays(year: int) -> set:
    """Ngày nghỉ lễ liên bang Mỹ (ngày nghỉ bù: rơi Thứ Bảy → Thứ Sáu, Chủ Nhật → Thứ Hai) —
    EIA lùi lịch công bố khi tuần có ngày nghỉ, Baker Hughes công bố sớm khi Thứ Sáu là ngày nghỉ."""
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    observed = set()
    for d in fixed:
        if d.weekday() == 5:
            d -= timedelta(days=1)
        elif d.weekday() == 6:
            d += timedelta(days=1)
        observed.add(d)
    observed |= {
        _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),   # Presidents' Day
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }
    return observed


def eia_release_date(week_wednesday: date) -> date:
    """Ngày công bố EIA (giờ Mỹ) của tuần chứa `week_wednesday`: lùi 1 ngày cho mỗi ngày nghỉ lễ
    liên bang rơi vào Thứ Hai–Thứ Tư tuần đó (vd Columbus Day 12/10/2026 → EIA công bố Thứ Năm 15/10)."""
    holidays = us_federal_holidays(week_wednesday.year)
    monday = week_wednesday - timedelta(days=2)
    shift = sum(1 for k in range(3) if monday + timedelta(days=k) in holidays)
    return week_wednesday + timedelta(days=shift)


def baker_hughes_release_date(week_friday: date) -> date:
    """Baker Hughes công bố Thứ Sáu; Thứ Sáu là ngày nghỉ lễ (vd 25/12) → công bố sớm 1 ngày.
    Tuần Lễ Tạ Ơn (Thứ Năm nghỉ, Thứ Sáu nhiều nơi nghỉ) Baker Hughes thường công bố Thứ Tư."""
    holidays = us_federal_holidays(week_friday.year)
    if week_friday - timedelta(days=1) in holidays:  # Thanksgiving
        return week_friday - timedelta(days=2)
    if week_friday in holidays:
        return week_friday - timedelta(days=1)
    return week_friday


def expected_data_week_ending(event_name: str, event_vn_date: date) -> Optional[date]:
    """Tuần dữ liệu (week_ending) mà sự kiện Mục 8 ngày `event_vn_date` (giờ VN) PHẢI công bố:
    EIA = Thứ Sáu liền trước ngày công bố (21:30/22:30 VN cùng ngày Mỹ); Baker Hughes = Thứ Sáu
    của tuần công bố (rạng sáng VN hôm sau). Dùng để KHÔNG gắn nhầm số kỳ trước vào sự kiện mới."""
    if "EIA" in event_name:
        return event_vn_date - timedelta(days=(event_vn_date.weekday() - 4) % 7 or 7)
    if "Baker Hughes" in event_name:
        # Giờ VN rơi vào rạng sáng hôm sau ngày công bố Mỹ → lùi 1 ngày rồi lấy Thứ Sáu của tuần đó.
        us_date = event_vn_date - timedelta(days=1)
        return us_date + timedelta(days=4 - us_date.weekday())
    return None


def data_matches_event(event_name: str, event_vn_date: date, week_ending: Optional[str]) -> bool:
    """Số liệu (week_ending "YYYY-MM-DD") có đúng kỳ của sự kiện không — EIA khớp tuyệt đối,
    Baker Hughes cho lệch 1 ngày (tuần lễ công bố sớm, file có thể ghi ngày Thứ Năm)."""
    expected = expected_data_week_ending(event_name, event_vn_date)
    if expected is None:
        return True
    try:
        actual = date.fromisoformat(str(week_ending or ""))
    except ValueError:
        return False
    tolerance = 1 if "Baker Hughes" in event_name else 0
    return abs((actual - expected).days) <= tolerance


# Múi giờ nguồn công bố gốc của 3 lịch định kỳ Mục 8 có DST (khác VN — VN
# không có DST) — quy đổi qua zoneinfo để tự động bù trừ giờ mùa hè/đông thay
# vì hardcode 1 offset cố định dễ sai lệch 1 tiếng tuỳ thời điểm trong năm.
_EIA_API_TZ = ZoneInfo("America/New_York")
_BAKER_HUGHES_TZ = ZoneInfo("America/Chicago")
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_EEX_TZ = ZoneInfo("Europe/Berlin")
_VN_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def _compute_recurring_calendar_events(target_date_str: str) -> List[Dict]:
    """Tính SẴN bằng Python (không để LLM tự đoán) các sự kiện lịch thị trường
    có lịch công bố ĐỊNH KỲ, CỐ ĐỊNH theo tuần.

    CHỈ tính 2 sự kiện có thể fetch dữ liệu thực tế tự động:
      - EIA Weekly Petroleum Status Report: Thứ Tư 10:30 ET → quy đổi sang VN.
      - Baker Hughes Rig Count: Thứ Sáu 12:00 CT → quy đổi sang VN.

    ĐÃ LOẠI BỎ (không thể lấy dữ liệu thực):
      - API Crude Inventory: URL bị 404, yêu cầu membership — không có endpoint công khai.
    Đấu giá EUA EEX tính riêng từ lịch chính thức trong DB — xem `_auction_calendar_events`.

    Quy đổi qua zoneinfo (tự bù DST của Mỹ) thay vì hardcode 1 offset cố định.
    """
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    window_end = target + timedelta(days=CALENDAR_WINDOW_DAYS)
    events: List[Dict] = []

    def _add_us_release(anchor_date: date, anchor_time: dtime, tz: ZoneInfo, label: str, impact: str) -> None:
        vn_dt = datetime.combine(anchor_date, anchor_time, tzinfo=tz).astimezone(_VN_TZ)
        if target <= vn_dt.date() <= window_end:
            # Luôn ghi giờ công bố gốc + giờ VN (cột ngày chỉ có DD/MM giờ VN) — vd Baker Hughes Thứ Sáu
            # 12:00 Chicago = rạng sáng Thứ Bảy VN; EIA lùi sang Thứ Năm khi tuần có ngày nghỉ lễ Mỹ.
            city = "New York" if tz is _EIA_API_TZ else "Chicago"
            label = (
                f"{label} — công bố {_VN_WEEKDAYS[anchor_date.weekday()]} {anchor_date.strftime('%d/%m')} "
                f"{anchor_time.strftime('%H:%M')} giờ {city} ({vn_dt.strftime('%H:%M')} giờ VN)"
            )
            events.append({
                "date": vn_dt.date().isoformat(),
                "datetime_vn": vn_dt.strftime("%d/%m"),
                "event": label,
                "impact": impact,
                # Giờ công bố thực (VN) — gắn kết quả khi đã công bố TRƯỚC lúc sinh báo cáo, kể cả khi rơi vào
                # chính ngày phát hành (Baker Hughes 00:00 17/10 VN, báo cáo 17/10 sinh lúc 07:00).
                "released_at": vn_dt.isoformat(),
            })

    # Quét dư 2 ngày trước cửa sổ VN vì mốc gốc tính theo lịch Mỹ (ET/CT) —
    # quy đổi sang VN có thể lệch sang ngày hôm sau
    # (VD: EIA 10:30 ET mùa hè = 21:30 VN cùng ngày; mùa đông = 22:30 VN cùng ngày).
    scan_day = target - timedelta(days=2)
    while scan_day <= window_end:
        if scan_day.weekday() == 2:  # Thứ Tư (ET) — lùi lịch nếu tuần có ngày nghỉ lễ Mỹ
            _add_us_release(eia_release_date(scan_day), dtime(10, 30), _EIA_API_TZ,
                             "Tồn kho dầu thô EIA (EIA Weekly Petroleum Status Report)", "Cao")
        if scan_day.weekday() == 4:  # Thứ Sáu (CT) — sớm hơn nếu Thứ Sáu/Thứ Năm là ngày nghỉ lễ Mỹ
            _add_us_release(baker_hughes_release_date(scan_day), dtime(12, 0), _BAKER_HUGHES_TZ,
                             "Số giàn khoan Baker Hughes (Baker Hughes Rig Count)", "Trung")
        scan_day += timedelta(days=1)

    # COT EUA của ICE Endex (báo cáo vị thế MiFID II hàng tuần): công bố Thứ Tư, vị thế chốt Thứ Sáu trước — Thứ Tư rơi
    # vào tuần có ngày nghỉ lễ thì sàn được lùi 1 ngày (circular ICE Endex). Hệ thống CHƯA thu thập số liệu này.
    day = target + timedelta(days=1)
    while day <= window_end:
        if day.weekday() == 2:
            events.append({
                "date": day.isoformat(),
                "datetime_vn": day.strftime("%d/%m"),
                "event": (f"Báo cáo vị thế quỹ EUA — Commitments of Traders ICE Endex (vị thế chốt Thứ Sáu "
                          f"{(day - timedelta(days=5)).strftime('%d/%m')}; hệ thống chưa thu thập số liệu, cần đọc tay)"),
                "impact": "Trung",
            })
        day += timedelta(days=1)

    events.sort(key=lambda e: e["date"])
    return events


POLICY_CALENDAR_PATH = Path(__file__).resolve().parent.parent / "policy_calendar.yaml"


def _policy_calendar_events(target_date_str: str, path: Path = POLICY_CALENDAR_PATH) -> List[Dict]:
    """Họp chính sách EU (Hội đồng châu Âu/Môi trường/Năng lượng, Nghị viện...) từ policy_calendar.yaml do admin cập nhật
    — Mục 8 bản 17/10 thiếu họp Hội đồng Môi trường vì chỉ thêm họp chính sách khi tin tức trong ngày nhắc ngày họp."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("[REPORT] Không đọc được %s — bỏ qua lịch họp chính sách.", path, exc_info=True)
        return []
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    window_end = target + timedelta(days=CALENDAR_WINDOW_DAYS)
    out = []
    for ev in data.get("events") or []:
        try:
            start = date.fromisoformat(str(ev["date"]))
            end = date.fromisoformat(str(ev.get("end") or ev["date"]))
        except (KeyError, ValueError):
            logger.warning("[REPORT] policy_calendar.yaml: dòng sai định dạng — bỏ: %r", ev)
            continue
        if end < target or start > window_end:
            continue
        span = start.strftime("%d/%m") if start == end else f"{start.strftime('%d/%m')}–{end.strftime('%d/%m')}"
        out.append({
            "date": max(start, target).isoformat(),
            "datetime_vn": max(start, target).strftime("%d/%m"),
            "event": f"{ev.get('event')} ({span})",
            "impact": ev.get("impact") if ev.get("impact") in ("Cao", "Trung", "Thấp") else "Cao",
        })
    return out


# Sự kiện định kỳ đã tới giờ công bố nhưng không lấy được số liệu tự động → vẫn là "đã diễn ra", không nằm ở "sắp tới"
# (bản 17/10: Baker Hughes công bố 00:00 17/10 giờ VN vẫn hiện trong lịch sắp tới).
RELEASED_NO_DATA_OUTCOME = "Đã công bố — hệ thống chưa lấy được số liệu tự động"


AUCTION_EVENT_RE = re.compile(r"Đấu giá EUA", re.IGNORECASE)





AUCTION_MIN_SAME_PLATFORM = 3


async def _latest_auction_summary(session: AsyncSession, max_date: str, eua_close: Optional[float]) -> str:
    """Kết quả các phiên đấu giá sơ cấp EUA gần nhất (≤ ngày dữ liệu) — để Mục 2/3 dùng làm tín hiệu cầu thật thay vì
    viết "thiếu xác nhận từ đấu giá" khi DB đã có (bản 17/10: phiên DE 16/10 khớp 85,50 > đóng cửa 85,41, cover 2,09)."""
    rows = (await session.execute(
        select(EuaAuction)
        .where(EuaAuction.auction_date <= date.fromisoformat(max_date), EuaAuction.status == "successful",
               EuaAuction.auction_price.is_not(None))
        .order_by(desc(EuaAuction.auction_date))
        .limit(3)
    )).scalars().all()
    if not rows:
        return "Chưa có kết quả đấu giá sơ cấp EUA trong dữ liệu — nếu nhắc phải ghi \"chưa có dữ liệu đấu giá\"."
    # Mốc so sánh tỷ lệ đặt mua/chào bán: TB 90 ngày CỦA CÙNG NỀN TẢNG (DE/EU/PL) — phiên DE khối lượng nhỏ (~1,5 triệu t
    # so với ~2,8 triệu t của EU) nên tỷ lệ thường cao hơn; bản 17/10 so DE 2,09 với TB mọi nền tảng 1,63 là lệch chuẩn.
    hist_rows = (await session.execute(
        select(EuaAuction.auction_name, EuaAuction.cover_ratio).where(
            EuaAuction.auction_date > date.fromisoformat(max_date) - timedelta(days=90),
            EuaAuction.auction_date <= date.fromisoformat(max_date),
            EuaAuction.status == "successful", EuaAuction.cover_ratio.is_not(None),
        )
    )).all()
    by_platform: Dict[str, List[float]] = {}
    for name, cover in hist_rows:
        by_platform.setdefault(name, []).append(cover)
    lines = []
    for r in rows:
        cover = r.cover_ratio
        hist = by_platform.get(r.auction_name) or []
        avg_cover = sum(hist) / len(hist) if len(hist) >= AUCTION_MIN_SAME_PLATFORM else None
        demand = (
            "" if cover is None
            else f" (chưa đủ {AUCTION_MIN_SAME_PLATFORM} phiên {r.auction_name} trong 90 ngày để so — KHÔNG gọi là cao/thấp)"
            if avg_cover is None
            else f" (TB 90 ngày của phiên {r.auction_name} {_vn_num(avg_cover)} trên {len(hist)} phiên → "
                 + ("CAO hơn TB" if cover > avg_cover * 1.1 else "THẤP hơn TB" if cover < avg_cover * 0.9 else "xấp xỉ TB") + ")"
        )
        vs_close = (f", chênh {_vn_num(r.auction_price - eua_close, sign=True)} so với đóng cửa futures Dec {_vn_num(eua_close)} "
                    "(KHÁC thời điểm và kỳ hạn — chỉ tham khảo, KHÔNG dùng làm tín hiệu cầu)"
                    if eua_close and r.auction_date.isoformat() == max_date else "")
        lines.append(
            f"- {r.auction_date.strftime('%d/%m')} {r.auction_name}: khớp {_vn_num(r.auction_price)} EUR/t{vs_close}"
            + (f", tỷ lệ đặt mua/chào bán {_vn_num(cover)}" if cover is not None else "")
            + (f", {r.total_bidders} nhà thầu" if r.total_bidders else "") + demand
        )
    return (
        "KẾT QUẢ ĐẤU GIÁ SƠ CẤP EUA GẦN NHẤT (EEX — dữ liệu thật, dùng làm tín hiệu cầu; KHÔNG viết \"thiếu xác nhận từ đấu giá\" "
        "khi đã có số dưới đây). SO SÁNH CÙNG CHUẨN (BẮT BUỘC): giá khớp đấu giá là giá GIAO NGAY chốt lúc ~11:00 giờ Frankfurt, "
        "giá đóng cửa là futures Dec chốt cuối phiên — KHÔNG được viết \"giá khớp cao hơn/thấp hơn futures → cầu mạnh/yếu\"; "
        "tỷ lệ đặt mua/chào bán CHỈ so với TB của CÙNG nền tảng (DE với DE, EU với EU) nêu kèm, KHÔNG so với TB chung. Người "
        "tham gia đấu giá gồm cả tổ chức tài chính, không chỉ bên tuân thủ → KHÔNG suy ra \"cầu tuân thủ vững\" từ tỷ lệ này):\n"
        + "\n".join(lines)
    )


async def _auction_calendar_events(session: AsyncSession, target_date_str: str) -> List[Dict]:
    """1 sự kiện gộp các phiên đấu giá sơ cấp EUA trên EEX trong (target, target + 7 ngày] — từ lịch
    chính thức đã crawl (eua_auctions, crawl_prices/crawl_eex_auctions.py). Gộp thay vì 1 dòng/phiên vì
    gần như ngày làm việc nào cũng có phiên; chỉ lấy phiên TƯƠNG LAI nên không cần "outcome" (kết quả
    đấu giá đã nằm ở khối ước tính CBAM của Mục 2)."""
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    rows = (await session.execute(
        select(EuaAuction)
        .where(
            EuaAuction.auction_date > target,
            EuaAuction.auction_date <= target + timedelta(days=CALENDAR_WINDOW_DAYS),
            EuaAuction.status != "cancelled",
        )
        .order_by(EuaAuction.auction_date)
    )).scalars().all()
    if not rows:
        return []
    first, last = rows[0].auction_date, rows[-1].auction_date
    span = first.strftime("%d/%m") if first == last else f"{first.strftime('%d/%m')}–{last.strftime('%d/%m')}"
    # Giờ đấu giá 09:00–11:00 giờ Frankfurt → giờ VN theo đúng ngày (CEST mùa hè = 14:00–16:00 VN, CET = 15:00–17:00).
    vn_open = datetime.combine(first, dtime(9, 0), tzinfo=_EEX_TZ).astimezone(_VN_TZ)
    vn_close = datetime.combine(first, dtime(11, 0), tzinfo=_EEX_TZ).astimezone(_VN_TZ)
    return [{
        "date": first.isoformat(),
        "datetime_vn": first.strftime("%d/%m"),
        "event": (
            f"Đấu giá EUA sơ cấp EEX: {len(rows)} phiên {span}, tổng {_vn_num(sum(r.volume for r in rows) / 1e6)} triệu tấn "
            f"({vn_open.strftime('%H:%M')}–{vn_close.strftime('%H:%M')} giờ VN; giá khớp các phiên quyết định giá CBAM quý)"
        ),
        # Nguồn cung EUA trực tiếp + quyết định giá CBAM → ưu tiên cao hơn số giàn khoan Baker Hughes ("Trung").
        "impact": "Cao",
    }]


def _format_recurring_calendar_events(events: List[Dict]) -> str:
    if not events:
        return "(Không có sự kiện định kỳ nào rơi vào cửa sổ 7 ngày này.)"
    
    formatted_lines = []
    for e in events:
        line = f'- date="{e["date"]}" datetime_vn="{e["datetime_vn"]}" event="{e["event"]}" impact="{e["impact"]}"'
        if "outcome" in e:
            line += f' outcome="{e["outcome"]}"'
        formatted_lines.append(line)
        
    return "\n".join(formatted_lines)


SECTION_PARSE_RETRIES = 3  # số lần gọi LLM tối đa cho 1 mục nếu JSON trả về không parse được


def _extract_message_text(message: "anthropic.types.Message") -> str:
    """Ghép các TextBlock trong content, bỏ qua ThinkingBlock/các block khác.

    Model có extended thinking có thể trả về 1 ThinkingBlock đứng TRƯỚC
    TextBlock trong content — content[0] không còn chắc chắn là text nữa.
    """
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


async def _call_llm(
    prompt: str,
    system: str = "",
    model: str = REPORT_MODEL_OPUS,
    max_tokens: int = 8192,
    max_retries: int = 3,
) -> Optional[str]:
    """Gọi Claude (Anthropic) async và trả về text thô, hoặc None nếu lỗi.

    system: instruction tĩnh (role, framework, quy tắc viết) — tách khỏi user
    message để Claude tuân thủ tốt hơn. KHÔNG đánh cache_control: mỗi system chỉ
    dùng 1 lần/ngày nên cache không được đọc lại, chỉ tốn thêm phụ phí ghi cache.
    model mặc định Opus cho các mục phân tích chuyên sâu (Mục 1-4, 8, biz);
    Mục 6 (tóm tắt từng bài) gọi với model=REPORT_MODEL_HAIKU — rẻ hơn, đủ dùng.
    """
    client = _get_anthropic_client()
    for attempt in range(max_retries):
        try:
            kwargs: dict = {
                "model": model,
                "max_tokens": max_tokens,
                "thinking": {"type": "disabled"},  # output là JSON có cấu trúc cố định — không cần
                                                     # extended thinking, và tắt để dành trọn max_tokens
                                                     # cho phần text thay vì bị thinking ăn bớt (gây cụt JSON).
                "messages": [{"role": "user", "content": prompt}],
            }
            if model.startswith("claude-haiku-5"):
                # Tóm tắt/JSON ngắn — effort thấp (Haiku 5.5 mặc định medium; thinking đã tắt ở trên).
                kwargs["output_config"] = {"effort": "low"}
            if system:
                # KHÔNG cache: mỗi prompt hệ thống (khung phân tích riêng từng mục) chỉ gọi 1 lần/ngày,
                # cache không bao giờ được đọc lại mà lần ghi cache bị tính thêm 25% giá input.
                kwargs["system"] = system
            response = await client.messages.create(**kwargs)
            fallback_model = REFUSAL_FALLBACK_MODELS.get(model)
            if response.stop_reason == "refusal" and fallback_model:
                logger.warning(
                    "[LLM] %s từ chối (category=%s) — gọi lại bằng %s.",
                    model, getattr(getattr(response, "stop_details", None), "category", None), fallback_model,
                )
                return await _call_llm(prompt, system=system, model=fallback_model,
                                       max_tokens=max_tokens, max_retries=max_retries)
            if response.stop_reason == "max_tokens":
                # Output bị CẮT NGANG do hết max_tokens — với JSON có cấu trúc,
                # phần bị cắt gần như chắc chắn làm JSON không hợp lệ (thiếu dấu
                # đóng ngoặc), khiến _extract_json() ở nơi gọi thất bại và rơi về
                # fallback. Log rõ nguyên nhân ở đây thay vì để nơi gọi tự đoán
                # "JSON lỗi" chung chung — dấu hiệu trực tiếp để tăng max_tokens.
                logger.warning(
                    f"[LLM] Response bị CẮT do đạt max_tokens={max_tokens} (model={model}) "
                    "— JSON trả về nhiều khả năng không hợp lệ do thiếu phần cuối; "
                    "cân nhắc tăng max_tokens hoặc giới hạn độ dài/số lượng output yêu cầu trong prompt."
                )
            return _extract_message_text(response)
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIError) as e:
            logger.error(f"Lỗi Anthropic (lần {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                # Đợi một lúc rồi thử lại do Rate Limit (429 Too Many Requests)
                await asyncio.sleep(10 * (attempt + 1))
            else:
                return None
        except Exception as e:
            logger.error(f"Lỗi gọi Claude (lần {attempt + 1}/{max_retries}): {e}")
            return None
    return None


def _escape_bare_control_chars_in_json_strings(text: str) -> str:
    """Escape các control character (newline/tab/carriage-return) xuất hiện THÔ
    bên trong JSON string literal.

    LLM (đặc biệt khi trả lời dài, nhiều gạch đầu dòng như Mục 5) thường chèn
    xuống dòng thật thay vì "\\n" hợp lệ trong 1 giá trị string — vi phạm JSON
    strict và làm json.loads() raise, khiến cả mục bị fallback dù nội dung
    LLM sinh ra hoàn toàn hợp lệ. Quét theo từng ký tự, chỉ escape khi đang ở
    TRONG 1 string literal (không đụng vào whitespace định dạng JSON ở ngoài).
    """
    out = []
    in_string = False
    escape_next = False
    for ch in text:
        if in_string:
            if escape_next:
                out.append(ch)
                escape_next = False
            elif ch == "\\":
                out.append(ch)
                escape_next = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                continue  # bỏ qua CR, giữ lại \n tương ứng nếu có (CRLF)
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def _close_unbalanced_json(text: str) -> str:
    """Thêm các dấu đóng "}" / "]" còn thiếu ở cuối (quét ngoài string literal).

    Vá trường hợp thiếu ngoặc đóng ở đuôi, string bị cắt ngang, hoặc dấu phẩy treo.
    """
    stack = []
    in_string = False
    escape_next = False
    for ch in text:
        if in_string:
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if not stack and not in_string:
        return text
    if in_string:
        text += '"'  # bị cắt giữa string — đóng string (item cuối có thể thiếu field, bước resolve sẽ loại)
    text = re.sub(r",\s*$", "", text)  # dấu phẩy treo ở cuối
    return text + "".join(reversed(stack))


def _extract_json(raw: str) -> Optional[dict]:
    """Tìm và parse khối JSON đầu tiên trong chuỗi.

    Thử parse trực tiếp trước; nếu lỗi (thường do control character thô trong
    string — xem `_escape_bare_control_chars_in_json_strings`), thử lại sau khi
    sanitize thay vì bỏ cuộc ngay, tránh mất nội dung LLM đã sinh hợp lệ.
    """
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return None

    snippet = raw[start:end]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        pass

    sanitized = _escape_bare_control_chars_in_json_strings(snippet)
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        pass

    # Model đôi khi thiếu dấu đóng ở cuối (vd thiếu "}" ngoài cùng của {"dev": {...}}) —
    # JSON bị cụt đúng chỗ đó nên nội dung vẫn đủ; tự đóng các ngoặc còn mở rồi thử lại.
    repaired = _close_unbalanced_json(sanitized)
    if repaired != sanitized:
        try:
            result = json.loads(repaired)
            logger.warning("[REPORT] JSON thiếu dấu đóng ngoặc ở cuối — đã tự vá và parse thành công.")
            return result
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(sanitized)
    except json.JSONDecodeError as e:
        # Log vị trí lỗi để biết vì sao mục rơi về fallback (thường là dấu " chưa escape
        # trong text, hoặc JSON bị cắt) thay vì chỉ thấy đuôi raw.
        ctx = snippet[max(0, e.pos - 60): e.pos + 60].replace("\n", " ")
        logger.warning(f"[REPORT] JSON không hợp lệ: {e.msg} tại vị trí {e.pos}/{len(snippet)} — ...{ctx}...")
        return None


# ─────────────────────────────────────────────────────────────────────
# Section-specific prompt builders
# ─────────────────────────────────────────────────────────────────────

def _prompt_section1(
    news_text: str, prices_text: str, eua_trend: str, eua_session_range: str, target_date: str,
    fuel_spread: str = "",
) -> tuple[str, str]:
    system = f"Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ:
{prices_text}

BIÊN ĐỘ PHIÊN LIỀN TRƯỚC EUA (dùng ĐÚNG số này, KHÔNG tự ước):
{eua_session_range}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

SPREAD THAN–KHÍ (tính sẵn, DÙNG CHUNG với Mục 2/3 — KHÔNG tự tính lại):
{fuel_spread or "Không có."}

TIN TỨC đã đánh số [N] (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, geopolitics, eu_policy, cbam) — CHỈ trích dẫn số có thật:
{news_text}

YÊU CẦU: Viết MỤC 1 — TÓM TẮT ĐIỀU HÀNH.
- Tối đa 5 bullet, mỗi cái ≤2 câu. Sắp xếp theo tác động EUA (cao→thấp). CHỈ đưa tin có cơ chế tác động tới EUA nêu rõ được trong bài; tin chỉ "có thể gián tiếp" hoặc không liên hệ trực tiếp (vd tin chính trị/kinh tế một nước không thuộc EU ETS) → KHÔNG đưa vào, không gắn nhãn "hỗ trợ nền giá EUA".
- Bullet đầu: giá đóng cửa EUA + biên độ phiên (dùng ĐÚNG số "BIÊN ĐỘ PHIÊN" ở trên; nếu "Không có dữ liệu" → bỏ qua biên độ) + xu hướng 30 ngày.
- Mỗi bullet mở đầu bằng tag đậm (**EUA:**, **Chính sách:**...). Nêu tin chính sách/địa chính trị ảnh hưởng EUA.
- CHUYỂN ĐỔI NHIÊN LIỆU (BẮT BUỘC thống nhất với Mục 2/3): khi khối "SPREAD THAN–KHÍ" có "KẾT LUẬN DÙNG CHUNG" → nhận định fuel switching PHẢI theo ĐÚNG kết luận đó (vd đã ghi "ĐÃ BÃO HÒA" thì TUYỆT ĐỐI KHÔNG viết "TTF tăng → nghiêng về chuyển sang than/cầu EUA tăng", và KHÔNG ghi "chưa tính spread" vì spread đã tính). Chỉ khi khối này báo thiếu dữ liệu mới dùng logic %Δ: than (API2) TĂNG so với khí → cầu EUA GIẢM; khí (TTF) TĂNG so với than → cầu EUA TĂNG, ghi rõ "theo %Δ, chưa tính spread". Than cho fuel switching EU là API2 (CIF ARA), KHÔNG dùng API4/NEWC. KHÔNG viết "giá than TĂNG hỗ trợ cầu EUA" (than đắt lên làm than kém cạnh tranh). NHƯNG khi than đang rẻ hơn khí rõ rệt thì ĐƯỢC (và nên) viết: điện than chạy gần tối đa, phát thải cao → HỖ TRỢ MỨC cầu EUA hiện tại (chỉ không tạo thêm lực tăng) — đây là nhánh đỡ giá, không phải nhánh giảm.
- Không có tin nổi bật → 1 bullet duy nhất "Không có sự kiện nổi bật." (source_index: null).
- Mỗi bullet là object {{"text": "...", "source_index": N | null}}: "source_index" là số [N] có thật của bài tin tức LÀM CĂN CỨ CHÍNH cho bullet đó (BẮT BUỘC kèm nếu bullet dựa trên 1 tin cụ thể ở trên), hoặc null nếu bullet chỉ dựa trên DỮ LIỆU GIÁ hệ thống (không phải tin tức) — TUYỆT ĐỐI KHÔNG bịa số không có thật.

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"1": {{"title": "Tóm tắt điều hành", "bullets": [{{"text": "...", "source_index": null}}]}}}}"""
    return system, user


_NO_PRICE_PROMPT_RULE = (
    "NGÀY KHÔNG CÓ PHIÊN GIAO DỊCH (cuối tuần) — KHÔNG có dữ liệu giá. TUYỆT ĐỐI KHÔNG nêu giá đóng cửa, "
    "%Δ ngày/Δ tuần, biên độ phiên, khối lượng giao dịch, vùng hỗ trợ/kháng cự, mức giá mục tiêu hay BẤT KỲ "
    "con số giá nào (kể cả giá phiên trước đó lấy từ tin tức); KHÔNG nhận định giá đã/sẽ tăng giảm bao nhiêu. "
    "CHỈ phân tích tin tức."
)


def _prompt_section1_news_only(news_text: str, target_date: str) -> tuple[str, str]:
    """Mục 1 cho báo cáo không có phiên giá (is_no_price_report) — chỉ dựa trên tin tức."""
    system = f"Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}
{_NO_PRICE_PROMPT_RULE}

TIN TỨC đã đánh số [N] (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, geopolitics, eu_policy, cbam) — CHỈ trích dẫn số có thật:
{news_text}

YÊU CẦU: Viết MỤC 1 — TÓM TẮT ĐIỀU HÀNH, CHỈ từ tin tức ở trên.
- Tối đa 5 bullet, mỗi cái ≤2 câu. Sắp xếp theo mức độ tác động tới cung/cầu EUA (cao→thấp).
- Mỗi bullet mở đầu bằng tag đậm (**Chính sách:**, **Năng lượng:**, **Địa chính trị:**...). KHÔNG có bullet giá EUA.
- Không có tin nổi bật → 1 bullet duy nhất "Không có sự kiện nổi bật." (source_index: null).
- Mỗi bullet là object {{"text": "...", "source_index": N | null}}: "source_index" là số [N] có thật của bài tin tức LÀM CĂN CỨ CHÍNH cho bullet đó (BẮT BUỘC kèm khi bullet dựa trên 1 tin cụ thể) — TUYỆT ĐỐI KHÔNG bịa số không có thật.

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"1": {{"title": "Tóm tắt điều hành", "bullets": [{{"text": "...", "source_index": 1}}]}}}}"""
    return system, user


def _prompt_section2(
    eua_key_facts: str, prices_text: str, eua_trend: str, gasoil_crack_spread: str,
    news_text: str, target_date: str, topics_present: List[str],
    overrides: Optional[Dict[str, str]] = None, fuel_spread: str = "",
) -> tuple[str, str]:
    framework = _eua_framework(topics_present, full=False, overrides=overrides)
    system = f"Bạn là chuyên gia phân tích thị trường carbon châu Âu.\n{CONCISENESS_RULE}\n\n{framework}"
    user = f"""Ngày báo cáo: {target_date}

SỐ LIỆU EUA (dùng ĐÚNG, KHÔNG bịa):
{eua_key_facts}

DỮ LIỆU GIÁ (dùng ĐÚNG):
{prices_text}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

GASOIL CRACK SPREAD (dùng đúng, KHÔNG tự tính lại):
{gasoil_crack_spread}

SPREAD THAN–KHÍ (tính sẵn — dùng đúng "KẾT LUẬN DÙNG CHUNG" để xếp bullish/bearish cho TTF/than, KHÔNG tự tính lại):
{fuel_spread}

TIN TỨC đã đánh số [N] (CHỈ trích dẫn số có thật):
{news_text}

YÊU CẦU: Viết "market_drivers" — BẢNG ĐỘNG LỰC THỊ TRƯỜNG.
Cấu trúc: {{"bullish": [...], "bearish": [...]}}. Mỗi phần tử gồm "tag", "text", "source_index":
- CHỈ xếp vào "bullish"/"bearish" yếu tố tạo LỰC THAY ĐỔI giá EUA. Yếu tố chỉ GIỮ MỨC/ĐỠ GIÁ hoặc đã được kết luận "không tạo thêm lực tăng/giảm" (vd dầu thô neo cao không đổi; chênh khí − than mở rộng khi chuyển đổi đã bão hòa) KHÔNG xếp vào cột nào — Mục 3 sẽ nêu ở phần Phân tích.
- "FACT" CHỈ chứa dữ kiện đo được (số, sự kiện đã xảy ra) — KHÔNG chứa diễn giải ("cho thấy", "phản ánh", "chứng tỏ", "vẫn vững", "vùng giá cao vừa thiết lập"...); phần diễn giải tách thành 1 mục "OPINION" riêng. Tỷ lệ đặt mua/chào bán đấu giá KHÔNG chứng minh "cầu tuân thủ" (người tham gia gồm cả tổ chức tài chính).
- "tag": "FACT", "OPINION" hoặc "CLAIM" — phân biệt theo ĐỘ CHẮC CHẮN: "CLAIM" = tuyên bố/con số do MỘT BÊN nêu (quan chức, chính phủ, công ty) chưa được xác nhận độc lập — text PHẢI mở đầu bằng chủ thể ("Theo Phó Thủ tướng...") và NÊN kèm source_index; KHÔNG dùng "OPINION" hay "FACT" cho loại này.
  + "FACT" = thông tin ĐÃ XÁC NHẬN: (a) từ DỮ LIỆU GIÁ hệ thống, dựa trên Δ ngày + Δ tuần của instrument (source_index=null) hoặc (b) từ TIN TỨC đánh số nêu sự kiện/số liệu đã xảy ra (BẮT BUỘC kèm source_index).
  + "OPINION" = nhận định/suy luận/dự báo từ tin tức hoặc phân tích của bạn (NÊN kèm source_index nếu gắn bài cụ thể, null nếu suy luận chung).
- "text": ĐÚNG 1 câu TÓM TẮT NGẮN GỌN, ĐỊNH TÍNH về trạng thái/động lực của yếu tố này — TUYỆT ĐỐI KHÔNG nhắc lại giá đóng cửa hay %Δ ngày/Δ tuần bằng SỐ (các số này đã hiển thị sẵn ở "Bảng giá nhanh" của báo cáo — lặp lại là thừa, không thêm thông tin). Thay vào đó mô tả ĐỊNH TÍNH vị thế/xu hướng — vd đang giữ vùng cao/thấp (so với biên độ 30 ngày), phá vỡ/chưa phá vỡ ngưỡng hỗ trợ-kháng cự, đi ngang tích lũy, biến động mạnh bất thường, thanh khoản tăng/giảm rõ rệt... — rồi nêu NGẮN GỌN nguyên nhân/sự kiện phía sau nếu có (vd "EUA đang giữ ở vùng cao, thanh khoản duy trì ổn định." hoặc "TTF suy yếu về vùng thấp nhiều tuần do nguồn cung LNG dồi dào."). Áp dụng ĐÚNG cách viết định tính này cho MỌI instrument trong mục này (EUA, TTF, than, dầu, điện Đức...), KHÔNG riêng EUA — KHÔNG giải thích/suy luận tác động lên EUA ở đây, KHÔNG viết chuỗi nhân quả hay kết luận hướng ảnh hưởng tới EUA (phần đó thuộc Mục 3).
- "source_index": số [N] có thật trong danh sách, hoặc null.
Xếp bullish/bearish theo ĐÚNG chuỗi nhân quả tới EUA (không theo chiều tăng/giảm bề ngoài của instrument) — chuỗi nhân quả CHỈ dùng để QUYẾT ĐỊNH xếp vào bullish hay bearish, KHÔNG viết ra trong "text". CHỈ đưa yếu tố có dữ liệu/tin hỗ trợ, không bịa thêm.
- KẾT LUẬN SPREAD THAN–KHÍ ƯU TIÊN TRƯỚC MỌI QUY TẮC %Δ BÊN DƯỚI: nếu khối "SPREAD THAN–KHÍ" ghi chuyển đổi khí→than "ĐÃ BÃO HÒA" → TUYỆT ĐỐI KHÔNG xếp "TTF tăng/bật mạnh" (hay "than rẻ tương đối so với khí") vào "bullish"; TTF tăng khi đó chỉ được xếp "bearish" nếu có tin xác nhận công nghiệp cắt giảm sản lượng, còn không thì bỏ khỏi bảng.
- NHẤT QUÁN VỚI MỤC 3 (fuel switching là giá TƯƠNG ĐỐI, CHỈ áp dụng khi khối spread KHÔNG ghi "ĐÃ BÃO HÒA"): than tụt lại/rẻ hơn tương đối so với khí (khí tăng mạnh hơn than, hoặc than giảm trong khi khí tăng) → than cạnh tranh hơn → cầu EUA tăng → xếp BULLISH; than tăng nhanh hơn khí hoặc khí rẻ đi tương đối → xếp BEARISH. TUYỆT ĐỐI KHÔNG xếp "than tụt lại so với khí" vào bearish. Mỗi yếu tố chỉ xuất hiện ở MỘT phía (không có cùng 1 yếu tố ở cả bullish lẫn bearish) và phải cùng chiều với kết luận ở Mục 3.
- Khi "SỐ LIỆU EUA" ghi dữ liệu khối lượng không hợp lệ/không đủ để so sánh → KHÔNG thêm mục khối lượng và KHÔNG viết 'khối lượng tăng đột biến', 'lực chốt lời' hay bất kỳ suy luận nào dựa trên khối lượng ở bất kỳ phần nào.
- KHỐI LƯỢNG GIAO DỊCH EUA: dựa vào khối lượng phiên liền trước so với TB các phiên gần nhất đã nêu trong "SỐ LIỆU EUA" ở trên (KHÔNG tự bịa số) để BẮT BUỘC thêm 1 mục "FACT" riêng mô tả ĐỊNH TÍNH tín hiệu khối lượng vào bullish/bearish (KHÔNG nêu số khối lượng cụ thể trong "text"), theo đúng logic "khối lượng xác nhận xu hướng giá":
  + Khối lượng tăng đột biến CÙNG chiều với giá tăng/giảm phiên đó → tín hiệu xác nhận lực mua/bán mạnh, xếp cùng chiều bullish/bearish tương ứng (vd "Khối lượng giao dịch tăng vọt, xác nhận lực mua mạnh.").
  + Khối lượng giảm mạnh trong khi giá vẫn biến động mạnh, hoặc khối lượng tăng đột biến nhưng giá gần như đi ngang → tín hiệu YẾU/thiếu xác nhận, ghi rõ là "OPINION" và nêu rủi ro đảo chiều/thiếu động lực thay vì kết luận dứt khoát.
  + Khối lượng ở mức bình thường → KHÔNG cần thêm mục riêng cho khối lượng (chỉ dùng khi có bất thường thực sự).

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"2": {{"market_drivers": {{"bullish": [{{"tag": "FACT|OPINION|CLAIM", "text": "...", "source_index": null}}], "bearish": [{{"tag": "FACT|OPINION|CLAIM", "text": "...", "source_index": null}}]}}}}}}"""
    return system, user


def _prompt_key_developments(
    news_text: str, target_date: str, topics_present: List[str],
    overrides: Optional[Dict[str, str]] = None, news_only: bool = False, fuel_spread: str = "",
) -> tuple[str, str]:
    """ "Diễn biến chính" — hiển thị ngay dưới Bảng giá nhanh: CHỈ các SỰ KIỆN TIN TỨC
    nổi bật có tác động tới giá/cung/cầu của các hợp đồng theo dõi (EUA + năng lượng,
    trực tiếp hoặc gián tiếp) — KHÔNG phải diễn
    biến giá tăng/giảm của các hợp đồng (phần đó đã có ở Bảng giá nhanh). Cố ý KHÔNG
    đưa dữ liệu giá vào prompt để LLM không viết lại biến động giá. Mỗi tin BẮT BUỘC
    có nguồn bài viết (source_index → backend map sang tên/URL thật).
    `news_only=True`: báo cáo không có phiên giá — khung phân tích bỏ quy tắc Δ ngày/Δ tuần."""
    framework = _eua_framework(topics_present, full=False, overrides=overrides, news_only=news_only)
    system = f"Bạn là chuyên gia phân tích thị trường carbon châu Âu.\n{CONCISENESS_RULE}\n\n{framework}"
    user = f"""Ngày báo cáo: {target_date}

TIN TỨC đã đánh số [N] — CHỈ trích dẫn số có thật:
{news_text}

SPREAD THAN–KHÍ (tính sẵn — tin về khí/than/điện PHẢI xếp chiều EUA theo đúng "KẾT LUẬN DÙNG CHUNG" này, KHÔNG dùng
tiền đề chung chung "TTF tăng → chuyển sang than → cầu EUA tăng" khi kết luận nói chuyển đổi đã bão hòa):
{fuel_spread or "Không có."}

YÊU CẦU: Viết "key_developments" — DIỄN BIẾN CHÍNH: các tin tức nổi bật trong danh sách trên CÓ TÁC ĐỘNG ĐẾN GIÁ của các hợp đồng đang theo dõi (EUA, TTF, điện Đức, than, dầu, gasoil...) — trực tiếp hay gián tiếp đều được, miễn bài nêu được sự kiện/nguyên nhân cụ thể ảnh hưởng tới cung, cầu hoặc giá của ít nhất 1 hợp đồng. Gồm: chính sách EU ETS/MSR/cap/đấu giá/CBAM, số liệu phát thải, động thái thị trường carbon, gián đoạn nguồn cung năng lượng, địa chính trị, thời tiết, tồn kho, OPEC+, sản lượng điện tái tạo/hydrogen, dự báo của tổ chức... Không cần bắt buộc tác động thẳng lên EUA.

QUY TẮC BẮT BUỘC — ĐÂY LÀ MỤC TIN TỨC, KHÔNG PHẢI MỤC GIÁ:
- TUYỆT ĐỐI KHÔNG viết diễn biến GIÁ (không "giá tăng/giảm X%", giá đóng cửa, hỗ trợ/kháng cự, biến động phiên/tuần) — phần đó đã có ở Bảng giá nhanh. Số liệu của chính sự kiện (khối lượng đấu giá, mức cắt phân bổ...) được phép.
- BỎ các bài chỉ là bản tin thị trường/tổng hợp giá không nêu sự kiện cụ thể.
- Từ 3 đến 6 mục (ít hơn nếu thật sự không đủ tin), xếp theo mức độ tác động mạnh → yếu. Nhiều bài cùng 1 sự kiện → gộp thành 1 mục. Bỏ tin không nêu được tác động nào tới giá/cung/cầu của hợp đồng nào.
- Mỗi mục là object {{"title": "...", "summary": "...", "impact": "tăng" | "giảm" | "trung lập", "source_index": N}}:
  + "title": tiêu đề bài báo (dịch/rút gọn sang tiếng Việt nếu bài tiếng Anh, giữ đúng ý, không thêm thắt).
  + "summary": 1–2 câu NGẮN GỌN: nêu nội dung chính của tin, rồi tác động tới giá/cung/cầu của hợp đồng liên quan, nêu rõ hợp đồng nào (vd "... → hỗ trợ giá TTF, gián tiếp thúc đẩy nhu cầu EUA"). KHÔNG chép lại tiêu đề, KHÔNG ghi nguồn/ngày trong câu (hệ thống tự thêm).
  + "impact": chiều tác động RÒNG lên GIÁ EUA (giao diện hiển thị là "Hỗ trợ EUA"/"Áp lực EUA"/"Chờ xác nhận"; Mục 2/3 bắt buộc dùng lại đúng chiều này) — KHÔNG phải chiều của hợp đồng năng lượng: tin đẩy TTF tăng mà chuyển đổi nhiên liệu đã bão hòa → "trung lập" với EUA. "tăng" = hỗ trợ EUA, "giảm" = áp lực giảm EUA, "trung lập" = chưa rõ chiều / chỉ tác động hợp đồng khác.
  + "source_index": BẮT BUỘC là số [N] có thật của bài làm căn cứ — mục nào không gắn được với 1 bài cụ thể thì BỎ, TUYỆT ĐỐI KHÔNG bịa số.
- Không có tin nào đạt yêu cầu → "key_developments": [].

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"dev": {{"key_developments": [{{"title": "...", "summary": "...", "impact": "tăng", "source_index": 1}}]}}}}"""
    return system, user


_KEY_DEV_IMPACTS = {"tăng", "giảm", "trung lập"}


def _resolve_key_developments(items: Optional[List[Any]], index_lookup: Dict[int, Dict]) -> List[Dict]:
    """Chuẩn hoá "key_developments" → {"text", "impact", "source_name", "source_url"}.
    Mục này yêu cầu LUÔN có nguồn bài viết — mục nào source_index không map được
    tới bài có thật thì bỏ hẳn (không hiển thị tin không kiểm chứng được)."""
    resolved = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        text = (it.get("summary") or it.get("text") or "").strip()
        src_art = index_lookup.get(_first_source_index(it.get("source_index")))
        if not text or not src_art:
            continue
        impact = str(it.get("impact") or "").strip().lower()
        resolved.append({
            "title": title or src_art["title"],
            "text": text,
            "impact": impact if impact in _KEY_DEV_IMPACTS else "trung lập",
            "source_name": src_art["source"],
            "source_url": src_art["url"],
            "source_date": src_art.get("published_date"),
        })
    return resolved



_DEV_IMPACT_LABEL = {"tăng": "HỖ TRỢ EUA (chỉ được ở cột tăng)", "giảm": "ÁP LỰC EUA (chỉ được ở cột giảm)",
                     "trung lập": "CHỜ XÁC NHẬN (KHÔNG xếp vào cột tăng lẫn giảm)"}
_DRIVER_SIDE_IMPACT = {"bullish": "tăng", "bearish": "giảm"}


def _dev_verdict_block(dev_impacts: Dict[str, str], index_lookup: Dict[int, Dict]) -> str:
    """Khối "chiều đã chốt" cho prompt Mục 2/3, đánh theo số [N] của CHÍNH mục đó."""
    lines = [f"[{n}] {art['title'][:120]} → {_DEV_IMPACT_LABEL[dev_impacts[art['url']]]}"
             for n, art in index_lookup.items() if art.get("url") in dev_impacts]
    if not lines:
        return ""
    return (
        "\n\nCHIỀU TÁC ĐỘNG ĐÃ CHỐT Ở \"DIỄN BIẾN CHÍNH\" (BẮT BUỘC — cùng 1 tin KHÔNG được đánh giá khác chiều ở BẤT KỲ chỗ "
        "nào của mục này: động lực thị trường, nhóm Phân tích, \"Cần theo dõi\"; nhắc lại tin này thì dùng đúng chiều dưới đây. "
        "Watchpoint về tin này chỉ được nêu nhánh khác chiều khi là diễn biến MỚI chưa xảy ra và ghi rõ vì sao khác):\n"
        + "\n".join(lines)
    )


def _drop_conflicting_drivers(drivers: Dict[str, Any], index_lookup: Dict[int, Dict], dev_impacts: Dict[str, str]) -> Dict[str, Any]:
    """Bỏ động lực Mục 2 trích CÙNG bài với Diễn biến chính nhưng nằm khác chiều (lưới an toàn sau prompt)."""
    out = {}
    for side in ("bullish", "bearish"):
        kept = []
        for it in drivers.get(side) or []:
            art = index_lookup.get(_first_source_index(it.get("source_index"))) if isinstance(it, dict) else None
            impact = dev_impacts.get(art["url"]) if art else None
            if impact and impact != _DRIVER_SIDE_IMPACT[side]:
                logger.warning("[REPORT] Mục 2: bỏ động lực %s trái chiều Diễn biến chính (%s): %s",
                               side, impact, str(it.get("text"))[:120])
                continue
            kept.append(it)
        out[side] = kept
    return out


def _prompt_section3(
    news_text: str, prices_text: str, eua_trend: str, eua_session_range: str,
    gasoil_crack_spread: str, target_date: str, topics_present: List[str],
    overrides: Optional[Dict[str, str]] = None, eua_technical_levels: str = "", fuel_spread: str = "",
    prev_scenarios: str = "", cbam_signal: str = "", open_orders: str = "",
) -> tuple[str, str]:
    framework = _eua_framework(topics_present, full=True, overrides=overrides)
    system = f"Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.\n{CONCISENESS_RULE}\n\n{framework}"
    user = f"""Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ PHIÊN VỪA QUA (dùng số liệu này để xây dựng chuỗi nhân quả, TUYỆT ĐỐI KHÔNG tự bịa số khác):
{prices_text}

BIÊN ĐỘ PHIÊN LIỀN TRƯỚC CỦA EUA:
{eua_session_range}

GASOIL CRACK SPREAD (số liệu đã tính sẵn — PHẢI dùng đúng con số này nếu nhắc đến crack spread, KHÔNG tự tính lại từ giá Gasoil/Brent thô vì khác đơn vị):
{gasoil_crack_spread}

SPREAD THAN–KHÍ (tính sẵn từ TTF/API2/EUA — PHẢI dùng đúng số và "KẾT LUẬN DÙNG CHUNG" khi phân tích fuel switching, KHÔNG tự tính lại; ghi là "ước tính theo mô hình chi phí biên", KHÔNG ghi "ước tính thô"/"chưa kiểm chứng"; KHÔNG dùng API4/NEWC cho fuel switching EU):
{fuel_spread or "Không có."}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

MỐC KỸ THUẬT EUA (tính sẵn từ OHLC thật — nguồn DUY NHẤT cho mức giá ở "trading_scenarios"):
{eua_technical_levels or "Không có đủ dữ liệu mốc kỹ thuật — KHÔNG đưa mức giá cụ thể, direction = đi ngang."}

KỊCH BẢN CỦA BÁO CÁO TRƯỚC (đối chiếu — người đọc so sánh các ngày với nhau):
{prev_scenarios or "Không có."}

LỆNH ĐANG MỞ (mô phỏng từ thẻ chiến lược các báo cáo trước — đã khớp, chưa chạm cắt lỗ/mục tiêu; đối chiếu ở "open_positions"):
{open_orders or "Không có lệnh nào đang mở."}

TÍN HIỆU PHÒNG VỆ CBAM (nhãn DUY NHẤT do hệ thống quyết định — "hedge_action" chép đúng mã nhãn này):
{cbam_signal or "Không có."}
NHẤT QUÁN VỚI BÁO CÁO TRƯỚC (BẮT BUỘC khi có kịch bản trước): nếu hôm nay đổi "direction"/xác suất của 1 horizon, hoặc dời Mục tiêu/cắt lỗ đáng kể, thì đoạn "**Tổng hợp:**" PHẢI có 1 câu ngắn nêu rõ điều chỉnh (cũ → mới) và lý do bằng dữ liệu; nếu kịch bản ngắn hạn trước đã đi NGƯỢC thực tế (giá đi ngược chiều dự báo) thì thừa nhận thẳng và hạ xác suất hoặc giải thích vì sao vẫn giữ — TUYỆT ĐỐI KHÔNG lặng lẽ đổi số.
KHUNG "SO VỚI BÁO CÁO TRƯỚC" (BẮT BUỘC khi có kịch bản/đối chiếu ở trên): thêm trường "vs_previous" (3–4 câu, tối đa ~90 từ) ở cấp mục 3 — hiển thị ở TRANG ĐẦU ngay dưới bảng "Dự báo trước: đúng hay sai" (bảng đã có số liệu, KHÔNG chép lại từng dòng), gồm: (1) THỪA NHẬN THẲNG mọi dự báo "SAI" trong "ĐỐI CHIẾU DỰ BÁO GẦN ĐÂY" — nêu ngày báo cáo, chiều đã dự báo, kết quả (chạm cắt lỗ ngày nào hoặc %Δ); dự báo SAI dù gắn xác suất CAO thì phải nói rõ và nêu vì sao độ tin cậy đó không có cơ sở (vd thiếu spread than–khí/dữ liệu vị thế khi chốt "Cao"); NGUYÊN NHÂN sai là tiền đề nào không đúng — KHÔNG né, KHÔNG đổ cho "biến động ngẫu nhiên"; dòng nào ghi "LỖI THIẾT KẾ LỆNH" (R:R tại giá vào thấp, cắt lỗ quá sát so ATR) thì PHẢI nêu lỗi lệnh đó như 1 nguyên nhân; nếu nguyên nhân là THIẾU DỮ LIỆU mà hệ thống vẫn chưa có (vd vị thế quỹ/COT) thì KHÔNG được chỉ nhắc lại — ghi rõ biện pháp đang áp dụng: "hệ thống chưa có dữ liệu vị thế quỹ nên mọi kịch bản tối đa xác suất Trung bình"; không có dự báo sai → 1 câu ngắn xác nhận; (2) hôm nay GIỮ hay ĐỔI hướng (cũ → mới) và lý do bằng dữ liệu; (3) TIỀN ĐỀ nào của báo cáo trước đã thay đổi/không còn đúng (vd trước coi "TTF tăng → chuyển sang than → cầu EUA tăng" nhưng spread nay cho thấy chuyển đổi đã bão hòa) — nêu thẳng, không né. Không có báo cáo trước → "vs_previous" = "".

CÂN NHẮC BẰNG CHỨNG NGƯỢC CHIỀU (BẮT BUỘC): trước khi chốt "**Tổng hợp:**", rà các tin ở dưới đi NGƯỢC kết luận của bạn (vd nguồn cung dầu phục hồi, sản lượng kỷ lục, lạm phát/lãi suất tăng khi kết luận tăng giá) — tin ngược chiều có tác động thật phải được nêu trong "Phân tích" hoặc hạ xác suất kịch bản; không chỉ trình bày 1 chiều.

TIN TỨC LIÊN QUAN (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, energy_renewable, energy_hydrogen, geopolitics, eu_policy, cbam, vcm, global_carbon_market, vietnam_carbon_policy):
{news_text}

YÊU CẦU: Viết MỤC 3 — PHÂN TÍCH CÁC YẾU TỐ NĂNG LƯỢNG TƯƠNG QUAN, CHÍNH SÁCH ẢNH HƯỞNG ĐẾN GIÁ EUA.
Đây là mục phân tích SÂU NHẤT của báo cáo — PHẢI đầy đủ nội dung bắt buộc, không bỏ trống phần nào bên dưới. NHƯNG PHẢI VIẾT SÚC TÍCH, TRỰC TIẾP: đi thẳng vào số liệu và kết luận, KHÔNG câu dẫn dắt/đệm không mang thông tin, KHÔNG lặp lại số liệu/nội dung đã nêu ở mục/trường khác trong cùng báo cáo — "đủ nội dung" nghĩa là đủ Ý bắt buộc, không phải đủ CÂU CHỮ. Mục này gồm 3 phần con:

A. "instrument_notes": object dạng {{"<mã>": "ghi chú ngắn"}} — cột "Ghi chú" của Bảng giá nhanh (Mục 2), hiển thị ngay cạnh mã đó trên giao diện. Phần này gộp nội dung "Diễn biến chính" vào bảng tổng hợp giá: heading "Diễn biến chính" KHÔNG còn tồn tại, TUYỆT ĐỐI KHÔNG đưa vào "analysis_blocks" ở mục B. Giá và biến động đã có sẵn trong bảng → ghi chú KHÔNG nêu lại.
   NGUYÊN TẮC CỐT LÕI (áp dụng cho MỌI mã): mỗi ghi chú PHẢI là NGUYÊN NHÂN/SỰ KIỆN CỤ THỂ, được tin tức NÊU RÕ, làm ảnh hưởng tới giá hoặc CUNG/CẦU của mã đó (hoặc của EUA), kèm TÁC ĐỘNG của nó theo dạng "<sự kiện cụ thể> → <tác động lên giá/cung/cầu>". Ví dụ đúng: "Nhiệt độ thấp hơn trung bình làm tăng nhu cầu sưởi, hỗ trợ giá khí". CẤM: (i) thông tin chi tiết phụ/mô tả về hợp đồng hay thị trường (kỳ hạn, khối lượng giao dịch, vị thế, thanh khoản, lịch đáo hạn, mô tả chung chung "thị trường biến động", "giá được theo dõi"...); (ii) nêu nguyên nhân khi tin tức KHÔNG xác định nguyên nhân đó — không suy diễn, không gán nguyên nhân cho biến động giá nếu bài viết không nói rõ; (iii) dự báo/quan điểm chung của tổ chức không gắn với 1 sự kiện cụ thể; (iv) số giá/Δ ngày/Δ tuần dưới bất kỳ hình thức nào.
   MỘT TIN ẢNH HƯỞNG NHIỀU HỢP ĐỒNG: nếu 1 sự kiện tác động tới nhiều mã (vd lạnh/thời tiết ảnh hưởng cả TTF và DEBY1; xung đột Trung Đông ảnh hưởng Brent, WTI, TTF; chính sách EU ảnh hưởng cả EUA và CBAM) → ghi vào TỪNG mã bị tác động, mỗi mã nêu đúng tác động của sự kiện lên CHÍNH mã đó (không chép nguyên văn giống nhau cho các mã), và với mã không phải EUA thì nêu cụ thể tác động lên EUA chỉ khi cơ chế truyền dẫn rõ ràng (theo chuỗi nhân quả của KHUNG PHÂN TÍCH).
   Phân nhóm theo "B. DANH MỤC THEO DÕI":
   - NHÓM 1 — Năng lượng & nhiên liệu hóa thạch: sự kiện cụ thể (thời tiết, gián đoạn/thay đổi nguồn cung, tồn kho EIA/API, OPEC+, xung đột/sanctions, rig count, sản lượng điện gió/mặt trời/thủy điện thấp hoặc cao bất thường...) mà tin tức nêu rõ là làm đổi cung/cầu hoặc giá của TTF, NEWC/API2, Brent/WTI, GASOIL, DEBY1 → ghi vào mã liên quan trực tiếp (Brent/WTI cho dầu, TTF cho khí, NEWC/API2 cho than, DEBY1 cho điện/năng lượng tái tạo), nêu rõ tác động. Sự kiện địa chính trị/gián đoạn nguồn cung vẫn PHẢI ghi dù giá chưa kịp phản ánh. Hydrogen/thép xanh không gắn mã nào → chỉ ghi vào "EUA" nếu có tác động cụ thể lên nhu cầu/chính sách carbon.
   - NHÓM 2 — Hạn ngạch & tín chỉ carbon: ghi vào "EUA" CHỈ các yếu tố tác động trực tiếp cung/cầu EUA (lịch/khối lượng đấu giá bất thường, MSR, thay đổi phát thải/nhu cầu tuân thủ, dòng vốn đầu cơ hoặc thay đổi vị thế lớn được nêu là nguyên nhân giá, nhu cầu từ chuyển đổi nhiên liệu...). Thị trường carbon NGOÀI EU (China/Korea ETS, California, CORSIA) và VCM — BỎ QUA HOÀN TOÀN trừ khi tin nêu rõ cơ chế nối sang EUA.
   - NHÓM 3 — Chính sách: thay đổi chính sách CỤ THỂ nêu rõ tác động tới cung/cầu/giá → "CBAM" (nếu mã có trong DỮ LIỆU GIÁ, nếu không thì "EUA"); chính sách EU khác ảnh hưởng ETS (Fit-for-55, mở rộng phạm vi ETS, thay đổi cap/MSR...) → "EUA"; chính sách carbon Việt Nam hoặc VCM không có mã tương ứng → BỎ QUA HOÀN TOÀN.
   QUY TẮC CHUNG: CHỈ dùng đúng các mã CÓ THẬT trong "DỮ LIỆU GIÁ" ở trên (đúng chính tả, vd "EUA", "TTF", "DEBY1", "NEWC", "BRENT", "WTI", "GASOIL", "CBAM" — TUYỆT ĐỐI KHÔNG bịa mã). Mỗi ghi chú tối đa 1–2 câu NGẮN. Mã nào KHÔNG có nguyên nhân/sự kiện cụ thể đáp ứng nguyên tắc cốt lõi → KHÔNG thêm khoá đó (không viết chuỗi rỗng, không viết ghi chú cho đủ). Nếu không mã nào đáp ứng: "instrument_notes" = {{}}.

B. "analysis_blocks": mảng gồm "heading" và "content". Heading "Phân tích" và "Cần theo dõi" LUÔN PHẢI có mặt; heading "Quan điểm thị trường" LÀ TÙY CHỌN — xem quy tắc riêng ở mục 2 bên dưới. QUY TẮC ĐỘ DÀI CHUNG: mỗi Ý/gạch đầu dòng trong "content" tối đa 1–2 câu NGẮN GỌN, đi thẳng vào số liệu/kết luận — không diễn giải dài dòng, không viết chung chung. NGOẠI LỆ: heading "Phân tích" và "Cần theo dõi" KHÔNG bị giới hạn 1–2 câu mỗi gạch đầu dòng — xem "ĐỘ DÀI RIÊNG" ngay trong quy tắc của từng heading đó bên dưới. "Phân tích" ưu tiên NGẮN GỌN, TRỰC DIỆN (đủ số liệu + kết luận, KHÔNG giải thích lại cơ chế/logic suy luận); "Cần theo dõi" ưu tiên ĐẦY ĐỦ 2 kịch bản trái chiều. Cả hai đều không thêm câu đệm/chuyển tiếp không mang thông tin mới:
   1. heading="Phân tích" — PHÂN TÍCH TÁC ĐỘNG (gộp chung cả phân tích liên thị trường Gas–Than–Điện Đức vào đây, KHÔNG tách thành mục riêng): dựa trên đúng các thông tin đã nêu ở "instrument_notes" (mục A ở trên) và số liệu ở "DỮ LIỆU GIÁ" (KHÔNG lặp lại số liệu, chỉ tham chiếu ngắn gọn khi cần làm căn cứ trực tiếp cho kết luận), phân tích thông tin của TỪNG NHÓM đã có mã tương ứng trong "instrument_notes" sẽ ảnh hưởng thế nào đến CUNG/CẦU và GIÁ EUA. BẮT BUỘC áp dụng ĐÚNG chuỗi nhân quả trong "A. CÁC MỐI LIÊN HỆ LIÊN THỊ TRƯỜNG" của KHUNG PHÂN TÍCH ở trên (fuel switching, CBAM/ETS, chính sách/MSR, địa chính trị...) để XÁC ĐỊNH đúng chiều/mức độ tác động — TUYỆT ĐỐI KHÔNG tự sinh chuỗi nhân quả khác hay suy diễn lệch khỏi khung chuẩn đó.
      NGUYÊN TẮC TRÌNH BÀY BẮT BUỘC — TRỰC DIỆN, KHÔNG GIẢI THÍCH LẠI LOGIC: các bước suy luận (i)–(v) bên dưới CHỈ dùng để bạn XÁC ĐỊNH ĐÚNG chiều/mức độ tác động ở NỘI BỘ suy nghĩ của bạn — khi viết ra "content", TUYỆT ĐỐI KHÔNG diễn giải lại từng bước cơ chế/logic kiểu văn xuôi "vì X nên Y nên Z nên..."; chỉ nêu SỐ LIỆU/SỰ KIỆN thật ngắn rồi ĐI THẲNG tới KẾT LUẬN TÁC ĐỘNG, không đường vòng qua mô tả cơ chế. Bôi đậm (**...**) tên mã/nhóm hoặc cụm kết luận chính ở đầu mỗi gạch đầu dòng để người đọc dễ quét mắt vào đúng nội dung cần chú ý.
      BỘ LỌC BẮT BUỘC TRƯỚC KHI VIẾT (áp dụng cho MỌI nhóm, không riêng Nhóm 1): mục này CHỈ chứa những yếu tố/mã/sự kiện mà — sau khi áp ĐÚNG chuỗi nhân quả chuẩn — THỰC SỰ tạo tác động có căn cứ, có hướng rõ ràng (tăng hoặc giảm, kể cả tác động nhẹ, miễn có cơ chế rõ và dữ liệu ủng hộ) lên CUNG/CẦU hoặc GIÁ EUA. Yếu tố nào rơi vào 1 trong 2 trường hợp sau → BỎ QUA HOÀN TOÀN, KHÔNG viết thành 1 gạch đầu dòng ở đây (dù yếu tố đó đã có ghi chú ở "instrument_notes" vì lý do khác, vd chỉ để đưa tin):
        - Biến động không đáng kể (Δ ngày và Δ tuần đều gần như đi ngang) và không đủ kích hoạt bất kỳ cơ chế dispatch/hedge/chính sách nào.
        - Theo đúng chuỗi nhân quả chuẩn, yếu tố này KHÔNG dẫn tới tác động nào lên cung/cầu/giá EUA (trung lập thật sự theo logic, không phải chỉ vì thiếu số liệu).
      PHÂN BIỆT RÕ với trường hợp TÍN HIỆU MÂU THUẪN/CHƯA ĐỦ MẠNH ở bước (i) bên dưới (Δ ngày và Δ tuần trái chiều nhưng cả 2 đều là biến động thực) — trường hợp đó VẪN PHẢI giữ lại và viết thành 1 gạch đầu dòng, vì đây là rủi ro/tín hiệu cần theo dõi chứ không phải yếu tố trung lập/không tác động.
      LOẠI TRỪ ĐÍCH DANH (không suy diễn gián tiếp — nêu thẳng để tránh bị đưa nhầm vào "Phân tích"):
      {chains.get_block("NON_EUA_CARBON_MARKETS", overrides)}
      Dù các tin này ĐÃ có thể có ghi chú ở "instrument_notes" (NHÓM 2, mục đích chỉ để thông tin), TUYỆT ĐỐI KHÔNG đưa vào "Phân tích" và TUYỆT ĐỐI KHÔNG tính là 1 yếu tố "trung lập" riêng cho nội dung này, theo đúng quy tắc ở trên.
      ĐỘ DÀI RIÊNG CHO HEADING NÀY: KHÔNG áp dụng giới hạn 1–2 câu của "QUY TẮC ĐỘ DÀI CHUNG" ở trên (mỗi gạch có thể dài hơn 2 câu nếu cần đủ Δ ngày/Δ tuần + kết luận), NHƯNG PHẢI súc tích, trực diện theo đúng "NGUYÊN TẮC TRÌNH BÀY BẮT BUỘC" ở trên — không câu mở đầu/đệm/chuyển tiếp thừa, không lặp lại nguyên văn nội dung đã nêu ở "instrument_notes" (chỉ nhắc lại khi trực tiếp làm căn cứ cho kết luận trong chính câu đó), và KHÔNG diễn giải lại từng bước cơ chế/logic suy luận.
      QUY TẮC SUY LUẬN NỘI BỘ CHO NHÓM 1 (Năng lượng & nhiên liệu hóa thạch) — áp dụng cho MỌI mã/sự kiện đã có ghi chú ở "instrument_notes" nhóm này để XÁC ĐỊNH đúng chiều/mức độ tác động (đây là cơ sở suy luận NỘI BỘ, không phải văn mẫu để chép lại nguyên văn vào "content" — xem "NGUYÊN TẮC TRÌNH BÀY BẮT BUỘC" ở trên). LƯU Ý: tên chuỗi/nhánh viết HOA bên dưới (FUEL_SWITCHING, POWER_EUA_TWO_WAY, OIL_GASOIL, GEOPOLITICS_SUPPLY_CHAIN, "nhánh (a)"...) CHỈ để bạn xác định ĐÚNG cơ chế cần áp dụng — TUYỆT ĐỐI KHÔNG chép các tên này, và TUYỆT ĐỐI KHÔNG diễn giải lại các bước (i)-(v) này bằng lời trong "content"; "content" chỉ chứa SỐ LIỆU + KẾT LUẬN đã áp dụng đúng các bước này, không mô tả quá trình suy luận:
        (i) ĐỐI CHIẾU KHUNG THỜI GIAN: so Δ ngày VÀ Δ tuần của chính mã đó. ĐỒNG THUẬN (cùng chiều) → xác nhận xu hướng bền vững, đủ cơ sở kết luận dứt khoát chiều tác động EUA. MÂU THUẪN (trái chiều) → nêu rõ đây là tín hiệu ngắn hạn/chưa đủ mạnh, cần thêm phiên xác nhận, KHÔNG được chốt chiều tác động EUA dứt khoát.
        (ii) GAS/THAN → áp dụng FUEL_SWITCHING: đây là cơ chế GIÁ TƯƠNG ĐỐI, không phải giá tuyệt đối của riêng 1 nhiên liệu — Gas tăng (than không đổi/tăng ít hơn) → than cạnh tranh hơn → dispatch dịch sang than → phát thải & cầu EUA tăng; Gas giảm HOẶC Than tăng (2 tín hiệu CÙNG CHIỀU, không phải trái chiều) → gas cạnh tranh hơn → dispatch dịch sang gas → phát thải & cầu EUA giảm. TUYỆT ĐỐI KHÔNG suy luận "than tăng → đốt than nhiều hơn" (sai chiều kinh tế — giá than tăng một mình làm than kém cạnh tranh hơn, không phải được đốt nhiều hơn); chỉ kết luận than được đốt nhiều hơn khi tin tức xác nhận rõ nguyên nhân khác (gas gián đoạn, RES thấp, sự cố hạ tầng) khiến cầu than tăng kéo giá than tăng theo, không suy đoán từ riêng chiều giá than. BẮT BUỘC dựa vào khối "SPREAD THAN–KHÍ" đã tính sẵn: nếu than đã rẻ hơn khí rõ rệt (chênh dương lớn, không có điểm hòa vốn) thì chuyển đổi nhiên liệu có thể đã chạm trần → tác động BIÊN của TTF tăng thêm lên cầu EUA yếu (chỉ qua chi phí/hedge) và còn có kênh ngược (công nghiệp cắt sản lượng → cầu EUA giảm); khi đó KHÔNG xếp "TTF tăng" là động lực tăng (ở BẤT KỲ đâu trong mục này, kể cả "Cần theo dõi"/kịch bản). Than dùng cho fuel switching EU là API2 (CIF ARA) — API4 (Richards Bay)/NEWC là chuẩn châu Á, KHÔNG dùng làm căn cứ dispatch EU. Ghi con số là "ước tính theo mô hình chi phí biên" (KHÔNG ghi "ước tính thô"/"chưa kiểm chứng"); vẫn chưa có vị thế quỹ/đấu giá EEX/MSR nên KHÔNG chốt hướng dứt khoát chỉ từ tương quan 1 tuần.
        (iii) ĐIỆN ĐỨC (DEBY1) → áp dụng ĐÚNG nhánh (a) của POWER_EUA_TWO_WAY: nếu DEBY1 biến động đồng pha với gas/than → kết luận điện tăng do chi phí nhiên liệu cao hơn, utility hedge thêm EUA tương ứng sản lượng đã bán — kênh hedge/chi phí này TÁCH BIỆT với việc thực tế đốt nhiên liệu nào, KHÔNG gộp chung "than+điện cùng tăng" thành kết luận "đốt than nhiều hơn". CHỈ được quy nguyên nhân "RES thấp" khi có dữ liệu/tin tức THỰC SỰ xác nhận RES thấp trong ngày — nếu KHÔNG có dữ liệu RES, PHẢI nêu rõ "không có dữ liệu RES trong ngày để xác nhận nhánh này" thay vì mặc định suy diễn.
        (iv) DẦU/GASOIL → áp dụng OIL_GASOIL: đối chiếu Δ ngày/Δ tuần của Brent/WTI theo đúng bước (i) trước khi kết luận fuel switching qua kênh dầu-khí (mâu thuẫn giữa 2 khung thời gian → tín hiệu ngắn hạn chưa đủ mạnh, cần thêm phiên xác nhận, chưa kết luận). Gasoil crack spread thu hẹp/mở rộng → nêu tác động lên cầu diesel/công nghiệp nặng và cầu EUA từ kênh công nghiệp đó, tách riêng khỏi kết luận fuel switching qua gas.
        (v) ĐỊA CHÍNH TRỊ — có 2 kênh TÁCH BIỆT trong GEOPOLITICS_SUPPLY_CHAIN, phải xác định đúng kênh trước khi suy luận:
            - Xung đột/sanctions/OPEC+/gián đoạn năng lượng vật lý (Trung Đông/Nga/Iran/Venezuela, eo biển vận chuyển...) → áp dụng ĐÚNG nhánh (a), BẮT BUỘC đi qua đủ bước cung → giá gas/dầu trước khi vào kết luận EUA: nếu Δ tuần của gas/dầu CHƯA xác nhận xu hướng tăng do sự kiện đó (vd giá dầu tuần vẫn giảm), CHỈ được ghi nhận đây là "rủi ro cung/giá tiềm ẩn", TUYỆT ĐỐI KHÔNG kết luận thẳng sự kiện địa chính trị đã đẩy tăng cầu EUA khi thiếu xác nhận giá.
            - Thay đổi chính phủ/định hướng chính sách khí hậu/quyết định thương mại quốc tế (KHÔNG phải gián đoạn năng lượng vật lý) → áp dụng ĐÚNG nhánh (b), đi qua kênh kỳ vọng thị trường/hoạt động công nghiệp, KHÔNG bắt buộc đi qua bước giá gas/dầu như nhánh (a) — CHỈ nêu khi tin tức nêu rõ sự kiện và hướng tác động cụ thể, không suy diễn chung chung.
      GỘP GAS–THAN–ĐIỆN ĐỨC THÀNH 1 GẠCH DUY NHẤT: khi Gas (TTF), Than (Newcastle/API2) và Điện Đức (DEBY1) CÙNG có ghi chú ở "instrument_notes" Nhóm 1 và cơ chế fuel-switching/POWER_EUA_TWO_WAY thực sự áp dụng được (theo bước (ii)/(iii) ở trên) — KHÔNG viết 3 gạch đầu dòng riêng cho từng mã, mà GỘP thành ĐÚNG 1 gạch đầu dòng duy nhất "**Fuel switching (Gas–Than–Điện Đức):**", nêu Δ ngày/Δ tuần của cả 3 mã thật ngắn gọn (lấy từ "DỮ LIỆU GIÁ") rồi đi thẳng tới 1 kết luận chiều dispatch và tác động EUA — không diễn giải lại cơ chế giá tương đối. Mã nào không có tin/giá đáng chú ý thì bỏ qua khỏi gạch này; nếu chỉ 1-2 trong 3 mã có mặt thì viết riêng như các mã khác trong Nhóm 1, không ép gộp.
      QUY TẮC NHÓM: CHỈ phân tích nhóm nào ĐÃ có ghi chú ở "instrument_notes" (nhóm không có ghi chú thì cũng không có gì để phân tích ở đây — bỏ qua tương ứng). SAU KHI áp BỘ LỌC BẮT BUỘC ở trên, nếu 1 nhóm không còn yếu tố nào có tác động thực → BỎ QUA HOÀN TOÀN nhóm đó ở "Phân tích" (không viết dòng "Tên nhóm:" cho nhóm đó), kể cả khi nhóm đó có ghi chú ở "instrument_notes".
      THỨ TỰ CỐ ĐỊNH: PHẢI theo ĐÚNG thứ tự NHÓM 1 (Năng lượng & nhiên liệu hóa thạch) → NHÓM 2 (Hạn ngạch & tín chỉ carbon) → NHÓM 3 (Chính sách) — TUYỆT ĐỐI KHÔNG đảo thứ tự theo mức độ quan trọng hay ưu tiên nhóm nào lên trước.
      MỖI NHÓM PHẢI MỞ ĐẦU BẰNG KẾT LUẬN NGAY TRÊN DÒNG TIÊU ĐỀ (KHÔNG còn chốt kết luận ở cuối nhóm như trước): dòng ĐẦU TIÊN của 1 nhóm viết LIỀN 1 dòng theo cú pháp "Tên nhóm: <kết luận>" — vd "Năng lượng & nhiên liệu hóa thạch: Tạo áp lực tăng rõ rệt lên EUA." — <kết luận> là ĐÚNG 1 câu ngắn nêu rõ nhóm này đẩy EUA tăng/giảm/trung lập, viết tiếp thẳng vào vị ngữ ngay sau dấu ":" (TUYỆT ĐỐI KHÔNG dùng cụm dẫn "Nhóm này..."/tag "**Kết luận:**" — vị trí đầu dòng đã tự nói lên đây là kết luận), chỉ chốt chiều, KHÔNG lặp lại số liệu hay diễn giải lại cơ chế đã dùng để suy ra kết luận đó. "Trung lập" CHỈ dùng khi nhóm có ≥2 yếu tố THỰC SỰ có tác động (đã qua BỘ LỌC BẮT BUỘC) nhưng triệt tiêu lẫn nhau về chiều. Các gạch đầu dòng SỐ LIỆU/LUẬN CỨ chi tiết (theo đúng các quy tắc suy luận ở trên) viết NGAY SAU dòng tiêu đề-kết luận này, xuống dòng thật ("- ..."); kết luận của nhóm CHỈ xuất hiện ĐÚNG 1 LẦN DUY NHẤT, ngay trên dòng tiêu đề đầu nhóm — TUYỆT ĐỐI KHÔNG lặp lại kết luận đó thêm 1 lần nữa ở cuối nhóm.
      KẾT LUẬN CHUNG CHO CẢ MỤC "PHÂN TÍCH": sau khi trình bày xong các nhóm áp dụng được ở trên, LUÔN kết thúc "content" bằng 1 ĐOẠN RIÊNG cuối cùng, BẮT ĐẦU bằng tag in đậm "**Tổng hợp:**" và NGAY SAU tag là ĐÚNG 1 nhãn xu hướng giá EUA trong ngoặc vuông, chọn 1 trong 3: "[TÍCH CỰC]" (giá EUA tăng/áp lực tăng chiếm ưu thế), "[TRUNG LẬP]" (đi ngang, biến động không đáng kể, hoặc tín hiệu hỗn hợp), "[TIÊU CỰC]" (giá EUA giảm/áp lực giảm chiếm ưu thế) — vd "**Tổng hợp:** [TIÊU CỰC] EUA giảm ...". Nhãn này quyết định màu khung "Nhận định" ở đầu báo cáo (xanh/vàng/đỏ) nên PHẢI khớp đúng chiều của kết luận, viết đúng chính tả, không thêm ký tự khác trong ngoặc. Phần nội dung sau nhãn DÀI ĐÚNG 40–45 CHỮ (đếm theo số từ/tiếng cách nhau bởi khoảng trắng, KHÔNG tính nhãn) — đây là nhận định CÔ ĐỌNG, TỔNG HỢP NHẤT về giá EUA, đặt ở vị trí nổi bật nhất đầu báo cáo: đối chiếu ngắn Δ ngày/Δ tuần của chính EUA (từ "SỐ LIỆU THẬT VỀ EUA"/"XU HƯỚNG EUA 30 NGÀY" ở trên) — cùng chiều → xu hướng nhất quán (chỉ nói "độ tin cậy cao" nếu đồng thời KHÔNG ghi "thiếu xác nhận" ở bất kỳ chỗ nào và khối lượng/yếu tố khác xác nhận; nếu không → "độ tin cậy trung bình/thiếu xác nhận"); trái chiều → tín hiệu ngắn hạn, cần thận trọng (KHÔNG gọi là "nhiễu" khi chưa có căn cứ — chỉ nói chưa đủ xác nhận) — rồi TỔNG HỢP các kết luận đã nêu ở dòng tiêu đề của từng nhóm ở trên thành 1 kết luận DỨT KHOÁT duy nhất về hướng đi EUA, KHÔNG lặp lại chi tiết/số liệu đã nêu ở các nhóm. Áp dụng quy tắc: kết luận chiều giá chỉ khi ≥2 yếu tố/nhóm cùng hướng; nếu mâu thuẫn → nhãn "[TRUNG LẬP]", ghi "tín hiệu hỗn hợp" + nêu ngắn 2 chiều + điều kiện kích hoạt mỗi chiều (vẫn gói gọn trong 40–45 chữ). Đây là câu quan trọng nhất Mục 3 — PHẢI dứt khoát, không mơ hồ.
      ĐỊNH DẠNG: "content" là 1 chuỗi string. Dòng ĐẦU TIÊN của mỗi nhóm PHẢI là dòng tiêu đề-kết luận gộp, viết LIỀN trên CÙNG 1 dòng theo cú pháp "Tên nhóm: <kết luận>" (dùng đúng tên "Năng lượng & nhiên liệu hóa thạch:", "Hạn ngạch & tín chỉ carbon:", "Chính sách:" — TUYỆT ĐỐI KHÔNG tách tên nhóm thành 1 dòng trơ trọi rồi để kết luận ở dòng/vị trí khác). NẾU 1 nhóm có nhiều yếu tố/chuỗi tác động → tách mỗi yếu tố thành 1 gạch đầu dòng xuống dòng thật (\\n"- ...") NGAY SAU dòng tiêu đề-kết luận đó để dễ nhìn; TUYỆT ĐỐI KHÔNG để lại các dấu "-" trống thừa thãi giữa các ý; giữa các NHÓM luôn xuống dòng thật (\\n); dòng "**Tổng hợp:**" luôn là dòng CUỐI CÙNG của "content" — TUYỆT ĐỐI KHÔNG viết liền thành 1 đoạn văn dài.
      Nếu "instrument_notes" rỗng, HOẶC có ghi chú nhưng SAU KHI áp BỘ LỌC BẮT BUỘC không nhóm nào còn yếu tố có tác động thực: "content" = "Không có thông tin mới liên quan trực tiếp đến giá EUA." (bỏ qua dòng "**Tổng hợp:**" trong trường hợp này).
   2. heading="Quan điểm thị trường" (TÙY CHỌN) — CHỈ đưa object này vào mảng "analysis_blocks" khi TIN TỨC ở trên THỰC SỰ có nêu quan điểm/nhận định cụ thể từ nguồn xác định (nhà phân tích, tổ chức, báo cáo). Khi có, "content" PHẢI nêu đủ: (1) quan điểm consensus — đa số thị trường/nhà phân tích đang nghĩ gì; (2) quan điểm contrarian khác biệt ra sao (nếu có), kèm tên nguồn cụ thể; (3) luận điểm/bằng chứng cụ thể mà nguồn đưa ra để bảo vệ quan điểm trái chiều đó; (4) điều kiện/kịch bản nào sẽ khiến quan điểm contrarian này đúng thay vì consensus. Nếu chỉ có consensus view (không có contrarian nào trong tin tức), bỏ qua (2)-(4) và chỉ nêu (1). "Đủ ý" nghĩa là đủ các nội dung áp dụng được ở trên, KHÔNG phải đủ số câu — vẫn theo đúng QUY TẮC ĐỘ DÀI CHUNG (tối đa 1–2 câu/ý). NẾU KHÔNG CÓ tin nào nêu quan điểm thị trường cụ thể: KHÔNG thêm object heading="Quan điểm thị trường" vào mảng — bỏ qua hoàn toàn (không viết "Không có quan điểm thị trường cụ thể." nữa).
      KẾT LUẬN BẮT BUỘC CHO BLOCK NÀY (khi block này được đưa vào): sau khi trình bày xong (1)-(4) áp dụng được, LUÔN kết thúc "content" bằng 1 câu RIÊNG cuối cùng, BẮT ĐẦU bằng tag in đậm "**Kết luận:**", chốt quan điểm nào (consensus hay contrarian) ĐÁNG TIN HƠN dựa trên chính bằng chứng/luận điểm đã nêu ở (2)-(3) và đối chiếu với chiều tác động đã kết luận ở block "Phân tích" (dòng "**Tổng hợp:**") — nếu 2 bên khớp chiều → nêu rõ đây là yếu tố củng cố thêm cho kết luận đó; nếu lệch chiều → nêu rõ đây là rủi ro/lý do cần thận trọng với kết luận đó. Nếu chỉ có consensus view (không có contrarian), câu kết luận chỉ cần nêu mức độ đồng thuận đó có khớp hay không với "Tổng hợp" ở "Phân tích". Câu này KHÔNG lặp lại số liệu/luận điểm đã nêu ở (1)-(4), chỉ chốt kết luận. Đây là câu bắt buộc, không được bỏ qua khi block "Quan điểm thị trường" đã được đưa vào mảng.
   3. heading="Cần theo dõi" — KHÔNG PHẢI danh sách lịch sự kiện chung chung. Đây là các WATCHPOINT rút ra TRỰC TIẾP từ chính số liệu giá, "instrument_notes" và "Phân tích" phía trên — ưu tiên đúng những điểm ở "Phân tích" CHƯA đủ cơ sở kết luận dứt khoát (vd Δ ngày/Δ tuần mâu thuẫn cần thêm phiên xác nhận, sự kiện địa chính trị/chính sách giá CHƯA kịp phản ánh, nhánh cơ chế còn thiếu dữ liệu để xác nhận...). Mục đích: cho người đọc biết CHÍNH XÁC cần nhìn vào đâu ở phiên/tin tiếp theo và mỗi khả năng xảy ra sẽ kéo cung/cầu, giá EUA theo hướng nào.
      ĐỘ DÀI RIÊNG CHO HEADING NÀY: KHÔNG áp dụng giới hạn 1–2 câu — mỗi watchpoint cần đủ chỗ nêu 2 kịch bản trái chiều nên có thể dài hơn, nhưng vẫn phải súc tích, thẳng vào thông tin, không thêm câu đệm.
      MỖI WATCHPOINT PHẢI CÓ ĐỦ 3 PHẦN:
        ƯU TIÊN BẮT BUỘC: quyết định chính sách EU đang chờ (thượng đỉnh/Hội đồng/Nghị viện EU bàn về ETS, đề xuất nới/hoãn/áp trần ETS của nước thành viên hoặc doanh nghiệp có trong tin) PHẢI có 1 watchpoint riêng, nêu rõ "chưa có kết quả" nếu tin chưa xác nhận.
        (a) CẦN THEO DÕI GÌ — nêu đích danh mã giá/chỉ số/tin tức cụ thể đã xuất hiện ở trên (vd "Δ tuần của Brent/WTI ở phiên tới", "diễn biến đàm phán sanctions Iran/Venezuela", "dữ liệu RES/gió-mặt trời ngày mai", "kết quả đấu giá EUA kỳ tới"...) — kèm ngày giờ Việt Nam cụ thể CHỈ khi tin tức/lịch công bố có nêu rõ, KHÔNG tự bịa thời điểm.
        (b) NẾU XẢY RA THEO HƯỚNG 1 — bắt đầu bằng "Nếu [diễn biến cụ thể theo hướng 1]..." → nêu rõ tác động tới cung/cầu và chiều giá EUA tương ứng.
        (c) NẾU XẢY RA THEO HƯỚNG NGƯỢC LẠI (hoặc không xảy ra) — bắt đầu bằng "Nếu [diễn biến ngược lại/không xác nhận]..." → nêu rõ tác động khác hoặc EUA đi ngang/giữ tín hiệu hỗn hợp.
        PHÉP THỬ TRUNG LẬP (BẮT BUỘC): 2 nhánh (b)/(c) của MỖI watchpoint phải dẫn tới 2 kết cục KHÁC NHAU cho EUA — TUYỆT ĐỐI KHÔNG để cả 2 nhánh cùng ra "đi ngang/giảm" (hoặc cùng "tăng"); watchpoint không có nhánh nào xác nhận được chiều ngược lại thì không còn là phép kiểm tra. Toàn bộ danh sách phải có ít nhất 1 nhánh xác nhận chiều TĂNG và 1 nhánh xác nhận chiều GIẢM của EUA. Vd watchpoint spread than–khí: "Nếu than vẫn rẻ hơn khí rõ rệt → điện than chạy gần tối đa, giữ mức cầu EUA (đỡ giá)" vs "Nếu TTF về dưới mức NGƯỠNG THẬT ở khối SPREAD THAN–KHÍ (khí tranh được với than ở nhà máy hiệu quả nhất) → dispatch sang khí, phát thải giảm → áp lực giảm EUA" — ngưỡng PHẢI là mức TTF/API2 ở dòng "NGƯỠNG THẬT", KHÔNG phải "chênh thu hẹp ±X EUR/MWh"; nếu khối đó ghi ngưỡng ngoài tầm 1–2 tuần thì KHÔNG viết watchpoint fuel switching dạng này.
      NGUỒN (BẮT BUỘC): mỗi watchpoint dựa trên tin tức PHẢI kết thúc bằng số tin đã đánh số ở "TIN TỨC LIÊN QUAN", dạng "[N]" (vd "... → áp lực giảm EUA. [3]"); watchpoint chỉ dựa trên dữ liệu giá/spread đã tính sẵn thì kết thúc bằng "[giá]". Watchpoint không có [N] hợp lệ hoặc [giá] sẽ bị hệ thống XOÁ — TUYỆT ĐỐI KHÔNG nêu sự kiện/phát ngôn (vd "CEO X kêu gọi tạm ngưng ETS") không có trong tin đã đánh số.
      MỖI watchpoint là 1 DÒNG RIÊNG, đánh số "1.", "2.", "3."... — PHẢI chèn ký tự xuống dòng thật (\\n) giữa các dòng, TUYỆT ĐỐI KHÔNG viết liền các watchpoint thành 1 đoạn văn dài không xuống dòng. Số lượng watchpoint bám theo đúng số điểm còn chưa chắc chắn thực sự tồn tại ở "Phân tích"/tin tức — KHÔNG bịa thêm watchpoint không có căn cứ chỉ để đủ số lượng.

C. "trading_scenarios": mảng ĐÚNG 3 kịch bản — BẮT BUỘC đủ cả 3 horizon "ngắn hạn", "trung hạn", "dài hạn". Đây là phần người đọc xem để RA QUYẾT ĐỊNH nên phải NGẮN, TRỰC DIỆN, đi thẳng vào giá trị: chỉ nêu điều kiện, mức giá và hành động — KHÔNG giải thích cơ chế, KHÔNG lặp lại mục B, KHÔNG câu đệm/mở đầu/sáo rỗng. Mỗi trường là 1 mệnh đề cô đọng, ưu tiên số liệu cụ thể hơn lời văn. Mỗi kịch bản gồm:
   - "horizon": "ngắn hạn" (1–2 tuần) / "trung hạn" (1–3 tháng) / "dài hạn" (3–12 tháng — ghi rõ mốc trong "condition", vd "tới hết Q2/2027")
     KHUNG THEO HORIZON (BẮT BUỘC): "trung hạn" dùng biên 30 phiên ĐÚNG như MỐC KỸ THUẬT (đáy–đỉnh 30 phiên, KHÔNG trộn mức cắt lỗ với kháng cự thành 1 biên lệch); "dài hạn" PHẢI dựa trên ≥2 động lực CẤU TRÚC (cap/hệ số giảm tuyến tính, MSR, lộ trình phân bổ miễn phí/CBAM, ETS2, rà soát ETS 2026 — từ "THEO DÕI CHÍNH SÁCH ETS/MSR" hoặc VĂN BẢN ĐÃ LUẬT HÓA), KHÔNG dùng mục tiêu đo biên độ 30 phiên (công cụ kỹ thuật ngắn hạn) và KHÔNG lấy giá đóng cửa hôm nay làm cận vùng; không suy ra được mức giá có cơ sở → nêu chiều + điều kiện, phần mức giá ghi "chưa đủ cơ sở định lượng".
   - "probability": xác suất kịch bản này xảy ra — CHỈ 1 trong 3 giá trị "Cao" / "Trung bình" / "Thấp", dựa trên driver ở mục B đã được dữ liệu/tin tức xác nhận rõ (probability cao hơn) hay mới chỉ là suy đoán/tin đồn (probability thấp hơn). CHỈ chọn "Cao" khi ≥2 yếu tố độc lập cùng chiều VÀ không có chỗ nào trong báo cáo ghi "thiếu xác nhận"/"tín hiệu hỗn hợp"/Δ ngày–Δ tuần trái chiều; luận điểm dựa trên tương quan ngắn hạn mà chưa định lượng được (không có spread, vị thế quỹ, đấu giá EEX, MSR) → tối đa "Trung bình". Kịch bản trung/dài hạn tối đa "Trung bình" trừ khi có sự kiện chính sách đã xác nhận.
   - "direction": ĐÚNG 1 trong 3 giá trị "tăng" / "giảm" / "đi ngang" — chiều giá EUA của RIÊNG kịch bản này, phải khớp ĐÚNG chiều với "trading_strategy" bên dưới (dùng để hiển thị mũi tên trên giao diện).
   - "condition" (1 câu, tối đa ~20 từ): "Nếu [X]..." — chỉ nêu trigger cụ thể (số liệu/ngưỡng/ngày) gắn với 1 driver ở mục B, không diễn giải thêm.
   - "price_zone" (tối đa ~15 từ, chỉ số liệu): vùng hỗ trợ/kháng cự EUA bằng EUR/tCO2, neo đúng 30-ngày-cao/thấp, giá đóng cửa, biên độ phiên đã cung cấp — TUYỆT ĐỐI KHÔNG bịa số.
   - "key_risk" (1 câu, tối đa ~20 từ): sự kiện/ngưỡng cụ thể làm kịch bản sai và đẩy giá lệch hướng nào — không viết chung chung.
   - "trading_strategy": đúng 3 dòng, mỗi dòng tối đa ~15 từ, bắt đầu bằng tag in đậm rồi xuống dòng thật (\\n) giữa các dòng:
     + "**Entry:**" mức giá/điều kiện vào lệnh, đúng chiều "direction", neo vào "price_zone".
     + "**Mục tiêu:**" mức giá chốt lời kế tiếp, risk/reward hợp lý so với Entry.
     + "**Quản trị rủi ro:**" mức giá cắt lỗ khi kịch bản bị vô hiệu, gắn với "key_risk".
     Chỉ dùng mức giá có căn cứ từ dữ liệu đã cho (giao diện đã có lưu ý tham khảo đi kèm) — không thêm giải thích sau mỗi dòng.
     LỆNH 2 CHIỀU (thường gặp ở "đi ngang": mua biên dưới + bán biên trên) → MỖI CHÂN PHẢI ĐỦ cả mục tiêu lẫn cắt lỗ, viết theo đúng mẫu:
       "**Entry:** Mua quanh A1–A2, bán quanh B1–B2" / "**Mục tiêu:** T_mua / T_bán" (2 số, chân mua trước) / "**Quản trị rủi ro:** Cắt lỗ dưới S_mua hoặc trên S_bán".
       TUYỆT ĐỐI KHÔNG để chân bán (hoặc chân mua) không có cắt lỗ/mục tiêu — 1 lệnh không có cắt lỗ là lỗi nghiêm trọng (hệ thống tự kiểm tra từng chân).
     "trading_strategy" là chiến lược cho BÀN GIAO DỊCH (vị thế đầu cơ độc lập) — KHÔNG phải cho sổ phòng hộ của SIM.
   KHÔNG có trường "hedge_note" trong từng kịch bản — hàm ý cho sổ phòng hộ gom về 1 trường DUY NHẤT "hedge_action" (mục D).
   QUY TẮC SỐ HỌC CHO MỨC GIÁ (BẮT BUỘC, kiểm tra lại phép tính trước khi viết):
     • Mọi mức giá (Entry/Mục tiêu/cắt lỗ/price_zone) CHỈ được lấy từ "MỐC KỸ THUẬT EUA" bên trên (đỉnh/đáy 30 phiên, mục tiêu đo biên độ, ATR) hoặc từ giá đóng cửa/biên độ phiên đã cho; KHÔNG tự đặt số tròn (vd 92,0 hay 95,0) không có phép tính nào dẫn tới. Kịch bản trung/dài hạn không có mốc từ dữ liệu → nêu mức theo % so với giá đóng cửa kèm phép tính ngắn (vd "+5% ≈ x.xx"), hoặc dùng vùng đo biên độ.
     • R:R (|Mục tiêu − Entry| / |Entry − cắt lỗ|) PHẢI ≥ 1:1,5; nếu mốc dữ liệu không cho R:R đạt mức này → đổi hướng "đi ngang"/chờ xác nhận, KHÔNG ép số.
     • Cắt lỗ đặt NGOÀI vùng hỗ trợ/kháng cự ít nhất 0,5 × ATR VÀ cách giá vào lệnh ít nhất 0,5 × ATR (vd ATR 1,8 → mua 82,5 thì cắt lỗ ≤ 81,6, KHÔNG đặt 82,35) — cắt lỗ sát Entry/sát mốc vài cent là nằm trong nhiễu phiên. Hỗ trợ chỉ là đáy 1 phiên thì coi là yếu.
     • Chọn mốc theo khung: "ngắn hạn" dùng "Mốc gần ... 10 phiên" (không dùng đỉnh/đáy 30 phiên làm Entry/cắt lỗ ngắn hạn vì quá xa); "trung hạn"/"dài hạn" dùng mốc 30 phiên/đo biên độ.
     • Mục tiêu trùng đúng đỉnh/đáy 30 phiên (kháng cự mạnh) → chốt 1 phần/thận trọng, ghi rõ là vùng cản, không coi là đích chắc chắn.
     • Nhất quán giá vào lệnh: nếu Entry khác giá đóng cửa hiện tại thì phải ghi điều kiện kích hoạt (vd "mua khi đóng cửa trên X"), và R:R PHẢI tính từ đúng giá vào lệnh đó (X), không dùng giá hiện tại. Entry không được "ngược" với điều kiện (vd mua khi giá đang dưới Entry mà không ghi chờ vượt).
     • Mức vô hiệu của kịch bản (trong "key_risk"/Phân tích) PHẢI trùng đúng mức cắt lỗ ở "Quản trị rủi ro" — không để 2 con số khác nhau (vd 87,01 và 87,15).
     • "price_zone" hỗ trợ/kháng cự KHÔNG được chứa giá đóng cửa hiện tại (vùng hỗ trợ phải nằm dưới giá, kháng cự nằm trên).
     • ĐIỀU KIỆN ↔ ENTRY: "condition" dạng "đóng cửa trên X" (lệnh mua) thì Entry là "mua khi đóng cửa trên X" (giá vào = X), KHÔNG được "mua quanh" vùng thấp hơn X; tương tự "dưới X" cho lệnh bán. R:R tính từ đúng X.
     • Kịch bản trung/dài hạn: mục tiêu PHẢI có phép suy ra từ dữ liệu (vd "+8% so với đóng cửa 87,14", "đỉnh 30 phiên"); không suy ra được → không đặt mức giá cụ thể. Luận điểm chính sách dài hạn (vd cắt phân bổ miễn phí) chỉ dùng khi có trong tin đã cung cấp (kèm nguồn) HOẶC thuộc "VĂN BẢN ĐÃ LUẬT HÓA" dưới đây (kèm trích dẫn văn bản ngay trong câu); không có cả hai → BỎ luận điểm/điều kiện đó.
       VĂN BẢN ĐÃ LUẬT HÓA:
       {LEGISLATED_POLICY_FACTS}
     • Mỗi "Mục tiêu" ghi kèm cơ sở trong ngoặc ngắn (vd "(kháng cự 30 phiên)", "(đo biên độ)", "(+4% so với đóng cửa)"); mục tiêu không nêu được cơ sở → bỏ.
     • Không dùng cụm "không phải khuyến nghị giao dịch" trong nội dung — giao diện đã có lưu ý riêng.
     • Mọi nhãn phần trăm "X (±p% so với đóng cửa Y)" PHẢI tính lại: p = (X / Y − 1) × 100 (vd 78,30 so với 85,84 là −8,78%, KHÔNG phải −5%; −5% từ 85,84 là 81,55) — hệ thống tự tính lại và sửa nhãn sai.
     • Mục tiêu tính theo % (dạng "X (Y × 0,95)" hoặc "−5% ≈ X") CHỈ được lấy cơ sở Y = giá đóng cửa hiện tại hoặc 1 mốc có trong "MỐC KỸ THUẬT EUA"; MỖI kịch bản CHỈ có 1 mục tiêu — "price_zone" và "**Mục tiêu:**" phải ghi CÙNG con số (bản 17/10 lỗi: Vùng giá 81,14 = 85,41 × 0,95 nhưng Chiến lược 77,95 = 82,05 × 0,95, mốc 82,05 không có trong dữ liệu).
     • Nhãn % của mục tiêu tính từ ĐÚNG giá vào lệnh: Entry là mức kích hoạt (vd "bán khi đóng cửa dưới 82,35") thì ghi "(−4,6% từ Entry 82,35)", KHÔNG ghi "−8% so với đóng cửa 85,41" (phóng đại kỳ vọng thực).
     • Ngắn hạn và trung hạn KHÔNG được chồng vùng hành động: nếu ngắn hạn giữ lệnh một chiều với cắt lỗ S, mức kích hoạt của kịch bản trung hạn ngược chiều phải nằm NGOÀI S (vd ngắn hạn mua cắt lỗ 81,50 → trung hạn bán chỉ khi đóng cửa dưới 81,50), và ghi rõ "trung hạn chỉ kích hoạt sau khi lệnh ngắn hạn đã cắt lỗ".
     • "đi ngang" thì "trading_strategy" PHẢI là lệnh 2 chiều trong biên (mua biên dưới + bán biên trên); kịch bản PHÁ BIÊN (bán khi thủng / mua khi vượt) KHÔNG được để dưới nhãn "đi ngang" — ghi nó ở "key_risk" như kịch bản thay thế, hoặc đặt "direction" theo chiều phá với xác suất "Thấp". Lệnh phá biên: mục tiêu = phép đo biên độ đã cho trong MỐC KỸ THUẬT, cắt lỗ cách mức phá 0,5–1,5 × ATR (vd ATR 1,69 → ~0,85–2,5).
     • "direction" khác "đi ngang" (nhất là trung/dài hạn) PHẢI dựa trên ≥1 driver ĐÃ xảy ra/đang diễn ra cùng chiều trong dữ liệu/tin hôm nay. Nếu mọi điều kiện kích hoạt của chiều đó CHƯA xảy ra, hoặc số liệu hiện tại đang đi NGƯỢC điều kiện (vd điều kiện "chênh khí − than thu hẹp" trong khi chênh đang mở rộng; chính sách được đánh giá trung lập) → "direction" = "đi ngang", ghi điều kiện đó ở "condition" như ngưỡng chờ kích hoạt.
     • Lệnh trong "trading_strategy" là LỆNH CHỜ có hiệu lực {forecast_review.ORDER_VALID_SESSIONS} phiên kể từ ngày báo cáo và tự huỷ nếu giá chạm mục tiêu trước khi khớp (hệ thống chấm theo quy tắc này) → đặt vùng Entry trong tầm với của {forecast_review.ORDER_VALID_SESSIONS} phiên (≈ 1–1,5 × ATR quanh giá hiện tại), không đặt quá xa. Với "đi ngang", ghi rõ "đặt lệnh chờ (limit) mua quanh … / bán quanh …".
     • Mức vô hiệu/rủi ro của MỖI kịch bản (key_risk) PHẢI trùng mức cắt lỗ trong "Quản trị rủi ro" của CHÍNH kịch bản đó; "price_zone" và vùng nêu ở "Entry" phải là CÙNG một vùng (không để 82,35–85,84 ở chỗ này và 82,35–85,00 ở chỗ khác).

D. "hedge_action": phần CHIẾN THUẬT của sổ phòng hộ SIM. NHÃN phòng hộ đã do hệ thống quyết định ở "TÍN HIỆU PHÒNG VỆ CBAM" (1 nhãn duy nhất, hiển thị cả ở thẻ CBAM) — KHÔNG tự đặt nhãn khác. Rủi ro là GIÁ của phần khối lượng quý chưa chốt (chi phí CBAM = VWAP các phiên còn lại); phần LÕI do hệ thống dựng sẵn = MUA futures EUA Dec ngay rồi BÁN rải theo lịch đấu giá còn lại (khóa chi phí ở giá mua hôm nay). TUYỆT ĐỐI KHÔNG viết "mua EUA rải/theo tỷ trọng lịch đấu giá" như 1 cách phòng hộ (không khóa được gì) và KHÔNG viết "chỉ gom/chỉ mua ở vùng X–Y thấp hơn giá hiện tại" cho sổ phòng hộ (thiếu phòng hộ nếu giá không về). Nêu % phải ghi rõ "% khối lượng quý" hay "% phần chưa chốt". Phơi nhiễm của SIM là GIÁN TIẾP (khách hàng xuất khẩu nhôm/thép sang EU chịu chi phí CBAM qua giá/chiết khấu với nhà nhập khẩu EU) — long EUA là phòng hộ kinh tế cho phơi nhiễm này, KHÔNG phải SIM mua chứng chỉ CBAM; TUYỆT ĐỐI KHÔNG khuyến nghị bán khống EUA cho sổ phòng hộ. Object gồm:
   - "action": chép ĐÚNG mã nhãn hệ thống: "increase" | "hold" | "reduce" (hệ thống sẽ ghi đè nếu khác).
   - "text" (1–2 câu, tối đa ~45 từ): CHỈ cho phần chiến thuật ≤20% phần chưa chốt — vùng/mức giá lấy ĐÚNG số trong kịch bản, mở đầu bằng "Phần chiến thuật (≤20% phần chưa chốt, quan điểm giao dịch):"; "increase" = mua thêm futures cho phần này ngay; "reduce" = chưa khóa phần này, nêu mức giá chờ; "hold" thì ghi không dùng phần chiến thuật hôm nay và lý do. Lý do phải nhất quán với nhận định kỹ thuật ở "Phân tích" (vd không viết "chưa có xác nhận đảo chiều" khi khối BIÊN ĐỘ PHIÊN ghi phiên cuối có dấu hiệu cạn lực — khi đó ghi "lực bán chững lại, chờ phiên xác nhận").

E. "open_positions": đối chiếu BẮT BUỘC từng lệnh trong "LỆNH ĐANG MỞ" bên dưới (rỗng [] nếu không có lệnh nào). Mỗi phần tử:
   - "report_date": ngày báo cáo đã ra lệnh, ĐÚNG dạng YYYY-MM-DD như trong danh sách;
   - "decision": "keep" (giữ cắt lỗ/mục tiêu cũ), "move" (chuyển sang mức mới — nêu mức trong "text"), "close" (đóng lệnh ở phiên tới);
   - "text" (1 câu, tối đa ~30 từ): mức cắt lỗ/mục tiêu áp dụng từ hôm nay và lý do, nhất quán với kịch bản ngắn hạn hôm nay (vd trung hạn nghiêng giảm mà vẫn giữ lệnh mua thì phải nói rõ vì sao).

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"3": {{"title": "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA", "instrument_notes": {{"EUA": "..."}}, "vs_previous": "...", "analysis_blocks": [{{"heading": "...", "content": "..."}}], "trading_scenarios": [{{"horizon": "...", "probability": "Cao/Trung bình/Thấp", "direction": "tăng/giảm/đi ngang", "condition": "...", "price_zone": "...", "key_risk": "...", "trading_strategy": "..."}}], "hedge_action": {{"action": "increase|hold|reduce", "text": "Phần chiến thuật (≤20%, quan điểm giao dịch): ..."}}, "open_positions": [{{"report_date": "YYYY-MM-DD", "decision": "keep|move|close", "text": "..."}}]}}}}"""
    return system, user


def _prompt_section3_news_only(
    news_text: str, target_date: str, topics_present: List[str],
    overrides: Optional[Dict[str, str]] = None,
) -> tuple[str, str]:
    """Mục 3 cho báo cáo không có phiên giá (is_no_price_report): CHỈ "analysis_blocks"
    dựa trên tin tức — không "instrument_notes" (cột ghi chú bảng giá) và không
    "trading_scenarios" (vùng giá/Entry/Mục tiêu/cắt lỗ đều cần giá). Giữ đúng định dạng
    dòng "Tên nhóm: <kết luận>" và "**Tổng hợp:** [NHÃN]" để frontend/Jack QC đọc được."""
    framework = _eua_framework(topics_present, full=True, overrides=overrides, news_only=True)
    system = f"Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.\n{CONCISENESS_RULE}\n\n{framework}"
    user = f"""Ngày báo cáo: {target_date}
{_NO_PRICE_PROMPT_RULE}

TIN TỨC LIÊN QUAN (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, energy_renewable, energy_hydrogen, geopolitics, eu_policy, cbam, vcm, global_carbon_market, vietnam_carbon_policy):
{news_text}

YÊU CẦU: Viết MỤC 3 — PHÂN TÍCH TÁC ĐỘNG CỦA TIN TỨC TỚI CUNG/CẦU EUA. Viết SÚC TÍCH, TRỰC DIỆN, không câu đệm. CHỈ gồm "analysis_blocks" (mảng "heading" + "content"); KHÔNG có "instrument_notes", KHÔNG có "trading_scenarios".

1. heading="Phân tích" (BẮT BUỘC): theo đúng thứ tự NHÓM 1 (Năng lượng & nhiên liệu hóa thạch) → NHÓM 2 (Hạn ngạch & tín chỉ carbon) → NHÓM 3 (Chính sách) của "B. DANH MỤC THEO DÕI", phân tích các SỰ KIỆN CỤ THỂ do tin tức nêu rõ sẽ tác động thế nào tới CUNG/CẦU EUA, áp dụng ĐÚNG chuỗi nhân quả ở phần A của KHUNG PHÂN TÍCH (không tự sinh chuỗi khác, không diễn giải lại từng bước cơ chế — chỉ nêu sự kiện ngắn rồi đi thẳng tới kết luận tác động).
   - CHỈ đưa yếu tố có tác động có căn cứ, có hướng rõ lên cung/cầu EUA; tin không dẫn tới tác động nào → BỎ. Nhóm không còn yếu tố nào → bỏ cả nhóm.
   - LOẠI TRỪ: {chains.get_block("NON_EUA_CARBON_MARKETS", overrides)}
   - Sự kiện năng lượng/địa chính trị cần giá gas/dầu xác nhận → chỉ ghi là "rủi ro/tác động tiềm năng, chờ phiên giao dịch tới xác nhận", KHÔNG kết luận đã tác động.
   - ĐỊNH DẠNG: dòng ĐẦU mỗi nhóm viết LIỀN "Tên nhóm: <kết luận 1 câu nhóm này hỗ trợ/gây áp lực/trung lập với EUA>" (đúng tên "Năng lượng & nhiên liệu hóa thạch:", "Hạn ngạch & tín chỉ carbon:", "Chính sách:"); các luận cứ là gạch đầu dòng "- ..." xuống dòng thật (\\n) ngay sau; bôi đậm (**...**) cụm chính đầu mỗi gạch.
   - Dòng CUỐI CÙNG của "content": "**Tổng hợp:** [NHÃN] ..." — NHÃN là 1 trong "[TÍCH CỰC]" (tin tức nghiêng về hỗ trợ EUA), "[TRUNG LẬP]" (tín hiệu hỗn hợp/không đáng kể), "[TIÊU CỰC]" (tin tức nghiêng về gây áp lực lên EUA); nội dung sau nhãn 40–45 chữ, tổng hợp kết luận các nhóm thành 1 nhận định dứt khoát về tác động của TIN TỨC trong ngày lên EUA — KHÔNG nêu bất kỳ con số giá nào, KHÔNG nêu Δ ngày/Δ tuần.
   - Không có tin nào tác động thực lên EUA: "content" = "Không có thông tin mới liên quan trực tiếp đến giá EUA." (bỏ dòng "**Tổng hợp:**").
2. heading="Quan điểm thị trường" (TÙY CHỌN) — CHỈ khi tin tức THỰC SỰ nêu quan điểm/nhận định từ nguồn xác định: nêu consensus; contrarian (nếu có, kèm tên nguồn, luận điểm, điều kiện đúng); kết thúc bằng câu "**Kết luận:** ..." đối chiếu với "**Tổng hợp:**". Không có → KHÔNG thêm block này. Dự báo giá của tổ chức được nêu ở dạng định tính (không chép con số giá).
3. heading="Cần theo dõi" (BẮT BUỘC) — các WATCHPOINT rút ra TRỰC TIẾP từ tin/phân tích ở trên, ưu tiên điều còn chưa chắc chắn (sự kiện chính sách đang chờ quyết định, rủi ro nguồn cung chờ giá phiên tới xác nhận...). Mỗi watchpoint 1 dòng đánh số "1.", "2."... (xuống dòng thật \\n giữa các dòng), gồm: (a) cần theo dõi gì (kèm ngày giờ Việt Nam CHỈ khi tin nêu rõ); (b) "Nếu [diễn biến hướng 1]..." → tác động cung/cầu EUA; (c) "Nếu [ngược lại/không xảy ra]..." → tác động khác. 2 nhánh (b)/(c) PHẢI dẫn tới 2 kết cục KHÁC NHAU cho EUA (không để cả 2 cùng "đi ngang/giảm"); cả danh sách có ít nhất 1 nhánh xác nhận chiều tăng và 1 nhánh xác nhận chiều giảm. KHÔNG nêu mức giá/ngưỡng giá. Không bịa watchpoint cho đủ số. Mỗi watchpoint PHẢI kết thúc bằng số tin đã đánh số dạng "[N]" — watchpoint không có [N] hợp lệ sẽ bị hệ thống xoá; KHÔNG nêu sự kiện/phát ngôn không có trong tin đã đánh số.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"3": {{"title": "Phân tích tác động của tin tức tới EUA (không có phiên giao dịch)", "analysis_blocks": [{{"heading": "Phân tích", "content": "..."}}, {{"heading": "Cần theo dõi", "content": "..."}}]}}}}"""
    return system, user


def _prompt_section4(news_text: str, target_date: str, cbam_estimate_text: str = "") -> tuple[str, str]:
    system = f"Bạn là chuyên gia phân tích thị trường carbon tự nguyện và CBAM.\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}

{cbam_estimate_text}
Bullet "Diễn biến CBAM" nhắc tới giá CBAM/chi phí CBAM của quý → dùng ĐÚNG số ước tính ở trên (ghi "ước tính"), KHÔNG lấy giá đóng cửa EUA làm giá CBAM. Không có tin CBAM thì KHÔNG viết bullet chỉ để nhắc lại ước tính (khối ước tính đã hiển thị riêng).

TIN TỨC LIÊN QUAN đã đánh số [N] (cbam, vcm, global_carbon_market, vietnam_carbon_policy) — CHỈ trích dẫn số có thật:
{news_text}

YÊU CẦU: Viết MỤC 4 — CẬP NHẬT TÍN CHỈ CARBON & CBAM.
Mục này theo dõi 3 cấu phần:
  (i)   VCM quốc tế: thông báo từ tổ chức xác minh (Verra, Gold Standard, ACR, CAR, Article 6...).
  (ii)  Dự án carbon gắn thép xanh / kim loại xanh.
  (iii) Diễn biến CBAM: EU CBAM, UK CBAM, lộ trình của các nước.

QUY TẮC BẮT BUỘC:
- Cấu phần nào CÓ tin trong TIN TỨC ở trên → viết 1 bullet riêng cho cấu phần đó (tối đa 2 câu NGẮN GỌN, đi thẳng vào thông tin chính — không diễn giải dài dòng), phần "text" BẮT ĐẦU bằng ĐÚNG TÊN ĐẦY ĐỦ in đậm markdown "**Tên cấu phần:**" (dùng đúng nguyên văn "VCM quốc tế", "Dự án carbon gắn thép xanh / kim loại xanh", "Diễn biến CBAM" — TUYỆT ĐỐI KHÔNG dùng ký hiệu La Mã "[i]"/"[ii]"/"[iii]").
- Cấu phần nào KHÔNG có tin → BỎ QUA, không viết dòng riêng "Không có diễn biến trọng yếu" cho từng cấu phần nữa.
- Nếu CẢ 3 cấu phần đều không có tin: chỉ viết ĐÚNG 1 bullet gộp chung duy nhất: {{"text": "**VCM quốc tế/Dự án carbon thép xanh/CBAM:** Không có diễn biến mới.", "source_index": null}} — KHÔNG liệt kê lặp lại từng cấu phần.
- Mỗi bullet là object {{"text": "...", "source_index": N | null}}: "source_index" là số [N] có thật của bài tin tức LÀM CĂN CỨ CHÍNH cho cấu phần đó ở trên — BẮT BUỘC kèm khi bullet dựa trên 1 tin cụ thể (chỉ chọn 1 số, chọn bài quan trọng/liên quan nhất nếu cấu phần có nhiều tin), null CHỈ khi không có tin nào làm căn cứ — TUYỆT ĐỐI KHÔNG bịa số không có thật.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"4": {{"title": "Cập nhật tín chỉ carbon & CBAM", "bullets": [{{"text": "**VCM quốc tế:** ...", "source_index": null}}, {{"text": "**Dự án carbon gắn thép xanh / kim loại xanh:** ...", "source_index": null}}, {{"text": "**Diễn biến CBAM:** ...", "source_index": null}}]}}}}"""
    return system, user


def _prompt_section8(
    news_text: str, prev_events_text: str, target_date: str, recurring_events_text: str,
) -> tuple[str, str]:
    window_start = target_date
    window_end = (datetime.strptime(target_date, "%Y-%m-%d").date() + timedelta(days=CALENDAR_WINDOW_DAYS)).isoformat()
    system = f"Bạn là chuyên gia lịch trình thị trường năng lượng & carbon châu Âu.\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date} (Giờ Việt Nam, UTC+7)
CỬA SỔ HIỂN THỊ: CHỈ liệt kê sự kiện có ngày diễn ra ("date") từ {window_start} đến {window_end} (đúng 7 ngày kể từ ngày báo cáo) — TUYỆT ĐỐI KHÔNG liệt kê sự kiện có "date" trước {window_start}, TRỪ đúng các sự kiện thuộc nhóm "Đã diễn ra, CẦN cập nhật kết quả" bên dưới.

SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN (tính bằng lịch thật + quy đổi múi giờ chính xác, KHÔNG phải LLM tự đoán — BẮT BUỘC đưa NGUYÊN VĂN từng sự kiện này vào "events", giữ đúng "date"/"datetime_vn"/"event"/"impact", TUYỆT ĐỐI KHÔNG tự đổi ngày/giờ hay bịa thêm sự kiện định kỳ khác ngoài danh sách này):
{recurring_events_text}

SỰ KIỆN TỪ BÁO CÁO TRƯỚC:
{prev_events_text}

TIN TỨC LIÊN QUAN:
{news_text}

YÊU CẦU: Viết MỤC 8 — LỊCH SỰ KIỆN 7 NGÀY TỚI ({window_start} → {window_end}).
"events" CHỈ được gồm sự kiện thuộc ĐÚNG 5 NHÓM sau — KHÔNG thêm bất kỳ sự kiện nào ngoài 5 nhóm này dù tin tức có nhắc tới:
  1. Tồn kho dầu EIA — lấy NGUYÊN từ "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên (đã có kết quả thực tế nếu sự kiện đã xảy ra), không tự tính lại, không tự đoán kết quả nếu outcome đã được điền sẵn.
  2. Rig count Baker Hughes — lấy NGUYÊN từ "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên (đã có kết quả thực tế nếu sự kiện đã xảy ra), không tự tính lại.
  3. Họp chính sách (ECB / FOMC / Hội nghị thượng đỉnh EU – Hội đồng châu Âu / Hội đồng Môi trường hoặc Năng lượng EU / bỏ phiếu của Nghị viện EU / EU Climate Action / quyết định về EU ETS, MSR, CBAM) — CHỈ thêm nếu TIN TỨC LIÊN QUAN ở trên xác nhận rõ ngày họp cụ thể; KHÔNG có xác nhận thì KHÔNG thêm (không đoán ngày). Cuộc họp đang/vừa diễn ra trong cửa sổ mà tin tức có nhắc (vd thượng đỉnh EU bàn về ETS, sức ép của các nước đòi nới ETS) → BẮT BUỘC đưa vào, kèm "outcome": "Chưa có kết quả được xác nhận" nếu tin chưa nêu kết quả — KHÔNG bỏ sót vì chưa có kết quả.
  4. Đấu giá EUA sơ cấp EEX — lấy NGUYÊN từ "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" (tính từ lịch đấu giá chính thức của EEX), KHÔNG tự thêm phiên đấu giá khác.
  5. Báo cáo vị thế quỹ EUA (Commitments of Traders ICE Endex) và họp chính sách EU từ lịch admin xác nhận — lấy NGUYÊN từ "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" (họp chính sách ở đây đã được xác nhận ngày, không cần tin tức nhắc lại).
LƯU Ý: API Crude Inventory đã bị loại bỏ khỏi danh sách theo dõi định kỳ do không có nguồn dữ liệu công khai lấy được tự động. KHÔNG thêm sự kiện này vào "events" dù báo cáo trước có liệt kê.
Với sự kiện bạn TỰ thêm ở nhóm 3: "date" (YYYY-MM-DD), "datetime_vn" (CHỈ "DD/MM", KHÔNG kèm năm, TUYỆT ĐỐI KHÔNG kèm giờ dù tin tức có nêu rõ giờ), "event", "impact" (Cao/Trung/Thấp).

CẬP NHẬT KẾT QUẢ SỰ KIỆN KỲ TRƯỚC (bắt buộc, chỉ áp dụng cho danh sách "SỰ KIỆN TỪ BÁO CÁO TRƯỚC" ở trên):
- Nhóm "Đã diễn ra hoặc diễn ra đúng hôm nay, CẦN cập nhật kết quả": với MỖI sự kiện EIA/Baker Hughes — nếu "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên đã kèm "outcome" thực tế (được tính sẵn bằng dữ liệu fetch trực tiếp), PHẢI dùng đúng outcome đó, KHÔNG được tự viết outcome khác; với sự kiện khác (nhóm 3) — CHỈ điền outcome nếu TIN TỨC LIÊN QUAN xác nhận rõ; nếu không xác nhận, ghi "Chưa có thông tin kết quả xác nhận" — TUYỆT ĐỐI KHÔNG tự bịa số liệu. Giữ nguyên "date" gốc.
- Nhóm "Vẫn còn sắp tới trong cửa sổ 7 ngày": liệt kê lại bình thường, giữ nguyên "date", KHÔNG cần field "outcome" — tránh liệt kê trùng nếu sự kiện này đã nằm trong "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên.
- Sự kiện mới của kỳ 7 ngày tới tính từ {target_date} → liệt kê bình thường, KHÔNG cần field "outcome".
- KHÔNG đưa vào "events" bất kỳ sự kiện nào có "date" nằm ngoài khoảng {window_start} → {window_end}, TRỪ các sự kiện thuộc nhóm "Đã diễn ra, CẦN cập nhật kết quả".

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"8": {{"title": "Lịch sự kiện 7 ngày tới", "events": [{{"date": "YYYY-MM-DD", "datetime_vn": "DD/MM", "event": "...", "impact": "Cao/Trung/Thấp", "outcome": "... (optional, chỉ khi sự kiện đã qua)"}}]}}}}"""
    return system, user




# Quy tắc CBAM dùng chung cho prompt gợi ý kinh doanh (và Jenny chat nếu cần) — tránh gửi khách con số
# sai bản chất: giai đoạn chuyển tiếp 2026–2034 phân bổ miễn phí CHƯA bằng 0, và thép/nhôm chỉ tính phát thải
# trực tiếp nên PPA/điện xanh không giảm chi phí CBAM của 2 nhóm hàng này.
CBAM_COST_RULE = (
    "Chi phí CBAM/tấn ≈ (phát thải nhúng tCO2/t − benchmark phân bổ miễn phí tCO2/t × hệ số phân bổ miễn phí còn lại) "
    "× giá CBAM (EUR/tCO2). Năm 2026 hệ số phân bổ miễn phí còn lại là 97,5% (CBAM chỉ thu trên 2,5% phần phân bổ "
    "miễn phí bị loại bỏ, cộng phần phát thải vượt benchmark) — TUYỆT ĐỐI KHÔNG giả định phân bổ miễn phí = 0 và "
    "KHÔNG lấy phát thải nhúng × giá CBAM làm chi phí. Ví dụ minh họa (thép lò cao, GIẢ ĐỊNH): phát thải 1,8 − "
    "benchmark ~1,3 × 97,5% ≈ 0,53 tCO2/t → × giá CBAM. Benchmark 2026–2030 phải đối chiếu theo mã CN cụ thể — "
    "luôn ghi \"benchmark giả định, cần đối chiếu theo mã CN\"; phát thải nhúng không có trong dữ liệu → gắn nhãn "
    "\"giả định\" hoặc để khách tự điền, KHÔNG trình bày như số thật."
)
CBAM_SCOPE_RULE = (
    "Phạm vi phát thải: với SẮT THÉP và NHÔM, CBAM hiện CHỈ tính phát thải TRỰC TIẾP (Scope 1 của quá trình sản xuất); "
    "phát thải gián tiếp từ điện CHỈ áp dụng cho xi măng và phân bón. Vì vậy PPA/điện tái tạo/REC KHÔNG làm giảm "
    "chi phí CBAM/tấn của thép, nhôm — chỉ giảm Scope 2, hỗ trợ hồ sơ ESG/yêu cầu của khách EU. TUYỆT ĐỐI KHÔNG viết "
    "PPA \"hạ chi phí CBAM\" cho thép/nhôm; muốn giảm chi phí CBAM thép/nhôm phải là giảm phát thải trực tiếp "
    "(lò điện EAF/thép phế, hydro, anode trơ...) hoặc nộp dữ liệu phát thải thực đã thẩm định thay giá trị mặc định."
)

CBAM_DECLARANT_RULE = (
    "Bên MUA và nộp chứng chỉ CBAM là \"declarant CBAM được cấp phép\" tại EU (nhà nhập khẩu hoặc đại diện hải quan gián "
    "tiếp) — doanh nghiệp xuất khẩu Việt Nam (khách hàng của SIM) KHÔNG mua chứng chỉ; họ chịu chi phí CBAM GIÁN TIẾP qua "
    "giá bán/chiết khấu đàm phán với nhà nhập khẩu, hoặc qua việc cung cấp dữ liệu phát thải thực đã thẩm định để giảm "
    "mức bị tính. Vì vậy với khách Việt Nam: khuyến nghị về đàm phán giá/điều khoản chia sẻ chi phí CBAM, dữ liệu phát "
    "thải — TUYỆT ĐỐI KHÔNG khuyên \"giãn/đẩy nhanh tiến độ mua chứng chỉ\". Khuyến nghị về THỜI ĐIỂM mua chứng chỉ chỉ "
    "được viết khi ghi rõ đối tượng là nhà nhập khẩu EU VÀ cơ chế định giá/thời điểm bán chứng chỉ có trong tin đã cung "
    "cấp (quy định sửa đổi CBAM 2025 thay đổi lịch bán chứng chỉ năm 2026 — không tự suy diễn)."
)


# Hồ sơ ngắn của SIM cho prompt gợi ý kinh doanh — gợi ý phải bám ngành lõi, không lan sang lĩnh vực xa.
SIM_PROFILE = (
    "SIM (Stavian Industrial Metal) là doanh nghiệp Việt Nam kinh doanh KIM LOẠI (nhôm, thép, đồng, kẽm...) — "
    "có khách hàng nhôm/thép xuất khẩu sang EU (chịu CBAM), quan tâm chi phí năng lượng/carbon trong chuỗi cung ứng kim loại."
)

# Lăng kính THỊ TRƯỜNG KIM LOẠI cho gợi ý kinh doanh — bản chấm 17/10 chỉ ra 4/6 gợi ý không chạm tới kinh doanh kim loại:
# "điều khoản giá năng lượng EU" (chi phí khí/điện EU không đổi đơn giá gia công của SIM tại Việt Nam), Hormuz như tuyến
# vận chuyển (tuyến Việt Nam–EU không qua Hormuz), SAF → truy xuất nguồn gốc (không liên quan kim loại), "dấu chân carbon
# Scope 1-2" (sai khái niệm — phát thải nhúng CBAM/ISO 14067 gồm cả tiền chất thượng nguồn).
METALS_LENS_RULE = (
    "LĂNG KÍNH BẮT BUỘC — đi từ tin tức tới THỊ TRƯỜNG KIM LOẠI rồi mới tới SIM, theo đúng các kênh sau (không suy diễn kênh khác):\n"
    "  (1) Năng lượng/carbon EU đắt → áp lực chi phí lên nhà luyện nhôm/kẽm/thép EU → rủi ro cắt sản lượng → premium kim loại "
    "tại EU (vd premium nhôm Rotterdam duty-paid) tăng → cơ hội XUẤT KHẨU sang EU, nhưng PHẢI trừ chi phí CBAM/tấn (thẻ CBAM) "
    "khi tính biên lợi nhuận. Chi phí khí/điện EU KHÔNG làm thay đổi chi phí sản xuất/gia công của SIM tại Việt Nam.\n"
    "  (2) Địa chính trị/tuyến hàng hải: chỉ nêu khi ảnh hưởng NGUỒN CUNG KIM LOẠI hoặc nguyên liệu (vd vùng Vịnh là nguồn nhôm "
    "nguyên sinh lớn xuất qua Hormuz — gián đoạn → thiếu cung, premium tăng); tuyến Việt Nam–EU KHÔNG đi qua Hormuz, KHÔNG "
    "viết gợi ý về cước/tuyến Việt Nam–EU qua Hormuz.\n"
    "  (3) CBAM: chi phí theo tấn sản phẩm = (phát thải nhúng − benchmark × hệ số phân bổ miễn phí còn lại) × giá CBAM; phát "
    "thải nhúng theo CBAM/ISO 14067 PHẢI tính cả TIỀN CHẤT thượng nguồn (với kim loại, khâu luyện thượng nguồn chiếm phần lớn) — "
    "KHÔNG gọi là \"dấu chân carbon Scope 1-2\" của riêng nhà máy.\n"
    "  (4) Tin không có đường truyền tới giá/cung/cầu/chi phí KIM LOẠI (vd SAF, dầu ăn đã qua sử dụng, bếp sạch, nông nghiệp) → "
    "KHÔNG dùng làm căn cứ gợi ý, kể cả qua suy diễn kiểu \"học cách truy xuất nguồn gốc\"."
)


BIZ_SHORT_EXTRA_KEYS = ("kpi", "owner")
BIZ_LONG_EXTRA_KEYS = ("pilot", "financials", "kpi", "owner")


def _prompt_biz_recommendation(
    news_text: str, prices_text: str, eua_trend: str, target_date: str,
    tracking_text: str = "Không có.", valid_codes: str = "", dismissed_text: str = "Không có.",
    news_only: bool = False,
) -> tuple[str, str]:
    """`news_only=True`: báo cáo không có phiên giá (is_no_price_report) — bỏ khối giá
    khỏi prompt, gợi ý chỉ dựa trên tin tức, "trigger" dạng tin tức/chính sách."""
    system = (
        f"Bạn là chuyên gia tư vấn kinh doanh về carbon và năng lượng cho doanh nghiệp Việt Nam (SIM).\n"
        f"{SIM_PROFILE}\n{CONCISENESS_RULE}"
    )
    if news_only:
        price_block = (
            f"{_NO_PRICE_PROMPT_RULE}\n"
            "Hôm nay mọi gợi ý chỉ dựa trên tin tức: \"trigger\" là điều kiện dạng tin tức/chính sách "
            "(KHÔNG dùng ngưỡng giá) và \"trigger_rule\" LUÔN là null.\n"
        )
    else:
        price_block = f"DỮ LIỆU GIÁ:\n{prices_text}\n\nXU HƯỚNG EUA 30 NGÀY:\n{eua_trend}\n"
    user = f"""Ngày báo cáo: {target_date}

{price_block}
TIN TỨC ĐA CHIỀU đã đánh số [N] — CHỈ trích dẫn số có thật:
{news_text}

GỢI Ý NGẮN HẠN JENNY ĐÃ ĐỀ XUẤT Ở CÁC BÁO CÁO TRƯỚC, VẪN ĐANG THEO DÕI (tình huống kích hoạt chưa xảy ra):
{tracking_text}

GỢI Ý (NGẮN HẠN VÀ DÀI HẠN) ĐÃ BỊ ADMIN GỠ BỎ (đánh giá là phi thực tế/không phù hợp) — TUYỆT ĐỐI KHÔNG đề xuất lại ý tương tự ở CẢ 2 bảng:
{dismissed_text}

YÊU CẦU: Viết MỤC GỢI Ý KINH DOANH & GIẢI PHÁP CHO SIM — trình bày dưới dạng BẢNG (mỗi gợi ý là 1 HÀNG với các CỘT tách bạch, KHÔNG viết gộp thành 1 câu văn dài).
Gồm 2 bảng:
A. "short_term": mảng object, gợi ý ngắn hạn MỚI gắn TRỰC TIẾP với tin quan trọng/cập nhật mới nhất trong ngày. TUYỆT ĐỐI KHÔNG đề xuất lại gợi ý trùng ý với danh sách "ĐANG THEO DÕI" ở trên (hệ thống tự nhắc lại các gợi ý đó) — chỉ thêm gợi ý có nội dung/hành động MỚI. Mỗi object gồm các trường sau, MỖI TRƯỜNG CHỮ TỐI ĐA 1 CÂU NGẮN GỌN (như 1 ô trong bảng, không viết thành đoạn văn):
   - "trigger": ĐIỀU KIỆN KÍCH HOẠT hướng tới TƯƠNG LAI, bắt đầu bằng "Nếu"/"Khi" — tình huống mà KHI XẢY RA thì SIM nên (hoặc lẽ ra đã nên) làm theo đề xuất, và có thể KIỂM CHỨNG được ở các ngày sau qua giá hoặc tin tức (vd "Nếu Brent vượt 100 USD/bbl", "Khi EU công bố cắt giảm phân bổ miễn phí cho ngành thép"). KHÔNG mô tả lại sự việc đã xảy ra hôm nay (bối cảnh hôm nay đưa vào "reason").
   - "trigger_rule": CHỈ khi "trigger" là 1 NGƯỠNG GIÁ đóng cửa của 1 mã trong bảng giá → object {{"code": "<mã>", "op": ">" | ">=" | "<" | "<=", "value": <số, cùng đơn vị với giá>}}; mã hợp lệ: {valid_codes or "(không có)"}. Điều kiện dạng tin tức/chính sách → null.
   - "action": hành động cụ thể SIM nên làm, khả thi và thực tế.
   - "reason": lý do vì sao hành động này hợp lý (bối cảnh/số liệu hôm nay + chuỗi nhân quả đã phân tích ở các mục trên).
   - "source_index": số [N] của bài tin tức làm căn cứ chính — BẮT BUỘC khi gợi ý dựa trên tin; null CHỈ khi điều kiện là ngưỡng giá có "trigger_rule". Gợi ý dựa trên tin mà không gắn được số [N] có thật → BỎ (hệ thống sẽ tự loại).
   - "kpi": chỉ tiêu đo được + mốc thời gian (1 câu ngắn).
   - "owner": đơn vị phụ trách.
B. "long_term": mảng object, gợi ý dài hạn rút ra từ cơ hội phân tích (chính sách CBAM, premium kim loại EU, nguồn cung kim loại, chuyển dịch năng lượng...). Mỗi object gồm các trường sau, MỖI TRƯỜNG TỐI ĐA 1 CÂU NGẮN GỌN:
   - "opportunity": cơ hội/xu hướng dài hạn cụ thể đã xác định được.
   - "solution": giải pháp/hướng đi đề xuất cho SIM để tận dụng cơ hội đó.
   - "expectation": kỳ vọng/kết quả nếu triển khai giải pháp này.
   - "pilot": bước thí điểm cụ thể — quy mô (khối lượng/số khách/số SKU), đối tượng, thời gian.
   - "financials": chi phí/doanh thu ước tính kèm phép tính ngắn từ dữ liệu có sẵn (vd chi phí CBAM/tấn ở thẻ CBAM × khối lượng thí điểm); thiếu số → ghi "cần xác minh: <số cần có>".
   - "kpi": chỉ tiêu đo được + mốc thời gian.
   - "owner": đơn vị phụ trách.
   - "source_index": BẮT BUỘC là số [N] có thật của bài làm căn cứ — gợi ý dài hạn không có bài làm căn cứ thì KHÔNG viết (hệ thống tự loại gợi ý thiếu nguồn vì người đọc không kiểm chứng được).
   - TRUNG THỰC VỚI NGUỒN: tên công ty/quỹ/dự án, số tiền, công suất, ngày tháng nêu trong gợi ý PHẢI có trong CHÍNH bài [N] được gắn — KHÔNG lấy chi tiết từ bài khác hay từ hiểu biết riêng.

CBAM: SIM có khách hàng nhôm/thép xuất khẩu sang EU nên nếu tin tức/dữ liệu có CBAM (giá CBAM, chính sách, nhôm/thép) thì BẮT BUỘC có ít nhất 1 gợi ý về CBAM. QUY TẮC CBAM BẮT BUỘC (gửi cho khách — sai là mất uy tín):
  (a) {CBAM_COST_RULE}
  (b) {CBAM_SCOPE_RULE}
  (b2) {CBAM_DECLARANT_RULE}
  (c) Giá CBAM 2026 chốt theo QUÝ (trung bình giá đấu giá EUA của quý, áp dụng cho chứng chỉ của quý đó) — điều kiện/trigger liên quan CHI PHÍ CBAM (bảng tính chi phí carbon, báo giá CBAM cho khách, mua chứng chỉ) PHẢI dùng "EUA lũy kế quý" so với giá CBAM hiện hành, KHÔNG dùng giá đóng cửa EUA 1 phiên; trigger_rule khi đó = {{"code": "EUA", "basis": "qtd_avg", "op": ..., "value": ...}} (xem "EUA lũy kế quý" trong DỮ LIỆU GIÁ — ghi đúng nhãn ở đó: "VWAP đấu giá EEX" hoặc "xấp xỉ"). Dự báo giá CBAM của quý: dùng ĐÚNG "ƯỚC TÍNH GIÁ CBAM" tính sẵn, KHÔNG tự ước tính.
QUY TẮC NHẤT QUÁN TRIGGER: nếu gợi ý mới có CÙNG hành động (hoặc cùng mục đích) với 1 gợi ý trong danh sách "ĐANG THEO DÕI" thì KHÔNG tạo lại; nếu buộc phải nêu lại thì GIỮ ĐÚNG chiều điều kiện cũ (không đổi "> X" thành "< Y" sau 1 ngày) — muốn đổi chiều phải nêu rõ lý do bằng dữ liệu trong "reason".
QUY TẮC NỘI DUNG: gợi ý dài hạn phải có 1 bước thí điểm cụ thể (quy mô/đối tượng/thời gian) hoặc chỉ tiêu tài chính gắn với dữ kiện trong tin tức — KHÔNG chỉ nêu khái niệm chung (BII, NBS, điện hóa nhiệt...). Số tiền/cam kết của 1 tổ chức phải khớp với bài được trích, không tự đổi số.
QUY TẮC SỐ LƯỢNG: chỉ đưa vào gợi ý THỰC SỰ có căn cứ từ tin tức/dữ liệu ở trên — TUYỆT ĐỐI KHÔNG bịa thêm cho đủ số dòng. Nếu 1 bảng không có gợi ý nào đủ căn cứ, để mảng đó rỗng. TỐI ĐA 3 gợi ý MỖI bảng (short_term và long_term riêng biệt) — nếu có nhiều hơn 3 gợi ý đủ căn cứ, CHỈ giữ lại ĐÚNG 3 gợi ý TRỌNG TÂM/có căn cứ mạnh nhất, bỏ phần còn lại (KHÔNG cố nhồi hết vào 1 bảng, sẽ làm output quá dài và bị cắt).
QUY TẮC NGÀNH LÕI: mọi gợi ý (nhất là "long_term") PHẢI gắn với hoạt động kinh doanh KIM LOẠI của SIM (nhôm, thép, đồng, kẽm — sản xuất/thương mại, khách hàng xuất khẩu EU, chi phí năng lượng/carbon của chuỗi cung ứng kim loại). Ý xa ngành lõi (vd bếp sạch, lúa phát thải thấp, MRV nông nghiệp) → KHÔNG đưa vào, kể cả khi tin hôm nay có.
{METALS_LENS_RULE}
CHUẨN "ĐƯA THÀNH DỰ ÁN THÍ ĐIỂM" (Phòng Chiến lược phát triển): mỗi gợi ý PHẢI lượng hóa được — "kpi" là chỉ tiêu đo được kèm con số/mốc (vd "premium chào bán ≥ X USD/t sau khi trừ CBAM", "chốt ≥ 1 hợp đồng thử với nhà nhập khẩu EU trong Q1/2027"); số liệu thị trường chưa có trong dữ liệu/tin → ghi rõ "cần xác minh" thay vì bịa; "owner" là đơn vị chịu trách nhiệm (vd "Phòng Kinh doanh xuất khẩu", "Phòng Chiến lược phát triển", "Phòng Tài chính – quản trị rủi ro"). Gợi ý không nêu được KPI đo được → BỎ.
QUY TẮC "trigger": phải CỤ THỂ, MỘT CHIỀU, kiểm chứng được — tin tức: nêu đúng sự kiện sẽ xảy ra (vd "Khi Nga và Ukraine công bố thoả thuận ngừng tấn công hạ tầng năng lượng có hiệu lực"), KHÔNG viết mơ hồ kiểu "xác nhận nội dung và phạm vi..."; ngưỡng giá: CHỈ dùng mã có trong bảng giá (mã không có — vd khí Henry Hub/NG — KHÔNG dùng làm ngưỡng) và BẮT BUỘC kèm "trigger_rule".
Lưu ý: KHÔNG dùng câu lệnh mua/bán tài chính trực tiếp.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"biz": {{"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": [{{"trigger": "Nếu ...", "trigger_rule": null, "action": "...", "reason": "...", "kpi": "...", "owner": "...", "source_index": null}}], "long_term": [{{"opportunity": "...", "solution": "...", "expectation": "...", "pilot": "...", "financials": "...", "kpi": "...", "owner": "...", "source_index": null}}]}}}}"""
    return system, user


def _scenario_math_issues(sec3: Dict[str, Any], chart_data: List[Dict], prices: List[Dict]) -> List[str]:
    """Lỗi số học của "trading_scenarios" (R:R, điều kiện ↔ Entry, cắt lỗ sát đáy/đỉnh phiên, sai
    phía) — dùng ĐÚNG bộ kiểm tra của Jack QC (services/report_qc.py) để bản sinh ra qua được QC."""
    from services.report_qc import (  # import muộn: report_qc import module này
        check_breakout_levels, check_fuel_thresholds, check_horizon_overlap, check_long_term_basis, check_scenario_basis,
        check_scenarios,
    )

    content = {"2": {"chart_data": chart_data, "prices": prices}, "3": sec3}
    issues = check_scenarios(content)
    close = next((p.get("close") for p in prices if str(p.get("code", "")).upper() == "EUA"), None)
    return [
        it["message"] for it in issues
        if it["severity"] in ("error", "warning") and ".trading_strategy" in it.get("field_path", "")
    ] + [it["message"] for it in derived_target_issues(sec3, close, _technical_level_values(chart_data))] + [
        it["message"] for it in check_scenario_basis({"3": sec3}, close) + check_horizon_overlap({"3": sec3})
        + check_breakout_levels(content) + check_fuel_thresholds(content, scenarios_only=True)
        + check_long_term_basis(content)
    ]


def _technical_level_values(chart_data: List[Dict]) -> List[float]:
    """Các mốc giá có trong "MỐC KỸ THUẬT EUA" — cơ sở hợp lệ cho mục tiêu dạng "(Y × f)"."""
    if not chart_data:
        return []
    hi, lo = max(c["high"] for c in chart_data), min(c["low"] for c in chart_data)
    near = chart_data[-10:]
    return [hi, lo, hi + (hi - lo), lo - (hi - lo), max(c["high"] for c in near), min(c["low"] for c in near)]


async def _revise_scenarios_if_invalid(
    sec3: Dict[str, Any], system_prompt: str, user_prompt: str, chart_data: List[Dict], prices: List[Dict],
) -> Dict[str, Any]:
    """Mục 3 vi phạm quy tắc số học của kịch bản → gọi LLM sửa ĐÚNG 1 lần, kèm danh sách lỗi.
    Chỉ nhận bản sửa nếu ít lỗi hơn (và parse được) — không thì giữ bản gốc, Jack QC sẽ báo."""
    problems = _scenario_math_issues(sec3, chart_data, prices)
    if not problems:
        return sec3
    logger.warning("[REPORT] Mục 3: kịch bản giao dịch vi phạm %d quy tắc, yêu cầu sửa: %s", len(problems), problems)
    feedback = (
        f"{user_prompt}\n\nBẢN TRƯỚC CỦA BẠN:\n{json.dumps({'3': sec3}, ensure_ascii=False)}\n\n"
        "BẢN TRÊN VI PHẠM QUY TẮC SỐ HỌC CỦA \"trading_scenarios\" — sửa đúng các lỗi sau (đổi Entry/Mục tiêu/"
        "cắt lỗ/điều kiện theo MỐC KỸ THUẬT, hoặc đổi direction thành \"đi ngang\" nếu không đạt được R:R ≥ 1,5), "
        "giữ nguyên các phần khác, trả về TOÀN BỘ JSON mục 3:\n- " + "\n- ".join(problems)
    )
    try:
        raw = await _call_llm(feedback, system=system_prompt, max_tokens=SECTION_MAX_TOKENS.get("3", 8192))
    except Exception:
        logger.exception("[REPORT] Mục 3: gọi LLM sửa kịch bản lỗi — giữ bản gốc.")
        return sec3
    parsed = _extract_json(raw) if raw else None
    revised = parsed.get("3") if isinstance(parsed, dict) and isinstance(parsed.get("3"), dict) else None
    if not revised or _has_cjk(revised):
        return sec3
    remaining = _scenario_math_issues(revised, chart_data, prices)
    if len(remaining) >= len(problems):
        logger.warning("[REPORT] Mục 3: bản sửa không tốt hơn (%d lỗi) — giữ bản gốc.", len(remaining))
        return sec3
    logger.info("[REPORT] Mục 3: đã sửa kịch bản, còn %d lỗi.", len(remaining))
    return revised


# Hệ thống CHƯA có dữ liệu vị thế quỹ (COT ICE Endex) — bảng đúng/sai từng thừa nhận mức "Cao" 13/10 thiếu xác nhận
# vị thế quỹ mà báo cáo sau vẫn không có dữ liệu đó. Chừng nào chưa có, xác suất mọi kịch bản tối đa "Trung bình".
POSITIONING_DATA_AVAILABLE = False


def _enforce_scenario_rules(sec3: Dict[str, Any]) -> None:
    """Quy tắc prompt mà LLM vẫn vi phạm sau vòng tự sửa → áp cứng bằng Python (tại chỗ):
      - "đi ngang" mà chiến lược là lệnh PHÁ biên 1 chiều → direction = chiều phá, xác suất "Thấp" (bản 17/10 trung hạn
        "đi ngang" + "bán khi đóng cửa dưới 81,50", Jack đã cảnh báo nhưng vẫn lọt);
      - chưa có dữ liệu vị thế quỹ → hạ "Cao" xuống "Trung bình"."""
    from services.strategy_levels import _split_buy_sell, is_breakout_entry, parse_strategy

    for sc in sec3.get("trading_scenarios") or []:
        if not isinstance(sc, dict):
            continue
        if sc.get("direction") == "đi ngang":
            entry = next((b.replace("*", "") for lbl, b in parse_strategy(sc.get("trading_strategy")) or []
                          if re.search("entry", lbl, re.I)), "")
            e_buy, e_sell, _ = _split_buy_sell(entry)
            side = "giảm" if e_sell and not e_buy else "tăng" if e_buy and not e_sell else None
            if side and is_breakout_entry(side, entry):
                logger.warning("[REPORT] Mục 3: kịch bản %s \"đi ngang\" nhưng lệnh phá biên 1 chiều — đổi direction=%s, xác suất Thấp.",
                               sc.get("horizon"), side)
                sc["direction"], sc["probability"] = side, "Thấp"
        if sc.get("probability") == "Cao" and not POSITIONING_DATA_AVAILABLE:
            logger.warning("[REPORT] Mục 3: kịch bản %s xác suất Cao nhưng chưa có dữ liệu vị thế quỹ — hạ xuống Trung bình.",
                           sc.get("horizon"))
            sc["probability"] = "Trung bình"


def _figure_conflicts(content: Dict[str, Any]) -> Dict[str, List[str]]:
    """{ref: [vấn đề]} cho bullet Mục 1 ("1.i") / Diễn biến chính ("dev.i") có số "còn lại" mâu thuẫn số học hoặc
    lệch với bài khác ở Mục 6 — dùng ĐÚNG phép kiểm tra của Jack QC (report_qc._check_remaining_*)."""
    from services.report_qc import _check_remaining_math, _check_remaining_vs_section6  # import muộn: tránh vòng

    out: Dict[str, List[str]] = {}
    items = [(f"1.{i}", b) for i, b in enumerate((content.get("1") or {}).get("bullets") or [])]
    items += [(f"dev.{i}", d) for i, d in enumerate((content.get("2") or {}).get("key_developments") or [])]
    for ref, it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "")
        found = _check_remaining_math(text, "", "", "") + _check_remaining_vs_section6(text, content, it, "", "", "")
        if found:
            out[ref] = [f["message"].lstrip(": ") for f in found]
    return out


async def _revise_figure_conflicts(content: Dict[str, Any]) -> None:
    """Sửa tại chỗ (1 lần gọi LLM) các bullet có số liệu mâu thuẫn — chỉ nhận bản sửa của bullet nào hết mâu thuẫn.
    Lỗi LLM chỉ log; Jack QC vẫn báo phần còn lại."""
    conflicts = _figure_conflicts(content)
    if not conflicts:
        return
    lookup = {f"1.{i}": b for i, b in enumerate((content.get("1") or {}).get("bullets") or [])}
    lookup.update({f"dev.{i}": d for i, d in enumerate((content.get("2") or {}).get("key_developments") or [])})
    sec6 = [a for r in ("international", "vietnam") for a in (content.get("6") or {}).get(r) or [] if isinstance(a, dict)]
    refs_text = "\n".join(
        f"- [{ref}] {lookup[ref].get('text')}\n  VẤN ĐỀ: {' | '.join(msgs)}" for ref, msgs in conflicts.items()
    )
    sec6_text = "\n".join(f"- [{a.get('source')}] {a.get('title')}: {a.get('summary')}" for a in sec6)[:12000]
    prompt = f"""Các câu sau trong báo cáo có SỐ LIỆU MÂU THUẪN (số học sai, hoặc lệch với nguồn chính xác hơn ở Tin tức chi tiết):
{refs_text}

TIN TỨC CHI TIẾT (Mục 6) của chính báo cáo — nguồn để đối chiếu:
{sec6_text}

YÊU CẦU: viết lại ĐÚNG các câu trên — dùng con số của nguồn chính xác/chi tiết nhất (ghi "theo <nguồn>"), chú thích ngắn độ lệch
với số kia (vd "100 triệu trong tiêu đề là số làm tròn"); bỏ con số sai số học. Giữ nguyên nhãn in đậm đầu câu (vd "**Dầu:**"),
giữ văn phong, độ dài tương đương, số kiểu Việt Nam (96; 1,5). KHÔNG thêm thông tin không có trong các nguồn trên.

CHỈ TRẢ VỀ JSON HỢP LỆ: {{"items": [{{"ref": "1.2", "text": "..."}}]}}"""
    try:
        raw = await _call_llm(prompt, system=CONCISENESS_RULE, max_tokens=2048)
    except Exception:
        logger.exception("[REPORT] Sửa số liệu mâu thuẫn lỗi — giữ nguyên.")
        return
    parsed = _extract_json(raw) if raw else None
    for row in (parsed or {}).get("items") or []:
        ref, text = (row or {}).get("ref"), (row or {}).get("text")
        if ref not in conflicts or not isinstance(text, str) or not text.strip() or _has_cjk(text):
            continue
        item = lookup[ref]
        old = item.get("text")
        item["text"] = text.strip()
        if ref in _figure_conflicts(content):
            item["text"] = old  # vẫn mâu thuẫn → giữ bản gốc, để Jack báo
            logger.warning("[REPORT] Bản sửa số liệu %s vẫn mâu thuẫn — giữ bản gốc.", ref)
        else:
            logger.info("[REPORT] Đã sửa số liệu mâu thuẫn ở %s.", ref)


async def _check_biz_triggers_llm(
    candidates: List[Any], news_text: str, index_lookup: Dict[int, Dict], target_date: str,
) -> Dict[int, Dict[str, Optional[str]]]:
    """Hỏi LLM: với từng gợi ý cũ (điều kiện dạng tin tức/chính sách — không có ngưỡng
    giá), TIN TỨC trong ngày có cho thấy tình huống kích hoạt ĐÃ XẢY RA chưa. Chỉ chấp
    nhận "đã xảy ra" khi kèm source_index trỏ tới bài có thật (backend map sang
    tên/URL) — không kiểm chứng được thì coi như chưa xảy ra. Lỗi LLM → {} (không
    làm hỏng việc sinh báo cáo; các gợi ý giữ nguyên trạng thái chờ)."""
    if not candidates or not index_lookup:
        return {}
    system = f"Bạn là trợ lý theo dõi các đề xuất kinh doanh cho doanh nghiệp SIM.\n{CONCISENESS_RULE}"
    user = f"""Ngày dữ liệu: {target_date}

CÁC GỢI Ý ĐÃ ĐỀ XUẤT TRƯỚC ĐÂY (đang chờ tình huống kích hoạt):
{biz_memory.describe_for_prompt(candidates)}

TIN TỨC TRONG NGÀY đã đánh số [N] — CHỈ trích dẫn số có thật:
{news_text}

YÊU CẦU: Với TỪNG gợi ý [S<id>], xác định tin tức trong ngày có cho thấy "Tình huống" của gợi ý ĐÃ THỰC SỰ XẢY RA hay chưa.
- "triggered": true CHỈ khi có bài báo nêu RÕ sự kiện/số liệu khớp với điều kiện (không suy diễn, không "có thể sắp xảy ra"); khi đó BẮT BUỘC "source_index" là số [N] của bài đó và "evidence" là 1 câu ngắn nêu điều đã xảy ra.
- Khớp ĐẦY ĐỦ mọi vế của điều kiện mới tính. Sự kiện chỉ LIÊN QUAN cùng chủ đề, khớp 1 phần, hoặc NGƯỢC với điều kiện (vd điều kiện "xác nhận ngừng bắn" nhưng tin là tập kích/đổ vỡ ngừng bắn) → "triggered": false.
- Điều kiện là NGƯỠNG GIÁ (vd "đóng cửa trên X", "> X USD") → LUÔN "triggered": false (hệ thống tự so với giá thật, không xét qua tin tức).
- Chưa đủ căn cứ → "triggered": false, "source_index": null, "evidence": null.

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"checks": [{{"id": 12, "triggered": false, "source_index": null, "evidence": null}}]}}"""
    try:
        raw = await _call_llm(user, system=system, max_tokens=2048)
    except Exception:
        logger.exception("[REPORT] Kiểm tra kích hoạt gợi ý cũ lỗi — bỏ qua.")
        return {}
    parsed = _extract_json(raw) if raw else None
    valid_ids = {c.id for c in candidates}
    result: Dict[int, Dict[str, Optional[str]]] = {}
    for chk in (parsed or {}).get("checks") or []:
        if not isinstance(chk, dict) or chk.get("triggered") is not True:
            continue
        raw_id = chk.get("id")
        if isinstance(raw_id, str):
            raw_id = raw_id.strip().lstrip("Ss")
        try:
            sid = int(raw_id)
        except (TypeError, ValueError):
            continue
        src_art = index_lookup.get(_first_source_index(chk.get("source_index")))
        if sid not in valid_ids or not src_art:
            continue
        result[sid] = {
            "evidence": (chk.get("evidence") or "").strip() or None,
            "source_name": src_art["source"],
            "source_url": src_art["url"],
        }
    return result


async def _check_biz_contradictions_llm(
    candidates: List[Any], news_text: str, index_lookup: Dict[int, Dict], prices_text: str, target_date: str,
    news_only: bool = False,
) -> Dict[int, Dict[str, Optional[str]]]:
    """Hỏi LLM: với từng gợi ý cũ CHƯA kích hoạt, diễn biến thực tế hôm nay (giá + tin
    tức) có đi NGƯỢC với giả định/kỳ vọng làm nền cho đề xuất không (vd đề xuất mua
    dự phòng vì kỳ vọng giá năng lượng tăng, nhưng giá giảm mạnh do nguồn cung phục
    hồi). CHỈ chấp nhận khi có bài báo có thật (source_index → tên/URL backend map) nêu sự
    kiện làm mất hiệu lực giả định — biến động giá của 1 phiên KHÔNG đủ để kết luận đề xuất
    sai (điều kiện chưa kích hoạt ≠ đề xuất sai; gợi ý ngưỡng giá không được đưa vào hàm này).
    Lỗi LLM/không đủ căn cứ → {} (giữ nguyên trạng thái chờ)."""
    if not candidates or not index_lookup:
        return {}
    if news_only:
        prices_text = "Không có — cuối tuần, không có phiên giao dịch. CHỈ đối chiếu theo tin tức (BẮT BUỘC source_index)."
    system = f"Bạn là trợ lý theo dõi các đề xuất kinh doanh cho doanh nghiệp SIM.\n{CONCISENESS_RULE}"
    user = f"""Ngày dữ liệu: {target_date}

CÁC GỢI Ý ĐÃ ĐỀ XUẤT TRƯỚC ĐÂY (tình huống kích hoạt CHƯA xảy ra):
{biz_memory.describe_for_prompt(candidates)}

DỮ LIỆU GIÁ PHIÊN VỪA QUA:
{prices_text}

TIN TỨC TRONG NGÀY đã đánh số [N] — CHỈ trích dẫn số có thật:
{news_text if index_lookup else "Không có tin tức."}

YÊU CẦU: Với TỪNG gợi ý [S<id>], xác định diễn biến thực tế hôm nay có đi NGƯỢC CHIỀU với giả định/kỳ vọng làm nền cho đề xuất hay không (tức đề xuất không còn phù hợp: thị trường/chính sách diễn ra trái với điều đề xuất dựa vào).
- "contradicted": true CHỈ khi có SỰ KIỆN trong bài báo (BẮT BUỘC "source_index" là số [N] có thật) làm mất hiệu lực RÕ RÀNG giả định của đề xuất (vd chính sách đã bị huỷ, xung đột đã chấm dứt). KHÔNG suy diễn. Điều kiện CHƯA KÍCH HOẠT/chưa chạm ngưỡng KHÔNG phải là ngược chiều — đề xuất vẫn được theo dõi tới ngày hết hạn ghi trong danh sách. Biến động giá của 1–vài phiên (kể cả đi xa ngưỡng) KHÔNG đủ để kết luận → false.
- "evidence": 1 câu ngắn nêu điều đã xảy ra trái với đề xuất.
- Không đủ căn cứ → "contradicted": false, "source_index": null, "evidence": null.

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"checks": [{{"id": 12, "contradicted": false, "source_index": null, "evidence": null}}]}}"""
    try:
        raw = await _call_llm(user, system=system, max_tokens=2048)
    except Exception:
        logger.exception("[REPORT] Kiểm tra đề xuất ngược chiều thực tế lỗi — bỏ qua.")
        return {}
    parsed = _extract_json(raw) if raw else None
    valid_ids = {c.id for c in candidates}
    result: Dict[int, Dict[str, Optional[str]]] = {}
    for chk in (parsed or {}).get("checks") or []:
        if not isinstance(chk, dict) or chk.get("contradicted") is not True:
            continue
        raw_id = chk.get("id")
        if isinstance(raw_id, str):
            raw_id = raw_id.strip().lstrip("Ss")
        try:
            sid = int(raw_id)
        except (TypeError, ValueError):
            continue
        evidence = (chk.get("evidence") or "").strip()
        if sid not in valid_ids or not evidence:
            continue
        src_art = index_lookup.get(_first_source_index(chk.get("source_index")))
        if src_art:
            result[sid] = {"evidence": evidence, "source_name": src_art["source"], "source_url": src_art["url"]}
    return result


# ─────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────

async def generate_report_content(session: AsyncSession, report_date: str) -> Dict[str, Any]:
    """
    Sinh nội dung báo cáo bằng cách gọi LLM riêng cho từng mục.
    Mỗi mục chỉ nhận đúng những topic tin tức liên quan.

    `report_date`: ngày TẠO báo cáo (khoá lưu DB). Toàn bộ dữ liệu (giá, tin tức,
    lịch sự kiện, prompt) dùng `target_date` = ngày dữ liệu = report_date - 1 —
    y hệt logic trước đây, xem report_data_date().
    """
    target_date = report_data_date(report_date)
    # Chủ Nhật/Thứ Hai: ngày dữ liệu là cuối tuần, không có phiên → CHỈ phân tích tin
    # tức. Không lấy giá (kể cả CBAM) để không phiên giá cũ nào lọt vào prompt/nội dung.
    no_price = is_no_price_report(report_date)
    # ── 1. Thu thập dữ liệu ──────────────────────────────────────────
    # 1 query duy nhất — override admin đã custom cho khung phân tích EUA (nếu
    # có), dùng cho cả Mục 2/3/5 bên dưới (xem services/eua_framework_admin.py).
    eua_framework_overrides = await get_overrides_map(session)
    if no_price:
        logger.info("[REPORT] %s: ngày dữ liệu %s không có phiên giao dịch — báo cáo chỉ phân tích tin tức.", report_date, target_date)
        prices, max_price_date, chart_data = [], None, []
    else:
        prices, max_price_date = await get_prices_for_report(session, target_date)
        chart_data = await get_historical_ohlc_for_report(session, "EUA", target_date)
    prev_report_close = await annotate_price_revisions(session, report_date, prices) if prices else {}
    news_by_topic, sources = await get_news_for_report(session, target_date)

    prices_text = _summarize_prices(prices)
    eua_trend = _eua_trend_summary(chart_data)
    eua_session_range = _eua_session_range_summary(chart_data)
    eua_volume = _eua_volume_summary(chart_data)
    eua_technical_levels = _eua_technical_levels_summary(chart_data)
    fuel_spread = _fuel_switching_spread_summary(prices)
    gasoil_crack_spread = _gasoil_crack_spread_summary(
        prices, prev_report_close, await _crack_spread_history(session, max_price_date) if max_price_date else None,
    ) or (
        "Không có dữ liệu Gasoil hoặc Brent trong phiên này — không tính được crack spread."
    )
    # Ước tính giá CBAM quý đang chạy (VWAP đấu giá EEX + lịch còn lại) — báo cáo CN/T2 không có giá → bỏ.
    eua_close = next((p.get("close") for p in prices if p.get("code") == "EUA"), None)
    auction_text = await _latest_auction_summary(session, max_price_date, eua_close) if max_price_date else ""
    cbam_est = (
        await cbam_estimate.build_cbam_estimate(session, max_price_date, eua_close, [c["close"] for c in chart_data])
        if prices and max_price_date else None
    )
    cbam_est_text = cbam_estimate.describe_for_prompt(cbam_est)
    # Đối chiếu dự báo của các báo cáo gần đây (chiều giá + lệnh khớp/chạm cắt lỗ) — khối cố định trang đầu.
    forecast_review_block, forecast_review_text = (
        await forecast_review.build_forecast_review(session, report_date, max_price_date)
        if prices and max_price_date else (None, "")
    )

    # Sự kiện đấu giá EEX tính lại mỗi ngày từ eua_auctions (_auction_calendar_events) — bỏ bản của
    # báo cáo trước để không bị đưa vào nhóm "cần cập nhật kết quả" (kết quả đấu giá nằm ở khối CBAM).
    pending_outcome_events, still_upcoming_events = _split_prev_events(
        [ev for ev in await get_previous_report_events(session, report_date)
         if not AUCTION_EVENT_RE.search(str(ev.get("event") or ""))],
        target_date,
    )
    prev_events_text = _format_prev_events(pending_outcome_events, still_upcoming_events)
    recurring_calendar_events = _compute_recurring_calendar_events(target_date)
    recurring_calendar_events += await _auction_calendar_events(session, target_date)
    recurring_calendar_events += _policy_calendar_events(target_date)

    # ── Fetch dữ liệu thực EIA + Baker Hughes (song song, non-blocking) ──
    # Chỉ fetch 2 nguồn có endpoint công khai:
    #   EIA: ir.eia.gov/wpsr/table1.csv  — CSV tĩnh, luôn là số liệu mới nhất
    #   BH : static Excel trên rigcount.bakerhughes.com
    # Nếu fetch/parse lỗi → graceful degradation (None), sự kiện vẫn hiển thị
    # trong Mục 8 nhưng không có outcome thực tế (LLM sẽ ghi "Chưa có thông tin").
    eia_data, bh_data = await asyncio.gather(
        fetch_eia_weekly_inventory(),
        fetch_baker_hughes_rig_count(),
        return_exceptions=False,
    )

    # Inject outcome thực vào recurring events đã xảy ra (ngày <= target_date).
    # Chỉ inject khi sự kiện đã qua và có data thực — tránh điền outcome vào
    # sự kiện tương lai (chúng sẽ vẫn giữ nguyên, không có field outcome).
    target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
    for ev in recurring_calendar_events:
        ev_date_str = ev.get("date", "")
        try:
            ev_date = datetime.strptime(ev_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        released = ev.get("released_at")
        if released:
            if datetime.fromisoformat(released) > datetime.now(_VN_TZ):
                continue  # chưa công bố tại thời điểm sinh báo cáo
        elif ev_date > target_dt:
            continue  # Sự kiện tương lai — không inject outcome

        event_name = ev.get("event", "")
        data = eia_data if "EIA" in event_name else bh_data if "Baker Hughes" in event_name else None
        if data is None:
            if released and ("EIA" in event_name or "Baker Hughes" in event_name):
                ev["outcome"] = RELEASED_NO_DATA_OUTCOME
            continue
        # File nguồn luôn là kỳ MỚI NHẤT đã công bố — chỉ gắn khi đúng tuần của sự kiện; lệch tuần
        # (kỳ này chưa công bố/lùi lịch) mà gắn vào sẽ thành số của tuần trước mang nhãn ngày mới.
        if not data_matches_event(event_name, ev_date, data.get("week_ending")):
            logger.warning(
                "[REPORT] Mục 8: %s ngày %s cần số tuần kết thúc %s nhưng nguồn đang là %s — không gắn.",
                event_name, ev_date_str, expected_data_week_ending(event_name, ev_date), data.get("week_ending"),
            )
            ev["outcome"] = "Chưa có số liệu kỳ này tại thời điểm lập báo cáo"
            continue
        ev["outcome"] = format_eia_outcome(data) if "EIA" in event_name else format_bh_outcome(data)

    recurring_events_text = _format_recurring_calendar_events(recurring_calendar_events)


    # Số liệu thật (tính sẵn bằng Python, không để LLM tự bịa) cho Mục 2:
    # giá đóng cửa phiên liền trước, biến động trong phiên liền trước, xu hướng 30 ngày.
    eua_prices = [p for p in prices if p["code"] == "EUA"]
    eua_key_facts = ""
    if eua_prices and chart_data:
        latest_close = eua_prices[0]["close"]
        week_change_pct = eua_prices[0].get("week_change_pct")
        # Tìm phiên cách >= 7 ngày (theo lịch) trước phiên mới nhất trong chart_data,
        # cùng logic với week_change_pct đã tính ở crawl_barchart.py, để lấy được số
        # tuyệt đối tăng/giảm — không chỉ %.
        week_close = None
        latest_date = date.fromisoformat(chart_data[-1]["date"])
        for row in reversed(chart_data[:-1]):
            if (latest_date - date.fromisoformat(row["date"])).days >= 7:
                week_close = row["close"]
                break
        if week_change_pct is not None and week_close:
            week_delta = latest_close - week_close
            week_change_str = f"{'tăng' if week_delta > 0 else ('giảm' if week_delta < 0 else 'đi ngang')} {abs(week_delta):.2f} ({week_change_pct:+.2f}%)"
        elif week_change_pct is not None:
            week_change_str = f"{week_change_pct:+.2f}%"
        else:
            week_change_str = "không có dữ liệu"
        prev_close = chart_data[-2]["close"] if len(chart_data) >= 2 else None
        if prev_close:
            delta = latest_close - prev_close
            delta_pct = (delta / prev_close * 100) if prev_close else 0
            direction = "tăng" if delta > 0 else ("giảm" if delta < 0 else "đi ngang")
            eua_key_facts = (
                f"Đóng cửa phiên liền trước ({target_date}): {latest_close:.2f} EUR/tCO2. "
                f"Δ ngày: {direction} {abs(delta):.2f} ({delta_pct:+.1f}%) so với phiên trước đó ({prev_close:.2f}). "
                f"Δ tuần: {week_change_str} so với 7 ngày trước. "
                f"{eua_session_range} {eua_trend} {eua_volume}"
                + (f"\n{auction_text}" if auction_text else "")
            )
        else:
            eua_key_facts = (
                f"Đóng cửa phiên liền trước ({target_date}): {latest_close:.2f} EUR/tCO2. "
                f"Δ tuần: {week_change_str} so với 7 ngày trước. "
                f"{eua_session_range} {eua_trend} {eua_volume}"
            )
    else:
        eua_key_facts = "Không có dữ liệu giá EUA cho phiên này."

    # Mục 2 (market_drivers): tag "FACT" dựa trên tin tức cũng cần trích nguồn cụ
    # thể — dùng cùng cơ chế đánh số [N] để LLM chỉ chọn số có thật, backend map
    # sang URL thật (tránh bịa nguồn).
    section2_news_text, section2_index_lookup = _filter_news_with_index(news_by_topic, "2")
    # Mục 1 (Tóm tắt điều hành) và Mục 4 (Cập nhật tín chỉ carbon & CBAM): mỗi
    # bullet dựa trên tin tức cũng cần hiện tên nguồn (giống Mục 6) — cùng cơ chế
    # đánh số [N], LLM chỉ chọn số có thật, backend tự map sang tên/URL nguồn thật.
    section1_news_text, section1_index_lookup = _filter_news_with_index(news_by_topic, "1")
    section4_news_text, section4_index_lookup = _filter_news_with_index(news_by_topic, "4")
    # "Diễn biến chính" (dưới Bảng giá nhanh): mỗi tin bắt buộc có nguồn — cùng cơ chế [N].
    dev_news_text, dev_index_lookup = _filter_news_with_index(news_by_topic, "dev", max_articles=15)
    # Mục 3: đánh số [N] để mỗi watchpoint "Cần theo dõi" bắt buộc gắn 1 tin có thật (vd nhận định "CEO Eni
    # kêu gọi tạm ngưng ETS" từng xuất hiện mà không có trong tin tức lẫn Nguồn tham khảo).
    section3_news_text, section3_index_lookup = _filter_news_with_index(news_by_topic, "3", max_articles=60, per_topic=4)
    # Khối theo dõi chính sách ETS/MSR: tin chính sách 21 ngày, tiếp số [N] sau tin trong ngày của Mục 3.
    policy_articles = await get_ets_policy_news(session, target_date)
    ets_policy_block = _ets_policy_prompt_block(
        _merge_policy_articles(section3_index_lookup, policy_articles), section3_index_lookup,
    )
    open_trade_list = forecast_review.open_trades(forecast_review_block)

    # ── Bộ nhớ gợi ý kinh doanh (services/biz_memory.py) ──
    # Gợi ý ngắn hạn các báo cáo trước (trong 10 ngày) — kiểm tra tình huống kích
    # hoạt đã xảy ra chưa: ngưỡng giá → so thẳng với giá thật; điều kiện tin tức →
    # LLM đối chiếu tin trong ngày (bắt buộc kèm bài báo). CHỈ ĐỌC ở đây — ghi lại
    # ở bước cuối (xem persist bên dưới).
    active_suggestions = await biz_memory.load_active(session, report_date)
    triggered_suggestions: Dict[int, Dict[str, Optional[str]]] = {}
    llm_check_candidates = []
    # Gợi ý có điều kiện NGƯỠNG GIÁ: chưa chạm ngưỡng = "chưa kích hoạt", KHÔNG phải đề xuất sai —
    # giữ theo dõi tới hết hạn (biz_memory.expires_on), không đưa qua bước "thực tế ngược chiều".
    price_condition_ids = set()
    for s in active_suggestions:
        # LLM quên trả trigger_rule (hoặc báo cáo CN/T2 ép null) nhưng câu tình huống là ngưỡng
        # giá → tự dựng rule từ câu chữ để so với giá thật, KHÔNG để LLM đọc tin rồi tự kết luận.
        # Rule LLM lưu có thể lệch câu chữ (sai chiều, "1.500" đọc thành 1,5) → câu chữ thắng (resolve_price_rule).
        rule = biz_memory.resolve_price_rule(s.trigger_rule, s.trigger, prices)
        if rule or biz_memory.looks_like_price_condition(s.trigger):
            price_condition_ids.add(s.id)
        if rule:
            evidence = biz_memory.check_price_rule(rule, prices)
            if evidence:
                triggered_suggestions[s.id] = {
                    "evidence": evidence,
                    "source_name": "Giá chốt phiên (Barchart/ICE)",
                    "source_url": next(
                        (p.get("source_url") for p in prices if p.get("code") == rule.get("code")), None
                    ),
                }
        elif biz_memory.looks_like_price_condition(s.trigger):
            # Ngưỡng giá của mã hệ thống KHÔNG theo dõi (vd NG/Henry Hub) — không kiểm chứng được
            # bằng số liệu → giữ trạng thái chờ, không cho LLM suy ra "đã kích hoạt" từ tin tức.
            logger.info("[REPORT] Gợi ý S%s: ngưỡng giá không có mã trong bảng giá — không xét kích hoạt.", s.id)
        else:
            llm_check_candidates.append(s)
    biz_news_text, biz_index_lookup = _filter_news_with_index(news_by_topic, "biz", max_articles=15)
    triggered_suggestions.update(
        await _check_biz_triggers_llm(llm_check_candidates, biz_news_text, biz_index_lookup, target_date)
    )
    # Gợi ý chưa kích hoạt → đối chiếu thêm: thực tế hôm nay có đi NGƯỢC giả định của đề xuất không.
    not_triggered = [s for s in active_suggestions if s.id not in triggered_suggestions]
    contradicted_suggestions = await _check_biz_contradictions_llm(
        [s for s in not_triggered if s.id not in price_condition_ids], biz_news_text, biz_index_lookup, prices_text, target_date, news_only=no_price,
    )
    still_tracking = [s for s in not_triggered if s.id not in contradicted_suggestions]
    dismissed_suggestions = await biz_memory.load_recent_dismissed(session, report_date)
    valid_price_codes = {str(p["code"]).upper() for p in prices if p.get("code") and p.get("close") is not None}

    # ── 2. Gọi LLM từng mục song song (tuần tự để tránh rate limit) ──
    content: Dict[str, Any] = {}

    if no_price:
        # Không có Mục "2" (market_drivers — động lực theo trạng thái giá từng mã).
        SECTIONS = [
            ("1", _prompt_section1_news_only(section1_news_text, target_date)),
            ("dev", _prompt_key_developments(
                dev_news_text, target_date, _topics_present(news_by_topic, "dev"),
                overrides=eua_framework_overrides, news_only=True,
            )),
            ("3", _prompt_section3_news_only(
                section3_news_text, target_date,
                _topics_present(news_by_topic, "3"), overrides=eua_framework_overrides,
            )),
            ("4", _prompt_section4(section4_news_text, target_date, cbam_est_text)),
            ("8", _prompt_section8(
                _filter_news_for_section(news_by_topic, "8"),
                prev_events_text, target_date, recurring_events_text,
            )),
            ("biz", _prompt_biz_recommendation(
                biz_news_text,
                prices_text, eua_trend, target_date,
                tracking_text=biz_memory.describe_for_prompt(still_tracking),
                dismissed_text=biz_memory.describe_for_prompt(dismissed_suggestions),
                news_only=True,
            )),
        ]
    else:
        # Tin/số liệu báo cáo trước đã nêu — để không nhắc lại sự kiện cũ như tin mới và nêu cả 2 số khi lệch.
        prev_digest = await get_previous_report_digest(session, report_date)
        SECTIONS = [
            ("1", _prompt_section1(
                section1_news_text + prev_digest,
                prices_text, eua_trend, eua_session_range, target_date, fuel_spread=fuel_spread,
            )),
            ("2", _prompt_section2(
                eua_key_facts, prices_text, eua_trend, gasoil_crack_spread,
                section2_news_text + prev_digest, target_date, _topics_present(news_by_topic, "2"),
                overrides=eua_framework_overrides, fuel_spread=fuel_spread,
            )),
            ("dev", _prompt_key_developments(
                dev_news_text, target_date, _topics_present(news_by_topic, "dev"),
                overrides=eua_framework_overrides, fuel_spread=fuel_spread,
            )),
            ("3", _prompt_section3(
                section3_news_text + prev_digest,
                prices_text + (f"\n\n{auction_text}" if auction_text else ""),
                eua_trend, eua_session_range, gasoil_crack_spread, target_date,
                _topics_present(news_by_topic, "3"),
                overrides=eua_framework_overrides,
                eua_technical_levels=eua_technical_levels,
                fuel_spread=fuel_spread,
                prev_scenarios="\n\n".join(filter(None, [
                    await get_previous_scenarios_text(
                        session, report_date,
                        next((p.get("close") for p in prices if str(p.get("code", "")).upper() == "EUA"), None),
                    ),
                    forecast_review_text,
                ])),
                cbam_signal=cbam_est_text,
                open_orders=forecast_review.describe_open_trades(open_trade_list),
            )),
            ("4", _prompt_section4(
                section4_news_text,
                target_date,
                cbam_est_text,
            )),
            ("8", _prompt_section8(
                _filter_news_for_section(news_by_topic, "8"),
                prev_events_text, target_date, recurring_events_text,
            )),
            ("biz", _prompt_biz_recommendation(
                biz_news_text,
                prices_text + "\n\n" + cbam_est_text, eua_trend, target_date,
                tracking_text=biz_memory.describe_for_prompt(still_tracking),
                dismissed_text=biz_memory.describe_for_prompt(dismissed_suggestions),
                valid_codes=", ".join(sorted(valid_price_codes)),
            )),
        ]

    FALLBACKS: Dict[str, dict] = {
        "1": {"title": "Tóm tắt điều hành", "bullets": [{"text": "Không thể sinh nội dung tự động.", "source_index": None}]},
        "2": {"market_drivers": {"bullish": [], "bearish": []}},
        "dev": {"key_developments": []},
        "3": {"title": "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA",
              "instrument_notes": {},
              "analysis_blocks": [{"heading": "Phân tích", "content": "Không có dữ liệu."}],
              "trading_scenarios": []},
        "4": {"title": "Cập nhật tín chỉ carbon & CBAM", "bullets": [{"text": "Không có diễn biến trọng yếu.", "source_index": None}]},
        "8": {"title": "Lịch sự kiện 7 ngày tới", "events": []},
        "biz": {"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": [], "long_term": []},
    }

    sem = asyncio.Semaphore(1)

    async def _process_section(sec_key: str, system_prompt: str, user_prompt: str):
        async with sem:
            logger.info(f"[REPORT] Đang sinh mục {sec_key}...")
            # Tạo khoảng trễ giữa các request liên tiếp để giảm tải rate limit
            await asyncio.sleep(3)
            raw, parsed = None, None
            for attempt in range(SECTION_PARSE_RETRIES):
                raw = await _call_llm(
                    user_prompt, system=system_prompt,
                    max_tokens=SECTION_MAX_TOKENS.get(sec_key, 8192),
                )
                parsed = _extract_json(raw) if raw else None
                if parsed and _has_cjk(parsed):
                    logger.warning(f"[REPORT] Mục {sec_key} lẫn ký tự CJK (lần {attempt + 1}), gọi lại LLM...")
                    if attempt == SECTION_PARSE_RETRIES - 1:
                        break  # hết lượt thử: giữ bản cuối (đã cảnh báo ở log)
                    continue
                if parsed:
                    break
                logger.warning(
                    f"[REPORT] Mục {sec_key} parse JSON lỗi (lần {attempt + 1}/{SECTION_PARSE_RETRIES})"
                    + (", gọi lại LLM..." if attempt < SECTION_PARSE_RETRIES - 1 else "")
                )

            if parsed and sec_key in parsed:
                sec_data = parsed[sec_key]
                logger.info(f"[REPORT] Mục {sec_key} OK.")
            elif parsed and set(parsed) == set(FALLBACKS[sec_key]):
                # Model đôi khi bỏ lớp bọc {"<sec_key>": ...} và trả thẳng nội dung mục
                # (vd {"key_developments": [...]} cho mục dev) — JSON vẫn hợp lệ, không cần fallback.
                sec_data = parsed
                logger.info(f"[REPORT] Mục {sec_key} OK (model bỏ lớp bọc '{sec_key}').")
            else:
                logger.warning(
                    f"[REPORT] Mục {sec_key} thất bại, dùng fallback. "
                    f"Raw ({len(raw) if raw else 0} ký tự): {raw[-500:] if raw else 'None'}"
                )
                sec_data = FALLBACKS[sec_key]

            if sec_key == "3" and not no_price and sec_data is not FALLBACKS["3"]:
                sec_data = await _revise_scenarios_if_invalid(sec_data, system_prompt, user_prompt, chart_data, prices)
                _enforce_scenario_rules(sec_data)
            return sec_key, sec_data

    # "Diễn biến chính" chạy TRƯỚC để chốt chiều tác động của từng tin; Mục 2/3 phải dùng đúng chiều đó (bản 17/10:
    # cùng tin kho khí EU được đánh giá 3 chiều — tăng ở Diễn biến chính, cột giảm ở Mục 2, "kênh ngược" ở Phân tích).
    dev_entry = next((e for e in SECTIONS if e[0] == "dev"), None)
    dev_result = await _process_section("dev", *dev_entry[1]) if dev_entry else None
    dev_resolved = _resolve_key_developments((dev_result[1] if dev_result else {}).get("key_developments"), dev_index_lookup)
    dev_impacts = {d["source_url"]: d["impact"] for d in dev_resolved}
    lookups = {"2": section2_index_lookup, "3": section3_index_lookup}
    SECTIONS = [
        (k, (sys_p, usr_p + (ets_policy_block if k == "3" else "")
             + (_dev_verdict_block(dev_impacts, lookups[k]) if k in lookups else "")))
        for k, (sys_p, usr_p) in SECTIONS if k != "dev"
    ]
    tasks = [_process_section(k, sys_p, usr_p) for k, (sys_p, usr_p) in SECTIONS]
    results = ([dev_result] if dev_result else []) + list(await asyncio.gather(*tasks))

    section2_data: dict = FALLBACKS["2"]
    key_developments: List[Dict] = []
    for section_key, section_data in results:
        if section_key == "2":
            section2_data = section_data
        elif section_key == "dev":
            # Gộp vào content["2"] (hiển thị ngay dưới Bảng giá nhanh), không thành mục riêng.
            raw_dev = section_data.get("key_developments") or []
            key_developments = _resolve_key_developments(raw_dev, dev_index_lookup)
            # Phân biệt "LLM trả rỗng" / "bị loại vì source_index sai" / "mục dev lỗi → fallback"
            # (xem thêm log "Mục dev thất bại" ở trên) khi Diễn biến chính trống.
            logger.info(
                "[REPORT] Diễn biến chính: LLM trả %d mục, giữ %d mục (bài ứng viên: %d).",
                len(raw_dev), len(key_developments), len(dev_index_lookup),
            )
        elif section_key == "8":
            content["8"] = {
                **section_data,
                "events": _finalize_section8_events(
                    section_data.get("events"), target_date, recurring_calendar_events,
                ),
            }
        elif section_key == "1":
            content["1"] = {**section_data, "bullets": _resolve_bullet_sources(section_data.get("bullets"), section1_index_lookup)}
        elif section_key == "4":
            content["4"] = {**section_data, "bullets": _resolve_bullet_sources(section_data.get("bullets"), section4_index_lookup)}
        elif section_key == "3":
            # "Diễn biến chính" không còn là 1 heading riêng trong Mục 3 nữa — LLM giờ
            # trả về "instrument_notes" (mã -> ghi chú ngắn, KHÔNG kèm số giá vì Δ ngày/Δ
            # tuần đã có sẵn trong bảng giá), merge thẳng vào field "note" của đúng dòng
            # giá tương ứng (theo "code") trước khi content["2"]["prices"] được dựng bên
            # dưới — cùng list `prices` object nên mutate ở đây là đủ, không cần gán lại.
            # Giữ nguyên "note" thủ công/bất thường có sẵn từ DB (price.note) nếu có,
            # nối thêm ghi chú của LLM phía sau thay vì ghi đè.
            instrument_notes = section_data.get("instrument_notes") or {}
            for p in prices:
                llm_note = instrument_notes.get(p["code"])
                if not llm_note:
                    continue
                # CBAM chỉ giữ ghi chú cố định "Giá chốt theo quý…" — không nối ghi chú LLM.
                if p["code"] == "CBAM":
                    continue
                p["note"] = f"{p['note']} {llm_note}".strip() if p.get("note") else llm_note
            content["3"] = {k: v for k, v in section_data.items() if k != "instrument_notes"}
            content["3"]["ets_policy"] = _resolve_ets_policy(section_data.get("ets_policy"), section3_index_lookup)
            content["3"]["sources"] = _resolve_section3_citations(content["3"], section3_index_lookup)
            # Giả định mô hình fuel switching hiển thị cho người đọc (dưới Phân tích) để kiểm chứng giá EUA hòa vốn.
            content["3"]["fuel_model_note"] = fuel_model_note(fuel_spread_state(prices)) if not no_price else ""
            if no_price:
                # Kịch bản giao dịch cần vùng giá/Entry/Mục tiêu — không có ở ngày không có phiên.
                content["3"]["trading_scenarios"] = []
                content["3"].pop("hedge_action", None)
                content["3"].pop("open_positions", None)
            else:
                _finalize_hedge_fields(content["3"], open_trade_list, cbam_est)
        else:
            content[section_key] = section_data

    def _resolve_driver_items(items: List[dict]) -> List[dict]:
        resolved = []
        for it in items or []:
            src_art = section2_index_lookup.get(_first_source_index(it.get("source_index")))
            resolved.append({
                "tag": it.get("tag", "OPINION"),
                "text": it.get("text", ""),
                "source_name": src_art["source"] if src_art else None,
                "source_url": src_art["url"] if src_art else None,
            })
        return resolved

    raw_drivers = section2_data.get("market_drivers") or {"bullish": [], "bearish": []}
    raw_drivers = _drop_conflicting_drivers(raw_drivers, section2_index_lookup, dev_impacts)
    if no_price:
        # Giữ đủ khoá cũ (rỗng) để frontend/Jenny/Jack QC đọc an toàn; "no_price_data"
        # là cờ để ẩn bảng giá, biểu đồ, thanh giá và khối KHUYẾN NGHỊ VỊ THẾ.
        content["2"] = {
            "title": "Diễn biến chính",
            "no_price_data": True,
            "no_price_notice": NO_PRICE_NOTICE.format(
                data_date=datetime.strptime(target_date, "%Y-%m-%d").strftime("%d/%m/%Y")
            ),
            "price_timestamp": "",
            "key_facts": "",
            "prices": [],
            "key_developments": key_developments,
            "chart_data": [],
            "market_drivers": {"bullish": [], "bearish": []},
        }
    else:
        content["2"] = {
            "title": "Bảng giá nhanh",
            "price_timestamp": f"Giá chốt phiên {max_price_date or target_date} (nguồn: Barchart/ICE EOD)",
            "key_facts": eua_key_facts,
            "prices": prices,
            "cbam_estimate": cbam_est,
            "forecast_review": forecast_review_block,
            "key_developments": key_developments,
            "chart_data": chart_data,
            "market_drivers": {
                "bullish": _resolve_driver_items(raw_drivers.get("bullish")),
                "bearish": _resolve_driver_items(raw_drivers.get("bearish")),
            },
        }

    section6_news = _build_section6_news(news_by_topic)
    # Bài đã được trích ở mục khác (kể cả gợi ý kinh doanh) luôn có mặt ở Mục 6 — bản 17/10 ghi "Không có tin tức
    # Việt Nam" trong khi gợi ý dài hạn dùng bài moitruong.net.vn về Nghệ An.
    cited_urls = _content_source_urls(content) | {
        art["url"] for it in ((content.get("biz") or {}).get("short_term") or []) + ((content.get("biz") or {}).get("long_term") or [])
        if isinstance(it, dict) and (art := biz_index_lookup.get(_first_source_index(it.get("source_index"))))
    }
    section6_international, section6_vietnam = _split_section6_by_subject(
        await _summarize_section6_articles(
            section6_news["international"] + section6_news["vietnam"], target_date, keep_urls=cited_urls,
        )
    )
    content["6"] = {
        "title": "Chi tiết các tin tức chính",
        "international": section6_international,
        "vietnam": section6_vietnam,
    }
    # Mục 1/Diễn biến chính viết TRƯỚC khi Mục 6 tóm tắt xong và chỉ thấy 10 bài × 500 ký tự → có thể dùng số làm
    # tròn/mâu thuẫn trong khi Mục 6 có nguồn chính xác hơn (vd IEA "còn 100 triệu thùng" vs Argus "khoảng 96").
    await _revise_figure_conflicts(content)

    # ── Bộ nhớ gợi ý kinh doanh: chuẩn hoá gợi ý mới + ghi DB (BƯỚC CUỐI) ──
    biz = content.get("biz") or dict(FALLBACKS["biz"])
    def _biz_source(it: Dict[str, Any]) -> Dict[str, Optional[str]]:
        art = biz_index_lookup.get(_first_source_index(it.get("source_index")))
        return {"source_name": art["source"], "source_url": art["url"]} if art else {}

    # Gợi ý KHÔNG kiểm chứng được (không có bài nguồn, cũng không phải ngưỡng giá) bị loại — người đọc
    # phải lần được tới tin gốc (vd "L&G và NTR huy động 330 triệu EUR" không có nguồn từng lọt vào báo cáo).
    new_short_term: List[Dict[str, Any]] = []
    for it in biz.get("short_term") or []:
        if not isinstance(it, dict) or not (it.get("trigger") or "").strip() or not (it.get("action") or "").strip():
            continue
        item = {
            "trigger": it["trigger"].strip(),
            "trigger_rule": biz_memory.resolve_price_rule(
                biz_memory.normalize_trigger_rule(it.get("trigger_rule"), valid_price_codes), it["trigger"], prices,
            ),
            "action": it["action"].strip(),
            "reason": (it.get("reason") or "").strip(),
            **{k: str(it[k]).strip() for k in BIZ_SHORT_EXTRA_KEYS if str(it.get(k) or "").strip()},
            **_biz_source(it),
        }
        if not item.get("source_url") and not item["trigger_rule"]:
            logger.warning("[REPORT] Gợi ý ngắn hạn không có nguồn/ngưỡng giá — bỏ: %s", item["trigger"][:120])
            continue
        new_short_term.append(item)
    new_long_term: List[Dict[str, Any]] = []
    for it in biz.get("long_term") or []:
        if not (isinstance(it, dict) and (it.get("opportunity") or "").strip() and (it.get("solution") or "").strip()):
            continue
        item = {
            "opportunity": it["opportunity"].strip(),
            "solution": it["solution"].strip(),
            "expectation": (it.get("expectation") or "").strip(),
            **{k: str(it[k]).strip() for k in BIZ_LONG_EXTRA_KEYS if str(it.get(k) or "").strip()},
            **_biz_source(it),
        }
        if not item.get("source_url"):
            logger.warning("[REPORT] Gợi ý dài hạn không có nguồn — bỏ: %s", item["opportunity"][:120])
            continue
        new_long_term.append(item)
    # "tracking" = đề xuất cũ CHƯA kích hoạt (vẫn đúng, chỉ chưa tới điều kiện) — hiển thị kèm hạn theo dõi.
    reminders, tracking = biz_memory.reminders_for_content(active_suggestions, triggered_suggestions, contradicted_suggestions)
    try:
        # flush trong cùng session/transaction — caller commit cùng report.content.
        created, created_long = await biz_memory.persist(
            session, report_date, triggered_suggestions, active_suggestions, new_short_term, new_long_term,
            contradicted=contradicted_suggestions,
        )
        # Gắn id (có sau flush, cùng thứ tự) để admin gỡ đúng gợi ý trên giao diện.
        for it, obj in zip(new_short_term, created):
            it["id"] = obj.id
        for it, obj in zip(new_long_term, created_long):
            it["id"] = obj.id
    except Exception:
        # Lỗi ghi bộ nhớ không được làm hỏng cả báo cáo: rollback phần ghi dở, vẫn
        # trả nội dung (hôm nay Jenny không "nhớ" được gợi ý mới — log để xử lý).
        logger.exception("[REPORT] Lỗi ghi bộ nhớ gợi ý kinh doanh %s — bỏ qua.", report_date)
        await session.rollback()
    content["biz"] = {
        **biz,
        # trigger_rule chỉ dùng nội bộ để kiểm tra ngưỡng giá — không lưu vào nội dung hiển thị.
        "short_term": [{k: v for k, v in it.items() if k != "trigger_rule"} for it in new_short_term],
        "long_term": new_long_term,
        # Gợi ý cũ vừa kích hoạt/bị tin tức vô hiệu hôm nay (khối "Jenny nhắc lại") + gợi ý
        # chưa kích hoạt, còn theo dõi tới "expires_on" (cũng nằm trong tracking_text của prompt biz).
        "reminders": reminders,
        "tracking": tracking,
    }

    # Nhãn % sai, mô tả vị trí trong biên độ sai, vùng giá độ rộng 0, số/ngày kiểu tiếng Anh — sửa bằng Python.
    for fix in apply_consistency_fixes(content):
        logger.warning("[REPORT] %s: tự sửa %s — %s", report_date, fix["path"], fix["message"])

    cited_articles = _collect_cited_articles(news_by_topic, limit=1000)
    cited_articles += [a for a in policy_articles if a["url"] not in {c["url"] for c in cited_articles}]
    # Liệt kê nguồn THỰC SỰ xuất hiện trong báo cáo: source_url của bullet/driver/diễn biến/gợi ý nhắc lại và
    # các bài ở Mục 6 (trước đây bỏ Mục 6 + dựng trước Mục biz → bài có trong Mục 6/gợi ý mà thiếu ở Mục 9).
    # Không liệt kê bài chỉ đưa vào prompt mà không được trích → tránh "nguồn mồ côi".
    used_urls: set = set()

    def _walk_urls(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "source_url" and isinstance(v, str):
                    used_urls.add(v)
                else:
                    _walk_urls(v)
        elif isinstance(node, list):
            for v in node:
                _walk_urls(v)

    for _k, _v in content.items():
        if _k != "9":
            _walk_urls(_v)
    for _region in ("international", "vietnam"):
        for _a in (content.get("6") or {}).get(_region) or []:
            if isinstance(_a, dict) and isinstance(_a.get("url"), str):
                used_urls.add(_a["url"])
    used_articles = [a for a in cited_articles if a["url"] in used_urls]
    content["9"] = {
        "title": "Nguồn tham khảo",
        "items": [
            {"source": a["source"], "title": a["title"], "url": a["url"]}
            for a in (used_articles or cited_articles[:40])
        ],
    }

    # Chuỗi nguồn gốc → nơi đăng lại (tóm tắt Mục 6 nhận diện "origin") áp cho mọi chỗ trích cùng bài.
    chain_by_url = {
        a["url"]: source_chain_label(a.get("origin"), a["source"])
        for a in section6_international + section6_vietnam
        if isinstance(a, dict) and a.get("origin") and a.get("url") and a.get("source")
    }
    if chain_by_url:
        apply_source_chains(content, chain_by_url)

    return content
