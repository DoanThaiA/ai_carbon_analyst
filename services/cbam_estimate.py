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

Phòng vệ CBAM — 1 nhãn DUY NHẤT cho cả báo cáo (thẻ CBAM + ô phòng hộ Mục 3 dùng chung, report_generator.
_finalize_hedge_fields). RỦI RO cần phòng: chi phí CBAM của quý = VWAP các phiên đấu giá → phần khối lượng CHƯA chốt giá
thả nổi theo VWAP các phiên còn lại. Không làm gì thì chi phí tự bằng VWAP (không có rủi ro "lệch VWAP", chỉ có rủi ro
GIÁ). Mua EUA rải theo lịch đấu giá KHÔNG phòng được gì — chỉ tạo thêm 1 kho EUA giá vốn ≈ VWAP, không nộp thay chứng chỉ
CBAM và vẫn chịu rủi ro giá tới khi bán (bản 17/10 khuyến nghị đúng cách đó). Cách KHÓA chi phí: MUA futures EUA
(Dec của năm) NGAY cho phần muốn khóa, rồi BÁN rải theo tỷ trọng lịch đấu giá còn lại → lãi/lỗ futures ≈ VWAP − giá mua,
chi phí ròng ≈ giá mua hôm nay (sai số: chênh spot–Dec, lệch tỷ trọng). Phần LÕI (≥ 100 − TACTICAL_MAX_PCT % phần chưa
chốt) khóa theo kế hoạch; chỉ phần CHIẾN THUẬT (≤ TACTICAL_MAX_PCT %) là quan điểm giá — "signal" là hướng của riêng phần này:
  - quý đã chốt ≥ HEDGE_FIXED_SHARE_HOLD khối lượng / chưa có phiên → "hold" (chỉ phần lõi);
  - giá hiện tại thấp hơn VWAP đã chốt ≥ HEDGE_GAP_PCT → "increase" (khóa thêm phần chiến thuật ngay);
  - cao hơn ≥ HEDGE_GAP_PCT → "reduce" (phần chiến thuật chưa khóa, chờ giá thấp hơn — phần lõi vẫn khóa);
  - còn lại → "hold".
Chi phí theo TẤN SẢN PHẨM (product_exposure): (phát thải nhúng − benchmark × hệ số phân bổ miễn phí còn lại của năm) ×
giá CBAM ước tính — để BLĐ thấy khoản phòng hộ có đáng làm không (2026: hệ số 97,5%).
Bản 17/10/2026 từng có thẻ CBAM "GIỮ" + Mục 3 "MUA TRẢI ĐỀU" nhưng thực chất là lệnh giới hạn "chỉ gom ở 82,35–84,00"
→ thiếu phòng hộ nếu giá không về vùng đó trong khi 77,1% khối lượng quý chưa chốt.
Kết quả là DỮ LIỆU THUẦN (số + enum ASCII) — frontend tự dựng nhãn/câu theo ngôn ngữ, nên bản dịch
máy (services/report_translation.py) không chạm vào số.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import logging
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import EuaAuction, Instrument, Price

logger = logging.getLogger(__name__)

HEDGE_FIXED_SHARE_HOLD = 0.85
TACTICAL_MAX_PCT = 20  # tối đa phần phòng vệ dành cho quan điểm giá (lệnh giới hạn/mua sớm); phần còn lại bám lịch đấu giá
HEDGE_GAP_PCT = 2.0
CI_LEVEL = 90
CI_Z = 1.645
MIN_RETURNS_FOR_VOL = 10
UPCOMING_DAYS = 7

# Tỷ lệ phân bổ miễn phí CÒN LẠI theo năm cho ngành CBAM (Chỉ thị (EU) 2023/959, Điều 10a(1a)) — nghĩa vụ CBAM được
# khấu trừ phần phân bổ miễn phí này.
CBAM_FREE_ALLOCATION_FACTOR = {
    2026: 0.975, 2027: 0.95, 2028: 0.90, 2029: 0.775, 2030: 0.515, 2031: 0.39, 2032: 0.265, 2033: 0.14, 2034: 0.0,
}
# Tham số sản phẩm (benchmark CBAM theo mã CN, CSCF, markup giá trị mặc định, ví dụ giá trị mặc định theo xuất xứ) nằm ở
# cbam_products.yaml — admin cập nhật. Bản 17/10 dùng nhầm benchmark ETS 2021–2025 (nhôm 1,464; gang lỏng 1,288) thay vì
# benchmark CBAM theo mã CN (Quy định thực thi (EU) 2025/2620). Không đọc được file → dùng CBAM_PRODUCT_FALLBACK.
CBAM_PRODUCTS_PATH = Path(__file__).resolve().parent.parent / "cbam_products.yaml"
CBAM_PRODUCT_FALLBACK: Dict[str, Any] = {
    "cscf": {2026: 1.0},
    "default_markup": {2026: 0.10, 2027: 0.20, 2028: 0.30},
    "benchmark_source": "Benchmark CBAM theo mã CN — Quy định thực thi (EU) 2025/2620",
    "benchmark_verified": False,
    "products": [
        {"key": "aluminium_primary", "cn": "7601", "embedded": 1.70, "benchmark": 1.423},
        {"key": "steel_bf_bof", "cn": "7208", "embedded": 1.80, "benchmark": 1.370},
    ],
    "default_examples": [],
}


def load_cbam_products(path: Path = CBAM_PRODUCTS_PATH) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("Không đọc được %s — dùng tham số CBAM sản phẩm mặc định trong code.", path, exc_info=True)
        return CBAM_PRODUCT_FALLBACK
    return {**CBAM_PRODUCT_FALLBACK, **{k: v for k, v in data.items() if v is not None}}


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
        "tactical_max_pct": TACTICAL_MAX_PCT,
        # Lõi tính theo % KHỐI LƯỢNG QUÝ (không phải % phần chưa chốt) — "≥80% phần chưa chốt — 77,1% khối lượng" từng khó hiểu.
        "open_share_pct": round((1 - fixed_share) * 100, 1),
        "core_share_of_quarter_pct": round((1 - fixed_share) * (100 - TACTICAL_MAX_PCT), 1),
        "tactical_share_of_quarter_pct": round((1 - fixed_share) * TACTICAL_MAX_PCT, 1),
    }


def product_exposure(
    estimate: Optional[float], ci_low: Optional[float], ci_high: Optional[float], year: int,
    params: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Chi phí CBAM / tấn sản phẩm ở giá CBAM ước tính (và 2 đầu khoảng tin cậy): khấu trừ = hệ số CBAM × CSCF × benchmark
    CBAM theo mã CN. Kèm kịch bản dùng GIÁ TRỊ MẶC ĐỊNH theo xuất xứ (× (1 + markup của năm)) — chênh 2–6 lần so với số thực
    đã thẩm định là giá trị của việc thẩm định phát thải."""
    params = params or load_cbam_products()
    factor = CBAM_FREE_ALLOCATION_FACTOR.get(year)
    if estimate is None or factor is None:
        return []
    cscf = float((params.get("cscf") or {}).get(year, 1.0))
    markup = float((params.get("default_markup") or {}).get(year, max((params.get("default_markup") or {0: 0}).values())))
    out = []
    for p in params.get("products") or []:
        deduction = factor * cscf * float(p["benchmark"])
        net = max(0.0, float(p["embedded"]) - deduction)
        defaults = []
        for d in params.get("default_examples") or []:
            if d.get("key") != p["key"] or not isinstance(d.get("default_value"), (int, float)):
                continue
            charged = max(0.0, d["default_value"] * (1 + markup) - deduction)
            defaults.append({
                "origin": d.get("origin"), "default_value": d["default_value"], "with_markup": round(d["default_value"] * (1 + markup), 3),
                "net_tco2_per_t": round(charged, 3), "cost_eur_per_t": round(charged * estimate, 2),
            })
        out.append({
            "key": p["key"], "cn": str(p["cn"]), "route": p.get("route"), "embedded": p["embedded"], "benchmark": p["benchmark"],
            "free_allocation_factor_pct": round(factor * 100, 1),
            "cscf_pct": round(cscf * 100, 1),
            "deduction_tco2_per_t": round(deduction, 3),
            "default_markup_pct": round(markup * 100),
            "defaults": defaults,
            "benchmark_source": params.get("benchmark_source"),
            "benchmark_verified": bool(params.get("benchmark_verified")),
            "net_tco2_per_t": round(net, 3),
            "cost_eur_per_t": round(net * estimate, 2),
            "cost_low": round(net * ci_low, 2) if ci_low is not None else None,
            "cost_high": round(net * ci_high, 2) if ci_high is not None else None,
            # Độ nhạy: mỗi +0,1 tCO2/t phát thải nhúng (vd dùng giá trị mặc định thay số thực đã thẩm định) → +EUR/t.
            "per_0_1_tco2_eur": round(0.1 * estimate, 2),
            "assumed": True,
        })
    return out


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
    out["last_auction_date"] = max(d for d, _ in remaining).isoformat() if remaining else None
    out["product_exposure"] = product_exposure(out["estimate"], out.get("ci_low"), out.get("ci_high"), year)

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


HEDGE_LABEL_VI = {
    "increase": "KHÓA LÕI BẰNG FUTURES + khóa thêm phần chiến thuật",
    "hold": "KHÓA LÕI BẰNG FUTURES",
    "reduce": "KHÓA LÕI BẰNG FUTURES, phần chiến thuật chưa khóa",
}


def _n(x: Optional[float], d: int = 2) -> str:
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".") if x is not None else "?"


def hedge_structure_text(est: Dict[str, Any]) -> str:
    """Câu mô tả rủi ro + cấu trúc khóa chi phí (dùng chung prompt, ô phòng hộ Mục 3, tool chat)."""
    up = est.get("upcoming") or {}
    nxt = f" (7 ngày tới {up['count']} phiên / {_n(up.get('volume_mt'))} triệu t)" if up.get("count") else ""
    quarter = est.get("quarter", "quý")
    year = str(quarter).split(" ")[-1][-2:] if str(quarter).split(" ")[-1].isdigit() else ""
    contract = f"EUA futures Dec-{year} (ICE Endex)" if year else "EUA futures Dec (ICE Endex)"
    last = est.get("last_auction_date")
    expiry_note = (
        f" Phiên đấu giá cuối quý: {last[8:10]}/{last[5:7]} — phần bán rải rơi SAU ngày đáo hạn {contract.split(' ')[2]} (nếu có) chuyển "
        "sang hợp đồng Mar kế tiếp; kiểm lịch đáo hạn ICE trước khi đặt lệnh." if last else ""
    )
    # % tính trên NGHĨA VỤ CBAM của quý (tCO2) — tỷ trọng chưa chốt = tỷ trọng khối lượng đấu giá còn lại (trọng số VWAP);
    # ghi "% khối lượng quý" dễ bị hiểu là khối lượng đấu giá.
    return (
        f"Rủi ro: {_n(est.get('open_share_pct'), 1)}% nghĩa vụ CBAM {quarter} của SIM (tCO2) chưa chốt giá (tỷ trọng theo khối lượng "
        f"đấu giá còn lại) — chi phí phần này thả nổi theo VWAP các phiên còn lại (ước tính {_n(est.get('estimate'))} EUR/t"
        + (f", KTC {est.get('ci_level')}%: {_n(est.get('ci_low'))}–{_n(est.get('ci_high'))}" if est.get("ci_low") is not None else "")
        + f"). Khóa chi phí: MUA {contract} ngay cho phần lõi = {_n(est.get('core_share_of_quarter_pct'), 1)}% nghĩa vụ CBAM "
        f"quý ({100 - int(est.get('tactical_max_pct') or TACTICAL_MAX_PCT)}% phần chưa chốt), rồi BÁN rải theo tỷ trọng lịch đấu "
        f"giá còn lại{nxt} → lãi/lỗ futures ≈ VWAP − giá mua, chi phí ròng ≈ giá mua hôm nay. Mua EUA rải theo lịch KHÔNG "
        "khóa được gì (chỉ tạo kho EUA giá vốn ≈ VWAP, không nộp thay chứng chỉ CBAM)." + expiry_note
    )


PRODUCT_NAMES_VI = {"aluminium_primary": "nhôm thỏi nguyên sinh", "steel_bf_bof": "thép lò cao"}


def describe_product_exposure(est: Dict[str, Any]) -> str:
    rows = est.get("product_exposure") or []
    if not rows:
        return ""
    r0 = rows[0]
    parts = []
    for r in rows:
        if "deduction_tco2_per_t" in r:  # công thức đầy đủ (báo cáo từ 18/10/2026)
            formula = (f"{_n(r['embedded'])} − {_n(r['free_allocation_factor_pct'], 1)}% × CSCF {_n(r['cscf_pct'], 0)}% × "
                       f"{_n(r['benchmark'], 3)} = {_n(r['net_tco2_per_t'], 3)} tCO2/t")
        else:
            formula = f"{_n(r['embedded'])} − {_n(r['benchmark'], 3)} × {_n(r['free_allocation_factor_pct'], 1)}% = {_n(r['net_tco2_per_t'], 3)} tCO2/t"
        text = (f"{PRODUCT_NAMES_VI.get(r['key'], r['key'])} (CN {r['cn']}{' ' + r['route'] if r.get('route') else ''}): {formula} → "
                f"≈ {_n(r['cost_eur_per_t'])} EUR/t" + (f" (KTC {_n(r['cost_low'])}–{_n(r['cost_high'])})" if r.get("cost_low") is not None else ""))
        if r.get("defaults"):
            text += "; nếu dùng GIÁ TRỊ MẶC ĐỊNH (+" + f"{r.get('default_markup_pct')}% markup): " + ", ".join(
                f"{d['origin']} {_n(d['with_markup'], 3)} → ≈ {_n(d['cost_eur_per_t'])} EUR/t" for d in r["defaults"])
        parts.append(text)
    src = r0.get("benchmark_source") or "benchmark CBAM theo mã CN"
    return (
        "- Chi phí CBAM / tấn sản phẩm (phát thải nộp là GIẢ ĐỊNH — thay bằng số thực đã thẩm định): " + "; ".join(parts)
        + f". Nguồn benchmark: {src}" + ("" if r0.get("benchmark_verified") else " (số theo nguồn thứ cấp — CẦN ĐỐI CHIẾU Phụ lục văn bản gốc)")
        + f". Mỗi +0,1 tCO2/t phát thải nộp ≈ +{_n(r0.get('per_0_1_tco2_eur'))} EUR/t sản phẩm; chênh lệch số thực ↔ giá trị mặc định là "
        "luận điểm chính cho chương trình thẩm định phát thải. Giá trị mặc định cho xuất xứ Việt Nam: chưa có trong dữ liệu — KHÔNG tự đoán."
    )


def describe_for_prompt(est: Optional[Dict[str, Any]]) -> str:
    """Tóm tắt 1 khối cho prompt LLM (Mục 4/biz) — để nhắc tới giá CBAM thì dùng đúng số này."""
    if not est:
        return "ƯỚC TÍNH GIÁ CBAM: chưa có dữ liệu đấu giá EEX của quý — KHÔNG tự ước tính giá CBAM."

    def n(x: Optional[float]) -> str:
        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if x is not None else "?"

    signal_vi = HEDGE_LABEL_VI[est["signal"]]
    lines = [
        f"ƯỚC TÍNH GIÁ CBAM {est['quarter']} (tính sẵn bằng Python — dùng ĐÚNG số này, KHÔNG tự tính lại; "
        "báo cáo đã hiển thị khối này riêng nên KHÔNG chép lại toàn bộ, chỉ dẫn số khi cần):",
        f"- Phương pháp EC: VWAP giá khớp mọi phiên đấu giá sơ cấp EUA trên EEX trong quý (KHÔNG phải giá đóng cửa futures).",
        f"- Đã chốt {est['auctions_done']}/{est['auctions_total']} phiên ({n(est['fixed_share_pct'])}% khối lượng), "
        f"VWAP đã chốt {n(est['qtd_vwap'])} EUR/t; giá EUA hiện tại {n(est['spot'])} EUR/t.",
        f"- Ước tính giá CBAM {est['quarter']}: {n(est['estimate'])} EUR/t"
        + (f", khoảng tin cậy {est['ci_level']}%: {n(est['ci_low'])}–{n(est['ci_high'])}" if est.get("ci_low") is not None else "")
        + ".",
        f"- PHÒNG VỆ CBAM (nhãn DUY NHẤT của báo cáo, Python quyết định — KHÔNG tự đặt nhãn khác): {signal_vi}. "
        + hedge_structure_text(est)
        + f" Lệnh giới hạn/vùng giá CHỈ áp cho phần chiến thuật ≤{est.get('tactical_max_pct', TACTICAL_MAX_PCT)}% phần chưa chốt "
        f"(= {_n(est.get('tactical_share_of_quarter_pct'), 1)}% nghĩa vụ CBAM quý) và phải ghi là quan điểm giao dịch. Khi nêu % PHẢI "
        "ghi rõ \"% nghĩa vụ CBAM quý\" hay \"% phần chưa chốt\" (KHÔNG viết \"% khối lượng quý\" — dễ hiểu nhầm là khối lượng đấu giá).",
    ]
    if describe_product_exposure(est):
        lines.append(describe_product_exposure(est))
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
