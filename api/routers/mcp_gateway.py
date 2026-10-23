"""Gateway chỉ-đọc cho MCP server cục bộ của Claude Desktop (xác thực bằng API
token, xem api/deps.py::get_api_token và services/claude_connect.py).

Tái dùng NGUYÊN bộ tool của Quote Chat (`CLIENT_TOOLS` + `_execute_client_tool`
trong services/quote_chat.py) — danh sách tool lấy động qua GET /tools nên thêm/
sửa tool ở server không cần cập nhật MCP client trên từng máy. Mọi tool đều chỉ
đọc DB, và chỉ phục vụ báo cáo đã published (cùng quyền với role 'user' trên web).

Ngoại lệ: token `scope='qc_agent'` (admin tạo cho lịch Claude QC 07:30) thấy thêm tool `get_qc_assignment` /
`submit_qc_review` + prompt `jack_qc_review`, được đọc cả báo cáo NHÁP (tool neo ngày + jack_qc) và là đường
GHI duy nhất của gateway — chỉ ghi biên bản QC (services/report_qc_review.py), không sửa báo cáo.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_api_token, get_db, settings
from api.routers.quote_chat import _get_embedder
from db.models import ApiToken, Report
from schemas.claude_models import (
    GatewayInstructions,
    GatewayMe,
    GatewayPromptDef,
    GatewayPromptRequest,
    GatewayPromptResult,
    GatewayToolCall,
    GatewayToolDef,
    GatewayToolResult,
    HandoffPayload,
)
from services.claude_connect import (
    DESKTOP_PROMPTS,
    DESKTOP_SERVER_INSTRUCTIONS,
    JACK_QC_DESKTOP_TOOL,
    RateLimiter,
    adapt_tools_for_desktop,
    build_desktop_prompt,
    build_handoff_instructions,
    consume_handoff,
)
from services.quote_chat import CLIENT_TOOLS, _execute_client_tool
from services.report_qc_review import (
    QC_AGENT_TOOL_NAMES,
    QC_AGENT_TOOLS,
    QC_PROMPT_DEF,
    QC_ROUTINE_PROMPT,
    QcSubmissionError,
    qc_assignment_text,
    save_review,
    submit_result_text,
)
from services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-gateway", tags=["mcp-gateway"])

_TOOLS_BY_NAME = {t["name"]: t for t in [*CLIENT_TOOLS, JACK_QC_DESKTOP_TOOL]}
# Mô tả đã chỉnh cho ngữ cảnh Claude Desktop — tính 1 lần (CLIENT_TOOLS là hằng số). Client >= 0.1.1
# gửi header X-Carbon-MCP-Client và nhận ngữ cảnh chung qua GET /instructions, nên mô tả tool không cần
# nối lặp ghi chú ngữ cảnh; client 0.1.0 (không gửi header) vẫn nhận bản có ghi chú như cũ.
_DESKTOP_TOOLS = [*adapt_tools_for_desktop(CLIENT_TOOLS, append_context_note=False), JACK_QC_DESKTOP_TOOL]
_DESKTOP_TOOLS_LEGACY = [*adapt_tools_for_desktop(CLIENT_TOOLS, append_context_note=True), JACK_QC_DESKTOP_TOOL]



def is_qc_agent(token: ApiToken) -> bool:
    return getattr(token, "scope", "user") == "qc_agent"


def tools_for_token(token: ApiToken, *, legacy: bool = False) -> list:
    """Bộ tool theo token: qc_agent thấy thêm tool QC (user thường không biết tool này tồn tại)."""
    base = _DESKTOP_TOOLS_LEGACY if legacy else _DESKTOP_TOOLS
    return [*base, *QC_AGENT_TOOLS] if is_qc_agent(token) else base


def prompts_for_token(token: ApiToken) -> list:
    return [*DESKTOP_PROMPTS, QC_PROMPT_DEF] if is_qc_agent(token) else DESKTOP_PROMPTS


def prompt_text_for_token(token: ApiToken, name: str, arguments: Optional[dict]) -> Optional[str]:
    if name == QC_PROMPT_DEF["name"]:
        return QC_ROUTINE_PROMPT if is_qc_agent(token) else None
    return build_desktop_prompt(name, arguments)


# 60 tool call/phút/token — chặn vòng lặp tool lỡ tay (search_news còn tốn Cohere).
_rate_limiter = RateLimiter(max_calls=60, window_seconds=60)


# Tool không neo theo ngày báo cáo (tìm/đọc tin, lịch, danh sách báo cáo, đọc URL, đấu giá EEX) — vẫn chạy
# được khi chưa có báo cáo published nào hoặc report_date trỏ tới ngày chưa published.
_REPORT_INDEPENDENT_TOOLS = frozenset(
    {"search_news", "get_article", "get_calendar_events", "list_reports", "fetch_user_source", "get_eua_auctions"}
)
# jack_qc neo theo ngày báo cáo (resolve về báo cáo published) nên không nằm trong tập trên.
_VN_TZ = timezone(timedelta(hours=7))


def _validate_report_date(report_date: Optional[str]) -> Optional[str]:
    # Tự validate thay vì `pattern` của Pydantic: lỗi 422 mặc định có `detail` dạng list
    # mà MCP client bản cũ in thẳng ra cho Claude.
    if report_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
        raise HTTPException(
            status_code=422,
            detail=f"report_date=\"{report_date}\" không hợp lệ — phải có dạng YYYY-MM-DD (hoặc bỏ trống).",
        )
    return report_date or None


async def _resolve_report_date(
    session: AsyncSession, report_date: Optional[str], *, include_drafts: bool = False
) -> str:
    """Ngày báo cáo ngữ cảnh cho tool: không truyền = báo cáo published mới nhất (qc_agent: mới nhất kể cả nháp)."""
    statuses = ("published", "draft") if include_drafts else ("published",)
    stmt = select(Report.report_date).where(Report.status.in_(statuses))
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
    return GatewayMe(email=token.user_email, token_name=token.name, tools=[t["name"] for t in tools_for_token(token)])


@router.get("/instructions", response_model=GatewayInstructions)
async def get_instructions(_token: ApiToken = Depends(get_api_token)):
    """Ngữ cảnh chung — MCP client đưa vào `instructions` của server lúc khởi động."""
    return GatewayInstructions(instructions=DESKTOP_SERVER_INSTRUCTIONS)


@router.get("/prompts", response_model=list[GatewayPromptDef])
async def list_prompts(token: ApiToken = Depends(get_api_token)):
    """MCP prompt (người dùng tự chọn trong Claude Desktop, vd "Trò chuyện với Jenny")."""
    return [GatewayPromptDef(**p) for p in prompts_for_token(token)]


@router.post("/prompts/{name}", response_model=GatewayPromptResult)
async def get_prompt(name: str, body: GatewayPromptRequest, token: ApiToken = Depends(get_api_token)):
    text = prompt_text_for_token(token, name, body.arguments)
    if text is None:
        raise HTTPException(status_code=404, detail=f"Prompt không tồn tại: {name}")
    description = next(p["title"] for p in prompts_for_token(token) if p["name"] == name)
    return GatewayPromptResult(description=description, text=text)


@router.get("/tools", response_model=list[GatewayToolDef])
async def list_tools(
    token: ApiToken = Depends(get_api_token),
    client_version: Optional[str] = Header(default=None, alias="X-Carbon-MCP-Client"),
):
    tools = tools_for_token(token, legacy=not client_version)
    return [
        GatewayToolDef(name=t["name"], description=t["description"], input_schema=t["input_schema"])
        for t in tools
    ]


async def execute_gateway_tool(
    session: AsyncSession, token: ApiToken, name: str, tool_input: dict, report_date_arg: Optional[str]
) -> GatewayToolResult:
    """Chạy 1 tool cho token — dùng chung cho gateway REST (client stdio) và MCP HTTP (api/mcp_http.py)."""
    qc_agent = is_qc_agent(token)
    is_qc_tool = name in QC_AGENT_TOOL_NAMES
    if (is_qc_tool and not qc_agent) or (not is_qc_tool and name not in _TOOLS_BY_NAME):
        # Token thường gọi tool QC: coi như tool không tồn tại.
        raise HTTPException(status_code=404, detail=f"Tool không tồn tại: {name}")
    if not _rate_limiter.allow(token.id):
        raise HTTPException(status_code=429, detail="Gọi tool quá nhanh — thử lại sau ít giây.")

    if is_qc_tool:
        requested_date = _validate_report_date(report_date_arg or tool_input.get("report_date"))
        return await _execute_qc_tool(session, token, name, tool_input, requested_date)
    requested_date = _validate_report_date(report_date_arg)
    if name in _REPORT_INDEPENDENT_TOOLS:
        # Các tool này không đọc report_date (chỉ dùng trong ghi chú lỗi) — không chặn vì báo cáo.
        report_date = requested_date or datetime.now(_VN_TZ).date().isoformat()
    else:
        report_date = await _resolve_report_date(session, requested_date, include_drafts=qc_agent)
    # search_news cần Cohere embed + rerank; các tool khác chỉ query DB nên không dựng.
    retrieval_service = (
        RetrievalService(embedder=_get_embedder(), session=session) if name == "search_news" else None
    )
    logger.info("[MCP-GATEWAY] user=%s tool=%s report_date=%s", token.user_email, name, report_date)
    result = await _execute_client_tool(
        name,
        tool_input,
        session,
        report_date,
        tool_cache={},
        chart_cache={},
        retrieval_service=retrieval_service,
        allow_jack_qc=True,
        # qc_agent: jack_qc chạy được trên bản nháp (giống admin trong Jenny chat).
        allow_admin_tools=qc_agent and name == "jack_qc",
    )
    return GatewayToolResult(
        result=result, report_date=None if name in _REPORT_INDEPENDENT_TOOLS else report_date
    )


async def _execute_qc_tool(
    session: AsyncSession, token: ApiToken, name: str, tool_input: dict, report_date: Optional[str]
) -> GatewayToolResult:
    if name == "get_qc_assignment":
        part = str(tool_input.get("part") or "").strip() or None
        logger.info("[MCP-GATEWAY] qc_agent=%s get_qc_assignment date=%s part=%s", token.name, report_date, part)
        return GatewayToolResult(result=await qc_assignment_text(session, report_date, part), report_date=None)
    payload = {**tool_input, "report_date": report_date}
    try:
        review = await save_review(session, payload, token=token)
    except QcSubmissionError as e:
        await session.rollback()
        # Trả như kết quả (không phải lỗi HTTP) để Claude đọc được danh sách cần sửa và gửi lại.
        return GatewayToolResult(result=str(e), report_date=None)
    return GatewayToolResult(result=submit_result_text(review, settings.auto_publish_time or None), report_date=None)


@router.post("/tools/{name}", response_model=GatewayToolResult)
async def call_tool(
    name: str,
    body: GatewayToolCall,
    session: AsyncSession = Depends(get_db),
    token: ApiToken = Depends(get_api_token),
):
    return await execute_gateway_tool(session, token, name, body.input, body.report_date)


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
