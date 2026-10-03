from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.logging import setup_logging

# PHẢI gọi TRƯỚC MỌI import khác có thể log — nếu không, root logger không có handler nào, Python
# chỉ tự in ra stderr đúng level WARNING trở lên (cơ chế "last resort" mặc định của thư viện
# logging), khiến MỌI `logger.info(...)` trong toàn bộ app (vd log tổng kết tool/token mỗi câu hỏi
# ở services/quote_chat.py) biến mất hoàn toàn dù code đúng — chỉ log ERROR/WARNING mới lọt ra
# (đây là lý do trước giờ chỉ thấy log lỗi 400, không bao giờ thấy log INFO nào). `main.py`/
# `scheduler.py` (2 entrypoint khác của repo) đã tự gọi `logging.basicConfig()` riêng — entrypoint
# API (chạy qua `uvicorn api.main:app`, xem Dockerfile.backend) trước giờ bị bỏ sót.
setup_logging()

from api.deps import get_current_user, get_db, settings
from api.routers import (
    admin_chat_reviews,
    admin_eua_framework,
    admin_feedback,
    admin_news_sources,
    admin_price_sources,
    admin_quote_chat_examples,
    admin_release_notes,
    admin_reports,
    admin_stats,
    admin_users,
    auth_admin,
    auth_user,
    claude_connect,
    feedback,
    hot_news,
    mcp_gateway,
    quote_chat,
    upload,
)
import logging
from datetime import datetime, timedelta, timezone

from db.models import Report, ReportView
from services.hot_news_broadcast import start_listening, stop_listening

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Giữ 1 kết nối asyncpg LISTEN xuyên suốt vòng đời app — nhận Postgres NOTIFY
    # do crawl pipeline bắn ra khi lưu bài hot news, fan-out cho các client SSE
    # đang mở (xem services/hot_news_broadcast.py).
    await start_listening(settings.database_url)
    yield
    await stop_listening()


app = FastAPI(title="Carbon Analyst API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://sim.mcv.network",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_admin.router)
app.include_router(auth_user.router)
app.include_router(admin_price_sources.router)
app.include_router(admin_news_sources.router)
app.include_router(admin_users.router)
app.include_router(admin_reports.router)
app.include_router(admin_stats.router)
app.include_router(admin_chat_reviews.router)
app.include_router(admin_eua_framework.router)
app.include_router(admin_quote_chat_examples.router)
app.include_router(quote_chat.router)
app.include_router(claude_connect.router)
app.include_router(mcp_gateway.router)
app.include_router(upload.router)
app.include_router(hot_news.router)
app.include_router(feedback.router)
app.include_router(admin_feedback.router)
app.include_router(admin_release_notes.router)


@app.get("/api/reports")
async def get_reports(
    session: AsyncSession = Depends(get_db),
    _payload: dict = Depends(get_current_user),
):
    """Lấy danh sách báo cáo ĐÃ DUYỆT — dùng cho màn hình daily report của user."""
    stmt = (
        select(Report)
        .where(Report.status == "published")
        .order_by(Report.report_date.desc())
    )
    result = await session.execute(stmt)
    reports = result.scalars().all()

    return [
        {
            "id": r.id,
            "report_date": r.report_date,
            "status": r.status,
            "created_at": r.created_at,
            "published_at": r.published_at,
        }
        for r in reports
    ]


# Cùng 1 người mở lại cùng báo cáo trong khoảng này chỉ tính 1 lượt xem.
REPORT_VIEW_DEDUPE_MINUTES = 30


async def _record_report_view(session: AsyncSession, report_date: str, payload: dict) -> None:
    """Ghi 1 lượt xem cho trang Thống kê hiệu suất (api/routers/admin_stats.py).
    Lỗi ghi thống kê KHÔNG được làm hỏng việc trả báo cáo cho người đọc."""
    viewer = payload.get("sub")
    if not viewer:
        return
    try:
        since = datetime.now(timezone.utc) - timedelta(minutes=REPORT_VIEW_DEDUPE_MINUTES)
        recent = await session.scalar(
            select(ReportView.id).where(
                ReportView.viewer == viewer,
                ReportView.report_date == report_date,
                ReportView.viewed_at >= since,
            ).limit(1)
        )
        if recent is None:
            session.add(ReportView(report_date=report_date, viewer=viewer, role=payload.get("role") or "user"))
            await session.commit()
    except Exception:
        logger.exception("Lỗi ghi lượt xem báo cáo %s — bỏ qua.", report_date)
        await session.rollback()


@app.get("/api/reports/{date}")
async def get_report_by_date(
    date: str,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    """Lấy chi tiết báo cáo theo ngày (YYYY-MM-DD) — chỉ trả về nếu đã published,
    để tránh lộ nội dung draft chưa qua admin duyệt cho user."""
    stmt = select(Report).where(Report.report_date == date, Report.status == "published")
    result = await session.execute(stmt)
    report = result.scalars().first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await _record_report_view(session, date, payload)

    return {
        "id": report.id,
        "report_date": report.report_date,
        "status": report.status,
        "content": report.content,
        "created_at": report.created_at,
        "published_at": report.published_at,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
