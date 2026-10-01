"""add_profile_to_users

Thông tin đồng nghiệp của Jenny: họ tên, chức danh, mối quan hệ ('superior' = cấp
trên → quyền admin + chỉnh sửa/đánh giá; 'peer' = đồng cấp → xem/chat/hỏi tư vấn/
đánh giá, không sửa). Phân quyền + phạm vi tương tác hiển thị suy ra từ mối quan hệ.

Revision ID: d1f6b4a8c2e7
Revises: c9e5a3b7d1f2
Create Date: 2026-10-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1f6b4a8c2e7'
down_revision: Union[str, None] = 'c9e5a3b7d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('full_name', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('job_title', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('relation_type', sa.Text(), server_default='peer', nullable=False))
    op.create_check_constraint('ck_users_relation_type', 'users', "relation_type IN ('superior', 'peer')")


def downgrade() -> None:
    op.drop_constraint('ck_users_relation_type', 'users', type_='check')
    op.drop_column('users', 'relation_type')
    op.drop_column('users', 'job_title')
    op.drop_column('users', 'full_name')
