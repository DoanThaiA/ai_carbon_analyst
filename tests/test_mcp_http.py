"""MCP qua HTTP (api/mcp_http.py): xác thực token trong URL + JSON-RPC tools/list, tools/call."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from api import mcp_http
from schemas.claude_models import GatewayToolResult

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def client(monkeypatch):
    good = SimpleNamespace(id=1, user_email="u@x.vn")

    async def fake_auth(_session, raw):
        return good if raw == "cat_good" else None

    calls = []

    async def fake_exec(_session, token, name, tool_input, report_date):
        calls.append((token.user_email, name, tool_input, report_date))
        return GatewayToolResult(result="KQ", report_date="2026-10-14")

    monkeypatch.setattr(mcp_http, "authenticate_token", fake_auth)
    monkeypatch.setattr(mcp_http, "async_session_maker", lambda: _FakeSession())
    monkeypatch.setattr(mcp_http, "execute_gateway_tool", fake_exec)

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_http.session_manager().run():
            yield

    # StreamableHTTPSessionManager.run() chỉ gọi được 1 lần/instance — dựng mới cho mỗi test.
    monkeypatch.setattr(
        mcp_http, "_session_manager",
        mcp_http.StreamableHTTPSessionManager(app=mcp_http.build_server(), stateless=True, json_response=True),
    )
    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", mcp_http.McpHttpApp())
    return app, calls


async def _apost(app, path, body):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post(path, json=body, headers=HEADERS)


def _post(app, path, body):
    return asyncio.run(_apost(app, path, body))


def test_bad_token_is_401(client):
    app, _ = client
    r = _post(app, "/mcp/cat_bad", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_missing_token_is_404(client):
    app, _ = client
    r = _post(app, "/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 404


def test_tools_list_has_get_handoff_and_report_date(client):
    app, _ = client
    r = _post(app, "/mcp/cat_good", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert "get_handoff" in tools and "jack_qc" in tools
    assert "report_date" in tools["search_news"]["inputSchema"]["properties"]


def test_tools_call_passes_report_date_and_prefixes_context(client):
    app, calls = client
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_reports", "arguments": {"report_date": "2026-10-14", "x": 1}}}
    r = _post(app, "/mcp/cat_good", body)
    text = r.json()["result"]["content"][0]["text"]
    assert text.startswith("[Báo cáo ngữ cảnh: 2026-10-14") and text.endswith("KQ")
    assert calls == [("u@x.vn", "list_reports", {"x": 1}, "2026-10-14")]
