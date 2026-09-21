"""add_release_notes

Revision ID: b3f9a7c1d5e8
Revises: e5a1c9d3b7f2
Create Date: 2026-09-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f9a7c1d5e8'
down_revision: Union[str, None] = 'e5a1c9d3b7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'release_notes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('customer_request', sa.Text(), nullable=False),
        sa.Column('change_description', sa.Text(), nullable=False),
        sa.Column('test_result', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default='chua_dat', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('dat', 'chua_dat')", name='ck_release_notes_status'),
    )


def downgrade() -> None:
    op.drop_table('release_notes')
