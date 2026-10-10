"""Báo cáo Chủ Nhật/Thứ Hai (ngày dữ liệu cuối tuần, không có phiên giao dịch) — chỉ phân
tích tin tức: không prompt nào chứa dữ liệu giá, Lucy QC không đòi bảng giá/kịch bản giao
dịch và bắt được phần giá còn sót."""
import copy

from services import report_generator as rg
from services import report_qc

SUNDAY_REPORT = "2026-10-11"  # ngày dữ liệu Thứ Bảy 10/10
MONDAY_REPORT = "2026-10-12"  # ngày dữ liệu Chủ Nhật 11/10
SUMMARY = "**Tổng hợp:** [TÍCH CỰC] " + " ".join(["chữ"] * 42)

NO_PRICE = {
    "1": {"title": "Tóm tắt điều hành", "bullets": [{"text": "**Chính sách:** ...", "source_name": "Reuters", "source_url": "https://x.com/a"}]},
    "2": {
        "title": "Diễn biến chính", "no_price_data": True,
        "no_price_notice": rg.NO_PRICE_NOTICE.format(data_date="10/10/2026"),
        "price_timestamp": "", "key_facts": "", "prices": [], "key_developments": [], "chart_data": [],
        "market_drivers": {"bullish": [], "bearish": []},
    },
    "3": {
        "title": "Phân tích tác động của tin tức tới EUA (không có phiên giao dịch)",
        "analysis_blocks": [
            {"heading": "Phân tích", "content": "Chính sách: Hỗ trợ EUA.\n- ...\n" + SUMMARY},
            {"heading": "Cần theo dõi", "content": "1. ..."},
        ],
        "trading_scenarios": [],
    },
}


def g():
    return copy.deepcopy(NO_PRICE)


def test_only_sunday_and_monday_reports_have_no_prices():
    assert rg.is_no_price_report(SUNDAY_REPORT)
    assert rg.is_no_price_report(MONDAY_REPORT)
    for d in ("2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"):  # Thứ Ba → Thứ Bảy
        assert not rg.is_no_price_report(d), d
    assert rg.report_data_date(SUNDAY_REPORT) == "2026-10-10"


def test_news_only_prompts_have_no_price_inputs():
    news = "[1] Reuters — EU siết MSR"
    prompts = [
        rg._prompt_section1_news_only(news, "2026-10-10"),
        rg._prompt_section3_news_only(news, "2026-10-10", ["eua_ets", "energy_gas", "eu_policy"]),
        rg._prompt_biz_recommendation(news, "PRICE_SENTINEL", "TREND_SENTINEL", "2026-10-10", news_only=True),
    ]
    for system, user in prompts:
        text = system + user
        assert "PRICE_SENTINEL" not in text and "TREND_SENTINEL" not in text
        for marker in ("DỮ LIỆU GIÁ:", "DỮ LIỆU GIÁ PHIÊN", "BIÊN ĐỘ PHIÊN", "XU HƯỚNG EUA 30 NGÀY", "GASOIL CRACK SPREAD", "D. KHUNG THỜI GIAN"):
            assert marker not in text, marker
    assert "trading_scenarios" not in prompts[1][1].split("CHỈ TRẢ VỀ JSON")[1]
    assert "instrument_notes" not in prompts[1][1].split("CHỈ TRẢ VỀ JSON")[1]


def test_qc_clean_no_price_report_has_no_price_or_scenario_issues():
    c = g()
    issues = report_qc.check_prices(c, {}, "2026-10-09", SUNDAY_REPORT) + report_qc.check_scenarios(c)
    assert issues == [], issues


def test_qc_flags_price_leftovers_in_no_price_report():
    c = g()
    c["2"]["prices"] = [{"code": "EUA", "close": 79.2}]
    c["2"]["chart_data"] = [{"date": "2026-10-09", "close": 79.2}]
    c["3"]["trading_scenarios"] = [{"horizon": "ngắn hạn"}]
    issues = report_qc.check_prices(c, {}, "2026-10-09", SUNDAY_REPORT) + report_qc.check_scenarios(c)
    messages = " | ".join(i["message"] for i in issues if i["severity"] == "error")
    assert "bảng giá" in messages and "biểu đồ EUA" in messages and "kịch bản giao dịch" in messages


def test_qc_warns_when_monday_report_still_shows_old_prices():
    c = g()
    c["2"] = {"title": "Bảng giá nhanh", "price_timestamp": "Giá chốt phiên 2026-10-09 (nguồn: Barchart EOD)",
              "prices": [{"code": "EUA", "name": "EUA", "close": 79.2, "price": "79.2000 EUR/tCO2"}]}
    issues = report_qc.check_prices(c, {"EUA": {"name": "EUA", "close": 79.2}}, "2026-10-09", MONDAY_REPORT)
    assert any("không có phiên" in i["message"] for i in issues), issues
