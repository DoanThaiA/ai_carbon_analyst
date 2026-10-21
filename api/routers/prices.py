"""Lịch sử giá cho người dùng đã đăng nhập (admin + user): biểu đồ chỉ số giá cạnh Bảng giá nhanh
và popup "So sánh hiệu suất" trong báo cáo (frontend/src/components/PriceIndexPanel.tsx)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from services.price_history import build_price_dashboard

router = APIRouter(
    prefix="/api/prices",
    tags=["prices"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/history")
async def price_history(
    days: int = Query(90, ge=7, le=365),
    before: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    session: AsyncSession = Depends(get_db),
):
    """Như /api/admin/prices/dashboard; `before` = ngày báo cáo → chỉ phiên trước ngày đó."""
    return await build_price_dashboard(session, days, before)
