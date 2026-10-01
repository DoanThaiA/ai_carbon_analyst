"""add_contradicted_status_to_biz_suggestions

Gợi ý kinh doanh ngắn hạn có thêm trạng thái 'contradicted': thực tế diễn ra NGƯỢC
với giả định/kỳ vọng của đề xuất (khác 'triggered' = tình huống kích hoạt đã xảy ra).
Dùng lại triggered_report_date/trigger_evidence/evidence_source_* cho ngày + bằng chứng.

Revision ID: c9e5a3b7d1f2
Revises: b8f2d4e6a1c7
Create Date: 2026-10-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c9e5a3b7d1f2'
down_revision: Union[str, None] = 'b8f2d4e6a1c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_biz_suggestions_status', 'biz_suggestions', type_='check')
    op.create_check_constraint(
        'ck_biz_suggestions_status', 'biz_suggestions',
        "status IN ('pending', 'triggered', 'contradicted', 'dismissed')",
    )


def downgrade() -> None:
    op.execute("UPDATE biz_suggestions SET status = 'pending' WHERE status = 'contradicted'")
    op.drop_constraint('ck_biz_suggestions_status', 'biz_suggestions', type_='check')
    op.create_check_constraint(
        'ck_biz_suggestions_status', 'biz_suggestions', "status IN ('pending', 'triggered', 'dismissed')"
    )
