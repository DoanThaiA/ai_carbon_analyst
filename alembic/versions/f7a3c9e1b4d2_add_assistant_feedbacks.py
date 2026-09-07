"""add_assistant_feedbacks

Revision ID: f7a3c9e1b4d2
Revises: d2a7f5c9e3b8
Create Date: 2026-09-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7a3c9e1b4d2'
down_revision: Union[str, None] = 'd2a7f5c9e3b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'assistant_feedbacks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('reporter_name', sa.Text(), nullable=True),
        sa.Column('reporter_role', sa.Text(), server_default='guest', nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("reporter_role IN ('user', 'admin', 'guest')", name='ck_assistant_feedbacks_reporter_role'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('assistant_feedbacks')
