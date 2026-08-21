"""
Crawler tin tức: ưu tiên RSS khi nguồn có sẵn feed, fallback sang tìm link
bài viết trên trang listing khi không có RSS.

Crawler CHỈ chịu trách nhiệm lấy HTML thô (raw_html). Việc trích xuất nội
dụng chính, dedupe, phân loại thuộc về pipeline (extraction.py / dedupe.py /
classification.py / pipeline.py) - tách riêng để 2 phần có thể test và scale
độc lập.

Playwright vs curl_cffi:
  - crawl_source() nhận thêm `playwright_fetcher` optional.
  - Nếu source.use_playwright=True và playwright_fetcher khả dụng:
      → Dùng Playwright để tải listing page (JS render xong rồi mới parse link).
  - Bài viết riêng lẻ luôn dùng fetcher thường để tiết kiệm tài nguyên.
"""
import logging
import re
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Set
from urllib.parse import urljoin, urlparse

import feedparser
from selectolax.parser import HTMLParser

from crawl_news.fetcher import PoliteFetcher
from schemas.crawl_models import CrawledItem, SourceConfig

if TYPE_CHECKING:
    from crawl_news.playwright_fetcher import PlaywrightFetcher

logger = logging.getLogger(__name__)

MAX_LINKS_PER_LISTING_PAGE = 25


async def crawl_source(
    fetcher: PoliteFetcher,
    source: SourceConfig,
    seen_urls: Set[str],
    playwright_fetcher: Optional["PlaywrightFetcher"] = None,
) -> List[CrawledItem]:
    """Crawl 1 nguồn theo config, trả về danh sách bài viết chưa từng thấy.

    Nếu source.use_playwright=True và playwright_fetcher được cung cấp,
    sẽ dùng Playwright để lấy HTML listing page (JS render xong rồi mới parse link).
    Bài viết riêng lẻ luôn dùng fetcher (curl_cffi) để tiết kiệm tài nguyên.
    """
    try:
        if source.type == "rss" and source.rss_url:
            return await _crawl_rss(fetcher, source, seen_urls)
        return await _crawl_html_listing(fetcher, source, seen_urls, playwright_fetcher)
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
    limit = source.max_articles or MAX_LINKS_PER_LISTING_PAGE
    items = []
    for entry in parsed.entries[:limit]:
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
    fetcher: PoliteFetcher,
    source: SourceConfig,
    seen_urls: Set[str],
    playwright_fetcher: Optional["PlaywrightFetcher"] = None,
) -> List[CrawledItem]:
    if not source.listing_url:
        logger.warning("[SKIP] %s không có listing_url lẫn rss_url", source.domain)
        return []

    # Chọn fetcher cho listing page:
    # - use_playwright=True + playwright_fetcher có sẵn → dùng Playwright (JS render)
    # - Còn lại → dùng curl_cffi (nhanh hơn)
    if source.use_playwright and playwright_fetcher is not None:
        logger.info("[Playwright] Dùng Playwright cho listing: %s", source.domain)
        listing_html = await playwright_fetcher.fetch(source.listing_url)
    else:
        listing_html = await fetcher.fetch(source.listing_url)

    if listing_html is None:
        return []

    candidate_urls = _extract_article_links(listing_html, source)
    limit = source.max_articles or MAX_LINKS_PER_LISTING_PAGE
    items = []
    for url in candidate_urls[:limit]:
        if url in seen_urls:
            continue
        # Chọn fetcher cho bài lẻ:
        # - use_playwright=True: dùng Playwright (site JS-rendered, curl_cffi bị block)
        # - Còn lại: dùng curl_cffi (nhanh hơn, đủ cho site HTML thường)
        if source.use_playwright and playwright_fetcher is not None:
            html = await playwright_fetcher.fetch(url)
        else:
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
        resp = await fetcher._client.head(google_url, allow_redirects=True)
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


def _numeric_id_sort_key(url: str) -> int:
    """Trích số ID từ query param ?id=XXXXX hoặc path /XXXXX-.html.
    Dùng để sort URL theo thứ tự mới nhất trước (số lớn = mới hơn).
    Trả về 0 nếu URL không chứa số.
    """
    m = re.search(r'[?&]id=(\d+)', url)
    if m:
        return int(m.group(1))
    m = re.search(r'/(\d{5,})', url)  # path-based numeric ID
    if m:
        return int(m.group(1))
    return 0


# Extensions tĩnh không phải bài viết
_SKIP_EXTENSIONS = (
    ".pdf", ".xls", ".xlsx", ".csv", ".zip",
    ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".mp4", ".mp3", ".doc", ".docx", ".ppt", ".pptx",
)


def _extract_article_links(listing_html: str, source: SourceConfig) -> List[str]:
    """
    Hếu lọc tìm link bài viết trên trang listing.

    Nếu source có `link_pattern` (regex) thì dùng regex đó thay hếu lọc chung.
    Hếu lọc chung yêu cầu path >=3 cấp (đã giảm bắt nhầm trang data/section có 2 cấp
    như /petroleum/gasdiesel/ hay /consumption/residential/).
    """
    tree = HTMLParser(listing_html)
    # Dùng listing_url làm base để urljoin resolve đúng href tương đối.
    # Ví dụ EIA: href="detail.php?id=67984" trên trang /todayinenergy/
    # → urljoin(listing_url, href) = "https://www.eia.gov/todayinenergy/detail.php?id=67984" ✓
    # Nếu dùng f"https://{domain}" thì ra "https://eia.gov/detail.php?id=67984" ✗ (sai path)
    base_url = source.listing_url or f"https://{source.domain}"
    found = []
    seen_in_page = set()

    for node in tree.css("a[href]"):
        href = node.attributes.get("href", "")
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        # Bỏ file tĩnh không phải bài viết (PDF, Excel, ảnh, ...)
        href_lower = href.lower().split("?")[0]
        if any(href_lower.endswith(ext) for ext in _SKIP_EXTENSIONS):
            continue

        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc and source.domain not in parsed.netloc:
            continue
        if any(excluded in parsed.path for excluded in source.exclude_path_patterns):
            continue

        # Bỏ file tĩnh qua path (trường hợp URL không có ?)
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in _SKIP_EXTENSIONS):
            continue

        if full_url in seen_in_page:
            continue

        # Bỏ pagination dạng /news/p2, /articles/p10 (path kết thúc bằng /p<số>)
        if re.search(r'/p\d+$', parsed.path, re.IGNORECASE):
            continue

        # Dùng regex riêng của nguồn nếu có, ngược lại dùng heuristic chung
        if source.link_pattern:
            if re.search(source.link_pattern, parsed.path, re.IGNORECASE):
                found.append(full_url)
                seen_in_page.add(full_url)
        elif _looks_like_article_path(parsed.path):
            found.append(full_url)
            seen_in_page.add(full_url)

    # Sort: ưu tiên bài có numeric ID lớn nhất (mới nhất) lên trước
    # Với các site không có numeric ID, sort key = 0 → thứ tự DOM giữ nguyên
    found.sort(key=_numeric_id_sort_key, reverse=True)

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
