from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import User

router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(get_current_admin)],
)


RelationType = Literal["superior", "peer"]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    relation_type: RelationType = "peer"


class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    relation_type: Optional[RelationType] = None


def _serialize(row: User) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "full_name": row.full_name,
        "job_title": row.job_title,
        "relation_type": row.relation_type,
        "is_active": row.is_active,
        "created_at": row.created_at,
    }


@router.get("")
async def list_users(session: AsyncSession = Depends(get_db)):
    rows = (await session.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("")
async def create_user(body: UserCreate, session: AsyncSession = Depends(get_db)):
    # Cho phép email thuộc mọi tên miền — EmailStr đã kiểm tra định dạng hợp lệ.
    email = body.email.strip().lower()

    user_count = (await session.execute(select(func.count(User.id)))).scalar()
    if user_count >= 4:
        raise HTTPException(status_code=400, detail="Hệ thống đã đạt giới hạn 4 người dùng. Vui lòng xoá user cũ để thêm mới.")

    row = User(
        email=email,
        full_name=(body.full_name or "").strip() or None,
        job_title=(body.job_title or "").strip() or None,
        relation_type=body.relation_type,
    )
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
    if body.is_active is not None:
        row.is_active = body.is_active
    if body.full_name is not None:
        row.full_name = body.full_name.strip() or None
    if body.job_title is not None:
        row.job_title = body.job_title.strip() or None
    if body.relation_type is not None:
        row.relation_type = body.relation_type
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
