"""HTTP client mỏng gọi /api/mcp-gateway/* của backend Carbon Analyst."""
import os
from typing import Any, Dict, List, Optional

import httpx

# search_news có Cohere embed + rerank nên có thể chậm hơn các tool chỉ query DB.
REQUEST_TIMEOUT_SECONDS = 60.0


class GatewayError(RuntimeError):
    """Lỗi đã có thông điệp tiếng Việt, hiển thị thẳng cho người dùng/Claude."""


class GatewayClient:
    def __init__(self, base_url: str, token: str) -> None:
        if not base_url or not token:
            raise GatewayError(
                "Thiếu CARBON_API_URL hoặc CARBON_API_TOKEN trong phần env của config MCP "
                "(lấy token tại trang \"Kết nối Claude Desktop\" trên web)."
            )
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/mcp-gateway",
            headers={"Authorization": f"Bearer {token}"},
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
            raise GatewayError(str(detail or f"Lỗi máy chủ (HTTP {resp.status_code})."))
        return resp.json()

    async def whoami(self) -> Dict[str, Any]:
        return await self._request("GET", "/me")

    async def list_tools(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/tools")

    async def call_tool(self, name: str, tool_input: Dict[str, Any], report_date: Optional[str]) -> str:
        body = {"report_date": report_date, "input": tool_input}
        return (await self._request("POST", f"/tools/{name}", json=body))["result"]

    async def get_handoff(self, handoff_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/handoffs/{handoff_id}")

    async def aclose(self) -> None:
        await self._http.aclose()
