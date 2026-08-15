"""
Crawler tin tức: ưu tiên RSS khi nguồn có sẵn feed, fallback sang tìm link
bài viết trên trang listing khi không có RSS.

Crawler CHỈ chịu trách nhiệm lấy HTML thô (raw_html). Việc trích xuất nội
dung chính, dedupe, phân loại thuộc về pipeline (extraction.py / dedupe.py /
classification.py / pipeline.py) - tách riêng để 2 phần có thể test và scale
độc lập.
"""
import logging
import re
from datetime import datetime, timezone
from typing import List, Set
from urllib.parse import urljoin, urlparse

import feedparser
from selectolax.parser import HTMLParser

from carbon_analyst.fetcher import PoliteFetcher
from carbon_analyst.models import CrawledItem, SourceConfig

logger = logging.getLogger(__name__)

MAX_LINKS_PER_LISTING_PAGE = 25


async def crawl_source(
    fetcher: PoliteFetcher, source: SourceConfig, seen_urls: Set[str]
) -> List[CrawledItem]:
    """Crawl 1 nguồn theo config, trả về danh sách bài viết chưa từng thấy."""
    try:
        if source.type == "rss" and source.rss_url:
            return await _crawl_rss(fetcher, source, seen_urls)
        return await _crawl_html_listing(fetcher, source, seen_urls)
    except Exception:
        logger.exception("[ERROR] Crawl nguồn %s thất bại", source.domain)
        return []


async def _crawl_rss(
    fetcher: PoliteFetcher, source: SourceConfig, seen_urls: Set[str]
) -> List[CrawledItem]:
    raw_feed = await fetcher.fetch(source.rss_url)
    if raw_feed is None:
        return []

    parsed = feedparser.parse(raw_feed)
    items = []
    for entry in parsed.entries:
        url = entry.get("link")
        if not url or url in seen_urls:
            continue

        html = await fetcher.fetch(url)
        if html is None:
            continue

        items.append(
            CrawledItem(
                url=url,
                source_domain=source.domain,
                tier=source.tier,
                title=entry.get("title"),
                raw_html=html,
                discovered_at=datetime.now(timezone.utc),
            )
        )
        seen_urls.add(url)

    logger.info("[RSS] %-30s -> %d bài mới", source.domain, len(items))
    return items


async def _crawl_html_listing(
    fetcher: PoliteFetcher, source: SourceConfig, seen_urls: Set[str]
) -> List[CrawledItem]:
    if not source.listing_url:
        logger.warning("[SKIP] %s không có listing_url lẫn rss_url", source.domain)
        return []

    listing_html = await fetcher.fetch(source.listing_url)
    if listing_html is None:
        return []

    candidate_urls = _extract_article_links(listing_html, source)
    items = []
    for url in candidate_urls[:MAX_LINKS_PER_LISTING_PAGE]:
        if url in seen_urls:
            continue
        html = await fetcher.fetch(url)
        if html is None:
            continue

        items.append(
            CrawledItem(
                url=url,
                source_domain=source.domain,
                tier=source.tier,
                title=None,  # lấy chính xác hơn ở bước extraction.py (trafilatura)
                raw_html=html,
                discovered_at=datetime.now(timezone.utc),
            )
        )
        seen_urls.add(url)

    logger.info("[HTML] %-30s -> %d bài mới (%d link ứng viên)",
                source.domain, len(items), len(candidate_urls))
    return items


def _extract_article_links(listing_html: str, source: SourceConfig) -> List[str]:
    """
    Heuristic tìm link bài viết trên trang listing:
    - Cùng domain với nguồn
    - Không nằm trong path bị loại trừ (tag, category, author...)
    - Path có dạng giống bài viết (>=2 cấp, slug đủ dài)

    Đây là heuristic DÙNG CHUNG cho ~50 nguồn khác nhau trong danh mục JD,
    hoạt động ổn với cấu trúc WordPress/blog phổ biến. Với site có cấu trúc
    đặc thù (ví dụ Bloomberg, FT cần đăng nhập, hoặc site dùng SPA/JS render),
    heuristic này sẽ trả về ít hoặc không có link - cần bổ sung field
    `link_pattern` (regex riêng) cho nguồn đó trong sources.yaml, hoặc dùng
    Playwright thay vì httpx cho các site cần JS render.
    """
    tree = HTMLParser(listing_html)
    base_url = f"https://{source.domain}"
    found = []
    seen_in_page = set()

    for node in tree.css("a[href]"):
        href = node.attributes.get("href", "")
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc and source.domain not in parsed.netloc:
            continue
        if any(excluded in parsed.path for excluded in source.exclude_path_patterns):
            continue
        if full_url in seen_in_page:
            continue
        if _looks_like_article_path(parsed.path):
            found.append(full_url)
            seen_in_page.add(full_url)

    return found


def _looks_like_article_path(path: str) -> bool:
    """Bài viết thường có path >=2 cấp và slug cuối chứa chữ/số đủ dài (không phải trang tĩnh)."""
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        return False
    last_segment = segments[-1]
    return bool(re.search(r"[a-z0-9\-]{8,}", last_segment, re.IGNORECASE))
