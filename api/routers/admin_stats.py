"""
Thống kê hiệu suất Jenny (trang /admin/performance): theo từng báo cáo ngày đã
phát hành — lượt xem (bảng report_views, ghi ở api/main.py::get_report_by_date,
chỉ đếm role 'user') và lượt hỏi đáp Quote Chat (chat_sessions + chat_messages).
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import ChatMessage, ChatSession, Report, ReportView

router = APIRouter(
    prefix="/api/admin/stats",
    tags=["admin-stats"],
    dependencies=[Depends(get_current_admin)],
)

TZ_VN = timezone(timedelta(hours=7))


@router.get("/performance")
async def performance_stats(
    days: int = Query(30, ge=7, le=365),
    session: AsyncSession = Depends(get_db),
):
    """Mỗi báo cáo đã phát hành trong `days` ngày gần nhất (theo report_date):
    views (lượt mở, đã gộp mở lại trong 30 phút), unique_viewers (số người xem),
    questions (số câu hỏi người dùng gửi Jenny), sessions (số phiên hỏi đáp)."""
    since = (datetime.now(TZ_VN).date() - timedelta(days=days - 1)).isoformat()

    report_dates = list((await session.execute(
        select(Report.report_date)
        .where(Report.status == "published", Report.report_date >= since)
        .order_by(Report.report_date)
    )).scalars().all())

    views = {
        row.report_date: row
        for row in (await session.execute(
            select(
                ReportView.report_date,
                func.count(ReportView.id).label("views"),
                func.count(distinct(ReportView.viewer)).label("unique_viewers"),
            )
            .where(ReportView.role == "user", ReportView.report_date >= since)
            .group_by(ReportView.report_date)
        )).all()
    }

    chats = {
        row.report_date: row
        for row in (await session.execute(
            select(
                ChatSession.report_date,
                func.count(ChatMessage.id).label("questions"),
                func.count(distinct(ChatSession.id)).label("sessions"),
            )
            .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(ChatMessage.role == "user", ChatSession.report_date >= since)
            .group_by(ChatSession.report_date)
        )).all()
    }

    items = []
    for d in report_dates:
        v, c = views.get(d), chats.get(d)
        items.append({
            "report_date": d,
            "views": v.views if v else 0,
            "unique_viewers": v.unique_viewers if v else 0,
            "questions": c.questions if c else 0,
            "sessions": c.sessions if c else 0,
        })

    # Người xem khác nhau trên TOÀN kỳ (không cộng dồn theo ngày — 1 người xem
    # nhiều báo cáo chỉ tính 1).
    total_unique_viewers = await session.scalar(
        select(func.count(distinct(ReportView.viewer)))
        .where(ReportView.role == "user", ReportView.report_date.in_(report_dates))
    ) if report_dates else 0

    return {
        "days": days,
        "totals": {
            "reports": len(items),
            "views": sum(i["views"] for i in items),
            "unique_viewers": total_unique_viewers or 0,
            "questions": sum(i["questions"] for i in items),
            "sessions": sum(i["sessions"] for i in items),
        },
        "items": items,
    }
