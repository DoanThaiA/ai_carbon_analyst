"""Ước tính giá CBAM của quý đang chạy từ đấu giá sơ cấp EUA trên EEX (bảng eua_auctions).

Giá CBAM 2026 EC công bố theo quý (Quy định thực thi (EU) 2025/2548) = VWAP — trung bình gia quyền
khối lượng — giá khớp của MỌI phiên đấu giá EEX trong quý. Đối chiếu file kết quả 2026: Q1 75,3641 /
Q2 75,2766 / Q3 82,3185 ↔ EC 75,36 / 75,28 / 82,32 (trung bình cộng thường thì lệch).

Ước tính quý đang chạy:
  - phần ĐÃ CHỐT: VWAP các phiên đã có kết quả (tới hết ngày dữ liệu as_of);
  - phần CÒN LẠI: khối lượng theo lịch EEX, giá giả định = giá EUA hiện tại (hợp đồng tháng 12 —
    sát giá đấu giá spot; vd 15/10/2026 cả hai 85,84);
  - khoảng tin cậy: giá phiên còn lại đi theo random walk với biến động ngày thực tế của EUA;
    Var(Σwⱼ·Pⱼ) = S²σ²·ΣᵢΣⱼ wᵢwⱼ·min(tᵢ,tⱼ) (tⱼ = số ngày giao dịch tới phiên j) — tính đúng việc các
    phiên gần nhau tương quan mạnh, không coi là độc lập.

Tín hiệu cho vị thế phòng vệ (người phải mua chứng chỉ CBAM theo giá quý, phòng vệ bằng EUA futures):
  - quý đã chốt ≥ HEDGE_FIXED_SHARE_HOLD khối lượng → "hold" (phần còn hở quá nhỏ);
  - giá hiện tại thấp hơn VWAP đã chốt ≥ HEDGE_GAP_PCT → "increase" (khoá phần còn lại rẻ hơn mức
    đã chốt, kéo giá quý xuống);
  - cao hơn ≥ HEDGE_GAP_PCT → "reduce" (giãn tiến độ mua thêm — mua lúc này khoá cao hơn mức quý đã chốt);
  - còn lại → "hold".
Kết quả là DỮ LIỆU THUẦN (số + enum ASCII) — frontend tự dựng nhãn/câu theo ngôn ngữ, nên bản dịch
máy (services/report_translation.py) không chạm vào số.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import EuaAuction, Instrument, Price

HEDGE_FIXED_SHARE_HOLD = 0.85
HEDGE_GAP_PCT = 2.0
CI_LEVEL = 90
CI_Z = 1.645
MIN_RETURNS_FOR_VOL = 10
UPCOMING_DAYS = 7


def quarter_of(d: date) -> Tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def quarter_bounds(year: int, q: int) -> Tuple[date, date]:
    start = date(year, 3 * (q - 1) + 1, 1)
    end = date(year + (q == 4), (3 * q) % 12 + 1, 1) - timedelta(days=1)
    return start, end


def quarter_label(year: int, q: int) -> str:
    return f"Q{q} {year}"


def business_days_between(start: date, end: date) -> int:
    """Số ngày làm việc (T2–T6) trong (start, end]."""
    if end <= start:
        return 0
    days = (end - start).days
    weeks, rem = divmod(days, 7)
    count = weeks * 5
    for i in range(1, rem + 1):
        if (start + timedelta(days=weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


def daily_log_vol(closes: Sequence[float]) -> Optional[float]:
    """Độ lệch chuẩn lợi suất log ngày; None nếu quá ít phiên."""
    vals = [c for c in closes if isinstance(c, (int, float)) and c > 0]
    rets = [math.log(b / a) for a, b in zip(vals, vals[1:])]
    if len(rets) < MIN_RETURNS_FOR_VOL:
        return None
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))


def vwap(rows: Sequence[Tuple[float, float]]) -> Optional[float]:
    """rows: (giá, khối lượng)."""
    vol = sum(v for _, v in rows)
    return sum(p * v for p, v in rows) / vol if vol else None


def estimate_quarter(
    done: Sequence[Tuple[date, float, float]],
    remaining: Sequence[Tuple[date, float]],
    spot: float,
    sigma: Optional[float],
    as_of: date,
) -> Dict[str, Any]:
    """done: (ngày, giá khớp, KL) các phiên đã có kết quả; remaining: (ngày, KL) phiên còn lại theo lịch."""
    vol_done = sum(v for _, _, v in done)
    vol_rem = sum(v for _, v in remaining)
    vol_total = vol_done + vol_rem
    qtd = vwap([(p, v) for _, p, v in done])
    est = ((qtd or 0) * vol_done + spot * vol_rem) / vol_total if vol_total else spot
    fixed_share = vol_done / vol_total if vol_total else 0.0

    std = None
    if sigma is not None and vol_rem and vol_total:
        ts = [max(1, business_days_between(as_of, d)) for d, _ in remaining]
        ws = [v / vol_rem for _, v in remaining]
        var_avg = sum(wi * wj * min(ti, tj) for wi, ti in zip(ws, ts) for wj, tj in zip(ws, ts))
        std = (vol_rem / vol_total) * spot * sigma * math.sqrt(var_avg)
    elif not vol_rem:
        std = 0.0

    gap = (spot / qtd - 1) * 100 if qtd else None
    if not vol_rem:
        signal, reason = "hold", "quarter_fixed"
    elif not done:
        signal, reason = "hold", "quarter_not_started"
    elif fixed_share >= HEDGE_FIXED_SHARE_HOLD:
        signal, reason = "hold", "quarter_mostly_fixed"
    elif gap <= -HEDGE_GAP_PCT:
        signal, reason = "increase", "spot_below_qtd"
    elif gap >= HEDGE_GAP_PCT:
        signal, reason = "reduce", "spot_above_qtd"
    else:
        signal, reason = "hold", "spot_near_qtd"

    def r2(x: Optional[float]) -> Optional[float]:
        return round(x, 2) if x is not None else None

    return {
        "auctions_done": len(done),
        "auctions_total": len(done) + len(remaining),
        "volume_done_mt": round(vol_done / 1e6, 2),
        "volume_total_mt": round(vol_total / 1e6, 2),
        "fixed_share_pct": round(fixed_share * 100, 1),
        "qtd_vwap": r2(qtd),
        "spot": r2(spot),
        "spot_vs_qtd_pct": r2(gap),
        "estimate": r2(est),
        "ci_level": CI_LEVEL,
        "ci_low": r2(est - CI_Z * std) if std is not None else None,
        "ci_high": r2(est + CI_Z * std) if std is not None else None,
        "daily_vol_pct": r2(sigma * 100) if sigma is not None else None,
        "signal": signal,
        "signal_reason": reason,
        "hedge_gap_pct": HEDGE_GAP_PCT,
        "hedge_fixed_share_pct": round(HEDGE_FIXED_SHARE_HOLD * 100),
    }


async def _auctions_between(session: AsyncSession, start: date, end: date) -> List[EuaAuction]:
    return list((await session.execute(
        select(EuaAuction)
        .where(EuaAuction.auction_date >= start, EuaAuction.auction_date <= end)
        .order_by(EuaAuction.auction_date, EuaAuction.auction_name)
    )).scalars())


async def _official_cbam_in(session: AsyncSession, start: date, end: date) -> Optional[float]:
    """Giá CBAM EC công bố trong khoảng ngày (giá quý Q công bố đầu quý Q+1 — save_cbam_price lưu
    price_date = ngày công bố)."""
    return (await session.execute(
        select(Price.close_price)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Instrument.code == "CBAM", Price.price_date >= start.isoformat(), Price.price_date <= end.isoformat())
        .order_by(Price.price_date.desc())
        .limit(1)
    )).scalar_one_or_none()


async def build_cbam_estimate(
    session: AsyncSession, as_of_str: str, spot: Optional[float], closes: Sequence[float],
) -> Optional[Dict[str, Any]]:
    """Khối "ƯỚC TÍNH GIÁ CBAM" cho báo cáo có ngày dữ liệu `as_of_str`. None nếu thiếu giá EUA hoặc
    chưa có lịch/kết quả đấu giá nào của quý (chưa chạy crawl_eex_auctions)."""
    if not spot:
        return None
    as_of = date.fromisoformat(as_of_str)
    year, q = quarter_of(as_of)
    q_start, q_end = quarter_bounds(year, q)
    rows = await _auctions_between(session, q_start, q_end)
    done = [(a.auction_date, a.auction_price, a.volume) for a in rows
            if a.status == "successful" and a.auction_price and a.auction_date <= as_of]
    # Phiên sau ngày dữ liệu tính là "còn lại" kể cả khi đã có kết quả (crawl muộn) — nhất quán với
    # giá EUA của báo cáo; phiên bị huỷ không cộng khối lượng (EEX dồn sang phiên khác trong lịch).
    remaining = [(a.auction_date, a.volume) for a in rows
                 if a.auction_date > as_of and a.status != "cancelled"]
    if not done and not remaining:
        return None

    out: Dict[str, Any] = {
        "quarter": quarter_label(year, q),
        "as_of": as_of_str,
        **estimate_quarter(done, remaining, float(spot), daily_log_vol(closes), as_of),
    }

    last = next((a for a in reversed(rows) if a.status == "successful" and a.auction_date <= as_of), None)
    out["last_auction"] = {
        "date": last.auction_date.isoformat(), "platform": last.auction_name, "price": last.auction_price,
        "cover_ratio": last.cover_ratio,
    } if last else None

    upcoming_end = as_of + timedelta(days=UPCOMING_DAYS)
    nxt = [a for a in await _auctions_between(session, as_of + timedelta(days=1), upcoming_end) if a.status != "cancelled"]
    out["upcoming"] = {
        "count": len(nxt),
        "volume_mt": round(sum(a.volume for a in nxt) / 1e6, 2),
        "first_date": nxt[0].auction_date.isoformat() if nxt else None,
        "last_date": nxt[-1].auction_date.isoformat() if nxt else None,
    }

    # Quý trước: VWAP tự tính vs giá EC đã công bố (công bố trong quý hiện tại) — kiểm chứng phương pháp.
    py, pq = (year, q - 1) if q > 1 else (year - 1, 4)
    p_start, p_end = quarter_bounds(py, pq)
    prev_rows = [a for a in await _auctions_between(session, p_start, p_end) if a.status == "successful" and a.auction_price]
    prev_vwap = vwap([(a.auction_price, a.volume) for a in prev_rows])
    out["prev_quarter"] = {
        "quarter": quarter_label(py, pq),
        "vwap": round(prev_vwap, 2) if prev_vwap is not None else None,
        "official": await _official_cbam_in(session, q_start, as_of),
    } if prev_vwap is not None else None
    return out


def describe_for_prompt(est: Optional[Dict[str, Any]]) -> str:
    """Tóm tắt 1 khối cho prompt LLM (Mục 4/biz) — để nhắc tới giá CBAM thì dùng đúng số này."""
    if not est:
        return "ƯỚC TÍNH GIÁ CBAM: chưa có dữ liệu đấu giá EEX của quý — KHÔNG tự ước tính giá CBAM."

    def n(x: Optional[float]) -> str:
        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if x is not None else "?"

    signal_vi = {"increase": "TĂNG phòng vệ", "hold": "GIỮ", "reduce": "GIẢM/giãn tiến độ phòng vệ"}[est["signal"]]
    lines = [
        f"ƯỚC TÍNH GIÁ CBAM {est['quarter']} (tính sẵn bằng Python — dùng ĐÚNG số này, KHÔNG tự tính lại; "
        "báo cáo đã hiển thị khối này riêng nên KHÔNG chép lại toàn bộ, chỉ dẫn số khi cần):",
        f"- Phương pháp EC: VWAP giá khớp mọi phiên đấu giá sơ cấp EUA trên EEX trong quý (KHÔNG phải giá đóng cửa futures).",
        f"- Đã chốt {est['auctions_done']}/{est['auctions_total']} phiên ({n(est['fixed_share_pct'])}% khối lượng), "
        f"VWAP đã chốt {n(est['qtd_vwap'])} EUR/t; giá EUA hiện tại {n(est['spot'])} EUR/t.",
        f"- Ước tính giá CBAM {est['quarter']}: {n(est['estimate'])} EUR/t"
        + (f", khoảng tin cậy {est['ci_level']}%: {n(est['ci_low'])}–{n(est['ci_high'])}" if est.get("ci_low") is not None else "")
        + f". Tín hiệu vị thế phòng vệ CBAM: {signal_vi}.",
    ]
    prev = est.get("prev_quarter")
    if prev and prev.get("official") is not None:
        lines.append(f"- {prev['quarter']}: VWAP đấu giá {n(prev['vwap'])} ↔ EC công bố {n(prev['official'])} EUR/t.")
    return "\n".join(lines)


async def quarter_to_date_auction_vwap(session: AsyncSession, as_of_str: str) -> Tuple[Optional[float], int]:
    """VWAP đấu giá EEX từ đầu quý tới hết `as_of_str` + số phiên; (None, 0) nếu chưa có phiên nào."""
    as_of = date.fromisoformat(as_of_str)
    q_start, _ = quarter_bounds(*quarter_of(as_of))
    rows = [a for a in await _auctions_between(session, q_start, as_of) if a.status == "successful" and a.auction_price]
    avg = vwap([(a.auction_price, a.volume) for a in rows])
    return (round(avg, 2) if avg is not None else None), len(rows)
