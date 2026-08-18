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
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
from typing import List, Set
from urllib.parse import urljoin, urlparse

import feedparser
from selectolax.parser import HTMLParser

from crawl_services.fetcher import PoliteFetcher
from schemas.crawl_models import CrawledItem, SourceConfig

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
    for entry in parsed.entries[:MAX_LINKS_PER_LISTING_PAGE]:
        url = entry.get("link")
        if not url or url in seen_urls:
            continue

        # Google News RSS: follow redirect để lấy URL bài gốc thật sự.
        # (field summary cũng chỉ chứa Google URL, không phải URL bài)
        if "news.google.com" in url:
            real_url = await _resolve_gnews_url(url, fetcher)
            if real_url:
                url = real_url
            else:
                logger.warning("[GNews] Không resolve được URL: %.80s", url)
                continue

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
                title=None,
                raw_html=html,
                discovered_at=datetime.now(timezone.utc),
            )
        )
        seen_urls.add(url)

    logger.info("[HTML] %-30s -> %d bài mới (%d link ứng viên)",
                source.domain, len(items), len(candidate_urls))
    return items


async def _resolve_gnews_url(google_url: str, fetcher: "PoliteFetcher") -> str | None:
    """
    Resolve URL redirect của Google News để lấy URL bài viết gốc.

    Cách hoạt động:
    1. Gửi HEAD request đến Google News URL.
    2. httpx follow redirect tự động đến URL cuối cùng.
    3. Nếu URL cuối không phải của Google → đó là URL bài gốc.

    Dùng HEAD (không tải body) để tiết kiệm bandwidth. Body sẽ được GET
    sau bởi fetcher.fetch() bình thường.
    """
    try:
        resp = await fetcher._client.head(google_url, follow_redirects=True)
        final_url = str(resp.url)
        if "google.com" not in final_url:
            logger.debug("[GNews] resolve: %s", final_url)
            return final_url
        # Nếu vẫn ở lại Google (Google dùng JS redirect), thử GET một lần
        # và lấy Location header nếu có
        if "Location" in resp.headers:
            loc = resp.headers["Location"]
            if "google.com" not in loc:
                return loc
    except Exception as e:
        logger.warning("[GNews] Lỗi resolve %s: %s", google_url[:60], e)
    return None


def _extract_gnews_article_url(entry: dict) -> str | None:
    """
    [DEPRECATED] - summary cũng chứa Google URL, không dùng được.
    Dùng _resolve_gnews_url() thay thế.
    """
    return None


def _decode_google_news_url(url: str) -> str:
    """
    [DEPRECATED] - không dùng nữa.
    """
    return url


def _extract_article_links(listing_html: str, source: SourceConfig) -> List[str]:
    """
    Hếu lọ tìm link bài viết trên trang listing.

    Nếu source có `link_pattern` (regex) thì dùng regex đó thay hếu lọ chung.
    Hếu lọ chung yêu cầu path >=3 cấp (giảm bắt nhầm trang data/section có 2 cấp
    như /petroleum/gasdiesel/ hay /consumption/residential/).
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

        # Dùng regex riêng của nguồn nếu có, ngược lại dùng heuristic chung
        if source.link_pattern:
            if re.search(source.link_pattern, parsed.path, re.IGNORECASE):
                found.append(full_url)
                seen_in_page.add(full_url)
        elif _looks_like_article_path(parsed.path):
            found.append(full_url)
            seen_in_page.add(full_url)

    return found


def _looks_like_article_path(path: str) -> bool:
    """
    Bài viết thường có path >=3 cấp và slug cuối chứa chữ/số đủ dài.
    Yêu cầu >=3 cấp (đã tăng từ 2) để tránh bắt nhầm trang section/data
    có cấu trúc /category/subcategory/ như EIA, IEA.
    """
    segments = [s for s in path.split("/") if s]
    if len(segments) < 3:
        return False
    last_segment = segments[-1]
    return bool(re.search(r"[a-z0-9\-]{8,}", last_segment, re.IGNORECASE))
