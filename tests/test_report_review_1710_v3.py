"""Bản chấm lần 4 báo cáo 17/10/2026 — chỉ các nhận xét đúng logic/số liệu."""
from datetime import date

from services import cbam_estimate as ce
from services import forecast_review as fr
from services import report_generator as rg
from services import report_qc

PRICES = [
    {"code": "EUA", "close": 85.41, "week_change_pct": -1.76},
    {"code": "TTF", "close": 87.55, "week_change_pct": 7.51},
    {"code": "API2", "close": 152.25, "week_change_pct": 3.75},
]


# 3.1 Nâng ngưỡng vô hiệu hóa MSR là NỚI cung dài hạn.
def test_msr_invalidation_threshold_is_loosening():
    bad = {"3": {"analysis_blocks": [{"heading": "Cần theo dõi", "content":
        "1. Nếu Hội đồng chốt lập trường MSR nâng ngưỡng vô hiệu hóa 800 triệu → kỳ vọng cung siết lại → hỗ trợ EUA."}]}}
    assert report_qc.check_msr_direction(bad)
    ok = {"3": {"analysis_blocks": [{"heading": "Cần theo dõi", "content":
        "1. Nếu Hội đồng bác đề nghị giảm chuyển 190 triệu vào MSR → cung giữ lộ trình → hỗ trợ EUA."}]}}
    assert report_qc.check_msr_direction(ok) == []
    msr = next(d for d in rg._load_ets_dossiers() if "ngưỡng vô hiệu" in d["status"])
    assert msr["impact"] == "giảm" and "NỚI cung" in msr["mechanism"]
    euco = next(d for d in rg._load_ets_dossiers() if d["kind"] == "event")
    assert euco["impact"] == "trung lập" and euco["source_name"] == "Eunews"
    block = rg._ets_policy_prompt_block([], {}, rg._dossier_lookup(rg._load_ets_dossiers()))
    assert "CƠ CHẾ MSR" in block and "NÂNG ngưỡng vô hiệu hóa → hủy ít hơn → NỚI cung" in block


# 3.2 Mẫu số lũy kế chỉ đếm báo cáo có kết luận Đúng/Sai.
def test_cumulative_denominator_excludes_direction_only():
    ev = [{"probability": "Cao", "verdict": "correct"}, {"probability": "Cao", "verdict": "wrong"},
          {"probability": "Cao", "verdict": "direction_only"}, {"probability": "Trung bình", "verdict": "correct"}]
    calib = fr.calibration_from(ev)
    assert sum(c["total"] for c in calib) == 3 and sum(c["direction_only"] for c in calib) == 1


# 3.3 Giá trị mặc định theo nước xuất xứ — số Trung Quốc chỉ là minh họa.
def test_default_values_marked_illustrative_for_vn():
    rows = ce.product_exposure(85.39, None, None, 2026)
    text = ce.describe_product_exposure({"product_exposure": rows})
    assert "MINH HỌA theo xuất xứ này — KHÔNG áp cho hàng xuất xứ Việt Nam" in text and "mặc định VN ×" in text
    biz = {"biz": {"short_term": [], "long_term": [{
        "opportunity": "Thẩm định phát thải", "solution": "Chương trình thẩm định",
        "financials": "Thép: mặc định Trung Quốc 185,29 so với số thực 39,64 → tiết kiệm 145,65 EUR/t"}]}}
    assert any("nước xuất xứ" in i["message"] for i in report_qc.check_biz(biz, {}, set(), [], "2026-10-18"))


# 4.6 Lộ trình hệ số CBAM hiện hành có sẵn cho bảng độ nhạy.
def test_biz_block_has_current_cbam_schedule():
    block = rg._biz_policy_block(rg._dossier_lookup(rg._load_ets_dossiers()))
    assert "2026: 97,5% · 2027: 95,0% · 2028: 90,0% · 2029: 77,5% · 2030: 51,5%" in block


# 5.1 Phiên đấu giá cuối Q4 trùng ngày đáo hạn Dec-26.
def test_expiry_same_day_note():
    est = {"quarter": "Q4 2026", "last_auction_date": "2026-12-14", "futures_expiry": "2026-12-14",
           **ce.estimate_quarter([(date(2026, 10, 1), 85.32, 26.71e6)], [(date(2026, 12, 14), 90.0e6)], 85.41, 0.0118,
                                 date(2026, 10, 16))}
    text = ce.hedge_structure_text(est)
    assert "TRÙNG ngày đáo hạn Dec-26" in text and "nếu có" not in text
    assert ce.load_cbam_products()["futures_expiry"][2026] == "2026-12-14"


# 5.2–5.3 Không có ngưỡng TTF chiều lên.
def test_no_upward_ttf_threshold():
    text = rg._fuel_switching_spread_summary(PRICES)
    assert "KHÔNG có ngưỡng chiều lên" in text and "CCGT hiệu suất cao nhất (60%)" in text
    content = {"2": {"prices": PRICES}, "3": {"analysis_blocks": [{"heading": "Phân tích", "content":
        "- **Dầu:** Hormuz chưa kéo TTF tới ngưỡng đổi thứ tự huy động nên chưa thành cầu EUA mới."}]}}
    assert any("chiều LÊN" in i["message"] for i in report_qc.check_ttf_channel(content))


# 5.4 Tóm tắt điều hành có ý Chính sách ETS.
def test_exec_summary_gets_ets_policy():
    block = rg._exec_policy_block(rg._dossier_lookup(rg._load_ets_dossiers()))
    assert '"**Chính sách ETS:**"' in block and "Hội đồng châu Âu 15–16/10/2026" in block


# 5.5 Vùng tích lũy dài hạn dưới giá hiện tại.
def test_long_term_zone_below_close_noted():
    sec3 = {"trading_scenarios": [{"horizon": "dài hạn", "direction": "tăng", "probability": "Trung bình",
                                   "price_zone": "Tích lũy 79,00–82,35 EUR/tCO2", "trading_strategy": ""}]}
    rg._enforce_scenario_rules(sec3, 85.41)
    assert "thấp hơn giá đóng cửa 85,41 3,6–7,5%: chờ giá điều chỉnh" in sec3["trading_scenarios"][0]["price_zone"]


# 4.1 / 5.6 Khối "Quan điểm thị trường" chỉ lặp bảng chính sách bị bỏ; dấu gạch thừa trước "Kết luận".
def test_trim_market_view_and_stray_bullet():
    lookup = {1: {"url": "eni"}, 2: {"url": "other"}}
    sec3 = {"ets_policy": [{"source_url": "eni"}], "analysis_blocks": [
        {"heading": "Phân tích", "content": "Chính sách: x\n- \n- **Kết luận:** y"},
        {"heading": "Quan điểm thị trường", "content": "- CEO Eni đòi tạm dừng ETS [1]\n**Kết luận:** z"},
    ]}
    rg._trim_section3_blocks(sec3, lookup)
    assert [b["heading"] for b in sec3["analysis_blocks"]] == ["Phân tích"]
    assert sec3["analysis_blocks"][0]["content"] == "Chính sách: x\n**Kết luận:** y"
