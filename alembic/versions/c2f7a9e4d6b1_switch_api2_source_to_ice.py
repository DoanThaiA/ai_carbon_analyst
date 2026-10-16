"""chuyển nguồn giá API2 từ Barchart (ITF*1) sang ICE

Barchart ngừng cập nhật mọi kỳ hạn API2 (ITF*) từ phiên 2023-08-18 dù vẫn trả HTTP 200 —
báo cáo luôn ghi "thiếu API2". Lấy thẳng từ dữ liệu trễ công khai của ICE
(crawl_prices/crawl_ice.py): symbol ICE:<productId>:<hubId>:<pageId>.
Chỉ đổi dòng còn đang dùng ITF*1 (admin đã tự sửa thì giữ nguyên).

Revision ID: c2f7a9e4d6b1
Revises: b2e7c4a9d1f3
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c2f7a9e4d6b1'
down_revision: Union[str, None] = 'b2e7c4a9d1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_SYMBOL = "ITF*1"
NEW_SYMBOL = "ICE:517:681:243"


def upgrade() -> None:
    op.execute(
        f"UPDATE price_crawl_sources SET symbol = '{NEW_SYMBOL}', exchange = 'ICE', updated_at = now() "
        f"WHERE instrument_code = 'API2' AND symbol = '{OLD_SYMBOL}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE price_crawl_sources SET symbol = '{OLD_SYMBOL}', updated_at = now() "
        f"WHERE instrument_code = 'API2' AND symbol = '{NEW_SYMBOL}'"
    )
