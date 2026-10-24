"""Biên bản QC Claude báo cáo 24/10/2026 — các lỗi đã kiểm chứng là thật."""
from services import forecast_review as fr
from services import report_consistency as rc
from services import report_generator as rg
from services import report_qc

SHORT = {
    "horizon": "ngắn hạn", "direction": "đi ngang", "probability": "Trung bình",
    "condition": "Nếu EUA giữ trong 84,54–88,82 và các hồ sơ ETS chưa có kết luận.",
    "trading_strategy": "**Entry:** Đặt lệnh chờ mua 84,7–85,3, bán 88,1–88,7\n"
                        "**Mục tiêu:** 87,90 (gần kháng cự 10 phiên) / 85,70 (gần hỗ trợ)\n"
                        "**Quản trị rủi ro:** Cắt lỗ dưới 83,70 hoặc trên 90,10",
}
LONG = {
    "horizon": "dài hạn", "direction": "đi ngang", "probability": "Trung bình",
    "condition": "nếu các hồ sơ nới lỏng đều không được thông qua, lộ trình phân bổ miễn phí giữ nguyên",
    "trading_strategy": "**Entry:** Tích lũy dần quanh 82,35 (đáy 30 phiên) nếu các hồ sơ nới lỏng bị bác\n"
                        "**Mục tiêu:** chưa đủ cơ sở định lượng (bốn hồ sơ nguồn cung chưa có kết quả)\n"
                        "**Quản trị rủi ro:** Giảm vị thế nếu đóng cửa dưới 80,50 (≈1,1 × ATR dưới đáy 30 phiên 82,35)",
}


def _msgs(sc):
    return [i["message"] for i in report_qc._check_strategy(sc, "Kịch bản", "3.trading_scenarios[0]", 86.26)]


# 1. Chân bán 88,1–88,7: tại 88,1 R:R 1,20 < 1,5; cận đạt chuẩn là 88,34 (biên bản QC tính nhầm ≈ 88,72). Chân mua tại 85,3 = 1,63 — đạt.
def test_two_sided_leg_rr_at_worst_edge():
    msgs = _msgs(SHORT)
    assert any("chân bán R:R chỉ 1.20" in m and "88.34" in m for m in msgs)
    assert not any("chân mua R:R" in m for m in msgs)
    ok = {**SHORT, "trading_strategy": SHORT["trading_strategy"].replace("bán 88,1–88,7", "bán 88,75–89,0")}
    assert not any("R:R" in m for m in _msgs(ok))


# 2. "Đi ngang" + lệnh tích lũy mua 1 chiều → nhãn trái lệnh.
def test_sideways_label_with_one_sided_accumulation():
    assert any("chỉ có lệnh mua 1 chiều" in m for m in _msgs(LONG))
    assert not any("1 chiều" in m for m in _msgs({**LONG, "direction": "tăng"}))
    assert not any("1 chiều" in m for m in _msgs(SHORT))


# 3. "Không có dự báo nào bị chấm SAI" trong khi 15/10 chạm cắt lỗ.
BLOCK = {"items": [
    {"report_date": "2026-10-23", "direction": "đi ngang", "probability": "Trung bình", "verdict": "on_track",
     "ref_close": 86.47, "last_close": 86.26, "change_pct": -0.24, "sessions": 1, "last_date": "2026-10-23"},
    {"report_date": "2026-10-15", "direction": "tăng", "probability": "Trung bình", "verdict": "wrong",
     "ref_close": 87.14, "last_close": 86.26, "change_pct": -1.01, "sessions": 7, "last_date": "2026-10-23",
     "trade": {"side": "buy", "order_type": "limit", "entry_low": 87.0, "entry_high": 87.5, "stop": 86.6, "target": 88.82,
               "status": "stopped", "exit_date": "2026-10-15", "filled_date": "2026-10-15", "fill_price": 86.87},
     "design_flaws": [{"kind": "tight_stop", "distance": 0.27, "atr": 1.69, "atr_multiple": 0.16, "entry": 86.87}]},
]}


def test_vs_previous_acknowledges_wrong_forecast():
    bad = ("Không có dự báo nào bị chấm SAI trong bảng đối chiếu; 6 báo cáo gần nhất đều đang đúng hướng. "
           "Hôm nay GIỮ chiều ngắn hạn \"đi ngang\".")
    assert report_qc.check_forecast_review({"2": {"forecast_review": BLOCK}, "3": {"vs_previous": bad}})
    out = fr.acknowledge_wrong_forecasts(bad, BLOCK)
    assert "Không có dự báo nào" not in out and out.startswith("Dự báo báo cáo 15/10")
    assert "86,87" in out and "86,60" in out and "0,16 × ATR" in out and "GIỮ chiều" in out
    assert not [i for i in report_qc.check_forecast_review({"2": {"forecast_review": BLOCK}, "3": {"vs_previous": out}})
                if "không nhắc" in i["message"]]
    assert fr.acknowledge_wrong_forecasts(out, BLOCK) == out
    assert "CÓ 1 DỰ BÁO BỊ CHẤM SAI: báo cáo 15/10" in fr.describe_for_prompt(BLOCK)


# 4. "+64" là mốc 1 TUẦN trước nhưng câu không ghi "tuần".
PRICES = [
    {"code": "EUA", "close": 86.26, "week_change_pct": 0.99},
    {"code": "TTF", "close": 93.853, "week_change_pct": 7.20},
    {"code": "API2", "close": 151.95, "week_change_pct": -0.20},
]


def test_spread_change_labelled_weekly():
    prev = rc.spread_prev_week({"2": {"prices": PRICES}})
    assert round(prev) == 64
    text = "Chênh chi phí phát điện khí − than mở rộng từ ≈ +64 lên ≈ +75 EUR/MWh; giá EUA hòa vốn ≈ 229 EUR/t."
    out, fixed = rc.fix_spread_period(text, prev)
    assert "+75 EUR/MWh trong tuần;" in out and fixed
    assert rc.fix_spread_period(out, prev) == (out, [])
    assert rc.fix_spread_period(text.replace("+64", "+69"), prev)[1] == []


# 5. "[N]" trong ghi chú bảng giá đổi thành tên nguồn thật.
def test_instrument_note_citations_resolved():
    lookup = {3: {"source": "uk.finance.yahoo.com", "url": "https://y"}, 4: {"source": "NBC News (gốc) → cafef.vn", "url": "https://n"}}
    holder = {"instrument_notes": {"BRENT": "Mỹ giảm quân tại Jordan, Qatar, Kuwait → rủi ro nguồn cung. [4]"}}
    used = rg._resolve_section3_citations(holder, lookup)
    assert holder["instrument_notes"]["BRENT"].endswith("(Nguồn: NBC News (gốc) → cafef.vn)")
    assert used == [{"source_name": "NBC News (gốc) → cafef.vn", "source_url": "https://n"}]


# 6. Mục 6: mốc "Thursday" quy đổi theo ngày đăng.
def test_section6_prompt_has_published_weekday():
    p = rg._prompt_section6_summary({"source": "x", "title": "t", "summary": "s", "published_date": "22/10/2026"}, "2026-10-24")
    assert "Ngày đăng: Thứ Năm 22/10/2026" in p
