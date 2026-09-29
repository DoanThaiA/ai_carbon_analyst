"""add_dismiss_to_biz_suggestions

Admin gỡ 1 gợi ý kinh doanh khỏi báo cáo (đề xuất phi thực tế...) → status
'dismissed' + ai gỡ/lúc nào/lý do; gợi ý bị gỡ không được theo dõi/nhắc lại nữa.

Revision ID: f3d8b6a2c9e4
Revises: e7c2a9f4b1d3
Create Date: 2026-09-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3d8b6a2c9e4'
down_revision: Union[str, None] = 'e7c2a9f4b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('biz_suggestions', sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('biz_suggestions', sa.Column('dismissed_by', sa.Text(), nullable=True))
    op.add_column('biz_suggestions', sa.Column('dismiss_reason', sa.Text(), nullable=True))
    op.drop_constraint('ck_biz_suggestions_status', 'biz_suggestions', type_='check')
    op.create_check_constraint(
        'ck_biz_suggestions_status', 'biz_suggestions', "status IN ('pending', 'triggered', 'dismissed')"
    )


def downgrade() -> None:
    op.execute("UPDATE biz_suggestions SET status = 'pending' WHERE status = 'dismissed'")
    op.drop_constraint('ck_biz_suggestions_status', 'biz_suggestions', type_='check')
    op.create_check_constraint('ck_biz_suggestions_status', 'biz_suggestions', "status IN ('pending', 'triggered')")
    op.drop_column('biz_suggestions', 'dismiss_reason')
    op.drop_column('biz_suggestions', 'dismissed_by')
    op.drop_column('biz_suggestions', 'dismissed_at')
