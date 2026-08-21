"""add_hot_news_columns_to_articles

Revision ID: e3bfab111b29
Revises: a2e20fcd514a
Create Date: 2026-08-21 14:35:02.089780
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3bfab111b29'
down_revision: Union[str, None] = 'a2e20fcd514a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'articles',
        sa.Column('is_hot_news', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column('articles', sa.Column('hot_news_reason', sa.Text(), nullable=True))
    op.create_index(
        'idx_articles_hot_news_crawled', 'articles', ['crawled_at'],
        postgresql_where=sa.text('is_hot_news = true'),
    )


def downgrade() -> None:
    op.drop_index('idx_articles_hot_news_crawled', table_name='articles')
    op.drop_column('articles', 'hot_news_reason')
    op.drop_column('articles', 'is_hot_news')
