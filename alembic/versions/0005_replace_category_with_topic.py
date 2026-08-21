"""Replace articles.category -> articles.topic with 13-topic taxonomy

Revision ID: 0005
Revises: 1ea9d13fd395
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "1ea9d13fd395"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VALID_TOPICS = (
    "'eua_ets','energy_gas','energy_power_eu','energy_coal','energy_oil',"
    "'energy_renewable','energy_hydrogen','geopolitics','eu_policy',"
    "'cbam','vcm','global_carbon_market','vietnam_carbon_policy'"
)


def upgrade() -> None:
    # 1. Xoa bo du lieu cu (user da xac nhan truncate articles)
    op.execute("TRUNCATE TABLE articles RESTART IDENTITY CASCADE")

    # 2. Drop constraint cu
    op.drop_constraint("ck_articles_category", "articles", type_="check")

    # 3. Rename column
    op.alter_column("articles", "category", new_column_name="topic")

    # 4. Them constraint moi voi 13 topic
    op.create_check_constraint(
        "ck_articles_topic",
        "articles",
        f"topic <@ ARRAY[{_VALID_TOPICS}]::text[]",
    )


def downgrade() -> None:
    op.drop_constraint("ck_articles_topic", "articles", type_="check")
    op.alter_column("articles", "topic", new_column_name="category")
    op.create_check_constraint(
        "ck_articles_category",
        "articles",
        "category <@ ARRAY['energy_fossil_fuels','carbon_credits','policy']::text[]",
    )
