"""add reports.content_en + content_en_source_hash

Revision ID: b2e7c4a9d1f3
Revises: a8c3d1f5e9b2
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b2e7c4a9d1f3'
down_revision: Union[str, None] = 'a8c3d1f5e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("content_en", postgresql.JSONB(), nullable=True))
    op.add_column("reports", sa.Column("content_en_source_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "content_en_source_hash")
    op.drop_column("reports", "content_en")
