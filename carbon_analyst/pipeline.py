"""
Orchestrator: crawl -> extract -> date filter -> dedupe (fingerprint) -> classify -> store.

Thứ tự bước được sắp xếp để tiết kiệm chi phí:
  1. date filter (loại bài cũ ngay, không tốn request DB hay LLM)
  2. dedupe check (rẻ, chỉ query DB) luôn chạy TRƯỚC classify
  3. classify (tốn tiền gọi LLM) — chỉ chạy cho bài mới, chưa có trong DB
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from carbon_analyst import storage
from carbon_analyst.classification import Classifier, ClassificationError
from carbon_analyst.crawler import crawl_source
from carbon_analyst.date_filter import filter_today
from carbon_analyst.dedupe import Fingerprinter
from carbon_analyst.extraction import extract_article
from carbon_analyst.fetcher import PoliteFetcher
from carbon_analyst.models import CrawledItem, ExtractedArticle, PipelineResult, SourceConfig, Tier

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    fetcher: PoliteFetcher
    classifier: Classifier
    fingerprinter: Fingerprinter
    pool: object  # asyncpg.Pool — kept loosely typed to avoid importing asyncpg here


async def process_url(
    ctx: PipelineContext, url: str, source_domain: str, tier: Tier,
) -> PipelineResult:
    """Fetch + chạy toàn bộ pipeline cho 1 URL. Dùng cho test_url.py."""
    html = await ctx.fetcher.fetch(url)
    if html is None:
        return PipelineResult(url=url, status="extraction_failed", detail="fetch thất bại")

    item = CrawledItem(
        url=url,
        source_domain=source_domain,
        tier=tier,
        title=None,
        raw_html=html,
        discovered_at=datetime.now(timezone.utc),
    )
    article = extract_article(item)
    if article is None:
        return PipelineResult(url=url, status="extraction_failed", detail="trafilatura không lấy được nội dung")

    return await _dedupe_classify_store(ctx, article)


async def process_source(
    ctx: PipelineContext,
    source: SourceConfig,
    seen_urls: Optional[set] = None,
    limit: Optional[int] = None,
    today_only: bool = True,
) -> List[PipelineResult]:
    """Crawl 1 nguồn, lọc bài hôm nay, rồi chạy pipeline đầy đủ.

    today_only=True (mặc định): chỉ xử lý bài đăng trong ngày crawl.
      - Bài có published_at khác hôm nay → status "skipped_old".
      - Bài không parse được ngày (published_at=None) → GIỮ LẠI an toàn.
    today_only=False: xử lý tất cả bài (dùng khi backfill / test).
    limit: cắt bớt số bài sau filter — dùng khi test để tiết kiệm gọi LLM.
    """
    seen_urls = seen_urls if seen_urls is not None else set()
    items = await crawl_source(ctx.fetcher, source, seen_urls)

    # Bước extract trước để có published_at phục vụ date filter
    results: List[PipelineResult] = []
    extracted = []
    for item in items:
        article = extract_article(item)
        if article is None:
            results.append(PipelineResult(url=item.url, status="extraction_failed"))
        else:
            extracted.append(article)

    # Date filter — chỉ giữ bài hôm nay
    if today_only and extracted:
        kept, skipped = filter_today(extracted)
        if skipped:
            logger.info(
                "[DATE-FILTER] %s: bỏ %d bài cũ, giữ %d bài hôm nay",
                source.domain, len(skipped), len(kept),
            )
        for art in skipped:
            results.append(PipelineResult(url=art.url, status="skipped_old"))
        extracted = kept

    if not extracted:
        logger.info("[SKIP] %s: không có bài mới trong ngày, bỏ qua.", source.domain)
        return results

    if limit is not None:
        extracted = extracted[:limit]

    for article in extracted:
        results.append(await _dedupe_classify_store(ctx, article))

    return results


async def _dedupe_classify_store(ctx: PipelineContext, article: ExtractedArticle) -> PipelineResult:
    """Phần chung sau khi đã có content trích xuất: fingerprint -> dedupe
    check -> classify (LLM) -> lưu DB. Dedupe check luôn chạy trước classify
    để không tốn tiền gọi Claude cho bài đã lưu."""
    content_hash = ctx.fingerprinter.fingerprint(article.text)

    if await storage.exists(ctx.pool, url=article.url, content_hash=content_hash):
        return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)

    try:
        classification = await ctx.classifier.classify(article.title, article.text)
    except ClassificationError as e:
        logger.warning("[CLASSIFY-FAIL] %s: %s", article.url, e)
        return PipelineResult(
            url=article.url, status="classification_failed", content_hash=content_hash, detail=str(e),
        )

    news_id = await storage.insert_news(
        ctx.pool,
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
        return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)

    return PipelineResult(
        url=article.url,
        status="stored",
        news_id=news_id,
        category=classification.category,
        confidence=classification.confidence,
        content_hash=content_hash,
    )
