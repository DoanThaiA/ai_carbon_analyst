from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import User

router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(get_current_admin)],
)


class UserCreate(BaseModel):
    email: EmailStr


class UserUpdate(BaseModel):
    is_active: bool


def _serialize(row: User) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "is_active": row.is_active,
        "created_at": row.created_at,
    }


@router.get("")
async def list_users(session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("")
async def create_user(body: UserCreate, session: AsyncSession = Depends(get_db)):
    row = User(email=body.email.strip().lower())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Email này đã có trong danh sách.")
    await session.refresh(row)
    return _serialize(row)


@router.put("/{user_id}")
async def update_user(user_id: int, body: UserUpdate, session: AsyncSession = Depends(get_db)):
    row = await session.get(User, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy user.")
    row.is_active = body.is_active
    await session.commit()
    await session.refresh(row)
    return _serialize(row)


@router.delete("/{user_id}")
async def delete_user(user_id: int, session: AsyncSession = Depends(get_db)):
    row = await session.get(User, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy user.")
    await session.delete(row)
    await session.commit()
    return {"ok": True}
