"""Đối chiếu dự báo trước đúng/sai (services/forecast_review.py) + Jack check_forecast_review."""
import pytest

from services import forecast_review as fr
from services import report_qc

# Bản 13/10/2026 bị nhận xét: "Tăng, xác suất Cao", mua 87,2–88,0, cắt lỗ 86,30; EUA tham chiếu 88,34.
SC_1310 = {
    "horizon": "ngắn hạn", "direction": "tăng", "probability": "Cao",
    "trading_strategy": "**Entry:** Mua quanh 87,2–88,0 EUR/tCO2\n**Mục tiêu:** 90,5 (kháng cự)\n**Quản trị rủi ro:** Cắt lỗ dưới 86,30",
}
SESSIONS = [
    {"date": "2026-10-13", "high": 88.6, "low": 87.5, "close": 88.05},
    {"date": "2026-10-14", "high": 88.2, "low": 86.9, "close": 87.14},
    {"date": "2026-10-15", "high": 87.3, "low": 85.6, "close": 85.84},
]


def test_1310_forecast_is_wrong_and_stopped_out():
    it = fr.evaluate_forecast("2026-10-13", SC_1310, 88.34, SESSIONS)
    assert it["verdict"] == "wrong" and it["high_confidence_miss"]
    assert it["change_pct"] == pytest.approx(-2.83, abs=0.01)
    t = it["trade"]
    assert (t["side"], t["entry_low"], t["entry_high"], t["stop"], t["target"]) == ("buy", 87.2, 88.0, 86.3, 90.5)
    assert (t["status"], t["filled_date"], t["exit_date"]) == ("stopped", "2026-10-13", "2026-10-15")


def test_target_hit_is_correct_and_fill_day_uses_close_only():
    sc = {"direction": "giảm", "probability": "Trung bình",
          "trading_strategy": "**Entry:** Bán quanh 86,0–86,5\n**Mục tiêu:** 83,0\n**Quản trị rủi ro:** Cắt lỗ trên 87,5"}
    sessions = [
        # phiên khớp: đỉnh 87,9 vượt cắt lỗ nhưng chỉ xét giá đóng cửa → chưa cắt lỗ
        {"date": "2026-10-13", "high": 87.9, "low": 85.9, "close": 86.2},
        {"date": "2026-10-14", "high": 86.4, "low": 82.8, "close": 83.1},
    ]
    it = fr.evaluate_forecast("2026-10-13", sc, 86.0, sessions)
    assert it["trade"]["status"] == "target_hit" and it["verdict"] == "correct"


def test_same_session_stop_and_target_counts_as_stop():
    sc = {"direction": "tăng", "trading_strategy": "**Entry:** Mua quanh 85\n**Mục tiêu:** 88\n**Quản trị rủi ro:** Cắt lỗ dưới 83"}
    sessions = [{"date": "d1", "high": 85.5, "low": 84.8, "close": 85.2},
                {"date": "d2", "high": 88.5, "low": 82.5, "close": 85.0}]
    assert fr.evaluate_forecast("d1", sc, 85.3, sessions)["trade"]["status"] == "stopped"


def test_not_filled_falls_back_to_direction_interim_until_horizon_ends():
    sc = {"direction": "tăng", "trading_strategy": "**Entry:** Mua khi đóng cửa trên 88\n**Mục tiêu:** 92\n**Quản trị rủi ro:** Cắt lỗ dưới 86"}
    sessions = [{"date": "d1", "high": 86.0, "low": 84.5, "close": 85.8}]
    it = fr.evaluate_forecast("d1", sc, 84.0, sessions)
    # Khung 1–2 tuần mới qua 1 phiên → chưa kết luận
    assert it["trade"]["status"] == "not_filled" and it["verdict"] == "on_track"
    it = fr.evaluate_forecast("d1", sc, 84.0, sessions * fr.REVIEW_MAX_SESSIONS)
    assert it["verdict"] == "correct"


def test_wrong_direction_before_horizon_is_diverging_not_wrong():
    sc = {"direction": "tăng", "trading_strategy": "**Entry:** Mua khi đóng cửa trên 88\n**Mục tiêu:** 92\n**Quản trị rủi ro:** Cắt lỗ dưới 80"}
    it = fr.evaluate_forecast("d1", sc, 87.14, [{"date": "d1", "open": 86.87, "high": 86.87, "low": 85.37, "close": 85.84}])
    assert it["trade"]["status"] == "not_filled" and it["verdict"] == "diverging"
    assert report_qc.check_forecast_review({"2": {"forecast_review": {"items": [it]}}, "3": {}}) == []


def test_limit_buy_fills_at_open_on_gap_down():
    # Bản 15/10: mua quanh 87,00–87,50, cắt lỗ 86,60; phiên 16/10 mở gap xuống 86,87 (= giá cao) rồi đóng 85,84.
    sc = {"direction": "tăng", "trading_strategy": "**Entry:** Mua quanh 87,00–87,50\n**Mục tiêu:** 89,00\n**Quản trị rủi ro:** Cắt lỗ dưới 86,60"}
    it = fr.evaluate_forecast("2026-10-15", sc, 87.14, [{"date": "2026-10-15", "open": 86.87, "high": 86.87, "low": 85.37, "close": 85.84}])
    t = it["trade"]
    assert (t["status"], t["fill_price"], t["exit_price"]) == ("stopped", 86.87, 85.84) and it["verdict"] == "wrong"


def test_open_trades_and_merge_positions():
    sc = {"direction": "tăng", "trading_strategy": "**Entry:** Mua quanh 87,2–88,0\n**Mục tiêu:** 88,5\n**Quản trị rủi ro:** Cắt lỗ dưới 85,00"}
    it = fr.evaluate_forecast("2026-10-09", sc, 88.5, [{"date": "2026-10-15", "open": 88.2, "high": 88.2, "low": 87.4, "close": 87.6}])
    trades = fr.open_trades({"items": [it]})
    assert [t["report_date"] for t in trades] == ["2026-10-09"]
    assert "Mua 87,20–88,00" in fr.describe_open_trades(trades)
    merged = fr.merge_open_positions(trades, [{"report_date": "2026-10-09", "decision": "move", "text": "Dời cắt lỗ về 81,50."}])
    assert merged[0]["decision"] == "move" and merged[0]["stop"] == 85.0
    assert fr.merge_open_positions(trades, [])[0]["decision"] is None


@pytest.mark.parametrize("direction,chg,verdict", [
    ("tăng", 0.3, "unclear"), ("tăng", -1.0, "wrong"), ("giảm", -0.6, "unclear"), ("giảm", -1.2, "correct"),
    ("đi ngang", 0.8, "correct"), ("đi ngang", 1.0, "wrong"), ("đi ngang", -2.0, "wrong"),
])
def test_direction_verdict(direction, chg, verdict):
    assert fr.direction_verdict(direction, chg) == verdict


def test_prompt_text_acknowledges_stop_and_high_confidence():
    block = {"items": [fr.evaluate_forecast("2026-10-13", SC_1310, 88.34, SESSIONS)], "wrong_in_row": 1}
    text = fr.describe_for_prompt(block, {"2026-10-13": "[TÍCH CỰC] Fuel switching hỗ trợ cầu EUA"})
    assert "13/10" in text and "CHẠM CẮT LỖ ngày 15/10" in text and "-2,83%" in text
    assert "SAI dù gắn xác suất CAO" in text and "Fuel switching" in text


def _content(vs_previous):
    item = fr.evaluate_forecast("2026-10-13", SC_1310, 88.34, SESSIONS)
    return {"2": {"forecast_review": {"items": [item], "wrong_in_row": 1}}, "3": {"vs_previous": vs_previous}}


def test_jack_requires_acknowledging_wrong_forecast():
    assert report_qc.check_forecast_review(_content(""))[0]["severity"] == "error"
    issues = report_qc.check_forecast_review(_content("Báo cáo hôm qua đúng chiều."))
    assert len(issues) == 1 and "13/10" in issues[0]["message"] and "xác suất Cao" in issues[0]["message"]
    assert report_qc.check_forecast_review(_content(
        "Dự báo tăng (xác suất Cao) của bản 13/10 đã sai: lệnh mua chạm cắt lỗ 86,30 ngày 15/10."
    )) == []
    assert report_qc.check_forecast_review({"2": {}, "3": {}}) == []


def test_every_report_in_horizon_stays_in_table():
    """Báo cáo 17/10: mọi bản còn trong khung ngắn hạn đều hiện (cả bản cũ đúng — vd 09/10 góp vào tỷ lệ "Cao")."""
    import asyncio
    from types import SimpleNamespace

    def rep(d, sc):
        return SimpleNamespace(report_date=d, content={
            "2": {"prices": [{"code": "EUA", "close": 88.34 if d == "2026-10-13" else 86.0}]},
            "3": {"trading_scenarios": [sc]},
        })

    flat = {"horizon": "ngắn hạn", "direction": "đi ngang", "probability": "Trung bình", "trading_strategy": ""}
    old_ok = {**flat, "direction": "giảm"}
    reports = [rep("2026-10-16", flat), rep("2026-10-15", flat), rep("2026-10-14", flat),
               rep("2026-10-13", SC_1310), rep("2026-10-12", old_ok)]

    class Res:
        def __init__(self, v): self.v = v
        def scalars(self): return self
        def all(self): return self.v

    class Sess:
        calls = 0
        async def execute(self, _):
            Sess.calls += 1
            return Res(reports if Sess.calls == 1 else [])

    async def fake_sessions(_s, _start, _end):
        return [{"date": "2026-10-12", "high": 88.4, "low": 88.0, "close": 88.34}] + SESSIONS + [
            {"date": "2026-10-16", "high": 86.2, "low": 85.5, "close": 86.0}]

    orig = fr._eua_sessions
    fr._eua_sessions = fake_sessions
    try:
        block, text = asyncio.run(fr.build_forecast_review(Sess(), "2026-10-17", "2026-10-16"))
    finally:
        fr._eua_sessions = orig
    assert [it["report_date"] for it in block["items"]] == ["2026-10-16", "2026-10-15", "2026-10-14", "2026-10-13", "2026-10-12"]
    assert block["items"][3]["verdict"] == "wrong" and "13/10" in text


def _s(rows):
    return [dict(date=d, open=o, high=h, low=l, close=c) for d, o, h, l, c in rows]


def test_0910_target_before_fill_cancels_order():
    # Bản 09/10: mua 85,50–86,30, mục tiêu 88,50; EUA lên 88,82 ngày 12/10 trước khi chạm vùng mua.
    sc = {"direction": "tăng", "probability": "Cao",
          "trading_strategy": "**Entry:** Mua quanh 85,50–86,30\n**Mục tiêu:** 88,50\n**Quản trị rủi ro:** Cắt lỗ dưới 85,00"}
    it = fr.evaluate_forecast("2026-10-09", sc, 86.5, _s([
        ("2026-10-09", 86.4, 87.6, 86.9, 87.4), ("2026-10-12", 87.5, 88.82, 87.3, 88.34),
        ("2026-10-15", 86.87, 86.87, 85.37, 85.84), ("2026-10-16", 85.8, 86.1, 84.9, 85.41),
    ]))
    assert it["trade"]["status"] == "cancelled_target" and it["trade"]["exit_date"] == "2026-10-12"
    assert it["verdict"] == "direction_only" and fr.open_trades({"items": [it]}) == []
    calib = fr.calibration_from([it])
    assert calib[0] == {"probability": "Cao", "correct": 0, "total": 0, "direction_only": 1}


def test_order_expires_after_valid_sessions():
    sc = {"direction": "tăng", "trading_strategy": "**Entry:** Mua quanh 80\n**Mục tiêu:** 95\n**Quản trị rủi ro:** Cắt lỗ dưới 78"}
    rows = [(f"d{i}", 86, 87, 85, 86) for i in range(fr.ORDER_VALID_SESSIONS + 2)]
    t = fr.evaluate_forecast("d0", sc, 86, _s(rows))["trade"]
    assert t["status"] == "expired" and t["exit_date"] == f"d{fr.ORDER_VALID_SESSIONS - 1}"
    # Vừa đủ N phiên (chưa có phiên N+1) cũng đã hết hiệu lực — bản 14/10: mua 88,82, đỉnh 14–16/10 chỉ 88,24.
    t = fr.evaluate_forecast("d0", sc, 86, _s(rows[:fr.ORDER_VALID_SESSIONS]))["trade"]
    assert t["status"] == "expired"


@pytest.mark.parametrize("entry,breakout", [
    ("Mua quanh 87,00–87,50 (trên hỗ trợ 86,90)", False),
    ("Mua quanh 87,00–87,50, giữ trên 86,60", False),
    ("Mua khi đóng cửa trên 88,82", True),
    ("Mua khi phá đỉnh 88,82", True),
])
def test_breakout_detection(entry, breakout):
    from services.strategy_levels import is_breakout_entry
    assert is_breakout_entry("tăng", entry) is breakout


def test_1510_gap_fill_uses_session_low_for_stop():
    # Bản 15/10: "Mua quanh 87,00–87,50 (trên hỗ trợ ...)", cắt lỗ 86,60; phiên mở 86,87 dưới vùng → khớp 86,87,
    # đáy 85,37 thủng cắt lỗ trong phiên → SAI (không phải "Đang lệch, chưa khớp").
    sc = {"direction": "tăng", "probability": "Trung bình",
          "trading_strategy": "**Entry:** Mua quanh 87,00–87,50 (trên hỗ trợ 86,90)\n**Mục tiêu:** 89,00\n**Quản trị rủi ro:** Cắt lỗ dưới 86,60"}
    it = fr.evaluate_forecast("2026-10-15", sc, 87.14, _s([("2026-10-15", 86.87, 86.87, 85.37, 87.0)]))
    t = it["trade"]
    assert (t["order_type"], t["status"], t["fill_price"]) == ("limit", "stopped", 86.87) and it["verdict"] == "wrong"


def test_calibration_counts_only_final_verdicts():
    calib = fr.calibration_from([
        {"probability": "Cao", "verdict": "wrong"}, {"probability": "Cao", "verdict": "wrong"},
        {"probability": "Trung bình", "verdict": "correct"}, {"probability": "Trung bình", "verdict": "diverging"},
    ])
    assert calib[0] == {"probability": "Cao", "correct": 0, "total": 2, "direction_only": 0}
    assert calib[1] == {"probability": "Trung bình", "correct": 1, "total": 1, "direction_only": 0}
    text = fr.describe_for_prompt({"items": [], "calibration": calib})
    assert "Cao 0/2" in text and "Thấp" not in text


def test_degenerate_zone_printed_as_single_level():
    assert fr._zone(88.82, 88.82) == "88,82" and fr._zone(87.0, 87.5) == "87,00–87,50"


def test_stop_entry_already_reached_at_issue_is_limit():
    # Bản 15/10: "Mua khi giá vượt 87,00–87,50" khi EUA đang 87,14 (đã trong vùng) → không phải lệnh chờ phá vỡ;
    # phiên 15/10 mở gap 86,87 → khớp 86,87, đáy 85,37 thủng cắt lỗ 86,60 → SAI.
    sc = {"direction": "tăng", "probability": "Trung bình",
          "trading_strategy": "**Entry:** Mua khi giá vượt 87,00–87,50\n**Mục tiêu:** 88,82\n**Quản trị rủi ro:** Cắt lỗ dưới 86,60"}
    it = fr.evaluate_forecast("2026-10-15", sc, 87.14, _s([("2026-10-15", 86.87, 86.87, 85.37, 85.84)]))
    t = it["trade"]
    assert (t["order_type"], t["status"], t["fill_price"]) == ("limit", "stopped", 86.87) and it["verdict"] == "wrong"
    # Bản 14/10: mua khi phá 88,82 khi EUA đang 88,05 → lệnh dừng thật, giữ nguyên.
    sc14 = {**sc, "trading_strategy": "**Entry:** Mua khi phá đỉnh 88,82\n**Mục tiêu:** 91,30\n**Quản trị rủi ro:** Cắt lỗ dưới 87,15"}
    assert fr.evaluate_forecast("2026-10-14", sc14, 88.05, _s([("2026-10-15", 86.87, 86.87, 85.37, 85.84)]))["trade"]["order_type"] == "stop"


def test_calibration_uses_last_n_scored_reports_only():
    """Tỷ lệ đúng chỉ tính CALIBRATION_REPORTS báo cáo gần nhất ĐÃ CÓ KẾT LUẬN — báo cáo còn trong khung không chiếm suất."""
    import asyncio
    from types import SimpleNamespace

    sc = {"horizon": "ngắn hạn", "direction": "tăng", "probability": "Cao",
          "trading_strategy": "**Entry:** Mua quanh 87,2–88,0\n**Mục tiêu:** 95,0\n**Quản trị rủi ro:** Cắt lỗ dưới 86,30"}
    # 12 báo cáo 01/09 → 12/09 (mới → cũ); giá rơi thủng cắt lỗ ngày 13/09 → báo cáo nào cũng SAI, trừ báo cáo
    # không còn phiên nào sau nó (13/09 trở đi chưa có báo cáo) — tất cả đều có kết luận.
    reports = [SimpleNamespace(report_date=f"2026-09-{d:02d}", content={
        "2": {"prices": [{"code": "EUA", "close": 87.5}]}, "3": {"trading_scenarios": [sc]}}) for d in range(12, 0, -1)]
    prices = [SimpleNamespace(price_date=f"2026-09-{d:02d}", open_price=87.5, high_price=87.8, low_price=87.3, close_price=87.5)
              for d in range(1, 13)]
    prices.append(SimpleNamespace(price_date="2026-09-13", open_price=86.0, high_price=86.2, low_price=85.0, close_price=85.2))

    class _R:
        def __init__(self, v):
            self.v = v

        def scalars(self):
            return SimpleNamespace(all=lambda: self.v)

    class _S:
        def __init__(self, *rs):
            self.rs = list(rs)

        async def execute(self, _stmt):
            return _R(self.rs.pop(0))

    calib = asyncio.run(fr._calibration(_S(reports, prices), "2026-09-14", "2026-09-13"))
    cao = next(c for c in calib if c["probability"] == "Cao")
    assert cao["total"] == fr.CALIBRATION_REPORTS == 7 and cao["correct"] == 0
