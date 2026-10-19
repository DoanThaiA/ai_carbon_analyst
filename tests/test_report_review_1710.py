"""Các lỗi lập luận/trình bày từ bản chấm báo cáo 17/10/2026 (Jack chấm 99/100 nhưng không kiểm lập luận) — mỗi test
dùng đúng số liệu của báo cáo đó: EUA 85,41 (OHLC 85,73/86,10/84,85/85,41, khối lượng 16.499 vs TB 22.231), TTF 87,55,
API2 152,25 USD/t, chuỗi giảm 88,34 → 88,05 → 87,14 → 85,84 → 85,41."""
from datetime import date

from services import cbam_estimate as ce
from services import forecast_review as fr
from services import report_consistency as rc
from services import report_generator as rg
from services import report_qc

PRICES = [
    {"code": "EUA", "close": 85.41, "week_change_pct": -1.76},
    {"code": "TTF", "close": 87.55, "week_change_pct": 7.51},
    {"code": "API2", "close": 152.25, "week_change_pct": 3.75},
]


def _chart():
    """14 phiên nền (TR ≈ 1,70) + chuỗi giảm 4 phiên của tháng 10."""
    rows = [{"date": f"2026-09-{d:02d}", "open": 86.0, "high": 86.85, "low": 85.15, "close": 86.0, "volume": 22231}
            for d in range(10, 28)]
    rows[0]["low"] = 82.35  # đáy 30 phiên (ngoài 14 phiên tính ATR)
    rows += [
        {"date": "2026-10-12", "open": 87.9, "high": 88.82, "low": 87.6, "close": 88.34, "volume": 23000},
        {"date": "2026-10-13", "open": 88.3, "high": 88.6, "low": 87.6, "close": 88.05, "volume": 27281},
        {"date": "2026-10-14", "open": 88.0, "high": 88.2, "low": 86.9, "close": 87.14, "volume": 23251},
        {"date": "2026-10-15", "open": 86.87, "high": 86.87, "low": 85.37, "close": 85.84, "volume": 24814},
        {"date": "2026-10-16", "open": 85.73, "high": 86.10, "low": 84.85, "close": 85.41, "volume": 16499},
    ]
    return rows


# ── Fuel switching: ngưỡng ±16 EUR/MWh vô nghĩa khi giá hòa vốn còn xa EUA ──────────────────────────────
def test_fleet_breakeven_range_and_real_threshold():
    st = rg.fuel_spread_state(PRICES)
    lo, hi = st["switch_range"]
    assert round(lo) == 144 and 370 <= hi <= 380  # bản chấm: ~144–378 EUR/t
    assert st["robust_saturation"] and rg.fuel_threshold_far(st)
    # Ngưỡng thật: TTF/API2 phải đi rất xa mới làm cặp nhà máy chuyển đổi sớm nhất hòa vốn ở EUA 85,41.
    assert st["ttf_first_move_pct"] < -25 and st["api2_first_move_pct"] > 25
    text = rg._fuel_switching_spread_summary(PRICES)
    assert "NGƯỠNG THẬT" in text and "±16" not in text and "KHÔNG đặt điều kiện fuel switching" in text


def test_jack_flags_spread_threshold_watchpoint():
    content = {"2": {"prices": PRICES}, "3": {
        "analysis_blocks": [{"heading": "Cần theo dõi", "content": "2. Chênh chi phí khí − than, ngưỡng theo dõi ±16 EUR/MWh "
                                                                  "(tương đương TTF ±10%)."}],
        "trading_scenarios": [{"horizon": "ngắn hạn", "condition": "Nếu chênh khí − than không thu hẹp quá 16 EUR/MWh."}],
    }}
    issues = report_qc.check_fuel_thresholds(content)
    assert len(issues) == 2 and all("Ngưỡng thật" in i["message"] for i in issues)
    assert len(report_qc.check_fuel_thresholds(content, scenarios_only=True)) == 1


# ── Xu hướng: "mức giảm thu hẹp dần" khi −0,29 → −0,91 → −1,30 → −0,43 ─────────────────────────────────
def test_momentum_claim_is_fixed():
    mom = rc.streak_momentum(rc.eua_streak(_chart()))
    assert mom["pattern"] == "mở rộng rồi thu hẹp ở phiên cuối"
    text, problems = rc.fix_momentum_claims("EUA giảm cả ngày lẫn tuần nhưng mức giảm thu hẹp dần và đấu giá tốt.", mom)
    assert "mức giảm thu hẹp ở phiên cuối" in text and problems
    assert rc.fix_momentum_claims("đà tăng chậm lại dần", mom)[1] == []  # khác chiều nhịp → không đụng


def test_prompt_streak_block_has_momentum():
    assert "mở rộng rồi thu hẹp ở phiên cuối" in rg._eua_streak_summary(_chart())


# ── Cạn lực / gap / vị trí trong biên ───────────────────────────────────────────────────────────────────
def test_exhaustion_signal_and_contradiction():
    sig = rc.session_signals(_chart())
    assert sig["exhausted"] and not sig["gap_significant"] and sig["trend"] == "giảm"
    issues = rc.technical_claim_issues("Lực bán vẫn chiếm ưu thế, chưa có bằng chứng cạn lực.", sig)
    assert issues and "cạn lực" in issues[0]
    assert rc.technical_claim_issues("Phiên mở với khoảng trống giảm — tín hiệu bán.", sig)
    shape = rg._eua_session_shape(_chart())
    assert "CẠN LỰC" in shape and "gap NHỎ" in shape


def test_jack_flags_small_gap_driver():
    content = {"2": {"chart_data": _chart(), "market_drivers": {"bullish": [], "bearish": [
        {"tag": "FACT", "text": "Phiên liền trước mở cửa với khoảng trống giảm so với giá đóng cửa trước đó."}]}}}
    assert any("gap mở cửa" in i["message"] for i in report_qc.check_technical_claims(content))


def test_near_midpoint_is_not_lower_half():
    text, problems = rc.fix_range_claims("giá nằm ở nửa dưới biên độ 30 ngày 82,35–88,82", 85.41)
    assert text == "giá nằm ở vùng giữa biên độ 30 ngày 82,35–88,82" and problems
    assert "QUANH ĐIỂM GIỮA" in rc.describe_range_position(85.41, 82.35, 88.82, "biên 30 phiên")


# ── Trình bày ──────────────────────────────────────────────────────────────────────────────────────────
def test_stray_quote_and_english_title_kept():
    assert rc.fix_stray_quotes("tách rời của Nga', nằm giữa các nước NATO") == "tách rời của Nga, nằm giữa các nước NATO"
    assert rc.fix_stray_quotes("đội tàu 'bóng tối' và Europe's gas") == "đội tàu 'bóng tối' và Europe's gas"
    content = {"6": {"international": [{"title": "Frontier Signs $11.7M Deal With 44.01's Technology", "summary": "giá 5.8%"}]}}
    rc.apply_consistency_fixes(content)
    art = content["6"]["international"][0]
    assert art["title"] == "Frontier Signs $11.7M Deal With 44.01's Technology" and art["summary"] == "giá 5,8%"


# ── Đấu giá: so DE với DE ──────────────────────────────────────────────────────────────────────────────
def test_auction_summary_compares_same_platform():
    import asyncio
    from types import SimpleNamespace

    latest = [SimpleNamespace(auction_date=date(2026, 10, 16), auction_name="DE", auction_price=85.50, cover_ratio=2.09,
                              total_bidders=26)]
    d = date(2026, 10, 9)
    # Chính phiên 16/10 (2,09) có trong kết quả truy vấn nhưng KHÔNG được tính vào TB (bản chấm: loại phiên đang so).
    hist = [("DE", d, 1.85), ("DE", d, 1.93), ("DE", d, 2.0), ("DE", date(2026, 10, 16), 2.09), ("EU", d, 1.5),
            ("EU", d, 1.45), ("PL", d, 1.6), ("DE", date(2026, 6, 1), 9.0)]

    class _S:
        def __init__(self):
            self.calls = 0

        async def execute(self, _stmt):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: latest))
            return SimpleNamespace(all=lambda: hist)

    text = asyncio.run(rg._latest_auction_summary(_S(), "2026-10-16", 85.41))
    assert "TB 90 ngày trước đó của phiên DE 1,93 trên 3 phiên" in text and "xấp xỉ TB" in text
    assert "KHÁC thời điểm và kỳ hạn" in text and "cao hơn giá đóng cửa futures" not in text


# ── Lịch Mục 8 ─────────────────────────────────────────────────────────────────────────────────────────
def test_calendar_has_cot_and_policy_meetings(tmp_path):
    events = rg._compute_recurring_calendar_events("2026-10-16")
    assert any("Commitments of Traders" in e["event"] and e["date"] == "2026-10-21" for e in events)
    cal = tmp_path / "policy_calendar.yaml"
    cal.write_text('events:\n  - date: "2026-10-20"\n    event: "Hội đồng Môi trường EU"\n    impact: "Cao"\n'
                   '  - date: "2026-12-01"\n    event: "ngoài cửa sổ"\n', encoding="utf-8")
    got = rg._policy_calendar_events("2026-10-16", cal)
    assert [e["event"] for e in got] == ["Hội đồng Môi trường EU (20/10)"]


def test_jack_flags_released_event_shown_as_upcoming():
    content = {"8": {"events": [{
        "date": "2026-10-17", "datetime_vn": "17/10", "event": "Số giàn khoan Baker Hughes (Baker Hughes Rig Count)",
        "impact": "Trung", "released_at": "2026-10-17T00:00:00+07:00",
    }]}}
    assert any("sắp tới" in i["message"] for i in report_qc.check_calendar(content, "2026-10-17"))


# ── Phòng hộ CBAM ──────────────────────────────────────────────────────────────────────────────────────
def test_cbam_hedge_structure_and_product_exposure():
    est = {"quarter": "Q4 2026", "upcoming": {"count": 4, "volume_mt": 9.92},
           **ce.estimate_quarter([(date(2026, 10, 1), 85.32, 26.71e6)], [(date(2026, 12, 1), 90.0e6)], 85.41, 0.0118,
                                 date(2026, 10, 16))}
    assert est["open_share_pct"] == 77.1 and est["core_share_of_quarter_pct"] == 61.7  # ≥80% của 77,1% = 61,7% quý
    text = ce.hedge_structure_text(est)
    assert "61,7% nghĩa vụ CBAM quý" in text and "MUA EUA futures Dec-26" in text and "KHÔNG khóa được gì" in text
    rows = ce.product_exposure(85.39, None, None, 2026)
    alu = next(r for r in rows if r["key"] == "aluminium_primary")
    # Benchmark CBAM theo mã CN (7601: 1,423 — cbam_products.yaml), CSCF 2026 = 100%.
    assert alu["free_allocation_factor_pct"] == 97.5 and alu["net_tco2_per_t"] == round(1.7 - 0.975 * 1.0 * 1.423, 3)
    assert alu["cost_eur_per_t"] == round((1.7 - 0.975 * 1.423) * 85.39, 2)
    assert "nhôm thỏi nguyên sinh" in ce.describe_product_exposure({"product_exposure": rows})


def test_jack_flags_schedule_buying_as_hedge():
    content = {"2": {"cbam_estimate": {"signal": "hold"}}, "3": {
        "trading_scenarios": [{"horizon": "ngắn hạn"}],
        "hedge_action": {"action": "hold", "text": "Phần lõi mua theo tỷ trọng lịch đấu giá để chi phí bám VWAP."},
    }}
    assert any("không khóa được" in i["message"] for i in report_qc.check_hedge_action(content))


# ── Chấm dự báo ────────────────────────────────────────────────────────────────────────────────────────
def test_sideways_graded_on_path():
    sc = {"horizon": "ngắn hạn", "direction": "đi ngang", "probability": "Trung bình", "trading_strategy": ""}
    sessions = [{"date": f"d{i}", "open": c, "high": c, "low": c, "close": c}
                for i, c in enumerate([86.0, 87.5, 88.34, 87.0, 86.0, 85.5, 85.3, 85.4, 85.2, 85.41])]
    it = fr.evaluate_forecast("2026-10-08", sc, 85.25, sessions)
    # Cuối khung chỉ +0,19% nhưng giữa kỳ đã +3,6% → không còn là "đi ngang".
    assert it["verdict"] == "wrong" and it["graded_by"] == "path" and it["max_excursion_pct"] >= 3.5


def test_design_flaw_low_rr_at_fill():
    t = {"side": "buy", "entry_low": 87.2, "entry_high": 88.0, "stop": 86.30, "target": 88.82, "fill_price": 88.0}
    flaws = fr.trade_design_flaws(t, 1.70)
    assert flaws[0]["kind"] == "low_rr" and flaws[0]["rr"] == 0.48
    tight = fr.trade_design_flaws({**t, "fill_price": 86.87, "stop": 86.60, "target": 88.82}, 1.70)
    assert any(f["kind"] == "tight_stop" and f["distance"] == 0.27 for f in tight)


def test_naive_baseline():
    ev = [
        {"verdict": "correct", "change_pct": 0.2, "max_excursion_pct": 0.5, "naive_momentum": "tăng"},
        {"verdict": "wrong", "change_pct": -3.0, "max_excursion_pct": -3.0, "naive_momentum": "giảm"},
    ]
    b = fr.baseline_from(ev)
    assert b["model"] == {"correct": 1, "total": 2}
    assert b["always_sideways"] == {"correct": 1, "total": 2}
    assert b["momentum"]["correct"] == 1 and b["momentum"]["total"] == 1  # +0,2% với dự báo "tăng" = chưa rõ


# ── Kịch bản ───────────────────────────────────────────────────────────────────────────────────────────
def test_enforce_sideways_breakout_and_probability_cap():
    sec3 = {"trading_scenarios": [
        {"horizon": "ngắn hạn", "direction": "tăng", "probability": "Cao", "trading_strategy": ""},
        {"horizon": "trung hạn", "direction": "đi ngang", "probability": "Trung bình",
         "trading_strategy": "**Entry:** Bán khi đóng cửa dưới 81,50\n**Mục tiêu:** 75,88\n**Quản trị rủi ro:** Cắt lỗ trên 83,60"},
    ]}
    rg._enforce_scenario_rules(sec3)
    short, mid = sec3["trading_scenarios"]
    assert short["probability"] == "Trung bình"
    assert mid["direction"] == "giảm" and mid["probability"] == "Thấp"


def test_long_term_basis():
    content = {"2": {"chart_data": _chart(), "prices": [{"code": "EUA", "close": 85.41}]}, "3": {"trading_scenarios": [{
        "horizon": "dài hạn", "direction": "tăng", "price_zone": "Tích lũy 82,35–85,41 EUR/tCO2",
        "trading_strategy": "**Entry:** Tích lũy dần 82,35–85,00\n**Mục tiêu:** 95,29 (đo biên độ 30 phiên)\n"
                            "**Quản trị rủi ro:** Giảm vị thế nếu đóng cửa dưới 80,50",
    }]}}
    msgs = [i["message"] for i in report_qc.check_long_term_basis(content)]
    assert any("đo biên độ 30 phiên" in m for m in msgs) and any("giá đóng cửa hôm nay" in m for m in msgs)


# ── Nguồn & gợi ý kinh doanh ───────────────────────────────────────────────────────────────────────────
def test_inline_attribution_state_media():
    content = {"1": {"bullets": [{"text": "**Chính sách:** EC đề xuất kho dự trữ khí, theo TASS ngày 16/10.",
                                  "source_name": "Politico (gốc) → cafef.vn"}]}}
    issues = report_qc.check_inline_attribution(content)
    assert issues and "truyền thông nhà nước" in issues[0]["message"]


def test_biz_lens_and_quantification():
    content = {"biz": {"short_term": [], "long_term": [{
        "opportunity": "Co-processing SAF ở nhà máy lọc dầu EU", "solution": "Lập hồ sơ dấu chân carbon sản phẩm (Scope 1-2)",
        "expectation": "Có khách mới",
    }]}}
    msgs = [i["message"] for i in report_qc.check_biz(content, {}, set(), [], "2026-10-18")]
    assert any("chưa lượng hóa" in m for m in msgs)
    assert any("SAF" in m for m in msgs) and any("Scope 1-2" in m for m in msgs)


# ── Theo dõi chính sách ETS/MSR ────────────────────────────────────────────────────────────────────────
def test_ets_policy_merge_and_resolve():
    lookup = {1: {"url": "u1", "title": "Tin ngày", "source": "a.com", "summary": "...", "published_date": "16/10/2026"}}
    policy = [
        {"url": "u1", "title": "Tin ngày", "source": "a.com", "summary": "...", "published_date": "16/10/2026"},
        {"url": "u2", "title": "Italy, Czechia seek MSR suspension", "source": "spglobal.com", "summary": "190 triệu",
         "published_date": "05/10/2026"},
    ]
    nums = rg._merge_policy_articles(lookup, policy)
    assert nums == [1, 2] and lookup[2]["url"] == "u2"
    assert "[2] [spglobal.com, đăng 05/10/2026]" in rg._ets_policy_prompt_block(nums, lookup)
    out = rg._resolve_ets_policy([
        {"item": "Đề nghị tạm dừng chuyển hạn ngạch vào MSR", "status": "Ý, Séc gửi văn bản", "impact": "giảm", "source_index": 2},
        {"item": "Không nguồn", "source_index": 9},
    ], lookup)
    assert len(out) == 1 and out[0]["source_url"] == "u2" and out[0]["next_step"] == "Chưa có lịch xác nhận"
    assert rg.ETS_POLICY_RE.search("Italy and Czechia call to suspend the transfer of allowances into the MSR")
    # Nguồn khối chính sách cố ý cũ hơn khung tin → Jack không báo "tin CŨ".
    from datetime import datetime, timezone
    content = {"3": {"ets_policy": [{"source_url": "https://x.com/msr", "source_name": "x.com"}]}, "9": {"items": [
        {"source": "x.com", "url": "https://x.com/msr"}]}}
    meta = {"https://x.com/msr": {"source": "x.com", "published_at": datetime(2026, 10, 5, tzinfo=timezone.utc)}}
    issues = report_qc.check_sources(content, ["https://x.com/msr"], meta, datetime(2026, 10, 13, tzinfo=timezone.utc))
    assert not any("tin CŨ" in i["message"] for i in issues)
