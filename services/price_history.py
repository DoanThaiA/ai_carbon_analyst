"""Lịch sử giá đóng cửa của các instrument (bảng prices) + giá mới nhất kèm Δ ngày/Δ tuần — dùng chung cho
Dashboard giá của admin (api/routers/admin_prices.py) và biểu đồ chỉ số giá / "So sánh hiệu suất" cạnh
Bảng giá nhanh trong báo cáo (api/routers/prices.py, mọi người dùng đã đăng nhập)."""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Instrument, Price

TZ_VN = timezone(timedelta(hours=7))


async def build_price_dashboard(session: AsyncSession, days: int, before: Optional[str] = None) -> Dict[str, Any]:
    """Mỗi instrument: thông tin + `series` (price_date, close, volume — tăng dần theo ngày, trong
    `days` ngày lịch gần nhất) + `latest` (dòng mới nhất, kể cả cũ hơn cửa sổ). `latest_date` toàn
    cục = ngày giá mới nhất của bất kỳ mã nào — FE đánh dấu mã có ngày cũ hơn là "chưa cập nhật".

    `before` (YYYY-MM-DD): chỉ lấy phiên TRƯỚC ngày này — báo cáo ngày D in giá phiên trước D, nên
    xem lại báo cáo cũ không lẫn giá các phiên sau ngày phát hành."""
    end = date.fromisoformat(before) - timedelta(days=1) if before else datetime.now(TZ_VN).date()
    since = (end - timedelta(days=days - 1)).isoformat()
    until = end.isoformat()

    instruments = (await session.execute(select(Instrument).order_by(Instrument.id))).scalars().all()
    rows = (
        await session.execute(
            select(Price)
            .where(Price.price_date >= since, Price.price_date <= until)
            .order_by(Price.instrument_id, Price.price_date)
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
                select(Price)
                .where(Price.instrument_id == inst.id, Price.price_date <= until)
                .order_by(Price.price_date.desc())
                .limit(1)
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
