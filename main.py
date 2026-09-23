
import asyncio
import logging
import sys
from typing import List, Optional

from sqlalchemy import select

from crawl_news.classification import build_classifier
from core.config import Settings
from crawl_news.dedupe import Sha256Fingerprinter
from services.embedding import CohereEmbedder
from crawl_news.fetcher import PoliteFetcher
from crawl_news.playwright_fetcher import PlaywrightFetcher
from db.models import NewsCrawlSource
from schemas.crawl_models import SourceConfig
from pipeline.crawl_pipeline import PipelineContext, PipelineResult, process_source
from db.session import build_sessionmaker, create_engine
from services import storage
from services.hot_news_email import send_pending_hot_news_digest

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


def _source_config_from_row(row: NewsCrawlSource) -> SourceConfig:
    """Map 1 dòng news_crawl_sources sang SourceConfig — thay cho đọc sources.yaml.
    Bỏ qua exclude_path_patterns khi None để SourceConfig dùng default_factory
    (danh sách mặc định) thay vì ghi đè bằng None."""
    kwargs = dict(
        domain=row.domain,
        name=row.name,
        tier=row.tier,
        category=row.category,
        region=row.region,
        type=row.source_type,
        rss_url=row.rss_url,
        listing_url=row.listing_url,
        group=row.group or [],
        confidence=row.confidence or "",
        note=row.note or "",
        link_pattern=row.link_pattern,
        max_articles=row.max_articles,
        use_playwright=row.use_playwright,
        bloomberg_feeds=row.bloomberg_feeds or [],
    )
    if row.exclude_path_patterns is not None:
        kwargs["exclude_path_patterns"] = row.exclude_path_patterns
    return SourceConfig(**kwargs)


async def load_sources_from_db(session_factory, *, noon_only: bool = False) -> List[SourceConfig]:
    """Đọc nguồn is_active=True từ bảng news_crawl_sources (thay cho sources.yaml
    — xem api/routers/admin_news_sources.py cho CRUD, scripts/migrate_sources.py
    cho backfill 1 lần từ sources.yaml cũ). noon_only=True -> chỉ lấy thêm nguồn
    is_noon_crawl=True (đợt crawl phụ 12:00, xem scheduler.py::noon_news_crawl_job)."""
    stmt = select(NewsCrawlSource).where(NewsCrawlSource.is_active == True)  # noqa: E712
    if noon_only:
        stmt = stmt.where(NewsCrawlSource.is_noon_crawl == True)  # noqa: E712
    async with session_factory() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_source_config_from_row(r) for r in rows]


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


async def main(domains: Optional[List[str]] = None, noon_only: bool = False) -> None:
    """domains=None -> crawl toàn bộ nguồn is_active=True trong news_crawl_sources
    (mặc định). Truyền list domain -> lọc thêm theo domain (test thủ công 1 nhóm
    nguồn). noon_only=True -> chỉ lấy nguồn is_noon_crawl=True (đợt crawl phụ
    12:00, xem scheduler.py::noon_news_crawl_job) — thay cho danh sách
    NOON_TIER_A_DOMAINS viết cứng trước đây."""
    settings = Settings.from_env()
    logger.info("[CONFIG] Backend: %s | Model: %s", settings.classifier_backend, settings.classifier_model)

    # 1. Kết nối database
    logger.info("[DB] Ket noi database...")
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    logger.info("[DB] Database san sang.")

    # 2. Đọc danh sách nguồn từ DB (news_crawl_sources)
    all_sources = await load_sources_from_db(session_factory, noon_only=noon_only)
    demo_sources = (
        [s for s in all_sources if s.domain in domains] if domains else all_sources
    )
    if domains and not demo_sources:
        logger.warning("[CONFIG] Không tìm thấy nguồn nào khớp domains=%s trong news_crawl_sources", domains)

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

    # 3. Khởi tạo các thành phần pipeline
    fetcher = PoliteFetcher()
    classifier = build_classifier(
        backend=settings.classifier_backend,
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

    # 6. Gửi 1 email digest gom toàn bộ hot news của đợt crawl này (tự bắt lỗi,
    # không làm hỏng job — xem services/hot_news_email.py)
    await send_pending_hot_news_digest(session_factory, settings)

    # 7. Dọn dẹp
    await fetcher.close()
    await engine.dispose()

    logger.info("\n%s", "=" * 60)
    logger.info("[DONE] Hoan thanh! Tong bai luu moi vao DB: %d", total_stored)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
