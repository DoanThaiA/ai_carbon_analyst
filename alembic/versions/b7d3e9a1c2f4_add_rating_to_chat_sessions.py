"""add_rating_to_chat_sessions

Revision ID: b7d3e9a1c2f4
Revises: a1b2c3d4e5f6
Create Date: 2026-09-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d3e9a1c2f4'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_sessions', sa.Column('rating', sa.Text(), nullable=True))
    op.add_column('chat_sessions', sa.Column('rating_reason', sa.Text(), nullable=True))
    op.add_column('chat_sessions', sa.Column('rated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        'ck_chat_sessions_rating', 'chat_sessions', "rating IN ('good', 'bad')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_chat_sessions_rating', 'chat_sessions', type_='check')
    op.drop_column('chat_sessions', 'rated_at')
    op.drop_column('chat_sessions', 'rating_reason')
    op.drop_column('chat_sessions', 'rating')
