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


def test_not_filled_falls_back_to_direction():
    sc = {"direction": "tăng", "trading_strategy": "**Entry:** Mua quanh 80–81\n**Mục tiêu:** 86\n**Quản trị rủi ro:** Cắt lỗ dưới 78"}
    sessions = [{"date": "d1", "high": 86.0, "low": 84.5, "close": 85.8}]
    it = fr.evaluate_forecast("d1", sc, 84.0, sessions)
    assert it["trade"]["status"] == "not_filled" and it["verdict"] == "correct"


@pytest.mark.parametrize("direction,chg,verdict", [
    ("tăng", 0.3, "unclear"), ("tăng", -1.0, "wrong"), ("giảm", -0.6, "correct"),
    ("đi ngang", 1.0, "correct"), ("đi ngang", -2.0, "wrong"),
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


def test_older_wrong_forecast_stays_in_table():
    """Báo cáo 17/10: 3 bản gần nhất (16, 15, 14/10) + bản 13/10 sai vẫn phải hiện; bản cũ ĐÚNG thì không."""
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
    assert [it["report_date"] for it in block["items"]] == ["2026-10-16", "2026-10-15", "2026-10-14", "2026-10-13"]
    assert block["items"][-1]["verdict"] == "wrong" and "13/10" in text
