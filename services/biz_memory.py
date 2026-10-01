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
    return {"code": code, "op": op, "value": value}


def check_price_rule(rule: Dict[str, Any], prices: List[Dict]) -> Optional[str]:
    """Trả về câu bằng chứng nếu ngưỡng giá đã chạm, None nếu chưa/không có giá."""
    price = next((p for p in prices if str(p.get("code", "")).upper() == rule["code"]), None)
    close = price.get("close") if price else None
    if close is None:
        return None
    value, op = rule["value"], rule["op"]
    hit = {
        ">": close > value, ">=": close >= value,
        "<": close < value, "<=": close <= value,
    }[op]
    if not hit:
        return None
    return f"{price.get('name') or rule['code']} đóng cửa {close:,.2f} (ngưỡng {op} {value:,.2f})."


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
                f"[S{s.id}] (đề xuất ngày {_fmt_vn_date(s.first_report_date)}) Tình huống: {s.trigger} | Hành động: {s.action}"
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
            tracking.append(base)
    return reminders, tracking
