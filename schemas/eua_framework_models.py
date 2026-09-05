"""Data models cho màn hình admin custom khung tri thức nhân quả EUA — xem
services/eua_causal_chains.py::BLOCK_DEFAULTS và services/eua_framework_admin.py.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class EuaFrameworkBlock(BaseModel):
    block_id: str
    title: str
    default_content: str
    custom_content: Optional[str] = None
    is_customized: bool
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class EuaFrameworkBlockUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
