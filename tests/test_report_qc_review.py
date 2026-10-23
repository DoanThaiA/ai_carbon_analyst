"""Biên bản QC Claude/Jack qua MCP: chuẩn hóa, tính điểm, kết luận, quyền tool theo token."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routers import mcp_gateway
from services import quote_chat
from services import report_qc_review as qr


def _criteria(score=8.5):
    return [{"key": c["key"], "score": score, "comment": "ổn"} for c in qr.QC_CRITERIA]


def _payload(**kw):
    base = {"report_date": "2026-10-19", "assignment_id": "x", "verdict": "pass", "summary": "Tốt.",
            "criteria": _criteria(), "findings": []}
    base.update(kw)
    return base


def test_rubric_weights_sum_to_100():
    assert sum(c["weight"] for c in qr.QC_CRITERIA) == 100
    assert len(set(qr.CRITERIA_KEYS)) == len(qr.QC_CRITERIA)


def test_overall_score_recomputed_by_weight():
    crit = _criteria(10)
    crit[1]["score"] = 0  # analysis_logic 25%
    data = qr.normalize_submission(_payload(criteria=crit, overall_score=10))
    assert data["overall_score"] == 7.5
    assert [c["key"] for c in data["criteria"]] == list(qr.CRITERIA_KEYS)  # đúng thứ tự rubric


def test_missing_criteria_and_bad_findings_rejected_together():
    with pytest.raises(qr.QcSubmissionError) as e:
        qr.normalize_submission(_payload(
            criteria=_criteria()[:5],
            findings=[{"severity": "critical", "criterion": "data_accuracy", "title": "Sai giá EUA"},
                      {"severity": "huge", "criterion": "x", "title": "?"}],
        ))
    msg = str(e.value)
    assert "Thiếu tiêu chí: biz, presentation" in msg
    assert "phải có `evidence`" in msg and "Finding #2" in msg


def test_verdict_never_softer_than_claude_and_rules():
    crit = _criteria(9)
    major = {"severity": "major", "criterion": "coverage", "title": "Thiếu tin MSR", "evidence": "bài X"}
    assert qr.normalize_submission(_payload(criteria=crit))["verdict"] == "pass"
    assert qr.normalize_submission(_payload(criteria=crit, findings=[major]))["verdict"] == "pass_with_fixes"
    assert qr.normalize_submission(_payload(criteria=crit, verdict="fail"))["verdict"] == "fail"
    crit_low = _criteria(5.5)
    assert qr.normalize_submission(_payload(criteria=crit_low))["verdict"] == "fail"
    verified_crit = {"severity": "critical", "criterion": "data_accuracy", "title": "Giá EUA sai",
                     "evidence": "DB 85,41 ≠ 86,41", "verification": "verified"}
    data = qr.normalize_submission(_payload(criteria=crit, findings=[verified_crit]))
    assert data["verdict"] == "fail" and data["claude_verdict"] == "pass"
    unverified_crit = {**verified_crit, "verification": "unverified"}
    assert qr.normalize_submission(_payload(criteria=crit, findings=[unverified_crit]))["verdict"] == "pass_with_fixes"


def test_section_normalization_and_fingerprint():
    assert qr.normalize_section("Mục 3") == "3"
    assert qr.normalize_section("Diễn biến chính #2") == "dev"
    assert qr.normalize_section("forecast_review") == "forecast"
    assert qr.normalize_section("chỗ lạ") == "general"
    a = qr.finding_fingerprint("analysis_logic", "3", "Sai chiều: API2 tăng 3,75% xếp vào hỗ trợ")
    b = qr.finding_fingerprint("analysis_logic", "3", "API2 tăng 2,1% — xếp vào hỗ trợ, sai chiều")
    assert a == b
    assert a != qr.finding_fingerprint("analysis_logic", "2", "Sai chiều: API2 tăng 3,75% xếp vào hỗ trợ")


def test_content_hash_stable_regardless_of_key_order():
    assert qr.content_hash({"a": 1, "b": [1, 2]}) == qr.content_hash({"b": [1, 2], "a": 1})
    assert qr.content_hash({"a": 1}) != qr.content_hash({"a": 2})


def test_source_refs_and_text_limits():
    f = {"severity": "minor", "criterion": "presentation", "title": "x" * 500,
         "source_refs": ["https://a.example/1", {"name": "Reuters", "url": "https://r.example"}]}
    data = qr.normalize_submission(_payload(findings=[f]))
    got = data["findings"][0]
    assert len(got["title"]) == 300
    assert got["source_refs"] == [{"label": "https://a.example/1", "url": "https://a.example/1"},
                                  {"label": "Reuters", "url": "https://r.example"}]


def test_qc_tools_only_for_qc_agent_token():
    user = SimpleNamespace(id=1, scope="user", name="u", user_email="a@b")
    agent = SimpleNamespace(id=2, scope="qc_agent", name="qc", user_email="admin")
    user_names = {t["name"] for t in mcp_gateway.tools_for_token(user)}
    agent_names = {t["name"] for t in mcp_gateway.tools_for_token(agent)}
    assert not user_names & qr.QC_AGENT_TOOL_NAMES
    assert qr.QC_AGENT_TOOL_NAMES <= agent_names
    assert mcp_gateway.prompt_text_for_token(user, "jack_qc_review", {}) is None
    assert "submit_qc_review" in mcp_gateway.prompt_text_for_token(agent, "jack_qc_review", {})
    assert "jack_qc_review" not in {p["name"] for p in mcp_gateway.prompts_for_token(user)}
    with pytest.raises(HTTPException) as e:
        asyncio.run(mcp_gateway.execute_gateway_tool(None, user, "submit_qc_review", {}, None))
    assert e.value.status_code == 404


def test_jenny_qc_review_tool_admin_only():
    assert "get_qc_review" in {t["name"] for t in quote_chat.ADMIN_TOOLS}
    assert "get_qc_review" not in {t["name"] for t in quote_chat.CLIENT_TOOLS}
    out = asyncio.run(quote_chat._execute_client_tool(
        "get_qc_review", {}, None, "2026-10-19", tool_cache={}, chart_cache={}, allow_jack_qc=True))
    assert "chỉ dành cho quản trị viên" in out


def test_routine_prompt_mentions_deadline_and_flow():
    p = qr.QC_ROUTINE_PROMPT
    assert "get_qc_assignment" in p and "submit_qc_review" in p and "07:50" in p and "07:55" in p and "08:15" in p
    schema = qr.submission_schema_text()
    for c in qr.QC_CRITERIA:
        assert f"`{c['key']}`" in schema


def test_policy_tracker_sources_not_stale_in_section9():
    from datetime import datetime, timezone
    from services import report_qc
    old = "https://climate.ec.europa.eu/auction-calendar"
    content = {"3": {"sources": [{"source_name": "EC", "source_url": old}]},
               "9": {"items": [{"url": old, "title": "x", "source": "climate.ec.europa.eu"}]}}
    meta = {report_qc._norm_url(old): {"source": "climate.ec.europa.eu",
                                        "published_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}}
    window_start = datetime(2026, 10, 17, tzinfo=timezone.utc)
    stale = report_qc.check_sources(content, [old], meta, window_start, datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert not [i for i in stale if "tin CŨ" in i["message"]]
    # Cũ hơn cả cửa sổ quét chính sách → vẫn là tin cũ.
    meta[report_qc._norm_url(old)]["published_at"] = datetime(2026, 9, 1, tzinfo=timezone.utc)
    stale = report_qc.check_sources(content, [old], meta, window_start, datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert [i for i in stale if "tin CŨ" in i["message"]]
    assert report_qc._norm_url(old) in report_qc.policy_window_urls(content)


# Bản 18/10: tên hồ sơ có ngoặc "COM(2026) 616" làm lặp "(Nguồn: …) (Nguồn: …)" và Jack báo nhầm số 616.
WATCH4 = ("4. Tiến độ đàm phán COM(2026) 616 tại Hội đồng và Nghị viện (kéo dài phân bổ miễn phí tới 2038, thêm 6 tỷ EUR "
          "phân bổ miễn phí 2026–2030). (Nguồn: Ủy ban châu Âu, COM(2026) 616, 07/2026) (Nguồn: argusmedia.com)")


def test_inline_sources_balanced_parentheses():
    from services import report_consistency as rc
    assert rc.inline_source_names(WATCH4) == ["Ủy ban châu Âu, COM(2026) 616, 07/2026", "argusmedia.com"]
    merged = rc.merge_adjacent_sources(WATCH4)
    assert merged.endswith("(Nguồn: Ủy ban châu Âu, COM(2026) 616, 07/2026; argusmedia.com)")
    assert merged.count("(Nguồn:") == 1
    stripped = rc.strip_inline_sources(WATCH4)
    assert "Nguồn" not in stripped and "07/2026)" not in stripped
    # Không liền nhau thì không gộp; cụm không đóng ngoặc bị bỏ qua.
    assert rc.merge_adjacent_sources("a (Nguồn: X) b (Nguồn: Y)") == "a (Nguồn: X) b (Nguồn: Y)"
    assert rc.inline_source_names("a (Nguồn: X") == []


def test_jack_no_false_alarm_on_legal_doc_id():
    from services import report_qc
    argus = "https://www.argusmedia.com/x"
    content = {"3": {"sources": [{"source_name": "argusmedia.com", "source_url": argus}],
                     "analysis_blocks": [{"heading": "Cần theo dõi", "content": WATCH4}]}}
    texts = {report_qc._norm_url(argus): "Commission warns against ETS price controls under Article 29a."}
    msgs = [i["message"] for i in report_qc.check_facts(content, texts)]
    assert not [m for m in msgs if "616" in m]
    assert not [m for m in msgs if "COM(2026;" in m]


def _review(verdict="fail", content_hash="h1"):
    return SimpleNamespace(run_no=1, verdict=verdict, overall_score=5.5, content_hash=content_hash)


def _finding(status, severity="major"):
    return SimpleNamespace(status=status, severity=severity)


def test_gate_decision_fail_blocks_until_heavy_findings_fixed():
    assert qr.gate_decision(None, [])[0] is True
    assert qr.gate_decision(_review("pass_with_fixes"), [_finding("open")])[0] is True
    # Không đạt, còn lỗi nặng mở → chặn
    assert qr.gate_decision(_review(), [_finding("fixed"), _finding("open", "critical")])[0] is False
    # Không đạt, chưa sửa gì (chỉ còn lỗi nhỏ) → vẫn chặn
    assert qr.gate_decision(_review(), [_finding("open", "minor")])[0] is False
    # Jenny/admin đã sửa hết lỗi nặng (lỗi nhỏ còn mở, wont_fix bỏ qua) → xuất bản
    ok, reason = qr.gate_decision(_review(), [_finding("fixed", "critical"), _finding("wont_fix"),
                                             _finding("open", "minor")])
    assert ok is True and "đã sửa hết" in reason


def test_auto_fix_skips_when_admin_already_edited():
    from services.report_qc_fix import auto_fix_skip_reason
    assert auto_fix_skip_reason("draft", "h1", _review(), [1]) is None
    assert "admin" in auto_fix_skip_reason("draft", "h2", _review(), [1])
    assert auto_fix_skip_reason("published", "h1", _review(), [1])
    assert auto_fix_skip_reason("draft", "h1", None, [1]) == "chưa có biên bản QC"
    assert auto_fix_skip_reason("draft", "h1", _review(), [])
