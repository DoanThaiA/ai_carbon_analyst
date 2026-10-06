"""Tool fetch_user_source của Quote Chat: chặn SSRF + đọc nguồn do người dùng cung cấp."""
import asyncio

import httpx
import pytest

from services import quote_chat as qc


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://localhost:8000/", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/x",
    "http://user:pw@example.com/", "not a url",
])
def test_rejects_non_public_or_invalid_urls(url):
    assert run(qc._validate_public_url(url)) is not None


def test_tool_registered_with_required_url():
    tool = next(t for t in qc.CLIENT_TOOLS if t["name"] == "fetch_user_source")
    assert tool["input_schema"]["required"] == ["url"]


def test_fetch_blocked_for_internal_address():
    out = run(qc._tool_fetch_user_source_text("http://127.0.0.1:5432/"))
    assert "Không truy cập được" in out


def test_fetch_reads_text_source(monkeypatch):
    async def ok(_url):
        return None
    monkeypatch.setattr(qc, "_validate_public_url", ok)
    transport = httpx.MockTransport(lambda req: httpx.Response(
        200, headers={"content-type": "text/plain; charset=utf-8"},
        text="CBAM certificate price Q2 2026: 82.32 EUR/tCO2 " * 3))
    real = httpx.AsyncClient
    monkeypatch.setattr(qc.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    out = run(qc._tool_fetch_user_source_text("https://example.com/p"))
    assert "82.32" in out and "nguồn NGƯỜI DÙNG cung cấp" in out


def test_fetch_http_error_message(monkeypatch):
    async def ok(_url):
        return None
    monkeypatch.setattr(qc, "_validate_public_url", ok)
    transport = httpx.MockTransport(lambda req: httpx.Response(403))
    real = httpx.AsyncClient
    monkeypatch.setattr(qc.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    assert "HTTP 403" in run(qc._tool_fetch_user_source_text("https://example.com/p"))
