"""
Lọc bài viết theo ngày đăng — chỉ giữ bài đăng trong ngày crawl.

Chiến lược 2 tầng:
  1. Nếu published_at trích xuất được → so sánh với crawl_date (ngày local).
     - published_at cùng ngày với crawl_date → GIỮ
     - published_at khác ngày → BỎ
  2. Nếu published_at = None (trafilatura không parse được ngày, thường do site
     thiếu meta SEO) → GIỮ LẠI an toàn để không bỏ sót bài mới thật sự.

Lý do giữ bài khi None: một số site lớn (eia.gov, opec.org) không gắn meta
date chuẩn, trafilatura phải đoán và có thể sai. Bỏ bài khi ngày = None sẽ
mất bài mới của những nguồn này. Để lọc chặt hơn sau khi nguồn đã ổn định,
đặt fallback_keep=False.
"""
from datetime import date, datetime, timezone
from typing import Optional

from carbon_analyst.models import ExtractedArticle


def is_today(article: ExtractedArticle, crawl_date: Optional[date] = None, fallback_keep: bool = True) -> bool:
    """
    Trả về True nếu bài đăng vào ngày crawl (crawl_date).
    - crawl_date mặc định là hôm nay (UTC).
    - fallback_keep=True: giữ bài nếu không parse được ngày (an toàn).
    - fallback_keep=False: bỏ bài nếu không parse được ngày (strict).
    """
    target = crawl_date or date.today()

    if article.published_at is None:
        return fallback_keep

    # Chuẩn hóa về UTC date để so sánh
    pub_date = article.published_at.astimezone(timezone.utc).date()
    return pub_date == target


def filter_today(
    articles: list,
    crawl_date: Optional[date] = None,
    fallback_keep: bool = True,
) -> tuple[list, list]:
    """
    Lọc danh sách ExtractedArticle, chỉ giữ bài đăng hôm nay.

    Trả về (kept, skipped):
      - kept: danh sách bài hợp lệ (tiếp tục pipeline)
      - skipped: danh sách bài bị lọc ra (bài cũ)
    """
    kept, skipped = [], []
    for art in articles:
        if is_today(art, crawl_date, fallback_keep):
            kept.append(art)
        else:
            skipped.append(art)
    return kept, skipped
