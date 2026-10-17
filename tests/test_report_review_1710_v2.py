"""Bản chấm lần 2 báo cáo 17/10/2026 (sau lượt sửa đầu) — chỉ các nhận xét đúng logic/số liệu."""
import asyncio
from types import SimpleNamespace

from services import forecast_review as fr
from services import report_consistency as rc
from services import report_generator as rg
from services import report_qc

PRICES = [
    {"code": "EUA", "close": 85.41, "week_change_pct": -1.76},
    {"code": "TTF", "close": 87.55, "week_change_pct": 7.51},
    {"code": "API2", "close": 152.25, "week_change_pct": 3.75},
]


# 1. Tỷ lệ đúng: cửa sổ gồm cả báo cáo đã ra khỏi bảng → phải liệt kê ngày; giữ thêm tỷ lệ lũy kế.
def test_calibration_window_lists_reports_and_cumulative():
    sc = {"horizon": "ngắn hạn", "direction": "tăng", "probability": "Cao",
          "trading_strategy": "**Entry:** Mua quanh 87,2–87,6\n**Mục tiêu:** 90,0\n**Quản trị rủi ro:** Cắt lỗ dưới 86,0"}
    reports = [SimpleNamespace(report_date=f"2026-09-{d:02d}", content={
        "2": {"prices": [{"code": "EUA", "close": 87.5}]}, "3": {"trading_scenarios": [sc]}}) for d in range(20, 0, -1)]
    prices = [SimpleNamespace(price_date=f"2026-09-{d:02d}", open_price=87.5, high_price=87.8, low_price=87.3, close_price=87.5)
              for d in range(1, 21)]
    prices.append(SimpleNamespace(price_date="2026-09-21", open_price=86.0, high_price=86.2, low_price=85.0, close_price=85.2))

    class _S:
        def __init__(self, *rs):
            self.rs = list(rs)

        async def execute(self, _stmt):
            v = self.rs.pop(0)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: v))

    calib, extra = asyncio.run(fr._calibration(_S(reports, prices), "2026-09-22", "2026-09-21"))
    assert len(extra["window"]) == fr.CALIBRATION_REPORTS and extra["window"][0]["report_date"] == "2026-09-20"
    cao_all = next(c for c in extra["calibration_all"] if c["probability"] == "Cao")
    # Báo cáo 01–11/09: đủ 10 phiên mà giá đi ngang → "chưa rõ", không có kết luận; 12–20/09 chạm cắt lỗ 21/09.
    assert cao_all["total"] == extra["calibration_all_reports"] == 9 and cao_all["correct"] == 0
    block = {"items": [], "calibration": calib, "calibration_window": extra["window"], "calibration_all": extra["calibration_all"]}
    text = fr.describe_for_prompt(block)
    assert "gồm báo cáo 20/09" in text and "LŨY KẾ: Cao 0/9" in text


# 2. Nhận định tổng quan ↔ kịch bản ngắn hạn.
def test_summary_label_must_match_sideways_short_term():
    content = {"2": {}, "3": {
        "analysis_blocks": [{"heading": "Phân tích", "content": "**Tổng hợp:** [TIÊU CỰC] " + "chữ " * 42}],
        "trading_scenarios": [{"horizon": "ngắn hạn", "direction": "đi ngang", "probability": "Trung bình"}],
    }}
    assert any("đi ngang" in i["message"] and i["field_path"].endswith(".trading_strategy")
               for i in report_qc.check_scenarios(content))


# 3. Phần chiến thuật CBAM: "hold" không được khuyên mua; không so với ước tính (vòng).
EST = {"spot": 85.41, "qtd_vwap": 85.32, "spot_vs_qtd_pct": 0.1, "hedge_gap_pct": 2.0, "signal_reason": "spot_near_qtd",
       "tactical_max_pct": 20, "estimate": 85.39}


def test_tactical_text_hold_and_circular():
    llm = "Phần chiến thuật (≤20%): mua futures EUA Dec ngay trong vùng 84,85–85,50; giá vẫn dưới ước tính VWAP quý 85,39."
    hold = rg.hedge_tactical_text(EST, llm, "hold")
    assert "không dùng hôm nay" in hold and "VWAP đấu giá đã chốt 85,32" in hold and "mua futures" not in hold
    inc = rg.hedge_tactical_text({**EST, "spot": 83.0, "spot_vs_qtd_pct": -2.7}, llm, "increase")
    assert "ước tính" not in inc and "thấp hơn VWAP đấu giá đã chốt" in inc
    content = {"2": {"cbam_estimate": {"signal": "hold"}}, "3": {"trading_scenarios": [{"horizon": "ngắn hạn"}],
                                                                "hedge_action": {"action": "hold", "text": llm}}}
    msgs = [i["message"] for i in report_qc.check_hedge_action(content)]
    assert any("hold" in m for m in msgs) and any("lập luận vòng" in m for m in msgs)


# 4. Kênh TTF → EUA thống nhất khi đã bão hòa.
def test_ttf_hedge_channel_flagged():
    content = {"2": {"prices": PRICES}, "3": {"analysis_blocks": [{"heading": "Cần theo dõi", "content":
        "6. Nếu gián đoạn Hormuz đẩy TTF tăng thêm → chi phí phát điện và hedge tăng, đỡ giá EUA. [3]"}]}}
    assert report_qc.check_ttf_channel(content)
    assert "KÊNH TTF → EUA DÙNG CHUNG" in rg._fuel_switching_spread_summary(PRICES)


# 5. Hồ sơ MSR cố định + trọng số + bỏ lịch đấu giá đã áp dụng + trích [HSk].
def test_dossiers_pinned_and_cited(tmp_path):
    f = tmp_path / "d.yaml"
    f.write_text('dossiers:\n  - item: "Sửa đổi MSR"\n    impact: "giảm"\n    source_name: "Brussels Times"\n'
                 '  - item: "Tạm dừng chuyển hạn ngạch vào MSR"\n    impact: "giảm"\n    supply_mt: 190\n    source_name: "S&P Global"\n'
                 '    source_date: "05/10/2026"\n  - item: "Đã xong"\n    closed: true\n', encoding="utf-8")
    dossiers = rg._load_ets_dossiers(f)
    assert [d["item"] for d in dossiers] == ["Tạm dừng chuyển hạn ngạch vào MSR", "Sửa đổi MSR"]  # quy mô lớn trước
    lookup = {1: {"url": "u1", "source": "argusmedia.com", "title": "x", "published_date": "14/10/2026"}}
    out = rg._resolve_ets_policy([
        {"item": "Lịch đấu giá EU ETS1 2027 sửa đổi", "source_index": 1},
        {"item": "Tạm dừng chuyển hạn ngạch vào MSR", "source_index": 1},
        {"item": "Đề nghị tạm dừng ETS (Eni)", "source_index": 1, "impact": "giảm"},
    ], lookup, dossiers)
    assert [o["item"] for o in out] == ["Tạm dừng chuyển hạn ngạch vào MSR", "Sửa đổi MSR", "Đề nghị tạm dừng ETS (Eni)"]
    sec3 = {"analysis_blocks": [{"heading": "Cần theo dõi", "content": "\n".join([
        "1. Kết luận Hội đồng về MSR. Nếu ... → giảm. Nếu ... → tăng. [HS1]",
        "2. Eni đòi tạm dừng ETS. Nếu ... [1]",
        "3. Eni lại lần nữa. Nếu ... [1]",
        "4. Đấu giá. [giá]", "5. Giá. [giá]", "6. Giá nữa. [giá]", "7. Thêm. [giá]",
    ])}]}
    rg._resolve_section3_citations(sec3, lookup, rg._dossier_lookup(dossiers))
    lines = sec3["analysis_blocks"][0]["content"].split("\n")
    assert len(lines) == rg.WATCH_MAX_ITEMS and "(Nguồn: S&P Global, 05/10/2026)" in lines[0]
    assert sum("Eni" in l for l in lines) == 1  # watchpoint lặp cùng nguồn bị bỏ


# 7–8. Kịch bản dài hạn theo % tròn.
def test_long_term_round_pct_target_removed():
    sc = {"horizon": "dài hạn", "direction": "tăng", "probability": "Trung bình",
          "trading_strategy": "**Entry:** Tích lũy dần 82,35–84,00\n**Mục tiêu:** 92,25 (+8% so với đóng cửa 85,41)\n"
                              "**Quản trị rủi ro:** Giảm vị thế nếu đóng cửa dưới 80,50"}
    assert any("% tròn" in i["message"] for i in report_qc.check_long_term_basis({"2": {}, "3": {"trading_scenarios": [sc]}}))
    sec3 = {"trading_scenarios": [sc]}
    rg._enforce_scenario_rules(sec3)
    assert "92,25" not in sec3["trading_scenarios"][0]["trading_strategy"]
    assert "chưa đủ cơ sở định lượng" in sec3["trading_scenarios"][0]["trading_strategy"]


# 9. Chi phí CBAM/tấn: benchmark CBAM theo mã CN (không phải benchmark ETS 2021–2025) + CSCF + kịch bản giá trị mặc định.
def test_product_exposure_caveats():
    from services import cbam_estimate as ce
    rows = ce.product_exposure(85.39, None, None, 2026)
    text = ce.describe_product_exposure({"product_exposure": rows})
    alu = next(r for r in rows if r["key"] == "aluminium_primary")
    steel = next(r for r in rows if r["key"] == "steel_bf_bof")
    assert (alu["cost_eur_per_t"], steel["cost_eur_per_t"]) == (26.69, 39.64)  # bản chấm 17/10 lần 3
    assert [d["cost_eur_per_t"] for d in alu["defaults"]] == [57.18, 163.31] and steel["defaults"][0]["cost_eur_per_t"] == 185.29
    assert "2025/2620" in text and "CSCF 100%" in text and "MẶC ĐỊNH (+10% markup)" in text and "+8,54 EUR/t" in text


# 10. Gợi ý trùng ý giữa 2 bảng, KPI ≥ 0.
def test_biz_duplicate_and_weak_kpi():
    content = {"biz": {
        "short_term": [{"trigger": "Khi gián đoạn xuất khẩu nhôm vùng Vịnh qua Hormuz",
                        "action": "Cập nhật nguồn cung nhôm nguyên sinh thay thế ngoài vùng Vịnh", "kpi": "premium ≥ 0"}],
        "long_term": [{"opportunity": "Nguồn nhôm nguyên sinh vùng Vịnh qua Hormuz kém ổn định",
                       "solution": "Thiết lập nguồn cung nhôm nguyên sinh thay thế ngoài vùng Vịnh"}],
    }}
    msgs = [i["message"] for i in report_qc.check_biz(content, {}, set(), [], "2026-10-18")]
    assert any("trùng ý" in m for m in msgs) and any("quá thấp" in m for m in msgs)


# 11. Lỗi nhỏ.
def test_small_fixes():
    text, problems = rc.fix_range_claims("**Mục tiêu:** 87,00 (vùng giữa biên) / 83,80", 85.41, (82.35, 88.82))
    assert "87,00 (nửa trên)" in text and problems
    assert rc.strip_state_media("Chủ tịch EC đề xuất kho dự trữ khí, theo TASS ngày 16/10.") == "Chủ tịch EC đề xuất kho dự trữ khí."
    assert rc.strip_state_media("Theo TASS và Politico, EC đề xuất") == "Theo Politico, EC đề xuất"
    assert rg.source_chain_label("TASS, Politico", "cafef.vn") == "Politico (gốc) → cafef.vn"
    assert next(e for e in rg._compute_recurring_calendar_events("2026-10-16") if "Baker Hughes" in e["event"])["impact"] == "Thấp"


# ── Lần chấm 3 ─────────────────────────────────────────────────────────────────────────────────────────
def test_repo_dossiers_cover_review_files():
    items = [d["item"] for d in rg._load_ets_dossiers()]
    assert items[0].startswith("Văn bản chung Ý–Séc")  # quy mô 190 triệu hạn ngạch xếp đầu
    assert any("COM(2026) 616" in i for i in items) and any("Hội đồng châu Âu 15–16/10" in i for i in items)
    assert "HỒ SƠ CẤU TRÚC" in rg._ets_policy_prompt_block([], {}, rg._dossier_lookup(rg._load_ets_dossiers()))
    assert "bảng độ nhạy chi phí" in rg._biz_policy_block(rg._dossier_lookup(rg._load_ets_dossiers()))


def test_policy_calendar_outcome_and_llm_duplicate_dropped():
    rec = rg._policy_calendar_events("2026-10-16")
    assert rec and "bế tắc" in rec[0]["outcome"]
    llm = [{"date": rec[0]["date"], "datetime_vn": "16/10", "impact": "Cao",
            "event": "Hội nghị thượng đỉnh EU (Hội đồng châu Âu) bàn đề nghị tạm dừng chuyển hạn ngạch vào MSR",
            "outcome": "Chưa có kết quả được xác nhận"}]
    out = rg._finalize_section8_events(llm, "2026-10-16", rec)
    summit = [e for e in out if "thượng đỉnh" in e["event"]]
    assert len(summit) == 1 and "bế tắc" in summit[0]["outcome"]


def test_calibration_drift_warning():
    drift = fr.calibration_drift([{"probability": "Cao", "correct": 0, "total": 2}],
                                 [{"probability": "Cao", "correct": 9, "total": 13}])
    assert drift and "0/2" in drift[0] and "9/13" in drift[0]
    assert fr.calibration_drift([{"probability": "Cao", "correct": 1, "total": 2}],
                                [{"probability": "Cao", "correct": 2, "total": 4}]) == []  # lũy kế < 5 báo cáo


def test_hedge_text_mentions_contract_and_obligation():
    from datetime import date
    from services import cbam_estimate as ce
    est = {"quarter": "Q4 2026", "last_auction_date": "2026-12-17",
           **ce.estimate_quarter([(date(2026, 10, 1), 85.32, 26.71e6)], [(date(2026, 12, 17), 90.0e6)], 85.41, 0.0118,
                                 date(2026, 10, 16))}
    text = ce.hedge_structure_text(est)
    assert "nghĩa vụ CBAM Q4 2026 của SIM (tCO2)" in text and "EUA futures Dec-26 (ICE Endex)" in text
    assert "Phiên đấu giá cuối quý: 17/12" in text and "Mar kế tiếp" in text
