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
  - lệnh chờ hết hiệu lực sau ORDER_VALID_SESSIONS phiên chưa khớp; giá chạm mục tiêu trước khi khớp → huỷ lệnh, dự báo ĐÚNG
    hướng (lỗi thực thi: Entry quá xa) — không để lệnh treo rồi khớp vào nhịp ngược chiều như bản 09/10;
  - kết luận: chạm cắt lỗ → SAI, chạm mục tiêu → ĐÚNG, mục tiêu tới trước khi khớp → "đúng hướng, không khớp lệnh"
    (đếm riêng, không vào tỷ lệ đúng); còn lại theo chiều %Δ luỹ kế — nhưng CHƯA hết khung
    REVIEW_MAX_SESSIONS phiên thì chỉ là "ĐANG LỆCH"/"ĐANG ĐÚNG HƯỚNG" (dự báo 1–2 tuần không bị chấm SAI
    sau 1 phiên).
Bảng gồm REVIEW_MAX_REPORTS báo cáo gần nhất + báo cáo cũ hơn bị chấm SAI còn trong khung (tối đa REVIEW_MAX_ITEMS) —
vd báo cáo 17/10 vẫn hiện bản 13/10 sai dù 3 bản gần nhất là 14, 15, 16/10.
Kết quả là dữ liệu thuần (số + enum ASCII; direction/probability giữ giá trị gốc tiếng Việt — khoá được
report_translation bảo vệ) — frontend dựng câu theo ngôn ngữ.

Vd báo cáo 13/10/2026: "Tăng, xác suất Cao", mua 87,2–88,0, cắt lỗ 86,30, EUA tham chiếu 88,34 → khớp 13/10,
đóng cửa 15/10 85,84 thủng cắt lỗ → SAI, −2,83%.

Bổ sung sau bản chấm báo cáo 17/10/2026:
  - "đi ngang" chấm cả HÀNH TRÌNH: giá đóng cửa lệch tham chiếu ≥ SIDEWAYS_PATH_MAX_PCT ở bất kỳ phiên nào trong khung
    → không còn là đi ngang (bản 08/10 được ghi "đúng hướng" dù EUA đã lên 88,34, +3,6% giữa kỳ);
  - "graded_by": "trade" (kết luận theo lệnh: chạm cắt lỗ/mục tiêu/huỷ) hay "direction" (theo %Δ) — dòng 10/10 "ĐÚNG"
    mà hiển thị −1,76% làm người đọc nghi ngờ khi không ghi rõ chấm theo lệnh;
  - "design_flaws": lỗi THIẾT KẾ lệnh (R:R tại giá khớp < MIN_RR, cắt lỗ cách giá khớp < MIN_STOP_ATR × ATR) — bản 13/10
    R:R 0,48, bản 15/10 cắt lỗ cách giá khớp 0,27 khi ATR 1,70 mà "Nguyên nhân & điều chỉnh" không nhắc;
  - "baseline": tỷ lệ đúng của dự báo ngây thơ (luôn "đi ngang"; lặp chiều 5 phiên trước) trên CÙNG các báo cáo đã có
    kết luận — 16/29 (55%) ở mức Trung bình chỉ có ý nghĩa khi so với chuẩn này.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Instrument, Price, Report
from services.report_consistency import atr_true_range
from services.strategy_levels import strategy_levels

REVIEW_MAX_REPORTS = 3        # (giữ tên cho tương thích) — giờ hiện MỌI báo cáo còn trong khung ngắn hạn...
REVIEW_MAX_ITEMS = 8          # ...tối đa N dòng: báo cáo nào cũng góp vào tỷ lệ đúng nên không được ẩn (bản 17/10 thiếu 09/10)
REVIEW_MAX_SESSIONS = 10      # khung "ngắn hạn" 1–2 tuần
# 1 ngưỡng chung (không chồng lấn): |%Δ| ≥ ngưỡng theo chiều dự báo → đúng chiều; < ngưỡng → "đi ngang" đúng,
# dự báo tăng/giảm thì chưa rõ. Trước đây 0,5% / 1,5% chồng nhau: −0,8% vừa "đúng giảm" vừa "đúng đi ngang".
DIRECTION_THRESHOLD_PCT = 1.0
SIDEWAYS_MAX_PCT = DIRECTION_THRESHOLD_PCT
ORDER_VALID_SESSIONS = 3       # lệnh chờ của thẻ chiến lược hết hiệu lực sau N phiên chưa khớp
CALIBRATION_REPORTS = 7        # tỷ lệ đúng theo mức xác suất: N báo cáo gần nhất ĐÃ CÓ KẾT LUẬN (đúng/sai/đúng hướng không khớp)
CALIBRATION_SCAN_REPORTS = 250  # quét lùi tối đa N báo cáo: đủ cho cửa sổ CALIBRATION_REPORTS lẫn tỷ lệ LŨY KẾ
PROBABILITY_LEVELS = ("Cao", "Trung bình", "Thấp")
SIDEWAYS_PATH_MAX_PCT = 2.0    # "đi ngang": mọi giá đóng cửa trong khung phải nằm trong ±N% giá tham chiếu
MIN_RR = 1.5                   # = report_qc.MIN_RISK_REWARD
MIN_STOP_ATR = 0.5             # cắt lỗ cách giá vào ≥ 0,5 × ATR (quy tắc prompt Mục 3)
MOMENTUM_SESSIONS = 5          # dự báo ngây thơ "lặp chiều N phiên trước"
_SUMMARY_LINE_RE = re.compile(r"\*\*Tổng hợp:?\*\*\s*(.*)")


def _hl(s: Dict[str, Any]) -> Tuple[float, float, float]:
    c = float(s["close"])
    return float(s.get("high") if s.get("high") is not None else c), float(s.get("low") if s.get("low") is not None else c), c


def simulate_trade(
    direction: str, levels: Optional[Dict[str, Any]], sessions: Sequence[Dict[str, Any]], ref_close: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
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
    # Lệnh "dừng" mà giá lúc ra báo cáo đã ở/qua mức kích hoạt (bản 15/10: "mua khi … trên 87,00–87,50" khi EUA
    # đang 87,14) không còn là lệnh chờ phá vỡ — thực chất là mua tại vùng đó → tính như lệnh giới hạn.
    if not limit_order and ref_close is not None and (ref_close >= lo if long else ref_close <= hi):
        limit_order = True
    trade: Dict[str, Any] = {
        "side": "buy" if long else "sell", "order_type": "limit" if limit_order else "stop",
        "entry_low": lo, "entry_high": hi, "stop": stop, "target": target,
        "status": "not_filled", "filled_date": None, "fill_price": None, "exit_date": None, "exit_price": None,
    }
    waited = 0
    prev_date = None
    for s in sessions:
        high, low, close = _hl(s)
        if trade["status"] == "not_filled":
            if waited >= ORDER_VALID_SESSIONS:
                trade.update(status="expired", exit_date=prev_date)
                break
            waited += 1
            prev_date = s["date"]
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
                if gapped:
                    # Khớp ngay tại giá mở → mọi giá trong phiên đều SAU khi khớp: xét đáy/đỉnh phiên.
                    hit_stop = low <= stop if long else high >= stop
                    hit_target = target is not None and (high >= target if long else low <= target)
                else:
                    hit_stop = close <= stop if long else close >= stop
                    hit_target = target is not None and (close >= target if long else close <= target)
            elif target is not None and (high >= target if long else low <= target):
                # Giá đi tới mục tiêu mà chưa chạm vùng Entry → kịch bản đúng hướng nhưng lệnh huỷ (bản 09/10:
                # EUA lên 88,82 vượt mục tiêu 88,50 ngày 12/10, lệnh mua 85,50–86,30 treo tới 15/10 mới khớp).
                trade.update(status="cancelled_target", exit_date=s["date"])
                break
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
    else:
        # Đã qua đủ N phiên mà chưa khớp → hết hiệu lực ngay (không đợi phiên thứ N+1 mới ghi nhận).
        if trade["status"] == "not_filled" and waited >= ORDER_VALID_SESSIONS:
            trade.update(status="expired", exit_date=sessions[-1]["date"])
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


def trade_design_flaws(trade: Optional[Dict[str, Any]], atr: Optional[float]) -> List[Dict[str, Any]]:
    """Lỗi thiết kế lệnh tính tại giá KHỚP thật (hoặc giữa vùng Entry nếu chưa khớp)."""
    if not trade or trade.get("stop") is None:
        return []
    entry = trade.get("fill_price")
    if entry is None:
        entry = (trade["entry_low"] + trade["entry_high"]) / 2
    risk = abs(entry - trade["stop"])
    flaws: List[Dict[str, Any]] = []
    if trade.get("target") is not None and risk > 0:
        rr = abs(trade["target"] - entry) / risk
        if rr < MIN_RR:
            flaws.append({"kind": "low_rr", "rr": round(rr, 2), "entry": round(entry, 2), "risk": round(risk, 2),
                          "reward": round(abs(trade["target"] - entry), 2)})
    if atr and risk < MIN_STOP_ATR * atr:
        flaws.append({"kind": "tight_stop", "distance": round(risk, 2), "atr": round(atr, 2),
                      "atr_multiple": round(risk / atr, 2), "entry": round(entry, 2)})
    return flaws


def evaluate_forecast(
    report_date: str, scenario: Dict[str, Any], ref_close: float, sessions: Sequence[Dict[str, Any]],
    atr: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """`sessions`: phiên EUA từ ngày phát hành (`report_date`, dữ liệu báo cáo là phiên hôm trước) trở đi.
    `atr`: ATR 14 phiên tính tới phiên tham chiếu — để chấm lỗi thiết kế lệnh (None = bỏ qua phần cắt lỗ/ATR)."""
    if not sessions or not ref_close:
        return None
    direction = scenario.get("direction") or ""
    last = sessions[-1]
    change_pct = (float(last["close"]) / ref_close - 1) * 100
    excursions = [(float(x["close"]) / ref_close - 1) * 100 for x in sessions]
    max_excursion = max(excursions, key=abs)
    trade = simulate_trade(direction, strategy_levels(scenario), sessions, ref_close)
    graded_by = "trade"
    final = len(sessions) >= REVIEW_MAX_SESSIONS
    if trade and trade["status"] == "stopped":
        verdict = "wrong"
    elif trade and trade["status"] == "target_hit":
        verdict = "correct"
    elif trade and trade["status"] == "cancelled_target":
        # Đúng hướng nhưng lệnh không khớp (bản 09/10) — tách riêng, KHÔNG tính vào tỷ lệ đúng (không thổi phồng).
        verdict = "direction_only"
    elif direction == "đi ngang" and abs(max_excursion) >= SIDEWAYS_PATH_MAX_PCT:
        graded_by = "path"
        verdict = "wrong" if final else "diverging"
    else:
        graded_by = "direction"
        verdict = direction_verdict(direction, change_pct, final=final)
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
        "graded_by": graded_by,
        "max_excursion_pct": round(max_excursion, 2),
        "design_flaws": trade_design_flaws(trade, atr),
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


def _atr_before(all_sessions: Sequence[Dict[str, Any]], report_date: str) -> Optional[float]:
    return atr_true_range([s for s in all_sessions if s["date"] < report_date])


def _sessions_lookback_start(earliest: str) -> str:
    """Lùi ~1,5 tháng để đủ 14 phiên tính ATR / 5 phiên cho dự báo ngây thơ trước báo cáo cũ nhất."""
    d = date.fromisoformat(earliest)
    return date.fromordinal(d.toordinal() - 45).isoformat()


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

    all_sessions = await _eua_sessions(session, _sessions_lookback_start(min(c[0] for c in candidates)), data_date)
    items, premises = [], {}
    for idx, (rdate, short, ref, premise) in enumerate(candidates):
        sessions = [s for s in all_sessions if s["date"] >= rdate]
        if len(sessions) > REVIEW_MAX_SESSIONS:
            continue  # đã quá khung ngắn hạn
        item = evaluate_forecast(rdate, short, ref, sessions, _atr_before(all_sessions, rdate))
        if not item:
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
    calibration, extra = await _calibration(session, report_date, data_date)
    block = {
        "items": items, "wrong_in_row": wrong_in_row,
        "calibration": calibration,
        "calibration_window": extra.pop("window"),
        "calibration_all": extra.pop("calibration_all"),
        "calibration_all_reports": extra.pop("calibration_all_reports", 0),
        "baseline": extra,
        "rules": {
            "horizon_sessions": REVIEW_MAX_SESSIONS, "direction_threshold_pct": DIRECTION_THRESHOLD_PCT,
            "sideways_max_pct": SIDEWAYS_MAX_PCT, "order_valid_sessions": ORDER_VALID_SESSIONS,
            "calibration_reports": CALIBRATION_REPORTS, "sideways_path_max_pct": SIDEWAYS_PATH_MAX_PCT,
            "min_rr": MIN_RR, "min_stop_atr": MIN_STOP_ATR,
        },
    }
    return block, describe_for_prompt(block, premises)


def calibration_from(evaluated: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tỷ lệ đúng theo mức xác suất, CHỈ tính dự báo đã có kết luận cuối (đúng/sai)."""
    out = []
    for level in PROBABILITY_LEVELS:
        final = [e for e in evaluated if e.get("probability") == level and e.get("verdict") in ("correct", "wrong")]
        out.append({
            "probability": level, "correct": sum(e["verdict"] == "correct" for e in final), "total": len(final),
            "direction_only": sum(1 for e in evaluated if e.get("probability") == level and e.get("verdict") == "direction_only"),
        })
    return out


def naive_direction(prior: Sequence[Dict[str, Any]], n: int = MOMENTUM_SESSIONS) -> str:
    """Dự báo ngây thơ "lặp chiều N phiên trước": %Δ đóng cửa N phiên tới phiên tham chiếu, cùng ngưỡng chấm."""
    closes = [float(s["close"]) for s in prior if s.get("close") is not None]
    if len(closes) <= n:
        return "đi ngang"
    pct = (closes[-1] / closes[-1 - n] - 1) * 100
    return "tăng" if pct >= DIRECTION_THRESHOLD_PCT else "giảm" if pct <= -DIRECTION_THRESHOLD_PCT else "đi ngang"


def baseline_from(evaluated: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """So tỷ lệ đúng của báo cáo với 2 dự báo ngây thơ trên CÙNG các báo cáo đã có kết luận đúng/sai. Dự báo ngây thơ
    chấm theo %Δ cuối khung + hành trình (như "đi ngang" của báo cáo), không có lệnh."""
    final = [e for e in evaluated if e.get("verdict") in ("correct", "wrong")]

    def _score(direction_of) -> Dict[str, int]:
        ok = total = 0
        for e in final:
            d = direction_of(e)
            if d == "đi ngang" and abs(e.get("max_excursion_pct") or 0) >= SIDEWAYS_PATH_MAX_PCT:
                v = "wrong"
            else:
                v = _direction_verdict(d, e["change_pct"])
            if v in ("correct", "wrong"):
                total += 1
                ok += v == "correct"
        return {"correct": ok, "total": total}

    return {
        "model": {"correct": sum(e["verdict"] == "correct" for e in final), "total": len(final)},
        "always_sideways": _score(lambda e: "đi ngang"),
        "momentum": {**_score(lambda e: e.get("naive_momentum") or "đi ngang"), "sessions": MOMENTUM_SESSIONS},
    }


def window_reports(evaluated: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Các báo cáo tạo nên tỷ lệ của cửa sổ — để người đọc kiểm lại được: bản chấm 17/10 tính "Cao 0/2" chỉ từ 3 dòng đã
    kết luận trong bảng, trong khi cửa sổ gồm cả báo cáo cũ đã ra khỏi bảng (quá khung 10 phiên)."""
    return [{"report_date": e["report_date"], "probability": e.get("probability"), "verdict": e["verdict"]} for e in evaluated]


async def _calibration(session: AsyncSession, report_date: str, data_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    reports = (await session.execute(
        select(Report)
        .where(Report.report_date < report_date, Report.status.in_(["draft", "published"]))
        .order_by(desc(Report.report_date))
        .limit(CALIBRATION_SCAN_REPORTS)
    )).scalars().all()
    candidates = []
    for r in reports:
        content = r.content or {}
        short = next((sc for sc in (content.get("3") or {}).get("trading_scenarios") or []
                      if isinstance(sc, dict) and sc.get("horizon") == "ngắn hạn"), None)
        ref = _ref_close(content)
        if short and ref:
            candidates.append((r.report_date, short, ref))
    if not candidates:
        return calibration_from([]), {**baseline_from([]), "window": [], "calibration_all": calibration_from([])}
    all_sessions = await _eua_sessions(session, _sessions_lookback_start(min(c[0] for c in candidates)), data_date)
    evaluated = []
    for rdate, short, ref in candidates:  # mới → cũ
        sessions = [s for s in all_sessions if s["date"] >= rdate][:REVIEW_MAX_SESSIONS]
        ev = evaluate_forecast(rdate, short, ref, sessions)
        # Chỉ lấy báo cáo đã có kết luận — báo cáo còn trong khung (đang đúng hướng/đang lệch) không chiếm suất.
        if ev and ev["verdict"] in ("correct", "wrong", "direction_only"):
            ev["naive_momentum"] = naive_direction([s for s in all_sessions if s["date"] < rdate])
            evaluated.append(ev)
    window = evaluated[:CALIBRATION_REPORTS]
    return calibration_from(window), {
        **baseline_from(window),
        "window": window_reports(window),
        # Tỷ lệ LŨY KẾ mọi báo cáo đã có kết luận trong CALIBRATION_SCAN_REPORTS báo cáo gần nhất (bản cũ hiển thị số này).
        "calibration_all": calibration_from(evaluated),
        "calibration_all_reports": len(evaluated),
    }


def _vn(x: float, sign: bool = False) -> str:
    s = f"{x:+,.2f}" if sign else f"{x:,.2f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _zone(lo: float, hi: float) -> str:
    """"88,82" thay vì vùng độ rộng 0 "88,82–88,82"."""
    return _vn(lo) if abs(hi - lo) < 0.005 else f"{_vn(lo)}–{_vn(hi)}"


def _dm(iso: Optional[str]) -> str:
    return date.fromisoformat(iso).strftime("%d/%m") if iso else "?"


ORDER_TYPE_VI = {"limit": "lệnh giới hạn", "stop": "lệnh dừng (vào khi giá phá mức)"}
TRADE_STATUS_VI = {
    "not_filled": "lệnh chưa khớp", "open": "lệnh đã khớp, đang mở",
    "stopped": "lệnh đã CHẠM CẮT LỖ", "target_hit": "lệnh đã chạm mục tiêu",
    "expired": f"lệnh HẾT HIỆU LỰC sau {ORDER_VALID_SESSIONS} phiên không khớp",
    "cancelled_target": "giá chạm mục tiêu TRƯỚC khi khớp — lệnh huỷ (đúng hướng nhưng Entry đặt quá xa)",
}
GRADED_BY_VI = {"trade": "chấm theo LỆNH", "direction": "chấm theo %Δ cuối khung", "path": "chấm theo HÀNH TRÌNH giá"}


def describe_design_flaws(flaws: Sequence[Dict[str, Any]]) -> str:
    parts = []
    for f in flaws or []:
        if f["kind"] == "low_rr":
            parts.append(f"R:R tại giá vào {_vn(f['entry'])} chỉ {_vn(f['rr'])} (rủi ro {_vn(f['risk'])}, lợi nhuận "
                         f"{_vn(f['reward'])}; tối thiểu {_vn(MIN_RR)})")
        elif f["kind"] == "tight_stop":
            parts.append(f"cắt lỗ cách giá vào {_vn(f['entry'])} chỉ {_vn(f['distance'])} = {_vn(f['atr_multiple'])} × ATR "
                         f"{_vn(f['atr'])} (tối thiểu {_vn(MIN_STOP_ATR)} ATR — nằm trong nhiễu phiên)")
    return "; ".join(parts)


VERDICT_VI = {
    "correct": "ĐÚNG", "wrong": "SAI", "unclear": "chưa rõ",
    "on_track": "ĐANG ĐÚNG HƯỚNG (chưa hết khung 1–2 tuần)",
    "direction_only": "ĐÚNG HƯỚNG, KHÔNG KHỚP LỆNH (Entry quá xa — không tính vào tỷ lệ đúng)", "diverging": "ĐANG LỆCH (chưa hết khung 1–2 tuần — chưa kết luận sai)",
}


WINDOW_VERDICT_VI = {"correct": "Đ", "wrong": "S", "direction_only": "không tính"}
DRIFT_MIN_TOTAL = 5        # lũy kế phải có ≥ N báo cáo của mức đó mới so
DRIFT_GAP_PCT = 20.0       # tỷ lệ gần đây thấp hơn lũy kế ≥ N điểm % → độ tin cậy mức đó đang suy giảm


def calibration_drift(window: Sequence[Dict[str, Any]], cumulative: Sequence[Dict[str, Any]]) -> List[str]:
    """"Cao 0/2 gần đây vs 9/13 lũy kế" → cảnh báo độ tin cậy mức Cao suy giảm (lý do có căn cứ để hạ mức xác suất)."""
    out = []
    by_level = {c["probability"]: c for c in cumulative}
    for c in window:
        a = by_level.get(c["probability"])
        if not c.get("total") or not a or a.get("total", 0) < DRIFT_MIN_TOTAL:
            continue
        recent, overall = c["correct"] / c["total"] * 100, a["correct"] / a["total"] * 100
        if overall - recent >= DRIFT_GAP_PCT:
            out.append(
                f"⚠ Độ tin cậy mức \"{c['probability']}\" đang suy giảm: gần đây {c['correct']}/{c['total']} so với lũy kế "
                f"{a['correct']}/{a['total']} — lý do có căn cứ để KHÔNG dùng mức này (ngoài việc thiếu dữ liệu vị thế quỹ); "
                "\"vs_previous\" nên nêu"
            )
    return out


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
                f"; {ORDER_TYPE_VI.get(t.get('order_type'), 'lệnh')} {side} {_zone(t['entry_low'], t['entry_high'])}, cắt lỗ {_vn(t['stop'])}"
                + (f", mục tiêu {_vn(t['target'])}" if t.get("target") is not None else "")
                + f" → {TRADE_STATUS_VI[t['status']]}"
                + (f" ngày {_dm(t['exit_date'])}" if t.get("exit_date") else "")
                + (f" (khớp {_dm(t['filled_date'])}" + (f" tại {_vn(t['fill_price'])}" if t.get("fill_price") is not None else "") + ")"
                   if t.get("filled_date") else "")
            )
        flaws = describe_design_flaws(it.get("design_flaws") or [])
        lines.append(
            f"- Báo cáo {_dm(it['report_date'])}: dự báo ngắn hạn \"{it['direction']}\" (xác suất {it['probability']}); EUA "
            f"{_vn(it['ref_close'])} → {_vn(it['last_close'])} ({_vn(it['change_pct'], sign=True)}%, {it['sessions']} phiên tới "
            f"{_dm(it['last_date'])}"
            + (f"; lệch xa nhất {_vn(it['max_excursion_pct'], sign=True)}%" if it.get("max_excursion_pct") is not None else "")
            + f"){trade_txt} → {VERDICT_VI[it['verdict']]}"
            + (f" ({GRADED_BY_VI[it['graded_by']]})" if it.get("graded_by") in GRADED_BY_VI else "")
            + (" — SAI dù gắn xác suất CAO" if it.get("high_confidence_miss") else "")
            + (f". LỖI THIẾT KẾ LỆNH: {flaws}" if flaws else "")
            + (f". Tiền đề khi đó: {premises[it['report_date']][:300]}" if premises.get(it["report_date"]) else "")
        )
    if block.get("wrong_in_row", 0) >= 2:
        lines.append(f"⚠ {block['wrong_in_row']} báo cáo LIÊN TIẾP dự báo sai.")
    if any(it.get("design_flaws") and it["verdict"] in ("wrong", "diverging") for it in block["items"]):
        lines.append("Dự báo sai/đang lệch có LỖI THIẾT KẾ LỆNH ở trên → \"vs_previous\" PHẢI nêu lỗi đó (R:R/cắt lỗ so ATR) "
                     "như 1 nguyên nhân, không chỉ tiền đề phân tích.")
    base = block.get("baseline") or {}
    if (base.get("model") or {}).get("total"):
        m, s_, mo = base["model"], base.get("always_sideways") or {}, base.get("momentum") or {}
        lines.append(
            f"CHUẨN SO SÁNH (cùng {m['total']} báo cáo đã có kết luận): báo cáo đúng {m['correct']}/{m['total']}; dự báo ngây thơ "
            f"\"luôn đi ngang\" đúng {s_.get('correct', 0)}/{s_.get('total', 0)}; \"lặp chiều {mo.get('sessions')} phiên trước\" đúng "
            f"{mo.get('correct', 0)}/{mo.get('total', 0)}. Báo cáo KHÔNG hơn chuẩn ngây thơ thì không được gọi độ tin cậy là cao."
        )
    calib = [c for c in block.get("calibration") or [] if c.get("total") or c.get("direction_only")]
    if calib:
        win = block.get("calibration_window") or []
        lines.append(
            f"TỶ LỆ ĐÚNG {CALIBRATION_REPORTS} BÁO CÁO GẦN NHẤT ĐÃ CÓ KẾT LUẬN, theo mức xác suất: "
            + "; ".join(f"{c['probability']} {c['correct']}/{c['total']}"
                        + (f" (+{c['direction_only']} đúng hướng nhưng không khớp lệnh)" if c.get("direction_only") else "")
                        for c in calib)
            + (f" — gồm báo cáo {', '.join(_dm(w['report_date']) + ' (' + WINDOW_VERDICT_VI.get(w['verdict'], w['verdict']) + ')' for w in win)}"
               f" (có báo cáo đã ra khỏi bảng vì quá khung {REVIEW_MAX_SESSIONS} phiên; báo cáo trong bảng còn \"đang...\" chưa được "
               "tính; KHÔNG suy lại tỷ lệ từ riêng các dòng trong bảng)" if win else "")
            + ". LŨY KẾ: " + "; ".join(
                f"{c['probability']} {c['correct']}/{c['total']}" for c in block.get("calibration_all") or [] if c.get("total"))
            + "".join(f". {w}" for w in calibration_drift(block.get("calibration") or [], block.get("calibration_all") or []))
            + ". Mức nào tỷ lệ đúng < 50% (vd \"Cao\") thì KHÔNG dùng lại mức đó trừ khi có ≥2 yếu tố độc lập đã xác nhận; "
            "nguyên nhân dự báo sai phải nêu cả phần THỰC THI (Entry quá xa, lệnh treo/hết hiệu lực) chứ không chỉ tiền đề."
        )
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
            f"- report_date {t['report_date']}: {side} {_zone(t['entry_low'], t['entry_high'])}, khớp {_dm(t['filled_date'])}"
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
