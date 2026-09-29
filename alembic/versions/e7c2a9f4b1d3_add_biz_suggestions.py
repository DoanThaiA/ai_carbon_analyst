"""add_biz_suggestions

Revision ID: e7c2a9f4b1d3
Revises: b6d2e8f4a1c3
Create Date: 2026-09-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e7c2a9f4b1d3'
down_revision: Union[str, None] = 'b6d2e8f4a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'biz_suggestions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('first_report_date', sa.Text(), nullable=False),
        sa.Column('trigger', sa.Text(), nullable=False),
        sa.Column('trigger_rule', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), server_default='', nullable=False),
        sa.Column('status', sa.Text(), server_default='pending', nullable=False),
        sa.Column('triggered_report_date', sa.Text(), nullable=True),
        sa.Column('trigger_evidence', sa.Text(), nullable=True),
        sa.Column('evidence_source_name', sa.Text(), nullable=True),
        sa.Column('evidence_source_url', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'triggered')", name='ck_biz_suggestions_status'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_biz_suggestions_status_date', 'biz_suggestions', ['status', 'first_report_date'])


def downgrade() -> None:
    op.drop_index('ix_biz_suggestions_status_date', table_name='biz_suggestions')
    op.drop_table('biz_suggestions')
