"""
Trích xuất nội dung chính + metadata (tiêu đề, ngày đăng) từ HTML thô của 1
bài viết, dùng trafilatura. Input là raw_html đã crawl được (fetcher.py /
crawler.py) - module này KHÔNG tự fetch.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import trafilatura

from schemas.crawl_models import CrawledItem, DateConfidence, ExtractedArticle

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 200  # bài quá ngắn thường là lỗi extract (trang chặn/redirect/404 dạng HTML)


def extract_article(item: CrawledItem) -> Optional[ExtractedArticle]:
    """
    Trích xuất content + metadata từ 1 CrawledItem.
    Trả về None nếu trafilatura không lấy được nội dung đủ dài để tin cậy
    (site chặn bot, trang không phải bài viết, JS-rendered content...).
    """
    raw_json = trafilatura.extract(
        item.raw_html,
        url=item.url,
        output_format="json",
        with_metadata=True,
        favor_recall=True,
    )
    if not raw_json:
        logger.warning("[EXTRACT-FAIL] Không trích xuất được nội dung: %s", item.url)
        return None

    parsed = json.loads(raw_json)
    text = (parsed.get("text") or "").strip()
    if len(text) < MIN_TEXT_LENGTH:
        logger.warning(
            "[EXTRACT-FAIL] Nội dung quá ngắn (%d ký tự), có thể site chặn bot: %s",
            len(text), item.url,
        )
        return None

    title = parsed.get("title") or item.title
    published_at = _parse_date(parsed.get("date"))
    # 'url' chưa được implement (chưa có logic đoán ngày từ path URL) — chỉ
    # phân biệt 'metadata' (trafilatura đọc được date) vs 'unknown'.
    date_confidence: DateConfidence = "metadata" if published_at is not None else "unknown"

    return ExtractedArticle(
        url=item.url,
        source_domain=item.source_domain,
        tier=item.tier,
        title=title,
        text=text,
        published_at=published_at,
        date_confidence=date_confidence,
        extracted_at=datetime.now(timezone.utc),
    )


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """trafilatura chuẩn hoá ngày về dạng 'YYYY-MM-DD' (không có giờ)."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Không parse được ngày đăng '%s'", date_str)
        return None
