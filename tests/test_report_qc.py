"""Test phần THUẦN của Lucy QC — services/report_qc.py (không đụng DB/LLM)."""
import asyncio
import copy

from services import report_qc, report_qc_llm

SUMMARY = "**Tổng hợp:** [TIÊU CỰC] " + " ".join(["chữ"] * 42)
SCENARIO = {
    "horizon": "ngắn hạn", "probability": "Cao", "direction": "giảm", "condition": "Nếu ...",
    "price_zone": "78-80", "key_risk": "...", "trading_strategy": "**Entry:** 79",
}
CONTENT = {
    "1": {"bullets": [{"text": "a", "source_url": "https://x.com/a"}]},
    "2": {
        "price_timestamp": "Giá chốt phiên 2026-10-06 (nguồn: Barchart EOD)",
        "prices": [
            {"name": "EUA", "code": "EUA", "price": "79.2000 EUR/tCO2", "close": 79.2,
             "day_change_pct": -0.5, "week_change_pct": 1.0, "up": False},
            {"name": "CBAM", "code": "CBAM", "price": "70 EUR", "close": 70.0},
        ],
        "chart_data": [{"date": "2026-10-06", "close": 79.2}],
    },
    "3": {"analysis_blocks": [{"heading": "Phân tích", "content": "Chính sách: ...\n" + SUMMARY}],
          "trading_scenarios": [SCENARIO]},
    "8": {"events": []},
    "9": {"items": [{"url": "https://x.com/a"}]},
    "biz": {"short_term": [{"id": 1, "trigger": "Nếu EUA < 70", "action": "Mua", "reason": "r"}], "long_term": []},
}
DB_PRICES = {"EUA": {"name": "EUA", "close": 79.2, "day_change_pct": -0.5, "week_change_pct": 1.0}}


def _content(**over):
    c = copy.deepcopy(CONTENT)
    c.update(over)
    return c


def test_prices_match():
    assert report_qc.check_prices(CONTENT, DB_PRICES, "2026-10-06") == []


def test_prices_mismatch_and_stale():
    c = copy.deepcopy(CONTENT)
    c["2"]["prices"][0]["close"] = 68.5
    issues = report_qc.check_prices(c, DB_PRICES, "2026-10-07")
    assert any(i["severity"] == "error" and "68.50" in i["message"] for i in issues)
    assert any("đã cũ" in i["message"] for i in issues)


def test_scenario_contradicts_summary():
    sc = {**SCENARIO, "direction": "tăng"}
    issues = report_qc.check_scenarios(_content(**{"3": {**CONTENT["3"], "trading_scenarios": [sc]}}))
    assert [i["severity"] for i in issues] == ["error"]
    assert "[TIÊU CỰC]" in issues[0]["message"]


def test_scenario_ok_and_missing_fields():
    assert report_qc.check_scenarios(CONTENT) == []
    sc = {**SCENARIO, "probability": "", "direction": "lên"}
    issues = report_qc.check_scenarios(_content(**{"3": {**CONTENT["3"], "trading_scenarios": [sc]}}))
    assert sum(i["severity"] == "error" for i in issues) == 2  # thiếu field + direction sai


def test_sources_unknown_url():
    assert report_qc.check_sources(CONTENT, {"https://x.com/a/"}) == []  # bỏ qua dấu "/" cuối
    issues = report_qc.check_sources(CONTENT, set())
    assert {i["section"] for i in issues} == {"1", "9"}
    assert all(i["severity"] == "warning" for i in issues)


def test_calendar_wrong_weekday_duplicate_and_missing():
    # report 2026-10-08 -> dữ liệu 2026-10-07 (thứ Tư). EIA thứ Tư giờ Mỹ = thứ Tư giờ VN;
    # Baker Hughes thứ Sáu 12:00 CT = rạng sáng thứ Bảy giờ VN (10/10).
    expected = report_qc.expected_recurring_dates("2026-10-07")
    assert "2026-10-07" in expected["eia"] and "2026-10-10" in expected["baker_hughes"]

    good = [
        {"date": "2026-10-07", "event": "Tồn kho dầu thô EIA", "impact": "Cao", "outcome": "x"},
        {"date": "2026-10-10", "event": "Số giàn khoan Baker Hughes", "impact": "Trung"},
    ]
    assert report_qc.check_calendar(_content(**{"8": {"events": good}}), "2026-10-08") == []

    bad = good + [
        {"date": "2026-10-09", "event": "Tồn kho dầu thô EIA", "impact": "Cao"},
        {"date": "2026-10-10", "event": "Số giàn khoan Baker Hughes (bản 2)", "impact": "Trung"},
    ]
    issues = report_qc.check_calendar(_content(**{"8": {"events": bad}}), "2026-10-08")
    assert any(i["severity"] == "error" and "sai lịch" in i["message"] for i in issues)
    assert any("trùng" in i["message"] for i in issues)

    issues = report_qc.check_calendar(_content(**{"8": {"events": []}}), "2026-10-08")
    assert sum("Thiếu sự kiện" in i["message"] for i in issues) == 2


def test_biz_trigger_rule():
    prices = CONTENT["2"]["prices"]
    codes = {"EUA"}
    assert report_qc.check_biz(CONTENT, {1: {"trigger_rule": {"code": "EUA", "op": "<", "value": 70}}}, codes, prices) == []
    hit = report_qc.check_biz(CONTENT, {1: {"trigger_rule": {"code": "EUA", "op": "<", "value": 90}}}, codes, prices)
    assert any("ĐÃ chạm" in i["message"] for i in hit)
    bad = report_qc.check_biz(CONTENT, {1: {"trigger_rule": {"code": "XYZ", "op": "<", "value": 1}}}, codes, prices)
    assert bad[0]["severity"] == "error"
    c = _content(biz={"short_term": [{"trigger": "", "action": "Mua"}], "long_term": []})
    assert any(i["severity"] == "error" for i in report_qc.check_biz(c, {}, codes, prices))


def test_summarize_scores():
    issues = {
        "price": [{"severity": "error"}],
        "scenario": [{"severity": "warning"}, {"severity": "info"}],
    }
    res = report_qc.summarize(issues, skipped={"consistency", "causal"})
    assert res["scores"] == {
        "price": 75, "scenario": 88, "source": 100, "calendar": 100, "biz": 100,
        "consistency": None, "causal": None,
    }
    assert res["overall_score"] == round((75 + 88 + 300) / 5)  # check bị bỏ qua không tính vào tổng
    assert res["issues"][0]["severity"] == "error"


def test_llm_normalize_issues_drops_malformed():
    raw = [
        {"section": "1", "severity": "error", "message": " Mâu thuẫn ", "field_path": "1.bullets[0]"},
        {"section": "8", "severity": "error", "message": "x", "field_path": "8"},  # section ngoài phạm vi
        {"section": "3", "severity": "fatal", "message": "x", "field_path": "3"},  # severity sai
        "rác",
    ]
    assert report_qc_llm.normalize_issues("causal", raw) == [
        {"check": "causal", "section": "1", "severity": "error", "message": "Mâu thuẫn", "field_path": "1.bullets[0]"},
    ]


def test_llm_digest_has_field_paths():
    digest = report_qc_llm.report_digest(CONTENT)
    assert "[2.prices[0]] EUA" in digest
    assert "[3.trading_scenarios[0]]" in digest and "[biz.short_term[0]]" in digest
    assert "chart_data" not in digest


def test_llm_check_failure_is_skipped(monkeypatch):
    async def fake_run(check, system, user):
        if check == "causal":
            raise report_qc_llm.LLMCheckError("API lỗi 529")
        return [{"check": check, "section": "1", "severity": "warning", "message": "m", "field_path": "1"}]

    monkeypatch.setattr(report_qc_llm, "build_prompts", lambda *a: {c: ("s", "u") for c in report_qc_llm.LLM_CHECKS})
    monkeypatch.setattr(report_qc_llm, "run_llm_check", fake_run)
    issues, skipped = asyncio.run(report_qc._run_llm_checks(CONTENT, "2026-10-08", None))
    assert skipped == {"causal"}
    assert issues["consistency"][0]["severity"] == "warning"
    assert "API lỗi 529" in issues["causal"][0]["message"]
    res = report_qc.summarize(issues, skipped)
    assert res["scores"]["causal"] is None and res["scores"]["consistency"] == 90
