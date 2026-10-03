"""Gateway chỉ-đọc cho MCP server cục bộ của Claude Desktop (xác thực bằng API
token, xem api/deps.py::get_api_token và services/claude_connect.py).

Tái dùng NGUYÊN bộ tool của Quote Chat (`CLIENT_TOOLS` + `_execute_client_tool`
trong services/quote_chat.py) — danh sách tool lấy động qua GET /tools nên thêm/
sửa tool ở server không cần cập nhật MCP client trên từng máy. Mọi tool đều chỉ
đọc DB, và chỉ phục vụ báo cáo đã published (cùng quyền với role 'user' trên web).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_api_token, get_db
from api.routers.quote_chat import _get_embedder
from db.models import ApiToken, Report
from schemas.claude_models import (
    GatewayMe,
    GatewayToolCall,
    GatewayToolDef,
    GatewayToolResult,
    HandoffPayload,
)
from services.claude_connect import (
    RateLimiter,
    adapt_tools_for_desktop,
    build_handoff_instructions,
    consume_handoff,
)
from services.quote_chat import CLIENT_TOOLS, _execute_client_tool
from services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-gateway", tags=["mcp-gateway"])

_TOOLS_BY_NAME = {t["name"]: t for t in CLIENT_TOOLS}
# Mô tả đã chỉnh cho ngữ cảnh Claude Desktop — tính 1 lần (CLIENT_TOOLS là hằng số).
_DESKTOP_TOOLS = adapt_tools_for_desktop(CLIENT_TOOLS)

# 60 tool call/phút/token — chặn vòng lặp tool lỡ tay (search_news còn tốn Cohere).
_rate_limiter = RateLimiter(max_calls=60, window_seconds=60)


async def _resolve_report_date(session: AsyncSession, report_date: Optional[str]) -> str:
    """Ngày báo cáo ngữ cảnh cho tool: không truyền = báo cáo published mới nhất."""
    stmt = select(Report.report_date).where(Report.status == "published")
    if report_date:
        stmt = stmt.where(Report.report_date == report_date)
    else:
        stmt = stmt.order_by(Report.report_date.desc())
    found = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không có báo cáo đã phát hành cho ngày {report_date}." if report_date else "Chưa có báo cáo nào được phát hành.",
        )
    return found


@router.get("/me", response_model=GatewayMe)
async def whoami(token: ApiToken = Depends(get_api_token)):
    """Kiểm tra nhanh token hợp lệ (dùng cho `carbon-analyst-mcp --check`)."""
    return GatewayMe(email=token.user_email, token_name=token.name, tools=list(_TOOLS_BY_NAME))


@router.get("/tools", response_model=list[GatewayToolDef])
async def list_tools(_token: ApiToken = Depends(get_api_token)):
    return [
        GatewayToolDef(name=t["name"], description=t["description"], input_schema=t["input_schema"])
        for t in _DESKTOP_TOOLS
    ]


@router.post("/tools/{name}", response_model=GatewayToolResult)
async def call_tool(
    name: str,
    body: GatewayToolCall,
    session: AsyncSession = Depends(get_db),
    token: ApiToken = Depends(get_api_token),
):
    if name not in _TOOLS_BY_NAME:
        raise HTTPException(status_code=404, detail=f"Tool không tồn tại: {name}")
    if not _rate_limiter.allow(token.id):
        raise HTTPException(status_code=429, detail="Gọi tool quá nhanh — thử lại sau ít giây.")

    report_date = await _resolve_report_date(session, body.report_date)
    # search_news cần Cohere embed + rerank; các tool khác chỉ query DB nên không dựng.
    retrieval_service = (
        RetrievalService(embedder=_get_embedder(), session=session) if name == "search_news" else None
    )
    logger.info("[MCP-GATEWAY] user=%s tool=%s report_date=%s", token.user_email, name, report_date)
    result = await _execute_client_tool(
        name,
        body.input,
        session,
        report_date,
        tool_cache={},
        chart_cache={},
        retrieval_service=retrieval_service,
    )
    return GatewayToolResult(result=result)


@router.get("/handoffs/{handoff_id}", response_model=HandoffPayload)
async def get_handoff(
    handoff_id: str,
    session: AsyncSession = Depends(get_db),
    token: ApiToken = Depends(get_api_token),
):
    handoff = await consume_handoff(session, handoff_id=handoff_id, user_email=token.user_email)
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff không tồn tại, đã hết hạn hoặc không thuộc về bạn.")
    return HandoffPayload(
        handoff_id=handoff.handoff_id,
        report_date=handoff.report_date,
        task_type=handoff.task_type,
        quote=handoff.quote,
        question=handoff.question,
        instructions=build_handoff_instructions(handoff),
    )
