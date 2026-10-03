"""add_api_tokens_and_claude_handoffs

Revision ID: a7c1e9d4b2f6
Revises: d1f6b4a8c2e7
Create Date: 2026-10-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c1e9d4b2f6'
down_revision: Union[str, None] = 'd1f6b4a8c2e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_email', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('token_prefix', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_api_tokens_token_hash'),
    )
    op.create_index('idx_api_tokens_user_email', 'api_tokens', ['user_email'])

    op.create_table(
        'claude_handoffs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('handoff_id', sa.Text(), nullable=False),
        sa.Column('user_email', sa.Text(), nullable=False),
        sa.Column('report_date', sa.Text(), nullable=False),
        sa.Column('quote', sa.Text(), nullable=False, server_default=''),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('task_type', sa.Text(), nullable=False, server_default='other'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "task_type IN ('pdf', 'excel', 'strategy', 'other')",
            name='ck_claude_handoffs_task_type',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('handoff_id', name='uq_claude_handoffs_handoff_id'),
    )
    op.create_index('idx_claude_handoffs_user_email', 'claude_handoffs', ['user_email'])


def downgrade() -> None:
    op.drop_index('idx_claude_handoffs_user_email', table_name='claude_handoffs')
    op.drop_table('claude_handoffs')
    op.drop_index('idx_api_tokens_user_email', table_name='api_tokens')
    op.drop_table('api_tokens')
