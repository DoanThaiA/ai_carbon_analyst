from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from api.deps import get_current_user, get_db, settings
from api.routers import admin_price_sources, admin_reports, admin_users, auth_admin, auth_user, hot_news, quote_chat
from db.models import Report
from services.hot_news_broadcast import start_listening, stop_listening


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Giữ 1 kết nối asyncpg LISTEN xuyên suốt vòng đời app — nhận Postgres NOTIFY
    # do crawl pipeline bắn ra khi lưu bài hot news, fan-out cho các client SSE
    # đang mở (xem services/hot_news_broadcast.py).
    await start_listening(settings.database_url)
    yield
    await stop_listening()


app = FastAPI(title="Carbon Analyst API", lifespan=lifespan)

# Allow CORS for Next.js frontend (default port 3000). allow_credentials=True
# là bắt buộc để trình duyệt gửi/nhận cookie session (access_token) cross-port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_admin.router)
app.include_router(auth_user.router)
app.include_router(admin_price_sources.router)
app.include_router(admin_users.router)
app.include_router(admin_reports.router)
app.include_router(quote_chat.router)
app.include_router(hot_news.router)


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


@app.get("/api/reports/{date}")
async def get_report_by_date(
    date: str,
    session: AsyncSession = Depends(get_db),
    _payload: dict = Depends(get_current_user),
):
    """Lấy chi tiết báo cáo theo ngày (YYYY-MM-DD) — chỉ trả về nếu đã published,
    để tránh lộ nội dung draft chưa qua admin duyệt cho user."""
    stmt = select(Report).where(Report.report_date == date, Report.status == "published")
    result = await session.execute(stmt)
    report = result.scalars().first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

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
