"""MCP server qua HTTP (Streamable HTTP) — để app Claude trên mobile/claude.ai dùng được.

Claude mobile chỉ nối MCP từ xa (Connectors) và không cho đặt header, nên token cá nhân
(`cat_...`, cùng bảng `api_tokens` với client stdio) nằm trong ĐƯỜNG DẪN: `/mcp/{token}`.
Coi URL này như mật khẩu — thu hồi token ở trang /connect-claude là URL chết ngay.

Chạy stateless (mỗi request độc lập, không giữ session trong RAM) nên không phụ thuộc số worker
uvicorn. Hệ quả: không nhớ `report_date` của handoff giữa các lần gọi như client stdio — kết quả
`get_handoff` nhắc Claude tự truyền `report_date` ở các tool sau.

Tái dùng NGUYÊN logic của gateway (`api/routers/mcp_gateway.py`): cùng danh sách tool, rate limit,
quyền (chỉ báo cáo published) và `DESKTOP_SERVER_INSTRUCTIONS`.
"""
import contextvars
import copy
import json
import logging
from typing import Any, Dict, List, Optional

import mcp.types as types
from fastapi import HTTPException
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from api.deps import async_session_maker
from api.routers.mcp_gateway import _DESKTOP_TOOLS, execute_gateway_tool
from db.models import ApiToken
from services.claude_connect import (
    DESKTOP_PROMPTS,
    DESKTOP_SERVER_INSTRUCTIONS,
    authenticate_token,
    build_desktop_prompt,
    build_handoff_instructions,
    consume_handoff,
)

logger = logging.getLogger(__name__)

# Token của request hiện tại — set bởi McpHttpApp sau khi xác thực, đọc trong handler của Server.
_current_token: contextvars.ContextVar[Optional[ApiToken]] = contextvars.ContextVar("mcp_http_token", default=None)

GET_HANDOFF_TOOL = types.Tool(
    name="get_handoff",
    description=(
        "Lấy đoạn trích (người dùng bôi đen) + yêu cầu mà người dùng vừa gửi từ báo cáo Carbon Analyst sang "
        "đây. GỌI ĐẦU TIÊN khi người dùng đưa handoff_id; kết quả gồm hướng dẫn thực hiện, ngày hôm nay và "
        "ngày báo cáo."
    ),
    inputSchema={
        "type": "object",
        "properties": {"handoff_id": {"type": "string", "description": "Mã handoff người dùng cung cấp."}},
        "required": ["handoff_id"],
    },
)

REPORT_DATE_SCHEMA = {
    "type": "string",
    "description": (
        "TUỲ CHỌN — YYYY-MM-DD của báo cáo đang làm việc. Không truyền = báo cáo published mới nhất. "
        "Nếu đang làm theo handoff, truyền ngày báo cáo của handoff. Kết quả tool luôn ghi rõ ngày báo cáo "
        "đã dùng — nếu không phải báo cáo người dùng đang nói tới, gọi lại kèm report_date."
    ),
}

_HANDOFF_HTTP_NOTE = (
    "\n\nLƯU Ý KẾT NỐI: kết nối này không nhớ ngữ cảnh giữa các lần gọi tool — khi gọi tool khác của "
    "carbon-analyst cho báo cáo này, luôn truyền report_date=\"{date}\"."
)


def _to_mcp_tool(defn: Dict[str, Any]) -> types.Tool:
    schema = copy.deepcopy(defn["input_schema"])
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})["report_date"] = REPORT_DATE_SCHEMA
    return types.Tool(name=defn["name"], description=defn["description"], inputSchema=schema)


def _error_text(exc: HTTPException) -> str:
    detail = exc.detail
    return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)


def build_server() -> Server:
    server: Server = Server("carbon-analyst", instructions=DESKTOP_SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        return [GET_HANDOFF_TOOL] + [_to_mcp_tool(d) for d in _DESKTOP_TOOLS]

    @server.list_prompts()
    async def list_prompts() -> List[types.Prompt]:
        return [
            types.Prompt(
                name=p["name"],
                title=p.get("title"),
                description=p.get("description"),
                arguments=[
                    types.PromptArgument(name=a["name"], description=a.get("description"), required=a.get("required", False))
                    for a in p.get("arguments", [])
                ],
            )
            for p in DESKTOP_PROMPTS
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: Optional[Dict[str, str]]) -> types.GetPromptResult:
        text = build_desktop_prompt(name, dict(arguments or {}))
        if text is None:
            raise RuntimeError(f"Prompt không tồn tại: {name}")
        title = next(p["title"] for p in DESKTOP_PROMPTS if p["name"] == name)
        return types.GetPromptResult(
            description=title,
            messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=text))],
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
        token = _current_token.get()
        if token is None:  # không xảy ra: McpHttpApp luôn xác thực trước khi vào đây
            raise RuntimeError("Thiếu API token.")
        arguments = dict(arguments or {})
        try:
            async with async_session_maker() as session:
                if name == "get_handoff":
                    handoff = await consume_handoff(
                        session, handoff_id=str(arguments.get("handoff_id", "")).strip(), user_email=token.user_email
                    )
                    if handoff is None:
                        raise HTTPException(
                            status_code=404, detail="Handoff không tồn tại, đã hết hạn hoặc không thuộc về bạn."
                        )
                    text = build_handoff_instructions(handoff) + _HANDOFF_HTTP_NOTE.format(date=handoff.report_date)
                else:
                    explicit = arguments.pop("report_date", None)
                    res = await execute_gateway_tool(session, token, name, arguments, explicit)
                    text = res.result
                    if res.report_date:
                        origin = "theo yêu cầu" if explicit else "báo cáo published mới nhất"
                        text = f"[Báo cáo ngữ cảnh: {res.report_date} — {origin}]\n{text}"
        except HTTPException as e:
            raise RuntimeError(_error_text(e)) from e
        return [types.TextContent(type="text", text=text)]

    return server


_session_manager = StreamableHTTPSessionManager(app=build_server(), stateless=True, json_response=True)


def session_manager() -> StreamableHTTPSessionManager:
    """Để lifespan của api/main.py chạy `async with session_manager().run():`."""
    return _session_manager


async def _reply(send, status: int, message: str) -> None:
    body = json.dumps({"detail": message}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class McpHttpApp:
    """ASGI app mount tại /mcp: lấy token từ phần đường dẫn còn lại (`/{token}`), xác thực rồi giao
    cho StreamableHTTPSessionManager."""

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        path = scope["path"]
        root = scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):]
        raw = path.strip("/")
        if not raw or "/" in raw:
            await _reply(send, 404, "Không tìm thấy — dùng URL dạng /mcp/<token> lấy tại trang Kết nối Claude.")
            return

        async with async_session_maker() as session:
            token = await authenticate_token(session, raw)
        if token is None:
            await _reply(send, 401, "API token không hợp lệ, đã hết hạn hoặc bị thu hồi — tạo token mới trên web.")
            return

        ctx = _current_token.set(token)
        try:
            await _session_manager.handle_request({**scope, "path": "/"}, receive, send)
        finally:
            _current_token.reset(ctx)
