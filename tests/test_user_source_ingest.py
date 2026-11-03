"""Tool MCP crawl_user_source (services/user_source_ingest.py + api/routers/mcp_gateway.py): Sonic crawl 1 link
người dùng đưa → lưu vào kho tin qua đúng pipeline crawl. Không cần DB/LLM thật — mock các bước I/O."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routers import mcp_gateway as gw
from schemas.crawl_models import NewsTopic, PipelineResult
from services import quote_chat as qc
from services import user_source_ingest as ing
from services.claude_connect import DESKTOP_SERVER_INSTRUCTIONS, SONIC_CRAWL_DESKTOP_TOOL
from services.source_reader import FetchedPage, SourceUnreadable

ARTICLE_HTML = (
    "<html><head><title>EU carbon price jumps</title></head><body><article><h1>EU carbon price jumps</h1><p>"
    + "EU carbon allowances rose after the auction cleared above expectations. " * 20
    + "</p></article></body></html>"
)


def run(coro):
    return asyncio.run(coro)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _factory():
    return _FakeSession()


@pytest.fixture
def pipeline(monkeypatch):
    """Mock fetch + DB lookup + _dedupe_classify_store; trả dict để test chỉnh kết quả pipeline."""
    state = {"existing": None, "result": None, "articles": [], "fetched": []}

    async def fake_existing(_session, urls, content_hash=None):
        return state["existing"]

    async def fake_meta(_session, domain):
        return "C", "vietnam" if domain.endswith(".vn") else "international"

    async def fake_fetch(url, *, playwright_timeout):
        state["fetched"].append(url)
        return FetchedPage(ARTICLE_HTML, "text/html", url, "text", "curl")

    async def fake_store(ctx, article):
        state["articles"].append(article)
        return state["result"] or PipelineResult(
            url=article.url, status="stored", article_id=42, topics=[NewsTopic.EUA_ETS], content_hash="h",
        )

    monkeypatch.setattr(ing, "_existing_article", fake_existing)
    monkeypatch.setattr(ing, "_source_meta", fake_meta)
    monkeypatch.setattr(ing, "fetch_source_page", fake_fetch)
    monkeypatch.setattr(ing, "_dedupe_classify_store", fake_store)
    monkeypatch.setattr(ing, "_get_classifier", lambda: None)
    monkeypatch.setattr(ing, "_get_embedder", lambda: None)
    return state


def test_stores_article_through_pipeline(pipeline):
    out = run(ing.ingest_user_url(_factory, "https://www.example.com/eua", submitted_by="u@x.vn"))
    assert "ĐÃ LƯU VÀO KHO TIN" in out and "ID bài: 42" in out and "eua_ets" in out
    art = pipeline["articles"][0]
    assert art.source_domain == "example.com" and art.tier == "C" and art.region == "international"


def test_vietnamese_domain_gets_vietnam_region(pipeline):
    run(ing.ingest_user_url(_factory, "https://vnexpress.vn/bai-viet", submitted_by="u@x.vn"))
    assert pipeline["articles"][0].region == "vietnam"


def test_existing_url_skips_fetch(pipeline):
    pipeline["existing"] = SimpleNamespace(
        id=7, title="Old", source="example.com", url="https://example.com/eua", published_at=None,
        crawled_at=datetime(2026, 11, 1, tzinfo=timezone.utc), topic=["cbam"],
    )
    out = run(ing.ingest_user_url(_factory, "https://example.com/eua", submitted_by="u@x.vn"))
    assert "ĐÃ CÓ TRONG KHO TIN" in out and "ID bài: 7" in out
    assert pipeline["fetched"] == [] and pipeline["articles"] == []


def test_irrelevant_is_not_stored(pipeline):
    pipeline["result"] = PipelineResult(url="u", status="irrelevant", content_hash="h")
    out = run(ing.ingest_user_url(_factory, "https://example.com/football", submitted_by="u@x.vn"))
    assert out.startswith("KHÔNG LƯU") and "EUA" in out


def test_unreadable_source_reports_reason(pipeline, monkeypatch):
    async def blocked(url, *, playwright_timeout):
        raise SourceUnreadable("Không truy cập được nguồn này: Địa chỉ này thuộc mạng nội bộ.")
    monkeypatch.setattr(ing, "fetch_source_page", blocked)
    out = run(ing.ingest_user_url(_factory, "http://127.0.0.1/", submitted_by="u@x.vn"))
    assert out.startswith("KHÔNG LƯU") and "mạng nội bộ" in out and pipeline["articles"] == []


def test_non_html_source_is_rejected(pipeline, monkeypatch):
    async def csv(url, *, playwright_timeout):
        return FetchedPage("a,b\n1,2", "text/csv", url, "a,b", "curl")
    monkeypatch.setattr(ing, "fetch_source_page", csv)
    out = run(ing.ingest_user_url(_factory, "https://example.com/data.csv", submitted_by="u@x.vn"))
    assert "text/csv" in out and pipeline["articles"] == []


def test_listing_page_without_article_text_is_rejected(pipeline, monkeypatch):
    async def short(url, *, playwright_timeout):
        return FetchedPage("<html><body><a href='/a'>A</a></body></html>", "text/html", url, "A", "curl")
    monkeypatch.setattr(ing, "fetch_source_page", short)
    out = run(ing.ingest_user_url(_factory, "https://example.com/", submitted_by="u@x.vn"))
    assert "không trích được nội dung" in out and pipeline["articles"] == []


def test_report_note_for_stale_article():
    now = datetime(2026, 11, 3, 3, tzinfo=timezone.utc)
    assert "KHÔNG được đưa vào báo cáo" in ing._report_note(now - timedelta(days=30), now)
    assert "báo cáo ngày 2026-11-04" in ing._report_note(now - timedelta(hours=2), now)


# ── Gateway ──

def test_tool_only_on_mcp_and_only_for_user_tokens():
    assert "crawl_user_source" not in {t["name"] for t in qc.CLIENT_TOOLS}  # Jenny chat web không có tool ghi
    user, qc_agent = SimpleNamespace(id=1, scope="user"), SimpleNamespace(id=2, scope="qc_agent")
    assert "crawl_user_source" in {t["name"] for t in gw.tools_for_token(user)}
    assert "crawl_user_source" in {t["name"] for t in gw.tools_for_token(user, legacy=True)}
    assert "crawl_user_source" not in {t["name"] for t in gw.tools_for_token(qc_agent)}
    assert SONIC_CRAWL_DESKTOP_TOOL["input_schema"]["required"] == ["url"]
    assert "crawl_user_source" in DESKTOP_SERVER_INSTRUCTIONS


def test_gateway_runs_ingest_for_user_and_404_for_qc_agent(monkeypatch):
    calls = []

    async def fake_ingest(factory, url, *, submitted_by):
        calls.append((url, submitted_by))
        return "ĐÃ LƯU VÀO KHO TIN."
    monkeypatch.setattr(gw, "ingest_user_url", fake_ingest)
    monkeypatch.setattr(gw, "_rate_limiter", gw.RateLimiter(max_calls=100, window_seconds=60))
    monkeypatch.setattr(gw, "_crawl_rate_limiter", gw.RateLimiter(max_calls=1, window_seconds=3600))

    user = SimpleNamespace(id=1, scope="user", user_email="u@x.vn")
    res = run(gw.execute_gateway_tool(None, user, "crawl_user_source", {"url": "https://e.com/a"}, None))
    assert res.result == "ĐÃ LƯU VÀO KHO TIN." and res.report_date is None
    assert calls == [("https://e.com/a", "u@x.vn")]

    limited = run(gw.execute_gateway_tool(None, user, "crawl_user_source", {"url": "https://e.com/b"}, None))
    assert "link trong 1 giờ" in limited.result and len(calls) == 1

    with pytest.raises(HTTPException) as e:
        run(gw.execute_gateway_tool(None, SimpleNamespace(id=2, scope="qc_agent", user_email=None, name="qc"),
                                    "crawl_user_source", {"url": "https://e.com/a"}, None))
    assert e.value.status_code == 404
