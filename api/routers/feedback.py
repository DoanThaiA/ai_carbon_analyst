"""Phản ánh thái độ AI assistant (Jenny) — chỉ gửi được khi đã đăng nhập
(gửi từ màn hình đọc báo cáo, xem QuoteChat.tsx). user_email/reporter_role lấy
từ JWT payload, không nhận từ client, để phản ánh luôn gắn đúng danh tính
người gửi. Xem api/routers/admin_feedback.py cho phần admin xem danh sách."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from db.models import AssistantFeedback
from schemas.feedback_models import FeedbackCreateRequest, FeedbackResponse

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse)
async def create_feedback(
    body: FeedbackCreateRequest,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    row = AssistantFeedback(
        user_email=payload["sub"],
        reporter_name=(body.reporter_name or "").strip() or None,
        reporter_role=payload["role"],
        content=body.content.strip(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return FeedbackResponse(
        id=row.id,
        user_email=row.user_email,
        reporter_name=row.reporter_name,
        reporter_role=row.reporter_role,
        content=row.content,
        created_at=row.created_at,
    )
