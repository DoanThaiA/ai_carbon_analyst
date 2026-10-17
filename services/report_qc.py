"""Jack — QC báo cáo ngày, chạy dưới dạng tool `jack_qc` của Jenny chat (chỉ admin —
xem services/quote_chat.py::ADMIN_TOOLS). Toàn bộ là check RULE-BASED bằng Python, KHÔNG gọi
LLM: đối chiếu nội dung JSON báo cáo (`reports.content`) với dữ liệu thật trong DB / lịch cố định.

    check       section(s)   nguồn sự thật
    price       "2"          bảng `prices` (giá chốt phiên) + nhãn mã ↔ giá thật (check_instrument_labels)
    scenario    "3"          logic + schema trading_scenarios / dòng "**Tổng hợp:**"
    source      "1","4","9"  bảng `articles` (URL nguồn có thật)
    calendar    "8"          lịch định kỳ EIA/Baker Hughes (_compute_recurring_calendar_events)
    biz         "biz"        schema gợi ý + `biz_suggestions.trigger_rule` + nguồn gợi ý nằm trong Mục 6/9
    fact        "1","2","4"  số liệu trong câu có trích nguồn ↔ nội dung bài gốc (`articles.content`)
    news        "6",...      bảng `articles` trong khung tin của báo cáo (số lượng, độ phủ)

Mỗi hàm `check_*` là hàm THUẦN (nhận content + dữ liệu đã truy vấn sẵn, trả list issue) để
test được không cần DB — `run_report_qc()` lo phần truy vấn, `qc_report_text()` dựng văn bản
kết quả trả cho Jenny. Mỗi issue: {"check", "section", "severity": "error"|"warning"|"info",
"message", "field_path"}.

Bảng `report_qc_results` (bản QC dạng nút bấm trước đây) không còn được ghi — giữ lại để
không mất lịch sử / không phá chuỗi migration đã chạy.
"""
import logging
import math
from collections import Counter
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Article, BizSuggestion, Instrument, Price, Report
from services import biz_memory
from services.strategy_levels import is_breakout_entry
from services.report_consistency import atr_true_range, consistency_issues, session_signals, technical_claim_issues
from services.report_generator import (
    CBAM_CODE,
    SECTION_TOPICS,
    TOPIC_DISPLAY_LABELS,
    CALENDAR_WINDOW_DAYS,
    STALE_NEWS_MAX_AGE_DAYS,
    FUEL_NEAR_BREAKEVEN,
    _compute_recurring_calendar_events,
    fuel_spread_state,
    fuel_threshold_far,
    _parse_event_date,
    data_matches_event,
    news_source_label,
    is_no_price_report,
    report_data_date,
)

logger = logging.getLogger(__name__)

CHECKS = ("price", "scenario", "source", "fact", "calendar", "biz", "news")
CHECK_LABELS = {
    "price": "Giá số liệu", "scenario": "Kịch bản giao dịch", "source": "Nguồn tin", "fact": "Số liệu ↔ tin gốc",
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


# Parser thẻ chiến lược — port 1-1 của frontend ReportDocument.tsx, nằm ở services/strategy_levels.py
# (re-export để code/test cũ gọi report_qc.parse_strategy/parse_signal_levels vẫn chạy).
from services.strategy_levels import (  # noqa: E402
    _STRATEGY_LINE_RE,
    _price_nums,
    _split_buy_sell,
    parse_signal_levels,
    parse_strategy,
)


def _eua_last_close(content: Dict[str, Any]) -> Optional[float]:
    """Giá EUA "hiện tại" — như frontend: close nến cuối chart_data, không có thì dòng EUA bảng giá."""
    sec2 = content.get("2") or {}
    chart = sec2.get("chart_data") or []
    if chart and isinstance(chart[-1], dict) and isinstance(chart[-1].get("close"), (int, float)):
        return float(chart[-1]["close"])
    row = next((p for p in sec2.get("prices") or [] if isinstance(p, dict) and str(p.get("code", "")).upper() == "EUA"), None)
    return float(row["close"]) if row and isinstance(row.get("close"), (int, float)) else None


# R:R tối thiểu (prompt Mục 3: "PHẢI ≥ 1:1,5").
MIN_RISK_REWARD = 1.5
# Cắt lỗ cách đáy/đỉnh phiên gần hơn tỉ lệ này của biên độ phiên → coi là đặt sát, dễ bị quét.
STOP_MIN_RANGE_FRACTION = 0.25
# Khoảng cách tối thiểu giá vào ↔ cắt lỗ, theo bội số ATR 14 phiên (mọi khung thời gian) — vd mua 82,5
# cắt lỗ 82,35 (đệm 0,15) khi ATR ~1,8 là bị nhiễu 1 phiên quét.
STOP_MIN_ATR_FRACTION = 0.5
_CONDITION_LEVEL_RE = re.compile(
    r"(đóng cửa|vượt|phá|giữ|thủng|xuống)[^\d]{0,20}(trên|dưới|qua)?[^\d]{0,8}(\d+(?:[.,]\d+)?)", re.IGNORECASE
)


def _eua_last_session(content: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Nến phiên gần nhất của EUA (chart_data) — {"high", "low", "atr"} để kiểm khoảng cách cắt lỗ.
    "atr" = TB True Range 14 phiên (report_consistency.atr_true_range — cùng công thức bên bộ sinh)."""
    chart = (content.get("2") or {}).get("chart_data") or []
    last = chart[-1] if chart and isinstance(chart[-1], dict) else None
    if last and isinstance(last.get("high"), (int, float)) and isinstance(last.get("low"), (int, float)):
        return {"high": float(last["high"]), "low": float(last["low"]), "atr": atr_true_range(chart)}
    return None


def _check_strategy_math(
    sc: Dict[str, Any], label: str, path: str, long: bool, entry_range: List[float],
    target: float, stop: float, session: Optional[Dict[str, float]], target_body: str,
) -> List[Dict[str, str]]:
    """Kiểm số học kịch bản: điều kiện ↔ Entry, R:R, cắt lỗ sát đáy/đỉnh phiên, cơ sở mục tiêu."""
    issues: List[Dict[str, str]] = []
    p = f"{path}.trading_strategy"
    entries = list(entry_range)
    cond = _CONDITION_LEVEL_RE.search(str(sc.get("condition") or ""))
    if cond:
        level = float(cond.group(3).replace(",", ".", 1))
        word = cond.group(0).lower()
        above = ("trên" in word or "vượt" in word or ("phá" in word and long)) and "dưới" not in word
        if level >= 10 and long and above and max(entry_range) < level:
            issues.append(_issue(
                "scenario", "3", "warning",
                f"{label}: điều kiện \"trên {_fmt(level)}\" mâu thuẫn với Entry mua quanh {_fmt(min(entry_range))}–{_fmt(max(entry_range))} (thấp hơn ngưỡng).",
                p,
            ))
            entries.append(level)  # vào lệnh theo điều kiện → R:R tính cả từ ngưỡng này
        elif level >= 10 and not long and not above and min(entry_range) > level:
            issues.append(_issue(
                "scenario", "3", "warning",
                f"{label}: điều kiện \"dưới {_fmt(level)}\" mâu thuẫn với Entry bán quanh {_fmt(min(entry_range))}–{_fmt(max(entry_range))} (cao hơn ngưỡng).",
                p,
            ))
            entries.append(level)
    worst = max(entries) if long else min(entries)
    reward, risk = (target - worst, worst - stop) if long else (worst - target, stop - worst)
    if risk > 0 and reward / risk < MIN_RISK_REWARD:
        issues.append(_issue(
            "scenario", "3", "warning",
            f"{label}: R:R chỉ {reward / risk:.2f}:1 (vào {_fmt(worst)}, mục tiêu {_fmt(target)}, cắt lỗ {_fmt(stop)}) — dưới 1,5:1.",
            p,
        ))
    atr = (session or {}).get("atr")
    if atr and 0 < risk < STOP_MIN_ATR_FRACTION * atr:
        issues.append(_issue(
            "scenario", "3", "warning",
            f"{label}: cắt lỗ {_fmt(stop)} chỉ cách giá vào {_fmt(worst)} {_fmt(risk)} EUR — dưới "
            f"{str(STOP_MIN_ATR_FRACTION).replace('.', ',')} × ATR ({_fmt(STOP_MIN_ATR_FRACTION * atr)}), nằm trong nhiễu giá.",
            p,
        ))
    if session and sc.get("horizon") == "ngắn hạn":
        rng = session["high"] - session["low"]
        edge = session["low"] if long else session["high"]
        gap = (edge - stop) if long else (stop - edge)
        if rng > 0 and 0 <= gap < STOP_MIN_RANGE_FRACTION * rng:
            issues.append(_issue(
                "scenario", "3", "warning",
                f"{label}: cắt lỗ {_fmt(stop)} chỉ cách {'đáy' if long else 'đỉnh'} phiên {_fmt(edge)} {_fmt(gap)} EUR "
                f"(biên độ phiên {_fmt(rng)}) — dễ bị quét.",
                p,
            ))
    if "(" not in target_body:
        issues.append(_issue("scenario", "3", "info", f"{label}: mục tiêu không ghi cơ sở (kháng cự/đo biên độ/%...).", p))
    return issues


def _check_strategy(
    sc: Dict[str, Any], label: str, path: str, eua_close: Optional[float],
    session: Optional[Dict[str, float]] = None,
) -> List[Dict[str, str]]:
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
    issues += _check_two_sided_legs(sc, label, path, bodies)
    if direction == "đi ngang":
        # "Đi ngang" mà chiến lược chỉ 1 chiều phá vỡ (bản 17/10 trung hạn: "bán khi thủng 81,50") → tự mâu thuẫn và
        # trước đây lọt hết kiểm tra số học (R:R 1,37). Báo + kiểm số học theo chiều thực của lệnh.
        e_buy, e_sell, _ = _split_buy_sell(bodies["Entry"])
        side = "giảm" if e_sell and not e_buy else "tăng" if e_buy and not e_sell else None
        if side and is_breakout_entry(side, bodies["Entry"]):
            issues.append(_issue(
                "scenario", "3", "warning",
                f"{label}: nhãn \"đi ngang\" nhưng chiến lược là lệnh {'bán khi thủng' if side == 'giảm' else 'mua khi phá'} 1 chiều "
                "— tách \"Cơ sở: đi ngang\" (mua biên dưới/bán biên trên) và \"Thay thế: phá biên\" (đổi direction, xác suất thấp).",
                f"{path}.trading_strategy",
            ))
            direction = side
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
        elif (target - entry if long else entry - target) > 0:
            issues += _check_strategy_math(sc, label, path, long, entry_range, target, stop, session, bodies["Mục tiêu"])
    if is_short and eua_close and abs(entry - eua_close) / eua_close > ENTRY_MAX_DISTANCE:
        issues.append(_issue(
            "scenario", "3", "warning",
            f"{label}: Entry {_fmt(entry)} lệch >{int(ENTRY_MAX_DISTANCE * 100)}% so với giá EUA hiện tại {_fmt(eua_close)}.",
            f"{path}.trading_strategy",
        ))
    return issues


def _check_two_sided_legs(sc: Dict[str, Any], label: str, path: str, bodies: Dict[str, str]) -> List[Dict[str, str]]:
    """Lệnh 2 chiều (Entry có cả "mua" lẫn "bán" — thường là kịch bản đi ngang) → MỖI chân phải có mục tiêu và
    cắt lỗ đúng phía. Vd bản lỗi: "Mua 82,5–83, bán 88,0–88,5 / Mục tiêu 88,0 / Cắt lỗ dưới 81,52" → chân bán
    không có cắt lỗ lẫn mục tiêu."""
    lv = parse_signal_levels(sc.get("direction") or "đi ngang", bodies["Entry"], bodies["Mục tiêu"], bodies["Quản trị rủi ro"])
    if not (lv["entry_buy"] and lv["entry_sell"]):
        return []
    issues: List[Dict[str, str]] = []
    p = f"{path}.trading_strategy"
    legs = (
        ("mua", lv["entry_buy"], lv["target_buy"], lv["stop_lower"], 1),
        ("bán", lv["entry_sell"], lv["target_sell"], lv["stop_upper"], -1),
    )
    for name, entry_range, target, stop, sign in legs:
        missing = [w for w, v in (("mục tiêu", target), ("cắt lỗ", stop)) if v is None]
        if missing:
            issues.append(_issue(
                "scenario", "3", "error",
                f"{label}: chân {name} ({_fmt(min(entry_range))}–{_fmt(max(entry_range))}) không có {' và '.join(missing)} — "
                f"lệnh 2 chiều phải ghi đủ mục tiêu/cắt lỗ cho từng chân (\"Mục tiêu: T_mua / T_bán\", "
                f"\"Cắt lỗ dưới S_mua hoặc trên S_bán\").",
                p,
            ))
            continue
        entry = sum(entry_range) / len(entry_range)
        if sign * (target - entry) <= 0 or sign * (entry - stop) <= 0:
            issues.append(_issue(
                "scenario", "3", "error",
                f"{label}: chân {name} có mục tiêu {_fmt(target)} / cắt lỗ {_fmt(stop)} sai phía so với Entry {_fmt(entry)}.",
                p,
            ))
    return issues


_API2_UP_RE = re.compile(r"(API ?2|giá than|than ARA)[^.;\n]{0,40}?(tăng|nhích|leo|đi lên|cao hơn)", re.IGNORECASE)
_API2_DOWN_RE = re.compile(r"(API ?2|giá than|than ARA)[^.;\n]{0,40}?(giảm|hạ|lùi|đi xuống|thấp hơn)", re.IGNORECASE)
_GAS_MENTION_RE = re.compile(r"TTF|khí|chênh|spread", re.IGNORECASE)


_TTF_UP_RE = re.compile(r"(TTF|giá khí|khí đốt|nguồn cung khí)[^.;\n]{0,50}?(tăng|leo|đẩy lên|cao hơn|rủi ro|gián đoạn)", re.IGNORECASE)
_SPREAD_RE = re.compile(r"chênh|spread", re.IGNORECASE)
_WIDEN_RE = re.compile(r"mở rộng|nới rộng|tăng lên|widen", re.IGNORECASE)


def check_fuel_direction(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Chiều tác động của nhiên liệu lên EUA xếp theo THAY ĐỔI RÒNG của chênh khí − than (fuel_spread_state, tính lại từ
    bảng giá của chính báo cáo), ĐỐI XỨNG cho cả 2 nhiên liệu:
      - "API2 tăng" không bao giờ là yếu tố hỗ trợ tăng; "API2 giảm" không bao giờ là áp lực giảm;
      - than đã rẻ hơn khí rõ rệt (bão hòa): "TTF tăng / rủi ro cung khí" KHÔNG là động lực tăng, và "API2 tăng"
        KHÔNG là áp lực giảm khi chênh còn MỞ RỘNG trong tuần (bản 17/10: chênh +54 → +64 mà vẫn viết "API2 tăng thu hẹp
        lợi thế điện than")."""
    drivers = (content.get("2") or {}).get("market_drivers") or {}
    if not isinstance(drivers, dict):
        return []
    state = fuel_spread_state((content.get("2") or {}).get("prices") or [])
    saturated = bool(state and state["spread"] > FUEL_NEAR_BREAKEVEN)
    widened = bool(state and state["delta"] is not None and state["delta"] >= 0)
    issues = []

    def _add(side: str, i: int, text: str, why: str) -> None:
        issues.append(_issue(
            "scenario", "3", "error", f"Yếu tố {'hỗ trợ tăng giá' if side == 'bullish' else 'gây áp lực giảm giá'} #{i + 1} sai chiều: "
            f"{why}: \"{text[:110]}\"", f"2.market_drivers.{side}[{i}].text",
        ))

    for side in ("bullish", "bearish"):
        for i, d in enumerate(drivers.get(side) or []):
            text = str(d.get("text") if isinstance(d, dict) else d or "")
            if side == "bullish" and _API2_UP_RE.search(text) and not _GAS_MENTION_RE.search(text):
                _add(side, i, text, "giá than API2 tăng tự thân không làm tăng cầu EUA (than kém cạnh tranh hơn) — chỉ "
                                    "\"chênh khí − than mở rộng\" mới là yếu tố hỗ trợ")
            elif side == "bearish" and _API2_DOWN_RE.search(text) and not _GAS_MENTION_RE.search(text):
                _add(side, i, text, "giá than API2 giảm làm than cạnh tranh hơn — không phải áp lực giảm cầu EUA")
            elif side == "bearish" and saturated and widened and _API2_UP_RE.search(text):
                _add(side, i, text, f"chênh khí − than trong tuần MỞ RỘNG ({state['spread_prev']:+.0f} → {state['spread']:+.0f} "
                                    "EUR/MWh) — lợi thế than được củng cố, \"API2 tăng\" không phải áp lực giảm")
            elif side == "bullish" and saturated and _SPREAD_RE.search(text) and _WIDEN_RE.search(text):
                _add(side, i, text, f"chuyển đổi đã bão hòa (chênh ≈ {state['spread']:+.0f} EUR/MWh) — chênh khí − than mở rộng chỉ "
                                    "GIỮ MỨC cầu EUA, không tạo lực tăng (Phân tích cùng báo cáo kết luận như vậy)")
            elif side == "bullish" and saturated and _TTF_UP_RE.search(text) and not _SPREAD_RE.search(text):
                _add(side, i, text, f"than đã rẻ hơn khí rõ rệt (chênh ≈ {state['spread']:+.0f} EUR/MWh, chuyển đổi bão hòa) — "
                                    "TTF tăng/rủi ro cung khí không còn nâng cầu EUA qua fuel switching")
    if saturated:
        for i, d in enumerate((content.get("2") or {}).get("key_developments") or []):
            text = str(d.get("text") or "") if isinstance(d, dict) else ""
            if d.get("impact") == "tăng" and _SWITCH_TO_COAL_RE.search(text):
                issues.append(_issue(
                    "scenario", "3", "error",
                    f"Diễn biến chính #{i + 1} dùng tiền đề \"chuyển sang than → cầu EUA tăng\" trong khi than đã rẻ hơn khí "
                    f"rõ rệt (chênh ≈ {state['spread']:+.0f} EUR/MWh, chuyển đổi bão hòa): \"{text[:110]}\"",
                    f"2.key_developments[{i}].impact",
                ))
    return issues


_SWITCH_TO_COAL_RE = re.compile(r"(chuyển|dịch|dispatch)[^.;\n]{0,25}(sang|về)\s+(?:điện\s+)?than", re.IGNORECASE)
# "ngưỡng theo dõi ±16 EUR/MWh", "chênh thu hẹp ≥16 EUR/MWh", "không thu hẹp quá 16 EUR/MWh", "TTF ±10%"
_SPREAD_THRESHOLD_RE = re.compile(
    r"(?:chênh|spread)[^.\n]{0,60}?(?:thu hẹp|±|thay đổi|quá)[^.\n]{0,25}?\d+(?:[.,]\d+)?\s*EUR/MWh"
    r"|±\s*\d+(?:[.,]\d+)?\s*EUR/MWh|TTF\s*±\s*\d+(?:[.,]\d+)?\s*%",
    re.IGNORECASE,
)


def check_fuel_thresholds(content: Dict[str, Any], scenarios_only: bool = False) -> List[Dict[str, str]]:
    """Ngưỡng fuel switching dạng "chênh khí − than thu hẹp ±X EUR/MWh" trong khi giá hòa vốn ở ĐẦU THẤP của dải hiệu
    suất đội nhà máy còn xa giá EUA (fuel_threshold_far) → ngưỡng đó không đổi thứ tự huy động, mâu thuẫn chính mô hình
    (bản 17/10: "±16 EUR/MWh ≈ TTF ±10%" trong khi TTF −10% thì giá hòa vốn vẫn ≈ 177 EUR/t, EUA 85)."""
    state = fuel_spread_state((content.get("2") or {}).get("prices") or [])
    if not fuel_threshold_far(state):
        return []
    lo = (state.get("switch_range") or (None,))[0]
    why = (
        f"giá EUA hòa vốn thấp nhất của đội nhà máy ≈ {_fmt(lo)} EUR/t so với EUA {_fmt(state['eua'])} — chênh thu hẹp mức này "
        f"không đổi thứ tự huy động. Ngưỡng thật: TTF ≈ {_fmt(state['ttf_first_switch'])} EUR/MWh hoặc API2 ≈ "
        f"{_fmt(state['api2_first_switch'])} USD/t (ngoài tầm 1–2 tuần → bỏ điều kiện fuel switching)"
    )
    sec3 = content.get("3") or {}
    targets: List[tuple] = []
    for i, sc in enumerate(sec3.get("trading_scenarios") or []):
        if isinstance(sc, dict):
            for k in ("condition", "key_risk", "trading_strategy"):
                targets.append((f"3.trading_scenarios[{i}].trading_strategy", f"Kịch bản {sc.get('horizon')}", str(sc.get(k) or "")))
    if not scenarios_only:
        for i, b in enumerate(sec3.get("analysis_blocks") or []):
            if isinstance(b, dict):
                targets.append((f"3.analysis_blocks[{i}].content", str(b.get("heading") or "Mục 3"), str(b.get("content") or "")))
    issues = []
    for path, label, text in targets:
        m = _SPREAD_THRESHOLD_RE.search(text)
        if m:
            issues.append(_issue("scenario", "3", "warning", f"{label}: ngưỡng fuel switching \"{m.group(0)[:60]}\" vô nghĩa — {why}.", path))
    return issues


_TRIGGER_RE = re.compile(r"(?:đóng cửa|giá|EUA)?[^.;\n]{0,15}?(trên|vượt|dưới|thủng)\s+(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def check_breakout_levels(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Lệnh PHÁ biên 30 phiên (mua trên đỉnh / bán dưới đáy): mục tiêu = phép đo biên độ (đỉnh/đáy ± biên độ 30 phiên,
    cùng phương pháp cả 2 chiều — bản 17/10 trung hạn lấy −4,5% tròn = 77,80 thay vì 75,88), cắt lỗ cách mức phá
    0,5–1,5 × ATR (cắt lỗ 84,20 cho lệnh bán thủng 81,50 là 1,6 ATR, không có cơ sở)."""
    chart = [c for c in (content.get("2") or {}).get("chart_data") or []
             if isinstance(c, dict) and isinstance(c.get("high"), (int, float)) and isinstance(c.get("low"), (int, float))]
    atr = atr_true_range(chart)
    if not chart or not atr:
        return []
    hi, lo = max(c["high"] for c in chart), min(c["low"] for c in chart)
    issues = []
    for i, sc in enumerate((content.get("3") or {}).get("trading_scenarios") or []):
        if not isinstance(sc, dict) or sc.get("horizon") == "ngắn hạn":
            continue
        parts = parse_strategy(sc.get("trading_strategy"))
        if not parts:
            continue
        body = {k: next((b.replace("*", "") for lbl, b in parts if re.search(p, lbl, re.I)), "") for k, p in
                (("entry", "entry"), ("target", "mục tiêu|target"), ("risk", "quản trị rủi ro|cắt lỗ|stop"))}
        e_buy, e_sell, _ = _split_buy_sell(body["entry"])
        side = sc.get("direction") if sc.get("direction") in ("tăng", "giảm") else (
            "giảm" if e_sell and not e_buy else "tăng" if e_buy and not e_sell else None)
        if not side or not is_breakout_entry(side, body["entry"]):
            continue
        lv = parse_signal_levels(side, body["entry"], body["target"], body["risk"])
        zone = lv["entry_buy"] if side == "tăng" else lv["entry_sell"]
        target = lv["target_buy"] if side == "tăng" else lv["target_sell"]
        stop = lv["stop_lower"] if side == "tăng" else lv["stop_upper"]
        if not zone:
            continue
        level = max(zone) if side == "tăng" else min(zone)
        p = f"3.trading_scenarios[{i}].trading_strategy"
        breaks_range = level >= hi - 0.25 * atr if side == "tăng" else level <= lo + 0.25 * atr
        mm = hi + (hi - lo) if side == "tăng" else lo - (hi - lo)
        if breaks_range and target is not None and abs(target - mm) > 0.5 * atr:
            issues.append(_issue(
                "scenario", "3", "warning",
                f"Kịch bản {sc.get('horizon')}: lệnh phá biên 30 phiên nhưng mục tiêu {_fmt(target)} không theo phép đo biên độ "
                f"({_fmt(mm)} = {'đỉnh' if side == 'tăng' else 'đáy'} 30 phiên {'+' if side == 'tăng' else '−'} biên độ {_fmt(hi - lo)}).", p,
            ))
        if stop is not None:
            dist = (level - stop) if side == "tăng" else (stop - level)
            if dist > 0 and not (0.5 * atr <= dist <= 1.5 * atr):
                issues.append(_issue(
                    "scenario", "3", "warning",
                    f"Kịch bản {sc.get('horizon')}: cắt lỗ {_fmt(stop)} cách mức phá {_fmt(level)} {_fmt(dist)} = {dist / atr:.1f} × ATR "
                    f"({_fmt(atr)}) — nên trong 0,5–1,5 × ATR ({_fmt(level + (0.5 if side == 'giảm' else -0.5) * atr)}…"
                    f"{_fmt(level + (1.5 if side == 'giảm' else -1.5) * atr)}).", p,
                ))
    return issues


def check_scenario_basis(content: Dict[str, Any], close: Optional[float] = None) -> List[Dict[str, str]]:
    """Kịch bản trung/dài hạn có chiều (tăng/giảm) mà "Tổng hợp" của chính báo cáo TRUNG LẬP và điều kiện chỉ là 1 ngưỡng
    giá CHƯA xảy ra → chiều không có luận cứ (bản 17/10: trung hạn "Nghiêng giảm" chỉ dựa vào "thủng 82,35" trong khi
    phân tích kết luận năng lượng giữ mức cầu, chính sách trung lập)."""
    sec3 = content.get("3") or {}
    if close is None:
        close = _eua_last_close(content)
    summary = extract_eua_summary(content) or ""
    m = EUA_SENTIMENT_RE.match(summary)
    if not close or not m or m.group(1).upper() != "TRUNG LẬP":
        return []
    issues = []
    for i, sc in enumerate(sec3.get("trading_scenarios") or []):
        if not isinstance(sc, dict) or sc.get("horizon") not in ("trung hạn", "dài hạn") or sc.get("direction") not in ("tăng", "giảm"):
            continue
        t = _TRIGGER_RE.search(str(sc.get("condition") or ""))
        if not t:
            continue
        level = float(t.group(2).replace(",", ".", 1))
        below = t.group(1).lower() in ("dưới", "thủng")
        if level >= 10 and ((below and close > level) or (not below and close < level)):
            issues.append(_issue(
                "scenario", "3", "warning",
                f"Kịch bản {sc['horizon']} \"{sc['direction']}\" chỉ dựa vào ngưỡng {t.group(1)} {_fmt(level)} CHƯA xảy ra (giá "
                f"{_fmt(close)}) trong khi Tổng hợp TRUNG LẬP — nên để \"đi ngang\" và ghi ngưỡng ở điều kiện chờ kích hoạt.",
                f"3.trading_scenarios[{i}].direction",
            ))
    return issues


def check_horizon_overlap(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Ngắn hạn giữ lệnh 1 chiều (cắt lỗ S) mà trung hạn ngược chiều kích hoạt TRƯỚC S → 2 khung chồng vùng hành động
    (bản 17/10: ngắn hạn mua cắt lỗ 81,50, trung hạn bán khi đóng cửa dưới 82,35 → vùng 81,50–82,35 mâu thuẫn)."""
    from services.strategy_levels import strategy_levels
    scs = {sc.get("horizon"): sc for sc in (content.get("3") or {}).get("trading_scenarios") or [] if isinstance(sc, dict)}
    short, mid = scs.get("ngắn hạn"), scs.get("trung hạn")
    if not short or not mid or {short.get("direction"), mid.get("direction")} != {"tăng", "giảm"}:
        return []
    lv = strategy_levels(short)
    t = _TRIGGER_RE.search(str(mid.get("condition") or ""))
    if not lv or not t:
        return []
    level = float(t.group(2).replace(",", ".", 1))
    stop = lv["stop_lower"] if short["direction"] == "tăng" else lv["stop_upper"]
    if stop is None or level < 10:
        return []
    if (short["direction"] == "tăng" and level > stop) or (short["direction"] == "giảm" and level < stop):
        return [_issue(
            "scenario", "3", "warning",
            f"Ngắn hạn {short['direction']} (cắt lỗ {_fmt(stop)}) và trung hạn {mid['direction']} (kích hoạt {t.group(1)} {_fmt(level)}) "
            f"chồng vùng {_fmt(min(stop, level))}–{_fmt(max(stop, level))}: chưa có quy tắc ưu tiên — mức kích hoạt trung hạn nên nằm ngoài cắt lỗ ngắn hạn.",
            "3.trading_scenarios",
        )]
    return []


def check_long_term_basis(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Kịch bản DÀI HẠN (>3 tháng): (1) mục tiêu = phép đo biên độ 30 phiên là công cụ kỹ thuật ngắn hạn, không có cơ sở
    cho khung >3 tháng; (2) cận vùng giá = đúng giá đóng cửa hôm nay là mốc không có cơ sở (bản 17/10: "Tích lũy
    82,35–85,41" với 85,41 là đóng cửa, mục tiêu 95,29 = đo biên độ 30 phiên)."""
    from services.strategy_levels import strategy_levels
    chart = [c for c in (content.get("2") or {}).get("chart_data") or []
             if isinstance(c, dict) and isinstance(c.get("high"), (int, float)) and isinstance(c.get("low"), (int, float))]
    close = _eua_last_close(content)
    issues = []
    for i, sc in enumerate((content.get("3") or {}).get("trading_scenarios") or []):
        if not isinstance(sc, dict) or sc.get("horizon") != "dài hạn":
            continue
        path = f"3.trading_scenarios[{i}].trading_strategy"
        lv = strategy_levels(sc)
        if chart and lv:
            hi, lo = max(c["high"] for c in chart), min(c["low"] for c in chart)
            atr = atr_true_range(chart) or 0.0
            target = lv["target_buy"] if sc.get("direction") == "tăng" else lv["target_sell"]
            mm = hi + (hi - lo) if sc.get("direction") == "tăng" else lo - (hi - lo)
            if target is not None and abs(target - mm) <= max(0.25 * atr, 0.05):
                issues.append(_issue(
                    "scenario", "3", "warning",
                    f"Kịch bản dài hạn: mục tiêu {_fmt(target)} = phép đo biên độ 30 phiên — công cụ kỹ thuật ngắn hạn, không phải "
                    "cơ sở cho khung >3 tháng (dùng động lực cấu trúc: cap/LRF, MSR, phân bổ miễn phí, ETS2, rà soát ETS; "
                    "không suy ra mức thì bỏ mức giá cụ thể).", path,
                ))
        zone_text = " ".join(str(sc.get(k) or "") for k in ("price_zone", "trading_strategy"))
        if close:
            for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*[–—-]\s*(\d+(?:[.,]\d+)?)", zone_text):
                ends = [float(x.replace(",", ".", 1)) for x in m.groups()]
                if any(abs(e - close) <= 0.011 for e in ends):
                    issues.append(_issue(
                        "scenario", "3", "warning",
                        f"Kịch bản dài hạn: vùng {m.group(0)} lấy giá đóng cửa hôm nay ({_fmt(close)}) làm cận — mốc không có cơ "
                        "sở cho khung >3 tháng.", path,
                    ))
                    break
    return issues


_FREE_ALLOC_RE = re.compile(r"phân bổ miễn phí[^.\n]{0,80}?(?:tới|đến|trước)\s*(?:năm\s*)?(?:2029\s*[–-]\s*)?2030", re.IGNORECASE)


def check_legislation_claims(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Lộ trình cắt phân bổ miễn phí (Chỉ thị (EU) 2023/959) chạy 2026–2034 (0% năm 2034) — viết "tới 2029–2030" là sai."""
    issues = []
    texts = _report_texts(content) + [
        ("3", f"3.trading_scenarios[{i}]", " ".join(str(v) for v in sc.values() if isinstance(v, str)))
        for i, sc in enumerate((content.get("3") or {}).get("trading_scenarios") or []) if isinstance(sc, dict)
    ]
    for section, path, text in texts:
        if _FREE_ALLOC_RE.search(text) and "2034" not in text:
            issues.append(_issue(
                "scenario", section, "warning",
                "Lộ trình cắt phân bổ miễn phí ghi kết thúc 2029–2030 — theo Chỉ thị (EU) 2023/959 chạy 2026–2034 (0% năm 2034), "
                "2029–2030 chỉ là bước giảm mạnh nhất.", path,
            ))
    return issues


_INTERPRETIVE_RE = re.compile(r"cho thấy|phản ánh|chứng tỏ|vẫn vững|củng cố niềm tin|vừa thiết lập|báo hiệu", re.IGNORECASE)


def check_fact_tags(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Nhãn FACT chỉ cho dữ kiện — câu chứa diễn giải ("cho thấy cầu tuân thủ vẫn vững") phải tách phần OPINION."""
    issues = []
    drivers = (content.get("2") or {}).get("market_drivers") or {}
    for side in ("bullish", "bearish"):
        for i, d in enumerate(drivers.get(side) or [] if isinstance(drivers, dict) else []):
            if isinstance(d, dict) and d.get("tag") == "FACT" and _INTERPRETIVE_RE.search(str(d.get("text") or "")):
                m = _INTERPRETIVE_RE.search(str(d.get("text")))
                issues.append(_issue(
                    "scenario", "3", "warning",
                    f"Nhãn FACT chứa diễn giải (\"{m.group(0)}\") — tách phần diễn giải thành OPINION: \"{str(d.get('text'))[:100]}\"",
                    f"2.market_drivers.{side}[{i}].text",
                ))
    return issues


def check_dev_driver_consistency(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Cùng 1 bài (source_url) không được nằm ở "Diễn biến chính" với 1 chiều và ở cột động lực Mục 2 chiều khác."""
    sec2 = content.get("2") or {}
    impacts = {
        _norm_url(d["source_url"]): d.get("impact") for d in sec2.get("key_developments") or []
        if isinstance(d, dict) and d.get("source_url")
    }
    drivers = sec2.get("market_drivers") or {}
    issues = []
    for side, want in (("bullish", "tăng"), ("bearish", "giảm")):
        for i, d in enumerate(drivers.get(side) or [] if isinstance(drivers, dict) else []):
            url = d.get("source_url") if isinstance(d, dict) else None
            impact = impacts.get(_norm_url(url)) if url else None
            if impact and impact != want:
                issues.append(_issue(
                    "scenario", "3", "error",
                    f"Cùng 1 tin bị đánh giá 2 chiều: Diễn biến chính ghi \"{impact}\" nhưng Mục 2 xếp vào "
                    f"{'động lực tăng' if side == 'bullish' else 'động lực giảm'}: \"{str(d.get('text'))[:100]}\"",
                    f"2.market_drivers.{side}[{i}].text",
                ))
    return issues


_DESIGN_FLAW_MENTION_RE = re.compile(r"R\s*:\s*R|tỷ lệ lời\s*/\s*lỗ|cắt lỗ[^.]{0,40}(?:chặt|sát|gần|ATR)|thiết kế lệnh", re.IGNORECASE)


def check_forecast_review(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Dự báo trước bị chấm SAI (content["2"].forecast_review, tính bằng Python) thì "vs_previous" phải thừa
    nhận — có đoạn đó và nhắc đúng ngày báo cáo đã dự báo sai."""
    review = (content.get("2") or {}).get("forecast_review") or {}
    wrong = [it for it in review.get("items") or [] if isinstance(it, dict) and it.get("verdict") == "wrong"]
    if not wrong:
        return []
    text = str((content.get("3") or {}).get("vs_previous") or "")
    if not text.strip():
        return [_issue(
            "scenario", "3", "error",
            "Có dự báo trước bị chấm SAI (" + ", ".join(_dm_label(it["report_date"]) for it in wrong)
            + ") nhưng không có đoạn \"So với báo cáo trước\" thừa nhận và giải thích nguyên nhân.",
            "3.vs_previous",
        )]
    issues = []
    if any(it.get("design_flaws") for it in wrong) and not _DESIGN_FLAW_MENTION_RE.search(text):
        dates = ", ".join(_dm_label(it["report_date"]) for it in wrong if it.get("design_flaws"))
        issues.append(_issue(
            "scenario", "3", "warning",
            f"Dự báo sai ({dates}) có lỗi THIẾT KẾ LỆNH (R:R tại giá vào < 1,5 hoặc cắt lỗ < 0,5 ATR) nhưng \"So với báo cáo "
            "trước\" chỉ nêu tiền đề phân tích, không nhắc lỗi lệnh.", "3.vs_previous",
        ))
    for it in wrong:
        d = date.fromisoformat(it["report_date"])
        if not re.search(rf"\b0?{d.day}/0?{d.month}\b", text):
            issues.append(_issue(
                "scenario", "3", "warning",
                f"Dự báo báo cáo {_dm_label(it['report_date'])} bị chấm SAI"
                + (" (gắn xác suất Cao)" if it.get("high_confidence_miss") else "")
                + " nhưng đoạn \"So với báo cáo trước\" không nhắc tới báo cáo này.",
                "3.vs_previous",
            ))
    return issues


# Nhãn phòng hộ = tín hiệu thẻ CBAM (services/cbam_estimate.py) — 1 nhãn duy nhất. Nhãn kiểu cũ (buy_now/...) còn ở
# báo cáo trước 18/10/2026: chấp nhận nhưng chỉ kiểm mâu thuẫn hẳn với thẻ CBAM.
HEDGE_ACTIONS = ("increase", "hold", "reduce")
LEGACY_HEDGE_ACTIONS = ("buy_now", "buy_scheduled", "defer")
CBAM_SIGNAL_CONFLICT = {"increase": ("defer",), "reduce": ("buy_now",)}
_PRICE_ZONE_ONLY_RE = re.compile(r"chỉ\s+(?:gom|mua)[^.;\n]{0,40}\d", re.IGNORECASE)
_SCHEDULE_BUY_RE = re.compile(r"\bmua\b(?:\s+EUA)?\s+(?:rải|trải|theo)\s+(?:đều\s+)?(?:theo\s+)?(?:tỷ trọng\s+)?lịch\s+đấu giá", re.IGNORECASE)


def check_hedge_action(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Mỗi báo cáo có giá phải có ĐÚNG 1 hành động phòng hộ ("3".hedge_action) và đối chiếu mọi lệnh đang mở
    ("3".open_positions). Bản 16/10/2026 từng ra 4 tín hiệu trái nhau (CHỜ / mua trải đều 83–86 / hoãn /
    đẩy nhanh) + thẻ CBAM "GIỮ", và bỏ quên lệnh mua 09/10 đang mở."""
    sec3 = content.get("3") or {}
    if not sec3.get("trading_scenarios"):
        return []
    issues: List[Dict[str, str]] = []
    ha = sec3.get("hedge_action")
    sig = ((content.get("2") or {}).get("cbam_estimate") or {}).get("signal")
    if not isinstance(ha, dict) or ha.get("action") not in HEDGE_ACTIONS + LEGACY_HEDGE_ACTIONS:
        issues.append(_issue("scenario", "3", "error",
                             "Thiếu nhãn phòng hộ duy nhất cho sổ SIM (hedge_action).", "3.hedge_action"))
    elif ha["action"] in HEDGE_ACTIONS and sig and ha["action"] != sig:
        issues.append(_issue(
            "scenario", "3", "error",
            f"2 nhãn phòng hộ trong 1 báo cáo: ô phòng hộ \"{ha['action']}\" ≠ thẻ CBAM \"{sig}\".", "3.hedge_action",
        ))
    elif ha["action"] in CBAM_SIGNAL_CONFLICT.get(sig, ()) and "CBAM" not in str(ha.get("text")):
        issues.append(_issue(
            "scenario", "3", "warning",
            f"Hành động phòng hộ \"{ha['action']}\" ngược tín hiệu thẻ CBAM \"{sig}\" mà không giải thích.",
            "3.hedge_action",
        ))
    if isinstance(ha, dict) and _SCHEDULE_BUY_RE.search(str(ha.get("text") or "")):
        issues.append(_issue(
            "scenario", "3", "warning",
            "Phòng hộ ghi \"mua EUA rải/theo lịch đấu giá\" — cách này không khóa được chi phí CBAM (chỉ tạo kho EUA giá vốn ≈ "
            "VWAP); khóa chi phí = mua futures trước rồi bán rải theo lịch đấu giá còn lại.", "3.hedge_action",
        ))
    if isinstance(ha, dict) and _PRICE_ZONE_ONLY_RE.search(str(ha.get("text") or "")):
        issues.append(_issue(
            "scenario", "3", "warning",
            "Phòng hộ ghi \"chỉ gom/mua ở vùng giá\" — phần lõi phải bám lịch đấu giá; vùng giá chỉ cho phần chiến thuật ≤20%.",
            "3.hedge_action",
        ))
    notes = [i for i, sc in enumerate(sec3.get("trading_scenarios") or []) if isinstance(sc, dict) and not _blank(sc.get("hedge_note"))]
    if notes and isinstance(ha, dict):
        issues.append(_issue("scenario", "3", "warning",
                             "Kịch bản vẫn có hedge_note riêng bên cạnh hedge_action — nhiều tín hiệu phòng hộ.",
                             f"3.trading_scenarios[{notes[0]}].hedge_note"))
    review = (content.get("2") or {}).get("forecast_review") or {}
    covered = {
        p.get("report_date") for p in sec3.get("open_positions") or []
        if isinstance(p, dict) and p.get("decision") and not _blank(p.get("text"))
    }
    for it in review.get("items") or []:
        t = it.get("trade") if isinstance(it, dict) else None
        if isinstance(t, dict) and t.get("status") == "open" and it.get("report_date") not in covered:
            issues.append(_issue(
                "scenario", "3", "error",
                f"Lệnh {'mua' if t.get('side') == 'buy' else 'bán'} của báo cáo {_dm_label(it['report_date'])} đang mở "
                f"(cắt lỗ {_fmt(t['stop'])}) nhưng báo cáo không nói giữ mức cũ, dời mức hay đóng lệnh.",
                "3.open_positions",
            ))
    return issues


def check_consistency(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Nhãn "x% so với y" sai phép tính, mô tả vị trí trong biên độ sai, định dạng số/ngày lẫn kiểu Anh
    (services/report_consistency.py — bộ sinh đã tự sửa, check này bắt bản admin sửa tay)."""
    issues = []
    minor_paths: Set[str] = set()
    for f in consistency_issues(content):
        if f.get("minor"):
            if f["path"] not in minor_paths:
                minor_paths.add(f["path"])
                issues.append(_issue("scenario", f["path"].split(".")[0], "info", f["message"], f["path"]))
        else:
            issues.append(_issue("scenario", f["path"].split(".")[0], "error", f["message"], f["path"]))
    return issues


def _dm_label(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m")


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
    session = _eua_last_session(content)
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
            issues += _check_strategy(sc, label, path, eua_close, session)

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
    urls.extend(_policy_tracker_urls(content))
    return urls


def _policy_tracker_urls(content: Dict[str, Any]) -> List[str]:
    """URL nguồn của khối theo dõi chính sách ETS/MSR (content["3"].ets_policy) — CỐ Ý lấy tin tới 21 ngày trước
    (report_generator.ETS_POLICY_LOOKBACK_DAYS), nên không áp quy tắc "tin cũ"."""
    return [
        it["source_url"] for it in (content.get("3") or {}).get("ets_policy") or []
        if isinstance(it, dict) and isinstance(it.get("source_url"), str)
    ]


def check_sources(
    content: Dict[str, Any],
    known_urls: Iterable[str],
    article_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    stale_before: Optional[datetime] = None,
) -> List[Dict[str, str]]:
    """`known_urls`: những URL trong `collect_source_urls(content)` CÓ tồn tại ở bảng articles.
    `article_meta`: _norm_url(url) -> {"source", "published_at"} của các bài đó — kiểm thêm tin cũ
    (đăng trước `stale_before`) và nhãn nguồn khác nhà xuất bản thật của URL."""
    known = {_norm_url(u) for u in known_urls}
    meta_by_url = article_meta or {}
    issues: List[Dict[str, str]] = []
    policy_urls = {_norm_url(u) for u in _policy_tracker_urls(content)}

    def _check_url(url: Optional[str], section: str, label: str, path: str, shown_source: Any = None) -> None:
        if not url:
            return
        if _norm_url(url) not in known:
            issues.append(_issue("source", section, "warning", f"{label}: URL nguồn không tìm thấy trong DB ({url}).", path))
            return
        meta = meta_by_url.get(_norm_url(url))
        if not meta:
            return
        published = meta.get("published_at")
        if stale_before and isinstance(published, datetime) and _norm_url(url) not in policy_urls:
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < stale_before:
                issues.append(_issue(
                    "source", section, "error",
                    f"{label}: tin CŨ — đăng {published.strftime('%d/%m/%Y')}, không phải tin trong khung báo cáo ({url}).",
                    path,
                ))
        expected = news_source_label(meta.get("source") or "", url)
        shown = shown_source.strip() if isinstance(shown_source, str) else ""
        # Chuỗi "Gốc (gốc) → nơi đăng" (report_generator.source_chain_label) hợp lệ khi nơi đăng đúng.
        if shown and shown != expected and not shown.endswith(f"→ {expected}"):
            issues.append(_issue(
                "source", section, "warning",
                f"{label}: nhãn nguồn \"{shown_source.strip()}\" không đúng nhà xuất bản của URL (đúng: {expected}).",
                path,
            ))

    # Bullet KHÔNG có nguồn là hợp lệ (prompt Mục 1: source_index=null khi bullet dựa trên dữ
    # liệu giá, vd bullet giá EUA đầu tiên) — chỉ kiểm URL khi CÓ trích dẫn.
    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        if isinstance(b, dict):
            _check_url(b.get("source_url"), "1", f"Bullet {i + 1}", f"1.bullets[{i}].source_url", b.get("source_name"))

    for i, b in enumerate((content.get("4") or {}).get("bullets") or []):
        if isinstance(b, dict):
            _check_url(b.get("source_url"), "4", f"Tín chỉ/CBAM #{i + 1}", f"4.bullets[{i}].source_url", b.get("source_name"))
    # key_developments hiển thị chung khối "Diễn biến chính" với Mục 4 → gắn note vào "4".
    for i, d in enumerate((content.get("2") or {}).get("key_developments") or []):
        if isinstance(d, dict):
            _check_url(d.get("source_url"), "4", f"Diễn biến chính #{i + 1}", f"2.key_developments[{i}].source_url", d.get("source_name"))
    drivers = (content.get("2") or {}).get("market_drivers") or {}
    for side, side_label in (("bullish", "hỗ trợ giá"), ("bearish", "gây áp lực giá")):
        for i, d in enumerate((drivers.get(side) if isinstance(drivers, dict) else None) or []):
            if isinstance(d, dict):
                _check_url(d.get("source_url"), "3", f"Yếu tố {side_label} #{i + 1}", f"2.market_drivers.{side}[{i}].source_url", d.get("source_name"))

    for i, it in enumerate((content.get("3") or {}).get("ets_policy") or []):
        if isinstance(it, dict):
            _check_url(it.get("source_url"), "3", f"Hồ sơ chính sách ETS #{i + 1}", f"3.ets_policy[{i}].source_url", it.get("source_name"))

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
        _check_url(url, "9", f"Nguồn #{i + 1}", f"{path}.url", it.get("source"))
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
    window_end = target + timedelta(days=CALENDAR_WINDOW_DAYS)
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
            elif ev.get("released_at") and _blank(ev.get("outcome")):
                # Báo cáo phát hành 07:00 giờ VN ngày report_date — đã công bố trước giờ đó thì không còn là "sắp tới".
                try:
                    released = datetime.fromisoformat(str(ev["released_at"]))
                except ValueError:
                    released = None
                publish = datetime.combine(date.fromisoformat(report_date), datetime.min.time().replace(hour=7),
                                           tzinfo=released.tzinfo) if released and released.tzinfo else None
                if publish and released <= publish:
                    issues.append(_issue(
                        "calendar", "8", "warning",
                        f"{label}: đã công bố lúc {released.strftime('%H:%M %d/%m')} (giờ VN), trước giờ phát hành báo cáo, "
                        "nhưng không có kết quả nên hiện ở lịch \"sắp tới\".", f"{path}.outcome",
                    ))

            kind = _event_kind(name)
            if kind:
                if ev_date.isoformat() not in expected[kind]:
                    issues.append(_issue(
                        "calendar", "8", "error",
                        f"{label}: ngày {ev_date.strftime('%d/%m')} sai lịch — {EVENT_KIND_LABEL[kind]}.",
                        f"{path}.date",
                    ))
                found[kind].add(ev_date.isoformat())
                # Số liệu phải đúng kỳ của sự kiện — file nguồn luôn là kỳ mới nhất nên dễ gắn nhầm
                # số tuần trước cho sự kiện mới (vd EIA 14/10 mang số "tuần kết thúc 02/10").
                week = re.search(r"tuần kết thúc\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(ev.get("outcome") or ""))
                if week:
                    week_iso = f"{week.group(3)}-{int(week.group(2)):02d}-{int(week.group(1)):02d}"
                    if not data_matches_event(name, ev_date, week_iso):
                        issues.append(_issue(
                            "calendar", "8", "error",
                            f"{label} {ev_date.strftime('%d/%m')}: kết quả là số của tuần kết thúc "
                            f"{week.group(1)}/{week.group(2)} — không phải kỳ công bố ngày này (số kỳ trước?).",
                            f"{path}.outcome",
                        ))

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

        # Đấu giá EUA EEX đã trở lại lịch (tính từ lịch EEX chính thức — report_generator._auction_calendar_events).
        if re.search(r"\bAPI\b", name) and not _event_kind(name):
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


# ── 3b. Số liệu ↔ tin gốc ────────────────────────────────────────────

# Số kiểu Việt (1.386,75 / 96 / -1,5) hoặc kiểu Anh LLM lỡ chép (85.84). Bỏ số dính chữ/ký tự
# (mã như API2, Q4, CO2) — chỉ lấy token số đứng riêng.
_TEXT_NUM_RE = re.compile(r"(?<![\w/.,])[+\-−]?\d+(?:[.,]\d+)*(?![\w/])")
_ARTICLE_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_DATE_TOKEN_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_SELF_FLAGGED_MISMATCH_RE = re.compile(r"chưa khớp|không khớp", re.IGNORECASE)
FACT_SMALL_INT_MAX = 10  # số đếm nhỏ ("3 tháng", "2 nước") hay được viết bằng chữ trong bài gốc


def _number_candidates(raw: str) -> Set[float]:
    """Mọi cách hiểu hợp lý của 1 token số: '1.386' = 1386 (kiểu Việt) hoặc 1,386 (kiểu Anh)."""
    raw = raw.replace("−", "-").lstrip("+")
    out: Set[float] = set()
    for cand in (
        raw.replace(".", "").replace(",", "."),  # kiểu Việt
        raw.replace(",", ""),                   # kiểu Anh
    ):
        try:
            out.add(abs(float(cand)))
        except ValueError:
            pass
    return out


def _decimals(raw: str) -> int:
    m = re.search(r"[.,](\d+)$", raw)
    return len(m.group(1)) if m and len(m.group(1)) != 3 else 0


def _numbers_in(text: str) -> Set[float]:
    out: Set[float] = set()
    for raw in _ARTICLE_NUM_RE.findall(text or ""):
        out |= _number_candidates(raw)
    return out


def _supported(raw: str, pool: Set[float]) -> bool:
    """Số trong câu khớp 1 số trong `pool` — cho phép làm tròn theo số chữ số thập phân đang hiển thị."""
    d = _decimals(raw)
    for v in _number_candidates(raw):
        for a in pool:
            if round(a, d) == round(v, d) or abs(a - v) <= 0.005 * max(abs(v), 1e-9):
                return True
    return False


def _shown_calculation(raw: str, text: str, pool: Set[float]) -> bool:
    """Số tự tính có phép tính ghi ngay trong câu: bằng a / b hoặc a × b của 2 số khác trong CÙNG câu (cả 2 có trong bài
    nguồn), bỏ qua bậc 10 do đơn vị triệu/nghìn — vd "≈ 559 USD/t (11,7 triệu USD / 20.932 t)" = 11,7e6 / 20.932."""
    vals = [v for v in _number_candidates(raw) if v > 0]
    operands = [o for tok in _TEXT_NUM_RE.findall(text) if tok != raw and _supported(tok, pool)
                for o in _number_candidates(tok) if o > 0]
    for v in vals:
        for i, a in enumerate(operands):
            for b in operands[i + 1:]:
                for r in (a / b, b / a, a * b):
                    d = math.log10(v) - math.log10(r)
                    if abs(d - round(d)) < 0.0005:
                        return True
    return False


def _checkable_number(raw: str) -> bool:
    vals = _number_candidates(raw)
    if not vals:
        return False
    v = min(vals)
    if float(v).is_integer() and (v <= FACT_SMALL_INT_MAX or 1990 <= v <= 2100):
        return False  # số đếm nhỏ / năm
    return True


_NUM = r"(\d+(?:[.,]\d+)?)"
_SCALE_UNIT = r"\s*(triệu|tỷ|nghìn|million|billion)\s+(thùng|tấn|m3|mét khối|MWh|GWh|TWh|EUR|USD|tCO2|hạn ngạch)"
# "đã rút 330/400 triệu thùng" | "330 trên tổng 400 triệu thùng"
_FRACTION_RE = re.compile(_NUM + r"\s*(?:/|trên tổng(?: số)?|trong tổng(?: số)?|trên)\s*" + _NUM + _SCALE_UNIT, re.IGNORECASE)
# "còn lại khoảng 100 triệu thùng" | "còn ~96 triệu thùng"
_REMAINING_RE = re.compile(r"còn(?:\s+lại)?(?:\s+(?:khoảng|chừng|gần|~|xấp xỉ))?\s*~?\s*" + _NUM + _SCALE_UNIT, re.IGNORECASE)


def _vnf(raw: str) -> float:
    return float(raw.replace(".", "").replace(",", ".")) if "," in raw else float(raw)


def _check_remaining_math(text: str, section: str, label: str, path: str) -> List[Dict[str, str]]:
    """Cùng 1 câu/bullet nêu "đã dùng X/Y <đơn vị>" và "còn Z <cùng đơn vị>" mà Y − X ≠ Z → số học mâu thuẫn
    (vd IEA: "đã rút 330/400 triệu thùng" nhưng "còn 100 triệu" — đúng ra 70)."""
    issues = []
    for f in _FRACTION_RE.finditer(text):
        used, total = _vnf(f.group(1)), _vnf(f.group(2))
        unit = (f.group(3).lower(), f.group(4).lower())
        for r in _REMAINING_RE.finditer(text):
            if (r.group(2).lower(), r.group(3).lower()) != unit:
                continue
            remaining = _vnf(r.group(1))
            if abs((total - used) - remaining) > max(1.0, 0.05 * total):
                issues.append(_issue(
                    "fact", section, "error",
                    f"{label}: số học mâu thuẫn — \"{f.group(0).strip()}\" nghĩa là còn {_fmt(total - used)} {' '.join(unit)}, "
                    f"nhưng lại ghi \"{r.group(0).strip()}\". Dùng số của nguồn rõ nhất và chú thích độ lệch.",
                    path,
                ))
    return issues


def _check_remaining_vs_section6(
    text: str, content: Dict[str, Any], item: Dict[str, Any], section: str, label: str, path: str,
) -> List[Dict[str, str]]:
    """"còn Z <đơn vị>" ở Tóm tắt/Diễn biến chính khác "còn W <cùng đơn vị>" mà 1 bài KHÁC ở Mục 6 nêu (vd tóm tắt
    ghi 100 triệu thùng theo tiêu đề làm tròn, Argus ở Mục 6 ghi khoảng 96) → báo để dùng số chính xác hơn."""
    mine = [(_vnf(m.group(1)), (m.group(2).lower(), m.group(3).lower())) for m in _REMAINING_RE.finditer(text)]
    if not mine:
        return []
    issues = []
    own_url = _norm_url(item.get("source_url") or "") if item.get("source_url") else None
    for region in ("international", "vietnam"):
        for art in (content.get("6") or {}).get(region) or []:
            if not isinstance(art, dict) or (own_url and _norm_url(art.get("url") or "") == own_url):
                continue
            for m in _REMAINING_RE.finditer(str(art.get("summary") or "")):
                other, unit = _vnf(m.group(1)), (m.group(2).lower(), m.group(3).lower())
                for value, my_unit in mine:
                    if unit == my_unit and value and abs(other - value) / value > 0.03:
                        issues.append(_issue(
                            "fact", section, "warning",
                            f"{label}: ghi còn {_fmt(value)} {' '.join(unit)} nhưng bài {art.get('source') or art.get('url')} ở Tin tức "
                            f"chi tiết nêu còn {_fmt(other)} — dùng số chính xác hơn và chú thích độ lệch (vd số làm tròn trong tiêu đề).",
                            path,
                        ))
    return issues


def check_facts(content: Dict[str, Any], article_texts: Dict[str, str]) -> List[Dict[str, str]]:
    """Mọi con số trong câu CÓ trích nguồn (Tóm tắt, Diễn biến chính, Mục 4, yếu tố thị trường) phải có
    trong chính bài nguồn (`article_texts`: _norm_url -> tiêu đề + nội dung bài). Số lấy từ bảng giá /
    khối ước tính CBAM của báo cáo được miễn (bullet hay kèm giá phiên). Bài không có trong DB → bỏ qua
    (check_sources đã báo URL lạ). Bullet tự ghi "số liệu chưa khớp" → gợi ý dùng nguồn rõ hơn."""
    issues: List[Dict[str, str]] = []
    sec2 = content.get("2") or {}
    report_pool = _numbers_in(" ".join(
        str(x) for x in (sec2.get("prices"), sec2.get("cbam_estimate"), sec2.get("key_facts")) if x
    ))

    items: List[tuple] = []
    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        items.append(("1", f"Tóm tắt — bullet {i + 1}", f"1.bullets[{i}]", b))
    for i, d in enumerate(sec2.get("key_developments") or []):
        items.append(("4", f"Diễn biến chính #{i + 1}", f"2.key_developments[{i}]", d))
    for i, b in enumerate((content.get("4") or {}).get("bullets") or []):
        items.append(("4", f"Tín chỉ/CBAM #{i + 1}", f"4.bullets[{i}]", b))
    for i, d in enumerate(_market_drivers(sec2)):
        items.append(("3", f"Yếu tố thị trường #{i + 1}", f"2.market_drivers[{i}]", d))

    for section, label, path, it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or it.get("summary") or "")
        if _SELF_FLAGGED_MISMATCH_RE.search(text):
            issues.append(_issue(
                "fact", section, "warning",
                f"{label}: tự ghi số liệu nguồn chưa khớp — kiểm tra bài khác trong báo cáo có nêu rõ hơn cùng chỉ tiêu "
                "không (dùng số đó, ghi nguồn và độ lệch) thay vì để số mâu thuẫn.",
                f"{path}.text",
            ))
        issues += _check_remaining_math(text, section, label, f"{path}.text")
        issues += _check_remaining_vs_section6(text, content, it, section, label, f"{path}.text")
        url = it.get("source_url")
        source_text = article_texts.get(_norm_url(url)) if url else None
        if not source_text:
            continue
        pool = _numbers_in(source_text)
        body = _DATE_TOKEN_RE.sub(" ", re.sub(r"\*\*[^*]*\*\*", " ", text))  # bỏ ngày + nhãn in đậm đầu bullet
        missing = [
            raw for raw in _TEXT_NUM_RE.findall(body)
            if _checkable_number(raw) and not _supported(raw, pool) and not _supported(raw, report_pool)
            and not _shown_calculation(raw, body, pool)
        ]
        if missing:
            shown = ", ".join(dict.fromkeys(m.replace("−", "-") for m in missing))
            issues.append(_issue(
                "fact", section, "warning",
                f"{label}: số {shown} không có trong bài nguồn ({it.get('source_name') or url}) — số tự tính/lấy từ bài khác "
                "phải ghi rõ cách tính hoặc nguồn.",
                f"{path}.text",
            ))
    return issues


# ── 3c. Nhãn mã instrument ↔ giá thật ───────────────────────────────

# Tên gọi CHẮC CHẮN của từng mã (không gồm từ chung như "than", "khí", "hạn ngạch").
INSTRUMENT_LABEL_ALIASES = {
    "EUA": (r"EUA",),
    "TTF": (r"TTF",),
    "BRENT": (r"Brent",),
    "WTI": (r"WTI",),
    "GASOIL": (r"Gasoil", r"gas ?oil"),
    "API2": (r"API ?2",),
    "API4": (r"API ?4", r"Richards Bay"),
    "NEWC": (r"NEWC", r"Newcastle"),
    "DEBY1": (r"DEBY1", r"điện Đức"),
    "NG": (r"Henry Hub",),
}
_LABEL_RES = {
    code: re.compile(r"(?<![\w])(?:" + "|".join(alts) + r")(?![\w])", re.IGNORECASE)
    for code, alts in INSTRUMENT_LABEL_ALIASES.items()
}
_PRICE_UNIT_IN_SENTENCE_RE = re.compile(r"USD|EUR|€|\$|/t\b|/tấn|/MWh|/bbl|/thùng|/MMBtu", re.IGNORECASE)
LABEL_MIN_PRICE = 10.0  # số nhỏ (Δ%, hệ số) dễ trùng ngẫu nhiên — chỉ so các số cỡ giá


def check_technical_claims(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Nhận định kỹ thuật trái số liệu phiên (report_consistency.session_signals): "chưa có bằng chứng cạn lực" khi
    phiên cuối đủ 2 điều kiện cạn lực, gap dưới 0,25 ATR gắn là tín hiệu (bản 17/10: Phân tích "lực bán vẫn chiếm ưu
    thế, chưa có bằng chứng cạn lực" trong khi phiên 16/10 biên ×0,73 ATR và khối lượng −25,8%)."""
    sig = session_signals((content.get("2") or {}).get("chart_data") or [])
    if not sig:
        return []
    texts = _report_texts(content)
    ha = (content.get("3") or {}).get("hedge_action")
    if isinstance(ha, dict):
        texts.append(("3", "3.hedge_action.text", str(ha.get("text") or "")))
    issues = []
    for section, path, text in texts:
        for msg in technical_claim_issues(text, sig):
            issues.append(_issue("scenario", section, "warning", msg, path))
    # Gap nhỏ được xếp thành 1 động lực riêng ở Mục 2 (bản 17/10: FACT "mở cửa với khoảng trống giảm" — gap 0,11 = 0,06 ATR).
    if not sig["gap_significant"]:
        drivers = (content.get("2") or {}).get("market_drivers") or {}
        for side in ("bullish", "bearish"):
            for i, d in enumerate(drivers.get(side) or [] if isinstance(drivers, dict) else []):
                text = str(d.get("text") if isinstance(d, dict) else d or "")
                path = f"2.market_drivers.{side}[{i}].text"
                if re.search(r"\bgap\b|khoảng trống", text, re.IGNORECASE) and not any(it["field_path"] == path for it in issues):
                    issues.append(_issue(
                        "scenario", "3", "warning",
                        f"Động lực dựa trên gap mở cửa {abs(sig['gap']):.2f} = ×{sig['gap_atr']:.2f} ATR — dưới 0,25 ATR là nhiễu, "
                        f"không đủ làm 1 động lực: \"{text[:90]}\"", path,
                    ))
    return issues


# Tên hãng tin ghi ngay trong câu ("theo TASS", "(Reuters 16/10)") phải là nhà xuất bản/hãng gốc của CHÍNH bài được
# trích; hãng truyền thông nhà nước thì nên dẫn nguồn sơ cấp (thông cáo Hội đồng/EC).
_AGENCY_RE = re.compile(
    r"(?:\btheo\s+|\()\s*(TASS|RIA(?: Novosti)?|Xinhua|Tân Hoa Xã|Reuters|Bloomberg|Politico|Financial Times|FT|AFP|AP|"
    r"Argus|Platts|S&P Global|Montel|Carbon Pulse|Euractiv|Interfax)\b",
    re.IGNORECASE,
)
STATE_MEDIA = ("tass", "ria", "ria novosti", "xinhua", "tân hoa xã")
_INLINE_SOURCE_RE = re.compile(r"\(Nguồn:\s*([^)]+)\)")


def check_inline_attribution(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Bản 17/10: "Đề xuất kho dự trữ khí đốt ... (TASS 16/10)" trong khi link là Politico qua cafef.vn."""
    items: List[tuple] = []
    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        if isinstance(b, dict):
            items.append(("1", f"1.bullets[{i}].text", str(b.get("text") or ""), str(b.get("source_name") or "")))
    sec2 = content.get("2") or {}
    for i, d in enumerate(sec2.get("key_developments") or []):
        if isinstance(d, dict):
            items.append(("4", f"2.key_developments[{i}].text", f"{d.get('title') or ''} {d.get('text') or ''}", str(d.get("source_name") or "")))
    for i, blk in enumerate((content.get("3") or {}).get("analysis_blocks") or []):
        if isinstance(blk, dict):
            for line in str(blk.get("content") or "").split("\n"):
                src = _INLINE_SOURCE_RE.search(line)
                items.append(("3", f"3.analysis_blocks[{i}].content", line, src.group(1) if src else ""))
    issues = []
    for section, path, text, label in items:
        for m in _AGENCY_RE.finditer(text):
            agency = m.group(1)
            if agency.lower() in STATE_MEDIA:
                issues.append(_issue(
                    "source", section, "warning",
                    f"Dẫn \"{agency}\" (truyền thông nhà nước) — nên dẫn nguồn sơ cấp (thông cáo Hội đồng/EC/chính phủ) "
                    f"hoặc đúng nhà xuất bản của bài được trích ({label or 'không rõ'}): \"{text[:90]}\"", path,
                ))
            elif label and agency.lower() not in label.lower():
                issues.append(_issue(
                    "source", section, "warning",
                    f"Câu ghi \"theo {agency}\" nhưng bài được trích là {label} — ghi đúng hãng gốc/nhà xuất bản (hệ thống đã gắn "
                    f"nhãn nguồn, không cần tự ghi tên hãng): \"{text[:90]}\"", path,
                ))
    return issues


def _report_texts(content: Dict[str, Any]) -> List[tuple]:
    """(section, field_path, text) của các phần văn xuôi có thể nhắc giá theo mã."""
    out: List[tuple] = []
    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        if isinstance(b, dict):
            out.append(("1", f"1.bullets[{i}].text", str(b.get("text") or "")))
    sec2 = content.get("2") or {}
    for i, d in enumerate(sec2.get("key_developments") or []):
        if isinstance(d, dict):
            out.append(("4", f"2.key_developments[{i}].text", str(d.get("text") or "")))
    for i, d in enumerate(_market_drivers(sec2)):
        if isinstance(d, dict):
            out.append(("3", f"2.market_drivers[{i}].text", str(d.get("text") or "")))
    sec3 = content.get("3") or {}
    for i, blk in enumerate(sec3.get("analysis_blocks") or []):
        if isinstance(blk, dict):
            out.append(("3", f"3.analysis_blocks[{i}].content", str(blk.get("content") or "")))
    notes = sec3.get("instrument_notes")
    if isinstance(notes, dict):
        for k, v in notes.items():
            out.append(("3", f"3.instrument_notes.{k}", str(v)))
    elif isinstance(notes, list):
        for i, v in enumerate(notes):
            out.append(("3", f"3.instrument_notes[{i}]", str(v.get("note") if isinstance(v, dict) else v)))
    biz = content.get("biz") or {}
    for key in ("short_term", "long_term"):
        for i, it in enumerate(biz.get(key) or []):
            if isinstance(it, dict):
                out.append(("biz", f"biz.{key}[{i}]", " ".join(str(v) for v in it.values() if isinstance(v, str))))
    return out


def check_instrument_labels(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Nhãn mã trong văn bản phải khớp dữ liệu thật của báo cáo (bảng giá Mục 2):
    - ERROR: câu nhắc mã X kèm 1 số ĐÚNG BẰNG giá đóng cửa của mã Y khác (Y không được nhắc trong câu,
      số đó không phải giá của X) — vd ghi "API2 132,0" trong khi 132,0 là giá API4.
    - WARNING: câu nêu giá (có đơn vị) cho mã X mà bảng giá ngày này KHÔNG có X — số không kiểm chứng
      được từ dữ liệu hệ thống (vd báo cáo nhắc giá API2 khi nguồn API2 đang hỏng)."""
    prices = [p for p in ((content.get("2") or {}).get("prices") or []) if isinstance(p, dict)]
    if not prices or is_no_price_content(content):
        return []
    # Mã theo họ — bỏ hậu tố kỳ hạn ("EUA_DEC26" → "EUA") để khớp tên gọi trong văn bản.
    close_by_code: Dict[str, float] = {}
    for p in prices:
        code = str(p.get("code") or "").upper().split("_")[0]
        if isinstance(p.get("close"), (int, float)) and code != CBAM_CODE:
            close_by_code.setdefault(code, p["close"])
    issues: List[Dict[str, str]] = []
    seen: Set[tuple] = set()
    for section, path, text in _report_texts(content):
        for sentence in re.split(r"(?<=[^\d])[.;]\s+|\n+", text):
            mentioned = {code for code, rx in _LABEL_RES.items() if rx.search(sentence)}
            if not mentioned:
                continue
            nums = [raw for raw in _TEXT_NUM_RE.findall(_DATE_TOKEN_RE.sub(" ", sentence))]
            for code in sorted(mentioned):
                if code not in close_by_code and code in INSTRUMENT_LABEL_ALIASES and _PRICE_UNIT_IN_SENTENCE_RE.search(sentence) \
                        and len(mentioned) == 1 and nums and (code, path, "missing") not in seen:
                    seen.add((code, path, "missing"))
                    issues.append(_issue(
                        "price", section, "warning",
                        f"Nhắc giá {code} nhưng bảng giá ngày này không có {code} — số liệu không kiểm chứng được từ dữ liệu "
                        f"hệ thống: \"{sentence.strip()[:120]}\"",
                        path,
                    ))
                own = close_by_code.get(code)
                for raw in nums:
                    vals = _number_candidates(raw)
                    if not vals or max(vals) < LABEL_MIN_PRICE:
                        continue
                    if own is not None and any(_close_enough(v, own) for v in vals):
                        continue
                    for other, close in close_by_code.items():
                        if other == code or other in mentioned:
                            continue
                        if any(_close_enough(v, close) for v in vals) and (code, other, path) not in seen:
                            seen.add((code, other, path))
                            issues.append(_issue(
                                "price", section, "error",
                                f"Số {raw} được gắn nhãn {code} nhưng là giá đóng cửa của {other} ({_fmt(close)}) — nhãn mã "
                                f"không khớp dữ liệu: \"{sentence.strip()[:120]}\"",
                                path,
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

    # Gợi ý CŨ được đánh dấu "ĐÃ KÍCH HOẠT" — đối chiếu lại với giá thật (ngưỡng giá) hoặc nguồn tin.
    for i, it in enumerate(biz.get("reminders") or []):
        if not isinstance(it, dict) or it.get("outcome") != "triggered":
            continue
        path = f"biz.reminders[{i}]"
        label = f"Gợi ý {it.get('suggested_date') or ''} đánh dấu ĐÃ KÍCH HOẠT".replace("  ", " ")
        trigger = str(it.get("trigger") or "")
        mem_rule = (suggestions.get(it.get("id")) or {}).get("trigger_rule")
        rule = biz_memory.normalize_trigger_rule(mem_rule, valid_codes) if mem_rule else None
        rule = biz_memory.resolve_price_rule(rule, trigger, prices)
        if rule:
            if not biz_memory.check_price_rule(rule, prices):
                price = next((p for p in prices if str(p.get("code", "")).upper() == rule["code"]), None)
                close = price.get("close") if price else None
                issues.append(_issue(
                    "biz", "biz", "error",
                    f"{label} nhưng ngưỡng {rule['code']} {rule['op']} {_fmt(rule['value'])} CHƯA chạm "
                    f"(giá đóng cửa {_fmt(close) if isinstance(close, (int, float)) else 'không có'}).",
                    f"{path}.outcome",
                ))
        elif biz_memory.looks_like_price_condition(trigger):
            issues.append(_issue(
                "biz", "biz", "error",
                f"{label} nhưng điều kiện là ngưỡng giá của mã không có trong bảng giá — không kiểm chứng được ({trigger[:80]}).",
                f"{path}.outcome",
            ))
        elif _blank(it.get("source_url")):
            issues.append(_issue("biz", "biz", "warning", f"{label} nhưng không có nguồn tin làm bằng chứng.", f"{path}.source_url"))

    for i, it in enumerate(biz.get("long_term") or []):
        path = f"biz.long_term[{i}]"
        if not isinstance(it, dict):
            issues.append(_issue("biz", "biz", "warning", f"Gợi ý dài hạn #{i + 1}: sai định dạng.", path))
            continue
        for field, name in (("opportunity", "cơ hội"), ("solution", "giải pháp")):
            if _blank(it.get(field)):
                issues.append(_issue("biz", "biz", "error", f"Gợi ý dài hạn #{i + 1}: thiếu {name}.", f"{path}.{field}"))
        # Chuẩn "đưa thành dự án thí điểm" (áp từ báo cáo có schema mới — bản cũ không có các trường này).
        if report_date and report_date >= BIZ_QUANTIFIED_SINCE:
            missing = [name for field, name in (("pilot", "quy mô thí điểm"), ("kpi", "KPI"), ("owner", "đơn vị phụ trách"),
                                                ("financials", "chi phí/doanh thu ước tính")) if _blank(it.get(field))]
            if missing:
                issues.append(_issue("biz", "biz", "warning",
                                     f"Gợi ý dài hạn #{i + 1}: chưa lượng hóa — thiếu {', '.join(missing)}.", path))
        text = " ".join(str(v) for v in it.values() if isinstance(v, str))
        for pattern, why in BIZ_OFF_LENS:
            if re.search(pattern, text, re.IGNORECASE):
                issues.append(_issue("biz", "biz", "warning", f"Gợi ý dài hạn #{i + 1}: {why}", path))
    for i, it in enumerate(short_term):
        if not isinstance(it, dict):
            continue
        if report_date and report_date >= BIZ_QUANTIFIED_SINCE and _blank(it.get("kpi")):
            issues.append(_issue("biz", "biz", "info", f"Gợi ý ngắn hạn #{i + 1}: thiếu KPI đo được.", f"biz.short_term[{i}].kpi"))
        text = " ".join(str(v) for v in it.values() if isinstance(v, str))
        for pattern, why in BIZ_OFF_LENS:
            if re.search(pattern, text, re.IGNORECASE):
                issues.append(_issue("biz", "biz", "warning", f"Gợi ý ngắn hạn #{i + 1}: {why}", f"biz.short_term[{i}]"))
    return issues + check_biz_sources(content, suggestions)


BIZ_QUANTIFIED_SINCE = "2026-10-18"
# Lỗi khái niệm/lăng kính đã gặp ở bản 17/10 (report_generator.METALS_LENS_RULE).
BIZ_OFF_LENS = (
    (r"dấu chân carbon[^.]{0,40}Scope\s*1\s*[-–/]\s*2|Scope\s*1\s*[-–/]\s*2[^.]{0,40}dấu chân carbon sản phẩm",
     "\"dấu chân carbon sản phẩm Scope 1-2\" sai khái niệm — phát thải nhúng CBAM/ISO 14067 gồm cả tiền chất thượng nguồn."),
    (r"\bSAF\b|dầu ăn đã qua sử dụng|\bUCO\b", "căn cứ SAF/dầu ăn đã qua sử dụng không có đường truyền tới kinh doanh kim loại."),
    (r"Hormuz[^.]{0,80}(?:tuyến|vận chuyển|cước)[^.]{0,40}(?:Việt Nam|EU)",
     "tuyến Việt Nam–EU không đi qua Hormuz — kênh đúng là nguồn cung nhôm vùng Vịnh."),
)


def _listed_urls(content: Dict[str, Any]) -> Set[str]:
    """URL có trong Tin tức chi tiết (Mục 6) hoặc Nguồn tham khảo (Mục 9) — nơi người đọc tra lại tin."""
    urls: Set[str] = set()
    for region in ("international", "vietnam"):
        for a in (content.get("6") or {}).get(region) or []:
            if isinstance(a, dict) and a.get("url"):
                urls.add(_norm_url(a["url"]))
    for it in (content.get("9") or {}).get("items") or []:
        if isinstance(it, dict) and it.get("url"):
            urls.add(_norm_url(it["url"]))
    return urls


def check_biz_sources(content: Dict[str, Any], suggestions: Dict[int, Dict[str, Any]]) -> List[Dict[str, str]]:
    """Mọi gợi ý kinh doanh MỚI phải kiểm chứng được: có bài nguồn, và bài đó nằm trong Mục 6/Mục 9. Gợi ý
    ngắn hạn là ngưỡng giá (có trigger_rule trong bộ nhớ hoặc câu điều kiện giá) được miễn nguồn tin."""
    biz = content.get("biz") or {}
    listed = _listed_urls(content)
    issues: List[Dict[str, str]] = []
    for key, name in (("short_term", "ngắn hạn"), ("long_term", "dài hạn")):
        for i, it in enumerate(biz.get(key) or []):
            if not isinstance(it, dict):
                continue
            label, path = f"Gợi ý {name} #{i + 1}", f"biz.{key}[{i}].source_url"
            url = it.get("source_url")
            if not url:
                if key == "short_term" and (
                    (suggestions.get(it.get("id")) or {}).get("trigger_rule")
                    or biz_memory.looks_like_price_condition(str(it.get("trigger") or ""))
                ):
                    continue
                issues.append(_issue("biz", "biz", "warning", f"{label}: không có nguồn tin — người đọc không kiểm chứng được.", path))
            elif _norm_url(url) not in listed:
                issues.append(_issue(
                    "biz", "biz", "warning",
                    f"{label}: nguồn ({it.get('source_name') or url}) không có trong Tin tức chi tiết/Nguồn tham khảo của báo cáo.",
                    path,
                ))
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
    """Chạy 7 check cho báo cáo `report_date` -> summarize() + "news_stats"."""
    target_date = report_data_date(report_date)

    db_prices, latest_price_date = await _load_db_prices(session, target_date, report_price_date(content))

    urls = collect_source_urls(content)
    known_urls: Set[str] = set()
    article_meta: Dict[str, Dict[str, Any]] = {}
    article_texts: Dict[str, str] = {}
    if urls:
        candidates = {u for u in urls} | {_norm_url(u) for u in urls} | {_norm_url(u) + "/" for u in urls}
        for r in (await session.execute(
            select(Article.url, Article.source, Article.published_at, Article.title, Article.content)
            .where(Article.url.in_(candidates))
        )).all():
            known_urls.add(r.url)
            article_meta[_norm_url(r.url)] = {"source": r.source, "published_at": r.published_at}
            article_texts[_norm_url(r.url)] = f"{r.title or ''}\n{r.content or ''}"
    stale_before = news_window(target_date)[0] - timedelta(days=STALE_NEWS_MAX_AGE_DAYS)

    biz = content.get("biz") or {}
    biz_ids = [
        it["id"] for it in (biz.get("short_term") or []) + (biz.get("reminders") or [])
        if isinstance(it, dict) and isinstance(it.get("id"), int)
    ]
    suggestions: Dict[int, Dict[str, Any]] = {}
    if biz_ids:
        for s in (await session.execute(select(BizSuggestion).where(BizSuggestion.id.in_(biz_ids)))).scalars():
            suggestions[s.id] = {"trigger_rule": s.trigger_rule, "status": s.status, "first_report_date": s.first_report_date}
    valid_codes = {c.upper() for c in (await session.execute(select(Instrument.code))).scalars()}
    news_issues, news_stats = check_news(content, await _load_window_articles(session, target_date))

    issues_by_check = {
        "price": check_prices(content, db_prices, latest_price_date, report_date) + check_instrument_labels(content),
        "scenario": (
            check_scenarios(content) + check_forecast_review(content) + check_fuel_direction(content)
            + check_hedge_action(content) + check_consistency(content) + check_dev_driver_consistency(content)
            + check_scenario_basis(content) + check_horizon_overlap(content) + check_breakout_levels(content)
            + check_legislation_claims(content) + check_fact_tags(content)
            + check_fuel_thresholds(content) + check_long_term_basis(content) + check_technical_claims(content)
        ),
        "source": check_sources(content, known_urls, article_meta, stale_before) + check_inline_attribution(content),
        "fact": check_facts(content, article_texts),
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
        f"KẾT QUẢ QC BÁO CÁO NGÀY {report_date} (trạng thái: {status}) — Jack kiểm tra tự động bằng cách "
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


async def qc_report_text(session: AsyncSession, report_date: str, *, published_only: bool = False) -> str:
    """Entry point cho tool `jack_qc` của Jenny chat."""
    report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
    if not report:
        return f"Không có báo cáo nào ngày {report_date}."
    if published_only and report.status != "published":
        # Người dùng thường (MCP) chỉ được QC báo cáo đã phát hành — không lộ bản draft.
        return f"Không có báo cáo đã phát hành cho ngày {report_date}."
    if report.status == "generating":
        return f"Báo cáo ngày {report_date} đang được sinh — chưa QC được, hãy thử lại sau ít phút."
    if not report.content:
        return f"Báo cáo ngày {report_date} không có nội dung (trạng thái: {report.status}{', lỗi: ' + report.error_message if report.error_message else ''})."
    result = await run_report_qc(session, report_date, report.content)
    return format_qc_report(report_date, report.status, result)
