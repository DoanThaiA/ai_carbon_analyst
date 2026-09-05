"""Admin custom nội dung khung tri thức nhân quả EUA — xem
services/eua_causal_chains.py (danh sách khối + cơ chế cố định) và
services/eua_framework_admin.py (đọc/ghi override trong DB).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from schemas.eua_framework_models import EuaFrameworkBlock, EuaFrameworkBlockUpdateRequest
from services.eua_framework_admin import (
    get_block_for_admin,
    list_blocks_for_admin,
    reset_override,
    set_override,
)

router = APIRouter(
    prefix="/api/admin/eua-framework",
    tags=["admin-eua-framework"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("/blocks", response_model=list[EuaFrameworkBlock])
async def list_blocks(session: AsyncSession = Depends(get_db)):
    return await list_blocks_for_admin(session)


@router.put("/blocks/{block_id}", response_model=EuaFrameworkBlock)
async def update_block(
    block_id: str,
    body: EuaFrameworkBlockUpdateRequest,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_admin),
):
    try:
        return await set_override(
            session, block_id=block_id, content=body.content, updated_by=payload.get("sub")
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Không tồn tại khối tri thức này.")


@router.delete("/blocks/{block_id}", response_model=EuaFrameworkBlock)
async def reset_block(block_id: str, session: AsyncSession = Depends(get_db)):
    """Xoá override — quay lại dùng bản mặc định trong code."""
    result = await reset_override(session, block_id=block_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Không tồn tại khối tri thức này.")
    return result


@router.get("/blocks/{block_id}", response_model=EuaFrameworkBlock)
async def get_block(block_id: str, session: AsyncSession = Depends(get_db)):
    result = await get_block_for_admin(session, block_id=block_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Không tồn tại khối tri thức này.")
    return result
