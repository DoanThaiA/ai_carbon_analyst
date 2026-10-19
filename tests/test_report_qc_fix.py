"""Sửa báo cáo theo biên bản QC: định vị lỗi → đoạn, kiểm đề xuất LLM (chặn sửa lan, bịa số, đụng nguồn)."""
import copy

from services import report_qc_fix as fx

SCEN = ("**Entry:** Bán quanh 79,0–79,5 EUR/tCO2\n**Mục tiêu:** 75,5 (hỗ trợ 30 phiên)\n"
        "**Quản trị rủi ro:** Cắt lỗ nếu trên 81,0")
CONTENT = {
    "1": {"title": "Tóm tắt điều hành", "bullets": [
        {"text": "**EUA:** đóng cửa 79,20 EUR/tCO2, giảm 0,5%.", "source_name": None, "source_url": None},
        {"text": "**Khí:** TTF tăng 2,1% hỗ trợ EUA (Nguồn: Reuters).", "source_name": "Reuters",
         "source_url": "https://x.com/a"},
    ]},
    "2": {"key_developments": [{"text": "EU đề xuất nới MSR, giá EUA chịu áp lực.", "impact": "giảm"}],
          "chart_data": [{"date": "2026-10-07", "close": 79.2}]},
    "3": {"trading_scenarios": [
        {"horizon": "ngắn hạn", "direction": "giảm", "condition": "Nếu thủng 78,5", "price_zone": "78,5–80,0",
         "trading_strategy": SCEN},
    ]},
    "6": {"international": [{"title": "EU carbon 79.20 close", "summary": "Giá EUA đóng cửa 79,20.", "url": "u"}]},
}


def _f(fid, **kw):
    base = {"id": fid, "severity": "major", "criterion": "data_accuracy", "section": None, "field_path": None,
            "title": "t", "current_text": None, "expected_text": None, "evidence": None, "status": "open"}
    base.update(kw)
    return base


def test_path_roundtrip_and_aliases():
    assert fx.parse_path("3.trading_scenarios[0].price_zone") == ["3", "trading_scenarios", 0, "price_zone"]
    assert fx.normalize_path('content["3"].trading_scenarios[0]') == "3.trading_scenarios[0]"
    c = copy.deepcopy(CONTENT)
    fx.set_at(c, "1.bullets[0].text", "x")
    assert fx.get_at(c, "1.bullets[0].text") == "x"


def test_editable_leaves_skip_enum_url_and_original_titles():
    leaves = fx.editable_leaves(CONTENT)
    assert "1.bullets[0].text" in leaves and "3.trading_scenarios[0].trading_strategy" in leaves
    assert "3.trading_scenarios[0].direction" not in leaves  # enum
    assert "1.bullets[1].source_url" not in leaves
    assert "6.international[0].title" not in leaves  # tiêu đề gốc bài
    assert "6.international[0].summary" in leaves


def test_locate_by_field_path_and_text():
    leaves = fx.editable_leaves(CONTENT)
    loc = fx.locate_finding(_f(1, field_path="1.bullets[0]", current_text="đóng cửa 79,20 EUR/tCO2"), leaves)
    assert loc.paths == ["1.bullets[0].text"] and loc.method == "field_path+text"


def test_locate_text_with_ellipsis_and_wrong_section():
    leaves = fx.editable_leaves(CONTENT)
    loc = fx.locate_finding(_f(1, section="1", current_text="“EU đề xuất nới MSR … chịu áp lực”"), leaves)
    assert loc.paths == ["2.key_developments[0].text"]  # QC ghi nhầm mục → vẫn tìm thấy duy nhất 1 chỗ


def test_locate_ambiguous_or_missing_goes_manual():
    leaves = fx.editable_leaves(CONTENT)
    assert fx.locate_finding(_f(1, current_text="Không có câu này trong báo cáo"), leaves).reason
    assert fx.locate_finding(_f(1, field_path="3.trading_scenarios[0].direction"), leaves).reason  # enum
    assert fx.locate_finding(_f(1, expected_text="Bổ sung tin X"), leaves).reason  # không vị trí, không nguyên văn


def test_locate_scenario_block_by_field_path_only():
    leaves = fx.editable_leaves(CONTENT)
    loc = fx.locate_finding(_f(1, field_path="3.trading_scenarios[0]", expected_text="Cắt lỗ trên 80,5"), leaves)
    assert set(loc.paths) == {"3.trading_scenarios[0].condition", "3.trading_scenarios[0].price_zone",
                              "3.trading_scenarios[0].trading_strategy"}


def test_number_variants_vi_en():
    assert fx.number_keys("79,20") & fx.number_keys("79.2")
    assert fx.number_keys("1.234,5") & fx.number_keys("1234.5")
    assert fx.novel_numbers("giảm 0,8%", fx.number_keys("0,80")) == []
    assert fx.novel_numbers("giảm 0,9%", fx.number_keys("0,8")) == ["0,9"]


def test_validate_edits_accepts_minimal_replacement():
    text = CONTENT["1"]["bullets"][0]["text"]
    out, ok, bad = fx.validate_edits(text, [{"old": "giảm 0,5%", "new": "giảm 0,8%", "finding_ids": [1]}],
                                     fx.number_keys("0,8"), {1})
    assert out == "**EUA:** đóng cửa 79,20 EUR/tCO2, giảm 0,8%." and len(ok) == 1 and not bad


def test_validate_edits_rejects_unsafe():
    text = CONTENT["1"]["bullets"][1]["text"]
    cases = [
        ({"old": "không có", "new": "x", "finding_ids": [1]}, "không có trong bản nháp"),
        ({"old": "tăng 2,1%", "new": "tăng 3,7%", "finding_ids": [1]}, "số mới"),  # bịa số
        ({"old": "tăng 2,1%", "new": "giảm 2,1%", "finding_ids": [9]}, "không gắn"),  # lỗi không giao cho đoạn này
        ({"old": "(Nguồn: Reuters)", "new": "(Nguồn: Bloomberg)", "finding_ids": [1]}, "trích nguồn"),
    ]
    for edit, why in cases:
        out, ok, bad = fx.validate_edits(text, [edit], set(), {1})
        assert out == text and not ok and why in bad[0]["reason"], (edit, bad)


def test_validate_edits_allows_wrapping_source_verbatim_and_rejects_overlap():
    text = CONTENT["1"]["bullets"][1]["text"]
    out, ok, _ = fx.validate_edits(
        text, [{"old": "hỗ trợ EUA (Nguồn: Reuters)", "new": "trung lập với EUA (Nguồn: Reuters)", "finding_ids": [1]}],
        set(), {1})
    assert ok and out.endswith("trung lập với EUA (Nguồn: Reuters).")
    _, ok, bad = fx.validate_edits(text, [
        {"old": "TTF tăng 2,1%", "new": "TTF tăng mạnh 2,1%", "finding_ids": [1]},
        {"old": "2,1% hỗ trợ", "new": "2,1% không hỗ trợ", "finding_ids": [1]},
    ], set(), {1})
    assert len(ok) == 1 and "chồng" in bad[0]["reason"]


def test_apply_llm_result_multi_leaf_group_stays_inside_group():
    leaves = fx.editable_leaves(CONTENT)
    f = _f(1, field_path="3.trading_scenarios[0]", expected_text="Cắt lỗ nếu trên 80,5", evidence="R:R = 1,5")
    groups = fx._group_locations([fx.locate_finding(f, leaves)], {1: f})
    g = groups[0]
    k = g["paths"].index("3.trading_scenarios[0].trading_strategy") + 1
    result = {"edits": [
        {"target": f"T1.{k}", "old": "trên 81,0", "new": "trên 80,5", "finding_ids": [1]},
        # đoạn ngoài nhóm — model không có quyền sửa: không đường dẫn nào nhận
        {"target": "T2", "old": "79,20", "new": "79,30", "finding_ids": [1]},
    ]}
    per_path, _ = fx.apply_llm_result(g, {p: leaves[p] for p in g["paths"]}, result)
    changed = {p: v for p, v in per_path.items() if v["edits"]}
    assert list(changed) == ["3.trading_scenarios[0].trading_strategy"]
    assert changed["3.trading_scenarios[0].trading_strategy"]["after"].endswith("Cắt lỗ nếu trên 80,5")
    assert any("đoạn đích" in r["reason"] for v in per_path.values() for r in v["rejected"])


def test_group_merges_findings_sharing_a_leaf():
    leaves = fx.editable_leaves(CONTENT)
    f1 = _f(1, current_text="đóng cửa 79,20 EUR/tCO2")
    f2 = _f(2, current_text="giảm 0,5%")
    locs = [fx.locate_finding(f1, leaves), fx.locate_finding(f2, leaves)]
    groups = fx._group_locations(locs, {1: f1, 2: f2})
    assert len(groups) == 1 and {f["id"] for f in groups[0]["findings"]} == {1, 2}


def test_build_fix_preview_end_to_end(monkeypatch):
    """Luồng xem trước với DB/LLM giả: chỉ đoạn bị lỗi thay đổi, lỗi không neo được → sửa tay."""
    import asyncio
    from types import SimpleNamespace

    from services import report_qc

    report = SimpleNamespace(content=copy.deepcopy(CONTENT), status="draft")
    review = SimpleNamespace(id=7, run_no=2, content_hash="old")
    findings = [
        SimpleNamespace(id=1, position=1, severity="critical", criterion="data_accuracy", section="1", location=None,
                        field_path="1.bullets[0].text", title="Sai %", current_text="giảm 0,5%",
                        expected_text="giảm 0,8%", evidence="DB: -0,80%", status="open"),
        SimpleNamespace(id=2, position=2, severity="major", criterion="coverage", section="general", location=None,
                        field_path=None, title="Thiếu tin đấu giá", current_text=None,
                        expected_text="Bổ sung tin đấu giá EEX", evidence="x", status="open"),
    ]

    async def _draft(session, d):
        return report

    async def _rev(session, d, rid):
        return review

    async def _load(session, ids):
        return {7: findings}

    async def _llm(prompt):
        assert "Lỗi #1" in prompt and "Lỗi #2" not in prompt  # lỗi không neo được không gửi LLM
        return {"edits": [{"target": "T1", "old": "giảm 0,5%", "new": "giảm 0,8%", "finding_ids": [1]}]}

    async def _jack(session, d, content):
        return {"overall_score": 90, "issues": []}

    monkeypatch.setattr(fx, "_draft_report", _draft)
    monkeypatch.setattr(fx, "_latest_review", _rev)
    monkeypatch.setattr(fx, "load_findings", _load)
    monkeypatch.setattr(fx, "_call_fix_llm", _llm)
    monkeypatch.setattr(report_qc, "run_report_qc", _jack)

    out = asyncio.run(fx.build_fix_preview(None, "2026-10-19"))
    assert [c["path"] for c in out["changes"]] == ["1.bullets[0].text"]
    assert out["changes"][0]["after"] == "**EUA:** đóng cửa 79,20 EUR/tCO2, giảm 0,8%." and out["changes"][0]["selected"]
    status = {f["id"]: f["status"] for f in out["findings"]}
    assert status == {1: "proposed", 2: "manual"}
    assert report.content == CONTENT  # xem trước không sửa gì
