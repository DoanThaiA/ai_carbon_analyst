"""MCP server (stdio) cho Claude Desktop.

Danh sách tool lấy ĐỘNG từ backend (GET /tools) nên thêm/sửa tool ở server không
cần cập nhật gói này trên từng máy. Riêng `get_handoff` do client định nghĩa: nó
nhớ `report_date` của handoff để các tool sau mặc định đúng báo cáo đang làm.
"""
import asyncio
import copy
import sys
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from carbon_analyst_mcp import __version__
from carbon_analyst_mcp.client import GatewayClient, GatewayError

GET_HANDOFF_TOOL = types.Tool(
    name="get_handoff",
    description=(
        "Lấy đoạn trích + yêu cầu mà người dùng vừa gửi từ báo cáo Carbon Analyst sang đây. "
        "GỌI ĐẦU TIÊN khi người dùng đưa handoff_id; kết quả gồm hướng dẫn thực hiện."
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
        "TUỲ CHỌN — YYYY-MM-DD của báo cáo đang làm việc. Không truyền = dùng báo cáo của handoff "
        "vừa lấy (hoặc báo cáo mới nhất nếu chưa có handoff)."
    ),
}


def _to_mcp_tool(defn: Dict[str, Any]) -> types.Tool:
    schema = copy.deepcopy(defn["input_schema"])
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})["report_date"] = REPORT_DATE_SCHEMA
    return types.Tool(name=defn["name"], description=defn["description"], inputSchema=schema)


def build_server(gateway: GatewayClient) -> Server:
    server: Server = Server("carbon-analyst")
    state: Dict[str, Optional[str]] = {"report_date": None}

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        try:
            remote = await gateway.list_tools()
        except GatewayError as e:
            raise RuntimeError(str(e)) from e
        return [GET_HANDOFF_TOOL] + [_to_mcp_tool(d) for d in remote]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
        arguments = dict(arguments or {})
        try:
            if name == "get_handoff":
                payload = await gateway.get_handoff(str(arguments.get("handoff_id", "")).strip())
                state["report_date"] = payload["report_date"]
                text = payload["instructions"]
            else:
                report_date = arguments.pop("report_date", None) or state["report_date"]
                text = await gateway.call_tool(name, arguments, report_date)
        except GatewayError as e:
            raise RuntimeError(str(e)) from e
        return [types.TextContent(type="text", text=text)]

    return server


async def _run() -> None:
    gateway = GatewayClient.from_env()
    server = build_server(gateway)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await gateway.aclose()


async def _check() -> int:
    gateway = GatewayClient.from_env()
    try:
        me = await gateway.whoami()
    except GatewayError as e:
        print(f"LỖI: {e}")
        return 1
    finally:
        await gateway.aclose()
    print(f"OK — carbon-analyst-mcp {__version__}: {me['email']} (token \"{me['token_name']}\"), {len(me['tools'])} tool.")
    return 0


def main() -> None:
    # stdout là kênh giao thức MCP — chỉ --check mới được print ra đó.
    if "--check" in sys.argv[1:]:
        sys.exit(asyncio.run(_check()))
    try:
        asyncio.run(_run())
    except GatewayError as e:
        print(f"carbon-analyst-mcp: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
