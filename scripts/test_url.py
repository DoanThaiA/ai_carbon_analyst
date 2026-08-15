"""
CLI test pipeline crawl -> extract -> dedupe -> classify -> store cho ĐÚNG 1
URL, in kết quả từng bước. Mặc định dry-run (không ghi DB) — dùng --store để
ghi thật. Chạy lần lượt từng link bằng script này trước khi tin tưởng
scripts/run_daily_crawl.py chạy cả 47 nguồn.

Usage:
    python -m scripts.test_url <url> --domain iea.org --tier A
    python -m scripts.test_url <url> --domain iea.org --tier A --store
"""
import argparse
import asyncio
import logging
from datetime import datetime, timezone

from carbon_analyst import storage
from carbon_analyst.classification import AnthropicClassifier
from carbon_analyst.config import Settings
from carbon_analyst.dedupe import Sha256Fingerprinter
from carbon_analyst.extraction import extract_article
from carbon_analyst.fetcher import PoliteFetcher
from carbon_analyst.models import CrawledItem

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--domain", required=True, help="source_domain, ví dụ: iea.org")
    parser.add_argument("--tier", default="B", choices=["A", "B", "C"])
    parser.add_argument("--store", action="store_true", help="Ghi vào DB (mặc định chỉ dry-run)")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = Settings.from_env()

    fetcher = PoliteFetcher()
    pool = await storage.create_pool(settings.database_url)
    await storage.ensure_schema(pool)
    classifier = AnthropicClassifier(
        api_key=settings.anthropic_api_key,
        model=settings.classifier_model,
        concurrency=settings.classify_concurrency,
    )
    fingerprinter = Sha256Fingerprinter()

    try:
        print(f"\n[1/5] Fetching {args.url} ...")
        html = await fetcher.fetch(args.url)
        if html is None:
            print("  -> FAILED: fetch trả về None (403/404/timeout — xem log ở trên)")
            return
        print(f"  -> OK, {len(html)} ký tự HTML")

        print("[2/5] Extracting content (trafilatura) ...")
        item = CrawledItem(
            url=args.url, source_domain=args.domain, tier=args.tier,
            title=None, raw_html=html, discovered_at=datetime.now(timezone.utc),
        )
        article = extract_article(item)
        if article is None:
            print("  -> FAILED: không trích xuất được nội dung đủ dài (site có thể chặn bot)")
            return
        print(f"  -> title: {article.title!r}")
        print(f"  -> published_at: {article.published_at}")
        print(f"  -> text length: {len(article.text)} ký tự")
        print(f"  -> preview: {article.text[:300]!r}...")

        print("[3/5] Fingerprinting (SHA-256) ...")
        content_hash = fingerprinter.fingerprint(article.text)
        print(f"  -> content_hash: {content_hash}")

        print("[4/5] Checking duplicate trong DB ...")
        is_dup = await storage.exists(pool, url=article.url, content_hash=content_hash)
        if is_dup:
            print("  -> DUPLICATE: bài (hoặc nội dung giống hệt) đã có trong DB — dừng, không gọi LLM.")
            return
        print("  -> chưa có trong DB, tiếp tục")

        print("[5/5] Classifying (Claude API) ...")
        classification = await classifier.classify(article.title, article.text)
        print(f"  -> category: {classification.category.value}")
        print(f"  -> confidence: {classification.confidence:.2f}")

        if not args.store:
            print("\n(dry-run — không ghi DB. Chạy lại với --store để lưu thật.)")
            return

        news_id = await storage.insert_news(
            pool,
            url=article.url,
            source_domain=article.source_domain,
            tier=article.tier,
            title=article.title,
            content=article.text,
            content_hash=content_hash,
            published_at=article.published_at,
            category=classification.category,
            category_confidence=classification.confidence,
        )
        if news_id is None:
            print("\n-> DUPLICATE khi insert (race condition) — không có id mới.")
        else:
            print(f"\n-> STORED — news.id = {news_id}")
    finally:
        await fetcher.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
