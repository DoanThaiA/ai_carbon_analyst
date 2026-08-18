"""
Lớp fetch HTTP dùng chung: giới hạn tốc độ theo domain, retry, timeout.
Dùng httpx async để crawl song song nhiều nguồn mà không làm quá tải 1 site
(và giảm rủi ro bị block IP khi crawl ~50 nguồn mỗi ngày).
"""
import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 20.0  
MAX_RETRIES = 2
PER_DOMAIN_CONCURRENCY = 2
PER_DOMAIN_DELAY_SECONDS = 1.0


class PoliteFetcher:
    """
    Fetcher tôn trọng từng domain: giới hạn số request đồng thời + delay
    giữa các request tới cùng 1 domain. Bỏ qua ngay (không retry) với lỗi
    403/404 theo đúng yêu cầu JD: "không sử dụng nguồn bị lỗi 403, 404".
    """

    def __init__(self):
        self._domain_semaphores: dict = defaultdict(
            lambda: asyncio.Semaphore(PER_DOMAIN_CONCURRENCY)
        )
        self._domain_last_request: dict = {}
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    async def close(self):
        await self._client.aclose()

    def _domain_of(self, url: str) -> str:
        return httpx.URL(url).host or url

    async def fetch(self, url: str) -> Optional[str]:
        """Fetch 1 URL, trả về HTML string hoặc None nếu lỗi/không đọc được."""
        domain = self._domain_of(url)
        semaphore = self._domain_semaphores[domain]

        async with semaphore:
            await self._respect_delay(domain)
            for attempt in range(1, MAX_RETRIES + 2):
                try:
                    resp = await self._client.get(url)
                    if resp.status_code == 200:
                        return resp.text
                    if resp.status_code in (403, 404):
                        logger.warning("[SKIP %s] nguồn không đọc được: %s", resp.status_code, url)
                        return None
                    logger.warning(
                        "[RETRY %d/%d] status=%s url=%s",
                        attempt, MAX_RETRIES + 1, resp.status_code, url,
                    )
                except httpx.RequestError as e:
                    logger.warning(
                        "[RETRY %d/%d] error=%s url=%s", attempt, MAX_RETRIES + 1, e, url
                    )
                await asyncio.sleep(1.5 * attempt)

            logger.error("[FAILED] %s sau %d lần thử", url, MAX_RETRIES + 1)
            return None

    async def _respect_delay(self, domain: str):
        last = self._domain_last_request.get(domain)
        now = time.monotonic()
        if last is not None:
            elapsed = now - last
            if elapsed < PER_DOMAIN_DELAY_SECONDS:
                await asyncio.sleep(PER_DOMAIN_DELAY_SECONDS - elapsed)
        self._domain_last_request[domain] = time.monotonic()
