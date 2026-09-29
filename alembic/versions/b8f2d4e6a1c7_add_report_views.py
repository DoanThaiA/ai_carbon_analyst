"""add_report_views

Ghi nhận lượt mở báo cáo — dữ liệu cho trang Thống kê hiệu suất.

Revision ID: b8f2d4e6a1c7
Revises: a4e9c1d7b3f5
Create Date: 2026-09-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8f2d4e6a1c7'
down_revision: Union[str, None] = 'a4e9c1d7b3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'report_views',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('report_date', sa.Text(), nullable=False),
        sa.Column('viewer', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('viewed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_report_views_report_date', 'report_views', ['report_date'])
    op.create_index('ix_report_views_viewer_date', 'report_views', ['viewer', 'report_date', 'viewed_at'])


def downgrade() -> None:
    op.drop_index('ix_report_views_viewer_date', table_name='report_views')
    op.drop_index('ix_report_views_report_date', table_name='report_views')
    op.drop_table('report_views')
