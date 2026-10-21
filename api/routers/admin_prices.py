"""Dashboard giá (trang /admin/price-dashboard): lịch sử giá đóng cửa của các instrument
đã crawl (bảng prices) + giá mới nhất kèm Δ ngày/Δ tuần, để admin nhìn nhanh xu hướng và
phát hiện mã bị thiếu/cũ dữ liệu sau đợt crawl giá. Logic ở services/price_history.py."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from services.price_history import build_price_dashboard

router = APIRouter(
    prefix="/api/admin/prices",
    tags=["admin-prices"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("/dashboard")
async def price_dashboard(
    days: int = Query(90, ge=7, le=365),
    session: AsyncSession = Depends(get_db),
):
    return await build_price_dashboard(session, days)
