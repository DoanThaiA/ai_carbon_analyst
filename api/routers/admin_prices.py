"""Dashboard giá (trang /admin/price-dashboard): lịch sử giá đóng cửa của các instrument
đã crawl (bảng prices) + giá mới nhất kèm Δ ngày/Δ tuần, để admin nhìn nhanh xu hướng và
phát hiện mã bị thiếu/cũ dữ liệu sau đợt crawl giá."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import Instrument, Price

router = APIRouter(
    prefix="/api/admin/prices",
    tags=["admin-prices"],
    dependencies=[Depends(get_current_admin)],
)

TZ_VN = timezone(timedelta(hours=7))


@router.get("/dashboard")
async def price_dashboard(
    days: int = Query(90, ge=7, le=365),
    session: AsyncSession = Depends(get_db),
):
    """Mỗi instrument: thông tin + `series` (price_date, close, volume — tăng dần theo ngày, trong
    `days` ngày lịch gần nhất) + `latest` (dòng mới nhất, kể cả cũ hơn cửa sổ). `latest_date` toàn
    cục = ngày giá mới nhất của bất kỳ mã nào — FE đánh dấu mã có ngày cũ hơn là "chưa cập nhật"."""
    since = (datetime.now(TZ_VN).date() - timedelta(days=days - 1)).isoformat()

    instruments = (await session.execute(select(Instrument).order_by(Instrument.id))).scalars().all()
    rows = (
        await session.execute(
            select(Price).where(Price.price_date >= since).order_by(Price.instrument_id, Price.price_date)
        )
    ).scalars().all()
    by_inst: dict[int, list[Price]] = {}
    for p in rows:
        by_inst.setdefault(p.instrument_id, []).append(p)

    items = []
    latest_date = None
    for inst in instruments:
        series = by_inst.get(inst.id, [])
        last = series[-1] if series else (
            await session.execute(
                select(Price).where(Price.instrument_id == inst.id).order_by(Price.price_date.desc()).limit(1)
            )
        ).scalars().first()
        if last is None:
            continue
        # CBAM chốt theo quý, ngày không theo phiên — không dùng làm mốc "ngày mới nhất".
        if inst.code != "CBAM" and (latest_date is None or last.price_date > latest_date):
            latest_date = last.price_date
        items.append({
            "code": inst.code,
            "name": inst.name,
            "category": inst.category,
            "unit": inst.unit,
            "exchange": inst.exchange,
            "latest": {
                "date": last.price_date,
                "close": last.close_price,
                "day_change_pct": last.day_change_pct,
                "week_change_pct": last.week_change_pct,
                "volume": last.volume,
            },
            "series": [{"date": p.price_date, "close": p.close_price, "volume": p.volume} for p in series],
        })

    return {"days": days, "latest_date": latest_date, "instruments": items}
