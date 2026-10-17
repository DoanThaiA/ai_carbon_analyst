"""Đối chiếu dự báo NGẮN HẠN của các báo cáo gần đây với giá EUA thực tế — khối cố định "Dự báo trước:
đúng hay sai" ở trang đầu báo cáo (content["2"].forecast_review) + dữ liệu cho LLM viết "vs_previous".

Tính bằng Python từ OHLC EUA (không để LLM tự chấm): với mỗi báo cáo trong REVIEW_MAX_REPORTS báo cáo gần
nhất có kịch bản ngắn hạn (còn trong khung ngắn hạn REVIEW_MAX_SESSIONS phiên):
  - %Δ luỹ kế từ giá EUA báo cáo đó in (giá tham chiếu) tới phiên mới nhất → chiều dự báo đúng/sai;
  - mô phỏng lệnh của thẻ chiến lược (services/strategy_levels.py): khớp khi biên độ phiên chạm vùng Entry;
    lệnh GIỚI HẠN ("mua quanh 87,0–87,5") mà phiên mở gap qua hẳn vùng theo chiều có lợi (mở 86,87, cả phiên
    dưới 87,0) vẫn khớp, tại giá mở; lệnh chờ phá vỡ ("mua khi đóng cửa trên X") thì không. Sau đó chạm cắt
    lỗ / mục tiêu theo đáy-đỉnh phiên. Riêng PHIÊN KHỚP LỆNH chỉ xét giá ĐÓNG CỬA (nến ngày không cho biết
    thứ tự trong phiên); 1 phiên chạm cả cắt lỗ lẫn mục tiêu → tính cắt lỗ (thận trọng);
  - kết luận: chạm cắt lỗ → SAI, chạm mục tiêu → ĐÚNG; còn lại theo chiều %Δ luỹ kế — nhưng CHƯA hết khung
    REVIEW_MAX_SESSIONS phiên thì chỉ là "ĐANG LỆCH"/"ĐANG ĐÚNG HƯỚNG" (dự báo 1–2 tuần không bị chấm SAI
    sau 1 phiên).
Bảng gồm REVIEW_MAX_REPORTS báo cáo gần nhất + báo cáo cũ hơn bị chấm SAI còn trong khung (tối đa REVIEW_MAX_ITEMS) —
vd báo cáo 17/10 vẫn hiện bản 13/10 sai dù 3 bản gần nhất là 14, 15, 16/10.
Kết quả là dữ liệu thuần (số + enum ASCII; direction/probability giữ giá trị gốc tiếng Việt — khoá được
report_translation bảo vệ) — frontend dựng câu theo ngôn ngữ.

Vd báo cáo 13/10/2026: "Tăng, xác suất Cao", mua 87,2–88,0, cắt lỗ 86,30, EUA tham chiếu 88,34 → khớp 13/10,
đóng cửa 15/10 85,84 thủng cắt lỗ → SAI, −2,83%.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Instrument, Price, Report
from services.strategy_levels import strategy_levels

REVIEW_MAX_REPORTS = 3        # luôn hiện N báo cáo gần nhất có kịch bản ngắn hạn...
REVIEW_MAX_ITEMS = 5          # ...cộng các báo cáo CŨ HƠN bị chấm SAI còn trong khung (không để dự báo sai rơi khỏi bảng)
REVIEW_MAX_SESSIONS = 10      # khung "ngắn hạn" 1–2 tuần
DIRECTION_THRESHOLD_PCT = 0.5  # |%Δ luỹ kế| dưới mức này → chưa rõ chiều
SIDEWAYS_MAX_PCT = 1.5         # dự báo "đi ngang" đúng khi |%Δ luỹ kế| dưới mức này
_SUMMARY_LINE_RE = re.compile(r"\*\*Tổng hợp:?\*\*\s*(.*)")


def _hl(s: Dict[str, Any]) -> Tuple[float, float, float]:
    c = float(s["close"])
    return float(s.get("high") if s.get("high") is not None else c), float(s.get("low") if s.get("low") is not None else c), c


def simulate_trade(direction: str, levels: Optional[Dict[str, Any]], sessions: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Mô phỏng lệnh của thẻ chiến lược trên các phiên sau phát hành. None nếu không tách được mức giá."""
    if not levels or direction not in ("tăng", "giảm"):
        return None
    long = direction == "tăng"
    zone = levels["entry_buy"] if long else levels["entry_sell"]
    stop = levels["stop_lower"] if long else levels["stop_upper"]
    target = levels["target_buy"] if long else levels["target_sell"]
    if not zone or stop is None:
        return None
    lo, hi = min(zone), max(zone)
    limit_order = not levels.get("entry_breakout")
    trade: Dict[str, Any] = {
        "side": "buy" if long else "sell", "entry_low": lo, "entry_high": hi, "stop": stop, "target": target,
        "status": "not_filled", "filled_date": None, "fill_price": None, "exit_date": None, "exit_price": None,
    }
    for s in sessions:
        high, low, close = _hl(s)
        if trade["status"] == "not_filled":
            touched = low <= hi and high >= lo
            # Giá đi qua hẳn vùng theo chiều có lợi (mua: cả phiên dưới vùng; bán: cả phiên trên vùng).
            gapped = limit_order and (high < lo if long else low > hi)
            if touched or gapped:
                open_p = s.get("open")
                trade["status"], trade["filled_date"] = "open", s["date"]
                if gapped:
                    trade["fill_price"] = round(float(open_p if open_p is not None else (high if long else low)), 2)
                elif open_p is not None and (lo <= open_p <= hi):
                    trade["fill_price"] = round(float(open_p), 2)
                else:
                    trade["fill_price"] = hi if long else lo
                hit_stop = close <= stop if long else close >= stop
                hit_target = target is not None and (close >= target if long else close <= target)
            else:
                continue
        else:
            hit_stop = low <= stop if long else high >= stop
            hit_target = target is not None and (high >= target if long else low <= target)
        if hit_stop:
            trade.update(status="stopped", exit_date=s["date"], exit_price=close)
            break
        if hit_target:
            trade.update(status="target_hit", exit_date=s["date"], exit_price=target)
            break
    return trade


def direction_verdict(direction: str, change_pct: float, final: bool = True) -> str:
    """`final=False` (chưa hết khung ngắn hạn) → "on_track"/"diverging" thay cho "correct"/"wrong"."""
    verdict = _direction_verdict(direction, change_pct)
    if not final:
        return {"correct": "on_track", "wrong": "diverging"}.get(verdict, verdict)
    return verdict


def _direction_verdict(direction: str, change_pct: float) -> str:
    if direction == "đi ngang":
        return "correct" if abs(change_pct) < SIDEWAYS_MAX_PCT else "wrong"
    sign = 1 if direction == "tăng" else -1 if direction == "giảm" else 0
    if not sign:
        return "unclear"
    if sign * change_pct >= DIRECTION_THRESHOLD_PCT:
        return "correct"
    if sign * change_pct <= -DIRECTION_THRESHOLD_PCT:
        return "wrong"
    return "unclear"


def evaluate_forecast(
    report_date: str, scenario: Dict[str, Any], ref_close: float, sessions: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """`sessions`: phiên EUA từ ngày phát hành (`report_date`, dữ liệu báo cáo là phiên hôm trước) trở đi."""
    if not sessions or not ref_close:
        return None
    direction = scenario.get("direction") or ""
    last = sessions[-1]
    change_pct = (float(last["close"]) / ref_close - 1) * 100
    trade = simulate_trade(direction, strategy_levels(scenario), sessions)
    if trade and trade["status"] == "stopped":
        verdict = "wrong"
    elif trade and trade["status"] == "target_hit":
        verdict = "correct"
    else:
        verdict = direction_verdict(direction, change_pct, final=len(sessions) >= REVIEW_MAX_SESSIONS)
    return {
        "report_date": report_date,
        "direction": direction,
        "probability": scenario.get("probability"),
        "ref_close": round(ref_close, 2),
        "last_date": last["date"],
        "last_close": round(float(last["close"]), 2),
        "change_pct": round(change_pct, 2),
        "sessions": len(sessions),
        "verdict": verdict,
        "high_confidence_miss": verdict == "wrong" and scenario.get("probability") == "Cao",
        "trade": trade,
    }


def _ref_close(content: Dict[str, Any]) -> Optional[float]:
    for p in (content.get("2") or {}).get("prices") or []:
        if isinstance(p, dict) and str(p.get("code", "")).upper() == "EUA" and isinstance(p.get("close"), (int, float)):
            return float(p["close"])
    return None


def _premise(content: Dict[str, Any]) -> str:
    for b in (content.get("3") or {}).get("analysis_blocks") or []:
        if isinstance(b, dict):
            m = _SUMMARY_LINE_RE.search(str(b.get("content") or ""))
            if m:
                return m.group(1).strip()
    return ""


async def _eua_sessions(session: AsyncSession, start: str, end: str) -> List[Dict[str, Any]]:
    rows = (await session.execute(
        select(Price)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Instrument.code == "EUA", Price.price_date >= start, Price.price_date <= end)
        .order_by(Price.price_date)
    )).scalars().all()
    return [
        {"date": p.price_date, "open": p.open_price, "high": p.high_price, "low": p.low_price, "close": p.close_price}
        for p in rows
    ]


async def build_forecast_review(session: AsyncSession, report_date: str, data_date: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """-> (khối content["2"].forecast_review hoặc None, văn bản cho prompt vs_previous)."""
    reports = (await session.execute(
        select(Report)
        .where(Report.report_date < report_date, Report.status.in_(["draft", "published"]))
        .order_by(desc(Report.report_date))
        .limit(REVIEW_MAX_REPORTS + 6)
    )).scalars().all()
    candidates = []
    for r in reports:
        content = r.content or {}
        short = next((sc for sc in (content.get("3") or {}).get("trading_scenarios") or []
                      if isinstance(sc, dict) and sc.get("horizon") == "ngắn hạn"), None)
        ref = _ref_close(content)
        if short and ref:
            candidates.append((r.report_date, short, ref, _premise(content)))
    if not candidates:
        return None, ""

    all_sessions = await _eua_sessions(session, min(c[0] for c in candidates), data_date)
    items, premises = [], {}
    for idx, (rdate, short, ref, premise) in enumerate(candidates):
        sessions = [s for s in all_sessions if s["date"] >= rdate]
        if len(sessions) > REVIEW_MAX_SESSIONS:
            continue  # đã quá khung ngắn hạn
        item = evaluate_forecast(rdate, short, ref, sessions)
        if not item or (idx >= REVIEW_MAX_REPORTS and item["verdict"] != "wrong"):
            continue
        items.append(item)
        premises[rdate] = premise
        if len(items) >= REVIEW_MAX_ITEMS:
            break
    if not items:
        return None, ""

    wrong_in_row = 0
    for it in items:  # mới → cũ
        if it["verdict"] != "wrong":
            break
        wrong_in_row += 1
    block = {"items": items, "wrong_in_row": wrong_in_row}
    return block, describe_for_prompt(block, premises)


def _vn(x: float, sign: bool = False) -> str:
    s = f"{x:+,.2f}" if sign else f"{x:,.2f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _dm(iso: Optional[str]) -> str:
    return date.fromisoformat(iso).strftime("%d/%m") if iso else "?"


TRADE_STATUS_VI = {
    "not_filled": "lệnh chưa khớp", "open": "lệnh đã khớp, đang mở",
    "stopped": "lệnh đã CHẠM CẮT LỖ", "target_hit": "lệnh đã chạm mục tiêu",
}
VERDICT_VI = {
    "correct": "ĐÚNG", "wrong": "SAI", "unclear": "chưa rõ",
    "on_track": "ĐANG ĐÚNG HƯỚNG (chưa hết khung 1–2 tuần)", "diverging": "ĐANG LỆCH (chưa hết khung 1–2 tuần — chưa kết luận sai)",
}


def describe_for_prompt(block: Optional[Dict[str, Any]], premises: Optional[Dict[str, str]] = None) -> str:
    if not block:
        return ""
    premises = premises or {}
    lines = ["ĐỐI CHIẾU DỰ BÁO GẦN ĐÂY (tính sẵn từ giá EUA thật + mô phỏng lệnh — dùng đúng, KHÔNG tự tính lại; "
             "khối này đã hiển thị dạng bảng ở trang đầu):"]
    for it in block["items"]:
        t = it.get("trade")
        trade_txt = ""
        if t:
            side = "mua" if t["side"] == "buy" else "bán"
            trade_txt = (
                f"; lệnh {side} {_vn(t['entry_low'])}–{_vn(t['entry_high'])}, cắt lỗ {_vn(t['stop'])}"
                + (f", mục tiêu {_vn(t['target'])}" if t.get("target") is not None else "")
                + f" → {TRADE_STATUS_VI[t['status']]}"
                + (f" ngày {_dm(t['exit_date'])}" if t.get("exit_date") else "")
                + (f" (khớp {_dm(t['filled_date'])}" + (f" tại {_vn(t['fill_price'])}" if t.get("fill_price") is not None else "") + ")"
                   if t.get("filled_date") else "")
            )
        lines.append(
            f"- Báo cáo {_dm(it['report_date'])}: dự báo ngắn hạn \"{it['direction']}\" (xác suất {it['probability']}); EUA "
            f"{_vn(it['ref_close'])} → {_vn(it['last_close'])} ({_vn(it['change_pct'], sign=True)}%, {it['sessions']} phiên tới "
            f"{_dm(it['last_date'])}){trade_txt} → {VERDICT_VI[it['verdict']]}"
            + (" — SAI dù gắn xác suất CAO" if it.get("high_confidence_miss") else "")
            + (f". Tiền đề khi đó: {premises[it['report_date']][:300]}" if premises.get(it["report_date"]) else "")
        )
    if block.get("wrong_in_row", 0) >= 2:
        lines.append(f"⚠ {block['wrong_in_row']} báo cáo LIÊN TIẾP dự báo sai.")
    return "\n".join(lines)


def open_trades(block: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lệnh mô phỏng đã khớp và CHƯA chạm cắt lỗ/mục tiêu — báo cáo mới phải nói giữ/dời/đóng (Mục 3 open_positions)."""
    return [
        {**it["trade"], "report_date": it["report_date"]}
        for it in (block or {}).get("items") or []
        if isinstance(it.get("trade"), dict) and it["trade"].get("status") == "open"
    ]


def describe_open_trades(trades: List[Dict[str, Any]]) -> str:
    lines = []
    for t in trades:
        side = "Mua" if t["side"] == "buy" else "Bán"
        lines.append(
            f"- report_date {t['report_date']}: {side} {_vn(t['entry_low'])}–{_vn(t['entry_high'])}, khớp {_dm(t['filled_date'])}"
            + (f" tại {_vn(t['fill_price'])}" if t.get("fill_price") is not None else "")
            + f", cắt lỗ {_vn(t['stop'])}" + (f", mục tiêu {_vn(t['target'])}" if t.get("target") is not None else "")
        )
    return "\n".join(lines)


OPEN_POSITION_DECISIONS = ("keep", "move", "close")


def merge_open_positions(trades: List[Dict[str, Any]], llm_items: Any) -> List[Dict[str, Any]]:
    """Ghép số liệu lệnh (Python) với quyết định LLM theo report_date. Lệnh LLM bỏ sót → decision None (Jack QC báo)."""
    by_date = {
        str(it.get("report_date")): it for it in (llm_items if isinstance(llm_items, list) else [])
        if isinstance(it, dict) and it.get("decision") in OPEN_POSITION_DECISIONS and str(it.get("text") or "").strip()
    }
    out = []
    for t in trades:
        d = by_date.get(t["report_date"])
        out.append({
            "report_date": t["report_date"], "side": t["side"], "entry_low": t["entry_low"], "entry_high": t["entry_high"],
            "filled_date": t["filled_date"], "fill_price": t.get("fill_price"), "stop": t["stop"], "target": t.get("target"),
            "decision": d["decision"] if d else None, "text": str(d["text"]).strip() if d else "",
        })
    return out
