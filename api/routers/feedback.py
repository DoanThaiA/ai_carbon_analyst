"""Phản ánh thái độ AI assistant (Jenny) — endpoint công khai, KHÔNG yêu cầu
đăng nhập (gửi thẳng từ landing page `/`), khác với phần lớn API còn lại của
hệ thống. Xem api/routers/admin_feedback.py cho phần admin xem danh sách."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from db.models import AssistantFeedback
from schemas.feedback_models import FeedbackCreateRequest, FeedbackResponse

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse)
async def create_feedback(
    body: FeedbackCreateRequest,
    session: AsyncSession = Depends(get_db),
):
    row = AssistantFeedback(
        reporter_name=(body.reporter_name or "").strip() or None,
        reporter_role=body.reporter_role,
        content=body.content.strip(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return FeedbackResponse(
        id=row.id,
        reporter_name=row.reporter_name,
        reporter_role=row.reporter_role,
        content=row.content,
        created_at=row.created_at,
    )
