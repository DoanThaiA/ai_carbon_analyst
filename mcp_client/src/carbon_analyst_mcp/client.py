"""HTTP client mỏng gọi /api/mcp-gateway/* của backend Carbon Analyst."""
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from carbon_analyst_mcp import __version__

# search_news có Cohere embed + rerank nên có thể chậm hơn các tool chỉ query DB.
REQUEST_TIMEOUT_SECONDS = 60.0
# Lấy instructions chạy TRƯỚC khi MCP server bắt tay với Claude Desktop — không được chờ lâu.
INSTRUCTIONS_TIMEOUT_SECONDS = 8.0


class GatewayError(RuntimeError):
    """Lỗi đã có thông điệp tiếng Việt, hiển thị thẳng cho người dùng/Claude."""


def _format_detail(detail: Any) -> str:
    """`detail` của FastAPI là chuỗi, hoặc list lỗi validate (422) — list thì gom thành 1 dòng dễ đọc."""
    if isinstance(detail, list):
        parts = []
        for err in detail:
            if isinstance(err, dict):
                loc = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
                parts.append(f"{loc}: {err.get('msg', '')}" if loc else str(err.get("msg", "")))
            else:
                parts.append(str(err))
        return "Tham số không hợp lệ — " + "; ".join(parts)
    return str(detail) if detail else ""


class GatewayClient:
    def __init__(self, base_url: str, token: str) -> None:
        if not base_url or not token:
            raise GatewayError(
                "Thiếu CARBON_API_URL hoặc CARBON_API_TOKEN trong phần env của config MCP "
                "(lấy token tại trang \"Kết nối Claude Desktop\" trên web)."
            )
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/mcp-gateway",
            # X-Carbon-MCP-Client: báo server rằng client nhận ngữ cảnh qua GET /instructions
            # (server bỏ ghi chú ngữ cảnh lặp lại trong mô tả từng tool).
            headers={"Authorization": f"Bearer {token}", "X-Carbon-MCP-Client": __version__},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    @classmethod
    def from_env(cls) -> "GatewayClient":
        return cls(os.environ.get("CARBON_API_URL", ""), os.environ.get("CARBON_API_TOKEN", ""))

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise GatewayError(f"Không kết nối được tới máy chủ Carbon Analyst: {e.__class__.__name__}") from e
        if resp.status_code == 401:
            raise GatewayError("API token không hợp lệ, đã hết hạn hoặc bị thu hồi — tạo token mới trên web.")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = None
            raise GatewayError(_format_detail(detail) or f"Lỗi máy chủ (HTTP {resp.status_code}).")
        return resp.json()

    async def whoami(self) -> Dict[str, Any]:
        return await self._request("GET", "/me")

    async def get_instructions(self) -> Optional[str]:
        """None nếu không lấy được (server cũ chưa có endpoint, mất mạng...) — caller dùng bản dự phòng."""
        try:
            data = await self._request("GET", "/instructions", timeout=INSTRUCTIONS_TIMEOUT_SECONDS)
        except GatewayError:
            return None
        return data.get("instructions") or None

    async def list_tools(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/tools")

    async def call_tool(
        self, name: str, tool_input: Dict[str, Any], report_date: Optional[str]
    ) -> Tuple[str, Optional[str]]:
        """Trả (kết quả, ngày báo cáo ngữ cảnh server đã dùng — None nếu tool không neo theo báo cáo
        hoặc server bản cũ chưa trả trường này)."""
        body = {"report_date": report_date, "input": tool_input}
        data = await self._request("POST", f"/tools/{name}", json=body)
        return data["result"], data.get("report_date")

    async def list_prompts(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/prompts")

    async def get_prompt(self, name: str, arguments: Dict[str, str]) -> Dict[str, Any]:
        return await self._request("POST", f"/prompts/{name}", json={"arguments": arguments})

    async def get_handoff(self, handoff_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/handoffs/{handoff_id}")

    async def aclose(self) -> None:
        await self._http.aclose()
