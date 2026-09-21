"""
Crawler chuyên biệt cho Bloomberg: lấy tiêu đề từ RSS ẩn của Bloomberg,
search bài syndicated trên các trang dễ crawl (Yahoo Finance, MSN, Reuters...)
thông qua Google News RSS Search, rồi fetch toàn văn từ trang trung gian.

Workflow:
  1. Fetch Bloomberg RSS feeds → parse bằng feedparser
  2. Với mỗi entry: dùng Google News RSS Search (https://news.google.com/rss/search?q=...)
     tìm các bài báo có tiêu đề tương tự. Ưu tiên domain dễ crawl (Yahoo, MSN...).
  3. Resolve link redirect của Google News ra URL bài gốc thật (xem
     _decode_gnews_url) rồi fetch HTML từ URL đó. Nếu decode thất bại (Google
     đổi định dạng nội bộ) mới fallback sang Playwright để tự render/theo JS
     redirect. Trang lỗi/chặn bot (Access Denied, CAPTCHA...) bị lọc ra
     (_looks_like_blocked_page) trước khi coi là fetch thành công.
  4. Trả về CrawledItem với source_domain="bloomberg.com", url=URL BÀI GỐC
     THẬT (không phải link redirect Google — quan trọng vì Article.url là
     unique key + nguồn trích dẫn cho báo cáo/audit), và rss_published_at
     = pubDate gốc từ Bloomberg RSS (dùng cho date filter chính xác).

Module này KHÔNG ảnh hưởng luồng crawl hiện tại — chỉ được gọi từ
crawler.py::crawl_source() khi source.type == "bloomberg_rss".
"""
import asyncio
import json
import logging
import random
import re
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

import feedparser

from crawl_news.fetcher import PoliteFetcher
from schemas.crawl_models import CrawledItem, SourceConfig

logger = logging.getLogger(__name__)

# Tên source ưu tiên từ Google News (source.title). Khớp theo substring nên
# không cần đúng 100% case — xem _is_preferred_source().
#
# Nhóm chuyên năng lượng/carbon (thêm để bao quát nguồn phụ đúng mảng của desk
# — đã verify sống từng cái: fetch được, không paywall/chặn bot, và đúng là
# bài Bloomberg syndicated hoặc bài gốc rất liên quan energy/carbon):
#   - Energy Connects (energyconnects.com): đăng lại rất nhiều tin Bloomberg
#     energy nguyên văn ("(Bloomberg) --...").
#   - gCaptain, worldoil.com: đăng lại nguyên văn tin Bloomberg mảng dầu khí/
#     hàng hải.
#   - Montel News: tin carbon/power market châu Âu (đúng mảng EUA/German Power
#     của desk).
#   - CarbonCredits.com, ESG Today: tin carbon market/ESG (không phải syndicate
#     nguyên văn nhưng cùng chủ đề, chất lượng tốt).
#   - The Edge Malaysia, theedgesingapore.com: đã thấy fetch tốt trong test
#     thực tế — tên khác "The Edge Markets" (không phải cùng masthead, giữ cả
#     2 để bao quát cả nhóm The Edge).
PREFERRED_SOURCES = [
    "Yahoo Finance",  # gộp chung "Yahoo" (thừa, cùng khớp substring, dễ dính nhầm Yahoo Sports/News)
    "MSN",
    "Reuters",
    "CNBC",
    "The Straits Times",  # đã verify ra bài geopolitics/policy thật (Pakistan-Afghanistan)
    "BNN Bloomberg",
    "Investing.com",
    "MarketWatch",
    "Business Standard",
    "Moneycontrol",
    "Livemint",
    "The Edge Markets",
    "The Edge Malaysia",
    "theedgesingapore.com",
    "Energy Connects",
    "gCaptain",
    "worldoil.com",
    "Montel News",
    "CarbonCredits.com",
    "ESG Today",
]

# Source bị chặn (paywall hoặc gốc)
BLOCKED_SOURCES = [
    "Bloomberg",
    "Bloomberg.com",
    "Financial Times",
    "The Wall Street Journal",
    "The New York Times",
    "The Washington Post",
    "The Economist",
    "Barron's",
]

# Delay giữa các lần search Google News để tránh rate limit
SEARCH_DELAY_SECONDS = 1.0

# Số candidate tối đa thử fetch cho mỗi tiêu đề (nhiều candidate hay bị chặn
# bot/paywall nên cần dự phòng nhiều hơn 1-2)
MAX_CANDIDATES_PER_TITLE = 5

# Marker nhận diện trang lỗi/chặn bot bị trafilatura extract nhầm thành nội
# dung bài (phát hiện thực tế: Access Denied của moneycontrol/Akamai,
# "Performing security verification" của moneyweb/Cloudflare — cả hai đều
# vượt MIN_TEXT_LENGTH=200 ở extraction.py nên cần lọc riêng ở đây).
_BLOCKED_PAGE_MARKERS = [
    "dotssplashui",
    "access denied",
    "performing security verification",
    "attention required",
    "checking your browser",
    "please verify you are a human",
    "captcha-delivery.com",
    "errors.edgesuite.net",
    "just a moment...",
    "cf-browser-verification",
    "enable javascript and cookies to continue",
    "pardon our interruption",
]

# Regex lấy 3 thuộc tính data-n-a-* trong HTML của link Google News — cần để
# gọi endpoint giải mã batchexecute (xem _decode_gnews_url).
_GNEWS_SIGNATURE_RE = re.compile(r'data-n-a-sg="([^"]*)"')
_GNEWS_TIMESTAMP_RE = re.compile(r'data-n-a-ts="([^"]*)"')
_GNEWS_ARTICLE_ID_RE = re.compile(r'data-n-a-id="([^"]*)"')


def _looks_like_blocked_page(html: str) -> bool:
    """True nếu HTML là trang lỗi/chặn bot (CAPTCHA, Access Denied, security
    check...) thay vì nội dung bài viết thật."""
    lowered = html[:5000].lower()
    return any(marker in lowered for marker in _BLOCKED_PAGE_MARKERS)


async def _decode_gnews_url(fetcher: PoliteFetcher, google_url: str) -> Optional[str]:
    """Giải mã link redirect của Google News (news.google.com/rss/articles/...)
    ra URL bài gốc — KHÔNG cần render JS/browser, chỉ 1 GET + 1 POST tới
    endpoint nội bộ (không chính thức) 'batchexecute' của Google.

    Nhanh hơn nhiều và ít bị site đích coi là bot hơn so với việc mở cả
    Playwright để theo JS redirect. Đây là API không chính thức (reverse-
    engineered từ hành vi thực tế của trang), Google có thể đổi định dạng bất
    kỳ lúc nào — nên hàm này trả None (không raise) khi parse thất bại, để nơi
    gọi luôn có fallback Playwright.
    """
    html = await fetcher.fetch(google_url)
    if not html:
        return None

    sig_match = _GNEWS_SIGNATURE_RE.search(html)
    ts_match = _GNEWS_TIMESTAMP_RE.search(html)
    id_match = _GNEWS_ARTICLE_ID_RE.search(html)
    if not (sig_match and ts_match and id_match):
        logger.debug("[Bloomberg] Không tìm thấy data-n-a-* trong trang Google News: %s", google_url)
        return None

    signature, timestamp, article_id = sig_match.group(1), ts_match.group(1), id_match.group(1)

    payload = {
        "f.req": json.dumps([[[
            "Fbv4je",
            json.dumps([
                "garturlreq",
                [
                    [
                        "en-US", "US",
                        ["FINANCE_TOP_INDICES", "GENESIS_PUBLISHER_SECTION", "WEB_TEST_1_0_0"],
                        None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0,
                    ],
                    "en-US", "US", True, [2, 4, 8], 1, True, None, None, None, None, None, None, None,
                ],
                article_id, timestamp, signature,
            ]),
        ]]])
    }

    resp_text = await fetcher.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=payload,
    )
    if not resp_text:
        return None

    try:
        # Response có 1 dòng "junk" độ dài trước phần JSON thật, ngăn cách bởi \n\n
        json_line = resp_text.split("\n\n", 1)[1]
        outer = json.loads(json_line)
        inner = json.loads(outer[0][2])
        real_url = inner[1]
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        logger.debug("[Bloomberg] Không parse được response decode GNews cho %s", google_url)
        return None

    if isinstance(real_url, str) and real_url.startswith("http"):
        return real_url
    return None


async def _fetch_real_article(
    fetcher: PoliteFetcher,
    playwright_fetcher,
    candidate_url: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve 1 candidate link Google News ra bài gốc + fetch HTML.

    Thứ tự ưu tiên:
      1. Decode link ra URL thật (_decode_gnews_url) rồi fetch thẳng bằng
         PoliteFetcher — nhanh, không cần browser.
      2. Nếu (1) fail hoặc trang đích vẫn bị chặn bot: dùng Playwright render
         (link đã decode nếu có, nếu không thì chính link Google — Playwright
         tự theo JS redirect) để lấy HTML + URL cuối cùng thật.

    Trả về (html, real_url) — real_url LUÔN là URL bài gốc, không phải link
    Google, để lưu đúng vào DB. (None, None) nếu mọi cách đều fail.
    """
    real_url = await _decode_gnews_url(fetcher, candidate_url)

    target_url = real_url or candidate_url
    html = await fetcher.fetch(target_url)
    if html and not _looks_like_blocked_page(html):
        return html, target_url

    if playwright_fetcher is None:
        return None, None

    pw_html, pw_final_url = await playwright_fetcher.fetch_with_url(target_url)
    if pw_html and not _looks_like_blocked_page(pw_html):
        return pw_html, pw_final_url or real_url or candidate_url
    return None, None


def _parse_rss_date(entry: dict) -> Optional[datetime]:
    """Parse pubDate từ feedparser entry, trả về datetime UTC hoặc None."""
    date_str = entry.get("published") or entry.get("updated")
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        logger.warning("[Bloomberg] Không parse được pubDate: %.60s", date_str)
        return None


def _is_source_usable(source_name: str) -> bool:
    """Kiểm tra source không thuộc danh sách bị chặn."""
    if not source_name:
        return False
    source_lower = source_name.lower()
    return not any(blocked.lower() in source_lower for blocked in BLOCKED_SOURCES)


def _is_preferred_source(source_name: str) -> bool:
    """Kiểm tra source có thuộc danh sách ưu tiên không."""
    if not source_name:
        return False
    source_lower = source_name.lower()
    return any(preferred.lower() in source_lower for preferred in PREFERRED_SOURCES)


def _rank_gnews_results(entries: list) -> List[str]:
    """Sắp xếp kết quả Google News theo độ ưu tiên, trả về danh sách URL (redirect links).

    Check preferred TRƯỚC blocked: 1 source đã được vetted và đưa vào
    PREFERRED_SOURCES (vd "BNN Bloomberg") được tin tưởng hơn heuristic
    substring-block chung — nếu không, "BNN Bloomberg" sẽ luôn bị loại vì
    chứa chữ "bloomberg" (khớp BLOCKED_SOURCES) dù đã lọt vào preferred list.
    """
    preferred_urls = []
    other_urls = []

    for entry in entries:
        url = entry.get("link", "")
        source_name = entry.get("source", {}).get("title", "")
        if not url:
            continue

        if _is_preferred_source(source_name):
            preferred_urls.append(url)
        elif _is_source_usable(source_name):
            other_urls.append(url)

    return preferred_urls + other_urls


async def _search_gnews(fetcher: PoliteFetcher, title: str) -> list:
    """Search bài viết trên Google News RSS."""
    # Tìm kiếm chính xác (dấu ngoặc kép) để có kết quả tốt nhất cho syndicated content
    query = urllib.parse.quote(f'"{title}"')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    
    try:
        xml = await fetcher.fetch(url)
        if not xml:
            return []
        parsed = feedparser.parse(xml)
        return parsed.entries
    except Exception as e:
        logger.warning("[Bloomberg] GNews search lỗi cho tiêu đề '%.60s': %s", title, e)
        return []


async def crawl_bloomberg_rss(
    fetcher: PoliteFetcher,
    playwright_fetcher,
    source: SourceConfig,
    seen_urls: Set[str],
) -> List[CrawledItem]:
    """Crawl Bloomberg qua RSS feeds → Google News RSS search → fetch trang trung gian."""
    if not source.bloomberg_feeds:
        logger.warning("[Bloomberg] Nguồn %s không có bloomberg_feeds, bỏ qua.", source.name)
        return []

    limit = source.max_articles or 10
    per_feed_entries: List[list] = []

    # Bước 1: Fetch và parse tất cả RSS feeds của Bloomberg
    for feed_url in source.bloomberg_feeds:
        logger.info("[Bloomberg] Đang fetch RSS: %s", feed_url)
        raw_xml = await fetcher.fetch(feed_url)
        if raw_xml is None:
            logger.warning("[Bloomberg] Không fetch được RSS: %s", feed_url)
            continue

        parsed_feed = feedparser.parse(raw_xml)
        feed_title = parsed_feed.feed.get("title", feed_url)
        logger.info("[Bloomberg] RSS '%s' có %d entries", feed_title, len(parsed_feed.entries))

        feed_entries = []
        for entry in parsed_feed.entries:
            title = entry.get("title", "").strip()
            bloomberg_url = entry.get("link", "")
            if not title:
                continue
            if bloomberg_url and bloomberg_url in seen_urls:
                continue
            feed_entries.append(entry)
        per_feed_entries.append(feed_entries)

    if not any(per_feed_entries):
        logger.info("[Bloomberg] Không có entry mới nào từ %d feeds.", len(source.bloomberg_feeds))
        return []

    # Trộn round-robin giữa các feed TRƯỚC khi cắt theo limit — nếu không, feed
    # đầu tiên (vd "markets", luôn ~20 entries) sẽ chiếm hết quota và feed sau
    # (vd "politics") không bao giờ được xử lý dù max_articles < tổng entries.
    all_entries = []
    for entries_tuple in zip_longest(*per_feed_entries):
        for entry in entries_tuple:
            if entry is not None:
                all_entries.append(entry)

    all_entries = all_entries[:limit]
    logger.info("[Bloomberg] Sẽ search %d tiêu đề trên Google News...", len(all_entries))

    items: List[CrawledItem] = []
    for i, entry in enumerate(all_entries):
        title = entry.get("title", "").strip()
        bloomberg_url = entry.get("link", "")
        rss_published_at = _parse_rss_date(entry)

        if i > 0:
            await asyncio.sleep(SEARCH_DELAY_SECONDS + random.uniform(0, 0.5))

        logger.info("[Bloomberg] [%d/%d] Đang search: '%.70s'", i + 1, len(all_entries), title)
        gnews_entries = await _search_gnews(fetcher, title)

        if not gnews_entries:
            # Fallback search không có dấu ngoặc kép nếu search chính xác fail
            logger.debug("[Bloomberg] Exact match fail, thử search rộng...")
            query_broad = urllib.parse.quote(title)
            url_broad = f"https://news.google.com/rss/search?q={query_broad}&hl=en-US&gl=US&ceid=US:en"
            xml = await fetcher.fetch(url_broad)
            if xml:
                gnews_entries = feedparser.parse(xml).entries
                
        if not gnews_entries:
            logger.info("[Bloomberg] Không tìm thấy bài syndicated cho: '%.70s'", title)
            continue

        # Sắp xếp URL theo độ ưu tiên (ưa chuộng Yahoo, MSN...)
        ranked_urls = _rank_gnews_results(gnews_entries)
        if not ranked_urls:
            logger.info(
                "[Bloomberg] Không có domain phù hợp trong %d kết quả cho: '%.60s'",
                len(gnews_entries), title,
            )
            continue

        # Thử từng candidate: decode link Google News ra URL bài gốc + fetch,
        # fallback Playwright nếu decode/fetch thẳng bị chặn (xem _fetch_real_article).
        html = None
        syndicated_url = None
        for candidate_url in ranked_urls[:MAX_CANDIDATES_PER_TITLE]:
            if candidate_url in seen_urls:
                continue
            logger.info("[Bloomberg] Đang thử fetch từ: %s", candidate_url)
            html, real_url = await _fetch_real_article(fetcher, playwright_fetcher, candidate_url)
            if html is not None and real_url is not None:
                syndicated_url = real_url
                break
            logger.debug("[Bloomberg] Fetch thất bại hoặc bị chặn bot, thử URL tiếp theo...")
            html = None

        if html is None or syndicated_url is None:
            logger.info("[Bloomberg] Không fetch được bài nào cho: '%.60s'", title)
            continue

        items.append(
            CrawledItem(
                url=syndicated_url,
                source_domain=source.domain,  # Nguồn gốc vẫn là Bloomberg
                tier=source.tier,
                title=title,
                raw_html=html,
                discovered_at=datetime.now(timezone.utc),
                region=source.region,
                rss_published_at=rss_published_at,
            )
        )
        seen_urls.add(syndicated_url)
        if bloomberg_url:
            seen_urls.add(bloomberg_url)

    logger.info(
        "[Bloomberg] %-30s -> %d bài mới (từ %d entries RSS)",
        source.domain, len(items), len(all_entries),
    )
    return items
