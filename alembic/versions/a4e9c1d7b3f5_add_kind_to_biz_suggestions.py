"""add_kind_to_biz_suggestions

Lưu cả gợi ý DÀI HẠN (kind='long') vào biz_suggestions để admin gỡ được và LLM
không đề xuất lại ý đã bị gỡ. Gợi ý dài hạn KHÔNG có tình huống kích hoạt nên không
được theo dõi/nhắc lại — cột trigger/action/reason chứa opportunity/solution/expectation.

Revision ID: a4e9c1d7b3f5
Revises: f3d8b6a2c9e4
Create Date: 2026-09-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4e9c1d7b3f5'
down_revision: Union[str, None] = 'f3d8b6a2c9e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('biz_suggestions', sa.Column('kind', sa.Text(), server_default='short', nullable=False))
    op.create_check_constraint('ck_biz_suggestions_kind', 'biz_suggestions', "kind IN ('short', 'long')")


def downgrade() -> None:
    op.execute("DELETE FROM biz_suggestions WHERE kind = 'long'")
    op.drop_constraint('ck_biz_suggestions_kind', 'biz_suggestions', type_='check')
    op.drop_column('biz_suggestions', 'kind')
