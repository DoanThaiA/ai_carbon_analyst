"""
crawl_ice.py
============
Lấy giá chốt theo ngày trực tiếp từ dữ liệu trễ công khai của ICE (ice.com) — dùng cho
hợp đồng Barchart không còn cập nhật. Hiện là API2 Rotterdam Coal: mọi kỳ hạn ITF* trên
Barchart dừng ở phiên 2023-08-18 dù trang vẫn trả HTTP 200.

Khai báo trong bảng price_crawl_sources (admin /admin/price-sources) bằng symbol dạng
    ICE:<productId>:<hubId>[:<pageId>]
vd API2 = "ICE:517:681:243":
  - productId/hubId: tham số API charting của ICE (xem tab Data trên trang sản phẩm,
    request contract-data?productId=...&hubId=...).
  - pageId (tuỳ chọn): số trong URL trang sản phẩm ice.com/products/<pageId>/... — KHÁC
    productId (API2: trang 243, productId 517); chỉ dùng dựng link cho người đọc bấm xem.

API không cần cookie/trình duyệt (httpx thường là đủ):
  - charting/contract-data → danh sách kỳ hạn đang vẽ biểu đồ (marketId, marketStrip, endDate).
    Danh sách chỉ là TẬP CON (API2: Nov26 rồi nhảy sang 2028) nên chọn kỳ hạn tháng gần nhất
    và báo lỗi nếu kỳ đó quá xa thay vì lặng lẽ dùng hợp đồng 2 năm sau.
  - charting/data/historical?marketId=..&historicalSpan=1 → ~3 tháng giá chốt ngày
    [["Thu Oct 15 00:00:00 2026", 149.2], ...]. Chỉ có giá chốt — không có OHLC/khối lượng.

Δ ngày/Δ tuần tính trong CÙNG 1 hợp đồng (không ghép qua kỳ hạn khi roll).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ICE_SYMBOL_PREFIX = "ICE:"
ICE_CHARTING_BASE = "https://www.ice.com/marketdata/api/productguide/charting"
ICE_PRODUCT_URL = "https://www.ice.com/products/{page_id}"
ICE_HTTP_TIMEOUT = 30.0
# Kỳ hạn tháng gần nhất kết thúc sau ngày dữ liệu quá số ngày này → coi như danh sách
# kỳ hạn của ICE đang thiếu front month (vd chỉ còn hợp đồng 2028) → báo lỗi.
ICE_MAX_FRONT_MONTH_DAYS = 70
MIN_VALID_PRICE = 0.01

_MONTH_STRIP_RE = re.compile(r"^[A-Z][a-z]{2}\d{2}$")  # "Nov26" — bỏ "Cal 28", "Q1 27"...
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class IceSymbol:
    product_id: str
    hub_id: str
    page_id: Optional[str] = None


def is_ice_symbol(symbol: str) -> bool:
    return (symbol or "").upper().startswith(ICE_SYMBOL_PREFIX)


def parse_ice_symbol(symbol: str) -> IceSymbol:
    """'ICE:517:681:243' → IceSymbol('517', '681', '243'); pageId tuỳ chọn."""
    parts = symbol[len(ICE_SYMBOL_PREFIX):].split(":") if is_ice_symbol(symbol) else []
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(
            f"Symbol ICE sai định dạng: {symbol!r} — cần ICE:<productId>:<hubId>[:<pageId>], vd ICE:517:681:243"
        )
    return IceSymbol(parts[0], parts[1], parts[2] if len(parts) == 3 else None)


def ice_product_url(symbol: str) -> Optional[str]:
    """Link trang sản phẩm ICE cho người đọc bấm xem; None nếu symbol không khai báo pageId."""
    try:
        page_id = parse_ice_symbol(symbol).page_id
    except ValueError:
        return None
    return ICE_PRODUCT_URL.format(page_id=page_id) if page_id else None


def pick_front_month(contracts: list[dict], target_date: date) -> dict:
    """Kỳ hạn THÁNG có endDate gần nhất mà chưa hết hạn tại target_date."""
    candidates = []
    for c in contracts:
        if not _MONTH_STRIP_RE.match(str(c.get("marketStrip") or "")) or c.get("endDate") is None:
            continue
        end = datetime.fromtimestamp(c["endDate"] / 1000, tz=timezone.utc).date()
        if end >= target_date:
            candidates.append((end, c))
    if not candidates:
        raise ValueError("ICE không trả kỳ hạn tháng nào còn hiệu lực")
    end, front = min(candidates, key=lambda x: x[0])
    if (end - target_date).days > ICE_MAX_FRONT_MONTH_DAYS:
        raise ValueError(
            f"Kỳ hạn tháng gần nhất ICE trả về là {front.get('marketStrip')} (hết hạn {end}) — quá xa "
            f"ngày dữ liệu {target_date}, danh sách kỳ hạn đang thiếu front month"
        )
    return front


def _bar_date(raw: str) -> date:
    # "Thu Oct 15 00:00:00 2026"
    return datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").date()


def parse_historical_bars(
    bars: list, instrument_code: str, target_date: date, max_results: int
) -> list[dict]:
    """bars [[ngày, giá chốt], ...] → list dict cùng định dạng crawl_barchart._parse_historical_json
    (open/high/low/volume = None vì ICE chỉ công khai giá chốt)."""
    rows: list[tuple[date, float]] = []
    for bar in bars or []:
        if not isinstance(bar, (list, tuple)) or len(bar) < 2 or bar[1] is None:
            continue
        d = _bar_date(bar[0])
        close = float(bar[1])
        if d <= target_date and close >= MIN_VALID_PRICE:
            rows.append((d, close))
    if not rows:
        raise ValueError(f"ICE không có phiên nào <= {target_date} cho {instrument_code}")
    rows.sort(key=lambda r: r[0])

    results = []
    output_from = max(0, len(rows) - max_results)
    for i, (d, close) in enumerate(rows):
        if i < output_from:
            continue
        day_change_pct = round((close - rows[i - 1][1]) / rows[i - 1][1] * 100, 3) if i >= 1 else None
        week_change_pct = None
        for prev_d, prev_close in reversed(rows[:i]):
            if (d - prev_d).days >= 7:
                week_change_pct = round((close - prev_close) / prev_close * 100, 3)
                break
        results.append({
            "instrument_code": instrument_code,
            "price_date":      d.isoformat(),
            "open_price":      None,
            "high_price":      None,
            "low_price":       None,
            "close_price":     close,
            "volume":          None,
            "day_change_pct":  day_change_pct,
            "week_change_pct": week_change_pct,
        })
    return results


async def fetch_ice_prices(
    client: httpx.AsyncClient, symbol: str, instrument_code: str, target_date: date, max_results: int
) -> list[dict]:
    """Chọn kỳ hạn tháng gần nhất rồi lấy ~3 tháng giá chốt ngày của kỳ đó."""
    sym = parse_ice_symbol(symbol)
    resp = await client.get(
        f"{ICE_CHARTING_BASE}/contract-data",
        params={"productId": sym.product_id, "hubId": sym.hub_id, "mode": 2},
    )
    resp.raise_for_status()
    front = pick_front_month(resp.json(), target_date)

    resp = await client.get(
        f"{ICE_CHARTING_BASE}/data/historical",
        params={"marketId": front["marketId"], "historicalSpan": 1},
    )
    resp.raise_for_status()
    results = parse_historical_bars(resp.json().get("bars"), instrument_code, target_date, max_results)
    logger.debug("ICE %s: kỳ hạn %s (marketId %s), %d phiên", symbol, front.get("marketStrip"), front["marketId"], len(results))
    return results


def new_ice_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=ICE_HTTP_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True)
