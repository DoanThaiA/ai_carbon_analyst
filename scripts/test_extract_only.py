import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')

from carbon_analyst.extraction import extract_article
from carbon_analyst.fetcher import PoliteFetcher
from carbon_analyst.models import CrawledItem

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test crawl raw content and extract metadata only.")
    parser.add_argument("url", help="URL bài viết cần test")
    parser.add_argument("--domain", default="example.com", help="Domain nguồn (vd: eia.gov)")
    return parser.parse_args()

async def main() -> None:
    args = parse_args()
    fetcher = PoliteFetcher()

    try:
        print(f"\n[1/2] Fetching {args.url} ...")
        html = await fetcher.fetch(args.url)
        if html is None:
            print("  -> FAILED: Không thể tải trang (bị chặn hoặc lỗi).")
            return
        print(f"  -> OK, Tải được {len(html)} ký tự HTML")

        print("\n[2/2] Extracting content & metadata ...")
        item = CrawledItem(
            url=args.url, 
            source_domain=args.domain, 
            tier="B", # Không quan trọng cho test này
            title=None, 
            raw_html=html, 
            discovered_at=datetime.now(timezone.utc),
        )
        article = extract_article(item)
        
        if article is None:
            print("  -> FAILED: Không trích xuất được nội dung (có thể cấu trúc trang đặc biệt hoặc bị chặn).")
            return
            
        print("  -> Trích xuất thành công!")
        print("-" * 50)
        print(f"📌 Title        : {article.title}")
        print(f"📅 Published At : {article.published_at}")
        print(f"📝 Text length  : {len(article.text)} ký tự")
        print("🔎 Nội dung chi tiết:\n")
        print(article.text)
        print("-" * 50)
        
    finally:
        await fetcher.close()

if __name__ == "__main__":
    asyncio.run(main())
