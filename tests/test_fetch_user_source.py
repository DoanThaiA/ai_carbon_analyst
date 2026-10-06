"""Tool fetch_user_source của Quote Chat (services/source_reader.py): chặn SSRF, 2 tầng fetch."""
import asyncio

import pytest

from services import quote_chat as qc, source_reader as sr


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://localhost:8000/", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/x",
    "http://user:pw@example.com/", "not a url",
])
def test_rejects_non_public_or_invalid_urls(url):
    assert run(sr.validate_public_url(url)) is not None


def test_tool_registered_with_required_url():
    tool = next(t for t in qc.CLIENT_TOOLS if t["name"] == "fetch_user_source")
    assert tool["input_schema"]["required"] == ["url"]


def test_read_blocked_for_internal_address():
    assert "Không truy cập được" in run(sr.read_source("http://127.0.0.1:5432/"))


def _public(monkeypatch):
    async def ok(_url):
        return None
    monkeypatch.setattr(sr, "validate_public_url", ok)


def test_curl_tier_reads_page_and_keeps_table(monkeypatch):
    _public(monkeypatch)
    html = ("<html><body><article><p>" + "CBAM certificate price commentary. " * 10 + "</p></article>"
            "<table><tr><th>Quarter</th><th>Date</th><th>Price</th></tr>"
            "<tr><td>Q3 2026</td><td>5 October 2026</td><td>82.32</td></tr></table></body></html>")

    async def fake_curl(url):
        return html, "text/html", url
    monkeypatch.setattr(sr, "_fetch_curl", fake_curl)
    out = run(sr.read_source("https://example.com/p"))
    assert "Q3 2026 | 5 October 2026 | 82.32" in out and "nguồn NGƯỜI DÙNG cung cấp" in out


def test_falls_back_to_playwright_when_blocked(monkeypatch):
    _public(monkeypatch)

    async def blocked(url):
        raise sr._Blocked("HTTP 401")

    async def browser(url):
        return "<html><body><article><p>" + "Real article text after JS render. " * 10 + "</p></article></body></html>", url
    monkeypatch.setattr(sr, "_fetch_curl", blocked)
    monkeypatch.setattr(sr, "_fetch_playwright", browser)
    assert "Real article text" in run(sr.read_source("https://example.com/p"))


def test_bot_wall_page_is_not_returned_as_content(monkeypatch):
    _public(monkeypatch)
    wall = "<html><body><p>JavaScript is disabled. In order to continue, we need to verify that you're not a robot. " + "x " * 120 + "</p></body></html>"

    async def fake_curl(url):
        return wall, "text/html", url

    async def browser(url):
        return wall, url
    monkeypatch.setattr(sr, "_fetch_curl", fake_curl)
    monkeypatch.setattr(sr, "_fetch_playwright", browser)
    out = run(sr.read_source("https://example.com/p"))
    assert "Không đọc được" in out and "not a robot" not in out


def test_http_404_is_reported_without_playwright(monkeypatch):
    _public(monkeypatch)

    async def gone(url):
        raise ValueError("Nguồn trả về lỗi HTTP 404")
    monkeypatch.setattr(sr, "_fetch_curl", gone)
    assert "404" in run(sr.read_source("https://example.com/p"))
