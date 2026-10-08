"""Lucy — agent QC báo cáo ngày TRƯỚC khi admin duyệt & xuất bản.

Đợt 1 (MVP) gồm 5 check RULE-BASED (không tốn LLM), đối chiếu nội dung JSON
báo cáo (`reports.content`) với nguồn sự thật trong DB / lịch cố định:

    check       section(s)   nguồn sự thật
    price       "2"          bảng `prices` (giá chốt phiên)
    scenario    "3"          logic + schema trading_scenarios / dòng "**Tổng hợp:**"
    source      "1","4","9"  bảng `articles` (URL nguồn có thật)
    calendar    "8"          lịch định kỳ EIA/Baker Hughes (_compute_recurring_calendar_events)
    biz         "biz"        schema gợi ý + `biz_suggestions.trigger_rule`

Mỗi hàm `check_*` là hàm THUẦN (nhận content + dữ liệu đã truy vấn sẵn, trả
list issue) để test được không cần DB — `run_report_qc()` lo phần truy vấn.
Mỗi issue: {"check", "section", "severity": "error"|"warning"|"info",
"message", "field_path"}; `section` khớp key của report.content để frontend
gắn note đúng chỗ (components/ReportDocument.tsx).

Đợt 2: thêm 2 check LLM — "consistency" (nhất quán nội bộ Mục 1 ↔ 2 ↔ 3 ↔ biz) và
"causal" (chuỗi nhân quả EUA) — xem services/report_qc_llm.py. Bỏ qua được bằng
use_llm=False (QC nhanh); LLM lỗi thì điểm check đó = None, không tính vào tổng.
"""
import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Article, BizSuggestion, Instrument, Price
from services import biz_memory, report_qc_llm
from services.eua_framework_admin import get_overrides_map
from services.report_generator import (
    CBAM_CODE,
    _compute_recurring_calendar_events,
    _parse_event_date,
    report_data_date,
)

logger = logging.getLogger(__name__)

RULE_CHECKS = ("price", "scenario", "source", "calendar", "biz")
CHECKS = RULE_CHECKS + report_qc_llm.LLM_CHECKS
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

# Trigger_rule lệch quá xa giá hiện tại → nhiều khả năng LLM nhầm đơn vị/mã.
BIZ_RULE_MAX_DISTANCE = 0.5


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


def check_prices(
    content: Dict[str, Any],
    db_prices: Dict[str, Dict[str, Any]],
    latest_price_date: Optional[str],
) -> List[Dict[str, str]]:
    """`db_prices`: code -> {"name", "close", "day_change_pct", "week_change_pct"} của ĐÚNG
    ngày giá báo cáo dùng (không gồm CBAM — giá chốt theo quý, không so theo phiên).
    `latest_price_date`: ngày giá mới nhất trong DB tính tới ngày dữ liệu báo cáo."""
    issues: List[Dict[str, str]] = []
    sec = content.get("2") or {}
    rows = sec.get("prices") or []

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

        for field, label in (("day_change_pct", "Δ ngày"), ("week_change_pct", "Δ tuần")):
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


def check_scenarios(content: Dict[str, Any]) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    sec = content.get("3") or {}
    scenarios = sec.get("trading_scenarios") or []

    summary = extract_eua_summary(content)
    sentiment: Optional[str] = None
    if summary is None:
        no_info = any(
            "Không có thông tin mới" in str(b) for b in sec.get("analysis_blocks") or []
        )
        if not no_info:
            issues.append(_issue("scenario", "3", "warning", "Thiếu dòng \"**Tổng hợp:**\" kết luận chiều giá EUA.", "3.analysis_blocks"))
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

    if not scenarios:
        issues.append(_issue("scenario", "3", "error", "Không có kịch bản giao dịch nào.", "3.trading_scenarios"))
        return issues

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

        strategy = str(sc.get("trading_strategy") or "")
        if strategy and "Entry" not in strategy:
            issues.append(_issue("scenario", "3", "info", f"{label}: chiến lược chưa có mốc Entry.", f"{path}.trading_strategy"))

    if "ngắn hạn" not in seen_horizons:
        issues.append(_issue("scenario", "3", "warning", "Thiếu kịch bản \"ngắn hạn\" — khối TÍN HIỆU HÔM NAY sẽ bị ẩn.", "3.trading_scenarios"))

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


def collect_source_urls(content: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    for key in ("1", "4"):
        for b in (content.get(key) or {}).get("bullets") or []:
            if isinstance(b, dict) and b.get("source_url"):
                urls.append(b["source_url"])
    for d in (content.get("2") or {}).get("key_developments") or []:
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

    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        path = f"1.bullets[{i}]"
        if isinstance(b, str) or not b.get("source_url"):
            issues.append(_issue("source", "1", "info", f"Bullet {i + 1}: không có nguồn trích dẫn.", path))
        else:
            _check_url(b["source_url"], "1", f"Bullet {i + 1}", f"{path}.source_url")

    for i, b in enumerate((content.get("4") or {}).get("bullets") or []):
        if isinstance(b, dict):
            _check_url(b.get("source_url"), "4", f"Tín chỉ/CBAM #{i + 1}", f"4.bullets[{i}].source_url")
    # key_developments hiển thị chung khối "Diễn biến chính" với Mục 4 → gắn note vào "4".
    for i, d in enumerate((content.get("2") or {}).get("key_developments") or []):
        if isinstance(d, dict):
            _check_url(d.get("source_url"), "4", f"Diễn biến chính #{i + 1}", f"2.key_developments[{i}].source_url")

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
    """Ngày (giờ VN) hợp lệ của EIA/Baker Hughes trong [target-7, target+6] — tính bằng ĐÚNG
    hàm report_generator dùng khi sinh Mục 8 (đã quy đổi múi giờ + DST: Baker Hughes 12:00 CT
    thứ Sáu rơi vào rạng sáng thứ Bảy giờ VN)."""
    prev_week = (datetime.strptime(target_date, "%Y-%m-%d").date() - timedelta(days=7)).isoformat()
    expected: Dict[str, Set[str]] = {"eia": set(), "baker_hughes": set()}
    for ev in _compute_recurring_calendar_events(prev_week) + _compute_recurring_calendar_events(target_date):
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


# ── Tổng hợp điểm ────────────────────────────────────────────────────


def score_issues(issues: List[Dict[str, str]]) -> int:
    return max(0, 100 - sum(SEVERITY_PENALTY.get(it["severity"], 0) for it in issues))


def summarize(
    issues_by_check: Dict[str, List[Dict[str, str]]], skipped: Iterable[str] = ()
) -> Dict[str, Any]:
    """-> {"scores": {check: điểm | None}, "overall_score": trung bình các check đã chạy, "issues": [...]}.
    `skipped`: check không chạy/không chạy được (điểm None, không tính vào tổng; issue của
    nó — vd note "không chạy được" — vẫn được liệt kê nhưng không trừ điểm)."""
    skipped = set(skipped)
    scores: Dict[str, Optional[int]] = {
        check: None if check in skipped else score_issues(issues_by_check.get(check, [])) for check in CHECKS
    }
    ran = [v for v in scores.values() if v is not None]
    severity_order = {"error": 0, "warning": 1, "info": 2}
    all_issues = [it for check in CHECKS for it in issues_by_check.get(check, [])]
    all_issues.sort(key=lambda it: severity_order.get(it["severity"], 3))  # sort ổn định — giữ thứ tự trong cùng mức
    return {
        "scores": scores,
        "overall_score": round(sum(ran) / len(ran)) if ran else 0,
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


async def _run_llm_checks(content: Dict[str, Any], report_date: str, overrides) -> tuple:
    """-> (issues_by_check, skipped). 2 check chạy song song; check nào lỗi thì bỏ điểm + ghi note."""
    prompts = report_qc_llm.build_prompts(content, report_date, overrides)
    results = await asyncio.gather(
        *(report_qc_llm.run_llm_check(check, *prompts[check]) for check in report_qc_llm.LLM_CHECKS),
        return_exceptions=True,
    )
    issues_by_check: Dict[str, List[Dict[str, str]]] = {}
    skipped: Set[str] = set()
    for check, res in zip(report_qc_llm.LLM_CHECKS, results):
        if isinstance(res, BaseException):
            logger.error("[REPORT-QC] Check LLM %s lỗi cho %s: %s", check, report_date, res)
            skipped.add(check)
            reason = str(res) if isinstance(res, report_qc_llm.LLMCheckError) else type(res).__name__
            issues_by_check[check] = [_issue(
                check, "3", "info",
                f"Lucy không chạy được check AI \"{report_qc_llm.LLM_CHECK_LABELS[check]}\" ({reason}) — điểm check này bỏ trống.",
                "3",
            )]
        else:
            issues_by_check[check] = res
    return issues_by_check, skipped


async def run_report_qc(
    session: AsyncSession, report_date: str, content: Dict[str, Any], use_llm: bool = True
) -> Dict[str, Any]:
    """Chạy 5 check rule-based (+ 2 check LLM nếu use_llm) cho báo cáo `report_date` -> summarize()."""
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
            suggestions[s.id] = {"trigger_rule": s.trigger_rule}
    valid_codes = {c.upper() for c in (await session.execute(select(Instrument.code))).scalars()}
    overrides = await get_overrides_map(session) if use_llm else None
    # Đóng transaction đọc NGAY — trả connection về pool thay vì giữ nó trong lúc chờ LLM
    # (vài chục giây). expire_on_commit=False nên object đang có trong session không bị expire.
    await session.commit()

    issues_by_check = {
        "price": check_prices(content, db_prices, latest_price_date),
        "scenario": check_scenarios(content),
        "source": check_sources(content, known_urls),
        "calendar": check_calendar(content, report_date),
        "biz": check_biz(content, suggestions, valid_codes, (content.get("2") or {}).get("prices") or []),
    }
    skipped: Set[str] = set(report_qc_llm.LLM_CHECKS)
    if use_llm:
        llm_issues, skipped = await _run_llm_checks(content, report_date, overrides)
        issues_by_check.update(llm_issues)
    result = summarize(issues_by_check, skipped)
    logger.info(
        "[REPORT-QC] %s: %d/100 (%s), %d issue.",
        report_date, result["overall_score"], result["scores"], len(result["issues"]),
    )
    return result
