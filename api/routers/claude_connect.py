"""Kết nối Claude Desktop (MCP) — phía web (xác thực bằng cookie JWT):

- /api/claude/tokens: user tự tạo/xem/thu hồi API token để dán vào config MCP
  server cục bộ trên máy mình (MCP server gọi /api/mcp-gateway/* bằng token này).
- POST /api/reports/{date}/quote-chat/handoff: "Hỏi Claude" — gói quote + câu hỏi
  thành handoff, trả về câu prompt ngắn để user dán vào Claude Desktop.

Chỉ dành cho role 'user' (đăng nhập email+OTP): token/handoff gắn với email, còn
admin đăng nhập bằng username nên không có bản ghi `users` để xác thực ở gateway.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.routers.quote_chat import _ensure_report_accessible
from schemas.claude_models import (
    ApiTokenCreated,
    ApiTokenCreateRequest,
    ApiTokenSummary,
    HandoffCreated,
    HandoffCreateRequest,
)
from services.claude_connect import (
    MAX_ACTIVE_TOKENS_PER_USER,
    MAX_HANDOFFS_PER_DAY,
    build_handoff_prompt,
    count_active_tokens,
    count_recent_handoffs,
    create_api_token,
    create_handoff,
    list_api_tokens,
    revoke_api_token,
)

router = APIRouter(tags=["claude-connect"])


def _require_user_role(payload: dict) -> str:
    if payload.get("role") != "user":
        raise HTTPException(
            status_code=403,
            detail="Kết nối Claude Desktop chỉ dành cho tài khoản người dùng (đăng nhập bằng email).",
        )
    return payload["sub"]


def _summary(t) -> ApiTokenSummary:
    return ApiTokenSummary(
        id=t.id,
        name=t.name,
        token_prefix=t.token_prefix,
        created_at=t.created_at,
        expires_at=t.expires_at,
        last_used_at=t.last_used_at,
        revoked_at=t.revoked_at,
    )


@router.get("/api/claude/tokens", response_model=list[ApiTokenSummary])
async def get_tokens(
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    email = _require_user_role(payload)
    return [_summary(t) for t in await list_api_tokens(session, user_email=email)]


@router.post("/api/claude/tokens", response_model=ApiTokenCreated, status_code=201)
async def create_token(
    body: ApiTokenCreateRequest,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    email = _require_user_role(payload)
    if await count_active_tokens(session, user_email=email) >= MAX_ACTIVE_TOKENS_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"Tối đa {MAX_ACTIVE_TOKENS_PER_USER} token đang hoạt động — thu hồi bớt token cũ trước.",
        )
    token, raw = await create_api_token(session, user_email=email, name=body.name)
    return ApiTokenCreated(**_summary(token).model_dump(), token=raw)


@router.delete("/api/claude/tokens/{token_id}", status_code=204)
async def delete_token(
    token_id: int,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    email = _require_user_role(payload)
    if not await revoke_api_token(session, token_id=token_id, user_email=email):
        raise HTTPException(status_code=404, detail="Token not found")


@router.post("/api/reports/{date}/quote-chat/handoff", response_model=HandoffCreated)
async def create_claude_handoff(
    date: str,
    body: HandoffCreateRequest,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    email = _require_user_role(payload)
    await _ensure_report_accessible(date, payload, session)
    if await count_recent_handoffs(session, user_email=email) >= MAX_HANDOFFS_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"Bạn đã tạo {MAX_HANDOFFS_PER_DAY} yêu cầu Claude trong 24 giờ qua — vui lòng thử lại sau.",
        )
    handoff = await create_handoff(
        session,
        user_email=email,
        report_date=date,
        quote=body.quote.strip(),
        question=body.question.strip(),
        task_type=body.task_type,
    )
    return HandoffCreated(
        handoff_id=handoff.handoff_id,
        prompt=build_handoff_prompt(handoff.handoff_id),
        expires_at=handoff.expires_at,
    )
