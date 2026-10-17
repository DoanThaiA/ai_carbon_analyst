"""Tự sửa/kiểm tra các lỗi số học & trình bày mà LLM hay mắc trong văn bản báo cáo — hàm THUẦN, chạy bằng
Python sau khi sinh báo cáo (report_generator.generate_report_content) và dùng lại trong Jack QC
(services/report_qc.py::check_consistency).

Bắt từ bản chấm báo cáo 16/10/2026:
  - nhãn phần trăm sai: "78,30 (-5% so với đóng cửa 85,84)" — -5% là 81,55, 78,30 là -8,78%
    → tính lại và sửa nhãn (mức giá là số giao dịch đã dùng ở thẻ chiến lược, giữ nguyên);
  - mô tả vị trí trong biên độ sai: "về nửa dưới biên độ 82,35–88,82" khi 85,84 ở 53,9% (nửa trên),
    "87,5 (giữa biên)" của vùng 83,25–88,25 (thực ra sát biên trên) → tính lại từ số;
  - vùng giá độ rộng 0 ("88,82–88,82") → 1 mức;
  - dấu chấm thập phân kiểu Anh ("85.84") lẫn với kiểu vi-VN ("85,84");
  - ngày kiểu tiếng Anh ("5 October 2026") → "05/10/2026".
"""
import copy
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

# Giá trị số kiểu vi-VN (1.386,75 / 85,84 / 85) hoặc kiểu Anh (85.84).
_NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
_DASH = r"\s*[–—-]\s*"


def parse_num(raw: str) -> float:
    """'1.386,75' -> 1386.75; '85,84' -> 85.84; '85.84' -> 85.84."""
    s = raw.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        return float(s.replace(".", "").replace(",", "."))
    return float(s.replace(",", "."))


def vn_num(x: float, digits: int = 2, sign: bool = False) -> str:
    # Làm tròn nửa lên theo số thập phân như người đọc tính tay (85,585 → 85,59; float format cho 85,58).
    x = float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
    s = f"{x:+,.{digits}f}" if sign else f"{x:,.{digits}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _decimals(raw: str) -> int:
    m = re.search(r"[.,](\d+)$", raw)
    return len(m.group(1)) if m and not re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw) else 0


# ── 1. Nhãn "x% so với y" ─────────────────────────────────────────────────────
# "78,30 (-5% so với đóng cửa 85,84)" / "81,55 (−5% so với giá đóng cửa 85,84)"
_PCT_PAREN_RE = re.compile(
    rf"(?P<price>{_NUM})(?P<mid>\s*(?:EUR/tCO2\s*)?\(\s*(?:≈\s*)?)(?P<pct>[+\-−]?\s*\d+(?:[.,]\d+)?)\s*%"
    rf"(?P<tail>\s*so với\s+(?:giá\s+)?(?:đóng cửa|hiện tại|tham chiếu)?\s*(?:hiện tại\s*)?)(?P<ref>{_NUM})"
)
# "+5% ≈ 90,13" / "−5% ≈ 81,55" — tham chiếu là giá đóng cửa EUA (quy tắc prompt Mục 3).
_PCT_APPROX_RE = re.compile(rf"(?P<pct>[+\-−]\s*\d+(?:[.,]\d+)?)\s*%\s*(?P<mid>≈|~)\s*(?P<price>{_NUM})")


def _pct_tolerance(raw_pct: str) -> float:
    # "5%" chấp nhận làm tròn 4,5–5,5; "5,2%" chấp nhận 5,15–5,25 (+ đệm nhỏ vì giá làm tròn 2 số lẻ).
    return 0.5 * 10 ** (-_decimals(raw_pct.replace(" ", ""))) + 0.1


def _signed(raw_pct: str) -> float:
    s = raw_pct.replace(" ", "").replace("−", "-")
    return parse_num(s.lstrip("+-")) * (-1 if s.startswith("-") else 1)


def _fmt_pct(actual: float, raw_pct: str) -> str:
    digits = max(1, _decimals(raw_pct.replace(" ", "")))
    out = vn_num(actual, digits, sign=True)
    return out.replace("-", "−") if "−" in raw_pct else out


def fix_pct_labels(text: str, ref_close: Optional[float] = None) -> Tuple[str, List[str]]:
    """Tính lại mọi nhãn phần trăm gắn với 1 mức giá; sai quá sai số làm tròn → sửa nhãn. -> (text, mô tả lỗi).
    Dạng "±x% ≈ giá" không ghi tham chiếu → chỉ xét khi có `ref_close` (giá đóng cửa EUA — chỉ truyền cho
    văn bản kịch bản EUA, nơi prompt quy định dạng này)."""
    problems: List[str] = []

    def _check(price_raw: str, pct_raw: str, ref: float) -> Optional[str]:
        price = parse_num(price_raw)
        # Chỉ nhãn của MỨC GIÁ (không phải Δ tuyệt đối "−0,30 (−0,35% ...)" hay giá mã khác hẳn cỡ).
        if not ref or not (0.5 * ref <= price <= 2 * ref):
            return None
        actual = (price / ref - 1) * 100
        if abs(actual - _signed(pct_raw)) <= _pct_tolerance(pct_raw):
            return None
        fixed = _fmt_pct(actual, pct_raw)
        problems.append(
            f"{price_raw} bị ghi {pct_raw.strip()}% so với {vn_num(ref)} — đúng là {fixed}% "
            f"({pct_raw.strip()}% tương ứng {vn_num(ref * (1 + _signed(pct_raw) / 100))})"
        )
        return fixed

    def _paren(m: re.Match) -> str:
        fixed = _check(m["price"], m["pct"], parse_num(m["ref"]))
        if fixed is None:
            return m.group(0)
        return f"{m['price']}{m['mid']}{fixed}%{m['tail']}{m['ref']}"

    def _approx(m: re.Match) -> str:
        if ref_close is None:
            return m.group(0)
        fixed = _check(m["price"], m["pct"], ref_close)
        return m.group(0) if fixed is None else f"{fixed}% {m['mid']} {m['price']}"

    text = _PCT_PAREN_RE.sub(_paren, text)
    text = _PCT_APPROX_RE.sub(_approx, text)
    return text, problems


# ── 2. Vị trí trong biên độ ───────────────────────────────────────────────────
def range_position(price: float, low: float, high: float) -> Optional[Dict[str, Any]]:
    """Vị trí của `price` trong [low, high] → {"pct", "mid", "half", "label"} (label dùng chung cho prompt + sửa câu)."""
    if high <= low:
        return None
    pct = (price - low) / (high - low) * 100
    mid = (low + high) / 2
    if pct > 100:
        label = "trên biên trên"
    elif pct < 0:
        label = "dưới biên dưới"
    elif pct >= 80:
        label = "sát biên trên"
    elif pct <= 20:
        label = "sát biên dưới"
    elif 45 <= pct <= 55:
        label = "giữa biên"
    else:
        label = "nửa trên" if pct > 50 else "nửa dưới"
    return {"pct": pct, "mid": mid, "half": "nửa trên" if pct >= 50 else "nửa dưới", "label": label}


def describe_range_position(price: float, low: float, high: float, what: str) -> str:
    pos = range_position(price, low, high)
    if not pos:
        return ""
    return (
        f"Vị trí giá đóng cửa {vn_num(price)} trong {what} {vn_num(low)}–{vn_num(high)}: {vn_num(pos['pct'], 1)}% tính từ đáy "
        f"(điểm giữa {vn_num(pos['mid'])}) → {pos['half'].upper()} biên độ. Nhắc vị trí PHẢI dùng đúng cụm \"{pos['half']}\"."
    )


# "nửa dưới biên độ 82,35–88,82" / "nửa trên của vùng 82,35 – 88,82"
_HALF_CLAIM_RE = re.compile(
    rf"(?P<half>nửa\s+(?:trên|dưới))(?P<mid>\s+(?:của\s+)?(?:biên(?:\s+độ)?|vùng|kênh)(?:\s+[^\d\n]{{0,25}}?)?\s*)"
    rf"(?P<lo>{_NUM}){_DASH}(?P<hi>{_NUM})"
)
# "87,5 (giữa biên)" — biên là vùng A–B gần nhất trong cùng đoạn chứa số đó.
_MID_LABEL_RE = re.compile(rf"(?P<price>{_NUM})(?P<pre>\s*\(\s*)(?P<label>giữa\s+(?:biên|vùng)(?:\s+độ)?)(?=\s*[),;])")
_RANGE_RE = re.compile(rf"(?P<lo>{_NUM}){_DASH}(?P<hi>{_NUM})")


def fix_range_claims(
    text: str, price: Optional[float], default_range: Optional[Tuple[float, float]] = None,
) -> Tuple[str, List[str]]:
    """`default_range` (đáy, đỉnh 30 phiên) dùng cho "X (giữa biên)" khi câu không tự ghi biên A–B
    (vd "Mục tiêu 87,0 (giữa biên)" — giữa biên 82,35–88,82 là 85,59)."""
    problems: List[str] = []

    def _half(m: re.Match) -> str:
        lo, hi = parse_num(m["lo"]), parse_num(m["hi"])
        pos = range_position(price, min(lo, hi), max(lo, hi)) if price else None
        claim = re.sub(r"\s+", " ", m["half"])
        if not pos or not (0 <= pos["pct"] <= 100) or claim == pos["half"] or 47 <= pos["pct"] <= 53:
            return m.group(0)
        problems.append(
            f"\"{claim} {m['lo']}–{m['hi']}\" sai: giá {vn_num(price)} ở {vn_num(pos['pct'], 1)}% biên độ "
            f"(điểm giữa {vn_num(pos['mid'])}) → {pos['half']}"
        )
        return f"{pos['half']}{m['mid']}{m['lo']}–{m['hi']}"

    def _mid(m: re.Match) -> str:
        n = parse_num(m["price"])
        ranges = [(parse_num(r["lo"]), parse_num(r["hi"])) for r in _RANGE_RE.finditer(text)]
        ranges = [(min(a, b), max(a, b)) for a, b in ranges if min(a, b) < n < max(a, b)]
        if not ranges and default_range and default_range[0] < n < default_range[1]:
            ranges = [default_range]
        if not ranges:
            return m.group(0)
        lo, hi = min(ranges, key=lambda r: r[1] - r[0])
        pos = range_position(n, lo, hi)
        if not pos or abs(pos["pct"] - 50) <= 15:
            return m.group(0)
        problems.append(
            f"{m['price']} gọi là \"giữa biên\" của {vn_num(lo)}–{vn_num(hi)} nhưng điểm giữa là {vn_num(pos['mid'])} "
            f"→ {pos['label']}"
        )
        return f"{m['price']}{m['pre']}{pos['label']}"

    text = _HALF_CLAIM_RE.sub(_half, text)
    text = _MID_LABEL_RE.sub(_mid, text)
    return text, problems


# ── 2a. ATR ───────────────────────────────────────────────────────────────────
ATR_SESSIONS = 14


def atr_true_range(chart: List[Dict[str, Any]], n: int = ATR_SESSIONS) -> Optional[float]:
    """ATR = TB True Range n phiên cuối của `chart`; TR = max(cao − thấp, |cao − đóng cửa trước|, |thấp − đóng cửa trước|).
    Dùng chung bộ sinh + Jack + văn bản báo cáo (trước dùng TB cao − thấp → 1,67 thay vì 1,69 theo True Range)."""
    rows = [c for c in chart if isinstance(c, dict) and all(isinstance(c.get(k), (int, float)) for k in ("high", "low"))]
    if not rows:
        return None
    trs = []
    for i in range(max(0, len(rows) - n), len(rows)):
        h, l = float(rows[i]["high"]), float(rows[i]["low"])
        pc = rows[i - 1].get("close") if i > 0 else None
        trs.append(max(h - l, abs(h - pc), abs(l - pc)) if isinstance(pc, (int, float)) else h - l)
    return sum(trs) / len(trs)


# ── 2b. Chuỗi phiên tăng/giảm liên tiếp ───────────────────────────────────────
def eua_streak(chart: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Số phiên liên tiếp cùng chiều (giá đóng cửa so phiên trước) tính tới phiên mới nhất.
    -> {"direction": "tăng"|"giảm", "count", "closes": [(ngày, giá) từ phiên GỐC trước chuỗi → mới nhất]}."""
    closes = [(c.get("date"), float(c["close"])) for c in chart if isinstance(c, dict) and c.get("close") is not None]
    if len(closes) < 2 or closes[-1][1] == closes[-2][1]:
        return None
    up = closes[-1][1] > closes[-2][1]
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        if (closes[i][1] > closes[i - 1][1]) != up or closes[i][1] == closes[i - 1][1]:
            break
        n += 1
    return {"direction": "tăng" if up else "giảm", "count": n, "closes": closes[-n - 1:]}


_VI_NUM = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10}
_VI_ORD = {2: "hai", 3: "ba", 4: "tư", 5: "năm", 6: "sáu", 7: "bảy", 8: "tám", 9: "chín", 10: "mười"}
_VI_CARD = {2: "hai", 3: "ba", 4: "bốn", 5: "năm", 6: "sáu", 7: "bảy", 8: "tám", 9: "chín", 10: "mười"}
_NUM_WORD = r"\d+|" + "|".join(_VI_NUM)
_STREAK_RE = re.compile(
    rf"(?P<dir>tăng|giảm)(?P<sp>\s+)(?:(?P<ord>phiên\s+thứ\s+(?P<o>{_NUM_WORD}))|(?P<c>{_NUM_WORD})\s+phiên)\s+liên\s+tiếp",
    re.IGNORECASE,
)
_OTHER_INSTRUMENT_RE = re.compile(r"TTF|Brent|WTI|API2|API4|NEWC|Gasoil|DEBY|điện|khí|than|dầu", re.IGNORECASE)


def _word_num(w: str) -> Optional[int]:
    return int(w) if w.isdigit() else _VI_NUM.get(w.lower())


def fix_streak_claims(text: str, streak: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """"EUA giảm phiên thứ hai liên tiếp" khi thực tế giảm 4 phiên → sửa số phiên (chỉ câu nói về EUA)."""
    problems: List[str] = []
    if not streak:
        return text, problems

    def _sub(m: re.Match) -> str:
        start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        subject = text[start:m.start()]
        if "EUA" not in subject or _OTHER_INSTRUMENT_RE.search(subject.split("EUA")[-1]):
            return m.group(0)
        claimed = _word_num(m["o"] or m["c"])
        if claimed is None or m["dir"].lower() != streak["direction"] or claimed == streak["count"]:
            return m.group(0)
        n = streak["count"]
        path = " → ".join(vn_num(c) for _, c in streak["closes"])
        problems.append(f"EUA {m['dir']} {claimed} phiên liên tiếp sai — thực tế {streak['direction']} {n} phiên liên tiếp ({path})")
        if n < 2 or n > 10:
            return m.group(0)
        if m["ord"]:
            return f"{m['dir']}{m['sp']}phiên thứ {_VI_ORD[n]} liên tiếp"
        return f"{m['dir']}{m['sp']}{_VI_CARD[n]} phiên liên tiếp"

    return _STREAK_RE.sub(_sub, text), problems


# ── 2c. Mục tiêu suy từ phép nhân "X (Y × 0,95)" ──────────────────────────────
_DERIVED_RE = re.compile(rf"(?P<x>{_NUM})\s*\(\s*=?\s*(?P<y>{_NUM})\s*[×x\*]\s*(?P<f>\d+(?:[.,]\d+)?)\s*\)")


_PCT_FROM_CLOSE_RE = re.compile(
    rf"(?P<x>{_NUM})\s*\(\s*(?P<pct>[+\-−]\s*\d+(?:[.,]\d+)?)\s*%\s*so với\s+(?:giá\s+)?đóng cửa\s*(?P<ref>{_NUM})"
)


def _entry_level(sc: Dict[str, Any]) -> Optional[float]:
    """Giá vào lệnh thực của kịch bản tăng/giảm: mức kích hoạt (lệnh phá vỡ) hoặc giữa vùng Entry."""
    from services.strategy_levels import strategy_levels
    lv = strategy_levels(sc)
    if not lv:
        return None
    zone = lv["entry_buy"] if sc.get("direction") == "tăng" else lv["entry_sell"]
    return sum(zone) / len(zone) if zone else None


def derived_target_issues(sec3: Dict[str, Any], close: Optional[float], levels: List[float]) -> List[Dict[str, str]]:
    """Mức giá ghi kèm phép nhân "(Y × f)": (1) X phải ≈ Y × f; (2) cơ sở Y phải là giá đóng cửa hoặc 1 mốc có trong
    dữ liệu (`levels`) / chính kịch bản đó; (3) trong CÙNG 1 kịch bản, "price_zone" và "trading_strategy" không được
    ra 2 mục tiêu khác nhau (bản 17/10: 81,14 = 85,41 × 0,95 ở Vùng giá nhưng 77,95 = 82,05 × 0,95 ở Chiến lược)."""
    out: List[Dict[str, str]] = []
    known = [v for v in [close, *levels] if v]
    for i, sc in enumerate(sec3.get("trading_scenarios") or []):
        if not isinstance(sc, dict):
            continue
        path = f"3.trading_scenarios[{i}]"
        found: Dict[str, List[float]] = {}
        own_text = " ".join(str(sc.get(k) or "") for k in ("condition", "price_zone", "key_risk", "trading_strategy"))
        own_nums = [parse_num(n) for n in re.findall(_NUM, re.sub(_DERIVED_RE, "", own_text))]
        for field in ("price_zone", "trading_strategy"):
            for m in _DERIVED_RE.finditer(str(sc.get(field) or "")):
                x, y, f = parse_num(m["x"]), parse_num(m["y"]), parse_num(m["f"])
                found.setdefault(field, []).append(x)
                if abs(x - y * f) > 0.02:
                    out.append({"path": f"{path}.trading_strategy", "message":
                                f"{sc.get('horizon')}: {m['x']} ≠ {m['y']} × {m['f']} (= {vn_num(y * f)})"})
                if not any(abs(y - k) <= 0.011 for k in known + own_nums):
                    out.append({"path": f"{path}.trading_strategy", "message":
                                f"{sc.get('horizon')}: mục tiêu {m['x']} tính từ cơ sở {m['y']} — không phải giá đóng cửa"
                                f"{' ' + vn_num(close) if close else ''} hay mốc nào trong dữ liệu/kịch bản"})
        for m in _PCT_FROM_CLOSE_RE.finditer(str(sc.get("trading_strategy") or "")):
            entry = _entry_level(sc)
            ref = parse_num(m["ref"])
            if entry and close and abs(ref - close) < 0.011 and abs(entry - close) > 0.01 * close:
                x = parse_num(m["x"])
                out.append({"path": f"{path}.trading_strategy", "message":
                            f"{sc.get('horizon')}: mục tiêu {m['x']} ghi {m['pct']}% so với đóng cửa {m['ref']} nhưng Entry là "
                            f"{vn_num(entry)} → kỳ vọng thực {vn_num((x / entry - 1) * 100, sign=True)}% từ Entry"})
        zone, strat = found.get("price_zone") or [], found.get("trading_strategy") or []
        if zone and strat and not any(abs(a - b) <= 0.011 for a in zone for b in strat):
            out.append({"path": f"{path}.trading_strategy", "message":
                        f"{sc.get('horizon')}: 2 mục tiêu khác nhau — Vùng giá {', '.join(vn_num(v) for v in zone)} "
                        f"vs Chiến lược {', '.join(vn_num(v) for v in strat)}"})
    return out


# ── 3. Trình bày ──────────────────────────────────────────────────────────────
_DEGENERATE_RANGE_RE = re.compile(rf"(?<![\d.,/])(?P<a>{_NUM}){_DASH}(?P<b>{_NUM})(?![\d.,]*\d)")
# Dấu chấm thập phân kiểu Anh: "85.84", "81.4400" (đúng 3 số sau dấu chấm là phân cách nghìn kiểu vi-VN → bỏ qua).
# Bỏ qua ngày "16.10.2026", phiên bản "1.2.3", số trong URL.
_EN_DECIMAL_RE = re.compile(r"(?<![\w.,/:])(\d{1,4})\.(\d{1,2}|\d{4,})(?![\w.,]*\d)(?!\.\d)")
_MONTHS = {
    m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
         "november", "december"], start=1)
}
_MONTH_ALT = "|".join(m.capitalize() for m in _MONTHS) + "|" + "|".join(_MONTHS)
_EN_DATE_DMY_RE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\s*,?\s+(\d{{4}})\b")
_EN_DATE_MDY_RE = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}})\s*,\s*(\d{{4}})\b")


def normalize_presentation(text: str) -> str:
    def _range(m: re.Match) -> str:
        return m["a"] if parse_num(m["a"]) == parse_num(m["b"]) else m.group(0)

    text = _DEGENERATE_RANGE_RE.sub(_range, text)
    text = re.sub(r"\b[Vv]iệt nam\b|\bviệt Nam\b", "Việt Nam", text)
    text = _EN_DECIMAL_RE.sub(r"\1,\2", text)
    text = _EN_DATE_DMY_RE.sub(lambda m: f"{int(m[1]):02d}/{_MONTHS[m[2].lower()]:02d}/{m[3]}", text)
    text = _EN_DATE_MDY_RE.sub(lambda m: f"{int(m[2]):02d}/{_MONTHS[m[1].lower()]:02d}/{m[3]}", text)
    return text


# ── Áp cho cả báo cáo ─────────────────────────────────────────────────────────
# Khoá KHÔNG phải văn xuôi (URL, enum, số liệu máy, khối Python tự dựng) — không đụng tới.
SKIP_KEYS = {
    "url", "source_url", "source_name", "source", "origin", "date", "time", "id", "code", "name", "price", "dday",
    "dweek", "chart_data", "cbam_estimate", "forecast_review", "trigger_rule", "price_timestamp", "heading",
    "horizon", "direction", "probability", "impact", "tag", "region", "country", "released_at", "9",
}


def walk_text(node: Any, fn: Callable[[str, str], str], path: str = "") -> Any:
    """Áp `fn(text, path)` cho mọi chuỗi văn xuôi trong `node` (sửa tại chỗ, trả node)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SKIP_KEYS:
                continue
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, str):
                node[k] = fn(v, p)
            else:
                walk_text(v, fn, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            p = f"{path}[{i}]"
            if isinstance(v, str):
                node[i] = fn(v, p)
            else:
                walk_text(v, fn, p)
    return node


def eua_reference(content: Dict[str, Any]) -> Tuple[Optional[float], Optional[Tuple[float, float]]]:
    """(giá đóng cửa EUA, (đáy, đỉnh) 30 phiên) của báo cáo."""
    sec2 = content.get("2") or {}
    close = next(
        (float(p["close"]) for p in sec2.get("prices") or []
         if isinstance(p, dict) and str(p.get("code", "")).upper() == "EUA" and isinstance(p.get("close"), (int, float))),
        None,
    )
    chart = [c for c in sec2.get("chart_data") or [] if isinstance(c, dict) and c.get("high") is not None]
    rng = (min(float(c["low"]) for c in chart), max(float(c["high"]) for c in chart)) if chart else None
    return close, rng


def _is_eua_scenario(path: str) -> bool:
    return path.startswith(("3.trading_scenarios", "3.hedge_action"))


def _chart(content: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in (content.get("2") or {}).get("chart_data") or [] if isinstance(c, dict)]


def apply_consistency_fixes(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Sửa tại chỗ toàn bộ văn bản báo cáo. -> danh sách {"path", "message"} các lỗi số đã sửa (để log)."""
    close, rng = eua_reference(content)
    streak = eua_streak(_chart(content))
    fixed: List[Dict[str, str]] = []

    def _fn(text: str, path: str) -> str:
        text = normalize_presentation(text)
        text, p1 = fix_pct_labels(text, close if _is_eua_scenario(path) else None)
        text, p2 = fix_range_claims(text, close, rng)
        text, p3 = fix_streak_claims(text, streak)
        fixed.extend({"path": path, "message": m} for m in p1 + p2 + p3)
        return text

    walk_text(content, _fn)
    return fixed


def consistency_issues(content: Dict[str, Any]) -> List[Dict[str, str]]:
    """Như apply_consistency_fixes nhưng KHÔNG sửa (cho QC báo cáo đã lưu/đã sửa tay)."""
    close, rng = eua_reference(content)
    streak = eua_streak(_chart(content))
    found: List[Dict[str, str]] = []

    def _fn(text: str, path: str) -> str:
        _, p1 = fix_pct_labels(text, close if _is_eua_scenario(path) else None)
        _, p2 = fix_range_claims(text, close, rng)
        _, p3 = fix_streak_claims(text, streak)
        found.extend({"path": path, "message": m} for m in p1 + p2 + p3)
        if normalize_presentation(text) != text:
            found.append({"path": path, "message": "định dạng số/ngày/vùng giá chưa thống nhất kiểu vi-VN", "minor": "1"})
        return text

    walk_text(copy.deepcopy(content), _fn)
    found.extend(derived_target_issues(content.get("3") or {}, close, list(rng or ())))
    return found
