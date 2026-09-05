"""Quản lý override nội dung khung tri thức nhân quả EUA cho admin — mỗi khối
tri thức (xem services/eua_causal_chains.py::BLOCK_DEFAULTS) có thể bị admin
ghi đè nội dung qua bảng `eua_framework_overrides`; không có override thì dùng
bản mặc định trong code. KHÔNG đổi được cơ chế nào áp dụng cho topic nào (đó là
MECHANISM_REGISTRY/build_context() trong eua_causal_chains.py, cố định trong
code) — module này chỉ quản lý NỘI DUNG của từng khối.
"""
from typing import Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import EuaFrameworkOverride
from services import eua_causal_chains as chains


async def get_overrides_map(session: AsyncSession) -> Dict[str, str]:
    """Toàn bộ override hiện có — dùng để tiêm vào build_context()/domain
    knowledge khi sinh báo cáo hoặc chat. 1 query duy nhất; bảng rất nhỏ (tối
    đa bằng số khối trong BLOCK_DEFAULTS, hiện 17) nên không cần cache thêm."""
    result = await session.execute(select(EuaFrameworkOverride))
    return {row.block_id: row.content for row in result.scalars().all()}


def _to_admin_dict(block_id: str, row: Optional[EuaFrameworkOverride]) -> dict:
    return {
        "block_id": block_id,
        "title": chains.BLOCK_TITLES.get(block_id, block_id),
        "default_content": chains.BLOCK_DEFAULTS[block_id],
        "custom_content": row.content if row else None,
        "is_customized": row is not None,
        "updated_at": row.updated_at if row else None,
        "updated_by": row.updated_by if row else None,
    }


async def list_blocks_for_admin(session: AsyncSession) -> List[dict]:
    """Danh sách đầy đủ cho màn hình admin — mọi khối trong BLOCK_DEFAULTS, kèm
    bản mặc định + override hiện có (nếu có)."""
    result = await session.execute(select(EuaFrameworkOverride))
    rows_by_id = {row.block_id: row for row in result.scalars().all()}
    return [_to_admin_dict(block_id, rows_by_id.get(block_id)) for block_id in chains.BLOCK_DEFAULTS]


async def get_block_for_admin(session: AsyncSession, *, block_id: str) -> Optional[dict]:
    if block_id not in chains.BLOCK_DEFAULTS:
        return None
    row = await session.get(EuaFrameworkOverride, block_id)
    return _to_admin_dict(block_id, row)


async def set_override(
    session: AsyncSession, *, block_id: str, content: str, updated_by: Optional[str]
) -> dict:
    """Ghi đè nội dung 1 khối. Raise ValueError nếu block_id không tồn tại
    trong BLOCK_DEFAULTS (chặn tạo override cho khối không dùng ở đâu cả)."""
    if block_id not in chains.BLOCK_DEFAULTS:
        raise ValueError(f"Không tồn tại khối tri thức '{block_id}'.")

    row = await session.get(EuaFrameworkOverride, block_id)
    if row is None:
        row = EuaFrameworkOverride(block_id=block_id, content=content, updated_by=updated_by)
        session.add(row)
    else:
        row.content = content
        row.updated_by = updated_by
    await session.commit()
    await session.refresh(row)
    return _to_admin_dict(block_id, row)


async def reset_override(session: AsyncSession, *, block_id: str) -> Optional[dict]:
    """Xoá override, quay lại dùng bản mặc định — trả None nếu block_id không
    tồn tại trong BLOCK_DEFAULTS."""
    if block_id not in chains.BLOCK_DEFAULTS:
        return None
    await session.execute(delete(EuaFrameworkOverride).where(EuaFrameworkOverride.block_id == block_id))
    await session.commit()
    return _to_admin_dict(block_id, None)
