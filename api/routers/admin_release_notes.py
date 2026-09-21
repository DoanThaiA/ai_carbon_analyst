from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import ReleaseNote

router = APIRouter(
    prefix="/api/admin/release-notes",
    tags=["admin-release-notes"],
    dependencies=[Depends(get_current_admin)],
)


class ReleaseNoteIn(BaseModel):
    customer_request: str
    change_description: str
    test_result: str | None = None
    status: str = Field(default="chua_dat", pattern="^(dat|chua_dat)$")


class ReleaseNoteUpdate(ReleaseNoteIn):
    order_index: int | None = None


def _serialize(row: ReleaseNote) -> dict:
    return {
        "id": row.id,
        "order_index": row.order_index,
        "customer_request": row.customer_request,
        "change_description": row.change_description,
        "test_result": row.test_result,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("")
async def list_release_notes(session: AsyncSession = Depends(get_db)):
    rows = (
        await session.execute(
            select(ReleaseNote).order_by(ReleaseNote.order_index, ReleaseNote.id)
        )
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("")
async def create_release_note(body: ReleaseNoteIn, session: AsyncSession = Depends(get_db)):
    next_order = (
        await session.execute(select(func.coalesce(func.max(ReleaseNote.order_index), 0)))
    ).scalar_one()
    row = ReleaseNote(**body.model_dump(), order_index=next_order + 1)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _serialize(row)


@router.put("/{note_id}")
async def update_release_note(
    note_id: int, body: ReleaseNoteUpdate, session: AsyncSession = Depends(get_db)
):
    row = await session.get(ReleaseNote, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục release note.")
    data = body.model_dump()
    order_index = data.pop("order_index")
    for field, value in data.items():
        setattr(row, field, value)
    if order_index is not None:
        row.order_index = order_index
    await session.commit()
    await session.refresh(row)
    return _serialize(row)


@router.delete("/{note_id}")
async def delete_release_note(note_id: int, session: AsyncSession = Depends(get_db)):
    row = await session.get(ReleaseNote, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục release note.")
    await session.delete(row)
    await session.commit()
    return {"ok": True}
