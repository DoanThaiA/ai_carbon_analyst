"""
Trích xuất nội dung chính + metadata (tiêu đề, ngày đăng) từ HTML thô của 1
bài viết, dùng trafilatura. Input là raw_html đã crawl được (fetcher.py /
crawler.py) - module này KHÔNG tự fetch.

Hệ thống trích xuất ngày đăng 3 tầng:
  1. trafilatura metadata (đáng tin nhất — đọc schema.org, Open Graph, <time>, ...)
  2. Structured data fallback (JSON-LD datePublished, <meta article:published_time>, <time datetime>)
  3. Regex fallback cho site Việt Nam dùng format dd/mm/yyyy trong DOM
Kết quả qua bộ lọc sanity check: nếu ngày > hôm nay hoặc < 2020 → bỏ qua.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import trafilatura

from schemas.crawl_models import CrawledItem, DateConfidence, ExtractedArticle

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 200  # bài quá ngắn thường là lỗi extract (trang chặn/redirect/404 dạng HTML)

_MIN_VALID_DATE = datetime(2000, 1, 1, tzinfo=timezone.utc)


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

    # Nếu trafilatura không tìm được ngày hoặc ngày = hôm nay (có thể sai —
    # nhiều site VN hiển thị ngày hiện tại trên sidebar/header), thử fallback.
    if not published_at or _is_likely_today(published_at):
        fallback = _extract_date_from_html(item.raw_html)
        if fallback:
            if published_at and _is_likely_today(published_at) and not _is_likely_today(fallback):
                # trafilatura trả về hôm nay nhưng HTML có ngày khác → ưu tiên HTML
                logger.debug(
                    "[DATE-FIX] Override trafilatura date (%s) bằng HTML date (%s): %s",
                    published_at.date(), fallback.date(), item.url,
                )
                published_at = fallback
            elif not published_at:
                published_at = fallback

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
        region=item.region,
    )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """trafilatura chuẩn hoá ngày về dạng 'YYYY-MM-DD' (không có giờ)."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt if _is_sane_date(dt) else None
    except ValueError:
        logger.warning("Không parse được ngày đăng '%s'", date_str)
        return None


def _is_likely_today(dt: datetime) -> bool:
    """Kiểm tra ngày có phải hôm nay không (UTC)."""
    return dt.date() == datetime.now(timezone.utc).date()


def _is_sane_date(dt: datetime) -> bool:
    """Sanity check: ngày phải nằm trong khoảng 2020 → ngày mai."""
    tomorrow = datetime.now(timezone.utc).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    return _MIN_VALID_DATE <= dt <= tomorrow


def _extract_date_from_html(html: str) -> Optional[datetime]:
    """
    Fallback trích xuất ngày đăng từ HTML thô khi trafilatura thất bại.

    Ưu tiên theo thứ tự tin cậy:
      1. JSON-LD "datePublished"
      2. <meta property="article:published_time">
      3. <time datetime="...">
      4. Visible text dd/mm/yyyy hoặc dd-mm-yyyy (phổ biến ở site VN)
    """
    # 1. JSON-LD datePublished (chuẩn schema.org — đáng tin nhất)
    jsonld_match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if jsonld_match:
        dt = _try_parse_iso(jsonld_match.group(1))
        if dt and _is_sane_date(dt):
            return dt

    # 2. <meta> Open Graph / article:published_time
    meta_match = re.search(
        r'<meta[^>]*(?:property|name)\s*=\s*"(?:article:published_time|datePublished|pubdate|DC\.date\.issued)"'
        r'[^>]*content\s*=\s*"([^"]+)"',
        html, re.IGNORECASE,
    )
    if meta_match:
        dt = _try_parse_iso(meta_match.group(1))
        if dt and _is_sane_date(dt):
            return dt

    # 3. <time datetime="..."> (HTML5 semantic)
    time_match = re.search(r'<time[^>]*datetime="([^"]+)"', html, re.IGNORECASE)
    if time_match:
        dt = _try_parse_iso(time_match.group(1))
        if dt and _is_sane_date(dt):
            return dt

    # 4. Visible text: "HH:mm dd/mm/yyyy" hoặc "dd/mm/yyyy" (rất phổ biến ở site VN)
    #    Tìm pattern đầu tiên trong content area (giữa các tag HTML)
    dmy_match = re.search(r'>\s*(?:\d{2}:\d{2}\s+)?(\d{2}[/\-]\d{2}[/\-]\d{4})\s*<', html)
    if dmy_match:
        date_str = dmy_match.group(1).replace("-", "/")
        try:
            dt = datetime.strptime(date_str, "%d/%m/%Y").replace(tzinfo=timezone.utc)
            if _is_sane_date(dt):
                return dt
        except ValueError:
            pass

    return None


def _try_parse_iso(s: str) -> Optional[datetime]:
    """Parse chuỗi ISO-8601 linh hoạt (có/không timezone, có/không giờ)."""
    # Clean HTML entities
    s = s.replace("&#x2B;", "+").replace("&amp;", "&").strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",      # 2026-08-27T07:36:49+07:00
        "%Y-%m-%dT%H:%M:%S.%f%z",   # 2026-08-27T00:47:04.690Z
        "%Y-%m-%dT%H:%M:%S",        # 2026-08-27T07:36:49
        "%Y-%m-%d",                  # 2026-08-27
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
