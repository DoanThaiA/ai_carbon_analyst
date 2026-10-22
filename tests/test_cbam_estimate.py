"""Đấu giá EEX (crawl_prices/crawl_eex_auctions.py) + ước tính giá CBAM quý (services/cbam_estimate.py)."""
import math
from datetime import date, datetime

import pytest

from crawl_prices import crawl_eex_auctions as eex
from services import cbam_estimate as ce


# ── Parser file EEX ────────────────────────────────────────────────────────────

def test_normalize_platform():
    assert eex.normalize_platform("Auction 4. Period CAP3 EU") == "EU"
    assert eex.normalize_platform("Auction 4. Period DE") == "DE"
    assert eex.normalize_platform("Spot Market - Polish Primary Auction CAP3 - EUA") == "PL"
    assert eex.normalize_platform("Spot Market - Northern Irish Primary Auction CAP3 - EUA") == "NIR"
    assert eex.normalize_platform("Spot Market - German Primary Auction - EUA") == "DE"
    assert eex.normalize_platform("Spot Market - EU Primary Auction CAP4 - EUA") == "EU"
    assert eex.normalize_platform("Spot Market - EU Primary Auction CAP3 - EUAA") is None
    assert eex.normalize_platform("ETS2 Primary Auction") is None


RESULT_HEADER = (None, "Date", "Time", "Auction Name", "Contract", "Status", "Auction Price €/tCO2",
                 "Minimum Bid €/tCO2", "Maximum Bid €/tCO2", "Mean €/tCO2", "Median €/tCO2", "Auction Volume tCO2",
                 "Total Amount of Bids", "Cover Ratio", "Total Number of Bidders")


def _res(d, name, status, price, vol, cover=1.7, bidders=27):
    return (None, datetime.fromisoformat(d), None, name, "T3PA", status, price, 10.0, 90.0, 83.3, 85.2, vol,
            4740000.0, cover, bidders)


def test_parse_results_rows():
    rows = [
        (None, "References", None, None, None, None, "Prices"),
        RESULT_HEADER,
        _res("2026-10-15", "Auction 4. Period CAP3 EU", "successful", 85.84, 2791500.0),
        _res("2026-10-14", "Auction 4. Period CAP3 PL", "successful", 87.02, 1945000.0, 1.78, 26),
        _res("2026-10-09", "Auction 4. Period CAP3 EU", "cancelled", None, 2791500.0, None, None),
        (None, None, None, None),
    ]
    out = eex.parse_results_rows(rows)
    assert len(out) == 3
    assert out[0] == {
        "auction_date": date(2026, 10, 15), "auction_name": "EU", "contract": "T3PA", "status": "successful",
        "volume": 2791500.0, "auction_price": 85.84, "cover_ratio": 1.7, "total_bidders": 27,
    }
    assert out[1]["auction_name"] == "PL"
    assert out[2]["status"] == "cancelled" and out[2]["auction_price"] is None


def test_parse_results_requires_header():
    with pytest.raises(ValueError):
        eex.parse_results_rows([(None, "foo")])


def test_parse_calendar_rows_assigns_following_date():
    # Bố cục thật của sheet "Calendar": dòng phiên nằm NGAY TRƯỚC dòng ngày
    rows = [
        ("09.00 am - 11.00 am CET", None, "Spot Market - EU Primary Auction CAP3 - EUA"),
        ("Week 42", "Call Trading Period", "Product Code", "Trading Period", "Volume", None, "Auction Name"),
        ("Monday", "09.00 am - 11.00 am", "T3PA", "4th Period", 2791500, None, "Spot Market - EU Primary Auction CAP3 - EUA"),
        (datetime(2026, 10, 12),),
        ("Tuesday", "09.00 am - 11.00 am", "T3PA", "4th Period", 2791500, None, "Spot Market - EU Primary Auction CAP3 - EUA"),
        (datetime(2026, 10, 13),),
        ("Wednesday", "09.00 am - 11.00 am", "T3PA", "4th Period", 1945000, None, "Spot Market - Polish Primary Auction CAP3 - EUA"),
        (datetime(2026, 10, 14),),
        ("Thursday", None, None, None, None, None, None),  # ngày nghỉ — không có phiên
        (datetime(2026, 10, 15),),
        ("Monday", "09.00 am - 11.00 am", "T3PA", "4th Period", 2791500, None, "Spot Market - EU Primary Auction CAP3 - EUA"),
        (datetime(2026, 10, 16),),  # lệch thứ (16/10 là Thứ Sáu) → bỏ, không gán nhầm
    ]
    out = eex.parse_calendar_rows(rows)
    assert [(r["auction_date"], r["auction_name"], r["volume"]) for r in out] == [
        (date(2026, 10, 12), "EU", 2791500),
        (date(2026, 10, 13), "EU", 2791500),
        (date(2026, 10, 14), "PL", 1945000),
    ]
    assert all(r["status"] == "scheduled" for r in out)


def test_calendar_links_skips_history_and_ets2():
    html = (
        '<a href="/fileadmin/EEX/Downloads/Trading/Calendar/Auction_Calendar/EEX_Auction_Calendar_EUA_MSR_adjustment_2026_30.7.2026.xlsx">a</a>'
        '<a href="/fileadmin/EEX/Downloads/Trading/Calendar/Auction_Calendar/EEX_ETS1_Auction_Calendar_2027_30.9.2026.xlsx">b</a>'
        '<a href="/fileadmin/EEX/Downloads/Trading/Calendar/Auction_Calendar/EEX_Auction_Calendar_EUA_MSR_adjustment_2026_30.7.2026.pdf">pdf</a>'
        '<a href="/fileadmin/EEX/Downloads/Trading/Calendar/Auction_Calendar/20260730-History_EUA_Primary_Auction_Calendar_2026_01.xlsx">h</a>'
        '<a href="/fileadmin/EEX/Downloads/Trading/Calendar/Auction_Calendar/EEX_ETS2_Auction_Calendar_2027.xlsx">e2</a>'
    )
    assert eex.calendar_links(html) == [
        "https://www.eex.com/fileadmin/EEX/Downloads/Trading/Calendar/Auction_Calendar/EEX_Auction_Calendar_EUA_MSR_adjustment_2026_30.7.2026.xlsx",
        "https://www.eex.com/fileadmin/EEX/Downloads/Trading/Calendar/Auction_Calendar/EEX_ETS1_Auction_Calendar_2027_30.9.2026.xlsx",
    ]


# ── Ước tính ───────────────────────────────────────────────────────────────────

def test_quarter_helpers():
    assert ce.quarter_bounds(2026, 4) == (date(2026, 10, 1), date(2026, 12, 31))
    assert ce.quarter_bounds(2026, 1) == (date(2026, 1, 1), date(2026, 3, 31))
    assert ce.quarter_of(date(2026, 10, 15)) == (2026, 4)
    assert ce.business_days_between(date(2026, 10, 15), date(2026, 10, 19)) == 2  # T6 + T2
    assert ce.business_days_between(date(2026, 10, 15), date(2026, 10, 15)) == 0


def test_vwap_matches_ec_method():
    # VWAP chứ không phải trung bình cộng: phiên khối lượng lớn nặng hơn
    assert ce.vwap([(80.0, 3.0), (90.0, 1.0)]) == pytest.approx(82.5)
    assert ce.vwap([]) is None


def test_cumulative_quarter_vwap_resets_each_quarter():
    rows = [
        (date(2026, 9, 29), 80.0, 3.0),
        (date(2026, 9, 30), 90.0, 1.0),
        # 2 phiên cùng ngày (EU + PL) → 1 điểm, cộng dồn cả 2
        (date(2026, 10, 1), 85.0, 2.0),
        (date(2026, 10, 1), 87.0, 2.0),
        (date(2026, 10, 2), 70.0, 0.0),  # khối lượng 0 → bỏ
        (date(2026, 10, 5), 89.0, 4.0),
    ]
    out = ce.cumulative_quarter_vwap(rows)
    assert [r["date"] for r in out] == ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-05"]
    assert out[1]["vwap"] == pytest.approx(82.5)
    assert out[1]["quarter"] == "Q3 2026"
    # sang Q4: đặt lại, không lẫn phiên Q3
    assert out[2] == {"date": "2026-10-01", "vwap": 86.0, "quarter": "Q4 2026", "sessions": 2}
    assert out[3]["vwap"] == pytest.approx((85 * 2 + 87 * 2 + 89 * 4) / 8, abs=0.01)
    assert out[3]["sessions"] == 3


def test_daily_log_vol():
    assert ce.daily_log_vol([80, 81]) is None
    closes = [80 * (1.01 if i % 2 else 0.99) ** 1 for i in range(20)]
    assert ce.daily_log_vol(closes) > 0


AS_OF = date(2026, 10, 15)
DONE = [(date(2026, 10, 13), 87.04, 2.0e6), (date(2026, 10, 14), 87.02, 2.0e6), (date(2026, 10, 15), 85.84, 2.0e6)]


def test_estimate_blends_fixed_and_remaining():
    remaining = [(date(2026, 10, 19), 2.0e6), (date(2026, 10, 20), 2.0e6)]
    est = ce.estimate_quarter(DONE, remaining, spot=80.0, sigma=0.02, as_of=AS_OF)
    qtd = (87.04 + 87.02 + 85.84) / 3
    assert est["qtd_vwap"] == round(qtd, 2)
    assert est["estimate"] == round((qtd * 3 + 80.0 * 2) / 5, 2)
    assert est["fixed_share_pct"] == 60.0
    assert est["auctions_done"] == 3 and est["auctions_total"] == 5
    # CI: t = 2 và 3 ngày giao dịch, w = 0,5/0,5 → Σwᵢwⱼmin = 0,25·(2+2+2+3) = 2,25
    std = 0.4 * 80.0 * 0.02 * math.sqrt(2.25)
    assert est["ci_low"] == pytest.approx(est["estimate"] - 1.645 * std, abs=0.01)
    assert est["ci_high"] == pytest.approx(est["estimate"] + 1.645 * std, abs=0.01)


def test_estimate_without_vol_has_no_ci():
    est = ce.estimate_quarter(DONE, [(date(2026, 10, 19), 1e6)], spot=86.0, sigma=None, as_of=AS_OF)
    assert est["ci_low"] is None and est["ci_high"] is None


def test_quarter_fully_auctioned_ci_collapses():
    est = ce.estimate_quarter(DONE, [], spot=70.0, sigma=0.02, as_of=AS_OF)
    assert est["estimate"] == est["qtd_vwap"] == est["ci_low"] == est["ci_high"]
    assert est["signal_reason"] == "quarter_fixed"


@pytest.mark.parametrize("spot,remaining_vol,signal,reason", [
    (82.0, 20e6, "increase", "spot_below_qtd"),   # thấp hơn VWAP đã chốt ~4%
    (90.0, 20e6, "reduce", "spot_above_qtd"),
    (86.8, 20e6, "hold", "spot_near_qtd"),
    (82.0, 0.5e6, "hold", "quarter_mostly_fixed"),  # đã chốt > 85% khối lượng
])
def test_hedge_signal(spot, remaining_vol, signal, reason):
    est = ce.estimate_quarter(DONE, [(date(2026, 10, 19), remaining_vol)], spot=spot, sigma=0.02, as_of=AS_OF)
    assert (est["signal"], est["signal_reason"]) == (signal, reason)


def test_quarter_not_started():
    est = ce.estimate_quarter([], [(date(2026, 10, 2), 1e6)], spot=86.0, sigma=0.02, as_of=date(2026, 10, 1))
    assert est["qtd_vwap"] is None and est["estimate"] == 86.0
    assert est["signal_reason"] == "quarter_not_started"


def test_describe_for_prompt():
    assert "chưa có dữ liệu" in ce.describe_for_prompt(None)
    est = {"quarter": "Q4 2026", **ce.estimate_quarter(DONE, [(date(2026, 10, 19), 2e6)], 86.0, 0.02, AS_OF),
           "prev_quarter": {"quarter": "Q3 2026", "vwap": 82.32, "official": 82.32}}
    text = ce.describe_for_prompt(est)
    assert "VWAP" in text and "Q4 2026" in text and "82,32" in text and "KHÓA LÕI BẰNG FUTURES" in text
    # % lõi ghi theo khối lượng QUÝ, cách khóa = mua futures trước rồi bán rải theo lịch (không phải mua EUA rải theo lịch).
    assert "% nghĩa vụ CBAM quý" in text and "MUA EUA futures Dec" in text and "KHÔNG khóa được gì" in text
