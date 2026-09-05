"""Quản lý đánh giá chat cho admin — xem lại toàn bộ phiên Quote Chat của mọi
user (đánh giá tốt/không tốt kèm lý do, và nội dung hội thoại đầy đủ), phục vụ
việc rà soát chất lượng câu trả lời của chatbot.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from schemas.chat_models import (
    AdminChatMessage,
    AdminChatSessionDetail,
    AdminChatSessionListResponse,
    AdminChatSessionSummary,
)
from services.chat_history import get_session_admin, list_session_messages, list_sessions_admin
from services.quote_chat_examples import get_example_map_for_session

router = APIRouter(
    prefix="/api/admin/chat-sessions",
    tags=["admin-chat-reviews"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=AdminChatSessionListResponse)
async def list_chat_sessions(
    rating: Optional[Literal["good", "bad", "none"]] = Query(
        None, description="Lọc theo đánh giá: good/bad/none (chưa đánh giá). Bỏ trống = tất cả."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    rows, total = await list_sessions_admin(session, rating=rating, limit=limit, offset=offset)
    items = [
        AdminChatSessionSummary(
            id=s.id,
            user_email=s.user_email,
            report_date=s.report_date,
            quote=s.quote,
            rating=s.rating,
            rating_reason=s.rating_reason,
            message_count=message_count,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s, message_count in rows
    ]
    return AdminChatSessionListResponse(items=items, total=total)


@router.get("/{session_id}", response_model=AdminChatSessionDetail)
async def get_chat_session_detail(session_id: int, session: AsyncSession = Depends(get_db)):
    chat_session = await get_session_admin(session, session_id=session_id)
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = await list_session_messages(session, session_id=session_id)
    example_map = await get_example_map_for_session(session, session_id=session_id)
    return AdminChatSessionDetail(
        id=chat_session.id,
        user_email=chat_session.user_email,
        report_date=chat_session.report_date,
        quote=chat_session.quote,
        rating=chat_session.rating,
        rating_reason=chat_session.rating_reason,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
        messages=[
            AdminChatMessage(id=m.id, role=m.role, content=m.content, example_id=example_map.get(m.id))
            for m in messages
        ],
    )
