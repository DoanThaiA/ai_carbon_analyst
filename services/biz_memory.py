"""
services/biz_memory.py
======================
Bộ nhớ gợi ý kinh doanh ngắn hạn của Jenny (mục "Gợi ý kinh doanh & giải pháp cho
SIM") — Jenny "nhớ" mình đã đề xuất gì và NHẮC LẠI khi tình huống kích hoạt xảy ra.

AI không tự nhớ giữa các lần sinh báo cáo — mỗi gợi ý ngắn hạn được lưu vào bảng
`biz_suggestions` (db/models.py::BizSuggestion); mỗi lần sinh báo cáo ngày D:
  1. load_active(): gợi ý còn trong trí nhớ (đề xuất trong BIZ_SUGGESTION_MEMORY_DAYS
     ngày gần nhất, trước D) và chưa kích hoạt — hoặc đã kích hoạt ở CHÍNH ngày D
     (admin bấm sinh lại báo cáo D → kiểm tra lại từ đầu). Quá hạn thì thôi nhớ.
  2. Kiểm tra kích hoạt: gợi ý có trigger_rule (ngưỡng giá) → check_price_rule() so
     thẳng với giá thật, không qua LLM; còn lại → LLM đối chiếu tin tức trong ngày
     (services/report_generator.py::_check_biz_triggers_llm), BẮT BUỘC kèm bài báo.
  3. persist() ghi kết quả + gợi ý mới — ở BƯỚC CUỐI của generate_report_content,
     cùng transaction với việc lưu report.content (caller commit). Không ghi gì
     trước đó để không giữ khoá DB suốt mấy phút chờ LLM, và lỗi giữa chừng không
     để lại bộ nhớ lệch với báo cáo. Gợi ý do lần sinh TRƯỚC của chính ngày D tạo
     ra bị xoá trước khi ghi gợi ý mới (tránh nhân đôi khi sinh lại).
Admin gỡ 1 gợi ý (dismiss() + remove_from_biz_content(), gọi từ POST
/api/admin/reports/{date}/biz-suggestions/{id}/dismiss): status='dismissed' → thôi
theo dõi/nhắc lại, và load_recent_dismissed() báo cho LLM không đề xuất lại ý đó.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BizSuggestion

logger = logging.getLogger(__name__)

# Jenny nhớ 1 gợi ý trong 10 ngày (theo lịch) kể từ báo cáo đề xuất — quá hạn mà
# tình huống vẫn chưa xảy ra thì thôi theo dõi, không nhắc nữa.
BIZ_SUGGESTION_MEMORY_DAYS = 10

_VALID_OPS = {">", ">=", "<", "<="}
# "basis" của trigger_rule: mặc định so với giá đóng cửa phiên; "qtd_avg" = trung bình lũy kế quý
# (giá CBAM 2026 chốt theo trung bình quý — trigger liên quan chi phí CBAM phải so với số này).
BASIS_QTD_AVG = "qtd_avg"
_QTD_AVG_RE = re.compile(r"trung bình[^.;]{0,30}quý|tb[^.;]{0,15}quý|bình quân[^.;]{0,30}quý", re.IGNORECASE)


def expires_on(first_report_date: str) -> str:
    """Ngày cuối cùng Jenny còn theo dõi gợi ý (ISO) — sau ngày này thôi nhắc."""
    return (date.fromisoformat(first_report_date) + timedelta(days=BIZ_SUGGESTION_MEMORY_DAYS)).isoformat()


def _fmt_vn_date(iso: str) -> str:
    """'2026-09-18' -> '18/09/2026'."""
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return iso or ""


async def load_active(session: AsyncSession, report_date: str) -> List[BizSuggestion]:
    """Gợi ý còn trong trí nhớ tại ngày `report_date` (so chuỗi ISO = so ngày): đề
    xuất trong BIZ_SUGGESTION_MEMORY_DAYS ngày gần nhất, TRƯỚC ngày này, và chưa kích
    hoạt — hoặc kích hoạt ở chính ngày này (sinh lại báo cáo → kiểm tra lại)."""
    since = (date.fromisoformat(report_date) - timedelta(days=BIZ_SUGGESTION_MEMORY_DAYS)).isoformat()
    stmt = (
        select(BizSuggestion)
        .where(
            BizSuggestion.kind == "short",  # gợi ý dài hạn không có tình huống kích hoạt
            BizSuggestion.status != "dismissed",  # admin đã gỡ → thôi theo dõi hẳn
            or_(
                BizSuggestion.status == "pending",
                # triggered/contradicted ở chính ngày này → sinh lại báo cáo thì kiểm tra lại
                BizSuggestion.triggered_report_date == report_date,
            ),
            BizSuggestion.first_report_date >= since,
            BizSuggestion.first_report_date < report_date,
        )
        .order_by(BizSuggestion.first_report_date, BizSuggestion.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def load_recent_dismissed(session: AsyncSession, report_date: str) -> List[BizSuggestion]:
    """Gợi ý admin đã gỡ trong thời gian nhớ (tính cả gợi ý của chính ngày này khi
    sinh lại) — đưa vào prompt để LLM KHÔNG đề xuất lại ý tương tự."""
    since = (date.fromisoformat(report_date) - timedelta(days=BIZ_SUGGESTION_MEMORY_DAYS)).isoformat()
    stmt = (
        select(BizSuggestion)
        .where(
            BizSuggestion.status == "dismissed",
            BizSuggestion.first_report_date >= since,
            BizSuggestion.first_report_date <= report_date,
        )
        .order_by(BizSuggestion.first_report_date, BizSuggestion.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def dismiss(
    session: AsyncSession, suggestion_id: int, dismissed_by: Optional[str], reason: Optional[str]
) -> Optional[BizSuggestion]:
    """Admin gỡ 1 gợi ý (KHÔNG commit — caller commit cùng việc sửa report.content).
    Trả về None nếu không tồn tại."""
    obj = await session.get(BizSuggestion, suggestion_id)
    if obj is None:
        return None
    obj.status = "dismissed"
    obj.dismissed_at = datetime.now(timezone.utc)
    obj.dismissed_by = dismissed_by
    obj.dismiss_reason = (reason or "").strip() or None
    return obj


def remove_from_biz_content(biz: Optional[Dict[str, Any]], suggestion_id: int) -> tuple[Optional[Dict[str, Any]], bool]:
    """Bỏ gợi ý `suggestion_id` khỏi mọi danh sách trong content["biz"] (gợi ý mới,
    nhắc lại, đang theo dõi). Trả về (biz mới — object MỚI để SQLAlchemy nhận ra JSONB
    đã đổi, đã_tìm_thấy)."""
    if not biz:
        return biz, False
    found = False
    new_biz = dict(biz)
    for key in ("short_term", "long_term", "reminders", "tracking"):
        items = biz.get(key) or []
        kept = [it for it in items if not (isinstance(it, dict) and it.get("id") == suggestion_id)]
        if len(kept) != len(items):
            found = True
        new_biz[key] = kept
    return new_biz, found


def normalize_trigger_rule(raw: Any, valid_codes: set) -> Optional[Dict[str, Any]]:
    """Chuẩn hoá trigger_rule LLM trả về — sai định dạng/mã không có thật → None
    (khi đó gợi ý được kiểm tra bằng LLM theo tin tức thay vì theo ngưỡng giá)."""
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "").strip().upper()
    op = str(raw.get("op") or "").strip()
    try:
        value = float(raw.get("value"))
    except (TypeError, ValueError):
        return None
    if code not in valid_codes or op not in _VALID_OPS:
        return None
    rule = {"code": code, "op": op, "value": value}
    if str(raw.get("basis") or "").strip().lower() == BASIS_QTD_AVG:
        rule["basis"] = BASIS_QTD_AVG
    return rule


# Từ khoá điều kiện ngưỡng giá trong câu "Tình huống" (tiếng Việt/ký hiệu) → toán tử. Giữa từ khoá và
# con số cho phép vài từ đệm ("vượt MỐC 1.500", "vượt KHÁNG CỰ 88,82", "tăng TRỞ LẠI VÙNG ...").
_THRESHOLD_RE = re.compile(
    r"(>=|<=|>|<|vượt lên trên|vượt qua|vượt|bứt phá|phá vỡ|phá|tăng lên|tăng|lên trên|lên|trên|cao hơn"
    r"|giảm xuống|xuống dưới|rơi xuống|xuống|giảm|thủng|dưới|thấp hơn)"
    r"(?:\s+(?:lại|trở lại|về|mức|ngưỡng|mốc|vùng|kháng cự|hỗ trợ|đỉnh|đáy|khỏi|quanh))*"
    r"\s*(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?![\d.,]*\s*%)",
    re.IGNORECASE,
)
_UP_WORDS = (">", "vượt", "trên", "cao hơn", "bứt phá", "phá", "lên", "tăng")
_DOWN_WORDS = ("<", "dưới", "xuống", "giảm", "thủng", "thấp hơn")
_PRICE_UNIT_RE = re.compile(r"(eur|usd|€|\$|/t|/mwh|/mmbtu|/bbl|/thùng|/tấn|đóng cửa|giá)", re.IGNORECASE)
# Tên gọi thường gặp của các mã giá (khớp thêm ngoài code/name trong bảng prices).
_CODE_ALIASES = {
    "EUA": ("eua", "hạn ngạch"),
    "TTF": ("ttf", "khí eu", "khí châu âu"),
    "BRENT": ("brent",),
    "WTI": ("wti",),
    "GASOIL": ("gasoil", "diesel"),
    "API2": ("api2", "than"),
    "API4": ("api4",),
    "NEWC": ("newc", "newcastle"),
    "DEBY1": ("deby1", "điện đức", "german power"),
    "CBAM": ("cbam",),
    "NG": ("henry hub", "khí mỹ"),
}
_ANY_ALIAS_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(a) for al in _CODE_ALIASES.values() for a in al) + r")(?!\w)",
    re.IGNORECASE,
)


def _parse_vn_number(raw: str) -> Optional[float]:
    """'88,53' / '88.53' / '1.234,5' / '1.500' (nghìn) -> float."""
    raw = raw.strip()
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        # '1.500' / '1.386' là dấu chấm phân cách hàng nghìn kiểu Việt, KHÔNG phải 1,5
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def looks_like_price_condition(trigger: str) -> bool:
    """Câu tình huống là điều kiện NGƯỠNG GIÁ (vd 'EUA đóng cửa trên 88,53', 'NG > 3,60 USD/MMBtu',
    'Gasoil vượt mốc 1.500') — các câu này KHÔNG được để LLM tự kết luận 'đã xảy ra' từ tin tức."""
    text = trigger or ""
    return bool(_THRESHOLD_RE.search(text)) and bool(_PRICE_UNIT_RE.search(text) or _ANY_ALIAS_RE.search(text))


def _op_from_word(word: str, phrase: str = "") -> str:
    word = word.lower()
    if "phá" in word and re.search(r"hỗ trợ|đáy", phrase.lower()):
        return "<"  # "phá vỡ hỗ trợ/đáy X" = giảm xuống dưới X
    if word in _VALID_OPS:
        return word
    if any(w in word for w in _DOWN_WORDS):
        return "<"
    return ">" if any(w in word for w in _UP_WORDS) else "<"


def infer_price_rule(trigger: str, prices: List[Dict]) -> Optional[Dict[str, Any]]:
    """Dựng trigger_rule từ câu tình huống khi LLM quên trả trigger_rule — chỉ khi nhận ra
    đúng 1 mã giá có trong `prices` và 1 ngưỡng ngay sau tên mã. Không chắc → None."""
    text = (trigger or "").lower()
    found = []
    for p in prices:
        code = str(p.get("code") or "").upper()
        if not code or p.get("close") is None:
            continue
        aliases = {code.lower(), str(p.get("name") or "").lower(), *_CODE_ALIASES.get(code, ())}
        positions = [m.end() for a in aliases if a for m in re.finditer(r"(?<!\w)" + re.escape(a) + r"(?!\w)", text)]
        if positions:
            found.append((min(positions), code))
    if len({c for _, c in found}) != 1:
        return None
    pos, code = min(found)
    m = _THRESHOLD_RE.search(text, pos)
    if not m or m.start() - pos > 60:
        return None
    value = _parse_vn_number(m.group(2))
    if value is None:
        return None
    rule = {"code": code, "op": _op_from_word(m.group(1), m.group(0)), "value": value}
    if _QTD_AVG_RE.search(text):
        rule["basis"] = BASIS_QTD_AVG
    return rule


def _plausible(rule: Dict[str, Any], prices: List[Dict]) -> bool:
    """Ngưỡng cùng bậc độ lớn với giá hiện tại (lệch < 3 lần) — chặn kiểu 1.500 bị đọc thành 1,5."""
    price = next((p for p in prices if str(p.get("code", "")).upper() == rule.get("code")), None)
    close = price.get("close") if price else None
    if not isinstance(close, (int, float)) or close <= 0:
        return True  # không có giá thì check_price_rule tự trả None
    return 1 / 3 < rule["value"] / close < 3 if rule["value"] > 0 else False


def resolve_price_rule(stored: Optional[Dict[str, Any]], trigger: str, prices: List[Dict]) -> Optional[Dict[str, Any]]:
    """trigger_rule dùng để so giá: câu chữ "Tình huống" là cái người đọc thấy nên THẮNG khi rule lưu
    (do LLM trả) lệch chiều/lệch số với câu chữ; rule sai bậc độ lớn so với giá hiện tại → bỏ."""
    inferred = infer_price_rule(trigger, prices)
    rule = stored
    if inferred and (
        not stored
        or stored.get("code") != inferred["code"]
        or (stored.get("op") in (">", ">=")) != (inferred["op"] in (">", ">="))
        or abs(float(stored.get("value") or 0) - inferred["value"]) > 0.01 * max(abs(inferred["value"]), 1e-9)
    ):
        rule = {**inferred, **({"basis": stored["basis"]} if stored and stored.get("basis") and "basis" not in inferred else {})}
    if rule and not _plausible(rule, prices):
        logger.warning("[BIZ] Bỏ trigger_rule sai bậc độ lớn so với giá hiện tại: %s (tình huống: %s)", rule, trigger)
        return None
    return rule


def check_price_rule(rule: Dict[str, Any], prices: List[Dict]) -> Optional[str]:
    """Trả về câu bằng chứng nếu ngưỡng giá đã chạm, None nếu chưa/không có giá."""
    price = next((p for p in prices if str(p.get("code", "")).upper() == rule["code"]), None)
    qtd = rule.get("basis") == BASIS_QTD_AVG
    close = (price.get("qtd_avg") if qtd else price.get("close")) if price else None
    if close is None:
        return None
    value, op = rule["value"], rule["op"]
    hit = {
        ">": close > value, ">=": close >= value,
        "<": close < value, "<=": close <= value,
    }[op]
    if not hit:
        return None
    what = ("VWAP đấu giá lũy kế quý" if price.get("qtd_basis") == "auction_vwap" else "trung bình lũy kế quý (xấp xỉ)") if qtd else "đóng cửa"
    return f"{price.get('name') or rule['code']} {what} {close:,.2f} (ngưỡng {op} {value:,.2f})."


def describe_for_prompt(suggestions: List[BizSuggestion]) -> str:
    """Danh sách gợi ý đang theo dõi, dạng [S<id>] để LLM trả lời theo id."""
    if not suggestions:
        return "Không có."
    lines = []
    for s in suggestions:
        if getattr(s, "kind", "short") == "long":
            lines.append(
                f"[S{s.id}] (dài hạn, đề xuất ngày {_fmt_vn_date(s.first_report_date)}) Cơ hội: {s.trigger} | Giải pháp: {s.action}"
            )
        else:
            lines.append(
                f"[S{s.id}] (đề xuất ngày {_fmt_vn_date(s.first_report_date)}, theo dõi đến "
                f"{_fmt_vn_date(expires_on(s.first_report_date))}) Tình huống: {s.trigger} | Hành động: {s.action}"
            )
    return "\n".join(lines)


async def persist(
    session: AsyncSession,
    report_date: str,
    triggered: Dict[int, Dict[str, Optional[str]]],
    active: List[BizSuggestion],
    new_items: List[Dict[str, Any]],
    new_long_items: Optional[List[Dict[str, Any]]] = None,
    contradicted: Optional[Dict[int, Dict[str, Optional[str]]]] = None,
) -> tuple[List[BizSuggestion], List[BizSuggestion]]:
    """Ghi kết quả vào session + flush (KHÔNG commit — caller commit cùng
    report.content). `triggered`: id -> {evidence, source_name, source_url}.
    `contradicted`: id -> {evidence, source_name, source_url} — thực tế ngược với đề xuất.
    `new_long_items`: gợi ý dài hạn {opportunity, solution, expectation}.
    Trả về (object ngắn hạn mới, object dài hạn mới) — cùng thứ tự đầu vào."""
    # Sinh lại báo cáo ngày này → bỏ gợi ý do lần sinh trước tạo ra (GIỮ lại gợi ý
    # admin đã gỡ — load_recent_dismissed() cần chúng để LLM không đề xuất lại).
    await session.execute(
        delete(BizSuggestion).where(
            BizSuggestion.first_report_date == report_date,
            BizSuggestion.status != "dismissed",
        )
    )

    # Ghi trạng thái cho MỌI gợi ý đang nhớ — kể cả đưa về 'pending' những gợi ý
    # lần sinh trước của ngày này đã đánh dấu kích hoạt nhưng lần này không còn.
    contradicted = contradicted or {}
    for s in active:
        info = triggered.get(s.id) or contradicted.get(s.id)
        s.status = "triggered" if s.id in triggered else "contradicted" if info else "pending"
        s.triggered_report_date = report_date if info else None
        s.trigger_evidence = info.get("evidence") if info else None
        s.evidence_source_name = info.get("source_name") if info else None
        s.evidence_source_url = info.get("source_url") if info else None

    created = []
    for it in new_items:
        obj = BizSuggestion(
            first_report_date=report_date,
            trigger=it["trigger"],
            trigger_rule=it.get("trigger_rule"),
            action=it["action"],
            reason=it.get("reason") or "",
        )
        session.add(obj)
        created.append(obj)

    created_long = []
    for it in new_long_items or []:
        obj = BizSuggestion(
            first_report_date=report_date,
            kind="long",
            trigger=it["opportunity"],
            action=it["solution"],
            reason=it.get("expectation") or "",
        )
        session.add(obj)
        created_long.append(obj)
    await session.flush()
    return created, created_long


def reminders_for_content(
    active: List[BizSuggestion],
    triggered: Dict[int, Dict[str, Optional[str]]],
    contradicted: Optional[Dict[int, Dict[str, Optional[str]]]] = None,
) -> tuple[List[Dict], List[Dict]]:
    """Chia gợi ý đang nhớ thành (reminders — hôm nay tình huống đã xảy ra
    (outcome='triggered') hoặc thực tế đi ngược đề xuất (outcome='contradicted'),
    Jenny cập nhật trong báo cáo; tracking — chưa có gì, chỉ ở trong bộ nhớ)."""
    contradicted = contradicted or {}
    reminders, tracking = [], []
    for s in active:
        base = {
            "id": s.id,  # để admin gỡ đúng gợi ý (POST .../biz-suggestions/{id}/dismiss)
            "suggested_date": s.first_report_date,
            "trigger": s.trigger,
            "action": s.action,
            "reason": s.reason,
        }
        info = triggered.get(s.id) or contradicted.get(s.id)
        if info:
            reminders.append({
                **base,
                "outcome": "triggered" if s.id in triggered else "contradicted",
                "evidence": info.get("evidence"),
                "source_name": info.get("source_name"),
                "source_url": info.get("source_url"),
            })
        else:
            # Chưa kích hoạt ≠ đề xuất sai — hiển thị kèm ngày Jenny còn theo dõi.
            tracking.append({**base, "expires_on": expires_on(s.first_report_date)})
    return reminders, tracking
