"""add reports.pdf_emailed_at

Revision ID: a8c3d1f5e9b2
Revises: d5b9e3f7a2c1
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a8c3d1f5e9b2'
down_revision: Union[str, None] = 'd5b9e3f7a2c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("pdf_emailed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "pdf_emailed_at")
