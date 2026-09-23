"""add_hot_news_emailed_at_to_articles

Revision ID: a9c3e7f1b5d2
Revises: f4b8d1e6a3c7
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9c3e7f1b5d2'
down_revision: Union[str, None] = 'f4b8d1e6a3c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('articles', sa.Column('hot_news_emailed_at', sa.DateTime(timezone=True), nullable=True))
    # Đánh dấu toàn bộ hot news CŨ là "đã gửi" — tránh lần crawl đầu tiên sau
    # khi deploy gửi dồn cả lịch sử hot news cho user.
    op.execute("UPDATE articles SET hot_news_emailed_at = crawled_at WHERE is_hot_news = true")
    op.create_index(
        'idx_articles_hot_news_pending_email', 'articles', ['crawled_at'],
        postgresql_where=sa.text('is_hot_news = true AND hot_news_emailed_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('idx_articles_hot_news_pending_email', table_name='articles')
    op.drop_column('articles', 'hot_news_emailed_at')
