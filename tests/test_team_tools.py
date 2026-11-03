"""Tool MCP cho chế độ trò chuyện với Sonic – Jenny – Jack (services/team_tools.py) + prompt "Họp nhóm"
(services/claude_connect.py). Không cần DB thật — hàm thuần + mock truy vấn."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.routers import mcp_gateway as gw
from services import quote_chat as qc
from services import team_tools as tt
from services.claude_connect import DESKTOP_PROMPTS, DESKTOP_SERVER_INSTRUCTIONS, TEAM_MEETING_RULES, build_desktop_prompt


def run(coro):
    return asyncio.run(coro)


def _dt(h=1):
    return datetime(2026, 11, 3, h, tzinfo=timezone.utc)


def _report(status="published", content=None, **kw):
    base = dict(status=status, content={"1": {}} if content is None else content, created_at=_dt(0),
                published_at=_dt(1) if status == "published" else None, pdf_emailed_at=None, error_message=None,
                content_en=None, content_en_source_hash=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _review():
    return SimpleNamespace(run_no=1, created_at=_dt(0), overall_score=6.4, verdict="pass_with_fixes",
                           criteria=[{"key": "facts", "label": "Số liệu", "score": 6.0, "weight": 100}])


def _finding(status="open", severity="major"):
    return SimpleNamespace(status=status, severity=severity, criterion="facts")


# ── Đăng ký tool / quyền ──

def test_team_tools_on_mcp_only_and_for_all_tokens():
    names = {t["name"] for t in tt.TEAM_TOOLS}
    assert names == {"get_qc_review", "get_daily_status", "get_crawl_stats", "get_report_lessons"}
    assert not names & {t["name"] for t in qc.CLIENT_TOOLS}  # Jenny chat web không đổi
    for scope in ("user", "qc_agent"):
        exposed = {t["name"] for t in gw.tools_for_token(SimpleNamespace(id=1, scope=scope))}
        assert names <= exposed
    assert names <= {t["name"] for t in gw.tools_for_token(SimpleNamespace(id=1, scope="user"), legacy=True)}


def test_gateway_passes_published_only_by_token_scope(monkeypatch):
    seen = []

    async def fake_exec(session, name, tool_input, *, published_only, settings):
        seen.append((name, published_only))
        return "OK"
    monkeypatch.setattr(gw, "execute_team_tool", fake_exec)
    monkeypatch.setattr(gw, "_rate_limiter", gw.RateLimiter(max_calls=100, window_seconds=60))
    user = SimpleNamespace(id=1, scope="user", user_email="u@x.vn")
    agent = SimpleNamespace(id=2, scope="qc_agent", user_email=None, name="qc")
    res = run(gw.execute_gateway_tool(None, user, "get_qc_review", {}, None))
    assert res.result == "OK" and res.report_date is None
    run(gw.execute_gateway_tool(None, agent, "get_qc_review", {}, None))
    assert seen == [("get_qc_review", True), ("get_qc_review", False)]


# ── get_qc_review: token thường chỉ đọc báo cáo đã phát hành ──

class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *values):
        self.values = list(values)

    async def execute(self, _stmt):
        return _Result(self.values.pop(0))


def test_qc_review_of_draft_is_hidden_for_user_token(monkeypatch):
    called = []

    async def fake_text(_s, d):
        called.append(d)
        return "BIÊN BẢN"

    async def has_review(_s, _d):
        return object()
    monkeypatch.setattr(tt, "qc_review_text", fake_text)
    monkeypatch.setattr(tt, "latest_review", has_review)
    out = run(tt.execute_team_tool(_Session("draft"), "get_qc_review", {"date": "2026-11-03"},
                                   published_only=True, settings=None))
    assert "chưa phát hành" in out and "ĐÃ có biên bản" in out and called == []
    out = run(tt.execute_team_tool(_Session("draft"), "get_qc_review", {"date": "2026-11-03"},
                                   published_only=False, settings=None))
    assert out == "BIÊN BẢN" and called == ["2026-11-03"]


def test_qc_review_defaults_to_latest_published(monkeypatch):
    async def fake_text(_s, d):
        return f"BIÊN BẢN {d}"
    monkeypatch.setattr(tt, "qc_review_text", fake_text)
    out = run(tt.execute_team_tool(_Session("2026-11-02", "published"), "get_qc_review", {},
                                   published_only=True, settings=None))
    assert out == "BIÊN BẢN 2026-11-02"


def test_bad_date_is_reported():
    out = run(tt.execute_team_tool(None, "get_crawl_stats", {"date": "03/11/2026"}, published_only=True, settings=None))
    assert "không hợp lệ" in out


# ── get_daily_status ──

def test_status_lines_hide_qc_details_of_draft():
    lines = "\n".join(tt.report_status_lines(_report("draft"), _review(), [_finding()], "2026-11-03"))
    assert "NHÁP" in lines and "đã có" in lines and "6.40" not in lines and "Đạt" not in lines


def test_status_lines_published_with_fixes():
    findings = [_finding("fixed"), _finding("open", "critical"), _finding("open", "minor")]
    lines = "\n".join(tt.report_status_lines(_report(), _review(), findings, "2026-11-03"))
    assert "ĐÃ PHÁT HÀNH" in lines and "6.40/10" in lines and "đã sửa 1" in lines
    assert "điểm sau sửa" in lines and "còn 1 lỗi nghiêm trọng/quan trọng" in lines


def test_status_lines_missing_generating_failed():
    assert "CHƯA được sinh" in tt.report_status_lines(None, None, [], "2026-11-03")[0]
    gen = tt.report_status_lines(_report("generating"), None, [], "d")[0]
    failed = tt.report_status_lines(_report("failed", error_message="Traceback secret"), None, [], "d")[0]
    assert "ĐANG SINH" in gen and "LỖI" in failed and "secret" not in failed  # không lộ lỗi nội bộ


def test_news_window_matches_report_convention():
    start, end = tt.news_window("2026-11-02")
    assert start == datetime(2026, 11, 2, tzinfo=timezone.utc) and (end - start).days == 1
    # 06:30 giờ VN ngày 03/11 = 23:30 UTC ngày 02/11 → vẫn là khung tin 02/11.
    assert tt.current_window_day(datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)) == "2026-11-02"


# ── get_crawl_stats ──

def _art(i, source="reuters.com", region="international", topic=("eua_ets",), hot=False):
    return SimpleNamespace(id=i, title=f"Bài {i}", source=source, region=region, topic=list(topic),
                           is_hot_news=hot, crawled_at=_dt(i % 5))


def test_crawl_stats_text():
    arts = [_art(1), _art(2, "vnexpress.net", "vietnam", ("vietnam_carbon_policy",)), _art(3, hot=True)]
    out = tt.crawl_stats_text("2026-11-02", arts, {"irrelevant": 7, "duplicate": 2},
                              {"vietnam": 5, "international": 30}, {"reuters.com": "A", "vnexpress.net": "B"})
    assert "báo cáo ngày 2026-11-03" in out
    assert "Đã lưu 3 bài (Việt Nam 1, quốc tế 2)" in out
    assert "reuters.com (hạng A) 2" in out and "[ID 3] Bài 3" in out
    assert "không liên quan EUA 7" in out and "Việt Nam 5" in out


def test_crawl_stats_empty():
    assert "Chưa lưu bài nào" in tt.crawl_stats_text("2026-11-02", [], {}, {}, {})


# ── get_report_lessons ──

def test_lessons_text_hides_reporter():
    lesson = SimpleNamespace(id=5, report_date="2026-10-30", section="3", mistake="R:R sai", correction="R:R 1,2",
                             lesson="Tính R:R từ giữa vùng Entry", reported_by="secret@x.vn")
    out = tt.lessons_text([lesson], 30)
    assert "#5" in out and "Tính R:R" in out and "Mục 3" in out and "secret@x.vn" not in out
    assert "Không có bài học" in tt.lessons_text([], 30)


# ── Prompt "Họp nhóm" ──

def test_team_meeting_prompt_and_instructions():
    assert [p["name"] for p in DESKTOP_PROMPTS] == ["jenny", "hop_nhom"]
    now = datetime(2026, 11, 3, 2, tzinfo=timezone.utc)
    opening = build_desktop_prompt("hop_nhom", {}, now=now)
    assert TEAM_MEETING_RULES in opening and "get_daily_status" in opening and "03/11/2026" in opening
    topic = build_desktop_prompt("hop_nhom", {"chu_de": "QC báo cáo\nhôm nay " + "x" * 500}, now=now)
    assert '"QC báo cáo hôm nay' in topic and "x" * 400 not in topic
    assert TEAM_MEETING_RULES in DESKTOP_SERVER_INSTRUCTIONS
    for tool in ("get_qc_review", "get_daily_status", "get_crawl_stats", "get_report_lessons"):
        assert tool in DESKTOP_SERVER_INSTRUCTIONS


def test_team_prompt_does_not_leak_eua_framework():
    from services import eua_causal_chains as chains
    text = build_desktop_prompt("hop_nhom", {})
    for block_id in ("INFERENCE_RULES", "FUEL_SWITCHING", "POLICY_MSR"):
        assert chains.get_block(block_id).strip()[:80] not in text
