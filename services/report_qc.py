"""Lucy — QC báo cáo ngày, chạy dưới dạng tool `lucy_qc` của Jenny chat (chỉ admin —
xem services/quote_chat.py::ADMIN_TOOLS). Toàn bộ là check RULE-BASED bằng Python, KHÔNG gọi
LLM: đối chiếu nội dung JSON báo cáo (`reports.content`) với dữ liệu thật trong DB / lịch cố định.

    check       section(s)   nguồn sự thật
    price       "2"          bảng `prices` (giá chốt phiên)
    scenario    "3"          logic + schema trading_scenarios / dòng "**Tổng hợp:**"
    source      "1","4","9"  bảng `articles` (URL nguồn có thật)
    calendar    "8"          lịch định kỳ EIA/Baker Hughes (_compute_recurring_calendar_events)
    biz         "biz"        schema gợi ý + `biz_suggestions.trigger_rule`
    news        "6",...      bảng `articles` trong khung tin của báo cáo (số lượng, độ phủ)

Mỗi hàm `check_*` là hàm THUẦN (nhận content + dữ liệu đã truy vấn sẵn, trả list issue) để
test được không cần DB — `run_report_qc()` lo phần truy vấn, `qc_report_text()` dựng văn bản
kết quả trả cho Jenny. Mỗi issue: {"check", "section", "severity": "error"|"warning"|"info",
"message", "field_path"}.

Bảng `report_qc_results` (bản QC dạng nút bấm trước đây) không còn được ghi — giữ lại để
không mất lịch sử / không phá chuỗi migration đã chạy.
"""
import logging
from collections import Counter
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Article, BizSuggestion, Instrument, Price, Report
from services import biz_memory
from services.report_generator import (
    CBAM_CODE,
    SECTION_TOPICS,
    TOPIC_DISPLAY_LABELS,
    _compute_recurring_calendar_events,
    _parse_event_date,
    is_no_price_report,
    report_data_date,
)

logger = logging.getLogger(__name__)

CHECKS = ("price", "scenario", "source", "calendar", "biz", "news")
CHECK_LABELS = {
    "price": "Giá số liệu", "scenario": "Kịch bản giao dịch", "source": "Nguồn tin",
    "calendar": "Lịch sự kiện", "biz": "Gợi ý kinh doanh", "news": "Độ phủ tin tức",
}
SECTION_LABELS = {
    "1": "Tóm tắt điều hành", "2": "Bảng giá nhanh", "3": "Phân tích & kịch bản giao dịch",
    "4": "Diễn biến chính / Tín chỉ carbon & CBAM", "6": "Tin tức chi tiết", "8": "Lịch sự kiện",
    "9": "Nguồn tham khảo", "biz": "Gợi ý kinh doanh",
}
SEVERITY_LABELS = {"error": "LỖI", "warning": "CẢNH BÁO", "info": "GỢI Ý"}
SEVERITY_PENALTY = {"error": 25, "warning": 10, "info": 2}

# Sai số cho phép khi so giá báo cáo với DB — giá DB lưu float, báo cáo hiển thị 4 số lẻ.
PRICE_ABS_TOLERANCE = 0.01
PRICE_REL_TOLERANCE = 0.0005
PCT_TOLERANCE = 0.05  # điểm phần trăm

VALID_HORIZONS = ("ngắn hạn", "trung hạn", "dài hạn")
VALID_PROBABILITIES = ("Cao", "Trung bình", "Thấp")
VALID_DIRECTIONS = ("tăng", "giảm", "đi ngang")
SCENARIO_REQUIRED_FIELDS = (
    "horizon", "probability", "direction", "condition", "price_zone", "key_risk", "trading_strategy",
)
VALID_IMPACTS = ("Cao", "Trung", "Thấp")

# Khớp _prompt_section3 (report_generator.py) và EUA_SENTIMENT_RE bên frontend.
EUA_SUMMARY_RE = re.compile(r"\*\*Tổng hợp\s*:?\*\*(.*)")
EUA_SENTIMENT_RE = re.compile(r"^\s*\[\s*(TÍCH CỰC|TRUNG LẬP|TIÊU CỰC)\s*\]\s*", re.IGNORECASE)
# Chiều giá "ngược" với nhãn Tổng hợp — kịch bản ngắn hạn đi theo chiều này là mâu thuẫn.
SENTIMENT_OPPOSITE_DIRECTION = {"TÍCH CỰC": "giảm", "TIÊU CỰC": "tăng"}
SUMMARY_WORDS_MIN, SUMMARY_WORDS_MAX = 30, 60  # prompt yêu cầu 40–45 chữ, nới biên để tránh báo nhiễu

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PCT_PREFIX_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%")
# Entry kịch bản ngắn hạn lệch quá xa giá EUA hiện tại → nhiều khả năng LLM nhầm số.
ENTRY_MAX_DISTANCE = 0.15
# Lịch: sự kiện đã qua (có kết quả) được giữ lại — chấp nhận ngày định kỳ tới 4 tuần trước.
CALENDAR_LOOKBACK_WEEKS = 4

# Trigger_rule lệch quá xa giá hiện tại → nhiều khả năng LLM nhầm đơn vị/mã.
BIZ_RULE_MAX_DISTANCE = 0.5

# get_news_for_report() chỉ lấy NEWS_GENERATOR_LIMIT bài crawl mới nhất trong khung tin.
NEWS_GENERATOR_LIMIT = 100
# Chủ đề có từ ngần này bài trở lên mà báo cáo không trích bài nào → gợi ý xem lại.
NEWS_TOPIC_UNCITED_MIN = 3


def _issue(check: str, section: str, severity: str, message: str, field_path: str) -> Dict[str, str]:
    return {"check": check, "section": section, "severity": severity, "message": message, "field_path": field_path}


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _close_enough(a: float, b: float) -> bool:
    return abs(a - b) <= max(PRICE_ABS_TOLERANCE, PRICE_REL_TOLERANCE * abs(b))


def _parse_price_number(raw: Any) -> Optional[float]:
    """"81.4400 EUR/tCO2" -> 81.44 (field "price" hiển thị trong bảng giá)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return float(raw.split(" ")[0].replace(",", ""))
    except ValueError:
        return None


def _fmt(n: float) -> str:
    return f"{n:,.2f}"


# ── 1. Giá số liệu (Mục 2) ───────────────────────────────────────────


def report_price_date(content: Dict[str, Any]) -> Optional[str]:
    """Ngày giá báo cáo đang dùng — rút từ "price_timestamp" ("Giá chốt phiên 2026-08-20 (...)")."""
    m = DATE_RE.search((content.get("2") or {}).get("price_timestamp") or "")
    return m.group(0) if m else None


def is_no_price_content(content: Dict[str, Any]) -> bool:
    """Báo cáo sinh ở chế độ chỉ tin tức (Chủ Nhật/Thứ Hai — xem report_generator.is_no_price_report)."""
    return bool((content.get("2") or {}).get("no_price_data"))


def check_no_price_leaks(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Báo cáo không có phiên giá KHÔNG được còn sót phần nào liên quan giá."""
    issues: List[Dict[str, str]] = []
    sec2 = content.get("2") or {}
    drivers = sec2.get("market_drivers") or {}
    leaks = (
        (sec2.get("prices"), "2", "bảng giá", "2.prices"),
        (sec2.get("chart_data"), "2", "biểu đồ EUA", "2.chart_data"),
        ((drivers.get("bullish") or []) + (drivers.get("bearish") or []), "2", "động lực thị trường", "2.market_drivers"),
        (sec2.get("price_timestamp"), "2", "dòng ngày chốt giá", "2.price_timestamp"),
        (sec2.get("key_facts"), "2", "số liệu EUA", "2.key_facts"),
        ((content.get("3") or {}).get("trading_scenarios"), "3", "kịch bản giao dịch", "3.trading_scenarios"),
    )
    for value, section, label, path in leaks:
        if value:
            issues.append(_issue(
                "price" if section == "2" else "scenario", section, "error",
                f"Báo cáo ngày không có phiên giao dịch nhưng vẫn có {label}.", path,
            ))
    return issues


def check_prices(
    content: Dict[str, Any],
    db_prices: Dict[str, Dict[str, Any]],
    latest_price_date: Optional[str],
    report_date: Optional[str] = None,
) -> List[Dict[str, str]]:
    """`db_prices`: code -> {"name", "close", "day_change_pct", "week_change_pct"} của ĐÚNG
    ngày giá báo cáo dùng (không gồm CBAM — giá chốt theo quý, không so theo phiên).
    `latest_price_date`: ngày giá mới nhất trong DB tính tới ngày dữ liệu báo cáo.
    Báo cáo Chủ Nhật/Thứ Hai (không có phiên): chỉ kiểm tra KHÔNG còn phần giá nào."""
    if is_no_price_content(content):
        return [it for it in check_no_price_leaks(content) if it["check"] == "price"]
    issues: List[Dict[str, str]] = []
    sec = content.get("2") or {}
    rows = sec.get("prices") or []
    if report_date and is_no_price_report(report_date):
        issues.append(_issue(
            "price", "2", "warning",
            "Báo cáo Chủ Nhật/Thứ Hai (ngày dữ liệu cuối tuần, không có phiên) nhưng vẫn có phần giá — "
            "giá đang hiển thị là của phiên trước đó. Sinh lại báo cáo để dùng chế độ chỉ phân tích tin tức.",
            "2.prices",
        ))

    if not rows:
        return [_issue("price", "2", "error", "Bảng giá nhanh trống.", "2.prices")]

    price_date = report_price_date(content)
    if not price_date:
        issues.append(_issue("price", "2", "info", "Không đọc được ngày chốt giá trong price_timestamp.", "2.price_timestamp"))
    elif latest_price_date and price_date < latest_price_date:
        issues.append(_issue(
            "price", "2", "warning",
            f"Bảng giá dùng phiên {price_date} nhưng DB đã có giá phiên {latest_price_date} — có thể đã cũ.",
            "2.price_timestamp",
        ))

    if not db_prices:
        issues.append(_issue(
            "price", "2", "warning",
            f"DB không có giá phiên {price_date or '?'} để đối chiếu.", "2.prices",
        ))
        return issues

    seen_codes: Set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").upper()
        name = row.get("name") or code or f"dòng {i + 1}"
        path = f"2.prices[{i}]"
        if code == CBAM_CODE:
            continue
        seen_codes.add(code)
        db = db_prices.get(code)
        if db is None:
            issues.append(_issue("price", "2", "warning", f"{name}: mã {code or '(trống)'} không có giá trong DB phiên này.", path))
            continue

        close = row.get("close")
        if not isinstance(close, (int, float)):
            issues.append(_issue("price", "2", "error", f"{name}: thiếu giá đóng cửa (close).", f"{path}.close"))
        elif not _close_enough(float(close), db["close"]):
            issues.append(_issue(
                "price", "2", "error",
                f"Giá {name} ghi {_fmt(close)} nhưng thực tế DB là {_fmt(db['close'])}.", f"{path}.close",
            ))

        shown = _parse_price_number(row.get("price"))
        if shown is not None and not _close_enough(shown, db["close"]):
            issues.append(_issue(
                "price", "2", "error",
                f"Giá hiển thị của {name} là {_fmt(shown)} nhưng DB là {_fmt(db['close'])}.", f"{path}.price",
            ))

        for field, text_field, label in (("day_change_pct", "dday", "Δ ngày"), ("week_change_pct", "dweek", "Δ tuần")):
            got, want = row.get(field), db.get(field)
            if want is None:
                continue
            if not isinstance(got, (int, float)):
                issues.append(_issue("price", "2", "info", f"{name}: thiếu {label} (DB có {want:+.2f}%).", f"{path}.{field}"))
            elif abs(got - want) > PCT_TOLERANCE:
                issues.append(_issue(
                    "price", "2", "warning",
                    f"{name}: {label} ghi {got:+.2f}% nhưng DB là {want:+.2f}%.", f"{path}.{field}",
                ))
            # Chuỗi hiển thị ("+2.34% (+1.05)") — thứ người đọc thực sự thấy, có thể bị sửa tay lệch số.
            m = PCT_PREFIX_RE.match(str(row.get(text_field) or ""))
            if m and abs(float(m.group(1)) - want) > PCT_TOLERANCE:
                issues.append(_issue(
                    "price", "2", "warning",
                    f"{name}: {label} hiển thị {m.group(1)}% nhưng DB là {want:+.2f}%.", f"{path}.{text_field}",
                ))

        if isinstance(close, (int, float)) and isinstance(row.get("day_change_pct"), (int, float)):
            if bool(row.get("up")) != (row["day_change_pct"] > 0):
                issues.append(_issue("price", "2", "info", f"{name}: cờ tăng/giảm (up) ngược dấu với Δ ngày.", f"{path}.up"))

    for code, db in db_prices.items():
        if code not in seen_codes:
            issues.append(_issue("price", "2", "warning", f"Thiếu {db.get('name') or code} trong bảng giá (DB có giá phiên này).", "2.prices"))

    # Nến cuối của biểu đồ EUA phải khớp giá EUA trong bảng (cùng phiên).
    chart = sec.get("chart_data") or []
    eua_db = db_prices.get("EUA")
    if chart and eua_db and price_date and chart[-1].get("date") == price_date:
        last_close = chart[-1].get("close")
        if isinstance(last_close, (int, float)) and not _close_enough(float(last_close), eua_db["close"]):
            issues.append(_issue(
                "price", "2", "warning",
                f"Nến cuối biểu đồ EUA ({_fmt(last_close)}) lệch giá EUA trong DB ({_fmt(eua_db['close'])}).",
                f"2.chart_data[{len(chart) - 1}].close",
            ))
    return issues


# ── 2. Kịch bản giao dịch (Mục 3) ────────────────────────────────────


def extract_eua_summary(content: Dict[str, Any]) -> Optional[str]:
    """Nội dung sau "**Tổng hợp:**" trong analysis_blocks (cùng logic extractEuaSummary bên frontend)."""
    for block in (content.get("3") or {}).get("analysis_blocks") or []:
        if not isinstance(block, dict):
            continue
        for line in str(block.get("content") or "").split("\n"):
            m = EUA_SUMMARY_RE.search(line)
            if m:
                return m.group(1).strip()
    return None


# Port 1-1 của parseStrategy/priceNums/splitBuySell/parseSignalLevels trong
# frontend/src/components/ReportDocument.tsx — QC kiểm ĐÚNG những mức giá mà thẻ
# "KHUYẾN NGHỊ VỊ THẾ" sẽ hiển thị. Sửa parser bên frontend thì phải sửa cả ở đây.
_STRATEGY_LINE_RE = re.compile(r"^\*\*([^*]+?)\*\*\s*:?\s*(.*)$")


def parse_strategy(text: Any) -> Optional[List[tuple]]:
    """"**Entry:** ...\n**Mục tiêu:** ..." -> [(label, body)], None nếu có dòng không đúng định dạng."""
    if not isinstance(text, str) or not text.strip():
        return None
    parts = []
    for line in (l.strip() for l in re.split(r"\n+", text)):
        if not line:
            continue
        m = _STRATEGY_LINE_RE.match(line)
        if not m:
            return None
        parts.append((re.sub(r":\s*$", "", m.group(1)).strip(), m.group(2).strip()))
    return parts or None


def _price_nums(text: str) -> List[float]:
    clean = text.replace("**", "")
    clean = re.sub(r"EUR\s*/\s*tCO[₂2]", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\d+(?:[.,]\d+)?\s*(?:%|phiên|ngày|tuần|tháng)", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"tCO[₂2]", "", clean, flags=re.IGNORECASE)
    nums = [float(m.replace(",", ".", 1)) for m in re.findall(r"\d+(?:[.,]\d+)?", clean)]
    return [n for n in nums if n >= 10]


def _split_buy_sell(body: str) -> tuple:
    buy: List[float] = []
    sell: List[float] = []
    free: List[float] = []
    for c in (x.strip() for x in re.split(r";|,\s+|\.\s+|\s/\s", body)):
        nums = _price_nums(c) if c else []
        if not nums:
            continue
        is_buy, is_sell = bool(re.search("mua", c, re.I)), bool(re.search("bán", c, re.I))
        if is_buy and not is_sell:
            buy += nums
        elif is_sell and not is_buy:
            sell += nums
        else:
            free += nums
    return buy, sell, free


def parse_signal_levels(direction: str, entry_body: str, target_body: str, risk_body: str) -> Dict[str, Any]:
    e_buy, e_sell, e_free = _split_buy_sell(entry_body)
    entry_buy, entry_sell = e_buy[:2], e_sell[:2]
    if not entry_buy and not entry_sell and e_free:
        if direction == "giảm":
            entry_sell = e_free[:2]
        else:
            entry_buy = e_free[:2]

    t_buy, t_sell, t_free = _split_buy_sell(target_body)
    target_buy = t_buy[0] if t_buy else None
    target_sell = t_sell[0] if t_sell else None
    if target_buy is None and target_sell is None and t_free:
        if direction == "giảm":
            target_sell = t_free[0]
        elif direction == "tăng":
            target_buy = t_free[0]
        else:
            target_buy = t_free[0]
            target_sell = t_free[1] if len(t_free) > 1 else None

    def _num(m):
        return float(m.group(1).replace(",", ".", 1)) if m else None

    stop_lower = _num(re.search(r"dưới[^\d]{0,15}(\d+(?:[.,]\d+)?)", risk_body, re.I))
    stop_upper = _num(re.search(r"trên[^\d]{0,15}(\d+(?:[.,]\d+)?)", risk_body, re.I))
    if stop_lower is None and stop_upper is None:
        nums = _price_nums(risk_body)
        if nums:
            if direction == "tăng":
                stop_lower = min(nums)
            elif direction == "giảm":
                stop_upper = max(nums)
            else:
                stop_lower = min(nums)
                if len(nums) > 1:
                    stop_upper = max(nums)
    return {
        "entry_buy": entry_buy, "entry_sell": entry_sell, "target_buy": target_buy,
        "target_sell": target_sell, "stop_lower": stop_lower, "stop_upper": stop_upper,
    }


def _eua_last_close(content: Dict[str, Any]) -> Optional[float]:
    """Giá EUA "hiện tại" — như frontend: close nến cuối chart_data, không có thì dòng EUA bảng giá."""
    sec2 = content.get("2") or {}
    chart = sec2.get("chart_data") or []
    if chart and isinstance(chart[-1], dict) and isinstance(chart[-1].get("close"), (int, float)):
        return float(chart[-1]["close"])
    row = next((p for p in sec2.get("prices") or [] if isinstance(p, dict) and str(p.get("code", "")).upper() == "EUA"), None)
    return float(row["close"]) if row and isinstance(row.get("close"), (int, float)) else None


def _check_strategy(sc: Dict[str, Any], label: str, path: str, eua_close: Optional[float]) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    is_short = sc.get("horizon") == "ngắn hạn"
    parts = parse_strategy(sc.get("trading_strategy"))
    if parts is None:
        issues.append(_issue(
            "scenario", "3", "warning",
            f"{label}: chiến lược không đúng định dạng các dòng \"**Entry:**\" / \"**Mục tiêu:**\" / \"**Quản trị rủi ro:**\""
            + (" — thẻ KHUYẾN NGHỊ VỊ THẾ sẽ không tách được mức giá." if is_short else "."),
            f"{path}.trading_strategy",
        ))
        return issues

    def _body(pattern: str) -> Optional[str]:
        return next((b.replace("*", "") for lbl, b in parts if re.search(pattern, lbl, re.I)), None)

    bodies = {"Entry": _body("entry"), "Mục tiêu": _body("mục tiêu"), "Quản trị rủi ro": _body("quản trị rủi ro|cắt lỗ|stop")}
    missing = [k for k, v in bodies.items() if v is None]
    if missing:
        issues.append(_issue("scenario", "3", "warning", f"{label}: chiến lược thiếu dòng {', '.join(missing)}.", f"{path}.trading_strategy"))
        return issues

    direction = sc.get("direction")
    if direction not in ("tăng", "giảm"):
        return issues
    lv = parse_signal_levels(direction, bodies["Entry"], bodies["Mục tiêu"], bodies["Quản trị rủi ro"])
    long = direction == "tăng"
    entry_range = lv["entry_buy"] if long else lv["entry_sell"]
    target = lv["target_buy"] if long else lv["target_sell"]
    stop = lv["stop_lower"] if long else lv["stop_upper"]
    if not entry_range:
        issues.append(_issue("scenario", "3", "info", f"{label}: không tách được mức giá Entry ({'mua' if long else 'bán'}).", f"{path}.trading_strategy"))
        return issues
    entry = sum(entry_range) / len(entry_range)
    side = "mua (tăng)" if long else "bán (giảm)"
    if target is None or stop is None:
        if is_short:
            issues.append(_issue(
                "scenario", "3", "info",
                f"{label}: không tách được mức {'Mục tiêu' if target is None else 'Cắt lỗ'} — thẻ KHUYẾN NGHỊ VỊ THẾ sẽ thiếu thang giá.",
                f"{path}.trading_strategy",
            ))
    else:
        if (target - entry if long else entry - target) <= 0:
            issues.append(_issue(
                "scenario", "3", "warning",
                f"{label}: Mục tiêu {_fmt(target)} nằm sai phía so với Entry {_fmt(entry)} cho lệnh {side}.",
                f"{path}.trading_strategy",
            ))
        if (entry - stop if long else stop - entry) <= 0:
            issues.append(_issue(
                "scenario", "3", "warning",
                f"{label}: Cắt lỗ {_fmt(stop)} nằm sai phía so với Entry {_fmt(entry)} cho lệnh {side}.",
                f"{path}.trading_strategy",
            ))
    if is_short and eua_close and abs(entry - eua_close) / eua_close > ENTRY_MAX_DISTANCE:
        issues.append(_issue(
            "scenario", "3", "warning",
            f"{label}: Entry {_fmt(entry)} lệch >{int(ENTRY_MAX_DISTANCE * 100)}% so với giá EUA hiện tại {_fmt(eua_close)}.",
            f"{path}.trading_strategy",
        ))
    return issues


def check_scenarios(content: Dict[str, Any]) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    sec = content.get("3") or {}
    scenarios = sec.get("trading_scenarios") or []
    no_price = is_no_price_content(content)
    if no_price:
        issues += [it for it in check_no_price_leaks(content) if it["check"] == "scenario"]

    summary = extract_eua_summary(content)
    sentiment: Optional[str] = None
    if summary is None:
        no_info = any(
            "Không có thông tin mới" in str(b) for b in sec.get("analysis_blocks") or []
        )
        if any("Tổng hợp" in str(b) for b in sec.get("analysis_blocks") or []):
            issues.append(_issue(
                "scenario", "3", "warning",
                "Có dòng \"Tổng hợp\" nhưng sai định dạng (cần đúng \"**Tổng hợp:**\") — khung NHẬN ĐỊNH TỔNG QUAN đầu báo cáo sẽ không hiện.",
                "3.analysis_blocks",
            ))
        elif not no_info:
            issues.append(_issue("scenario", "3", "warning", "Thiếu dòng \"**Tổng hợp:**\" kết luận chiều giá EUA — khung NHẬN ĐỊNH TỔNG QUAN sẽ không hiện.", "3.analysis_blocks"))
    else:
        m = EUA_SENTIMENT_RE.match(summary)
        if not m:
            issues.append(_issue(
                "scenario", "3", "warning",
                "Dòng Tổng hợp thiếu nhãn [TÍCH CỰC]/[TRUNG LẬP]/[TIÊU CỰC] — khung Nhận định sẽ mặc định trung lập.",
                "3.analysis_blocks",
            ))
            body = summary
        else:
            sentiment = m.group(1).upper()
            body = summary[m.end():]
        n_words = len(body.split())
        if not SUMMARY_WORDS_MIN <= n_words <= SUMMARY_WORDS_MAX:
            issues.append(_issue("scenario", "3", "info", f"Dòng Tổng hợp dài {n_words} chữ (yêu cầu 40–45).", "3.analysis_blocks"))

    if no_price:
        return issues  # không có phiên giá → không có kịch bản giao dịch (đã báo lỗi nếu còn sót ở trên)
    if not scenarios:
        issues.append(_issue("scenario", "3", "error", "Không có kịch bản giao dịch nào.", "3.trading_scenarios"))
        return issues

    eua_close = _eua_last_close(content)
    seen_horizons: Set[str] = set()
    for i, sc in enumerate(scenarios):
        path = f"3.trading_scenarios[{i}]"
        if not isinstance(sc, dict):
            issues.append(_issue("scenario", "3", "error", f"Kịch bản {i + 1}: sai định dạng.", path))
            continue
        label = f"Kịch bản {sc.get('horizon') or i + 1}"
        missing = [f for f in SCENARIO_REQUIRED_FIELDS if _blank(sc.get(f))]
        if missing:
            issues.append(_issue("scenario", "3", "error", f"{label}: thiếu {', '.join(missing)}.", path))

        horizon = sc.get("horizon")
        if horizon and horizon not in VALID_HORIZONS:
            issues.append(_issue("scenario", "3", "warning", f"{label}: horizon \"{horizon}\" không hợp lệ.", f"{path}.horizon"))
        elif horizon in seen_horizons:
            issues.append(_issue("scenario", "3", "warning", f"Trùng kịch bản \"{horizon}\" — chỉ 1 kịch bản được hiển thị.", f"{path}.horizon"))
        if horizon:
            seen_horizons.add(horizon)

        prob = sc.get("probability")
        if prob and prob not in VALID_PROBABILITIES:
            issues.append(_issue("scenario", "3", "warning", f"{label}: xác suất \"{prob}\" không thuộc Cao/Trung bình/Thấp.", f"{path}.probability"))
        direction = sc.get("direction")
        if direction and direction not in VALID_DIRECTIONS:
            issues.append(_issue("scenario", "3", "error", f"{label}: chiều giá \"{direction}\" không thuộc tăng/giảm/đi ngang.", f"{path}.direction"))

        if not _blank(sc.get("trading_strategy")):
            issues += _check_strategy(sc, label, path, eua_close)

    # Prompt Mục 3 yêu cầu ĐÚNG 3 kịch bản, đủ cả 3 horizon.
    for horizon in VALID_HORIZONS:
        if horizon not in seen_horizons:
            note = " — khối KHUYẾN NGHỊ VỊ THẾ sẽ bị ẩn" if horizon == "ngắn hạn" else ""
            issues.append(_issue("scenario", "3", "warning", f"Thiếu kịch bản \"{horizon}\"{note}.", "3.trading_scenarios"))

    short = next((sc for sc in scenarios if isinstance(sc, dict) and sc.get("horizon") == "ngắn hạn"), None)
    if short and sentiment:
        direction, prob = short.get("direction"), short.get("probability")
        if direction == SENTIMENT_OPPOSITE_DIRECTION.get(sentiment):
            issues.append(_issue(
                "scenario", "3", "error" if prob == "Cao" else "warning",
                f"Tổng hợp [{sentiment}] nhưng kịch bản ngắn hạn = {direction} (xác suất {prob or '?'}).",
                "3.trading_scenarios",
            ))
        elif sentiment == "TRUNG LẬP" and direction in ("tăng", "giảm") and prob == "Cao":
            issues.append(_issue(
                "scenario", "3", "info",
                f"Tổng hợp [TRUNG LẬP] nhưng kịch bản ngắn hạn {direction} với xác suất Cao.",
                "3.trading_scenarios",
            ))
    return issues


# ── 3. Nguồn tin (Mục 1, 4, 9) ───────────────────────────────────────


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/")


def _market_drivers(sec2: Dict[str, Any]) -> List[Any]:
    drivers = sec2.get("market_drivers") or {}
    return list(drivers.get("bullish") or []) + list(drivers.get("bearish") or []) if isinstance(drivers, dict) else []


def collect_source_urls(content: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    for key in ("1", "4"):
        for b in (content.get(key) or {}).get("bullets") or []:
            if isinstance(b, dict) and b.get("source_url"):
                urls.append(b["source_url"])
    sec2 = content.get("2") or {}
    for d in (sec2.get("key_developments") or []) + _market_drivers(sec2):
        if isinstance(d, dict) and d.get("source_url"):
            urls.append(d["source_url"])
    for it in (content.get("9") or {}).get("items") or []:
        if isinstance(it, dict) and it.get("url"):
            urls.append(it["url"])
    return urls


def check_sources(content: Dict[str, Any], known_urls: Iterable[str]) -> List[Dict[str, str]]:
    """`known_urls`: những URL trong `collect_source_urls(content)` CÓ tồn tại ở bảng articles."""
    known = {_norm_url(u) for u in known_urls}
    issues: List[Dict[str, str]] = []

    def _check_url(url: Optional[str], section: str, label: str, path: str) -> None:
        if url and _norm_url(url) not in known:
            issues.append(_issue("source", section, "warning", f"{label}: URL nguồn không tìm thấy trong DB ({url}).", path))

    # Bullet KHÔNG có nguồn là hợp lệ (prompt Mục 1: source_index=null khi bullet dựa trên dữ
    # liệu giá, vd bullet giá EUA đầu tiên) — chỉ kiểm URL khi CÓ trích dẫn.
    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        if isinstance(b, dict):
            _check_url(b.get("source_url"), "1", f"Bullet {i + 1}", f"1.bullets[{i}].source_url")

    for i, b in enumerate((content.get("4") or {}).get("bullets") or []):
        if isinstance(b, dict):
            _check_url(b.get("source_url"), "4", f"Tín chỉ/CBAM #{i + 1}", f"4.bullets[{i}].source_url")
    # key_developments hiển thị chung khối "Diễn biến chính" với Mục 4 → gắn note vào "4".
    for i, d in enumerate((content.get("2") or {}).get("key_developments") or []):
        if isinstance(d, dict):
            _check_url(d.get("source_url"), "4", f"Diễn biến chính #{i + 1}", f"2.key_developments[{i}].source_url")
    drivers = (content.get("2") or {}).get("market_drivers") or {}
    for side, side_label in (("bullish", "hỗ trợ giá"), ("bearish", "gây áp lực giá")):
        for i, d in enumerate((drivers.get(side) if isinstance(drivers, dict) else None) or []):
            if isinstance(d, dict):
                _check_url(d.get("source_url"), "3", f"Yếu tố {side_label} #{i + 1}", f"2.market_drivers.{side}[{i}].source_url")

    items = (content.get("9") or {}).get("items") or []
    if not items:
        issues.append(_issue("source", "9", "warning", "Mục Nguồn tham khảo trống.", "9.items"))
    seen: Set[str] = set()
    for i, it in enumerate(items):
        url = it.get("url") if isinstance(it, dict) else None
        path = f"9.items[{i}]"
        if not url:
            issues.append(_issue("source", "9", "info", f"Nguồn #{i + 1}: thiếu URL.", path))
            continue
        if _norm_url(url) in seen:
            issues.append(_issue("source", "9", "info", f"Nguồn #{i + 1}: trùng URL với nguồn phía trên.", f"{path}.url"))
            continue
        seen.add(_norm_url(url))
        _check_url(url, "9", f"Nguồn #{i + 1}", f"{path}.url")
    return issues


# ── 4. Lịch sự kiện (Mục 8) ──────────────────────────────────────────


def _event_kind(name: str) -> Optional[str]:
    if "Baker Hughes" in name:
        return "baker_hughes"
    if "EIA" in name:
        return "eia"
    return None


EVENT_KIND_LABEL = {"eia": "Tồn kho EIA (thứ Tư giờ Mỹ)", "baker_hughes": "Baker Hughes (thứ Sáu giờ Mỹ)"}


def expected_recurring_dates(target_date: str) -> Dict[str, Set[str]]:
    """Ngày (giờ VN) hợp lệ của EIA/Baker Hughes từ CALENDAR_LOOKBACK_WEEKS tuần trước tới
    target+6 — tính bằng ĐÚNG hàm report_generator dùng khi sinh Mục 8 (đã quy đổi múi giờ +
    DST: Baker Hughes 12:00 CT thứ Sáu rơi vào rạng sáng thứ Bảy giờ VN). Nhìn lùi vài tuần vì
    sự kiện đã qua mà có kết quả vẫn được giữ trong Mục 8 (_finalize_section8_events)."""
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    expected: Dict[str, Set[str]] = {"eia": set(), "baker_hughes": set()}
    for weeks_back in range(CALENDAR_LOOKBACK_WEEKS, -1, -1):
        window_start = (target - timedelta(days=7 * weeks_back)).isoformat()
        for ev in _compute_recurring_calendar_events(window_start):
            kind = _event_kind(ev["event"])
            if kind:
                expected[kind].add(ev["date"])
    return expected


def check_calendar(content: Dict[str, Any], report_date: str) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    events = (content.get("8") or {}).get("events")
    if events is None:
        return [_issue("calendar", "8", "warning", "Mục Lịch sự kiện không có danh sách events.", "8.events")]

    target_date = report_data_date(report_date)
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    window_end = target + timedelta(days=6)
    expected = expected_recurring_dates(target_date)
    upcoming_expected = {
        kind: {d for d in dates if target <= date.fromisoformat(d) <= window_end} for kind, dates in expected.items()
    }
    found: Dict[str, Set[str]] = {"eia": set(), "baker_hughes": set()}
    seen_keys: Set[tuple] = set()

    for i, ev in enumerate(events):
        path = f"8.events[{i}]"
        if not isinstance(ev, dict):
            issues.append(_issue("calendar", "8", "warning", f"Sự kiện {i + 1}: sai định dạng.", path))
            continue
        name = str(ev.get("event") or "").strip()
        label = name or f"Sự kiện {i + 1}"
        if not name:
            issues.append(_issue("calendar", "8", "warning", f"Sự kiện {i + 1}: thiếu tên.", f"{path}.event"))
        ev_date = _parse_event_date(ev)
        if ev_date is None:
            issues.append(_issue("calendar", "8", "warning", f"{label}: thiếu/sai định dạng ngày (YYYY-MM-DD).", f"{path}.date"))
        else:
            if ev_date > window_end:
                issues.append(_issue("calendar", "8", "warning", f"{label}: ngày {ev_date.isoformat()} nằm ngoài cửa sổ 7 ngày.", f"{path}.date"))
            elif ev_date < target and _blank(ev.get("outcome")):
                issues.append(_issue("calendar", "8", "warning", f"{label}: đã qua ({ev_date.isoformat()}) nhưng chưa có kết quả.", f"{path}.outcome"))

            kind = _event_kind(name)
            if kind:
                if ev_date.isoformat() not in expected[kind]:
                    issues.append(_issue(
                        "calendar", "8", "error",
                        f"{label}: ngày {ev_date.strftime('%d/%m')} sai lịch — {EVENT_KIND_LABEL[kind]}.",
                        f"{path}.date",
                    ))
                found[kind].add(ev_date.isoformat())

            # Giao diện hiển thị NGÀY theo "datetime_vn" (EventTimeline), không theo "date".
            shown = str(ev.get("datetime_vn") or "")
            shown_dm = re.match(r"^\s*(\d{1,2})/(\d{1,2})", shown)
            shown_iso = DATE_RE.match(shown.strip())
            if shown_dm and (int(shown_dm.group(1)), int(shown_dm.group(2))) != (ev_date.day, ev_date.month):
                issues.append(_issue(
                    "calendar", "8", "warning",
                    f"{label}: ngày hiển thị \"{shown}\" khác ngày thật {ev_date.strftime('%d/%m')}.", f"{path}.datetime_vn",
                ))
            elif shown_iso and shown_iso.group(0) != ev_date.isoformat():
                issues.append(_issue(
                    "calendar", "8", "warning",
                    f"{label}: ngày hiển thị \"{shown}\" khác ngày thật {ev_date.strftime('%d/%m')}.", f"{path}.datetime_vn",
                ))

            key = (ev_date.isoformat(), kind or name.lower())
            if key in seen_keys:
                issues.append(_issue("calendar", "8", "warning", f"{label}: trùng sự kiện cùng ngày {ev_date.strftime('%d/%m')}.", path))
            seen_keys.add(key)

        if re.search(r"\bAPI\b|đấu giá EUA|EEX", name, re.IGNORECASE) and not _event_kind(name):
            issues.append(_issue("calendar", "8", "info", f"{label}: đã bỏ khỏi danh sách theo dõi định kỳ (không có nguồn dữ liệu).", path))
        impact = ev.get("impact")
        if impact and impact not in VALID_IMPACTS:
            issues.append(_issue("calendar", "8", "info", f"{label}: mức tác động \"{impact}\" không thuộc Cao/Trung/Thấp.", f"{path}.impact"))

    for kind, dates in upcoming_expected.items():
        for d in sorted(dates - found[kind]):
            issues.append(_issue(
                "calendar", "8", "warning",
                f"Thiếu sự kiện {EVENT_KIND_LABEL[kind]} ngày {date.fromisoformat(d).strftime('%d/%m')}.",
                "8.events",
            ))
    return issues


# ── 5. Gợi ý kinh doanh (mục biz) ────────────────────────────────────


def check_biz(
    content: Dict[str, Any],
    suggestions: Dict[int, Dict[str, Any]],
    valid_codes: Set[str],
    prices: List[Dict[str, Any]],
    report_date: Optional[str] = None,
) -> List[Dict[str, str]]:
    """`suggestions`: id -> {"trigger_rule"} của biz_suggestions (trigger_rule KHÔNG nằm trong
    content — xem generate_report_content). `prices`: content["2"].prices (để so ngưỡng)."""
    issues: List[Dict[str, str]] = []
    biz = content.get("biz")
    if not biz:
        return [_issue("biz", "biz", "warning", "Báo cáo không có mục Gợi ý kinh doanh.", "biz")]

    short_term = biz.get("short_term") or []
    if not short_term:
        issues.append(_issue("biz", "biz", "warning", "Không có gợi ý ngắn hạn nào.", "biz.short_term"))
    for i, it in enumerate(short_term):
        path = f"biz.short_term[{i}]"
        label = f"Gợi ý ngắn hạn #{i + 1}"
        if not isinstance(it, dict):
            issues.append(_issue("biz", "biz", "warning", f"{label}: sai định dạng (báo cáo cũ?) — thiếu trigger/action.", path))
            continue
        for field, name in (("trigger", "tình huống kích hoạt"), ("action", "hành động")):
            if _blank(it.get(field)):
                issues.append(_issue("biz", "biz", "error", f"{label}: thiếu {name}.", f"{path}.{field}"))
        if _blank(it.get("reason")):
            issues.append(_issue("biz", "biz", "info", f"{label}: thiếu lý do.", f"{path}.reason"))
        trigger = str(it.get("trigger") or "").strip()
        if trigger and not re.match(r"^(Nếu|Khi)\b", trigger, re.IGNORECASE):
            issues.append(_issue("biz", "biz", "info", f"{label}: tình huống kích hoạt nên bắt đầu bằng \"Nếu/Khi\".", f"{path}.trigger"))

        sid = it.get("id")
        if sid is None:
            issues.append(_issue("biz", "biz", "info", f"{label}: chưa được lưu vào bộ nhớ Jenny (không có id) — sẽ không được theo dõi.", path))
            continue
        if sid not in suggestions:
            issues.append(_issue("biz", "biz", "warning", f"{label}: id #{sid} không còn trong bộ nhớ (đã bị xoá/gỡ?).", path))
            continue
        mem = suggestions[sid]
        if mem.get("status") == "dismissed":
            issues.append(_issue("biz", "biz", "warning", f"{label}: đã bị admin gỡ khỏi bộ nhớ nhưng vẫn còn trong báo cáo.", path))
        if report_date and mem.get("first_report_date") and mem["first_report_date"] != report_date:
            issues.append(_issue(
                "biz", "biz", "info",
                f"{label}: id #{sid} thuộc báo cáo {mem['first_report_date']}, không phải báo cáo này — Jenny sẽ theo dõi theo ngày đó.",
                path,
            ))
        rule = suggestions[sid].get("trigger_rule")
        if not rule:
            continue  # điều kiện dạng tin tức — kiểm tra bằng LLM, không có ngưỡng giá
        normalized = biz_memory.normalize_trigger_rule(rule, valid_codes)
        if normalized is None:
            issues.append(_issue("biz", "biz", "error", f"{label}: trigger_rule không hợp lệ ({rule}).", f"{path}.trigger_rule"))
            continue
        rule = normalized
        price = next((p for p in prices if str(p.get("code", "")).upper() == rule["code"]), None)
        close = price.get("close") if price else None
        if not isinstance(close, (int, float)) or close == 0:
            continue
        if biz_memory.check_price_rule(rule, prices):
            issues.append(_issue(
                "biz", "biz", "warning",
                f"{label}: ngưỡng {rule['code']} {rule['op']} {_fmt(rule['value'])} ĐÃ chạm ngay hôm nay (giá {_fmt(close)}) — gợi ý sẽ kích hoạt tức thì.",
                f"{path}.trigger_rule",
            ))
        elif abs(rule["value"] - close) / abs(close) > BIZ_RULE_MAX_DISTANCE:
            issues.append(_issue(
                "biz", "biz", "warning",
                f"{label}: ngưỡng {rule['code']} {_fmt(rule['value'])} lệch >{int(BIZ_RULE_MAX_DISTANCE * 100)}% so với giá hiện tại {_fmt(close)} — kiểm tra đơn vị/mã.",
                f"{path}.trigger_rule",
            ))

    for i, it in enumerate(biz.get("long_term") or []):
        path = f"biz.long_term[{i}]"
        if not isinstance(it, dict):
            issues.append(_issue("biz", "biz", "warning", f"Gợi ý dài hạn #{i + 1}: sai định dạng.", path))
            continue
        for field, name in (("opportunity", "cơ hội"), ("solution", "giải pháp")):
            if _blank(it.get(field)):
                issues.append(_issue("biz", "biz", "error", f"Gợi ý dài hạn #{i + 1}: thiếu {name}.", f"{path}.{field}"))
    return issues


# ── 6. Độ phủ tin tức (khung tin của báo cáo) ────────────────────────


def news_window(target_date: str) -> tuple:
    """Khung tin của báo cáo — ĐÚNG như get_news_for_report: 07:00 VN ngày dữ liệu → 07:00 VN hôm sau
    (= 00:00 UTC → 00:00 UTC). Trả (start, end) datetime UTC."""
    start = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def check_news(content: Dict[str, Any], articles: List[Dict[str, Any]]) -> tuple:
    """`articles`: bài crawl trong khung tin, MỚI NHẤT TRƯỚC — mỗi phần tử
    {"url", "source", "region", "topic": [..], "is_hot_news"}. -> (issues, stats)."""
    issues: List[Dict[str, str]] = []
    tagged = [a for a in articles if a.get("topic")]
    # get_news_for_report: LIMIT 100 bài mới nhất TRƯỚC, rồi mới bỏ bài chưa gắn topic.
    used = [a for a in articles[:NEWS_GENERATOR_LIMIT] if a.get("topic")]
    window_urls = {_norm_url(a["url"]) for a in articles}

    by_topic: Counter = Counter(t for a in tagged for t in a["topic"])
    stats: Dict[str, Any] = {
        "total": len(articles),
        "international": sum(a.get("region") != "vietnam" for a in articles),
        "vietnam": sum(a.get("region") == "vietnam" for a in articles),
        "hot": sum(bool(a.get("is_hot_news")) for a in articles),
        "untagged": len(articles) - len(tagged),
        "sources": Counter(a.get("source") or "?" for a in articles),
        "by_topic": by_topic,
    }

    sec6 = content.get("6") or {}
    stats["sec6_international"] = len(sec6.get("international") or [])
    stats["sec6_vietnam"] = len(sec6.get("vietnam") or [])
    stats["sec9"] = len((content.get("9") or {}).get("items") or [])

    cited: List[tuple] = []  # (url, section, label, field_path) — trích dẫn trong phần phân tích
    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        if isinstance(b, dict) and b.get("source_url"):
            cited.append((b["source_url"], "1", f"Bullet {i + 1}", f"1.bullets[{i}].source_url"))
    for i, d in enumerate((content.get("2") or {}).get("key_developments") or []):
        if isinstance(d, dict) and d.get("source_url"):
            cited.append((d["source_url"], "4", f"Diễn biến chính #{i + 1}", f"2.key_developments[{i}].source_url"))
    for i, b in enumerate((content.get("4") or {}).get("bullets") or []):
        if isinstance(b, dict) and b.get("source_url"):
            cited.append((b["source_url"], "4", f"Tín chỉ/CBAM #{i + 1}", f"4.bullets[{i}].source_url"))
    for side in ("bullish", "bearish"):
        for i, d in enumerate(((content.get("2") or {}).get("market_drivers") or {}).get(side) or []):
            if isinstance(d, dict) and d.get("source_url"):
                cited.append((d["source_url"], "3", f"Yếu tố {side} #{i + 1}", f"2.market_drivers.{side}[{i}].source_url"))
    cited_urls = {_norm_url(u) for u, *_ in cited}
    stats["cited"] = len(cited_urls)

    if not articles:
        issues.append(_issue("news", "6", "error", "Không có bài nào được crawl trong khung tin của báo cáo — báo cáo không có tin tức làm căn cứ.", "6"))
        return issues, stats
    if stats["untagged"]:
        issues.append(_issue("news", "6", "info", f"{stats['untagged']} bài chưa được gắn chủ đề nên bộ sinh báo cáo bỏ qua.", "6"))
    if len(articles) > NEWS_GENERATOR_LIMIT:
        skipped = len(tagged) - len(used)
        issues.append(_issue(
            "news", "6", "info",
            f"Khung tin có {len(articles)} bài nhưng bộ sinh chỉ đọc {NEWS_GENERATOR_LIMIT} bài mới nhất — {skipped} bài có chủ đề bị bỏ qua.",
            "6",
        ))
    for region, label in (("international", "quốc tế"), ("vietnam", "Việt Nam")):
        n_used = sum((a.get("region") == "vietnam") == (region == "vietnam") for a in used)
        if n_used and not stats[f"sec6_{region}"]:
            issues.append(_issue("news", "6", "warning", f"Có {n_used} bài {label} trong ngày nhưng Mục Tin tức chi tiết phần {label} trống.", f"6.{region}"))

    for url, section, label, path in cited:
        if _norm_url(url) not in window_urls:
            issues.append(_issue("news", section, "info", f"{label}: nguồn không thuộc khung tin của báo cáo (có thể là tin cũ hoặc nhập tay).", path))
    for i, it in enumerate((content.get("9") or {}).get("items") or []):
        url = it.get("url") if isinstance(it, dict) else None
        if url and _norm_url(url) not in window_urls:
            issues.append(_issue("news", "9", "info", f"Nguồn #{i + 1}: không thuộc khung tin của báo cáo.", f"9.items[{i}].url"))

    # Chỉ xét chủ đề được đưa vào prompt Mục 1/Diễn biến chính (SECTION_TOPICS["1"]) — VCM, tin
    # carbon VN... vốn không thuộc phạm vi các mục này nên không trích là đúng.
    cited_topics = Counter(t for a in used if _norm_url(a["url"]) in cited_urls for t in a["topic"])
    used_by_topic = Counter(t for a in used for t in a["topic"])
    for topic, n in used_by_topic.most_common():
        if topic in SECTION_TOPICS["1"] and n >= NEWS_TOPIC_UNCITED_MIN and not cited_topics[topic]:
            issues.append(_issue(
                "news", "1", "info",
                f"Chủ đề \"{TOPIC_DISPLAY_LABELS.get(topic, topic)}\" có {n} bài trong ngày nhưng không bài nào được trích ở Tóm tắt/Diễn biến chính.",
                "1",
            ))
    return issues, stats


def format_news_stats(stats: Dict[str, Any], target_date: str) -> str:
    start = datetime.strptime(target_date, "%Y-%m-%d").date()
    lines = [
        f"THỐNG KÊ TIN TỨC (khung tin 07:00 {start:%d/%m} → 07:00 {start + timedelta(days=1):%d/%m}, giờ VN):",
        f"- Tổng {stats['total']} bài (quốc tế {stats['international']}, Việt Nam {stats['vietnam']}); "
        f"{stats['hot']} tin nóng; {len(stats['sources'])} nguồn; {stats['untagged']} bài chưa gắn chủ đề.",
    ]
    if stats["sources"]:
        lines.append("- Nguồn nhiều bài nhất: " + ", ".join(f"{src} ({n})" for src, n in stats["sources"].most_common(5)))
    if stats["by_topic"]:
        lines.append("- Theo chủ đề: " + ", ".join(
            f"{TOPIC_DISPLAY_LABELS.get(t, t)} ({n})" for t, n in stats["by_topic"].most_common()
        ))
    lines.append(
        f"- Báo cáo đã dùng: Tin tức chi tiết {stats.get('sec6_international', 0)} bài quốc tế + "
        f"{stats.get('sec6_vietnam', 0)} bài Việt Nam; Nguồn tham khảo {stats.get('sec9', 0)} mục; "
        f"{stats.get('cited', 0)} bài được trích dẫn trong Tóm tắt/Diễn biến chính/Yếu tố thị trường."
    )
    return "\n".join(lines)


# ── Tổng hợp điểm ────────────────────────────────────────────────────


def score_issues(issues: List[Dict[str, str]]) -> int:
    return max(0, 100 - sum(SEVERITY_PENALTY.get(it["severity"], 0) for it in issues))


def summarize(issues_by_check: Dict[str, List[Dict[str, str]]]) -> Dict[str, Any]:
    """-> {"scores": {check: điểm}, "overall_score": trung bình các check, "issues": [...]}."""
    scores = {check: score_issues(issues_by_check.get(check, [])) for check in CHECKS}
    severity_order = {"error": 0, "warning": 1, "info": 2}
    all_issues = [it for check in CHECKS for it in issues_by_check.get(check, [])]
    all_issues.sort(key=lambda it: severity_order.get(it["severity"], 3))  # sort ổn định — giữ thứ tự trong cùng mức
    return {
        "scores": scores,
        "overall_score": round(sum(scores.values()) / len(CHECKS)),
        "issues": all_issues,
    }


# ── Truy vấn DB + chạy toàn bộ ───────────────────────────────────────


async def _load_db_prices(session: AsyncSession, target_date: str, price_date: Optional[str]) -> tuple:
    """-> (giá DB của phiên `price_date` (hoặc phiên mới nhất nếu None), phiên mới nhất <= target_date)."""
    latest = await session.scalar(
        select(func.max(Price.price_date))
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date <= target_date, Instrument.code != CBAM_CODE)
    )
    compare_date = price_date or latest
    if not compare_date:
        return {}, latest
    rows = (await session.execute(
        select(Price, Instrument)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date == compare_date, Instrument.code != CBAM_CODE)
    )).all()
    db_prices = {
        inst.code.upper(): {
            "name": inst.name,
            "close": p.close_price,
            "day_change_pct": p.day_change_pct,
            "week_change_pct": p.week_change_pct,
        }
        for p, inst in rows
    }
    return db_prices, latest


async def _load_window_articles(session: AsyncSession, target_date: str) -> List[Dict[str, Any]]:
    start, end = news_window(target_date)
    rows = (await session.execute(
        select(Article.url, Article.source, Article.region, Article.topic, Article.is_hot_news)
        .where(Article.crawled_at >= start, Article.crawled_at < end)
        .order_by(Article.crawled_at.desc())
    )).all()
    return [
        {"url": r.url, "source": r.source, "region": r.region, "topic": list(r.topic or []), "is_hot_news": r.is_hot_news}
        for r in rows
    ]


async def run_report_qc(session: AsyncSession, report_date: str, content: Dict[str, Any]) -> Dict[str, Any]:
    """Chạy 6 check cho báo cáo `report_date` -> summarize() + "news_stats"."""
    target_date = report_data_date(report_date)

    db_prices, latest_price_date = await _load_db_prices(session, target_date, report_price_date(content))

    urls = collect_source_urls(content)
    known_urls: Set[str] = set()
    if urls:
        candidates = {u for u in urls} | {_norm_url(u) for u in urls} | {_norm_url(u) + "/" for u in urls}
        known_urls = set((await session.execute(select(Article.url).where(Article.url.in_(candidates)))).scalars())

    biz_ids = [
        it["id"] for it in ((content.get("biz") or {}).get("short_term") or [])
        if isinstance(it, dict) and isinstance(it.get("id"), int)
    ]
    suggestions: Dict[int, Dict[str, Any]] = {}
    if biz_ids:
        for s in (await session.execute(select(BizSuggestion).where(BizSuggestion.id.in_(biz_ids)))).scalars():
            suggestions[s.id] = {"trigger_rule": s.trigger_rule, "status": s.status, "first_report_date": s.first_report_date}
    valid_codes = {c.upper() for c in (await session.execute(select(Instrument.code))).scalars()}
    news_issues, news_stats = check_news(content, await _load_window_articles(session, target_date))

    issues_by_check = {
        "price": check_prices(content, db_prices, latest_price_date, report_date),
        "scenario": check_scenarios(content),
        "source": check_sources(content, known_urls),
        "calendar": check_calendar(content, report_date),
        "biz": check_biz(content, suggestions, valid_codes, (content.get("2") or {}).get("prices") or [], report_date),
        "news": news_issues,
    }
    result = summarize(issues_by_check)
    result["news_stats"] = news_stats
    logger.info(
        "[REPORT-QC] %s: %d/100 (%s), %d issue.",
        report_date, result["overall_score"], result["scores"], len(result["issues"]),
    )
    return result


def format_qc_report(report_date: str, status: str, result: Dict[str, Any]) -> str:
    """Văn bản kết quả QC trả về cho Jenny (tool_result) — Jenny dựa vào đây để trình bày cho admin."""
    lines = [
        f"KẾT QUẢ QC BÁO CÁO NGÀY {report_date} (trạng thái: {status}) — Lucy kiểm tra tự động bằng cách "
        "đối chiếu nội dung báo cáo với dữ liệu hệ thống (giá, tin tức, lịch, bộ nhớ gợi ý). Không dùng AI.",
        f"ĐIỂM TỔNG: {result['overall_score']}/100 (mỗi hạng mục 100 điểm, trừ 25/lỗi, 10/cảnh báo, 2/gợi ý).",
        "Điểm từng hạng mục: " + " | ".join(f"{CHECK_LABELS[c]} {result['scores'][c]}" for c in CHECKS),
        "",
        format_news_stats(result["news_stats"], report_data_date(report_date)),
        "",
    ]
    issues = result["issues"]
    if not issues:
        lines.append("KHÔNG PHÁT HIỆN VẤN ĐỀ NÀO.")
        return "\n".join(lines)

    counts = Counter(it["severity"] for it in issues)
    lines.append(
        f"DANH SÁCH VẤN ĐỀ ({counts['error']} lỗi, {counts['warning']} cảnh báo, {counts['info']} gợi ý) — theo mục báo cáo:"
    )
    by_section: Dict[str, List[Dict[str, str]]] = {}
    for it in issues:  # đã sort theo mức độ
        by_section.setdefault(it["section"], []).append(it)
    for section in sorted(by_section, key=lambda s: list(SECTION_LABELS).index(s) if s in SECTION_LABELS else 99):
        lines.append(f"[{SECTION_LABELS.get(section, 'Mục ' + section)}]")
        for it in by_section[section]:
            lines.append(f"- {SEVERITY_LABELS[it['severity']]}: {it['message']}")
    return "\n".join(lines)


async def qc_report_text(session: AsyncSession, report_date: str) -> str:
    """Entry point cho tool `lucy_qc` của Jenny chat."""
    report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
    if not report:
        return f"Không có báo cáo nào ngày {report_date}."
    if report.status == "generating":
        return f"Báo cáo ngày {report_date} đang được sinh — chưa QC được, hãy thử lại sau ít phút."
    if not report.content:
        return f"Báo cáo ngày {report_date} không có nội dung (trạng thái: {report.status}{', lỗi: ' + report.error_message if report.error_message else ''})."
    result = await run_report_qc(session, report_date, report.content)
    return format_qc_report(report_date, report.status, result)
