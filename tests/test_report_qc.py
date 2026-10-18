"""Test phần THUẦN của Jack QC — services/report_qc.py (không đụng DB/LLM).

GOLDEN là 1 báo cáo SẠCH dựng đúng định dạng generate_report_content() xuất ra — phải
ra 0 vấn đề (không báo sai). Các test còn lại cài từng loại lỗi vào bản sao của GOLDEN
và kiểm Jack bắt đúng lỗi đó (không bỏ sót).
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
             "trading_strategy": "**Entry:** Bán quanh 79,0–79,5 EUR/tCO2\n**Mục tiêu:** 75,5 (hỗ trợ 30 phiên)\n**Quản trị rủi ro:** Cắt lỗ nếu trên 81,0"},
            {"horizon": "trung hạn", "probability": "Trung bình", "direction": "đi ngang", "condition": "Nếu ...",
             "price_zone": "75–83", "key_risk": "...",
             "trading_strategy": "**Entry:** Mua quanh 75, bán quanh 83\n**Mục tiêu:** 82 / 76\n**Quản trị rủi ro:** Cắt lỗ dưới 73 hoặc trên 85"},
            {"horizon": "dài hạn", "probability": "Thấp", "direction": "tăng", "condition": "Nếu ...",
             "price_zone": "75–90", "key_risk": "...",
             "trading_strategy": "**Entry:** Mua quanh 75–76\n**Mục tiêu:** 85 (kháng cự 30 phiên)\n**Quản trị rủi ro:** Cắt lỗ dưới 72"},
        ],
        "hedge_action": {"action": "hold", "core": "Phần lõi: mua theo tỷ trọng lịch đấu giá.",
                         "text": "Phần chiến thuật (≤20%, quan điểm giao dịch): không dùng hôm nay."},
        "open_positions": [],
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
    ], "long_term": [{"opportunity": "o", "solution": "s", "expectation": "e", "id": 12,
                     "source_name": "Reuters", "source_url": A1}], "reminders": []},
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


# Nội dung bài gốc cho check_facts (bullet GOLDEN chỉ có "...", không có số → không báo).
ARTICLE_TEXTS = {A1: "EU lawmakers agree ETS changes", A2: "Gas prices fall", A3: "Heatwave lifts power demand"}


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
        + report_qc.check_facts(c, ARTICLE_TEXTS)
        + report_qc.check_instrument_labels(c)
        + report_qc.check_hedge_action(c)
        + report_qc.check_consistency(c)
        + report_qc.check_dev_driver_consistency(c)
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
                   trading_strategy="**Entry:** Mua quanh 79\n**Mục tiêu:** 82 (kháng cự 30 phiên)\n**Quản trị rủi ro:** Cắt lỗ dưới 77")
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
    assert res["scores"] == {"price": 75, "scenario": 88, "source": 100, "fact": 100, "calendar": 100, "biz": 100, "news": 100}
    assert res["overall_score"] == round((75 + 88 + 500) / 7)


def test_format_qc_report():
    issues = {"price": [{"check": "price", "section": "2", "severity": "error", "message": "Giá EUA lệch.", "field_path": "2"}]}
    res = report_qc.summarize(issues)
    res["news_stats"] = report_qc.check_news(g(), ARTICLES)[1]
    text = report_qc.format_qc_report(REPORT_DATE, "draft", res)
    assert "ĐIỂM TỔNG" in text and "[Bảng giá nhanh]" in text and "- LỖI: Giá EUA lệch." in text
    assert "Tổng 4 bài (quốc tế 3, Việt Nam 1)" in text


def test_qc_tool_is_admin_only():
    from services import quote_chat
    assert "jack_qc" not in {t["name"] for t in quote_chat.CLIENT_TOOLS}  # MCP gateway / role user không thấy
    assert "jack_qc" in {t["name"] for t in quote_chat.ADMIN_TOOLS}
    out = asyncio.run(quote_chat._execute_client_tool("jack_qc", {}, None, REPORT_DATE, tool_cache={}, chart_cache={}))
    assert "chỉ dành cho quản trị viên" in out


def test_jack_qc_exposed_on_mcp_gateway():
    from api.routers import mcp_gateway
    names = {t["name"] for t in mcp_gateway._DESKTOP_TOOLS}
    assert "jack_qc" in names and "jack_qc" in mcp_gateway._TOOLS_BY_NAME
    desc = next(t["description"] for t in mcp_gateway._DESKTOP_TOOLS if t["name"] == "jack_qc")
    assert "HOÁ VAI JACK" in desc and "Lucy" not in desc


# ── Khối lượng EUA: phiên volume 0 không được kéo TB về 0 ──────────────


def _chart(volumes):
    return [
        {"date": f"2026-10-{i + 1:02d}", "open": 88.0, "high": 89.0, "low": 87.0, "close": 88.5, "volume": v}
        for i, v in enumerate(volumes)
    ]


def test_eua_volume_summary_ignores_zero_volume():
    from services.report_generator import _eua_volume_summary

    text = _eua_volume_summary(_chart([1000, 0, 1000, 1200]))
    assert "TB 2 phiên" in text and "(0)" not in text


def test_eua_volume_summary_all_zero_has_no_signal():
    from services.report_generator import _eua_volume_summary

    assert "KHÔNG nêu" in _eua_volume_summary(_chart([0, 0, 0]))


def test_baker_hughes_label_notes_us_date():
    from services.report_generator import _compute_recurring_calendar_events

    evs = _compute_recurring_calendar_events("2026-10-14")
    bh = [e for e in evs if "Baker Hughes" in e["event"]]
    assert bh and bh[0]["date"] == "2026-10-17" and "Thứ Sáu 16/10 12:00 giờ Chicago" in bh[0]["event"]


def test_calendar_window_and_us_holiday_shift():
    """Báo cáo 15/10/2026 (dữ liệu 14/10): Columbus Day 12/10 → EIA lùi sang Thứ Năm 15/10;
    cửa sổ 7 ngày phải có cả EIA 21/10."""
    from services.report_generator import _compute_recurring_calendar_events

    eia = [e["date"] for e in _compute_recurring_calendar_events("2026-10-14") if "EIA" in e["event"]]
    assert eia == ["2026-10-15", "2026-10-21"]


def test_eia_outcome_must_match_event_week():
    from datetime import date
    from services.report_generator import data_matches_event

    assert data_matches_event("Tồn kho dầu thô EIA", date(2026, 10, 15), "2026-10-09")
    assert not data_matches_event("Tồn kho dầu thô EIA", date(2026, 10, 15), "2026-10-02")
    assert data_matches_event("Số giàn khoan Baker Hughes", date(2026, 10, 17), "2026-10-16")


def test_calendar_flags_outcome_from_previous_week():
    c = g()
    c["8"]["events"].append({
        "date": "2026-10-15", "datetime_vn": "15/10", "event": "Tồn kho dầu thô EIA (EIA Weekly Petroleum Status Report)",
        "impact": "Cao", "outcome": "Tồn kho 424.134 Mbbl (tuần kết thúc 02/10/2026)",
    })
    m = msgs(report_qc.check_calendar(c, "2026-10-16"))
    assert "tuần kết thúc 02/10" in m


# ── Spread than–khí, CJK, gộp tin trùng ────────────────────────────────


def _px(**kw):
    return [{"code": k, "close": v} for k, v in kw.items()]


def test_fuel_spread_matches_client_estimate():
    from services.report_generator import _fuel_switching_spread_summary

    text = _fuel_switching_spread_summary(_px(TTF=87.0, API2=100.0, EUA=88.05))
    # khách ước tính khí ≈ 190 EUR/MWh (than ≈ 112 với than = 100 USD/t)
    assert "Chạy khí (TTF 87.00 EUR/MWh, CCGT hiệu suất 55%) ≈ 191" in text
    assert "ĐÃ BÃO HÒA" in text and "KẾT LUẬN DÙNG CHUNG" in text
    # nhãn cũ khiến báo cáo trông như chưa kiểm chứng — chỉ còn xuất hiện trong câu cấm
    assert "ước tính thô, chưa kiểm chứng" not in text


def test_fuel_spread_ignores_api4():
    """Fuel switching EU phải dùng API2 (CIF ARA) — API4 (Richards Bay) là chuẩn châu Á."""
    from services.report_generator import _fuel_switching_spread_summary

    only_api4 = _fuel_switching_spread_summary(_px(TTF=87.0, API4=100.0, EUA=88.05))
    assert only_api4.startswith("Thiếu giá than API2")
    both = _fuel_switching_spread_summary(_px(TTF=87.0, API2=100.0, API4=80.0, EUA=88.05))
    assert "API4 80" not in both and "API2 CIF ARA 100.0" in both


def test_fuel_spread_missing_inputs():
    from services.report_generator import _fuel_switching_spread_summary

    assert "Thiếu" in _fuel_switching_spread_summary(_px(TTF=87.0, EUA=88.0))


def test_has_cjk():
    from services.report_generator import _has_cjk

    assert _has_cjk("chiến l略c") and _has_cjk({"a": ["碳"]})
    assert not _has_cjk("chiến lược hạn ngạch phát thải EU")


def test_dedupe_similar_articles():
    from services.report_generator import _dedupe_similar_articles

    arts = [
        {"title": "Trump Putin agree diesel deal on Russian exports", "url": "1"},
        {"title": "Trump and Putin diesel deal agreed on Russian exports", "url": "2"},
        {"title": "EU ETS auction volumes cut by MSR", "url": "3"},
    ]
    assert [a["url"] for a in _dedupe_similar_articles(arts)] == ["1", "3"]


# ── Tin cũ / nhãn nguồn / gợi ý kích hoạt sai / số học kịch bản ─────────

def test_source_label_is_real_publisher():
    from services.report_generator import news_source_label

    # Nguồn cấu hình crawl chỉ là kênh thu tin, không phải nguồn tin → không ghi kèm
    assert news_source_label("bloomberg.com", "https://www.business-standard.com/x") == "business-standard.com"
    assert news_source_label("reuters.com", "https://www.reuters.com/a") == "reuters.com"


def test_sources_flag_stale_article_and_wrong_label():
    from datetime import datetime, timezone

    url = "https://www.business-standard.com/vessel-sinks"
    c = g()
    c["9"]["items"].append({"source": "bloomberg.com", "title": "Vessel sinks", "url": url})
    meta = {report_qc._norm_url(url): {"source": "bloomberg.com", "published_at": datetime(2026, 5, 14, tzinfo=timezone.utc)}}
    issues = report_qc.check_sources(
        c, report_qc.collect_source_urls(c), meta, datetime(2026, 10, 11, tzinfo=timezone.utc),
    )
    m = msgs(issues)
    assert "tin CŨ — đăng 14/05/2026" in m and "không đúng nhà xuất bản" in m


def test_biz_reminder_triggered_below_threshold_is_error():
    prices = [{"code": "EUA", "name": "EUA", "close": 88.34}]
    content = {"biz": {"short_term": [], "reminders": [
        {"id": 1, "suggested_date": "2026-10-09", "outcome": "triggered",
         "trigger": "Nếu EUA đóng cửa trên 88,53 EUR/t", "source_url": "https://x"},
        {"id": 2, "suggested_date": "2026-10-10", "outcome": "triggered",
         "trigger": "Nếu NG vượt 3,60 USD/MMBtu", "source_url": "https://y"},
    ]}}
    issues = [i for i in report_qc.check_biz(content, {}, {"EUA"}, prices) if i["severity"] == "error"]
    assert len(issues) == 2
    assert "CHƯA chạm" in issues[0]["message"] and "không kiểm chứng được" in issues[1]["message"]


def test_infer_price_rule_from_trigger_text():
    from services.biz_memory import infer_price_rule, looks_like_price_condition

    prices = [{"code": "EUA", "name": "EU Carbon", "close": 87.1}, {"code": "BRENT", "name": "Brent", "close": 70}]
    assert infer_price_rule("Khi EUA đóng cửa trên 88,53 EUR/t", prices) == {"code": "EUA", "op": ">", "value": 88.53}
    assert infer_price_rule("Nếu NG vượt 3,60 USD/MMBtu", prices) is None
    assert looks_like_price_condition("Nếu NG vượt 3,60 USD/MMBtu")
    assert not looks_like_price_condition("Khi xác nhận nội dung và phạm vi ngừng bắn năng lượng")


def test_scenario_math_condition_rr_and_tight_stop():
    c = g()
    c["2"]["chart_data"] = [{"open": 87.5, "high": 88.24, "low": 86.62, "close": 87.14}]
    c = _set_short(
        c, direction="tăng", condition="Nếu EUA đóng cửa trên 88,24",
        trading_strategy="**Entry:** mua quanh 87,0–87,5\n**Mục tiêu:** 88,82 (đo biên độ)\n**Quản trị rủi ro:** cắt lỗ dưới 86,6",
    )
    m = msgs(report_qc.check_scenarios(c))
    assert "mâu thuẫn với Entry" in m and "R:R chỉ 0.35:1" in m and "dễ bị quét" in m


def test_no_trade_and_vn_number_format():
    from types import SimpleNamespace
    from services.report_generator import _is_no_trade_session, _summarize_prices, _vn_num

    flat = SimpleNamespace(open_price=132.0, high_price=132.0, low_price=132.0, close_price=132.0, volume=0)
    traded = SimpleNamespace(open_price=130.0, high_price=133.0, low_price=129.5, close_price=132.0, volume=120)
    assert _is_no_trade_session(flat) and not _is_no_trade_session(traded)
    assert _vn_num(1386.75) == "1.386,75" and _vn_num(-3.054, sign=True) == "-3,05"
    text = _summarize_prices([{
        "name": "Coal API4", "code": "API4", "price": "1,132.0000 USD/t", "close": 1132.0,
        "dday": "+1.23% (+13.70)", "dweek": "-0.32% (-3.60)", "day_change_pct": 1.23, "week_change_pct": -0.32,
        "no_trade": True, "revision": "Dữ liệu phiên 14/10 đã được nguồn điều chỉnh",
    }])
    assert "1.132,00 USD/t" in text and "+1,23% (+13,70)" in text
    assert "KHÔNG có giao dịch" in text and "đã được nguồn điều chỉnh" in text


def test_stop_inside_noise_flagged_for_all_horizons():
    from services.report_qc import _check_strategy

    sc = {
        "horizon": "trung hạn", "direction": "tăng", "condition": "Nếu EUA giữ trên 82,5",
        "trading_strategy": "**Entry:** Mua quanh 82,5\n**Mục tiêu:** 88,8 (kháng cự 30 phiên)\n**Quản trị rủi ro:** Cắt lỗ dưới 82,35",
    }
    issues = _check_strategy(sc, "Kịch bản trung hạn", "3.trading_scenarios[1]", 85.0, {"high": 86.0, "low": 84.5, "atr": 1.8})
    assert any("nằm trong nhiễu" in it["message"] for it in issues)


def test_technical_levels_include_near_levels():
    from services.report_generator import _eua_technical_levels_summary

    chart = [{"high": 80 + i * 0.3 + 1, "low": 80 + i * 0.3 - 1, "close": 80 + i * 0.3} for i in range(30)]
    text = _eua_technical_levels_summary(chart)
    assert "10 phiên gần nhất" in text


def test_prompts_render():
    """Các prompt sửa đổi dựng được (không lỗi f-string/KeyError)."""
    from services import report_generator as rg

    _, biz = rg._prompt_biz_recommendation("[1] tin", "giá", "xu hướng", "2026-10-15", valid_codes="EUA")
    assert "97,5%" in biz and "phát thải TRỰC TIẾP" in biz and "qtd_avg" in biz
    _, s3 = rg._prompt_section3("[1] tin", "giá", "xu hướng", "biên độ", "crack", "2026-10-15", [])
    assert "hedge_action" in s3 and "open_positions" in s3 and "vs_previous" in s3 and "Chỉ thị (EU) 2023/959" in s3
    assert '"hedge_note": "..."' not in s3
    _, s1 = rg._prompt_section1("[1] tin", "giá", "xu hướng", "biên độ", "2026-10-15", fuel_spread="KẾT LUẬN DÙNG CHUNG: x")
    assert "KẾT LUẬN DÙNG CHUNG" in s1


# ── Chuỗi nguồn gốc → nơi đăng lại (lỗi (d)) ──────────────────────────


def test_source_chain_label_and_apply():
    from services.report_generator import apply_source_chains, source_chain_label

    assert source_chain_label("Financial Times", "politico.com") == "Financial Times (gốc) → politico.com"
    assert source_chain_label(None, "politico.com") == "politico.com"
    assert source_chain_label("Reuters", "reuters.com") == "reuters.com"  # tự đưa tin
    content = {
        "1": {"bullets": [{"text": "x", "source_name": "politico.com", "source_url": "u1"}]},
        "9": {"items": [{"source": "politico.com", "url": "u1"}, {"source": "reuters.com", "url": "u2"}]},
    }
    apply_source_chains(content, {"u1": "Financial Times (gốc) → politico.com"})
    assert content["1"]["bullets"][0]["source_name"] == "Financial Times (gốc) → politico.com"
    assert content["9"]["items"][0]["source"] == "Financial Times (gốc) → politico.com"
    assert content["9"]["items"][1]["source"] == "reuters.com"


def test_sources_accept_chain_label():
    c = g()
    c["1"]["bullets"][1]["source_name"] = "Financial Times (gốc) → x.com"
    meta = {A1: {"source": "x.com", "published_at": None}}
    bullet_issues = lambda: [i for i in report_qc.check_sources(c, {A1, A2, A3, VN1}, meta) if i["section"] == "1"]
    assert bullet_issues() == []
    c["1"]["bullets"][1]["source_name"] = "Bloomberg"
    assert any("nhãn nguồn" in i["message"] for i in bullet_issues())


# ── Jack: số liệu ↔ tin gốc ───────────────────────────────────────────


def test_facts_flag_number_missing_from_source():
    c = g()
    c["1"]["bullets"][1]["text"] = "**IEA:** còn 100 triệu thùng dự trữ khẩn cấp, đã giải phóng 330 triệu."
    texts = {A1: "The IEA said 330 million barrels have been released so far."}
    issues = report_qc.check_facts(c, texts)
    assert len(issues) == 1 and "100" in issues[0]["message"] and "330" not in issues[0]["message"]
    assert issues[0]["check"] == "fact" and issues[0]["severity"] == "warning"


def test_facts_allow_rounding_formats_prices_and_small_counts():
    c = g()
    c["1"]["bullets"][1]["text"] = (
        "**Dầu:** còn khoảng 96 triệu thùng (46 triệu ở châu Âu), 1.386,75 USD/tấn; 3 tháng tới; năm 2026; "
        "EUA 79,20 EUR/t; ngày 15/10."
    )
    texts = {A1: "About 95.8 million barrels remain, 46m in Europe; gasoil at 1,386.75 per tonne."}
    assert report_qc.check_facts(c, texts) == []


def test_facts_flag_self_reported_mismatch():
    c = g()
    c["1"]["bullets"][1]["text"] = "**IEA:** còn 100 triệu thùng (số liệu trong bài chưa khớp: 400 − 330 = 70)."
    issues = report_qc.check_facts(c, {})
    assert any("chưa khớp" in i["message"] for i in issues)


# ── Jack: nguồn của gợi ý kinh doanh (lỗi (e)) ───────────────────────


def test_biz_long_term_without_source_or_unlisted_source():
    c = g()
    c["biz"]["long_term"][0].pop("source_url")
    assert any("dài hạn #1: không có nguồn" in i["message"] for i in report_qc.check_biz_sources(c, SUGGESTIONS))
    c["biz"]["long_term"][0]["source_url"] = "https://example.com/not-listed"
    assert any("không có trong Tin tức chi tiết" in i["message"] for i in report_qc.check_biz_sources(c, SUGGESTIONS))


def test_biz_short_term_news_trigger_needs_source_price_trigger_does_not():
    c = g()
    c["biz"]["short_term"].append({"trigger": "Nếu EU thông qua MSR mới", "action": "a", "reason": "r"})
    issues = report_qc.check_biz_sources(c, SUGGESTIONS)
    assert len(issues) == 1 and "ngắn hạn #2" in issues[0]["message"]


# ── Jack: nhãn mã instrument ↔ giá thật ──────────────────────────────


def _with_coal(c):
    c["2"]["prices"].append(_price_row("API4 Coal Richards Bay", "API4", 132.0, 1.2, 2.3, "USD/MT"))
    return c


def test_label_number_belongs_to_other_instrument_is_error():
    c = _with_coal(g())
    c["3"]["analysis_blocks"][0]["content"] += "\n- Than API2 đứng ở 132,0 USD/t, rẻ hơn khí."
    issues = report_qc.check_instrument_labels(c)
    assert any(i["severity"] == "error" and "API2" in i["message"] and "API4" in i["message"] for i in issues)


def test_label_price_for_missing_instrument_is_warning():
    c = g()
    c["3"]["analysis_blocks"][0]["content"] += "\n- Than API2 ở mức 149,2 USD/t."
    issues = report_qc.check_instrument_labels(c)
    assert len(issues) == 1 and issues[0]["severity"] == "warning" and "không có API2" in issues[0]["message"]


def test_label_ok_when_both_codes_in_sentence_or_own_price():
    c = _with_coal(g())
    c["3"]["analysis_blocks"][0]["content"] += (
        "\n- API4 132,0 USD/t (chuẩn châu Á) so với TTF 31,5 EUR/MWh.\n- EUA đóng cửa 79,20 EUR/t."
    )
    assert report_qc.check_instrument_labels(c) == []


def test_instrument_labels_match_tracked_data():
    """Nhãn mã trong mô tả tool Jenny/MCP phải là mã thật có nguồn giá (instruments.yaml) — và API2 thật sự
    được crawl (nguồn ICE), không phải nhãn treo trên dữ liệu API4."""
    import pathlib

    import yaml

    from services.quote_chat import TRACKED_INSTRUMENTS_LABEL

    cfg = yaml.safe_load((pathlib.Path(__file__).parents[1] / "instruments.yaml").read_text(encoding="utf-8"))
    codes = {i["code"] for i in cfg["instruments"]}
    for code in ("EUA", "TTF", "BRENT", "WTI", "GASOIL", "API2", "API4", "DEBY1", "NG"):
        assert code in codes
    for label_code, code in (("EUA", "EUA"), ("TTF", "TTF"), ("Brent", "BRENT"), ("WTI", "WTI"), ("Gasoil", "GASOIL"),
                             ("API2", "API2"), ("API4", "API4"), ("DEBY1", "DEBY1"), ("NG", "NG")):
        assert label_code in TRACKED_INSTRUMENTS_LABEL and code in codes
    api2 = next(i for i in cfg["instruments"] if i["code"] == "API2")
    assert api2.get("symbol_ice", "").startswith("ICE:")


def test_label_matches_code_family_with_tenor_suffix():
    c = g()
    c["2"]["prices"][0]["code"] = "EUA_DEC26"  # báo cáo cũ: mã kèm kỳ hạn
    c["3"]["analysis_blocks"][0]["content"] += "\n- Giá EUA Futures Dec'26 đóng cửa ở mức 79,20 EUR/tCO2."
    assert report_qc.check_instrument_labels(c) == []


# ── Nhận xét bản 17/10: (a) IEA 100 vs 96, (b) API2 tăng xếp sai chiều, (c) chân bán thiếu cắt lỗ ──


def test_two_sided_strategy_sell_leg_without_stop_and_target_is_error():
    c = g()
    sc = c["3"]["trading_scenarios"][0]
    sc.update(direction="đi ngang", trading_strategy=(
        "**Entry:** Mua quanh 82,5–83,0, bán quanh 88,0–88,5\n**Mục tiêu:** 88,0 (biên trên)\n**Quản trị rủi ro:** Cắt lỗ dưới 81,52"
    ))
    issues = [i for i in report_qc.check_scenarios(c) if "chân bán" in i["message"]]
    assert len(issues) == 1 and issues[0]["severity"] == "error"
    assert "mục tiêu và cắt lỗ" in issues[0]["message"] and issues[0]["field_path"].endswith(".trading_strategy")


def test_two_sided_strategy_complete_legs_ok_and_wrong_side_flagged():
    c = g()
    assert not [i for i in report_qc.check_scenarios(c) if "chân" in i["message"]]  # GOLDEN trung hạn: đủ 2 chân
    c["3"]["trading_scenarios"][1]["trading_strategy"] = (
        "**Entry:** Mua quanh 75, bán quanh 83\n**Mục tiêu:** 82 / 86\n**Quản trị rủi ro:** Cắt lỗ dưới 73 hoặc trên 85"
    )
    assert any("chân bán" in i["message"] and "sai phía" in i["message"] for i in report_qc.check_scenarios(c))


def test_fuel_direction_api2_rise_as_bullish_is_error():
    c = g()
    c["2"]["market_drivers"]["bullish"].append({"tag": "FACT", "text": "Giá than API2 tăng 2,19% trong tuần, hỗ trợ cầu EUA."})
    issues = report_qc.check_fuel_direction(c)
    assert len(issues) == 1 and issues[0]["severity"] == "error" and "sai chiều" in issues[0]["message"]
    # So với khí (chênh khí − than mở rộng) thì hợp lệ
    c["2"]["market_drivers"]["bullish"][-1]["text"] = "TTF tăng 9,62% mạnh hơn API2 (+2,19%) → chênh khí − than mở rộng."
    assert report_qc.check_fuel_direction(c) == []
    c["2"]["market_drivers"]["bearish"].append({"tag": "FACT", "text": "API2 giảm 3% tuần qua."})
    assert len(report_qc.check_fuel_direction(c)) == 1


def test_remaining_math_conflict_and_section6_mismatch():
    c = g()
    c["1"]["bullets"][1]["text"] = "**Dầu:** IEA đã rút 330/400 triệu thùng dự trữ khẩn cấp, còn lại 100 triệu thùng."
    c["6"]["international"].append({"url": "https://argus.test/x", "source": "argusmedia.com",
                                    "summary": "Theo Argus, kho dự trữ khẩn cấp còn khoảng 96 triệu thùng, trong đó 46 triệu ở châu Âu."})
    issues = report_qc.check_facts(c, {})
    msgs_ = msgs(issues)
    assert any(i["severity"] == "error" and "còn 70" in i["message"] for i in issues), msgs_
    assert any(i["severity"] == "warning" and "96" in i["message"] and "argusmedia.com" in i["message"] for i in issues), msgs_


def test_remaining_consistent_numbers_not_flagged():
    c = g()
    c["1"]["bullets"][1]["text"] = "**Dầu:** đã rút 304/400 triệu thùng, còn khoảng 96 triệu thùng (theo Argus)."
    c["6"]["international"].append({"url": "u", "source": "argusmedia.com", "summary": "còn khoảng 96 triệu thùng"})
    assert [i for i in report_qc.check_facts(c, {}) if "còn" in i["message"]] == []


def test_generator_revises_figure_conflict(monkeypatch):
    import asyncio

    from services import report_generator as rg

    c = g()
    c["1"]["bullets"][1]["text"] = "**Dầu:** IEA đã rút 330/400 triệu thùng, còn lại 100 triệu thùng."
    c["6"]["international"].append({"url": "u", "source": "argusmedia.com", "title": "IEA stocks",
                                    "summary": "Kho dự trữ còn khoảng 96 triệu thùng."})
    assert set(rg._figure_conflicts(c)) == {"1.1"}

    async def fake_llm(prompt, **kw):
        assert "argusmedia.com" in prompt and "[1.1]" in prompt
        return '{"items": [{"ref": "1.1", "text": "**Dầu:** Theo Argus, kho dự trữ khẩn cấp còn khoảng 96 triệu thùng (100 triệu trong tiêu đề là số làm tròn)."}]}'

    monkeypatch.setattr(rg, "_call_llm", fake_llm)
    asyncio.run(rg._revise_figure_conflicts(c))
    assert "96 triệu" in c["1"]["bullets"][1]["text"] and rg._figure_conflicts(c) == {}


def test_fuel_spread_weekly_shift_and_direction_rule():
    from services import report_generator as rg

    prices = [{"code": "TTF", "close": 86.43, "week_change_pct": 9.62},
              {"code": "EUA", "close": 85.84, "week_change_pct": -1.20},
              {"code": "API2", "close": 149.2, "week_change_pct": 2.19}]
    text = rg._fuel_switching_spread_summary(prices)
    assert "TTF +9,62%, API2 +2,19%" in text and "lợi thế của than CỦNG CỐ" in text
    assert "\"API2 tăng thu hẹp lợi thế điện than\" là SAI" in text
    assert "không tạo thêm lực tăng" in text  # đã bão hòa
    # Bão hòa áp dụng ĐỐI XỨNG: TTF tăng không là động lực tăng, API2 tăng không là áp lực giảm.
    assert "\"API2 tăng\" KHÔNG phải động lực giảm" in text and "\"TTF tăng\" KHÔNG phải động lực tăng" in text
    assert "Giả định mô hình" in text and "1 EUR = 1,15 USD" in text


def _fuel_content(bullish=(), bearish=()):
    c = g()
    c["2"]["prices"] = [
        {"code": "TTF", "close": 86.43, "week_change_pct": 9.62},
        {"code": "EUA", "close": 85.84, "week_change_pct": -1.20},
        {"code": "API2", "close": 149.2, "week_change_pct": 2.19},
    ]
    c["2"]["market_drivers"] = {"bullish": [{"text": t} for t in bullish], "bearish": [{"text": t} for t in bearish]}
    return c


def test_jack_fuel_direction_symmetric_when_saturated():
    issues = report_qc.check_fuel_direction(_fuel_content(
        bullish=["Estonia: nguy cơ gián đoạn nguồn cung khí đẩy TTF tăng"],
        bearish=["API2 tăng 2,19% trong tuần, thu hẹp lợi thế điện than"],
    ))
    assert len(issues) == 2, msgs(issues)
    assert "MỞ RỘNG" in issues[1]["message"] and "bão hòa" in issues[0]["message"]
    # Đã bão hòa: "chênh mở rộng" chỉ giữ mức, không phải động lực tăng (bản chấm 17/10).
    widened = report_qc.check_fuel_direction(_fuel_content(bullish=["Chênh khí − than mở rộng, TTF tăng mạnh hơn API2"]))
    assert len(widened) == 1 and "GIỮ MỨC" in widened[0]["message"]


# ── Nhất quán số học / 1 hành động phòng hộ (bản chấm 16/10/2026) ─────


def test_wrong_pct_label_is_flagged_and_auto_fixed():
    from services.report_consistency import apply_consistency_fixes

    c = g()
    c["3"]["trading_scenarios"][1]["trading_strategy"] = (
        "**Entry:** Bán quanh 79,0\n**Mục tiêu:** 72,26 (-5% so với đóng cửa 79,20)\n**Quản trị rủi ro:** Cắt lỗ trên 81,0"
    )
    issues = report_qc.check_consistency(c)
    assert any(i["severity"] == "error" and "-8,8%" in i["message"] and "75,24" in i["message"] for i in issues), msgs(issues)
    fixed = apply_consistency_fixes(c)
    assert fixed and "72,26 (-8,8% so với đóng cửa 79,20)" in c["3"]["trading_scenarios"][1]["trading_strategy"]
    assert report_qc.check_consistency(c) == []


def test_range_half_claim_is_recomputed():
    c = g()
    c["2"]["chart_data"] = [{"date": "d", "high": 84.0, "low": 76.0, "close": 79.2}]
    c["1"]["bullets"][0]["text"] = "**EUA:** giá hồi về nửa trên biên độ 76,00–84,00."
    issues = report_qc.check_consistency(c)
    assert any("nửa dưới" in i["message"] and "40,0%" in i["message"] for i in issues), msgs(issues)


def test_presentation_normalized():
    from services.report_consistency import normalize_presentation

    assert normalize_presentation("Kháng cự 88,82–88,82, giá 85.84 ngày 5 October 2026") == "Kháng cự 88,82, giá 85,84 ngày 05/10/2026"
    assert normalize_presentation("1.386,75 và 16.10.2026") == "1.386,75 và 16.10.2026"


def test_hedge_action_required_and_open_trade_must_be_reconciled():
    c = g()
    del c["3"]["hedge_action"]
    assert any("hedge_action" in i["field_path"] for i in report_qc.check_hedge_action(c))
    c = g()
    c["2"]["forecast_review"] = {"items": [{
        "report_date": "2026-10-02", "verdict": "on_track",
        "trade": {"status": "open", "side": "buy", "stop": 77.0},
    }]}
    issues = report_qc.check_hedge_action(c)
    assert len(issues) == 1 and "02/10" in issues[0]["message"]
    c["3"]["open_positions"] = [{"report_date": "2026-10-02", "decision": "keep", "text": "Giữ cắt lỗ 77,0."}]
    assert report_qc.check_hedge_action(c) == []


def test_hedge_label_must_match_cbam_card():
    c = g()
    c["2"]["cbam_estimate"] = {"signal": "increase"}
    issues = report_qc.check_hedge_action(c)
    assert issues[0]["severity"] == "error" and "2 nhãn phòng hộ" in issues[0]["message"]
    c["3"]["hedge_action"]["action"] = "increase"
    assert report_qc.check_hedge_action(c) == []
    c["3"]["hedge_action"]["text"] = "Chỉ gom ở 82,35–84,00."
    assert "chỉ gom" in report_qc.check_hedge_action(c)[0]["message"]


def test_generator_forces_system_hedge_label():
    from services.report_generator import _finalize_hedge_fields

    sec3 = {"trading_scenarios": [{"hedge_note": "x"}], "hedge_action": {"action": "buy_scheduled", "text": "t"}}
    _finalize_hedge_fields(sec3, [], {"signal": "hold", "fixed_share_pct": 22.9, "tactical_max_pct": 20, "quarter": "Q4 2026",
                                      "upcoming": {"count": 5, "volume_mt": 15.2}})
    ha = sec3["hedge_action"]
    assert ha["action"] == "hold" and "77,1%" in ha["core"] and "5 phiên" in ha["core"] and "hedge_note" not in sec3["trading_scenarios"][0]


def test_session_shape_not_called_reversal():
    from services.report_generator import _eua_session_shape

    chart = [{"open": 86, "high": 86 + 0.8, "low": 86 - 0.9, "close": 86} for _ in range(14)]
    chart += [{"open": 87.0, "high": 87.5, "low": 86.6, "close": 87.14},
              {"open": 86.87, "high": 86.87, "low": 85.37, "close": 85.84}]
    text = _eua_session_shape(chart)
    # Gap 0,27 = 0,16 × ATR 1,69 (< 0,25 ATR) → nhiễu, không gọi là tín hiệu (bản chấm 17/10).
    assert "KHÔNG được gọi là rộng" in text and "gap NHỎ" in text and "KHÔNG gọi là \"đảo chiều\"" in text


def test_section6_split_by_subject():
    """Mục 6 chia theo NƠI ĐĂNG (region nguồn), không theo chủ đề — quyết định của bàn 18/10/2026."""
    from services.report_generator import _section6_must_keep, _split_section6_by_subject

    intl, vn = _split_section6_by_subject([
        {"url": "a", "region": "vietnam", "about_vietnam": False},  # Reuters đăng lại trên vnexpress → vẫn nhóm Việt Nam
        {"url": "b", "region": "vietnam"},
        {"url": "c", "region": "international"},
    ])
    assert [a["url"] for a in intl] == ["c"] and [a["url"] for a in vn] == ["a", "b"]
    assert "about_vietnam" not in vn[0]
    # Bài nguồn VN về chính sách carbon VN/CBAM luôn được giữ dù bản tóm tắt chấm không liên quan.
    assert _section6_must_keep({"region": "vietnam", "topics": ["Chính sách carbon VN"]})
    assert not _section6_must_keep({"region": "international", "topics": ["Chính sách carbon VN"]})


def test_same_news_cannot_have_two_directions():
    c = g()  # GOLDEN: A2 ở Diễn biến chính "giảm"
    c["2"]["market_drivers"]["bullish"].append({"tag": "FACT", "text": "Kho khí EU đầy → than", "source_url": A2})
    issues = report_qc.check_dev_driver_consistency(c)
    assert len(issues) == 1 and "2 chiều" in issues[0]["message"]


def test_generator_drops_conflicting_driver_and_builds_verdict_block():
    from services.report_generator import _dev_verdict_block, _drop_conflicting_drivers

    lookup = {1: {"url": "u1", "title": "Kho khí EU"}, 2: {"url": "u2", "title": "Khác"}}
    impacts = {"u1": "tăng"}
    out = _drop_conflicting_drivers(
        {"bullish": [{"text": "a", "source_index": 1}], "bearish": [{"text": "b", "source_index": 1}, {"text": "c", "source_index": 2}]},
        lookup, impacts,
    )
    assert [d["text"] for d in out["bullish"]] == ["a"] and [d["text"] for d in out["bearish"]] == ["c"]
    assert "[1] Kho khí EU → HỖ TRỢ EUA" in _dev_verdict_block(impacts, lookup)


def test_streak_and_derived_targets_flagged():
    c = g()
    c["2"]["chart_data"] = [{"date": d, "close": v, "high": v + 0.5, "low": v - 0.5} for d, v in
                            [("2026-10-12", 88.34), ("2026-10-13", 88.05), ("2026-10-14", 87.14),
                             ("2026-10-15", 85.84), ("2026-10-16", 79.2)]]
    c["2"]["market_drivers"]["bearish"][0]["text"] = "EUA giảm phiên thứ hai liên tiếp."
    c["3"]["trading_scenarios"][1]["price_zone"] = "Mục tiêu 75,24 (79,20 × 0,95)"
    c["3"]["trading_scenarios"][1]["trading_strategy"] = (
        "**Entry:** Mua quanh 75, bán quanh 83\n**Mục tiêu:** 82 / 77,95 (82,05 × 0,95)\n**Quản trị rủi ro:** Cắt lỗ dưới 73 hoặc trên 85"
    )
    text = msgs(report_qc.check_consistency(c))
    assert "thực tế giảm 4 phiên liên tiếp" in text and "cơ sở 82,05" in text and "2 mục tiêu khác nhau" in text


# ── Bản chấm 17/10/2026 ───────────────────────────────────────────────

SUMMARY_NEUTRAL = "**Tổng hợp:** [TRUNG LẬP] Năng lượng giữ mức cầu, chính sách trung lập; EUA giảm 4 phiên có xác nhận khối lượng."


def _sc1710():
    return [
        {"horizon": "ngắn hạn", "probability": "Trung bình", "direction": "tăng", "condition": "Nếu giữ trên 83,25",
         "price_zone": "83,25–85,41", "key_risk": "Thủng 81,50",
         "trading_strategy": "**Entry:** Mua quanh 83,25–84,00\n**Mục tiêu:** 86,50\n**Quản trị rủi ro:** Cắt lỗ dưới 81,50"},
        {"horizon": "trung hạn", "probability": "Trung bình", "direction": "giảm", "condition": "Nếu đóng cửa dưới 82,35",
         "price_zone": "78,58–82,35", "key_risk": "Vượt 85,00",
         "trading_strategy": "**Entry:** Bán khi đóng cửa dưới 82,35\n**Mục tiêu:** 78,58 (−8% so với đóng cửa 85,41)\n**Quản trị rủi ro:** Cắt lỗ trên 85,00"},
    ]


def test_1710_mid_term_without_basis_overlap_and_pct_from_entry():
    from services.report_consistency import derived_target_issues

    c = {"2": {"prices": [{"code": "EUA", "close": 85.41}]},
         "3": {"analysis_blocks": [{"heading": "Phân tích", "content": SUMMARY_NEUTRAL}], "trading_scenarios": _sc1710()}}
    basis = report_qc.check_scenario_basis(c)
    assert len(basis) == 1 and "trung hạn" in basis[0]["message"] and report_qc._fmt(82.35) in basis[0]["message"]
    overlap = report_qc.check_horizon_overlap(c)
    assert len(overlap) == 1 and f"{report_qc._fmt(81.5)}–{report_qc._fmt(82.35)}" in overlap[0]["message"]
    msgs_ = " | ".join(i["message"] for i in derived_target_issues(c["3"], 85.41, []))
    assert "kỳ vọng thực -4,58% từ Entry" in msgs_, msgs_


def test_1710_technical_levels_have_breakdown_target():
    from services.report_generator import _eua_technical_levels_summary

    chart = [{"high": 88.82, "low": 86.0, "close": 87.0}] + [{"high": 86.0, "low": 82.35, "close": 85.0}] * 29
    assert "75.88" in _eua_technical_levels_summary(chart)


def test_1710_mid_term_sideways_with_breakdown_strategy():
    c = g()
    # ATR ≈ 1,6 như thực tế (1,69): cắt lỗ 84,20 cách mức thủng 81,50 là 2,70 ≈ 1,7 × ATR → báo.
    c["2"]["chart_data"] = [{"date": "d", "high": 88.82, "low": 87.0, "close": 88.0}] + [
        {"date": "d", "high": 84.0, "low": 82.35 + (i % 2) * 0.05, "close": 83.2} for i in range(29)]
    c["3"]["trading_scenarios"][1] = {
        "horizon": "trung hạn", "probability": "Trung bình", "direction": "đi ngang", "condition": "Nếu đóng cửa dưới 81,50",
        "price_zone": "81,50–85,41", "key_risk": "Vượt 84,20",
        "trading_strategy": "**Entry:** Bán khi đóng cửa dưới 81,50\n**Mục tiêu:** 77,80 (−4,5%)\n**Quản trị rủi ro:** Cắt lỗ trên 84,20",
    }
    text = msgs(report_qc.check_scenarios(c) + report_qc.check_breakout_levels(c))
    assert "nhãn \"đi ngang\" nhưng chiến lược là lệnh bán khi thủng" in text, text
    assert "phép đo biên độ" in text and "75.88" in text and "× ATR" in text, text


def test_free_allocation_phaseout_claim():
    c = g()
    c["3"]["trading_scenarios"][2]["condition"] = "Nếu lộ trình cắt phân bổ miễn phí tới 2029–2030 (Chỉ thị (EU) 2023/959) được giữ"
    assert len(report_qc.check_legislation_claims(c)) == 1
    c["3"]["trading_scenarios"][2]["condition"] = "Nếu lộ trình cắt phân bổ miễn phí (về 0% năm 2034, giảm mạnh nhất 2029–2030) được giữ"
    assert report_qc.check_legislation_claims(c) == []


