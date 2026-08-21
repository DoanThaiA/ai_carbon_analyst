from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import PriceCrawlSource

router = APIRouter(
    prefix="/api/admin/price-sources",
    tags=["admin-price-sources"],
    dependencies=[Depends(get_current_admin)],
)


class PriceSourceIn(BaseModel):
    symbol: str
    instrument_code: str
    instrument_name: str
    category: str
    unit: str
    exchange: str
    is_active: bool = True


class PriceSourceUpdate(BaseModel):
    symbol: str
    instrument_name: str
    category: str
    unit: str
    exchange: str
    is_active: bool


def _serialize(row: PriceCrawlSource) -> dict:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "instrument_code": row.instrument_code,
        "instrument_name": row.instrument_name,
        "category": row.category,
        "unit": row.unit,
        "exchange": row.exchange,
        "is_active": row.is_active,
        "updated_at": row.updated_at,
    }


@router.get("")
async def list_price_sources(session: AsyncSession = Depends(get_db)):
    rows = (
        await session.execute(select(PriceCrawlSource).order_by(PriceCrawlSource.instrument_code))
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("")
async def create_price_source(body: PriceSourceIn, session: AsyncSession = Depends(get_db)):
    row = PriceCrawlSource(**body.model_dump())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="instrument_code đã tồn tại.")
    await session.refresh(row)
    return _serialize(row)


@router.put("/{source_id}")
async def update_price_source(
    source_id: int, body: PriceSourceUpdate, session: AsyncSession = Depends(get_db)
):
    row = await session.get(PriceCrawlSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn giá.")
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return _serialize(row)


@router.delete("/{source_id}")
async def delete_price_source(source_id: int, session: AsyncSession = Depends(get_db)):
    row = await session.get(PriceCrawlSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn giá.")
    await session.delete(row)
    await session.commit()
    return {"ok": True}
