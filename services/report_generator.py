import json
import logging
from datetime import datetime, timedelta, date, time as dtime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from urllib.parse import quote
import asyncio
import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from db.models import Article, Price, Instrument, PriceCrawlSource, Report
from core.config import Settings
from services import eua_causal_chains as chains
from services.eua_framework_admin import get_overrides_map
from crawl_prices.market_events_fetcher import (
    fetch_eia_weekly_inventory,
    fetch_baker_hughes_rig_count,
    format_eia_outcome,
    format_bh_outcome,
)

logger = logging.getLogger(__name__)

# Mục 1-5, 7, 8, biz (phân tích chuyên sâu, chuỗi nhân quả, chiến lược) dùng Opus;
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
    "5": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_renewable", "geopolitics", "cbam"],
    "7": ["eua_ets", "geopolitics"],
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
    "5": 3072,    # tới ~7 bullet (6 tín hiệu + Tổng hợp), tiếng Việt có dấu tốn token hơn ước tính
    "7": 2048,    # 1-3 viewpoints
    "8": 2048,    # danh sách events
    "biz": 3072,  # 2 bảng nhỏ
}

# ─────────────────────────────────────────────────────────────────────
# Khung phân tích giá EUA — tiêm ĐỘNG vào prompt Mục 2/3/5
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
danh mục này mới được đưa vào phân tích Mục 3/5; tin ngoài phạm vi này bỏ qua):

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
    (đúng thiết kế "Diễn biến chính" NHÓM 1 mục (c) — hydrogen là driver tổng
    quan, không phải tín hiệu ngày/tuần, nhưng Mục 3 vẫn cần nhắc tới khi có
    tin) — "short" sẽ loại cơ chế này dù đang có tin, sai với thiết kế đó.
    `full=False` (Mục 2/5): bỏ phần B, horizon="short" (đúng bản chất 2 mục
    này — bảng động lực/tín hiệu theo phiên, không cần phần driver dài hạn).

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


async def _fetch_cbam_price() -> Optional[Dict]:
    url = CBAM_PRICE_PAGE_URL
    try:
        from selectolax.parser import HTMLParser
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
                clean_price = latest_price.replace(".", "").replace(",", ".")
                note = f"Giá chốt theo quý, tại ngày {latest_date}"
                if next_date:
                    note += f", ngày chốt giá tiếp theo {next_date}"
                    
                return {
                    "name": "CBAM Certificate",
                    "code": "CBAM",
                    "price": f"{float(clean_price):,.2f} EUR",
                    "dday": "-",
                    "dweek": "-",
                    "up": True,
                    "note": note,
                    "close": float(clean_price),
                    "day_change_pct": None,
                    "week_change_pct": None,
                    "category": "carbon",
                    "source_url": CBAM_PRICE_PAGE_URL,
                }
    except Exception as e:
        logger.error(f"Error fetching CBAM price: {e}")
        
    return None

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


async def get_prices_for_report(session: AsyncSession, target_date_str: str) -> tuple[List[Dict], str]:
    """Lấy dữ liệu giá của ngày gần nhất có dữ liệu (<= target_date_str)."""
    # Tìm ngày gần nhất có dữ liệu
    max_date_stmt = select(func.max(Price.price_date)).where(Price.price_date <= target_date_str)
    max_date = await session.scalar(max_date_stmt)

    if not max_date:
        return [], None

    stmt = (
        select(Price, Instrument)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date == max_date)
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

    cbam_price = await _fetch_cbam_price()
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
        })
    return chart_data


async def get_news_for_report(session: AsyncSession, target_date_str: str) -> tuple[Dict[str, List[Dict]], List[str]]:
    """Lấy tin tức trong khung 07:00 (VN) ngày báo cáo → 07:00 (VN) ngày hôm sau.

    Khớp với lịch tự động: news_crawl chạy 06:00 & 12:00 (VN) mỗi ngày, report cho
    ngày T được auto-generate lúc 07:00 (VN) ngày T+1 — nên tin tức đưa vào báo cáo
    ngày T là tin thu thập từ 07:00 (VN) ngày T đến 07:00 (VN) ngày T+1 (bao gồm cả
    đợt crawl 06:00 của ngày T+1, chạy ngay trước khi report được sinh).
    Ví dụ: báo cáo ngày 23/08 (sinh lúc 07:00 ngày 24/08) lấy tin từ 07:00 ngày 23/08
    đến 07:00 ngày 24/08.

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
        src_art = index_lookup.get(it.get("source_index"))
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


async def get_previous_report_events(session: AsyncSession, target_date_str: str) -> List[Dict]:
    """Lấy danh sách sự kiện Mục 8 từ báo cáo gần nhất TRƯỚC ngày target — để Mục
    8 hôm nay có thể cập nhật lại kết quả thực tế của các sự kiện kỳ trước đã qua."""
    stmt = (
        select(Report)
        .where(
            Report.report_date < target_date_str,
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
    model mặc định Opus cho các mục phân tích chuyên sâu (Mục 1-5, 7, 8, biz);
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

    try:
        return json.loads(_escape_bare_control_chars_in_json_strings(snippet))
    except json.JSONDecodeError:
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
  + "FACT" = thông tin ĐÃ XÁC NHẬN: (a) từ DỮ LIỆU GIÁ hệ thống (nêu instrument, giá, Δ ngày + Δ tuần, source_index=null) hoặc (b) từ TIN TỨC đánh số nêu sự kiện/số liệu đã xảy ra (BẮT BUỘC kèm source_index).
  + "OPINION" = nhận định/suy luận/dự báo từ tin tức hoặc phân tích của bạn (NÊN kèm source_index nếu gắn bài cụ thể, null nếu suy luận chung).
- "text": CHỈ liệt kê fact (số liệu/sự kiện) — KHÔNG giải thích/suy luận tác động, KHÔNG viết chuỗi nhân quả hay kết luận hướng ảnh hưởng tới EUA. Nêu Δ ngày + Δ tuần khi trích dữ liệu giá.
- "source_index": số [N] có thật trong danh sách, hoặc null.
Xếp bullish/bearish theo ĐÚNG chuỗi nhân quả tới EUA (không theo chiều tăng/giảm bề ngoài của instrument) — chuỗi nhân quả CHỈ dùng để QUYẾT ĐỊNH xếp vào bullish hay bearish, KHÔNG viết ra trong "text". CHỈ đưa yếu tố có dữ liệu/tin hỗ trợ, không bịa thêm.
- KHỐI LƯỢNG GIAO DỊCH EUA: dùng đúng số liệu khối lượng phiên liền trước so với TB các phiên gần nhất đã nêu trong "SỐ LIỆU EUA" ở trên (KHÔNG tự tính lại/bịa số khác) để BẮT BUỘC thêm 1 mục "FACT" riêng suy luận từ khối lượng vào bullish/bearish, theo đúng logic "khối lượng xác nhận xu hướng giá":
  + Khối lượng tăng đột biến CÙNG chiều với giá tăng/giảm phiên đó → tín hiệu xác nhận lực mua/bán mạnh, xếp cùng chiều bullish/bearish tương ứng.
  + Khối lượng giảm mạnh trong khi giá vẫn biến động mạnh, hoặc khối lượng tăng đột biến nhưng giá gần như đi ngang → tín hiệu YẾU/thiếu xác nhận, ghi rõ là "OPINION" và nêu rủi ro đảo chiều/thiếu động lực thay vì kết luận dứt khoát.
  + Khối lượng ở mức bình thường → KHÔNG cần thêm mục riêng cho khối lượng (chỉ dùng khi có bất thường thực sự).

CHỈ TRẢ VỀ JSON HỢP LỆ:
{{"2": {{"market_drivers": {{"bullish": [{{"tag": "FACT", "text": "...", "source_index": null}}], "bearish": [{{"tag": "FACT", "text": "...", "source_index": null}}]}}}}}}"""
    return system, user


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
Đây là mục phân tích SÂU NHẤT của báo cáo — PHẢI đầy đủ nội dung bắt buộc, không bỏ trống phần nào bên dưới. NHƯNG PHẢI VIẾT SÚC TÍCH, TRỰC TIẾP: đi thẳng vào số liệu và kết luận, KHÔNG câu dẫn dắt/đệm không mang thông tin, KHÔNG lặp lại số liệu/nội dung đã nêu ở mục/trường khác trong cùng báo cáo — "đủ nội dung" nghĩa là đủ Ý bắt buộc, không phải đủ CÂU CHỮ. Mục này gồm 2 phần con:

A. "analysis_blocks": mảng gồm "heading" và "content". Heading 1, 2, 4 LUÔN PHẢI có mặt; heading 3 ("Quan điểm thị trường") LÀ TÙY CHỌN — xem quy tắc riêng ở mục 3 bên dưới. QUY TẮC ĐỘ DÀI CHUNG: mỗi Ý/gạch đầu dòng trong "content" tối đa 1–2 câu NGẮN GỌN, đi thẳng vào số liệu/kết luận — không diễn giải dài dòng, không viết chung chung (heading 1 có thể gồm NHIỀU gạch đầu dòng theo nhóm như quy tắc riêng bên dưới, nhưng mỗi gạch vẫn phải súc tích). NGOẠI LỆ: heading 2 ("Phân tích") và heading 4 ("Cần theo dõi") KHÔNG bị giới hạn 1–2 câu mỗi gạch đầu dòng — xem "ĐỘ DÀI RIÊNG" ngay trong quy tắc của từng heading đó bên dưới. Heading 2 ưu tiên NGẮN GỌN, TRỰC DIỆN (đủ số liệu + kết luận, KHÔNG giải thích lại cơ chế/logic suy luận); heading 4 ưu tiên ĐẦY ĐỦ 2 kịch bản trái chiều. Cả hai đều không thêm câu đệm/chuyển tiếp không mang thông tin mới:
   1. heading="Diễn biến chính" — trình bày SỐ LIỆU/THÔNG TIN THỰC TẾ (fact-only, CHƯA phân tích tác động EUA ở đây — phần phân tích thuộc heading "Phân tích" bên dưới), tổ chức theo ĐÚNG 3 NHÓM trong "B. DANH MỤC THEO DÕI" ở KHUNG PHÂN TÍCH trên, theo ĐÚNG THỨ TỰ:
      - NHÓM 1 — Năng lượng & nhiên liệu hóa thạch: gồm 3 loại nội dung, CẢ BA đều thuộc NHÓM 1 (không phải chỉ giá):
        (a) Giá đóng cửa của các mã liên quan có biến động đáng chú ý trong phiên (TTF, Coal/NEWC/API2, Dầu Brent/WTI, Gasoil, Power Đức DEBY1...) kèm Δ ngày/Δ tuần lấy đúng từ DỮ LIỆU GIÁ, và 1 câu ngắn giải thích NGUYÊN NHÂN tăng/giảm đó (không phải tác động lên EUA — chỉ giải thích vì sao chính mã đó tăng/giảm, nếu tin tức có nêu).
        (b) Sự kiện ĐỊA CHÍNH TRỊ/gián đoạn nguồn cung năng lượng (xung đột, sanctions, OPEC+, căng thẳng Trung Đông/Nga/Mỹ/Trung Quốc, rig count, tồn kho EIA/API...) — PHẢI nêu dù giá CHƯA kịp phản ánh rõ trong phiên này, KHÔNG được bỏ qua chỉ vì thiếu số liệu giá đi kèm. Nêu ngắn gọn sự kiện + rủi ro cung/giá tiềm ẩn (nếu giá đã phản ánh thì nêu luôn số liệu; nếu chưa thì ghi rõ "rủi ro cung/giá trong ngắn hạn" thay vì bịa số).
        (c) NĂNG LƯỢNG TÁI TẠO (gió/mặt trời/thủy điện — quy mô sản xuất, dự báo tăng trưởng, dự án/đầu tư) và HYDROGEN (dự án/chính sách hydrogen xanh, thép xanh) nếu tin tức có đề cập — nêu ngắn gọn diễn biến, KHÔNG bỏ qua chỉ vì đây không phải tin giá.
      - NHÓM 2 — Hạn ngạch & tín chỉ carbon: các thông tin chính về EUA/EU ETS trong ngày (đấu giá, MSR, dòng vốn/đầu cơ, động thái big players, dự báo giá từ tổ chức...), VÀ diễn biến các thị trường carbon compliance NGOÀI EU (China ETS, Korea ETS, California Cap-and-Trade, CORSIA...) hoặc VCM nếu có tin — nêu rõ đây là thị trường khác, không lẫn với EUA. LƯU Ý: phần thị trường ngoài EU/VCM này CHỈ mang tính thông tin ở "Diễn biến chính" — theo thiết kế hệ thống, các thị trường này KHÔNG fungible với EUA và KHÔNG tạo cầu/cung EUA trực tiếp, nên sẽ KHÔNG được phân tích tác động ở "Phân tích" (xem LOẠI TRỪ ĐÍCH DANH trong mục 2 bên dưới) trừ khi tin tức nêu rõ 1 cơ chế cụ thể nối sang EUA.
      - NHÓM 3 — Chính sách: các thông tin chính sách trong ngày (CBAM, Fit-for-55, ETS mở rộng, VCM, chính sách carbon VN...) nếu có.
      QUAN TRỌNG — CHỈ VIẾT NHÓM CÓ THÔNG TIN THẬT: nhóm nào KHÔNG có dữ liệu giá biến động đáng chú ý hoặc tin tức khớp danh mục → BỎ QUA HOÀN TOÀN, KHÔNG viết dòng "không có thông tin" cho nhóm đó. "content" có thể chỉ có 1, 2, hoặc đủ 3 đoạn nhóm tuỳ ngày.
      ĐỊNH DẠNG: "content" là 1 chuỗi string. Mỗi nhóm bắt đầu bằng "Tên nhóm: " (dùng đúng "Năng lượng & nhiên liệu hóa thạch:", "Hạn ngạch & tín chỉ carbon:", "Chính sách:") rồi tới nội dung. NẾU 1 nhóm có từ 2 mã/ý trở lên (vd NHÓM 1 có cả TTF và Coal cùng biến động): PHẢI tách mỗi mã/ý thành 1 GẠCH ĐẦU DÒNG RIÊNG xuống dòng thật (\\n"- ...") ngay dưới dòng tên nhóm, để dễ nhìn — TUYỆT ĐỐI KHÔNG gộp nhiều mã/ý vào chung 1 câu văn dài. Giữa các NHÓM cũng luôn xuống dòng thật (\\n).
      Nếu CẢ 3 nhóm đều không có thông tin: "content" = "Không có thông tin mới liên quan trực tiếp đến giá EUA."
      Ví dụ format (chỉ minh hoạ cấu trúc, không copy nội dung mẫu):
      "Năng lượng & nhiên liệu hóa thạch:\\n- TTF đóng cửa X, Δ ngày +3.7%, Δ tuần +5.1% — tăng do lo ngại nguồn cung LNG.\\n- Than NEWC đóng cửa Y, Δ ngày -1.2% — giảm do nhu cầu nhiệt điện châu Á yếu.\\nHạn ngạch & tín chỉ carbon:\\n- ICE ghi nhận khối lượng đấu giá EUA tuần này tăng so với kế hoạch.\\nChính sách:\\n- EU công bố siết lịch đấu giá EUA quý 4."
   2. heading="Phân tích" — PHÂN TÍCH TÁC ĐỘNG (gộp chung cả phân tích liên thị trường Gas–Than–Điện Đức vào đây, KHÔNG tách thành mục riêng): dựa trên đúng các thông tin/số liệu đã nêu ở "Diễn biến chính" (KHÔNG lặp lại số liệu, chỉ tham chiếu ngắn gọn khi cần làm căn cứ trực tiếp cho kết luận), phân tích thông tin của TỪNG NHÓM đã xuất hiện ở "Diễn biến chính" sẽ ảnh hưởng thế nào đến CUNG/CẦU và GIÁ EUA. BẮT BUỘC áp dụng ĐÚNG chuỗi nhân quả trong "A. CÁC MỐI LIÊN HỆ LIÊN THỊ TRƯỜNG" của KHUNG PHÂN TÍCH ở trên (fuel switching, CBAM/ETS, chính sách/MSR, địa chính trị...) để XÁC ĐỊNH đúng chiều/mức độ tác động — TUYỆT ĐỐI KHÔNG tự sinh chuỗi nhân quả khác hay suy diễn lệch khỏi khung chuẩn đó.
      NGUYÊN TẮC TRÌNH BÀY BẮT BUỘC — TRỰC DIỆN, KHÔNG GIẢI THÍCH LẠI LOGIC: các bước suy luận (i)–(v) bên dưới CHỈ dùng để bạn XÁC ĐỊNH ĐÚNG chiều/mức độ tác động ở NỘI BỘ suy nghĩ của bạn — khi viết ra "content", TUYỆT ĐỐI KHÔNG diễn giải lại từng bước cơ chế/logic kiểu văn xuôi "vì X nên Y nên Z nên..."; chỉ nêu SỐ LIỆU/SỰ KIỆN thật ngắn rồi ĐI THẲNG tới KẾT LUẬN TÁC ĐỘNG, không đường vòng qua mô tả cơ chế. Bôi đậm (**...**) tên mã/nhóm hoặc cụm kết luận chính ở đầu mỗi gạch đầu dòng để người đọc dễ quét mắt vào đúng nội dung cần chú ý.
      BỘ LỌC BẮT BUỘC TRƯỚC KHI VIẾT (áp dụng cho MỌI nhóm, không riêng Nhóm 1): mục này CHỈ chứa những yếu tố/mã/sự kiện mà — sau khi áp ĐÚNG chuỗi nhân quả chuẩn — THỰC SỰ tạo tác động có căn cứ, có hướng rõ ràng (tăng hoặc giảm, kể cả tác động nhẹ, miễn có cơ chế rõ và dữ liệu ủng hộ) lên CUNG/CẦU hoặc GIÁ EUA. Yếu tố nào rơi vào 1 trong 2 trường hợp sau → BỎ QUA HOÀN TOÀN, KHÔNG viết thành 1 gạch đầu dòng ở đây (dù yếu tố đó đã xuất hiện ở "Diễn biến chính" vì lý do khác, vd chỉ để đưa tin):
        - Biến động không đáng kể (Δ ngày và Δ tuần đều gần như đi ngang) và không đủ kích hoạt bất kỳ cơ chế dispatch/hedge/chính sách nào.
        - Theo đúng chuỗi nhân quả chuẩn, yếu tố này KHÔNG dẫn tới tác động nào lên cung/cầu/giá EUA (trung lập thật sự theo logic, không phải chỉ vì thiếu số liệu).
      PHÂN BIỆT RÕ với trường hợp TÍN HIỆU MÂU THUẪN/CHƯA ĐỦ MẠNH ở bước (i) bên dưới (Δ ngày và Δ tuần trái chiều nhưng cả 2 đều là biến động thực) — trường hợp đó VẪN PHẢI giữ lại và viết thành 1 gạch đầu dòng, vì đây là rủi ro/tín hiệu cần theo dõi chứ không phải yếu tố trung lập/không tác động.
      LOẠI TRỪ ĐÍCH DANH (không suy diễn gián tiếp — nêu thẳng để tránh bị đưa nhầm vào "Phân tích"):
      {chains.get_block("NON_EUA_CARBON_MARKETS", overrides)}
      Dù các tin này ĐÃ xuất hiện ở "Diễn biến chính" (NHÓM 2, mục đích chỉ để thông tin), TUYỆT ĐỐI KHÔNG đưa vào "Phân tích" và TUYỆT ĐỐI KHÔNG viết kiểu "Kết luận: trung lập" cho riêng nội dung này, theo đúng quy tắc ở trên.
      ĐỘ DÀI RIÊNG CHO HEADING NÀY: KHÔNG áp dụng giới hạn 1–2 câu của "QUY TẮC ĐỘ DÀI CHUNG" ở trên (mỗi gạch có thể dài hơn 2 câu nếu cần đủ Δ ngày/Δ tuần + kết luận), NHƯNG PHẢI súc tích, trực diện theo đúng "NGUYÊN TẮC TRÌNH BÀY BẮT BUỘC" ở trên — không câu mở đầu/đệm/chuyển tiếp thừa, không lặp lại nguyên văn số liệu đã nêu ở "Diễn biến chính" (chỉ nhắc số liệu khi trực tiếp làm căn cứ cho kết luận trong chính câu đó), và KHÔNG diễn giải lại từng bước cơ chế/logic suy luận.
      QUY TẮC SUY LUẬN NỘI BỘ CHO NHÓM 1 (Năng lượng & nhiên liệu hóa thạch) — áp dụng cho MỌI mã/sự kiện đã xuất hiện ở "Diễn biến chính" nhóm này để XÁC ĐỊNH đúng chiều/mức độ tác động (đây là cơ sở suy luận NỘI BỘ, không phải văn mẫu để chép lại nguyên văn vào "content" — xem "NGUYÊN TẮC TRÌNH BÀY BẮT BUỘC" ở trên). LƯU Ý: tên chuỗi/nhánh viết HOA bên dưới (FUEL_SWITCHING, POWER_EUA_TWO_WAY, OIL_GASOIL, GEOPOLITICS_SUPPLY_CHAIN, "nhánh (a)"...) CHỈ để bạn xác định ĐÚNG cơ chế cần áp dụng — TUYỆT ĐỐI KHÔNG chép các tên này, và TUYỆT ĐỐI KHÔNG diễn giải lại các bước (i)-(v) này bằng lời trong "content"; "content" chỉ chứa SỐ LIỆU + KẾT LUẬN đã áp dụng đúng các bước này, không mô tả quá trình suy luận:
        (i) ĐỐI CHIẾU KHUNG THỜI GIAN: so Δ ngày VÀ Δ tuần của chính mã đó. ĐỒNG THUẬN (cùng chiều) → xác nhận xu hướng bền vững, đủ cơ sở kết luận dứt khoát chiều tác động EUA. MÂU THUẪN (trái chiều) → nêu rõ đây là tín hiệu ngắn hạn/chưa đủ mạnh, cần thêm phiên xác nhận, KHÔNG được chốt chiều tác động EUA dứt khoát.
        (ii) GAS/THAN → áp dụng FUEL_SWITCHING: đây là cơ chế GIÁ TƯƠNG ĐỐI, không phải giá tuyệt đối của riêng 1 nhiên liệu — Gas tăng (than không đổi/tăng ít hơn) → than cạnh tranh hơn → dispatch dịch sang than → phát thải & cầu EUA tăng; Gas giảm HOẶC Than tăng (2 tín hiệu CÙNG CHIỀU, không phải trái chiều) → gas cạnh tranh hơn → dispatch dịch sang gas → phát thải & cầu EUA giảm. TUYỆT ĐỐI KHÔNG suy luận "than tăng → đốt than nhiều hơn" (sai chiều kinh tế — giá than tăng một mình làm than kém cạnh tranh hơn, không phải được đốt nhiều hơn); chỉ kết luận than được đốt nhiều hơn khi tin tức xác nhận rõ nguyên nhân khác (gas gián đoạn, RES thấp, sự cố hạ tầng) khiến cầu than tăng kéo giá than tăng theo, không suy đoán từ riêng chiều giá than.
        (iii) ĐIỆN ĐỨC (DEBY1) → áp dụng ĐÚNG nhánh (a) của POWER_EUA_TWO_WAY: nếu DEBY1 biến động đồng pha với gas/than → kết luận điện tăng do chi phí nhiên liệu cao hơn, utility hedge thêm EUA tương ứng sản lượng đã bán — kênh hedge/chi phí này TÁCH BIỆT với việc thực tế đốt nhiên liệu nào, KHÔNG gộp chung "than+điện cùng tăng" thành kết luận "đốt than nhiều hơn". CHỈ được quy nguyên nhân "RES thấp" khi có dữ liệu/tin tức THỰC SỰ xác nhận RES thấp trong ngày — nếu KHÔNG có dữ liệu RES, PHẢI nêu rõ "không có dữ liệu RES trong ngày để xác nhận nhánh này" thay vì mặc định suy diễn.
        (iv) DẦU/GASOIL → áp dụng OIL_GASOIL: đối chiếu Δ ngày/Δ tuần của Brent/WTI theo đúng bước (i) trước khi kết luận fuel switching qua kênh dầu-khí (mâu thuẫn giữa 2 khung thời gian → tín hiệu ngắn hạn chưa đủ mạnh, cần thêm phiên xác nhận, chưa kết luận). Gasoil crack spread thu hẹp/mở rộng → nêu tác động lên cầu diesel/công nghiệp nặng và cầu EUA từ kênh công nghiệp đó, tách riêng khỏi kết luận fuel switching qua gas.
        (v) ĐỊA CHÍNH TRỊ — có 2 kênh TÁCH BIỆT trong GEOPOLITICS_SUPPLY_CHAIN, phải xác định đúng kênh trước khi suy luận:
            - Xung đột/sanctions/OPEC+/gián đoạn năng lượng vật lý (Trung Đông/Nga/Iran/Venezuela, eo biển vận chuyển...) → áp dụng ĐÚNG nhánh (a), BẮT BUỘC đi qua đủ bước cung → giá gas/dầu trước khi vào kết luận EUA: nếu Δ tuần của gas/dầu CHƯA xác nhận xu hướng tăng do sự kiện đó (vd giá dầu tuần vẫn giảm), CHỈ được ghi nhận đây là "rủi ro cung/giá tiềm ẩn", TUYỆT ĐỐI KHÔNG kết luận thẳng sự kiện địa chính trị đã đẩy tăng cầu EUA khi thiếu xác nhận giá.
            - Thay đổi chính phủ/định hướng chính sách khí hậu/quyết định thương mại quốc tế (KHÔNG phải gián đoạn năng lượng vật lý) → áp dụng ĐÚNG nhánh (b), đi qua kênh kỳ vọng thị trường/hoạt động công nghiệp, KHÔNG bắt buộc đi qua bước giá gas/dầu như nhánh (a) — CHỈ nêu khi tin tức nêu rõ sự kiện và hướng tác động cụ thể, không suy diễn chung chung.
      GỘP GAS–THAN–ĐIỆN ĐỨC THÀNH 1 GẠCH DUY NHẤT: khi Gas (TTF), Than (Newcastle/API2) và Điện Đức (DEBY1) CÙNG có mặt ở "Diễn biến chính" Nhóm 1 và cơ chế fuel-switching/POWER_EUA_TWO_WAY thực sự áp dụng được (theo bước (ii)/(iii) ở trên) — KHÔNG viết 3 gạch đầu dòng riêng cho từng mã, mà GỘP thành ĐÚNG 1 gạch đầu dòng duy nhất "**Fuel switching (Gas–Than–Điện Đức):**", nêu Δ ngày/Δ tuần của cả 3 mã thật ngắn gọn rồi đi thẳng tới 1 kết luận chiều dispatch và tác động EUA — không diễn giải lại cơ chế giá tương đối. Mã nào không có tin/giá đáng chú ý thì bỏ qua khỏi gạch này; nếu chỉ 1-2 trong 3 mã có mặt thì viết riêng như các mã khác trong Nhóm 1, không ép gộp.
      QUY TẮC NHÓM: CHỈ phân tích nhóm nào ĐÃ xuất hiện ở "Diễn biến chính" (nhóm không có thông tin thì cũng không có gì để phân tích ở đây — bỏ qua tương ứng). SAU KHI áp BỘ LỌC BẮT BUỘC ở trên, nếu 1 nhóm không còn yếu tố nào có tác động thực → BỎ QUA HOÀN TOÀN nhóm đó ở "Phân tích" (không viết dòng "Tên nhóm:" cho nhóm đó), kể cả khi nhóm đó có xuất hiện ở "Diễn biến chính".
      THỨ TỰ CỐ ĐỊNH: PHẢI theo ĐÚNG thứ tự NHÓM 1 (Năng lượng & nhiên liệu hóa thạch) → NHÓM 2 (Hạn ngạch & tín chỉ carbon) → NHÓM 3 (Chính sách) — GIỐNG HỆT thứ tự đã dùng ở "Diễn biến chính", TUYỆT ĐỐI KHÔNG đảo thứ tự theo mức độ quan trọng hay ưu tiên nhóm nào lên trước.
      MỖI NHÓM PHẢI CÓ KẾT LUẬN NGẮN: sau các gạch đầu dòng của 1 nhóm, CHỐT bằng 1 câu RIÊNG bắt đầu bằng tag in đậm "**Kết luận:**" nêu rõ nhóm này đẩy EUA tăng/giảm/trung lập (vd "**Kết luận:** Nhóm này tạo áp lực tăng nhẹ lên EUA.") — chỉ chốt chiều, KHÔNG lặp lại số liệu hay diễn giải lại cơ chế đã dùng để suy ra kết luận đó. "Trung lập" CHỈ dùng khi nhóm có ≥2 yếu tố THỰC SỰ có tác động (đã qua BỘ LỌC BẮT BUỘC) nhưng triệt tiêu lẫn nhau về chiều. LƯU Ý QUAN TRỌNG: Dòng "**Kết luận:**" là một đoạn văn bản riêng, TUYỆT ĐỐI KHÔNG đặt dấu gạch đầu dòng ("-") ở trước nó và KHÔNG tạo ra các gạch đầu dòng trống (chỉ có dấu "-" rồi để trống) trước khi viết kết luận.
      KẾT LUẬN CHUNG CHO CẢ MỤC "PHÂN TÍCH": sau khi trình bày xong các nhóm áp dụng được ở trên, LUÔN kết thúc "content" bằng 1 ĐOẠN RIÊNG cuối cùng (2–3 câu), BẮT ĐẦU bằng tag in đậm "**Tổng hợp:**" (không dùng lại "**Kết luận:**" cho đoạn này — tag đó chỉ dùng riêng cho từng nhóm ở trên): đối chiếu ngắn Δ ngày/Δ tuần của chính EUA (từ "SỐ LIỆU THẬT VỀ EUA"/"XU HƯỚNG EUA 30 NGÀY" ở trên) — cùng chiều → xu hướng nhất quán, độ tin cậy cao; trái chiều → tín hiệu ngắn hạn/nhiễu, cần thận trọng — rồi TỔNG HỢP các "**Kết luận:**" của từng nhóm ở trên thành 1 kết luận DỨT KHOÁT duy nhất về hướng đi EUA, KHÔNG lặp lại chi tiết/số liệu đã nêu ở các nhóm. Áp dụng quy tắc: kết luận chiều giá chỉ khi ≥2 yếu tố/nhóm cùng hướng; nếu mâu thuẫn → ghi "tín hiệu hỗn hợp" + nêu ngắn 2 chiều + điều kiện kích hoạt mỗi chiều. Đây là câu quan trọng nhất Mục 3 — PHẢI dứt khoát, không mơ hồ.
      ĐỊNH DẠNG: "content" là 1 chuỗi string, mỗi nhóm bắt đầu bằng "Tên nhóm: " giống hệt "Diễn biến chính"; NẾU 1 nhóm có nhiều yếu tố/chuỗi tác động → tách mỗi yếu tố thành 1 gạch đầu dòng xuống dòng thật (\\n"- ...") để dễ nhìn; TUYỆT ĐỐI KHÔNG để lại các dấu "-" trống thừa thãi giữa các ý hoặc trước kết luận; giữa các NHÓM luôn xuống dòng thật (\\n); dòng "**Tổng hợp:**" luôn là dòng CUỐI CÙNG của "content" — TUYỆT ĐỐI KHÔNG viết liền thành 1 đoạn văn dài.
      Nếu CẢ 3 nhóm đều không có thông tin, HOẶC còn thông tin ở "Diễn biến chính" nhưng SAU KHI áp BỘ LỌC BẮT BUỘC không nhóm nào còn yếu tố có tác động thực: "content" = "Không có thông tin mới liên quan trực tiếp đến giá EUA." (bỏ qua dòng "**Tổng hợp:**" trong trường hợp này).
   3. heading="Quan điểm thị trường" (TÙY CHỌN) — CHỈ đưa object này vào mảng "analysis_blocks" khi TIN TỨC ở trên THỰC SỰ có nêu quan điểm/nhận định cụ thể từ nguồn xác định (nhà phân tích, tổ chức, báo cáo) — nêu cả consensus view VÀ contrarian view nếu có, kèm tên nguồn cụ thể. NẾU KHÔNG CÓ tin nào nêu quan điểm thị trường cụ thể: KHÔNG thêm object heading="Quan điểm thị trường" vào mảng — bỏ qua hoàn toàn (không viết "Không có quan điểm thị trường cụ thể." nữa).
   4. heading="Cần theo dõi" — KHÔNG PHẢI danh sách lịch sự kiện chung chung. Đây là các WATCHPOINT rút ra TRỰC TIẾP từ chính số liệu giá và tin tức ĐÃ NÊU ở "Diễn biến chính"/"Phân tích" phía trên — ưu tiên đúng những điểm ở "Phân tích" CHƯA đủ cơ sở kết luận dứt khoát (vd Δ ngày/Δ tuần mâu thuẫn cần thêm phiên xác nhận, sự kiện địa chính trị/chính sách giá CHƯA kịp phản ánh, nhánh cơ chế còn thiếu dữ liệu để xác nhận...). Mục đích: cho người đọc biết CHÍNH XÁC cần nhìn vào đâu ở phiên/tin tiếp theo và mỗi khả năng xảy ra sẽ kéo cung/cầu, giá EUA theo hướng nào.
      ĐỘ DÀI RIÊNG CHO HEADING NÀY: KHÔNG áp dụng giới hạn 1–2 câu — mỗi watchpoint cần đủ chỗ nêu 2 kịch bản trái chiều nên có thể dài hơn, nhưng vẫn phải súc tích, thẳng vào thông tin, không thêm câu đệm.
      MỖI WATCHPOINT PHẢI CÓ ĐỦ 3 PHẦN:
        (a) CẦN THEO DÕI GÌ — nêu đích danh mã giá/chỉ số/tin tức cụ thể đã xuất hiện ở trên (vd "Δ tuần của Brent/WTI ở phiên tới", "diễn biến đàm phán sanctions Iran/Venezuela", "dữ liệu RES/gió-mặt trời ngày mai", "kết quả đấu giá EUA kỳ tới"...) — kèm ngày giờ Việt Nam cụ thể CHỈ khi tin tức/lịch công bố có nêu rõ, KHÔNG tự bịa thời điểm.
        (b) NẾU XẢY RA THEO HƯỚNG 1 — bắt đầu bằng "Nếu [diễn biến cụ thể theo hướng 1]..." → nêu rõ tác động tới cung/cầu và chiều giá EUA tương ứng.
        (c) NẾU XẢY RA THEO HƯỚNG NGƯỢC LẠI (hoặc không xảy ra) — bắt đầu bằng "Nếu [diễn biến ngược lại/không xác nhận]..." → nêu rõ tác động khác hoặc EUA đi ngang/giữ tín hiệu hỗn hợp.
      MỖI watchpoint là 1 DÒNG RIÊNG, đánh số "1.", "2.", "3."... — PHẢI chèn ký tự xuống dòng thật (\\n) giữa các dòng, TUYỆT ĐỐI KHÔNG viết liền các watchpoint thành 1 đoạn văn dài không xuống dòng. Số lượng watchpoint bám theo đúng số điểm còn chưa chắc chắn thực sự tồn tại ở "Phân tích"/tin tức — KHÔNG bịa thêm watchpoint không có căn cứ chỉ để đủ số lượng.

B. "trading_scenarios": mảng ĐÚNG 3 kịch bản — BẮT BUỘC đủ cả 3 horizon "ngắn hạn", "trung hạn", "dài hạn" (không được bỏ trống horizon nào). ĐÂY LÀ PHẦN CHIẾN LƯỢC QUAN TRỌNG NHẤT BÁO CÁO — viết bằng kiến thức chuyên môn thực sự của 1 chuyên gia trading hàng hoá/carbon dày dạn (KHÔNG phải câu mẫu chung chung, sáo rỗng, hay lặp nguyên văn mục A). Mỗi kịch bản kể theo đúng mạch câu chuyện điều kiện: "Nếu [X] xảy ra, giá EUA có khả năng đi theo hướng [Y]; rủi ro chính là [Z]" — rồi mới khai triển thành 1 chiến lược trading cụ thể theo đúng kịch bản đó. Mỗi kịch bản gồm:
   - "horizon": "ngắn hạn" (1–2 tuần) / "trung hạn" (1–3 tháng) / "dài hạn" (>3 tháng)
   - "probability": xác suất kịch bản này xảy ra — CHỈ 1 trong 3 giá trị "Cao" / "Trung bình" / "Thấp", dựa trên driver ở mục A đã được dữ liệu/tin tức xác nhận rõ (probability cao hơn) hay mới chỉ là suy đoán/tin đồn (probability thấp hơn).
   - "direction": ĐÚNG 1 trong 3 giá trị "tăng" / "giảm" / "đi ngang" — chiều giá EUA của RIÊNG kịch bản này, phải khớp ĐÚNG chiều với "trading_strategy" bên dưới (dùng để hiển thị mũi tên trên giao diện).
   - "condition" (1 câu): "Nếu [X cụ thể — gắn thẳng với 1 driver đã nêu ở mục A, có số liệu/ngưỡng/ngày tháng cụ thể] xảy ra..." — KHÔNG viết mơ hồ kiểu "nếu thị trường biến động mạnh".
   - "price_zone" (1 câu, chỉ số liệu): vùng giá EUA tham chiếu CỤ THỂ bằng EUR/tCO2 cho kịch bản này (vùng hỗ trợ gần nhất / vùng kháng cự gần nhất) — PHẢI neo vào đúng số liệu 30-ngày-cao, 30-ngày-thấp, giá đóng cửa, biên độ phiên liền trước đã cung cấp ở trên, TUYỆT ĐỐI KHÔNG bịa con số không có căn cứ từ dữ liệu đã cho.
   - "key_risk" (tối đa 2 câu): "...rủi ro chính là [Z]..." — kịch bản rủi ro CỤ THỂ gắn với 1 sự kiện/ngưỡng/mốc thời gian rõ ràng (KHÔNG viết chung chung "rủi ro là biến động thị trường"), nêu ngắn gọn nếu rủi ro này xảy ra thì đẩy giá lệch khỏi "price_zone" theo hướng nào, mức độ bao nhiêu.
   - "trading_strategy": CHIẾN LƯỢC TRADING CHUYÊN NGHIỆP cho riêng kịch bản này — suy luận trực tiếp từ "condition"/"price_zone"/"key_risk" đã nêu ở trên, PHẢI logic chặt chẽ và chính xác, KHÔNG chung chung/sáo rỗng. Bắt buộc đủ 3 phần, mỗi phần 1 câu súc tích, bắt đầu bằng đúng tag in đậm rồi xuống dòng thật (\\n) giữa 3 phần:
     + "**Entry:**" vùng giá/điều kiện tham gia vị thế CỤ THỂ, ĐÚNG chiều với "direction" ở trên và neo đúng vào "price_zone" (KHÔNG bịa mức giá khác ngoài dữ liệu đã cho).
     + "**Mục tiêu:**" vùng giá chốt lời hợp lý kế tiếp — dựa trên đúng số liệu 30-ngày-cao/30-ngày-thấp/biên độ phiên đã cung cấp, đảm bảo tỷ lệ risk/reward hợp lý so với Entry.
     + "**Quản trị rủi ro:**" ngưỡng giá cụ thể để cắt lỗ/thoát vị thế nếu kịch bản bị vô hiệu hóa — gắn thẳng với "key_risk" đã nêu ở trên, nêu rõ mức giá nào xác nhận kịch bản này sai.
     Đây là chiến lược tham khảo cho người đọc tự cân nhắc (giao diện đã có lưu ý rõ ràng đi kèm) — ĐƯỢC PHÉP nêu mức giá/vùng giá Entry/Target/cắt lỗ cụ thể, nhưng TUYỆT ĐỐI KHÔNG bịa số liệu không có căn cứ từ dữ liệu đã cho ở trên.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"3": {{"title": "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA", "analysis_blocks": [{{"heading": "...", "content": "..."}}], "trading_scenarios": [{{"horizon": "...", "probability": "Cao/Trung bình/Thấp", "direction": "tăng/giảm/đi ngang", "condition": "...", "price_zone": "...", "key_risk": "...", "trading_strategy": "..."}}]}}}}"""
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


def _prompt_section5(
    news_text: str, prices_text: str, gasoil_crack_spread: str, target_date: str,
    topics_present: List[str], overrides: Optional[Dict[str, str]] = None,
) -> tuple[str, str]:
    framework = _eua_framework(topics_present, full=False, overrides=overrides)
    system = f"Bạn là chuyên gia phân tích liên thị trường năng lượng và carbon.\n{CONCISENESS_RULE}\n\n{framework}"
    user = f"""Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ PHIÊN VỪA QUA:
{prices_text}

GASOIL CRACK SPREAD (số liệu đã tính sẵn — PHẢI dùng đúng con số này nếu nhắc đến crack spread):
{gasoil_crack_spread}

TIN TỨC LIÊN QUAN (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_renewable, geopolitics, cbam):
{news_text}

YÊU CẦU: Viết MỤC 5 — TÍN HIỆU LIÊN THỊ TRƯỜNG.
Kết quả PHẢI là "bullets": một MẢNG các chuỗi, MỖI TÍN HIỆU LIÊN THỊ TRƯỜNG LÀ MỘT PHẦN TỬ RIÊNG (xuống dòng riêng khi hiển thị) — TUYỆT ĐỐI KHÔNG gộp nhiều tín hiệu vào chung một đoạn văn dài.

Quy tắc BẮT BUỘC:
- Mục này LUÔN XUẤT HIỆN trong báo cáo.
- Dựa vào KHUNG PHÂN TÍCH bên trên, quét LẦN LƯỢT từng mối liên kết có thể áp dụng (fuel switching Gas/Coal/Power, Dầu & Gasoil crack spread, RES/thời tiết, CBAM & mở rộng ETS, Chính sách & MSR, Kim loại cơ bản nếu có số liệu, Macro nếu có số liệu):
    A. Với mỗi nhóm: xác định xem có biến động đáng kể hay không — xét ĐỦ CẢ Δ ngày (>0.5%) VÀ Δ tuần lấy từ DỮ LIỆU GIÁ (Δ ngày và Δ tuần cùng chiều, rõ xu hướng → biến động đáng kể dù mức Δ ngày nhỏ; Δ ngày lớn nhưng Δ tuần đi ngược → hạ mức đáng tin cậy, coi là biến động phiên đơn lẻ) — hoặc có tin tức hỗ trợ cụ thể.
    B. Nếu có: kiểm tra xem biến động đó có tạo ra chuỗi lan truyền sang EUA không (theo đúng chuỗi nhân quả trong KHUNG).
    C. Nếu có tín hiệu LAN TRUYỀN: viết THÀNH MỘT BULLET RIÊNG cho liên kết đó (tối đa 2 câu NGẮN GỌN, đi thẳng vào số liệu và kết luận) — bắt đầu bằng tag in đậm nêu rõ cặp liên kết (vd "**Gas → EUA:**", "**Dầu/Crack spread → EUA:**", "**Điện Đức → EUA:**", "**Địa chính trị → EUA:**", "**Chính sách/MSR → EUA:**"...), nêu số liệu cụ thể (Δ ngày VÀ Δ tuần — theo đúng "D. KHUNG THỜI GIAN PHÂN TÍCH" ở trên, không chỉ 1 trong 2) và KẾT LUẬN rõ ràng về chiều tác động lên EUA (không liệt kê suông, phải chốt chiều tăng/giảm/trung lập) — KHÔNG diễn giải thêm ngoài 2 câu này.
    D. Nếu tín hiệu của các nhóm mâu thuẫn nhau: thêm 1 bullet riêng (tối đa 2 câu) ghi "**Tín hiệu hỗn hợp:**" + nêu ngắn gọn 2 chiều đối lập và điều kiện nào sẽ khiến chiều nào thắng thế.
    E. Nếu không nhóm nào có biến động đáng kể: "bullets" chỉ gồm đúng 1 phần tử là câu "Không có tín hiệu liên thị trường mới." — KHÔNG bịa liên kết gượng ép.
- Nếu có từ 2 tín hiệu lan truyền trở lên: thêm 1 bullet CUỐI CÙNG (tối đa 1–2 câu) bắt đầu bằng "**Tổng hợp:**" tóm tắt áp lực chung (tăng/giảm/hỗn hợp) lên EUA trong phiên — không liệt kê lại từng tín hiệu đã nêu.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"5": {{"title": "Tín hiệu liên thị trường", "bullets": ["**Gas → EUA:** ...", "**Dầu/Crack spread → EUA:** ...", "**Tổng hợp:** ..."]}}}}"""
    return system, user


def _prompt_section7(news_text: str, target_date: str) -> tuple[str, str]:
    system = f"Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}

TIN TỨC LIÊN QUAN (đánh số [1], [2], ... — eua_ets, geopolitics):
{news_text}

YÊU CẦU: Viết MỤC 7 — QUAN ĐIỂM TRÁI CHIỀU ĐÁNG CHÚ Ý.
Quy tắc:
- CHỈ viết khi có quan điểm contrarian có cơ sở dữ liệu, dựa ĐÚNG vào tin tức đã đánh số ở trên — TUYỆT ĐỐI KHÔNG bịa quan điểm hay nguồn không có trong danh sách.
- Nếu có, trả về mảng "points" — MỖI quan điểm trái chiều là 1 phần tử RIÊNG, gồm:
    - "viewpoint": phân tích SÚC TÍCH, TRỰC TIẾP nhưng ĐỦ Ý (tối đa 4 câu NGẮN, mỗi ý 1 câu, không diễn giải dài dòng/lặp ý): (1) quan điểm consensus — đa số thị trường/nhà phân tích đang nghĩ gì; (2) quan điểm contrarian khác biệt ra sao; (3) luận điểm/bằng chứng cụ thể mà nguồn đưa ra để bảo vệ quan điểm trái chiều đó; (4) điều kiện/kịch bản nào sẽ khiến quan điểm contrarian này đúng thay vì consensus. "Đủ ý" nghĩa là đủ 4 nội dung trên, KHÔNG phải đủ số câu.
    - "source_index": số thứ tự [N] của tin tức ở trên đã dùng làm căn cứ — PHẢI là số có thật trong danh sách đã đánh số, KHÔNG được bịa số khác.
  Nếu có nhiều quan điểm trái chiều đáng chú ý, liệt kê đủ thành nhiều phần tử trong "points" (không giới hạn 1 phần tử).
- Nếu KHÔNG có quan điểm contrarian có cơ sở nào trong tin tức đã cho: "has_content" = false, "points" = [], "text" = "Không có quan điểm trái chiều có cơ sở trong kỳ này."

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"7": {{"title": "Quan điểm trái chiều đáng chú ý", "has_content": true/false, "points": [{{"viewpoint": "...", "source_index": 1}}], "text": "..."}}}}"""
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




def _prompt_biz_recommendation(news_text: str, prices_text: str, eua_trend: str, target_date: str) -> tuple[str, str]:
    system = f"Bạn là chuyên gia tư vấn kinh doanh về carbon và năng lượng cho doanh nghiệp Việt Nam (SIM).\n{CONCISENESS_RULE}"
    user = f"""Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ:
{prices_text}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

TIN TỨC ĐA CHIỀU:
{news_text}

YÊU CẦU: Viết MỤC GỢI Ý KINH DOANH & GIẢI PHÁP CHO SIM — trình bày dưới dạng BẢNG (mỗi gợi ý là 1 HÀNG với các CỘT tách bạch, KHÔNG viết gộp thành 1 câu văn dài).
Gồm 2 bảng:
A. "short_term": mảng object, gợi ý ngắn hạn gắn TRỰC TIẾP với tin quan trọng/cập nhật mới nhất trong ngày. Mỗi object gồm ĐÚNG 3 trường, MỖI TRƯỜNG TỐI ĐA 1 CÂU NGẮN GỌN (như 1 ô trong bảng, không viết thành đoạn văn):
   - "trigger": tình huống/tin tức cụ thể kích hoạt gợi ý này (nêu rõ số liệu/sự kiện, không viết chung chung).
   - "action": hành động cụ thể SIM nên làm, khả thi và thực tế.
   - "reason": lý do vì sao hành động này hợp lý, gắn với chuỗi nhân quả đã phân tích ở các mục trên.
B. "long_term": mảng object, gợi ý dài hạn rút ra từ cơ hội phân tích (chính sách CBAM, VCM, chuyển dịch năng lượng...). Mỗi object gồm ĐÚNG 3 trường, MỖI TRƯỜNG TỐI ĐA 1 CÂU NGẮN GỌN:
   - "opportunity": cơ hội/xu hướng dài hạn cụ thể đã xác định được.
   - "solution": giải pháp/hướng đi đề xuất cho SIM để tận dụng cơ hội đó.
   - "expectation": kỳ vọng/kết quả nếu triển khai giải pháp này.

QUY TẮC SỐ LƯỢNG: chỉ đưa vào gợi ý THỰC SỰ có căn cứ từ tin tức/dữ liệu ở trên — TUYỆT ĐỐI KHÔNG bịa thêm cho đủ số dòng. Nếu 1 bảng không có gợi ý nào đủ căn cứ, để mảng đó rỗng.
Lưu ý: KHÔNG dùng câu lệnh mua/bán tài chính trực tiếp.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"biz": {{"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": [{{"trigger": "...", "action": "...", "reason": "..."}}], "long_term": [{{"opportunity": "...", "solution": "...", "expectation": "..."}}]}}}}"""
    return system, user


# ─────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────

async def generate_report_content(session: AsyncSession, target_date: str) -> Dict[str, Any]:
    """
    Sinh nội dung báo cáo bằng cách gọi LLM riêng cho từng mục.
    Mỗi mục chỉ nhận đúng những topic tin tức liên quan.
    """
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
        await get_previous_report_events(session, target_date), target_date
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

    # Mục 7 cần trích dẫn nguồn có thể bấm link — đánh số tin tức trước, LLM chỉ
    # được chọn số thứ tự, backend tự map số đó sang URL thật (tránh bịa link).
    section7_news_text, section7_index_lookup = _filter_news_with_index(news_by_topic, "7")
    # Mục 2 (market_drivers): tag "FACT" dựa trên tin tức cũng cần trích nguồn cụ
    # thể — dùng cùng cơ chế đánh số [N] để LLM chỉ chọn số có thật, backend map
    # sang URL thật (tránh bịa nguồn).
    section2_news_text, section2_index_lookup = _filter_news_with_index(news_by_topic, "2")
    # Mục 1 (Tóm tắt điều hành) và Mục 4 (Cập nhật tín chỉ carbon & CBAM): mỗi
    # bullet dựa trên tin tức cũng cần hiện tên nguồn (giống Mục 6) — cùng cơ chế
    # đánh số [N], LLM chỉ chọn số có thật, backend tự map sang tên/URL nguồn thật.
    section1_news_text, section1_index_lookup = _filter_news_with_index(news_by_topic, "1")
    section4_news_text, section4_index_lookup = _filter_news_with_index(news_by_topic, "4")

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
        ("5", _prompt_section5(
            _filter_news_for_section(news_by_topic, "5"),
            prices_text, gasoil_crack_spread, target_date, _topics_present(news_by_topic, "5"),
            overrides=eua_framework_overrides,
        )),
        ("7", _prompt_section7(
            section7_news_text,
            target_date
        )),
        ("8", _prompt_section8(
            _filter_news_for_section(news_by_topic, "8"),
            prev_events_text, target_date, recurring_events_text,
        )),
        ("biz", _prompt_biz_recommendation(
            _filter_news_for_section(news_by_topic, "biz"),
            prices_text, eua_trend, target_date
        )),
    ]

    FALLBACKS: Dict[str, dict] = {
        "1": {"title": "Tóm tắt điều hành", "bullets": [{"text": "Không thể sinh nội dung tự động.", "source_index": None}]},
        "2": {"market_drivers": {"bullish": [], "bearish": []}},
        "3": {"title": "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA",
              "analysis_blocks": [{"heading": "Diễn biến chính", "content": "Không có dữ liệu."}],
              "trading_scenarios": []},
        "4": {"title": "Cập nhật tín chỉ carbon & CBAM", "bullets": [{"text": "Không có diễn biến trọng yếu.", "source_index": None}]},
        "5": {"title": "Tín hiệu liên thị trường", "bullets": ["Không có tín hiệu liên thị trường mới."]},
        "7": {"title": "Quan điểm trái chiều đáng chú ý", "has_content": False, "points": [], "text": "Không có quan điểm trái chiều có cơ sở trong kỳ này."},
        "8": {"title": "Lịch sự kiện 7 ngày tới", "events": []},
        "biz": {"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": [], "long_term": []},
    }

    sem = asyncio.Semaphore(1)

    async def _process_section(sec_key: str, system_prompt: str, user_prompt: str):
        async with sem:
            logger.info(f"[REPORT] Đang sinh mục {sec_key}...")
            # Tạo khoảng trễ giữa các request liên tiếp để giảm tải rate limit
            await asyncio.sleep(3)
            raw = await _call_llm(
                user_prompt, system=system_prompt,
                max_tokens=SECTION_MAX_TOKENS.get(sec_key, 8192),
            )
            parsed = _extract_json(raw) if raw else None

            if parsed and sec_key in parsed:
                sec_data = parsed[sec_key]
                logger.info(f"[REPORT] Mục {sec_key} OK.")
            else:
                logger.warning(f"[REPORT] Mục {sec_key} thất bại, dùng fallback. Raw: {raw[:200] if raw else 'None'}")
                sec_data = FALLBACKS[sec_key]

            return sec_key, sec_data

    tasks = [_process_section(k, sys_p, usr_p) for k, (sys_p, usr_p) in SECTIONS]
    results = await asyncio.gather(*tasks)

    section2_data: dict = FALLBACKS["2"]
    for section_key, section_data in results:
        if section_key == "7":
            if section_data.get("has_content") and section_data.get("points"):
                resolved_points = []
                for pt in section_data["points"]:
                    src_art = section7_index_lookup.get(pt.get("source_index"))
                    resolved_points.append({
                        "viewpoint": pt.get("viewpoint", ""),
                        "source_name": src_art["source"] if src_art else None,
                        "source_url": src_art["url"] if src_art else None,
                    })
                content["7"] = {**section_data, "points": resolved_points}
        elif section_key == "2":
            section2_data = section_data
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
        else:
            content[section_key] = section_data

    def _resolve_driver_items(items: List[dict]) -> List[dict]:
        resolved = []
        for it in items or []:
            src_art = section2_index_lookup.get(it.get("source_index"))
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

    return content
