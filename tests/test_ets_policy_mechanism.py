"""Bảng "Theo dõi chính sách ETS/MSR": nhãn chiều ↔ câu cơ chế (lỗi 21/10/2026 — nhãn "Áp lực EUA" mà câu cơ chế kết thúc
"→ hỗ trợ EUA" vì dính nhánh "nếu Hội đồng BÁC")."""
from services import report_generator as rg
from services import report_qc

MERGED = ("Giảm/tạm dừng chuyển hạn ngạch vào MSR → giữ thêm hạn ngạch trên thị trường đấu giá → NỚI cung; "
          "nếu Hội đồng BÁC đề nghị → cung đấu giá giữ lộ trình → hỗ trợ EUA")


def test_split_rejected_branch():
    main, rejected = rg.split_policy_mechanism(MERGED)
    assert main.endswith("NỚI cung") and "hỗ trợ" not in main
    assert rejected == "Hội đồng BÁC đề nghị → cung đấu giá giữ lộ trình → hỗ trợ EUA"
    assert rg.split_policy_mechanism("Xả hạn ngạch từ MSR → nới cung") == ("Xả hạn ngạch từ MSR → nới cung", None)
    assert rg.split_policy_mechanism("A. Nếu bị từ chối → B") == ("A", "Bị từ chối → B")
    assert rg.split_policy_mechanism(None) == (None, None)


def test_yaml_dossiers_have_consistent_labels():
    ds = rg._load_ets_dossiers()
    it_cz = next(d for d in ds if "Ý–Séc" in d["item"])
    assert it_cz["impact"] == "giảm" and "hỗ trợ" not in it_cz["mechanism"]
    assert "hỗ trợ EUA" in it_cz["mechanism_if_rejected"]
    assert report_qc.check_ets_policy_impact({"3": {"ets_policy": ds}}) == []


def test_llm_item_split_on_resolve():
    lookup = {1: {"source": "Reuters", "url": "https://x/1", "published_date": "2026-10-20"}}
    out = rg._resolve_ets_policy([{"item": "Đề nghị tạm dừng chuyển vào MSR (Ba Lan)", "impact": "giảm",
                                   "mechanism": MERGED, "source_index": 1}], lookup, [])
    assert "hỗ trợ" not in out[0]["mechanism"] and out[0]["mechanism_if_rejected"].startswith("Hội đồng BÁC")


def test_jack_flags_label_opposite_to_mechanism():
    ok_old = {"3": {"ets_policy": [{"item": "Ý–Séc", "impact": "giảm", "mechanism": MERGED}]}}
    assert report_qc.check_ets_policy_impact(ok_old) == []  # nhánh "bị bác" không tính
    negated = {"3": {"ets_policy": [{"item": "MSR", "impact": "giảm",
                                     "mechanism": "Nâng ngưỡng vô hiệu hóa → NỚI cung — KHÔNG phải yếu tố siết cung/hỗ trợ EUA"}]}}
    assert report_qc.check_ets_policy_impact(negated) == []
    bad = {"3": {"ets_policy": [
        {"item": "X", "impact": "giảm", "mechanism": "Tăng chuyển vào MSR → SIẾT cung → hỗ trợ EUA"},
        {"item": "Y", "impact": "tăng", "mechanism": "Xả hạn ngạch từ MSR → nới cung"},
        {"item": "Z", "impact": "trung lập", "kind": "event", "mechanism": "Chưa quyết định → hỗ trợ EUA nếu bác"},
    ]}}
    issues = report_qc.check_ets_policy_impact(bad)
    assert [i["field_path"] for i in issues] == ["3.ets_policy[0].mechanism", "3.ets_policy[1].mechanism"]
