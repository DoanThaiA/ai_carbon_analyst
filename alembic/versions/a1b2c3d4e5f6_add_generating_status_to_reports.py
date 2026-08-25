"""add_generating_status_to_reports

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-08-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Mục /generate giờ chạy nền — cần trạng thái trung gian 'generating' (đang
    # sinh, chưa có content) và 'failed' (job lỗi, giữ lại error_message để
    # admin xem thay vì mất luôn báo cáo ngày đó).
    op.drop_constraint('ck_reports_status', 'reports', type_='check')
    op.create_check_constraint(
        'ck_reports_status', 'reports',
        "status IN ('draft', 'published', 'generating', 'failed')",
    )
    op.alter_column('reports', 'content', existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    op.add_column('reports', sa.Column('error_message', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('reports', 'error_message')
    op.alter_column('reports', 'content', existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    op.drop_constraint('ck_reports_status', 'reports', type_='check')
    op.create_check_constraint(
        'ck_reports_status', 'reports', "status IN ('draft', 'published')",
    )
