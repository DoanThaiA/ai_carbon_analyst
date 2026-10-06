import json
import re
import logging
from datetime import datetime, timedelta, timezone, date, time as dtime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from urllib.parse import quote
import asyncio
import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import Article, Price, Instrument, PriceCrawlSource, Report
from core.config import Settings
from services import biz_memory
from services import eua_causal_chains as chains
from services.eua_framework_admin import get_overrides_map
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
REPORT_MODEL_HAIKU = "claude-haiku-4-5"

# Meta-instruction chống dài dòng — đặt ở đầu system message mọi section,
# LLM anchor vào instruction đầu tiên mạnh nhất.
CONCISENESS_RULE = (
    "QUY TẮC VIẾT BẮT BUỘC: Mỗi ý tối đa 1–2 câu ngắn. Không câu dẫn dắt/đệm/chuyển tiếp thừa. "
    "Không lặp số liệu đã nêu ở trường/mục khác. Ưu tiên SỐ LIỆU → KẾT LUẬN, bỏ diễn giải thừa. "
    "Cấm bịa số liệu/sự kiện/source không có trong dữ liệu đã cung cấp."
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


def _eua_framework(
    topics_present: List[str], *, full: bool, overrides: Optional[Dict[str, str]] = None
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
    """
    horizon = "medium" if full else "short"
    dynamic = chains.build_context(topics_present, horizon=horizon, overrides=overrides)

    parts = [
        "=== KHUNG PHÂN TÍCH GIÁ CARBON (EUA) — BẮT BUỘC ÁP DỤNG ===",
        _EUA_FRAMEWORK_PREAMBLE,
        "A. CÁC MỐI LIÊN HỆ LIÊN THỊ TRƯỜNG (cross-market signals) — CHỈ áp dụng cơ chế nào có dữ "
        "liệu/tin tức thực sự hỗ trợ, KHÔNG suy diễn gượng ép:",
        dynamic,
        _EUA_FRAMEWORK_NOTES,
    ]
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


def _barchart_url(symbol: str) -> str:
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


def _format_pct_with_abs(pct: Optional[float], close_price: float) -> str:
    """Format % kèm số tuyệt đối tăng/giảm, vd '+2.34% (+1.05)' — suy ngược giá
    kỳ trước từ close_price và pct (không lưu giá kỳ trước riêng trong DB)."""
    if pct is None:
        return "-"
    denom = 1 + pct / 100
    if denom == 0:
        return f"{pct:+.2f}%"
    prev_close = close_price / denom
    abs_change = close_price - prev_close
    return f"{pct:+.2f}% ({abs_change:+.2f})"


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

    prices = []
    for price, instrument in rows:
        is_up = price.day_change_pct is not None and price.day_change_pct > 0
        # Ghi chú bất thường: volume đột biến hoặc note thủ công
        note = price.note or ""
        symbol = barchart_symbols.get(instrument.code)
        prices.append({
            "name": instrument.name,
            "code": instrument.code,
            "price": f"{price.close_price:,.4f} {instrument.unit}",
            "dday": _format_pct_with_abs(price.day_change_pct, price.close_price),
            "dweek": _format_pct_with_abs(price.week_change_pct, price.close_price),
            "up": is_up,
            "note": note,
            "close": price.close_price,
            "day_change_pct": price.day_change_pct,
            "week_change_pct": price.week_change_pct,
            "volume": price.volume,
            "category": instrument.category,
            "source_url": _barchart_url(symbol) if symbol else None,
        })

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


async def get_news_for_report(session: AsyncSession, target_date_str: str) -> tuple[Dict[str, List[Dict]], List[str]]:
    """Lấy tin tức trong khung 07:00 (VN) ngày báo cáo → 07:00 (VN) ngày hôm sau.

    Khớp với lịch tự động: news_crawl chạy 06:00 & 12:00 (VN) mỗi ngày, report cho
    ngày T được auto-generate lúc 07:00 (VN) ngày T+1 — nên tin tức đưa vào báo cáo
    ngày T là tin thu thập từ 07:00 (VN) ngày T đến 07:00 (VN) ngày T+1 (bao gồm cả
    đợt crawl 06:00 của ngày T+1, chạy ngay trước khi report được sinh).
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
    for article in articles:
        if not article.topic:
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
                "source": article.source,
                "url": article.url,
                "region": article.region,
                "topics": topic_labels,
                # Ngày đăng bài (fallback ngày crawl) — dùng trích dẫn "(Nguồn, ngày)" ở "Diễn biến chính"
                "published_date": (article.published_at or article.crawled_at).strftime("%d/%m/%Y"),
            })

    return news_by_topic, list(sources)


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
                f"{i+1}. [{art['source']}] {art['title']}\n   Tóm tắt: {art['summary']}"
            )
    return "\n".join(text_parts) if text_parts else "Không có tin tức liên quan."


def _filter_news_with_index(
    news_by_topic: Dict[str, List[Dict]], section_key: str, max_articles: int = 10
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
        for art in news_by_topic.get(topic, []):
            if art["url"] in seen_urls:
                continue
            seen_urls.add(art["url"])
            numbered.append(art)
            if len(numbered) >= max_articles:
                break
        if len(numbered) >= max_articles:
            break

    if not numbered:
        return "Không có tin tức liên quan.", {}

    index_lookup = {i + 1: art for i, art in enumerate(numbered)}
    lines = [
        f"[{i}] [{art['source']}] {art['title']}\n   Tóm tắt: {art['summary']}"
        for i, art in index_lookup.items()
    ]
    return "\n".join(lines), index_lookup


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
    articles = _collect_cited_articles(news_by_topic, limit=1000)
    international = [a for a in articles if a.get("region") != "vietnam"][:limit_per_region]
    vietnam = [a for a in articles if a.get("region") == "vietnam"][:limit_per_region]
    return {"international": international, "vietnam": vietnam}


def _prompt_section6_summary(article: Dict, target_date: str) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường carbon & năng lượng châu Âu.
Ngày báo cáo: {target_date}

BÀI VIẾT CẦN TÓM TẮT (CHỈ bài này, không liên quan bài nào khác):
Nguồn: {article['source']}
Tiêu đề: {article['title']}
Nội dung (trích): {article.get('content_excerpt') or article['summary']}

YÊU CẦU: Viết đúng 1 đoạn tóm tắt bằng TIẾNG VIỆT, ĐÚNG 2 CÂU (không hơn) CHO RIÊNG bài viết này, ngắn gọn tối đa, không dài dòng:
- Câu 1: mô tả ngắn gọn sự kiện/nội dung chính của bài (fact, số liệu nếu bài có nêu) — TUYỆT ĐỐI KHÔNG bịa thêm thông tin ngoài nội dung đã cho, KHÔNG trộn với thông tin của bài viết khác.
- Câu 2: nêu ngắn gọn tin này tác động thế nào tới thị trường carbon/năng lượng châu Âu hoặc giá EUA — chỉ viết câu này nếu có cơ sở hợp lý từ nội dung bài; nếu bài không liên quan thì thay bằng 1 câu tóm tắt thêm fact khác của bài (vẫn giữ đúng 2 câu).
- Nếu bài viết bằng tiếng Anh hoặc ngôn ngữ khác: dịch ý sang tiếng Việt tự nhiên, không dịch máy móc từng từ.
- Văn phong khách quan. Mỗi câu ngắn, đi thẳng vào trọng tâm. KHÔNG dùng markdown (không **, không gạch đầu dòng).

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"summary": "..."}}"""


SECTION6_SUMMARY_CONCURRENCY = 4


async def _summarize_section6_articles(
    articles: List[Dict], target_date: str, concurrency: int = SECTION6_SUMMARY_CONCURRENCY
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

    async def _summarize_one(art: Dict) -> Dict:
        async with sem:
            await asyncio.sleep(1)  # giãn nhịp nhẹ trong mỗi slot, tránh dồn request tức thời
            raw = await _call_llm(
                _prompt_section6_summary(art, target_date),
                model=REPORT_MODEL_HAIKU,
                max_tokens=512,
            )
            parsed = _extract_json(raw) if raw else None
            summary = (parsed.get("summary") or "").strip() if parsed else ""

            if not summary:
                logger.warning("[REPORT] Mục 6: tóm tắt LLM thất bại cho bài %s, dùng fallback.", art["url"])
                summary = art["summary"]

            return {**art, "summary": summary}

    return list(await asyncio.gather(*[_summarize_one(art) for art in articles]))


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
            lines.append(
                f"  - {p['name']} ({p['code']}): {p['price']} | Δ ngày {p['dday']} | Δ tuần {p['dweek']}"
            )
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
        f"({change_30:+.1f}%); 30-ngày-cao {high_30:.2f}, 30-ngày-thấp {low_30:.2f}."
    )


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
    return (
        f"Biên độ phiên liền trước ({latest['date']}): mở {open_p:.2f} — cao {high:.2f} — thấp {low:.2f} "
        f"— đóng cửa {close:.2f} EUR/tCO2 (biên độ {high - low:.2f}, ~{range_pct:.1f}% so với giá đóng cửa)."
    )


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

    return (
        f"Kháng cự/điểm chốt lời kỹ thuật (đỉnh 30 phiên gần nhất): ~{resistance:.2f} EUR/tCO2 — "
        f"nếu giá phá vỡ mốc này, mục tiêu kỹ thuật tham khảo (đo biên độ dao động 30 phiên) "
        f"~{breakout_target:.2f} EUR/tCO2. "
        f"Hỗ trợ/điểm giảm kỹ thuật (đáy 30 phiên gần nhất): ~{support:.2f} EUR/tCO2 — vùng thường "
        f"xuất hiện lực mua bắt đáy về mặt kỹ thuật. "
        f"Giá đóng cửa gần nhất: {last_close:.2f} EUR/tCO2."
    )


# Ngưỡng phân loại % lệch khối lượng phiên liền trước so với TB — dùng để gắn
# nhãn factual (KHÔNG phải kết luận hướng giá, chỉ mô tả mức độ bất thường của
# khối lượng) cho LLM viết market_drivers ở Mục 2 dựa vào, thay vì tự đặt
# ngưỡng khác nhau giữa các lần sinh báo cáo.
EUA_VOLUME_SESSIONS_FOR_AVG = 20
EUA_VOLUME_SPIKE_PCT = 20.0
EUA_VOLUME_DROP_PCT = -20.0


def _eua_volume_summary(chart_data: List[Dict]) -> str:
    with_volume = [c for c in chart_data if c.get("volume") is not None]
    if not with_volume:
        return "Không có dữ liệu khối lượng giao dịch EUA cho phiên này."

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
    pct_diff = ((latest_volume - avg_volume) / avg_volume * 100) if avg_volume else 0

    if pct_diff >= EUA_VOLUME_SPIKE_PCT:
        level = "tăng đột biến"
    elif pct_diff <= EUA_VOLUME_DROP_PCT:
        level = "giảm mạnh"
    else:
        level = "ở mức bình thường"

    return (
        f"Khối lượng giao dịch EUA phiên liền trước ({latest['date']}): {latest_volume:,.0f} hợp đồng, "
        f"so với TB {len(prior)} phiên gần nhất ({avg_volume:,.0f} hợp đồng) — {level} ({pct_diff:+.1f}%). "
        f"Giá phiên đó {price_direction} so với giá mở cửa cùng phiên."
    )


# Hệ số quy đổi ICE Gasoil (niêm yết USD/tấn) sang USD/thùng để so được cùng đơn
# vị với Brent/WTI (USD/bbl) khi tính crack spread — ~7.45 thùng/tấn là hệ số quy
# đổi chuẩn ngành cho gasoil/diesel (EIA/API), không phải số chính xác tuyệt đối
# cho mọi lô hàng cụ thể — luôn gắn nhãn "ước tính" khi đưa vào prompt.
GASOIL_BBL_PER_TONNE = 7.45


def _gasoil_crack_spread_summary(prices: List[Dict]) -> Optional[str]:
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

    return (
        f"Gasoil crack spread vs Brent (ƯỚC TÍNH, quy đổi {GASOIL_BBL_PER_TONNE} thùng/tấn — "
        f"KHÔNG phải số liệu chính thức từ sàn): {spread_today:+.2f} USD/bbl "
        f"(Gasoil {gasoil_bbl:.2f} USD/bbl từ {gasoil['close']:.2f} USD/MT; Brent {brent['close']:.2f} USD/bbl)."
        f"{trend}"
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
    window_end = target + timedelta(days=6)
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
    window_end = target + timedelta(days=6)
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

    existing_keys = {(ev.get("date"), ev.get("event")) for ev in kept}
    for ev in recurring_events or []:
        key = (ev.get("date"), ev.get("event"))
        if key not in existing_keys:
            kept.append(dict(ev))
            existing_keys.add(key)

    kept.sort(key=lambda ev: ev.get("date") or "")
    return kept


# Múi giờ nguồn công bố gốc của 3 lịch định kỳ Mục 8 có DST (khác VN — VN
# không có DST) — quy đổi qua zoneinfo để tự động bù trừ giờ mùa hè/đông thay
# vì hardcode 1 offset cố định dễ sai lệch 1 tiếng tuỳ thời điểm trong năm.
_EIA_API_TZ = ZoneInfo("America/New_York")
_BAKER_HUGHES_TZ = ZoneInfo("America/Chicago")
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _compute_recurring_calendar_events(target_date_str: str) -> List[Dict]:
    """Tính SẴN bằng Python (không để LLM tự đoán) các sự kiện lịch thị trường
    có lịch công bố ĐỊNH KỲ, CỐ ĐỊNH theo tuần.

    CHỈ tính 2 sự kiện có thể fetch dữ liệu thực tế tự động:
      - EIA Weekly Petroleum Status Report: Thứ Tư 10:30 ET → quy đổi sang VN.
      - Baker Hughes Rig Count: Thứ Sáu 12:00 CT → quy đổi sang VN.

    ĐÃ LOẠI BỎ (không thể lấy dữ liệu thực):
      - API Crude Inventory: URL bị 404, yêu cầu membership — không có endpoint công khai.
      - Đấu giá EUA EEX: dữ liệu nằm trong JS widget, cần Playwright — không thể
        fetch bằng HTTP thuần. Lịch ngày Mon/Tue/Thu là ước tính không xác nhận.

    Quy đổi qua zoneinfo (tự bù DST của Mỹ) thay vì hardcode 1 offset cố định.
    """
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    window_end = target + timedelta(days=6)
    events: List[Dict] = []

    def _add_us_release(anchor_date: date, anchor_time: dtime, tz: ZoneInfo, label: str, impact: str) -> None:
        vn_dt = datetime.combine(anchor_date, anchor_time, tzinfo=tz).astimezone(_VN_TZ)
        if target <= vn_dt.date() <= window_end:
            events.append({
                "date": vn_dt.date().isoformat(),
                "datetime_vn": vn_dt.strftime("%d/%m"),
                "event": label,
                "impact": impact,
            })

    # Quét dư 2 ngày trước cửa sổ VN vì mốc gốc tính theo lịch Mỹ (ET/CT) —
    # quy đổi sang VN có thể lệch sang ngày hôm sau
    # (VD: EIA 10:30 ET mùa hè = 21:30 VN cùng ngày; mùa đông = 22:30 VN cùng ngày).
    scan_day = target - timedelta(days=2)
    while scan_day <= window_end:
        if scan_day.weekday() == 2:  # Thứ Tư (ET)
            _add_us_release(scan_day, dtime(10, 30), _EIA_API_TZ,
                             "Tồn kho dầu thô EIA (EIA Weekly Petroleum Status Report)", "Cao")
        if scan_day.weekday() == 4:  # Thứ Sáu (CT)
            _add_us_release(scan_day, dtime(12, 0), _BAKER_HUGHES_TZ,
                             "Số giàn khoan Baker Hughes (Baker Hughes Rig Count)", "Trung")
        scan_day += timedelta(days=1)

    events.sort(key=lambda e: e["date"])
    return events



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
    message để Claude tuân thủ tốt hơn. Đánh dấu cache_control (ephemeral) trên
    block system — đây là phần GIỐNG HỆT nhau giữa các lần chạy (framework/quy
    tắc không đổi theo ngày, chỉ user message chứa DATA mới đổi) nên tận dụng
    được prompt caching thật sự của Anthropic (KHÔNG tự động nếu chỉ truyền
    chuỗi thường — phải khai báo cache_control tường minh như dưới đây).
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
            if system:
                kwargs["system"] = [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ]
            response = await client.messages.create(**kwargs)
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
    news_text: str, prices_text: str, eua_trend: str, eua_session_range: str, target_date: str
) -> tuple[str, str]:
    system = f"Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ:
{prices_text}

BIÊN ĐỘ PHIÊN LIỀN TRƯỚC EUA (dùng ĐÚNG số này, KHÔNG tự ước):
{eua_session_range}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

TIN TỨC đã đánh số [N] (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, geopolitics, eu_policy, cbam) — CHỈ trích dẫn số có thật:
{news_text}

YÊU CẦU: Viết MỤC 1 — TÓM TẮT ĐIỀU HÀNH.
- Tối đa 5 bullet, mỗi cái ≤2 câu. Sắp xếp theo tác động EUA (cao→thấp).
- Bullet đầu: giá đóng cửa EUA + biên độ phiên (dùng ĐÚNG số "BIÊN ĐỘ PHIÊN" ở trên; nếu "Không có dữ liệu" → bỏ qua biên độ) + xu hướng 30 ngày.
- Mỗi bullet mở đầu bằng tag đậm (**EUA:**, **Chính sách:**...). Nêu tin chính sách/địa chính trị ảnh hưởng EUA.
- Không có tin nổi bật → 1 bullet duy nhất "Không có sự kiện nổi bật." (source_index: null).
- Mỗi bullet là object {{"text": "...", "source_index": N | null}}: "source_index" là số [N] có thật của bài tin tức LÀM CĂN CỨ CHÍNH cho bullet đó (BẮT BUỘC kèm nếu bullet dựa trên 1 tin cụ thể ở trên), hoặc null nếu bullet chỉ dựa trên DỮ LIỆU GIÁ hệ thống (không phải tin tức) — TUYỆT ĐỐI KHÔNG bịa số không có thật.

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"1": {{"title": "Tóm tắt điều hành", "bullets": [{{"text": "...", "source_index": null}}]}}}}"""
    return system, user


def _prompt_section2(
    eua_key_facts: str, prices_text: str, eua_trend: str, gasoil_crack_spread: str,
    news_text: str, target_date: str, topics_present: List[str],
    overrides: Optional[Dict[str, str]] = None,
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

TIN TỨC đã đánh số [N] (CHỈ trích dẫn số có thật):
{news_text}

YÊU CẦU: Viết "market_drivers" — BẢNG ĐỘNG LỰC THỊ TRƯỜNG.
Cấu trúc: {{"bullish": [...], "bearish": [...]}}. Mỗi phần tử gồm "tag", "text", "source_index":
- "tag": "FACT" hoặc "OPINION" — phân biệt theo ĐỘ CHẮC CHẮN:
  + "FACT" = thông tin ĐÃ XÁC NHẬN: (a) từ DỮ LIỆU GIÁ hệ thống, dựa trên Δ ngày + Δ tuần của instrument (source_index=null) hoặc (b) từ TIN TỨC đánh số nêu sự kiện/số liệu đã xảy ra (BẮT BUỘC kèm source_index).
  + "OPINION" = nhận định/suy luận/dự báo từ tin tức hoặc phân tích của bạn (NÊN kèm source_index nếu gắn bài cụ thể, null nếu suy luận chung).
- "text": ĐÚNG 1 câu TÓM TẮT NGẮN GỌN, ĐỊNH TÍNH về trạng thái/động lực của yếu tố này — TUYỆT ĐỐI KHÔNG nhắc lại giá đóng cửa hay %Δ ngày/Δ tuần bằng SỐ (các số này đã hiển thị sẵn ở "Bảng giá nhanh"/"Bảng tín hiệu nhanh" của báo cáo — lặp lại là thừa, không thêm thông tin). Thay vào đó mô tả ĐỊNH TÍNH vị thế/xu hướng — vd đang giữ vùng cao/thấp (so với biên độ 30 ngày), phá vỡ/chưa phá vỡ ngưỡng hỗ trợ-kháng cự, đi ngang tích lũy, biến động mạnh bất thường, thanh khoản tăng/giảm rõ rệt... — rồi nêu NGẮN GỌN nguyên nhân/sự kiện phía sau nếu có (vd "EUA đang giữ ở vùng cao, thanh khoản duy trì ổn định." hoặc "TTF suy yếu về vùng thấp nhiều tuần do nguồn cung LNG dồi dào."). Áp dụng ĐÚNG cách viết định tính này cho MỌI instrument trong mục này (EUA, TTF, than, dầu, điện Đức...), KHÔNG riêng EUA — KHÔNG giải thích/suy luận tác động lên EUA ở đây, KHÔNG viết chuỗi nhân quả hay kết luận hướng ảnh hưởng tới EUA (phần đó thuộc Mục 3).
- "source_index": số [N] có thật trong danh sách, hoặc null.
Xếp bullish/bearish theo ĐÚNG chuỗi nhân quả tới EUA (không theo chiều tăng/giảm bề ngoài của instrument) — chuỗi nhân quả CHỈ dùng để QUYẾT ĐỊNH xếp vào bullish hay bearish, KHÔNG viết ra trong "text". CHỈ đưa yếu tố có dữ liệu/tin hỗ trợ, không bịa thêm.
- KHỐI LƯỢNG GIAO DỊCH EUA: dựa vào khối lượng phiên liền trước so với TB các phiên gần nhất đã nêu trong "SỐ LIỆU EUA" ở trên (KHÔNG tự bịa số) để BẮT BUỘC thêm 1 mục "FACT" riêng mô tả ĐỊNH TÍNH tín hiệu khối lượng vào bullish/bearish (KHÔNG nêu số khối lượng cụ thể trong "text"), theo đúng logic "khối lượng xác nhận xu hướng giá":
  + Khối lượng tăng đột biến CÙNG chiều với giá tăng/giảm phiên đó → tín hiệu xác nhận lực mua/bán mạnh, xếp cùng chiều bullish/bearish tương ứng (vd "Khối lượng giao dịch tăng vọt, xác nhận lực mua mạnh.").
  + Khối lượng giảm mạnh trong khi giá vẫn biến động mạnh, hoặc khối lượng tăng đột biến nhưng giá gần như đi ngang → tín hiệu YẾU/thiếu xác nhận, ghi rõ là "OPINION" và nêu rủi ro đảo chiều/thiếu động lực thay vì kết luận dứt khoát.
  + Khối lượng ở mức bình thường → KHÔNG cần thêm mục riêng cho khối lượng (chỉ dùng khi có bất thường thực sự).

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"2": {{"market_drivers": {{"bullish": [{{"tag": "FACT", "text": "...", "source_index": null}}], "bearish": [{{"tag": "FACT", "text": "...", "source_index": null}}]}}}}}}"""
    return system, user


def _prompt_key_developments(
    news_text: str, target_date: str, topics_present: List[str],
    overrides: Optional[Dict[str, str]] = None,
) -> tuple[str, str]:
    """ "Diễn biến chính" — hiển thị ngay dưới Bảng giá nhanh: CHỈ các SỰ KIỆN TIN TỨC
    nổi bật có tác động tới giá/cung/cầu của các hợp đồng theo dõi (EUA + năng lượng,
    trực tiếp hoặc gián tiếp) — KHÔNG phải diễn
    biến giá tăng/giảm của các hợp đồng (phần đó đã có ở Bảng giá nhanh). Cố ý KHÔNG
    đưa dữ liệu giá vào prompt để LLM không viết lại biến động giá. Mỗi tin BẮT BUỘC
    có nguồn bài viết (source_index → backend map sang tên/URL thật)."""
    framework = _eua_framework(topics_present, full=False, overrides=overrides)
    system = f"Bạn là chuyên gia phân tích thị trường carbon châu Âu.\n{CONCISENESS_RULE}\n\n{framework}"
    user = f"""Ngày báo cáo: {target_date}

TIN TỨC đã đánh số [N] — CHỈ trích dẫn số có thật:
{news_text}

YÊU CẦU: Viết "key_developments" — DIỄN BIẾN CHÍNH: các tin tức nổi bật trong danh sách trên CÓ TÁC ĐỘNG ĐẾN GIÁ của các hợp đồng đang theo dõi (EUA, TTF, điện Đức, than, dầu, gasoil...) — trực tiếp hay gián tiếp đều được, miễn bài nêu được sự kiện/nguyên nhân cụ thể ảnh hưởng tới cung, cầu hoặc giá của ít nhất 1 hợp đồng. Gồm: chính sách EU ETS/MSR/cap/đấu giá/CBAM, số liệu phát thải, động thái thị trường carbon, gián đoạn nguồn cung năng lượng, địa chính trị, thời tiết, tồn kho, OPEC+, sản lượng điện tái tạo/hydrogen, dự báo của tổ chức... Không cần bắt buộc tác động thẳng lên EUA.

QUY TẮC BẮT BUỘC — ĐÂY LÀ MỤC TIN TỨC, KHÔNG PHẢI MỤC GIÁ:
- TUYỆT ĐỐI KHÔNG viết diễn biến GIÁ (không "giá tăng/giảm X%", giá đóng cửa, hỗ trợ/kháng cự, biến động phiên/tuần) — phần đó đã có ở Bảng giá nhanh. Số liệu của chính sự kiện (khối lượng đấu giá, mức cắt phân bổ...) được phép.
- BỎ các bài chỉ là bản tin thị trường/tổng hợp giá không nêu sự kiện cụ thể.
- Từ 3 đến 6 mục (ít hơn nếu thật sự không đủ tin), xếp theo mức độ tác động mạnh → yếu. Nhiều bài cùng 1 sự kiện → gộp thành 1 mục. Bỏ tin không nêu được tác động nào tới giá/cung/cầu của hợp đồng nào.
- Mỗi mục là object {{"title": "...", "summary": "...", "impact": "tăng" | "giảm" | "trung lập", "source_index": N}}:
  + "title": tiêu đề bài báo (dịch/rút gọn sang tiếng Việt nếu bài tiếng Anh, giữ đúng ý, không thêm thắt).
  + "summary": 1–2 câu NGẮN GỌN: nêu nội dung chính của tin, rồi tác động tới giá/cung/cầu của hợp đồng liên quan, nêu rõ hợp đồng nào (vd "... → hỗ trợ giá TTF, gián tiếp thúc đẩy nhu cầu EUA"). KHÔNG chép lại tiêu đề, KHÔNG ghi nguồn/ngày trong câu (hệ thống tự thêm).
  + "impact": chiều tác động lên GIÁ của hợp đồng chính bị ảnh hưởng (theo đúng chuỗi nhân quả ở KHUNG PHÂN TÍCH khi liên quan EUA) — "tăng" = hỗ trợ giá, "giảm" = gây áp lực giảm giá, "trung lập" = chưa rõ chiều.
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


def _prompt_section3(
    news_text: str, prices_text: str, eua_trend: str, eua_session_range: str,
    gasoil_crack_spread: str, target_date: str, topics_present: List[str],
    overrides: Optional[Dict[str, str]] = None,
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

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

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
        (ii) GAS/THAN → áp dụng FUEL_SWITCHING: đây là cơ chế GIÁ TƯƠNG ĐỐI, không phải giá tuyệt đối của riêng 1 nhiên liệu — Gas tăng (than không đổi/tăng ít hơn) → than cạnh tranh hơn → dispatch dịch sang than → phát thải & cầu EUA tăng; Gas giảm HOẶC Than tăng (2 tín hiệu CÙNG CHIỀU, không phải trái chiều) → gas cạnh tranh hơn → dispatch dịch sang gas → phát thải & cầu EUA giảm. TUYỆT ĐỐI KHÔNG suy luận "than tăng → đốt than nhiều hơn" (sai chiều kinh tế — giá than tăng một mình làm than kém cạnh tranh hơn, không phải được đốt nhiều hơn); chỉ kết luận than được đốt nhiều hơn khi tin tức xác nhận rõ nguyên nhân khác (gas gián đoạn, RES thấp, sự cố hạ tầng) khiến cầu than tăng kéo giá than tăng theo, không suy đoán từ riêng chiều giá than.
        (iii) ĐIỆN ĐỨC (DEBY1) → áp dụng ĐÚNG nhánh (a) của POWER_EUA_TWO_WAY: nếu DEBY1 biến động đồng pha với gas/than → kết luận điện tăng do chi phí nhiên liệu cao hơn, utility hedge thêm EUA tương ứng sản lượng đã bán — kênh hedge/chi phí này TÁCH BIỆT với việc thực tế đốt nhiên liệu nào, KHÔNG gộp chung "than+điện cùng tăng" thành kết luận "đốt than nhiều hơn". CHỈ được quy nguyên nhân "RES thấp" khi có dữ liệu/tin tức THỰC SỰ xác nhận RES thấp trong ngày — nếu KHÔNG có dữ liệu RES, PHẢI nêu rõ "không có dữ liệu RES trong ngày để xác nhận nhánh này" thay vì mặc định suy diễn.
        (iv) DẦU/GASOIL → áp dụng OIL_GASOIL: đối chiếu Δ ngày/Δ tuần của Brent/WTI theo đúng bước (i) trước khi kết luận fuel switching qua kênh dầu-khí (mâu thuẫn giữa 2 khung thời gian → tín hiệu ngắn hạn chưa đủ mạnh, cần thêm phiên xác nhận, chưa kết luận). Gasoil crack spread thu hẹp/mở rộng → nêu tác động lên cầu diesel/công nghiệp nặng và cầu EUA từ kênh công nghiệp đó, tách riêng khỏi kết luận fuel switching qua gas.
        (v) ĐỊA CHÍNH TRỊ — có 2 kênh TÁCH BIỆT trong GEOPOLITICS_SUPPLY_CHAIN, phải xác định đúng kênh trước khi suy luận:
            - Xung đột/sanctions/OPEC+/gián đoạn năng lượng vật lý (Trung Đông/Nga/Iran/Venezuela, eo biển vận chuyển...) → áp dụng ĐÚNG nhánh (a), BẮT BUỘC đi qua đủ bước cung → giá gas/dầu trước khi vào kết luận EUA: nếu Δ tuần của gas/dầu CHƯA xác nhận xu hướng tăng do sự kiện đó (vd giá dầu tuần vẫn giảm), CHỈ được ghi nhận đây là "rủi ro cung/giá tiềm ẩn", TUYỆT ĐỐI KHÔNG kết luận thẳng sự kiện địa chính trị đã đẩy tăng cầu EUA khi thiếu xác nhận giá.
            - Thay đổi chính phủ/định hướng chính sách khí hậu/quyết định thương mại quốc tế (KHÔNG phải gián đoạn năng lượng vật lý) → áp dụng ĐÚNG nhánh (b), đi qua kênh kỳ vọng thị trường/hoạt động công nghiệp, KHÔNG bắt buộc đi qua bước giá gas/dầu như nhánh (a) — CHỈ nêu khi tin tức nêu rõ sự kiện và hướng tác động cụ thể, không suy diễn chung chung.
      GỘP GAS–THAN–ĐIỆN ĐỨC THÀNH 1 GẠCH DUY NHẤT: khi Gas (TTF), Than (Newcastle/API2) và Điện Đức (DEBY1) CÙNG có ghi chú ở "instrument_notes" Nhóm 1 và cơ chế fuel-switching/POWER_EUA_TWO_WAY thực sự áp dụng được (theo bước (ii)/(iii) ở trên) — KHÔNG viết 3 gạch đầu dòng riêng cho từng mã, mà GỘP thành ĐÚNG 1 gạch đầu dòng duy nhất "**Fuel switching (Gas–Than–Điện Đức):**", nêu Δ ngày/Δ tuần của cả 3 mã thật ngắn gọn (lấy từ "DỮ LIỆU GIÁ") rồi đi thẳng tới 1 kết luận chiều dispatch và tác động EUA — không diễn giải lại cơ chế giá tương đối. Mã nào không có tin/giá đáng chú ý thì bỏ qua khỏi gạch này; nếu chỉ 1-2 trong 3 mã có mặt thì viết riêng như các mã khác trong Nhóm 1, không ép gộp.
      QUY TẮC NHÓM: CHỈ phân tích nhóm nào ĐÃ có ghi chú ở "instrument_notes" (nhóm không có ghi chú thì cũng không có gì để phân tích ở đây — bỏ qua tương ứng). SAU KHI áp BỘ LỌC BẮT BUỘC ở trên, nếu 1 nhóm không còn yếu tố nào có tác động thực → BỎ QUA HOÀN TOÀN nhóm đó ở "Phân tích" (không viết dòng "Tên nhóm:" cho nhóm đó), kể cả khi nhóm đó có ghi chú ở "instrument_notes".
      THỨ TỰ CỐ ĐỊNH: PHẢI theo ĐÚNG thứ tự NHÓM 1 (Năng lượng & nhiên liệu hóa thạch) → NHÓM 2 (Hạn ngạch & tín chỉ carbon) → NHÓM 3 (Chính sách) — TUYỆT ĐỐI KHÔNG đảo thứ tự theo mức độ quan trọng hay ưu tiên nhóm nào lên trước.
      MỖI NHÓM PHẢI MỞ ĐẦU BẰNG KẾT LUẬN NGAY TRÊN DÒNG TIÊU ĐỀ (KHÔNG còn chốt kết luận ở cuối nhóm như trước): dòng ĐẦU TIÊN của 1 nhóm viết LIỀN 1 dòng theo cú pháp "Tên nhóm: <kết luận>" — vd "Năng lượng & nhiên liệu hóa thạch: Tạo áp lực tăng rõ rệt lên EUA." — <kết luận> là ĐÚNG 1 câu ngắn nêu rõ nhóm này đẩy EUA tăng/giảm/trung lập, viết tiếp thẳng vào vị ngữ ngay sau dấu ":" (TUYỆT ĐỐI KHÔNG dùng cụm dẫn "Nhóm này..."/tag "**Kết luận:**" — vị trí đầu dòng đã tự nói lên đây là kết luận), chỉ chốt chiều, KHÔNG lặp lại số liệu hay diễn giải lại cơ chế đã dùng để suy ra kết luận đó. "Trung lập" CHỈ dùng khi nhóm có ≥2 yếu tố THỰC SỰ có tác động (đã qua BỘ LỌC BẮT BUỘC) nhưng triệt tiêu lẫn nhau về chiều. Các gạch đầu dòng SỐ LIỆU/LUẬN CỨ chi tiết (theo đúng các quy tắc suy luận ở trên) viết NGAY SAU dòng tiêu đề-kết luận này, xuống dòng thật ("- ..."); kết luận của nhóm CHỈ xuất hiện ĐÚNG 1 LẦN DUY NHẤT, ngay trên dòng tiêu đề đầu nhóm — TUYỆT ĐỐI KHÔNG lặp lại kết luận đó thêm 1 lần nữa ở cuối nhóm.
      KẾT LUẬN CHUNG CHO CẢ MỤC "PHÂN TÍCH": sau khi trình bày xong các nhóm áp dụng được ở trên, LUÔN kết thúc "content" bằng 1 ĐOẠN RIÊNG cuối cùng, BẮT ĐẦU bằng tag in đậm "**Tổng hợp:**" và NGAY SAU tag là ĐÚNG 1 nhãn xu hướng giá EUA trong ngoặc vuông, chọn 1 trong 3: "[TÍCH CỰC]" (giá EUA tăng/áp lực tăng chiếm ưu thế), "[TRUNG LẬP]" (đi ngang, biến động không đáng kể, hoặc tín hiệu hỗn hợp), "[TIÊU CỰC]" (giá EUA giảm/áp lực giảm chiếm ưu thế) — vd "**Tổng hợp:** [TIÊU CỰC] EUA giảm ...". Nhãn này quyết định màu khung "Nhận định" ở đầu báo cáo (xanh/vàng/đỏ) nên PHẢI khớp đúng chiều của kết luận, viết đúng chính tả, không thêm ký tự khác trong ngoặc. Phần nội dung sau nhãn DÀI ĐÚNG 40–45 CHỮ (đếm theo số từ/tiếng cách nhau bởi khoảng trắng, KHÔNG tính nhãn) — đây là nhận định CÔ ĐỌNG, TỔNG HỢP NHẤT về giá EUA, đặt ở vị trí nổi bật nhất đầu báo cáo: đối chiếu ngắn Δ ngày/Δ tuần của chính EUA (từ "SỐ LIỆU THẬT VỀ EUA"/"XU HƯỚNG EUA 30 NGÀY" ở trên) — cùng chiều → xu hướng nhất quán, độ tin cậy cao; trái chiều → tín hiệu ngắn hạn/nhiễu, cần thận trọng — rồi TỔNG HỢP các kết luận đã nêu ở dòng tiêu đề của từng nhóm ở trên thành 1 kết luận DỨT KHOÁT duy nhất về hướng đi EUA, KHÔNG lặp lại chi tiết/số liệu đã nêu ở các nhóm. Áp dụng quy tắc: kết luận chiều giá chỉ khi ≥2 yếu tố/nhóm cùng hướng; nếu mâu thuẫn → nhãn "[TRUNG LẬP]", ghi "tín hiệu hỗn hợp" + nêu ngắn 2 chiều + điều kiện kích hoạt mỗi chiều (vẫn gói gọn trong 40–45 chữ). Đây là câu quan trọng nhất Mục 3 — PHẢI dứt khoát, không mơ hồ.
      ĐỊNH DẠNG: "content" là 1 chuỗi string. Dòng ĐẦU TIÊN của mỗi nhóm PHẢI là dòng tiêu đề-kết luận gộp, viết LIỀN trên CÙNG 1 dòng theo cú pháp "Tên nhóm: <kết luận>" (dùng đúng tên "Năng lượng & nhiên liệu hóa thạch:", "Hạn ngạch & tín chỉ carbon:", "Chính sách:" — TUYỆT ĐỐI KHÔNG tách tên nhóm thành 1 dòng trơ trọi rồi để kết luận ở dòng/vị trí khác). NẾU 1 nhóm có nhiều yếu tố/chuỗi tác động → tách mỗi yếu tố thành 1 gạch đầu dòng xuống dòng thật (\\n"- ...") NGAY SAU dòng tiêu đề-kết luận đó để dễ nhìn; TUYỆT ĐỐI KHÔNG để lại các dấu "-" trống thừa thãi giữa các ý; giữa các NHÓM luôn xuống dòng thật (\\n); dòng "**Tổng hợp:**" luôn là dòng CUỐI CÙNG của "content" — TUYỆT ĐỐI KHÔNG viết liền thành 1 đoạn văn dài.
      Nếu "instrument_notes" rỗng, HOẶC có ghi chú nhưng SAU KHI áp BỘ LỌC BẮT BUỘC không nhóm nào còn yếu tố có tác động thực: "content" = "Không có thông tin mới liên quan trực tiếp đến giá EUA." (bỏ qua dòng "**Tổng hợp:**" trong trường hợp này).
   2. heading="Quan điểm thị trường" (TÙY CHỌN) — CHỈ đưa object này vào mảng "analysis_blocks" khi TIN TỨC ở trên THỰC SỰ có nêu quan điểm/nhận định cụ thể từ nguồn xác định (nhà phân tích, tổ chức, báo cáo). Khi có, "content" PHẢI nêu đủ: (1) quan điểm consensus — đa số thị trường/nhà phân tích đang nghĩ gì; (2) quan điểm contrarian khác biệt ra sao (nếu có), kèm tên nguồn cụ thể; (3) luận điểm/bằng chứng cụ thể mà nguồn đưa ra để bảo vệ quan điểm trái chiều đó; (4) điều kiện/kịch bản nào sẽ khiến quan điểm contrarian này đúng thay vì consensus. Nếu chỉ có consensus view (không có contrarian nào trong tin tức), bỏ qua (2)-(4) và chỉ nêu (1). "Đủ ý" nghĩa là đủ các nội dung áp dụng được ở trên, KHÔNG phải đủ số câu — vẫn theo đúng QUY TẮC ĐỘ DÀI CHUNG (tối đa 1–2 câu/ý). NẾU KHÔNG CÓ tin nào nêu quan điểm thị trường cụ thể: KHÔNG thêm object heading="Quan điểm thị trường" vào mảng — bỏ qua hoàn toàn (không viết "Không có quan điểm thị trường cụ thể." nữa).
      KẾT LUẬN BẮT BUỘC CHO BLOCK NÀY (khi block này được đưa vào): sau khi trình bày xong (1)-(4) áp dụng được, LUÔN kết thúc "content" bằng 1 câu RIÊNG cuối cùng, BẮT ĐẦU bằng tag in đậm "**Kết luận:**", chốt quan điểm nào (consensus hay contrarian) ĐÁNG TIN HƠN dựa trên chính bằng chứng/luận điểm đã nêu ở (2)-(3) và đối chiếu với chiều tác động đã kết luận ở block "Phân tích" (dòng "**Tổng hợp:**") — nếu 2 bên khớp chiều → nêu rõ đây là yếu tố củng cố thêm cho kết luận đó; nếu lệch chiều → nêu rõ đây là rủi ro/lý do cần thận trọng với kết luận đó. Nếu chỉ có consensus view (không có contrarian), câu kết luận chỉ cần nêu mức độ đồng thuận đó có khớp hay không với "Tổng hợp" ở "Phân tích". Câu này KHÔNG lặp lại số liệu/luận điểm đã nêu ở (1)-(4), chỉ chốt kết luận. Đây là câu bắt buộc, không được bỏ qua khi block "Quan điểm thị trường" đã được đưa vào mảng.
   3. heading="Cần theo dõi" — KHÔNG PHẢI danh sách lịch sự kiện chung chung. Đây là các WATCHPOINT rút ra TRỰC TIẾP từ chính số liệu giá, "instrument_notes" và "Phân tích" phía trên — ưu tiên đúng những điểm ở "Phân tích" CHƯA đủ cơ sở kết luận dứt khoát (vd Δ ngày/Δ tuần mâu thuẫn cần thêm phiên xác nhận, sự kiện địa chính trị/chính sách giá CHƯA kịp phản ánh, nhánh cơ chế còn thiếu dữ liệu để xác nhận...). Mục đích: cho người đọc biết CHÍNH XÁC cần nhìn vào đâu ở phiên/tin tiếp theo và mỗi khả năng xảy ra sẽ kéo cung/cầu, giá EUA theo hướng nào.
      ĐỘ DÀI RIÊNG CHO HEADING NÀY: KHÔNG áp dụng giới hạn 1–2 câu — mỗi watchpoint cần đủ chỗ nêu 2 kịch bản trái chiều nên có thể dài hơn, nhưng vẫn phải súc tích, thẳng vào thông tin, không thêm câu đệm.
      MỖI WATCHPOINT PHẢI CÓ ĐỦ 3 PHẦN:
        (a) CẦN THEO DÕI GÌ — nêu đích danh mã giá/chỉ số/tin tức cụ thể đã xuất hiện ở trên (vd "Δ tuần của Brent/WTI ở phiên tới", "diễn biến đàm phán sanctions Iran/Venezuela", "dữ liệu RES/gió-mặt trời ngày mai", "kết quả đấu giá EUA kỳ tới"...) — kèm ngày giờ Việt Nam cụ thể CHỈ khi tin tức/lịch công bố có nêu rõ, KHÔNG tự bịa thời điểm.
        (b) NẾU XẢY RA THEO HƯỚNG 1 — bắt đầu bằng "Nếu [diễn biến cụ thể theo hướng 1]..." → nêu rõ tác động tới cung/cầu và chiều giá EUA tương ứng.
        (c) NẾU XẢY RA THEO HƯỚNG NGƯỢC LẠI (hoặc không xảy ra) — bắt đầu bằng "Nếu [diễn biến ngược lại/không xác nhận]..." → nêu rõ tác động khác hoặc EUA đi ngang/giữ tín hiệu hỗn hợp.
      MỖI watchpoint là 1 DÒNG RIÊNG, đánh số "1.", "2.", "3."... — PHẢI chèn ký tự xuống dòng thật (\\n) giữa các dòng, TUYỆT ĐỐI KHÔNG viết liền các watchpoint thành 1 đoạn văn dài không xuống dòng. Số lượng watchpoint bám theo đúng số điểm còn chưa chắc chắn thực sự tồn tại ở "Phân tích"/tin tức — KHÔNG bịa thêm watchpoint không có căn cứ chỉ để đủ số lượng.

C. "trading_scenarios": mảng ĐÚNG 3 kịch bản — BẮT BUỘC đủ cả 3 horizon "ngắn hạn", "trung hạn", "dài hạn". Đây là phần người đọc xem để RA QUYẾT ĐỊNH nên phải NGẮN, TRỰC DIỆN, đi thẳng vào giá trị: chỉ nêu điều kiện, mức giá và hành động — KHÔNG giải thích cơ chế, KHÔNG lặp lại mục B, KHÔNG câu đệm/mở đầu/sáo rỗng. Mỗi trường là 1 mệnh đề cô đọng, ưu tiên số liệu cụ thể hơn lời văn. Mỗi kịch bản gồm:
   - "horizon": "ngắn hạn" (1–2 tuần) / "trung hạn" (1–3 tháng) / "dài hạn" (>3 tháng)
   - "probability": xác suất kịch bản này xảy ra — CHỈ 1 trong 3 giá trị "Cao" / "Trung bình" / "Thấp", dựa trên driver ở mục B đã được dữ liệu/tin tức xác nhận rõ (probability cao hơn) hay mới chỉ là suy đoán/tin đồn (probability thấp hơn).
   - "direction": ĐÚNG 1 trong 3 giá trị "tăng" / "giảm" / "đi ngang" — chiều giá EUA của RIÊNG kịch bản này, phải khớp ĐÚNG chiều với "trading_strategy" bên dưới (dùng để hiển thị mũi tên trên giao diện).
   - "condition" (1 câu, tối đa ~20 từ): "Nếu [X]..." — chỉ nêu trigger cụ thể (số liệu/ngưỡng/ngày) gắn với 1 driver ở mục B, không diễn giải thêm.
   - "price_zone" (tối đa ~15 từ, chỉ số liệu): vùng hỗ trợ/kháng cự EUA bằng EUR/tCO2, neo đúng 30-ngày-cao/thấp, giá đóng cửa, biên độ phiên đã cung cấp — TUYỆT ĐỐI KHÔNG bịa số.
   - "key_risk" (1 câu, tối đa ~20 từ): sự kiện/ngưỡng cụ thể làm kịch bản sai và đẩy giá lệch hướng nào — không viết chung chung.
   - "trading_strategy": đúng 3 dòng, mỗi dòng tối đa ~15 từ, bắt đầu bằng tag in đậm rồi xuống dòng thật (\\n) giữa các dòng:
     + "**Entry:**" mức giá/điều kiện vào lệnh, đúng chiều "direction", neo vào "price_zone".
     + "**Mục tiêu:**" mức giá chốt lời kế tiếp, risk/reward hợp lý so với Entry.
     + "**Quản trị rủi ro:**" mức giá cắt lỗ khi kịch bản bị vô hiệu, gắn với "key_risk".
     Chỉ dùng mức giá có căn cứ từ dữ liệu đã cho (giao diện đã có lưu ý tham khảo đi kèm) — không thêm giải thích sau mỗi dòng.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"3": {{"title": "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA", "instrument_notes": {{"EUA": "..."}}, "analysis_blocks": [{{"heading": "...", "content": "..."}}], "trading_scenarios": [{{"horizon": "...", "probability": "Cao/Trung bình/Thấp", "direction": "tăng/giảm/đi ngang", "condition": "...", "price_zone": "...", "key_risk": "...", "trading_strategy": "..."}}]}}}}"""
    return system, user


def _prompt_section4(news_text: str, target_date: str) -> tuple[str, str]:
    system = f"Bạn là chuyên gia phân tích thị trường carbon tự nguyện và CBAM.\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}

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
    window_end = (datetime.strptime(target_date, "%Y-%m-%d").date() + timedelta(days=6)).isoformat()
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
"events" CHỈ được gồm sự kiện thuộc ĐÚNG 3 NHÓM sau — KHÔNG thêm bất kỳ sự kiện nào ngoài 3 nhóm này dù tin tức có nhắc tới:
  1. Tồn kho dầu EIA — lấy NGUYÊN từ "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên (đã có kết quả thực tế nếu sự kiện đã xảy ra), không tự tính lại, không tự đoán kết quả nếu outcome đã được điền sẵn.
  2. Rig count Baker Hughes — lấy NGUYÊN từ "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên (đã có kết quả thực tế nếu sự kiện đã xảy ra), không tự tính lại.
  3. Họp chính sách (ECB / EU Climate Action / FOMC / chính sách EU ETS liên quan) — CHỈ thêm nếu TIN TỨC LIÊN QUAN ở trên xác nhận rõ ngày họp cụ thể; KHÔNG có xác nhận thì KHÔNG thêm (không đoán ngày).
LƯU Ý: API Crude Inventory và Đấu giá EUA EEX đã bị loại bỏ khỏi danh sách theo dõi định kỳ do không có nguồn dữ liệu công khai lấy được tự động. KHÔNG thêm các sự kiện này vào "events" dù báo cáo trước có liệt kê.
Với sự kiện bạn TỰ thêm ở nhóm 3: "date" (YYYY-MM-DD), "datetime_vn" (CHỈ "DD/MM", KHÔNG kèm năm, TUYỆT ĐỐI KHÔNG kèm giờ dù tin tức có nêu rõ giờ), "event", "impact" (Cao/Trung/Thấp).

CẬP NHẬT KẾT QUẢ SỰ KIỆN KỲ TRƯỚC (bắt buộc, chỉ áp dụng cho danh sách "SỰ KIỆN TỪ BÁO CÁO TRƯỚC" ở trên):
- Nhóm "Đã diễn ra hoặc diễn ra đúng hôm nay, CẦN cập nhật kết quả": với MỖI sự kiện EIA/Baker Hughes — nếu "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên đã kèm "outcome" thực tế (được tính sẵn bằng dữ liệu fetch trực tiếp), PHẢI dùng đúng outcome đó, KHÔNG được tự viết outcome khác; với sự kiện khác (nhóm 3) — CHỈ điền outcome nếu TIN TỨC LIÊN QUAN xác nhận rõ; nếu không xác nhận, ghi "Chưa có thông tin kết quả xác nhận" — TUYỆT ĐỐI KHÔNG tự bịa số liệu. Giữ nguyên "date" gốc.
- Nhóm "Vẫn còn sắp tới trong cửa sổ 7 ngày": liệt kê lại bình thường, giữ nguyên "date", KHÔNG cần field "outcome" — tránh liệt kê trùng nếu sự kiện này đã nằm trong "SỰ KIỆN ĐỊNH KỲ ĐÃ TÍNH SẴN" ở trên.
- Sự kiện mới của kỳ 7 ngày tới tính từ {target_date} → liệt kê bình thường, KHÔNG cần field "outcome".
- KHÔNG đưa vào "events" bất kỳ sự kiện nào có "date" nằm ngoài khoảng {window_start} → {window_end}, TRỪ các sự kiện thuộc nhóm "Đã diễn ra, CẦN cập nhật kết quả".

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"8": {{"title": "Lịch sự kiện 7 ngày tới", "events": [{{"date": "YYYY-MM-DD", "datetime_vn": "DD/MM", "event": "...", "impact": "Cao/Trung/Thấp", "outcome": "... (optional, chỉ khi sự kiện đã qua)"}}]}}}}"""
    return system, user




def _prompt_biz_recommendation(
    news_text: str, prices_text: str, eua_trend: str, target_date: str,
    tracking_text: str = "Không có.", valid_codes: str = "", dismissed_text: str = "Không có.",
) -> tuple[str, str]:
    system = f"Bạn là chuyên gia tư vấn kinh doanh về carbon và năng lượng cho doanh nghiệp Việt Nam (SIM).\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ:
{prices_text}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

TIN TỨC ĐA CHIỀU:
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
B. "long_term": mảng object, gợi ý dài hạn rút ra từ cơ hội phân tích (chính sách CBAM, VCM, chuyển dịch năng lượng...). Mỗi object gồm ĐÚNG 3 trường, MỖI TRƯỜNG TỐI ĐA 1 CÂU NGẮN GỌN:
   - "opportunity": cơ hội/xu hướng dài hạn cụ thể đã xác định được.
   - "solution": giải pháp/hướng đi đề xuất cho SIM để tận dụng cơ hội đó.
   - "expectation": kỳ vọng/kết quả nếu triển khai giải pháp này.

QUY TẮC SỐ LƯỢNG: chỉ đưa vào gợi ý THỰC SỰ có căn cứ từ tin tức/dữ liệu ở trên — TUYỆT ĐỐI KHÔNG bịa thêm cho đủ số dòng. Nếu 1 bảng không có gợi ý nào đủ căn cứ, để mảng đó rỗng. TỐI ĐA 3 gợi ý MỖI bảng (short_term và long_term riêng biệt) — nếu có nhiều hơn 3 gợi ý đủ căn cứ, CHỈ giữ lại ĐÚNG 3 gợi ý TRỌNG TÂM/có căn cứ mạnh nhất, bỏ phần còn lại (KHÔNG cố nhồi hết vào 1 bảng, sẽ làm output quá dài và bị cắt).
Lưu ý: KHÔNG dùng câu lệnh mua/bán tài chính trực tiếp.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"biz": {{"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": [{{"trigger": "Nếu ...", "trigger_rule": null, "action": "...", "reason": "..."}}], "long_term": [{{"opportunity": "...", "solution": "...", "expectation": "..."}}]}}}}"""
    return system, user


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
) -> Dict[int, Dict[str, Optional[str]]]:
    """Hỏi LLM: với từng gợi ý cũ CHƯA kích hoạt, diễn biến thực tế hôm nay (giá + tin
    tức) có đi NGƯỢC với giả định/kỳ vọng làm nền cho đề xuất không (vd đề xuất mua
    dự phòng vì kỳ vọng giá năng lượng tăng, nhưng giá giảm mạnh do nguồn cung phục
    hồi). Chỉ chấp nhận khi bằng chứng cụ thể: bài báo có thật (source_index → tên/URL
    backend map) HOẶC số liệu giá trong "DỮ LIỆU GIÁ" (source_index null → nguồn ghi
    "Giá chốt phiên (Barchart)"). Lỗi LLM/không đủ căn cứ → {} (giữ nguyên trạng thái chờ)."""
    if not candidates:
        return {}
    system = f"Bạn là trợ lý theo dõi các đề xuất kinh doanh cho doanh nghiệp SIM.\n{CONCISENESS_RULE}"
    user = f"""Ngày dữ liệu: {target_date}

CÁC GỢI Ý ĐÃ ĐỀ XUẤT TRƯỚC ĐÂY (tình huống kích hoạt CHƯA xảy ra):
{biz_memory.describe_for_prompt(candidates)}

DỮ LIỆU GIÁ PHIÊN VỪA QUA:
{prices_text}

TIN TỨC TRONG NGÀY đã đánh số [N] — CHỈ trích dẫn số có thật:
{news_text if index_lookup else "Không có tin tức."}

YÊU CẦU: Với TỪNG gợi ý [S<id>], xác định diễn biến thực tế hôm nay có đi NGƯỢC CHIỀU với giả định/kỳ vọng làm nền cho đề xuất hay không (tức đề xuất không còn phù hợp: thị trường/chính sách diễn ra trái với điều đề xuất dựa vào).
- "contradicted": true CHỈ khi có dữ kiện RÕ RÀNG, trực tiếp trái với giả định của đề xuất — từ bài báo (BẮT BUỘC "source_index" là số [N] có thật) HOẶC từ số liệu trong DỮ LIỆU GIÁ (khi đó "source_index": null và "evidence" nêu mã + biến động cụ thể). KHÔNG suy diễn; chỉ chưa xảy ra/chưa kích hoạt KHÔNG phải là ngược chiều; biến động nhỏ, không rõ hướng → false.
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
        elif chk.get("source_index") is None:
            result[sid] = {"evidence": evidence, "source_name": "Giá chốt phiên (Barchart)", "source_url": None}
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
    # ── 1. Thu thập dữ liệu ──────────────────────────────────────────
    # 1 query duy nhất — override admin đã custom cho khung phân tích EUA (nếu
    # có), dùng cho cả Mục 2/3/5 bên dưới (xem services/eua_framework_admin.py).
    eua_framework_overrides = await get_overrides_map(session)
    prices, max_price_date = await get_prices_for_report(session, target_date)
    chart_data = await get_historical_ohlc_for_report(session, "EUA", target_date)
    news_by_topic, sources = await get_news_for_report(session, target_date)

    prices_text = _summarize_prices(prices)
    eua_trend = _eua_trend_summary(chart_data)
    eua_session_range = _eua_session_range_summary(chart_data)
    eua_volume = _eua_volume_summary(chart_data)
    gasoil_crack_spread = _gasoil_crack_spread_summary(prices) or (
        "Không có dữ liệu Gasoil hoặc Brent trong phiên này — không tính được crack spread."
    )
    pending_outcome_events, still_upcoming_events = _split_prev_events(
        await get_previous_report_events(session, report_date), target_date
    )
    prev_events_text = _format_prev_events(pending_outcome_events, still_upcoming_events)
    recurring_calendar_events = _compute_recurring_calendar_events(target_date)

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
        if ev_date > target_dt:
            continue  # Sự kiện tương lai — không inject outcome

        event_name = ev.get("event", "")
        if "EIA" in event_name and eia_data:
            ev["outcome"] = format_eia_outcome(eia_data)
        elif "Baker Hughes" in event_name and bh_data:
            ev["outcome"] = format_bh_outcome(bh_data)

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

    # ── Bộ nhớ gợi ý kinh doanh (services/biz_memory.py) ──
    # Gợi ý ngắn hạn các báo cáo trước (trong 10 ngày) — kiểm tra tình huống kích
    # hoạt đã xảy ra chưa: ngưỡng giá → so thẳng với giá thật; điều kiện tin tức →
    # LLM đối chiếu tin trong ngày (bắt buộc kèm bài báo). CHỈ ĐỌC ở đây — ghi lại
    # ở bước cuối (xem persist bên dưới).
    active_suggestions = await biz_memory.load_active(session, report_date)
    triggered_suggestions: Dict[int, Dict[str, Optional[str]]] = {}
    llm_check_candidates = []
    for s in active_suggestions:
        if s.trigger_rule:
            evidence = biz_memory.check_price_rule(s.trigger_rule, prices)
            if evidence:
                triggered_suggestions[s.id] = {
                    "evidence": evidence,
                    "source_name": "Giá chốt phiên (Barchart)",
                    "source_url": next(
                        (p.get("source_url") for p in prices if p.get("code") == s.trigger_rule.get("code")), None
                    ),
                }
        else:
            llm_check_candidates.append(s)
    biz_news_text, biz_index_lookup = _filter_news_with_index(news_by_topic, "biz", max_articles=15)
    triggered_suggestions.update(
        await _check_biz_triggers_llm(llm_check_candidates, biz_news_text, biz_index_lookup, target_date)
    )
    # Gợi ý chưa kích hoạt → đối chiếu thêm: thực tế hôm nay có đi NGƯỢC giả định của đề xuất không.
    not_triggered = [s for s in active_suggestions if s.id not in triggered_suggestions]
    contradicted_suggestions = await _check_biz_contradictions_llm(
        not_triggered, biz_news_text, biz_index_lookup, prices_text, target_date
    )
    still_tracking = [s for s in not_triggered if s.id not in contradicted_suggestions]
    dismissed_suggestions = await biz_memory.load_recent_dismissed(session, report_date)
    valid_price_codes = {str(p["code"]).upper() for p in prices if p.get("code") and p.get("close") is not None}

    # ── 2. Gọi LLM từng mục song song (tuần tự để tránh rate limit) ──
    content: Dict[str, Any] = {}

    SECTIONS = [
        ("1", _prompt_section1(
            section1_news_text,
            prices_text, eua_trend, eua_session_range, target_date
        )),
        ("2", _prompt_section2(
            eua_key_facts, prices_text, eua_trend, gasoil_crack_spread,
            section2_news_text, target_date, _topics_present(news_by_topic, "2"),
            overrides=eua_framework_overrides,
        )),
        ("dev", _prompt_key_developments(
            dev_news_text, target_date, _topics_present(news_by_topic, "dev"),
            overrides=eua_framework_overrides,
        )),
        ("3", _prompt_section3(
            _filter_news_for_section(news_by_topic, "3"),
            prices_text, eua_trend, eua_session_range, gasoil_crack_spread, target_date,
            _topics_present(news_by_topic, "3"),
            overrides=eua_framework_overrides,
        )),
        ("4", _prompt_section4(
            section4_news_text,
            target_date
        )),
        ("8", _prompt_section8(
            _filter_news_for_section(news_by_topic, "8"),
            prev_events_text, target_date, recurring_events_text,
        )),
        ("biz", _prompt_biz_recommendation(
            _filter_news_for_section(news_by_topic, "biz"),
            prices_text, eua_trend, target_date,
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

            return sec_key, sec_data

    tasks = [_process_section(k, sys_p, usr_p) for k, (sys_p, usr_p) in SECTIONS]
    results = await asyncio.gather(*tasks)

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
    content["2"] = {
        "title": "Bảng giá nhanh",
        "price_timestamp": f"Giá chốt phiên {max_price_date or target_date} (nguồn: Barchart EOD)",
        "key_facts": eua_key_facts,
        "prices": prices,
        "key_developments": key_developments,
        "chart_data": chart_data,
        "market_drivers": {
            "bullish": _resolve_driver_items(raw_drivers.get("bullish")),
            "bearish": _resolve_driver_items(raw_drivers.get("bearish")),
        },
    }

    section6_news = _build_section6_news(news_by_topic)
    section6_international = await _summarize_section6_articles(section6_news["international"], target_date)
    section6_vietnam = await _summarize_section6_articles(section6_news["vietnam"], target_date)
    content["6"] = {
        "title": "Chi tiết các tin tức chính",
        "international": section6_international,
        "vietnam": section6_vietnam,
    }

    cited_articles = _collect_cited_articles(news_by_topic)
    content["9"] = {
        "title": "Nguồn tham khảo",
        "items": [{"source": a["source"], "title": a["title"], "url": a["url"]} for a in cited_articles],
    }

    # ── Bộ nhớ gợi ý kinh doanh: chuẩn hoá gợi ý mới + ghi DB (BƯỚC CUỐI) ──
    biz = content.get("biz") or dict(FALLBACKS["biz"])
    new_short_term: List[Dict[str, Any]] = []
    for it in biz.get("short_term") or []:
        if not isinstance(it, dict) or not (it.get("trigger") or "").strip() or not (it.get("action") or "").strip():
            continue
        new_short_term.append({
            "trigger": it["trigger"].strip(),
            "trigger_rule": biz_memory.normalize_trigger_rule(it.get("trigger_rule"), valid_price_codes),
            "action": it["action"].strip(),
            "reason": (it.get("reason") or "").strip(),
        })
    new_long_term: List[Dict[str, Any]] = [
        {
            "opportunity": it["opportunity"].strip(),
            "solution": it["solution"].strip(),
            "expectation": (it.get("expectation") or "").strip(),
        }
        for it in biz.get("long_term") or []
        if isinstance(it, dict) and (it.get("opportunity") or "").strip() and (it.get("solution") or "").strip()
    ]
    # "tracking" (đề xuất chưa kích hoạt) chỉ nằm trong bộ nhớ Jenny, không đưa vào báo cáo ngày.
    reminders, _ = biz_memory.reminders_for_content(active_suggestions, triggered_suggestions, contradicted_suggestions)
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
        # Chỉ gợi ý cũ vừa kích hoạt hôm nay mới lên báo cáo (khối "Jenny nhắc lại");
        # gợi ý chưa kích hoạt ở lại trong bộ nhớ (xem tracking_text trong prompt biz).
        "reminders": reminders,
    }

    return content
