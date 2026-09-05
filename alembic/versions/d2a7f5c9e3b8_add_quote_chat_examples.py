"""add_quote_chat_examples

Revision ID: d2a7f5c9e3b8
Revises: c8f4a2d6b1e9
Create Date: 2026-09-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2a7f5c9e3b8'
down_revision: Union[str, None] = 'c8f4a2d6b1e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'quote_chat_examples',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('source_session_id', sa.BigInteger(), nullable=True),
        sa.Column('source_answer_message_id', sa.BigInteger(), nullable=True),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['source_session_id'], ['chat_sessions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_answer_message_id'], ['chat_messages.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('source_answer_message_id', name='uq_quote_chat_examples_answer_message'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('quote_chat_examples')
