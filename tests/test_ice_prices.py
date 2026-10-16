"""Giá API2 lấy từ ICE (crawl_prices/crawl_ice.py) + chặn dữ liệu chết của nguồn giá."""
from datetime import date, datetime, timezone

import pytest

from crawl_prices import crawl_ice as ice
from crawl_prices.crawl_barchart import _stale_reason
from services import report_generator as rg


def _ms(d: str) -> int:
    return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_parse_symbol():
    assert ice.parse_ice_symbol("ICE:517:681:243") == ice.IceSymbol("517", "681", "243")
    assert ice.parse_ice_symbol("ice:517:681") == ice.IceSymbol("517", "681", None)
    for bad in ("ICE:517", "ICE:a:b", "ITF*1", "ICE:1:2:3:4"):
        with pytest.raises(ValueError):
            ice.parse_ice_symbol(bad)


def test_product_url():
    assert ice.ice_product_url("ICE:517:681:243") == "https://www.ice.com/products/243"
    assert ice.ice_product_url("ICE:517:681") is None
    assert rg._barchart_url("ICE:517:681:243") == "https://www.ice.com/products/243"
    assert rg._barchart_url("CK*0") == "https://www.barchart.com/futures/quotes/CK%2A0/overview"


# Danh sách thật ICE trả về 16/10/2026: chỉ là tập con, sau Nov26 nhảy sang 2028
CONTRACTS = [
    {"marketId": 1, "marketStrip": "Nov26", "endDate": _ms("2026-11-30")},
    {"marketId": 2, "marketStrip": "Jan28", "endDate": _ms("2028-01-31")},
    {"marketId": 3, "marketStrip": "Cal 28", "endDate": _ms("2028-12-31")},
]


def test_pick_front_month():
    assert ice.pick_front_month(CONTRACTS, date(2026, 10, 15))["marketStrip"] == "Nov26"


def test_pick_front_month_refuses_far_contract():
    # Nov26 hết hạn → chỉ còn Jan28: báo lỗi thay vì lặng lẽ dùng hợp đồng 2028
    with pytest.raises(ValueError, match="quá xa"):
        ice.pick_front_month(CONTRACTS, date(2026, 12, 2))


def test_parse_bars_changes_and_target_cutoff():
    bars = [
        ["Thu Oct 08 00:00:00 2026", 140.0],
        ["Fri Oct 09 00:00:00 2026", 142.0],
        ["Mon Oct 12 00:00:00 2026", 148.0],
        ["Thu Oct 15 00:00:00 2026", 149.2],
        ["Fri Oct 16 00:00:00 2026", 150.0],  # sau ngày dữ liệu → bỏ
        ["Sat Oct 17 00:00:00 2026", None],
    ]
    out = ice.parse_historical_bars(bars, "API2", date(2026, 10, 15), max_results=30)
    assert [r["price_date"] for r in out] == ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-15"]
    last = out[-1]
    assert last["close_price"] == 149.2
    assert last["day_change_pct"] == pytest.approx(0.811, abs=1e-3)   # vs 148,0
    assert last["week_change_pct"] == pytest.approx(6.571, abs=1e-3)  # vs 140,0 (08/10)
    assert last["open_price"] is None and last["volume"] is None
    assert out[0]["day_change_pct"] is None


def test_parse_bars_max_results_keeps_latest():
    bars = [[f"Thu Oct {d:02d} 00:00:00 2026", 100.0 + d] for d in range(1, 16)]
    out = ice.parse_historical_bars(bars, "API2", date(2026, 10, 15), max_results=3)
    assert [r["price_date"] for r in out] == ["2026-10-13", "2026-10-14", "2026-10-15"]
    assert out[0]["day_change_pct"] is not None  # vẫn tính Δ từ phiên trước đó


def test_parse_bars_empty_raises():
    with pytest.raises(ValueError):
        ice.parse_historical_bars([["Fri Oct 16 00:00:00 2026", 150.0]], "API2", date(2026, 10, 15), 30)


def test_stale_reason():
    target = date(2026, 10, 15)
    assert _stale_reason([], target) is None
    assert _stale_reason([{"price_date": "2026-10-12"}], target) is None  # qua cuối tuần
    # Barchart ITF*1: trả 200 nhưng dữ liệu dừng ở 2023
    assert "ngừng cập nhật" in _stale_reason([{"price_date": "2023-08-18"}], target)
