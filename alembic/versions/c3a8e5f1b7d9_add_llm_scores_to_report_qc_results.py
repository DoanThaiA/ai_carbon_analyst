"""add_llm_scores_to_report_qc_results

Revision ID: c3a8e5f1b7d9
Revises: b9e4c2a7d1f3
Create Date: 2026-10-08 00:00:01.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3a8e5f1b7d9'
down_revision: Union[str, None] = 'b9e4c2a7d1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('report_qc_results', sa.Column('consistency_score', sa.Integer(), nullable=True))
    op.add_column('report_qc_results', sa.Column('causal_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('report_qc_results', 'causal_score')
    op.drop_column('report_qc_results', 'consistency_score')
