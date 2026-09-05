"""add_eua_framework_overrides

Revision ID: c8f4a2d6b1e9
Revises: b7d3e9a1c2f4
Create Date: 2026-09-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8f4a2d6b1e9'
down_revision: Union[str, None] = 'b7d3e9a1c2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'eua_framework_overrides',
        sa.Column('block_id', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('block_id'),
    )


def downgrade() -> None:
    op.drop_table('eua_framework_overrides')
