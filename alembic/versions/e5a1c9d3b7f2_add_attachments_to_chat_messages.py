"""add_attachments_to_chat_messages

Revision ID: e5a1c9d3b7f2
Revises: a3d8f2c1e9b7
Create Date: 2026-09-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e5a1c9d3b7f2'
down_revision: Union[str, None] = 'a3d8f2c1e9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Danh sách file đính kèm (ảnh/PDF/Word) của 1 tin nhắn user trong Quote
    # Chat — mảng object {file_name, file_key, media_type}, xem
    # schemas/chat_models.py::Attachment. NULL cho tin nhắn không đính kèm gì
    # (đa số) và cho tin nhắn 'assistant' (chỉ 'user' có thể đính kèm file).
    op.add_column('chat_messages', sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_messages', 'attachments')
