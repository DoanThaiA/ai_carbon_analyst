"""add_news_crawl_sources

Revision ID: f4b8d1e6a3c7
Revises: d6f1a4c8b2e5
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f4b8d1e6a3c7'
down_revision: Union[str, None] = 'd6f1a4c8b2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'news_crawl_sources',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('domain', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('tier', sa.CHAR(length=1), nullable=False),
        sa.Column('region', sa.Text(), server_default='international', nullable=False),
        sa.Column('source_type', sa.Text(), server_default='html', nullable=False),
        sa.Column('listing_url', sa.Text(), nullable=True),
        sa.Column('rss_url', sa.Text(), nullable=True),
        sa.Column('link_pattern', sa.Text(), nullable=True),
        sa.Column('exclude_path_patterns', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('group', postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column('bloomberg_feeds', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('confidence', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('max_articles', sa.Integer(), nullable=True),
        sa.Column('use_playwright', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('is_noon_crawl', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.CheckConstraint("tier IN ('A', 'B', 'C')", name='ck_news_crawl_sources_tier'),
        sa.CheckConstraint(
            "region IN ('vietnam', 'international')", name='ck_news_crawl_sources_region'
        ),
        sa.CheckConstraint(
            "source_type IN ('html', 'rss', 'bloomberg_rss')",
            name='ck_news_crawl_sources_source_type',
        ),
    )
    op.create_index('idx_news_crawl_sources_domain', 'news_crawl_sources', ['domain'])


def downgrade() -> None:
    op.drop_index('idx_news_crawl_sources_domain', table_name='news_crawl_sources')
    op.drop_table('news_crawl_sources')
