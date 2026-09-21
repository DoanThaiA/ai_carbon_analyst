"""
Entry point chạy crawl + xử lý tin tức hằng ngày. Thay thế run_crawl.py cũ
(chỉ crawl HTML thô) bằng luồng đầy đủ: crawl -> extract -> dedupe ->
classify -> store, dùng pipeline.crawl_pipeline.

CHỈ crawl tin tức — lấy giá thị trường (Barchart) là job riêng, xem
scheduler.py::daily_prices_job (BarchartPriceCrawler đọc spec từ DB
price_crawl_sources, không còn dùng instruments.yaml).

Production thật chạy qua scheduler.py (APScheduler, gọi main.py::main()),
không phải script này — script này để test 1 nguồn/limit trước khi tin tưởng
scheduler.py chạy cả sources.yaml.

Chạy độc lập để test: python -m scripts.run_daily_crawl

Usage:
    python -m scripts.run_daily_crawl
    python -m scripts.run_daily_crawl --source-domain eia.gov --limit 5   # test 1 nguồn trước
"""
import argparse
import asyncio
import logging
from typing import List, Optional

import yaml

from crawl_news.classification import AnthropicClassifier
from core.config import Settings
from crawl_news.dedupe import Sha256Fingerprinter
from services.embedding import CohereEmbedder
from crawl_news.fetcher import PoliteFetcher
from crawl_news.playwright_fetcher import PlaywrightFetcher
from schemas.crawl_models import SourceConfig
from pipeline.crawl_pipeline import PipelineContext, process_source
from db.session import build_sessionmaker, create_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources-file", default="sources.yaml")
    parser.add_argument(
        "--source-domain", help="Chỉ crawl 1 nguồn — dùng để test trước khi bật toàn bộ nguồn trong sources.yaml"
    )
    parser.add_argument("--limit", type=int, help="Giới hạn số bài xử lý mỗi nguồn (test, tiết kiệm gọi LLM)")
    return parser.parse_args()


def load_sources(path: str, only_domain: Optional[str] = None) -> List[SourceConfig]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    sources = [
        SourceConfig(
            domain=item["domain"],
            name=item["name"],
            tier=item["tier"],
            category=item["category"],
            region=item.get("region", "international"),
            type=item.get("type", "html"),
            rss_url=item.get("rss_url"),
            listing_url=item.get("listing_url"),
            max_articles=item.get("max_articles"),
            use_playwright=item.get("use_playwright", False),
            bloomberg_feeds=item.get("bloomberg_feeds", []),
        )
        for item in raw["sources"]
    ]
    if only_domain:
        sources = [s for s in sources if s.domain == only_domain]
        if not sources:
            raise SystemExit(f"Không tìm thấy domain '{only_domain}' trong {path}")
    return sources


async def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    sources = load_sources(args.sources_file, args.source_domain)

    fetcher = PoliteFetcher()
    engine = create_engine(settings.database_url)
    # Chỉ khởi động Playwright (tốn tài nguyên) nếu có nguồn thực sự cần —
    # dùng cho listing page JS-heavy (use_playwright: true) và cho fallback
    # resolve link Google News trong bloomberg_crawler.py.
    needs_playwright = any(s.use_playwright for s in sources)
    playwright_fetcher = PlaywrightFetcher() if needs_playwright else None

    try:
        if playwright_fetcher:
            await playwright_fetcher.start()

        ctx = PipelineContext(
            fetcher=fetcher,
            classifier=AnthropicClassifier(
                api_key=settings.anthropic_api_key,
                model=settings.classifier_model,
                concurrency=settings.classify_concurrency,
            ),
            fingerprinter=Sha256Fingerprinter(),
            embedder=CohereEmbedder(),
            session_factory=build_sessionmaker(engine),
            playwright_fetcher=playwright_fetcher,
        )

        logger.info("=== Bắt đầu crawl %d nguồn ===", len(sources))
        seen_urls: set = set()
        all_results = []
        for source in sources:
            results = await process_source(ctx, source, seen_urls, limit=args.limit)
            all_results.extend(results)

        by_status: dict = {}
        for r in all_results:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        hot_news_count = sum(1 for r in all_results if r.is_hot_news)
        logger.info("=== Xử lý xong %d bài viết: %s (hot_news=%d) ===", len(all_results), by_status, hot_news_count)
    finally:
        if playwright_fetcher:
            await playwright_fetcher.stop()
        await fetcher.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
