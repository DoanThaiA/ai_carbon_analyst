"""Test phần THUẦN của Lucy QC — services/report_qc.py (không đụng DB/LLM).

GOLDEN là 1 báo cáo SẠCH dựng đúng định dạng generate_report_content() xuất ra — phải
ra 0 vấn đề (không báo sai). Các test còn lại cài từng loại lỗi vào bản sao của GOLDEN
và kiểm Lucy bắt đúng lỗi đó (không bỏ sót).
"""
import asyncio
import copy

from services import report_qc
from services.report_generator import _format_pct_with_abs

REPORT_DATE = "2026-10-08"  # ngày dữ liệu 2026-10-07 (thứ Tư)
PRICE_DATE = "2026-10-07"
SUMMARY = "**Tổng hợp:** [TIÊU CỰC] " + " ".join(["chữ"] * 42)
A1, A2, A3, VN1 = "https://x.com/eua-1", "https://x.com/eua-2", "https://x.com/gas-1", "https://vn.vn/carbon-1"


def _price_row(name, code, close, day, week, unit="EUR/tCO2"):
    return {
        "name": name, "code": code, "price": f"{close:,.4f} {unit}",
        "dday": _format_pct_with_abs(day, close), "dweek": _format_pct_with_abs(week, close),
        "up": day is not None and day > 0, "note": "", "close": close,
        "day_change_pct": day, "week_change_pct": week, "volume": None, "category": "carbon",
    }


GOLDEN = {
    "1": {"title": "Tóm tắt điều hành", "bullets": [
        {"text": "**EUA:** đóng cửa 79,20 EUR/tCO2.", "source_name": None, "source_url": None},  # bullet giá: không nguồn là hợp lệ
        {"text": "**Chính sách:** ...", "source_name": "Reuters", "source_url": A1},
    ]},
    "2": {
        "title": "Bảng giá nhanh",
        "price_timestamp": f"Giá chốt phiên {PRICE_DATE} (nguồn: Barchart EOD)",
        "prices": [
            _price_row("EUA Futures Dec'26", "EUA", 79.2, -0.5, 1.2),
            {"name": "CBAM", "code": "CBAM", "price": "70.0000 EUR/tCO2", "close": 70.0, "dday": "-", "dweek": "-"},
            _price_row("TTF", "TTF", 31.5, 2.1, -3.0, "EUR/MWh"),
        ],
        "key_developments": [{"text": "...", "impact": "giảm", "source_name": "Reuters", "source_url": A2}],
        "chart_data": [{"date": "2026-10-06", "close": 79.6}, {"date": PRICE_DATE, "close": 79.2}],
        "market_drivers": {
            "bullish": [{"tag": "FACT", "text": "...", "source_name": "Reuters", "source_url": A3}],
            "bearish": [{"tag": "FACT", "text": "...", "source_name": None, "source_url": None}],
        },
    },
    "3": {
        "title": "Phân tích",
        "analysis_blocks": [{"heading": "Phân tích", "content": "Chính sách: ...\n- ...\n" + SUMMARY}],
        "trading_scenarios": [
            {"horizon": "ngắn hạn", "probability": "Cao", "direction": "giảm", "condition": "Nếu ...",
             "price_zone": "78,5–80,0", "key_risk": "...",
             "trading_strategy": "**Entry:** Bán quanh 79,0–79,5 EUR/tCO2\n**Mục tiêu:** 76,5\n**Quản trị rủi ro:** Cắt lỗ nếu trên 81,0"},
            {"horizon": "trung hạn", "probability": "Trung bình", "direction": "đi ngang", "condition": "Nếu ...",
             "price_zone": "75–83", "key_risk": "...",
             "trading_strategy": "**Entry:** Mua quanh 75, bán quanh 83\n**Mục tiêu:** 82 / 76\n**Quản trị rủi ro:** Cắt lỗ dưới 73 hoặc trên 85"},
            {"horizon": "dài hạn", "probability": "Thấp", "direction": "tăng", "condition": "Nếu ...",
             "price_zone": "75–90", "key_risk": "...",
             "trading_strategy": "**Entry:** Mua quanh 75–76\n**Mục tiêu:** 85\n**Quản trị rủi ro:** Cắt lỗ dưới 72"},
        ],
    },
    "4": {"title": "Cập nhật tín chỉ carbon & CBAM", "bullets": [{"text": "**VCM:** ...", "source_name": None, "source_url": None}]},
    "6": {"title": "Chi tiết các tin tức chính",
          "international": [{"url": A1}, {"url": A2}, {"url": A3}], "vietnam": [{"url": VN1}]},
    "8": {"title": "Lịch sự kiện 7 ngày tới", "events": [
        {"date": "2026-10-07", "datetime_vn": "07/10", "event": "Tồn kho dầu thô EIA (EIA Weekly Petroleum Status Report)", "impact": "Cao"},
        {"date": "2026-10-10", "datetime_vn": "10/10", "event": "Số giàn khoan Baker Hughes (Baker Hughes Rig Count)", "impact": "Trung"},
    ]},
    "9": {"title": "Nguồn tham khảo", "items": [
        {"source": "Reuters", "title": "t", "url": A1}, {"source": "Reuters", "title": "t", "url": A2},
        {"source": "Reuters", "title": "t", "url": A3}, {"source": "VnExpress", "title": "t", "url": VN1},
    ]},
    "biz": {"title": "Gợi ý kinh doanh", "short_term": [
        {"trigger": "Nếu EUA đóng cửa dưới 75", "action": "Mua bổ sung", "reason": "r", "id": 11},
    ], "long_term": [{"opportunity": "o", "solution": "s", "expectation": "e", "id": 12}], "reminders": []},
}
DB_PRICES = {
    "EUA": {"name": "EUA Futures Dec'26", "close": 79.2, "day_change_pct": -0.5, "week_change_pct": 1.2},
    "TTF": {"name": "TTF", "close": 31.5, "day_change_pct": 2.1, "week_change_pct": -3.0},
}
SUGGESTIONS = {11: {"trigger_rule": {"code": "EUA", "op": "<", "value": 75}, "status": "pending", "first_report_date": REPORT_DATE}}
CODES = {"EUA", "TTF", "CBAM"}


def _art(url, region="international", topic=("eua_ets",), hot=False, source="Reuters"):
    return {"url": url, "source": source, "region": region, "topic": list(topic), "is_hot_news": hot}


ARTICLES = [_art(A1, hot=True), _art(A2), _art(A3, topic=("energy_gas",)),
            _art(VN1, region="vietnam", topic=("vietnam_carbon_policy",), source="VnExpress")]


def g():
    return copy.deepcopy(GOLDEN)


def run_all(c, db_prices=DB_PRICES, latest=PRICE_DATE, known=None, suggestions=SUGGESTIONS, articles=ARTICLES):
    known = {A1, A2, A3, VN1} if known is None else known
    news_issues, _ = report_qc.check_news(c, articles)
    return (
        report_qc.check_prices(c, db_prices, latest)
        + report_qc.check_scenarios(c)
        + report_qc.check_sources(c, known)
        + report_qc.check_calendar(c, REPORT_DATE)
        + report_qc.check_biz(c, suggestions, CODES, c["2"]["prices"], REPORT_DATE)
        + news_issues
    )


def msgs(issues):
    return " | ".join(f"{i['severity']}:{i['message']}" for i in issues)


# ── Không báo sai ────────────────────────────────────────────────────


def test_golden_report_has_no_issues():
    assert run_all(g()) == [], msgs(run_all(g()))


# ── Giá ──────────────────────────────────────────────────────────────


def test_price_close_mismatch_is_error():
    c = g()
    c["2"]["prices"][0]["close"] = 68.5
    issues = report_qc.check_prices(c, DB_PRICES, PRICE_DATE)
    assert any(i["severity"] == "error" and "68.50" in i["message"] and "79.20" in i["message"] for i in issues)


def test_price_displayed_pct_tampered():
    c = g()
    c["2"]["prices"][2]["dday"] = "+5.00% (+1.50)"  # sửa tay chuỗi hiển thị, số gốc vẫn đúng
    issues = report_qc.check_prices(c, DB_PRICES, PRICE_DATE)
    assert len(issues) == 1 and "hiển thị +5.00%" in issues[0]["message"]


def test_price_stale_session_and_missing_instrument():
    c = g()
    del c["2"]["prices"][2]
    issues = report_qc.check_prices(c, DB_PRICES, "2026-10-08")
    m = msgs(issues)
    assert "đã cũ" in m and "Thiếu TTF" in m


# ── Kịch bản ─────────────────────────────────────────────────────────


def _set_short(c, **kw):
    c["3"]["trading_scenarios"][0].update(kw)
    return c


def test_scenario_contradicts_summary():
    c = _set_short(g(), direction="tăng",
                   trading_strategy="**Entry:** Mua quanh 79\n**Mục tiêu:** 82\n**Quản trị rủi ro:** Cắt lỗ dưới 77")
    issues = report_qc.check_scenarios(c)
    assert [i["severity"] for i in issues] == ["error"] and "[TIÊU CỰC]" in issues[0]["message"]


def test_scenario_target_and_stop_wrong_side():
    c = _set_short(g(), trading_strategy="**Entry:** Bán quanh 79\n**Mục tiêu:** 82\n**Quản trị rủi ro:** Cắt lỗ nếu trên 77")
    m = msgs(report_qc.check_scenarios(c))
    assert "Mục tiêu 82.00 nằm sai phía" in m and "Cắt lỗ 77.00 nằm sai phía" in m


def test_scenario_strategy_bad_format_and_missing_line():
    c = _set_short(g(), trading_strategy="Bán quanh 79, chốt lời 76.5")
    assert "không đúng định dạng" in msgs(report_qc.check_scenarios(c))
    c = _set_short(g(), trading_strategy="**Entry:** Bán quanh 79\n**Mục tiêu:** 76")
    assert "thiếu dòng Quản trị rủi ro" in msgs(report_qc.check_scenarios(c))


def test_scenario_entry_far_from_price():
    c = _set_short(g(), trading_strategy="**Entry:** Bán quanh 95\n**Mục tiêu:** 90\n**Quản trị rủi ro:** Cắt lỗ nếu trên 98")
    assert "lệch >15%" in msgs(report_qc.check_scenarios(c))


def test_scenario_missing_horizon_and_bad_values():
    c = g()
    c["3"]["trading_scenarios"].pop()  # bỏ dài hạn
    c["3"]["trading_scenarios"][1].update(probability="cao vừa", direction="lên")
    m = msgs(report_qc.check_scenarios(c))
    assert 'Thiếu kịch bản "dài hạn"' in m and "cao vừa" in m and "error:Kịch bản trung hạn: chiều giá" in m


def test_scenario_summary_wrong_format():
    c = g()
    # Không in đậm → frontend không tìm thấy dòng Tổng hợp → khung Nhận định biến mất.
    c["3"]["analysis_blocks"][0]["content"] = "Chính sách: ...\nTổng hợp: [TIÊU CỰC] ..."
    assert "sai định dạng" in msgs(report_qc.check_scenarios(c))
    # Dấu ":" nằm ngoài ** → frontend vẫn bắt được dòng nhưng không đọc được nhãn → tô màu trung lập.
    c["3"]["analysis_blocks"][0]["content"] = "Chính sách: ...\n**Tổng hợp**: [TIÊU CỰC] ..."
    assert "thiếu nhãn" in msgs(report_qc.check_scenarios(c))


def test_signal_parser_matches_frontend_rules():
    lv = report_qc.parse_signal_levels("đi ngang", "Mua quanh 75, bán quanh 83", "82 / 76", "Cắt lỗ dưới 73 hoặc trên 85")
    assert lv["entry_buy"] == [75.0] and lv["entry_sell"] == [83.0]
    assert (lv["target_buy"], lv["target_sell"], lv["stop_lower"], lv["stop_upper"]) == (82.0, 76.0, 73.0, 85.0)
    # Bỏ %, số phiên, đơn vị — không nhầm là mức giá
    lv = report_qc.parse_signal_levels("tăng", "Mua quanh 78,5 EUR/tCO2 trong 2 phiên", "+3% lên 81", "dưới 77")
    assert lv["entry_buy"] == [78.5] and lv["target_buy"] == 81.0 and lv["stop_lower"] == 77.0


# ── Nguồn tin ────────────────────────────────────────────────────────


def test_source_unknown_url_everywhere():
    issues = report_qc.check_sources(g(), {VN1})
    assert {i["section"] for i in issues} == {"1", "3", "4", "9"}
    assert all(i["severity"] == "warning" for i in issues)


def test_source_duplicate_and_empty_section9():
    c = g()
    c["9"]["items"].append({"url": A1 + "/"})
    assert "trùng URL" in msgs(report_qc.check_sources(c, {A1, A2, A3, VN1}))
    c["9"]["items"] = []
    assert "Nguồn tham khảo trống" in msgs(report_qc.check_sources(c, {A1, A2, A3, VN1}))


# ── Lịch sự kiện ─────────────────────────────────────────────────────


def test_calendar_dst_aware_expected_dates():
    exp = report_qc.expected_recurring_dates("2026-10-07")
    assert "2026-10-07" in exp["eia"] and "2026-10-10" in exp["baker_hughes"]  # BH thứ Sáu CT = thứ Bảy VN
    assert "2026-09-16" in exp["eia"]  # nhìn lùi đủ xa cho sự kiện cũ đã có kết quả


def test_calendar_errors():
    c = g()
    c["8"]["events"] = [
        {"date": "2026-10-08", "datetime_vn": "08/10", "event": "Tồn kho dầu thô EIA", "impact": "Cao"},  # sai thứ
        {"date": "2026-10-10", "datetime_vn": "11/10", "event": "Số giàn khoan Baker Hughes", "impact": "Trung"},  # hiển thị sai ngày
        {"date": "2026-10-10", "datetime_vn": "10/10", "event": "Baker Hughes Rig Count", "impact": "Trung"},  # trùng
        {"date": "2026-10-05", "datetime_vn": "05/10", "event": "Họp ECB", "impact": "Rất cao"},  # đã qua, chưa có kết quả
        {"date": "2026-10-20", "datetime_vn": "20/10", "event": "Họp FOMC", "impact": "Cao"},  # ngoài cửa sổ
    ]
    m = msgs(report_qc.check_calendar(c, REPORT_DATE))
    for expected in ("error:Tồn kho dầu thô EIA: ngày 08/10 sai lịch", 'hiển thị "11/10"', "trùng sự kiện",
                     "chưa có kết quả", "ngoài cửa sổ", "Rất cao", "Thiếu sự kiện Tồn kho EIA (thứ Tư giờ Mỹ) ngày 07/10"):
        assert expected in m, expected


def test_calendar_old_event_with_outcome_is_ok():
    c = g()
    c["8"]["events"].insert(0, {"date": "2026-09-23", "datetime_vn": "23/09", "event": "Tồn kho dầu thô EIA",
                                "impact": "Cao", "outcome": "Giảm 1,2 triệu thùng"})
    assert report_qc.check_calendar(c, REPORT_DATE) == []


# ── Gợi ý kinh doanh ─────────────────────────────────────────────────


def _biz(suggestions, c=None):
    c = c or g()
    return msgs(report_qc.check_biz(c, suggestions, CODES, c["2"]["prices"], REPORT_DATE))


def test_biz_trigger_rule_problems():
    assert "ĐÃ chạm" in _biz({11: {**SUGGESTIONS[11], "trigger_rule": {"code": "EUA", "op": "<", "value": 90}}})
    assert "lệch >50%" in _biz({11: {**SUGGESTIONS[11], "trigger_rule": {"code": "EUA", "op": ">", "value": 790}}})
    assert "error:" in _biz({11: {**SUGGESTIONS[11], "trigger_rule": {"code": "XYZ", "op": "<", "value": 1}}})


def test_biz_memory_mismatch():
    assert "đã bị admin gỡ" in _biz({11: {**SUGGESTIONS[11], "status": "dismissed"}})
    assert "thuộc báo cáo 2026-10-01" in _biz({11: {**SUGGESTIONS[11], "first_report_date": "2026-10-01"}})
    assert "không còn trong bộ nhớ" in _biz({})


def test_biz_missing_fields():
    c = g()
    c["biz"]["short_term"][0]["action"] = ""
    c["biz"]["long_term"][0]["solution"] = " "
    m = _biz(SUGGESTIONS, c)
    assert "error:Gợi ý ngắn hạn #1: thiếu hành động" in m and "error:Gợi ý dài hạn #1: thiếu giải pháp" in m


# ── Tin tức ──────────────────────────────────────────────────────────


def test_news_stats():
    _, stats = report_qc.check_news(g(), ARTICLES)
    assert (stats["total"], stats["international"], stats["vietnam"], stats["hot"], stats["cited"]) == (4, 3, 1, 1, 3)


def test_news_empty_window_is_error():
    issues, stats = report_qc.check_news(g(), [])
    assert stats["total"] == 0 and issues[0]["severity"] == "error"


def test_news_coverage_gaps():
    c = g()
    c["6"]["vietnam"] = []
    extra = [_art(f"https://x.com/oil-{i}", topic=("energy_oil",)) for i in range(3)]
    extra += [_art(f"https://x.com/vcm-{i}", topic=("vcm",)) for i in range(3)]
    extra += [_art("https://x.com/untagged", topic=())]
    m = msgs(report_qc.check_news(c, ARTICLES + extra)[0])
    assert "Việt Nam trống" in m and "chưa được gắn chủ đề" in m
    assert '"Dầu" có 3 bài' in m
    assert "VCM" not in m  # VCM không thuộc phạm vi Mục 1 → không trích là đúng


def test_news_generator_limit_counts_untagged_first():
    # 100 bài mới nhất chưa gắn topic → bộ sinh bỏ hết, 4 bài có topic cũ hơn cũng không được đọc
    articles = [_art(f"https://x.com/u{i}", topic=()) for i in range(100)] + ARTICLES
    m = msgs(report_qc.check_news(g(), articles)[0])
    assert "4 bài có chủ đề bị bỏ qua" in m


def test_news_source_outside_window():
    c = g()
    c["1"]["bullets"][1]["source_url"] = "https://x.com/old"
    issues = report_qc.check_news(c, ARTICLES)[0]
    assert [i["section"] for i in issues if "không thuộc khung tin" in i["message"]] == ["1"]


# ── Điểm + văn bản + quyền ───────────────────────────────────────────


def test_summarize_scores():
    issues = {"price": [{"severity": "error"}], "scenario": [{"severity": "warning"}, {"severity": "info"}]}
    res = report_qc.summarize(issues)
    assert res["scores"] == {"price": 75, "scenario": 88, "source": 100, "calendar": 100, "biz": 100, "news": 100}
    assert res["overall_score"] == round((75 + 88 + 400) / 6)


def test_format_qc_report():
    issues = {"price": [{"check": "price", "section": "2", "severity": "error", "message": "Giá EUA lệch.", "field_path": "2"}]}
    res = report_qc.summarize(issues)
    res["news_stats"] = report_qc.check_news(g(), ARTICLES)[1]
    text = report_qc.format_qc_report(REPORT_DATE, "draft", res)
    assert "ĐIỂM TỔNG" in text and "[Bảng giá nhanh]" in text and "- LỖI: Giá EUA lệch." in text
    assert "Tổng 4 bài (quốc tế 3, Việt Nam 1)" in text


def test_qc_tool_is_admin_only():
    from services import quote_chat
    assert "lucy_qc" not in {t["name"] for t in quote_chat.CLIENT_TOOLS}  # MCP gateway / role user không thấy
    assert "lucy_qc" in {t["name"] for t in quote_chat.ADMIN_TOOLS}
    out = asyncio.run(quote_chat._execute_client_tool("lucy_qc", {}, None, REPORT_DATE, tool_cache={}, chart_cache={}))
    assert "chỉ dành cho quản trị viên" in out
