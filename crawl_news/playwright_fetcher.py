"""
PlaywrightFetcher — Fetch HTML sau khi JavaScript đã render xong trang.

Dùng cho các site JS-heavy (Next.js, SPA) không trả link bài viết trong
HTML tĩnh ban đầu. Playwright khởi động 1 Chromium instance dùng chung
cho toàn bộ crawl session (không tạo lại mỗi request).

Interface:
  - .fetch(url) -> str | None  (tương thích PoliteFetcher)
  - Async context manager (async with PlaywrightFetcher() as pf: ...)

Chiến lược wait_until:
  - 'networkidle'  : chờ network yên tĩnh 500ms — tốt cho SPA/Next.js
  - 'domcontentloaded': nhanh hơn, dùng khi 'networkidle' timeout

Stealth: dùng các user-agent thật và ẩn webdriver flag để tránh bị detect.
"""
import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

logger = logging.getLogger(__name__)

PLAYWRIGHT_MAX_CONCURRENT_PAGES = 2
PLAYWRIGHT_PAGE_TIMEOUT_MS = 60_000    # 60s — bài lẻ JS-heavy cần nhiều thời gian hơn
PLAYWRIGHT_NAVIGATION_TIMEOUT_MS = 60_000
PER_DOMAIN_DELAY_SECONDS = 3.0          # 3s delay giữa các request cùng domain để giảm bot detection
PLAYWRIGHT_JS_SETTLE_SECONDS = 5.0     # Chờ 5s sau domcontentloaded cho React/Next.js render xong


class PlaywrightFetcher:
    """
    Fetch HTML sau khi JS render xong. Dùng 1 browser instance chung.
    Gọi `await pf.start()` trước khi dùng, `await pf.stop()` khi xong.
    Hoặc dùng async context manager: `async with PlaywrightFetcher() as pf`.
    """

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        # Semaphore để giới hạn số page mở cùng lúc
        self._page_semaphore = asyncio.Semaphore(PLAYWRIGHT_MAX_CONCURRENT_PAGES)
        # Per-domain delay (giống PoliteFetcher)
        self._domain_locks: dict = defaultdict(asyncio.Lock)
        self._domain_last_request: dict = {}

    async def start(self) -> None:
        """Khởi động Playwright và mở browser."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            java_script_enabled=True,
            # Bỏ qua các request không cần thiết để tăng tốc
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        # Ẩn webdriver flag để bypass bot detection
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        logger.info("[Playwright] Browser khởi động xong (headless=%s)", self._headless)

    async def stop(self) -> None:
        """Đóng browser và giải phóng tài nguyên."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("[Playwright] Browser đã đóng.")

    async def __aenter__(self) -> "PlaywrightFetcher":
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.stop()

    def _domain_of(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc or url

    async def _respect_delay(self, domain: str) -> None:
        async with self._domain_locks[domain]:
            last = self._domain_last_request.get(domain)
            now = time.monotonic()
            if last is not None:
                elapsed = now - last
                if elapsed < PER_DOMAIN_DELAY_SECONDS:
                    await asyncio.sleep(PER_DOMAIN_DELAY_SECONDS - elapsed)
            self._domain_last_request[domain] = time.monotonic()

    async def fetch(self, url: str) -> Optional[str]:
        """
        Fetch 1 URL bằng Playwright, chờ JS render xong, trả về HTML.
        Trả về None nếu timeout hoặc lỗi.
        """
        if self._context is None:
            logger.error("[Playwright] Chưa gọi start(). Hãy dùng async with hoặc await pf.start().")
            return None

        domain = self._domain_of(url)
        async with self._page_semaphore:
            await self._respect_delay(domain)
            page: Optional[Page] = None
            try:
                page = await self._context.new_page()

                # Chặn resource không cần thiết để tăng tốc
                await page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in ("image", "media", "font", "stylesheet")
                    else route.continue_(),
                )

                page.set_default_navigation_timeout(PLAYWRIGHT_NAVIGATION_TIMEOUT_MS)
                page.set_default_timeout(PLAYWRIGHT_PAGE_TIMEOUT_MS)

                # Chiến lược: dùng domcontentloaded + sleep để tương thích tốt nhất.
                # - networkidle: quá nghiêm ngặt, timeout với các site có analytics/polling liên tục.
                # - load: cũng thường timeout với SPA/Next.js nặng.
                # - domcontentloaded + sleep(PLAYWRIGHT_JS_SETTLE_SECONDS): nhanh và ổn định.
                #   DOM có sẵn để JS framework (React/Next.js) render, rồi chờ thêm vài giây.
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    # Chờ JS framework render xong content (article links)
                    await asyncio.sleep(PLAYWRIGHT_JS_SETTLE_SECONDS)
                except PlaywrightTimeoutError:
                    logger.warning("[Playwright] Timeout ngay cả domcontentloaded: %s", url)
                    return None

                html = await page.content()
                logger.debug("[Playwright] Fetch OK (%d bytes): %s", len(html), url)
                return html

            except PlaywrightTimeoutError:
                logger.warning("[Playwright] Timeout: %s", url)
                return None
            except Exception as e:
                logger.warning("[Playwright] Lỗi fetch %s: %s", url, e)
                return None
            finally:
                if page and not page.is_closed():
                    await page.close()
