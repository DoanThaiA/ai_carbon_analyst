"""
Chỉ số chất lượng cho trang Thống kê hiệu suất (/admin/performance, api/routers/admin_stats.py), ngoài lượt
xem/lượt hỏi đáp:

  - Chất lượng hỏi đáp: phiên Quote Chat theo đánh giá của người dùng (tốt/không tốt/chưa đánh giá) và số cặp
    hỏi-đáp admin đưa vào tham khảo (quote_chat_examples) — theo ngày báo cáo của phiên.
  - Chất lượng dự báo: kịch bản NGẮN HẠN của từng báo cáo đã phát hành, chấm bằng CHÍNH
    forecast_review.evaluate_forecast (cùng khung REVIEW_MAX_SESSIONS phiên) — không tự chấm riêng để không trái với
    bảng "Dự báo trước" trong báo cáo.
  - Chất lượng dự báo CBAM: ước tính giá CBAM quý (content["2"].cbam_estimate) so với giá chốt của quý đó — ưu tiên
    giá EC công bố (bảng prices mã CBAM, công bố đầu quý sau), quý đã kết thúc mà chưa có giá EC thì dùng VWAP mọi phiên
    đấu giá trong quý (cách EC tính); quý đang chạy chưa chấm. Kèm VWAP đấu giá EUA của tuần chứa ngày báo cáo
    (đường tham chiếu trên biểu đồ).

Kết quả là số + enum ASCII (direction/probability giữ giá trị gốc tiếng Việt như forecast_review) — frontend dựng chữ.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ChatMessage, ChatSession, EuaAuction, Instrument, Price, QuoteChatExample, Report
from services import forecast_review as fr
from services.cbam_estimate import quarter_bounds, quarter_of, vwap

FINAL_VERDICTS = ("correct", "wrong")
PENDING_VERDICTS = ("on_track", "diverging", "unclear")


# ── Hỏi đáp ────────────────────────────────────────────────────────────────────

async def build_chat_quality(session: AsyncSession, since: str, report_dates: Sequence[str]) -> Dict[str, Any]:
    """Phiên có ít nhất 1 câu hỏi của người dùng, theo report_date ≥ since."""
    asked = (
        select(ChatMessage.session_id)
        .where(ChatMessage.role == "user")
        .group_by(ChatMessage.session_id)
        .subquery()
    )
    rows = (await session.execute(
        select(ChatSession.report_date, ChatSession.rating, func.count(ChatSession.id))
        .join(asked, asked.c.session_id == ChatSession.id)
        .where(ChatSession.report_date >= since)
        .group_by(ChatSession.report_date, ChatSession.rating)
    )).all()
    examples = dict((await session.execute(
        select(ChatSession.report_date, func.count(distinct(QuoteChatExample.id)))
        .join(ChatSession, QuoteChatExample.source_session_id == ChatSession.id)
        .where(ChatSession.report_date >= since)
        .group_by(ChatSession.report_date)
    )).all())
    bad_reasons = [
        {"report_date": r.report_date, "reason": r.rating_reason, "session_id": r.id}
        for r in (await session.execute(
            select(ChatSession.id, ChatSession.report_date, ChatSession.rating_reason)
            .where(ChatSession.report_date >= since, ChatSession.rating == "bad")
            .order_by(ChatSession.rated_at.desc().nulls_last())
            .limit(5)
        )).all()
    ]
    return summarize_chat_quality(rows, examples, report_dates, bad_reasons)


def summarize_chat_quality(
    rows: Sequence[Tuple[str, Optional[str], int]],
    examples: Dict[str, int],
    report_dates: Sequence[str],
    bad_reasons: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """rows: (report_date, rating|None, số phiên). Trục ngày = các báo cáo đã phát hành trong kỳ (giống 2 biểu đồ
    lượt xem/hỏi đáp); phiên của ngày không có báo cáo phát hành vẫn tính vào tổng."""
    per: Dict[str, Dict[str, int]] = {}
    for d, rating, n in rows:
        key = rating if rating in ("good", "bad") else "unrated"
        per.setdefault(d, {"good": 0, "bad": 0, "unrated": 0})[key] += n
    totals = {k: sum(p[k] for p in per.values()) for k in ("good", "bad", "unrated")}
    totals["sessions"] = sum(totals.values())
    totals["examples"] = sum(examples.values())
    rated = totals["good"] + totals["bad"]
    totals["rated"] = rated
    totals["good_rate_pct"] = round(totals["good"] / rated * 100, 1) if rated else None
    totals["rated_share_pct"] = round(rated / totals["sessions"] * 100, 1) if totals["sessions"] else None
    items = []
    for d in report_dates:
        p = per.get(d, {"good": 0, "bad": 0, "unrated": 0})
        items.append({"report_date": d, **p, "examples": examples.get(d, 0)})
    return {"totals": totals, "items": items, "recent_bad": bad_reasons or []}


# ── Dự báo ngắn hạn ────────────────────────────────────────────────────────────

async def build_forecast_quality(session: AsyncSession, reports: Sequence[Report]) -> Dict[str, Any]:
    candidates = []
    for r in reports:
        content = r.content or {}
        short = next((sc for sc in (content.get("3") or {}).get("trading_scenarios") or []
                      if isinstance(sc, dict) and sc.get("horizon") == "ngắn hạn"), None)
        ref = fr._ref_close(content)
        if short and ref:
            candidates.append((r.report_date, short, ref))
    if not candidates:
        return summarize_forecasts([])
    all_sessions = await fr._eua_sessions(
        session, fr._sessions_lookback_start(min(c[0] for c in candidates)), "9999-12-31",
    )
    evaluated = []
    for rdate, short, ref in candidates:
        sessions = [s for s in all_sessions if s["date"] >= rdate][:fr.REVIEW_MAX_SESSIONS]
        ev = fr.evaluate_forecast(rdate, short, ref, sessions, fr._atr_before(all_sessions, rdate))
        if ev:
            ev["naive_momentum"] = fr.naive_direction([s for s in all_sessions if s["date"] < rdate])
            evaluated.append(ev)
    return summarize_forecasts(evaluated)


def summarize_forecasts(evaluated: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """evaluated: kết quả evaluate_forecast (+ naive_momentum), mọi thứ tự."""
    evaluated = sorted(evaluated, key=lambda e: e["report_date"])
    counts = {k: 0 for k in ("correct", "wrong", "direction_only", "pending")}
    for e in evaluated:
        v = e["verdict"]
        counts[v if v in counts else "pending"] += 1
    final = counts["correct"] + counts["wrong"]
    # Lệnh mô phỏng đã khớp (đang mở / chạm cắt lỗ / chạm mục tiêu) trên số báo cáo có lệnh.
    with_trade = [e for e in evaluated if e.get("trade")]
    filled = sum(1 for e in with_trade if e["trade"].get("status") in ("open", "stopped", "target_hit"))
    return {
        "totals": {
            **counts,
            "reports": len(evaluated),
            "hit_rate_pct": round(counts["correct"] / final * 100, 1) if final else None,
            "high_confidence_miss": sum(1 for e in evaluated if e.get("high_confidence_miss")),
            "design_flaws": sum(1 for e in evaluated if e.get("design_flaws")),
            "trades": len(with_trade),
            "trades_filled": filled,
        },
        "items": [
            {
                "report_date": e["report_date"], "direction": e["direction"], "probability": e.get("probability"),
                "verdict": e["verdict"] if e["verdict"] in ("correct", "wrong", "direction_only") else "pending",
                "raw_verdict": e["verdict"], "change_pct": e["change_pct"], "sessions": e["sessions"],
                "graded_by": e.get("graded_by"), "trade_status": (e.get("trade") or {}).get("status"),
            }
            for e in evaluated
        ],
        "calibration": fr.calibration_from(evaluated),
        "baseline": fr.baseline_from(evaluated),
        "horizon_sessions": fr.REVIEW_MAX_SESSIONS,
    }


# ── Ước tính CBAM ──────────────────────────────────────────────────────────────

def _quarter_key(label: str) -> Optional[Tuple[int, int]]:
    """"Q3/2026" → (2026, 3) — khớp cbam_estimate.quarter_label."""
    try:
        q, y = label.upper().lstrip("Q").split("/")
        return int(y), int(q)
    except (ValueError, AttributeError):
        return None


async def _quarter_actual(session: AsyncSession, year: int, q: int, today: date) -> Tuple[Optional[float], str]:
    """(giá chốt, basis): 'official' = giá EC công bố trong quý sau; 'vwap' = quý đã hết, tự tính VWAP đấu giá;
    'pending' = quý chưa kết thúc / chưa có dữ liệu."""
    q_start, q_end = quarter_bounds(year, q)
    ny, nq = (year, q + 1) if q < 4 else (year + 1, 1)
    n_start, n_end = quarter_bounds(ny, nq)
    official = (await session.execute(
        select(Price.close_price)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Instrument.code == "CBAM", Price.price_date >= n_start.isoformat(), Price.price_date <= n_end.isoformat())
        .order_by(Price.price_date)
        .limit(1)
    )).scalar_one_or_none()
    if official is not None:
        return float(official), "official"
    if today <= q_end:
        return None, "pending"
    rows = (await session.execute(
        select(EuaAuction.auction_price, EuaAuction.volume)
        .where(EuaAuction.auction_date >= q_start, EuaAuction.auction_date <= q_end,
               EuaAuction.status == "successful", EuaAuction.auction_price.is_not(None))
    )).all()
    v = vwap([(float(p), float(vol)) for p, vol in rows if vol])
    return (round(v, 2), "vwap") if v is not None else (None, "pending")


async def build_cbam_quality(session: AsyncSession, reports: Sequence[Report], today: date) -> Dict[str, Any]:
    estimates = []
    for r in reports:
        est = ((r.content or {}).get("2") or {}).get("cbam_estimate")
        if isinstance(est, dict) and isinstance(est.get("estimate"), (int, float)) and est.get("quarter"):
            estimates.append((r.report_date, est))
    weekly: Dict[str, float] = {}
    if estimates:
        first = week_start(date.fromisoformat(min(d for d, _ in estimates)))
        last = week_start(date.fromisoformat(max(d for d, _ in estimates))) + timedelta(days=6)
        weekly = weekly_vwap((await session.execute(
            select(EuaAuction.auction_date, EuaAuction.auction_price, EuaAuction.volume)
            .where(EuaAuction.auction_date >= first, EuaAuction.auction_date <= last,
                   EuaAuction.status == "successful", EuaAuction.auction_price.is_not(None))
        )).all())
    actuals: Dict[str, Tuple[Optional[float], str]] = {}
    for _, est in estimates:
        label = est["quarter"]
        key = _quarter_key(label)
        if label not in actuals and key:
            actuals[label] = await _quarter_actual(session, key[0], key[1], today)
    return summarize_cbam(estimates, actuals, weekly)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_vwap(rows: Sequence[Tuple[date, Any, Any]]) -> Dict[str, float]:
    """rows: (ngày đấu giá, giá khớp, KL) → {thứ Hai đầu tuần ISO: VWAP các phiên trong tuần}."""
    by_week: Dict[str, List[Tuple[float, float]]] = {}
    for d, price, vol in rows:
        if price is not None and vol:
            by_week.setdefault(week_start(d).isoformat(), []).append((float(price), float(vol)))
    return {w: round(v, 2) for w, r in by_week.items() if (v := vwap(r)) is not None}


def summarize_cbam(
    estimates: Sequence[Tuple[str, Dict[str, Any]]], actuals: Dict[str, Tuple[Optional[float], str]],
    weekly: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    items = []
    for rdate, est in sorted(estimates, key=lambda x: x[0]):
        actual, basis = actuals.get(est["quarter"], (None, "pending"))
        e = float(est["estimate"])
        lo, hi = est.get("ci_low"), est.get("ci_high")
        error_pct = round((e / actual - 1) * 100, 2) if actual else None
        week = week_start(date.fromisoformat(rdate)).isoformat()
        items.append({
            "report_date": rdate, "quarter": est["quarter"], "estimate": round(e, 2),
            "ci_low": lo, "ci_high": hi, "fixed_share_pct": est.get("fixed_share_pct"), "signal": est.get("signal"),
            "actual": actual, "actual_basis": basis, "error_pct": error_pct,
            "week_start": week, "week_vwap": (weekly or {}).get(week),
            "in_ci": (lo <= actual <= hi) if actual is not None and lo is not None and hi is not None else None,
        })
    scored = [i for i in items if i["error_pct"] is not None]
    with_ci = [i for i in scored if i["in_ci"] is not None]
    quarters = []
    for label in dict.fromkeys(i["quarter"] for i in items):
        qi = [i for i in items if i["quarter"] == label]
        qs = [i for i in qi if i["error_pct"] is not None]
        quarters.append({
            "quarter": label, "actual": qi[0]["actual"], "actual_basis": qi[0]["actual_basis"], "reports": len(qi),
            "mape_pct": round(sum(abs(i["error_pct"]) for i in qs) / len(qs), 2) if qs else None,
            "last_error_pct": qs[-1]["error_pct"] if qs else None,
        })
    return {
        "totals": {
            "reports": len(items),
            "scored": len(scored),
            "mape_pct": round(sum(abs(i["error_pct"]) for i in scored) / len(scored), 2) if scored else None,
            "bias_pct": round(sum(i["error_pct"] for i in scored) / len(scored), 2) if scored else None,
            "in_ci": sum(1 for i in with_ci if i["in_ci"]),
            "ci_total": len(with_ci),
            "in_ci_pct": round(sum(1 for i in with_ci if i["in_ci"]) / len(with_ci) * 100, 1) if with_ci else None,
            "ci_level": next((est.get("ci_level") for _, est in estimates if est.get("ci_level")), 90),
        },
        "items": items,
        "quarters": quarters,
    }
