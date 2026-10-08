"""add_report_qc_results

Revision ID: b9e4c2a7d1f3
Revises: a7c1e9d4b2f6
Create Date: 2026-10-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b9e4c2a7d1f3'
down_revision: Union[str, None] = 'a7c1e9d4b2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'report_qc_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('report_date', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), server_default='running', nullable=False),
        sa.Column('overall_score', sa.Integer(), nullable=True),
        sa.Column('price_accuracy_score', sa.Integer(), nullable=True),
        sa.Column('scenario_score', sa.Integer(), nullable=True),
        sa.Column('source_score', sa.Integer(), nullable=True),
        sa.Column('calendar_score', sa.Integer(), nullable=True),
        sa.Column('biz_score', sa.Integer(), nullable=True),
        sa.Column('issues', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('checked_by', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['report_date'], ['reports.report_date'], ondelete='CASCADE'),
        sa.CheckConstraint("status IN ('running', 'done', 'failed')", name='ck_report_qc_results_status'),
    )
    op.create_index('ix_report_qc_results_report_date', 'report_qc_results', ['report_date'])


def downgrade() -> None:
    op.drop_index('ix_report_qc_results_report_date', table_name='report_qc_results')
    op.drop_table('report_qc_results')
