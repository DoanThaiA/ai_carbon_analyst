import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone

import trafilatura

sys.path.insert(0, ".")

from crawl_news.playwright_fetcher import PlaywrightFetcher
from crawl_news.extraction import _parse_date, _extract_date_from_html

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

async def test_url(url: str):
    fetcher = PlaywrightFetcher(headless=True)
    await fetcher.start()
    try:
        html = await fetcher.fetch(url)
        if not html:
            print("Failed to fetch.")
            return

        raw_json = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            favor_recall=True,
        )
        parsed = json.loads(raw_json)
        
        print("Trafilatura raw date:", parsed.get("date"))
        print("Trafilatura parsed date:", _parse_date(parsed.get("date")))
        
        fallback_date = _extract_date_from_html(html)
        print("Fallback HTML date:", fallback_date)
        
        # Look for dates in JSON-LD manually
        jsonld_match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
        if jsonld_match:
            print("JSON-LD datePublished:", jsonld_match.group(1))
            
        meta_match = re.search(
            r'<meta[^>]*(?:property|name)\s*=\s*"(?:article:published_time|datePublished|pubdate|DC\.date\.issued)"'
            r'[^>]*content\s*=\s*"([^"]+)"',
            html, re.IGNORECASE,
        )
        if meta_match:
            print("Meta published_time:", meta_match.group(1))
            
        time_match = re.search(r'<time[^>]*datetime="([^"]+)"', html, re.IGNORECASE)
        if time_match:
            print("Time datetime:", time_match.group(1))
        
    finally:
        await fetcher.stop()

if __name__ == "__main__":
    asyncio.run(test_url("https://www.argusmedia.com/en/news-and-insights/latest-market-news/2870428-uk-sets-out-schemes-eligible-for-cbam-price-relief"))
