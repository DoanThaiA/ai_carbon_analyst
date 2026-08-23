
import asyncio
import logging
import sys
from pathlib import Path
from typing import List

import yaml

from crawl_news.classification import build_classifier
from core.config import Settings
from crawl_news.dedupe import Sha256Fingerprinter
from services.embedding import CohereEmbedder
from crawl_news.fetcher import PoliteFetcher
from crawl_news.playwright_fetcher import PlaywrightFetcher
from schemas.crawl_models import SourceConfig
from pipeline.crawl_pipeline import PipelineContext, PipelineResult, process_source
from db.session import build_sessionmaker, create_engine
from services import storage

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def load_sources(yaml_path: Path) -> List[SourceConfig]:
    """Đọc sources.yaml và chuyển đổi thành danh sách SourceConfig."""
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    sources = []
    for entry in data.get("sources", []):
        sources.append(
            SourceConfig(
                domain=entry["domain"],
                name=entry["name"],
                tier=entry["tier"],
                category=entry["category"],
                type=entry.get("type", "html"),
                rss_url=entry.get("rss_url"),
                listing_url=entry.get("listing_url"),
                exclude_path_patterns=entry.get(
                    "exclude_path_patterns",
                    ["/tag/", "/category/", "/author/", "/about",
                     "/contact", "/login", "/search", "/page/"],
                ),
                group=entry.get("group", []),
                confidence=entry.get("confidence", ""),
                note=entry.get("note", ""),
                link_pattern=entry.get("link_pattern"),
                max_articles=entry.get("max_articles"),
                use_playwright=entry.get("use_playwright", False),
            )
        )
    return sources


def _print_summary(source_name: str, results: List[PipelineResult]) -> None:
    """In tóm tắt kết quả xử lý cho một nguồn."""
    status_counts: dict[str, int] = {}
    for r in results:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    stored = status_counts.get("stored", 0)
    dupes = status_counts.get("duplicate", 0)
    failed_extract = status_counts.get("extraction_failed", 0)
    failed_classify = status_counts.get("classification_failed", 0)
    skipped_old = status_counts.get("skipped_old", 0)
    irrelevant = status_counts.get("irrelevant", 0)

    logger.info(
        "%-40s | ✅ lưu: %d | ♻️  trùng: %d | 📅 cũ: %d | 🚫 không liên quan: %d | ❌ extract: %d | ⚠️  classify: %d",
        source_name[:40],
        stored, dupes, skipped_old, irrelevant, failed_extract, failed_classify,
    )

    for r in results:
        if r.status == "stored":
            logger.info(
                "  → [ID:%d] [%s | %.0f%%] %s",
                r.article_id or 0,
                ",".join(t.value for t in r.topics) if r.topics else "?",
                (r.confidence or 0) * 100,
                r.url,
            )


async def main() -> None:
    settings = Settings.from_env()
    logger.info("[CONFIG] Backend: %s | Model: %s", settings.classifier_backend, settings.classifier_model)

    # 1. Đọc danh sách nguồn
    sources_path = Path(__file__).parent / "sources.yaml"
    all_sources = load_sources(sources_path)
    demo_sources = all_sources

    has_playwright_sources = any(s.use_playwright for s in demo_sources)
    logger.info("📰 Demo với %d nguồn (%d dùng Playwright):",
                len(demo_sources), sum(1 for s in demo_sources if s.use_playwright))
    for s in demo_sources:
        flag = " ⚠️  (URL có thể lỗi)" if s.confidence == "low" else ""
        pw_flag = " 🎭" if s.use_playwright else ""
        logger.info(
            "   - [Tier %s] %-40s | confidence: %s%s%s",
            s.tier, s.name, s.confidence or "?", flag, pw_flag,
        )

    # 2. Kết nối database
    logger.info("[DB] Ket noi database...")
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    logger.info("[DB] Database san sang.")

    # 3. Khởi tạo các thành phần pipeline
    fetcher = PoliteFetcher()
    classifier = build_classifier(
        backend=settings.classifier_backend,
        cohere_api_key=settings.cohere_api_key,
        anthropic_api_key=settings.anthropic_api_key,
        model=settings.classifier_model,
        concurrency=settings.classify_concurrency,
    )
    fingerprinter = Sha256Fingerprinter()

    # 4. seed seen_urls và seen_hashes từ DB
    logger.info("[SEEN-URLS] Đang load URL và Hash đã biết từ DB...")
    async with session_factory() as session:
        seen_urls: set = await storage.load_recent_urls(session, days=7)
        seen_hashes: set = await storage.load_recent_content_hashes(session, days=7)

    # 5. Crawl với Playwright (nếu có source yêu cầu)
    logger.info("\n%s", "=" * 60)
    logger.info("[START] Bat dau crawl %d nguon song song...", len(demo_sources))
    logger.info("=" * 60)

    async with PlaywrightFetcher() as pw_fetcher:
        ctx = PipelineContext(
            fetcher=fetcher,
            classifier=classifier,
            fingerprinter=fingerprinter,
            embedder=CohereEmbedder(),
            session_factory=session_factory,
            playwright_fetcher=pw_fetcher if has_playwright_sources else None,
            seen_hashes=seen_hashes,
        )

        all_results = []
        for source in demo_sources:
            logger.info(">>> Đang xử lý nguồn: %s (%s)", source.name, source.domain)
            try:
                res = await process_source(ctx, source, seen_urls=seen_urls, limit=None, today_only=True)
                all_results.append(res)
            except Exception as e:
                logger.error("[ERROR] Nguồn %s thất bại: %s", source.name, e)
                all_results.append(e)

    total_stored = 0
    for source, results in zip(demo_sources, all_results):
        if isinstance(results, Exception):
            logger.error("[ERROR] Nguồn %s thất bại: %s", source.name, results)
            continue
        _print_summary(source.name, results)
        total_stored += sum(1 for r in results if r.status == "stored")

    # 6. Dọn dẹp
    await fetcher.close()
    await engine.dispose()

    logger.info("\n%s", "=" * 60)
    logger.info("[DONE] Hoan thanh! Tong bai luu moi vao DB: %d", total_stored)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
