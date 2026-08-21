"""add_auth_and_price_sources_tables

Revision ID: d4c21b21ed90
Revises: 784fe66c1b73
Create Date: 2026-08-20 23:50:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4c21b21ed90'
down_revision: Union[str, None] = '784fe66c1b73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Seed data — bản sao literal của BARCHART_SPECS trong crawl_prices/crawl_barchart.py
# tại thời điểm viết migration này, để crawler không bị gián đoạn khi chuyển sang
# đọc cấu hình từ DB thay vì hardcode.
_SEED_PRICE_SOURCES = [
    {"symbol": "CK*0", "instrument_code": "EUA", "instrument_name": "EUA Carbon Futures (Front Month)", "category": "carbon", "unit": "EUR/tCO2", "exchange": "ICE"},
    {"symbol": "TG*0", "instrument_code": "TTF", "instrument_name": "TTF Dutch Natural Gas", "category": "gas", "unit": "EUR/MWh", "exchange": "ICE"},
    {"symbol": "NG*0", "instrument_code": "NG", "instrument_name": "Henry Hub Natural Gas", "category": "gas", "unit": "USD/MMBtu", "exchange": "NYMEX"},
    {"symbol": "CB*0", "instrument_code": "BRENT", "instrument_name": "Brent Crude Oil", "category": "oil", "unit": "USD/bbl", "exchange": "ICE"},
    {"symbol": "CL*0", "instrument_code": "WTI", "instrument_name": "WTI Crude Oil", "category": "oil", "unit": "USD/bbl", "exchange": "NYMEX"},
    {"symbol": "LF*0", "instrument_code": "GASOIL", "instrument_name": "ICE Gasoil (Gas Oil)", "category": "oil", "unit": "USD/MT", "exchange": "ICE"},
    {"symbol": "ITF*1", "instrument_code": "API2", "instrument_name": "API2 Coal ARA", "category": "coal", "unit": "USD/MT", "exchange": "ICE"},
    {"symbol": "LV*0", "instrument_code": "API4", "instrument_name": "API4 Coal Richards Bay", "category": "coal", "unit": "USD/MT", "exchange": "ICE"},
]


def upgrade() -> None:
    price_sources = op.create_table(
        'price_crawl_sources',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('symbol', sa.Text(), nullable=False),
        sa.Column('instrument_code', sa.Text(), nullable=False),
        sa.Column('instrument_name', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('unit', sa.Text(), nullable=False),
        sa.Column('exchange', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instrument_code'),
    )

    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )

    op.create_table(
        'otp_codes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('code_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempt_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_otp_codes_email_created', 'otp_codes', ['email', 'created_at'])

    op.bulk_insert(price_sources, _SEED_PRICE_SOURCES)


def downgrade() -> None:
    op.drop_index('idx_otp_codes_email_created', table_name='otp_codes')
    op.drop_table('otp_codes')
    op.drop_table('users')
    op.drop_table('price_crawl_sources')
