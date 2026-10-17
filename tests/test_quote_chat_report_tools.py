"""Tool đọc báo cáo của Jenny/MCP: Mục 2/3 đủ trường, bảng dự báo đúng/sai, ước tính CBAM, đấu giá EEX.

review_past_forecast PHẢI chấm cùng logic với khối "Dự báo trước: đúng hay sai" (services/forecast_review.py) —
trước đây tool tự chấm theo chiều giá, có thể nói "đúng" trong khi bảng trên trang đầu ghi "sai".
"""
import asyncio
from datetime import date
from types import SimpleNamespace

from services import quote_chat as qc

SC_SHORT = {
    "horizon": "ngắn hạn", "direction": "tăng", "probability": "Cao",
    "condition": "c", "price_zone": "87,2–88,0", "key_risk": "r",
    "trading_strategy": "**Entry:** Mua quanh 87,2–88,0 EUR/tCO2\n**Mục tiêu:** 90,5 (kháng cự)\n**Quản trị rủi ro:** Cắt lỗ dưới 86,30",
}
SESSIONS = [
    {"date": "2026-10-13", "open": 88.3, "high": 88.6, "low": 87.5, "close": 88.05},
    {"date": "2026-10-14", "open": 88.0, "high": 88.2, "low": 86.9, "close": 87.14},
    {"date": "2026-10-15", "open": 87.1, "high": 87.3, "low": 85.6, "close": 85.84},
]

CBAM_EST = {
    "quarter": "Q4 2026", "as_of": "2026-10-15", "auctions_done": 10, "auctions_total": 52,
    "volume_done_mt": 25.1, "volume_total_mt": 110.0, "fixed_share_pct": 22.9, "qtd_vwap": 84.1, "spot": 85.84,
    "spot_vs_qtd_pct": 2.07, "estimate": 85.44, "ci_level": 90, "ci_low": 80.1, "ci_high": 90.8,
    "daily_vol_pct": 1.9, "signal": "reduce", "signal_reason": "spot_above_qtd", "hedge_gap_pct": 2.0,
    "hedge_fixed_share_pct": 85, "tactical_max_pct": 20,
    "last_auction": {"date": "2026-10-15", "platform": "EU", "price": 85.7, "cover_ratio": 1.62},
    "upcoming": {"count": 5, "volume_mt": 12.3, "first_date": "2026-10-16", "last_date": "2026-10-22"},
    "prev_quarter": {"quarter": "Q3 2026", "vwap": 82.32, "official": 82.32},
}

FORECAST_BLOCK = {
    "items": [{
        "report_date": "2026-10-13", "direction": "tăng", "probability": "Cao", "ref_close": 88.34,
        "last_date": "2026-10-16", "last_close": 85.41, "change_pct": -3.32, "sessions": 4, "verdict": "wrong",
        "high_confidence_miss": True,
        "trade": {"side": "buy", "order_type": "limit", "entry_low": 87.2, "entry_high": 88.0, "stop": 86.3,
                  "target": 88.82, "status": "stopped", "filled_date": "2026-10-13", "fill_price": 88.0,
                  "exit_date": "2026-10-15", "exit_price": 85.84},
    }],
    "wrong_in_row": 1,
    "calibration": [{"probability": "Cao", "correct": 1, "total": 3}, {"probability": "Thấp", "correct": 0, "total": 0}],
    "rules": {"horizon_sessions": 10, "direction_threshold_pct": 1.0, "sideways_max_pct": 1.0, "order_valid_sessions": 3},
}

CONTENT = {
    "2": {
        "title": "Bảng giá nhanh", "prices": [{"code": "EUA", "close": 88.34, "note": "Đấu giá yếu"}, {"code": "TTF", "close": 30, "note": ""}],
        "key_developments": [], "market_drivers": {}, "cbam_estimate": CBAM_EST, "forecast_review": FORECAST_BLOCK,
    },
    "3": {
        "title": "Phân tích", "analysis_blocks": [{"heading": "Phân tích", "content": "**Tổng hợp:** TĂNG"}],
        "trading_scenarios": [SC_SHORT], "vs_previous": "Báo cáo 13/10 sai vì tiền đề fuel switching.",
        "fuel_model_note": "Hiệu suất khí 55%, than 38%.",
        "hedge_action": {"action": "reduce", "core": "Phần lõi bám lịch đấu giá.", "text": "Phần chiến thuật chờ 84."},
        "open_positions": [{"report_date": "2026-10-14", "side": "buy", "entry_low": 87.0, "entry_high": 87.5,
                            "filled_date": "2026-10-15", "fill_price": 87.2, "stop": 86.0, "target": 89.0,
                            "decision": "keep", "text": "Giữ vì hỗ trợ 86,5."}],
        "sources": [{"source_name": "Reuters", "source_url": "https://reuters.com/x"}],
    },
}


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: self._value)


class _Session:
    def __init__(self, *results):
        self._results = list(results)

    async def execute(self, _stmt):
        return _Result(self._results.pop(0))


def _price_rows(sessions):
    return [SimpleNamespace(price_date=s["date"], open_price=s["open"], high_price=s["high"], low_price=s["low"],
                            close_price=s["close"]) for s in sessions]


def test_section2_includes_price_notes_cbam_and_forecast_review():
    text = qc._format_section2(CONTENT["2"])
    assert "EUA: Đấu giá yếu" in text and "TTF:" not in text
    assert "Ước tính giá CBAM Q4 2026" in text and "85.44" in text and "80.10–90.80" in text
    assert "Dự báo trước: đúng hay sai" in text and "chạm cắt lỗ".upper() in text.upper()


def test_section3_includes_hedge_positions_vs_previous_and_sources():
    text = qc._format_section3(CONTENT["3"])
    for needle in ("tiền đề fuel switching", "Hiệu suất khí 55%", "Phòng hộ CBAM", "Phần chiến thuật chờ 84",
                   "Lệnh mô phỏng đang mở", "GIỮ: Giữ vì hỗ trợ 86,5", "Reuters (https://reuters.com/x)"):
        assert needle in text, needle


def test_section3_old_report_without_new_fields_still_formats():
    text = qc._format_section3({"title": "P", "analysis_blocks": [], "trading_scenarios": []})
    assert "Phòng hộ" not in text and "Lệnh mô phỏng" not in text and "(không có)" in text


def test_forecast_scorecard_matches_report_block():
    text = asyncio.run(qc._tool_forecast_scorecard_text(_Session(CONTENT), "2026-10-17"))
    assert "Báo cáo 2026-10-13" in text and "SAI (dù gắn xác suất CAO)" in text
    assert "Cao 1/3" in text and "Thấp" not in text.split("Tỷ lệ đúng")[1].split("\n")[0]
    assert "Nguyên nhân & điều chỉnh" in text and "khung ngắn hạn 10 phiên" in text


def test_forecast_scorecard_unpublished():
    text = asyncio.run(qc._tool_forecast_scorecard_text(_Session(None), "2026-10-17"))
    assert "Không có báo cáo published" in text


def test_cbam_estimate_tool_and_hedge_label():
    text = asyncio.run(qc._tool_cbam_estimate_text(_Session(CONTENT), "2026-10-16"))
    assert "Q4 2026" in text and "phần chiến thuật chưa khóa" in text
    assert "cao hơn VWAP đã chốt ≥ 2.00%" in text and "Ô phòng hộ Mục 3" in text
    assert "Q3 2026: VWAP đấu giá 82.32 ↔ EC công bố 82.32" in text


def test_cbam_estimate_no_price_report():
    content = {"2": {"no_price_data": True}}
    text = asyncio.run(qc._tool_cbam_estimate_text(_Session(content), "2026-10-19"))
    assert "chỉ tin tức" in text


def test_eua_auctions_lists_rows_and_vwap():
    rows = [
        SimpleNamespace(auction_date=date(2026, 10, 14), auction_name="EU", status="successful", volume=3e6,
                        auction_price=86.0, cover_ratio=1.5, total_bidders=20),
        SimpleNamespace(auction_date=date(2026, 10, 15), auction_name="DE", status="successful", volume=1e6,
                        auction_price=90.0, cover_ratio=None, total_bidders=None),
        SimpleNamespace(auction_date=date(2026, 10, 20), auction_name="EU", status="scheduled", volume=3e6,
                        auction_price=None, cover_ratio=None, total_bidders=None),
    ]
    text = asyncio.run(qc._tool_eua_auctions_text(_Session(rows), "2026-10-10", "2026-10-25"))
    assert "cover ratio 1.50" in text and "20 nhà thầu" in text and "theo lịch" in text
    assert "VWAP 87.00 EUR/t trên 4.00 triệu t" in text


def test_eua_auctions_empty():
    text = asyncio.run(qc._tool_eua_auctions_text(_Session([]), "2026-10-10", "2026-10-25"))
    assert "Không có phiên đấu giá" in text


def test_review_past_forecast_uses_forecast_review_verdict():
    """Giá đi lên lại sau cắt lỗ không được biến dự báo thành 'đúng' — bảng trang đầu ghi SAI."""
    sessions = SESSIONS + [{"date": f"2026-10-{d}", "open": 89, "high": 90, "low": 88.5, "close": 89.5} for d in (16, 19, 20)]
    content = {"2": {"prices": [{"code": "EUA", "close": 88.34}]}, "3": {"trading_scenarios": [SC_SHORT], "analysis_blocks": []}}
    text = asyncio.run(qc._tool_review_forecast_text(_Session(content, _price_rows(sessions)), "2026-10-13", 5))
    result = text.split("KẾT QUẢ CHẤM")[1]
    assert "SAI" in result and "CHẠM CẮT LỖ ngày 2026-10-15" in result
    assert text.count("- 2026-10-") == 5  # liệt kê đúng `sessions` phiên


def test_new_tools_exposed_to_mcp_gateway():
    from api.routers import mcp_gateway

    for name in ("get_forecast_scorecard", "get_cbam_estimate", "get_eua_auctions"):
        assert name in mcp_gateway._TOOLS_BY_NAME
    assert "get_eua_auctions" in mcp_gateway._REPORT_INDEPENDENT_TOOLS
