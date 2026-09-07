"""require_login_for_assistant_feedbacks

Revision ID: a3d8f2c1e9b7
Revises: f7a3c9e1b4d2
Create Date: 2026-09-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3d8f2c1e9b7'
down_revision: Union[str, None] = 'f7a3c9e1b4d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phản ánh giờ chỉ gửi được khi đã đăng nhập — gắn với email tài khoản,
    # không còn cho phép "Khách" (guest) ẩn danh.
    op.execute("DELETE FROM assistant_feedbacks WHERE reporter_role = 'guest'")
    op.add_column('assistant_feedbacks', sa.Column('user_email', sa.Text(), nullable=False, server_default=''))
    op.alter_column('assistant_feedbacks', 'user_email', server_default=None)
    op.drop_constraint('ck_assistant_feedbacks_reporter_role', 'assistant_feedbacks', type_='check')
    op.create_check_constraint(
        'ck_assistant_feedbacks_reporter_role', 'assistant_feedbacks', "reporter_role IN ('user', 'admin')"
    )
    op.alter_column('assistant_feedbacks', 'reporter_role', server_default='user')


def downgrade() -> None:
    op.alter_column('assistant_feedbacks', 'reporter_role', server_default='guest')
    op.drop_constraint('ck_assistant_feedbacks_reporter_role', 'assistant_feedbacks', type_='check')
    op.create_check_constraint(
        'ck_assistant_feedbacks_reporter_role', 'assistant_feedbacks', "reporter_role IN ('user', 'admin', 'guest')"
    )
    op.drop_column('assistant_feedbacks', 'user_email')
