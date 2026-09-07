"""Xem danh sách phản ánh thái độ AI assistant (Jenny) — chỉ admin ("Sếp của
Jenny") mới xem được."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import AssistantFeedback
from schemas.feedback_models import FeedbackListResponse, FeedbackResponse

router = APIRouter(
    prefix="/api/admin/feedbacks",
    tags=["admin-feedback"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=FeedbackListResponse)
async def list_feedbacks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    total = (await session.execute(select(func.count(AssistantFeedback.id)))).scalar_one()
    stmt = (
        select(AssistantFeedback)
        .order_by(AssistantFeedback.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    items = [
        FeedbackResponse(
            id=r.id,
            user_email=r.user_email,
            reporter_name=r.reporter_name,
            reporter_role=r.reporter_role,
            content=r.content,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return FeedbackListResponse(items=items, total=total)
