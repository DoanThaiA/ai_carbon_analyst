
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services import storage
from services.chunking import chunk_text
from services.hot_news_broadcast import notify_hot_news
from crawl_news.classification import Classifier, ClassificationError
from crawl_news.crawler import crawl_source

from crawl_news.dedupe import Fingerprinter
from services.embedding import Embedder
from crawl_news.extraction import extract_article
from crawl_news.fetcher import PoliteFetcher
from schemas.crawl_models import CrawledItem, ExtractedArticle, PipelineResult, SourceConfig, Tier

if TYPE_CHECKING:
    from crawl_news.playwright_fetcher import PlaywrightFetcher

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    fetcher: PoliteFetcher
    classifier: Classifier
    fingerprinter: Fingerprinter
    embedder: Embedder
    session_factory: async_sessionmaker[AsyncSession]
    playwright_fetcher: Optional["PlaywrightFetcher"] = None  # None → không dùng Playwright
    seen_hashes: Optional[set] = None  # In-memory deduplication


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
    """Crawl 1 nguồn, lọc bài trong 24h qua, rồi chạy pipeline đầy đủ.

    Quy tắc lọc ngày:
      - Bài có published_at và cũ hơn 24h tính từ thời điểm crawl → bỏ qua (skipped_old).
      - Bài KHÔNG có published_at (None) → GIỮ LẠI, xử lý bình thường.
        Nếu bài đó bị trùng lặp, lần crawl sau sẽ bị loại tự động qua dedup hash.
    today_only=False: bỏ qua filter 24h (dùng khi backfill / test).
    limit: cắt bớt số bài sau filter — dùng khi test để tiết kiệm gọi LLM.
    """
    seen_urls = seen_urls if seen_urls is not None else set()
    items = await crawl_source(ctx.fetcher, source, seen_urls, ctx.playwright_fetcher)

    # Bước extract trước để có published_at phục vụ date filter
    results: List[PipelineResult] = []
    extracted = []
    crawl_time = datetime.now(timezone.utc)
    # Lọc theo ngày: chỉ giữ bài đăng từ ngày hôm qua trở đi
    # (crawl_date - 1). Bài cũ hơn → loại.
    cutoff_date = (crawl_time - timedelta(days=1)).date()

    for item in items:
        article = extract_article(item)
        if article is None:
            results.append(PipelineResult(url=item.url, status="extraction_failed"))
        else:
            # Lọc ngày: chỉ áp dụng khi bài có published_at VÀ today_only=True
            if today_only and article.published_at is not None:
                pub = article.published_at
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                pub_date = pub.date()
                if pub_date < cutoff_date:
                    logger.debug(
                        "[DATE-FILTER] Bỏ qua bài cũ hơn ngày hôm qua: %s (published=%s, cutoff=%s)",
                        article.url, pub_date, cutoff_date,
                    )
                    results.append(PipelineResult(url=article.url, status="skipped_old"))
                    continue
            extracted.append(article)

    if not extracted:
        if len(items) == 0 and not results:
            reason = "tất cả link ứng viên đã crawl trước đó hoặc fetch lỗi"
        else:
            reason = "không có bài nào trong 24h qua hoặc không extract được nội dung"
        logger.info("[SKIP] %s: %s, bỏ qua.", source.domain, reason)
        return results

    if limit is not None:
        extracted = extracted[:limit]

    # OPT-2: classify song song — Semaphore(concurrency) trong Classifier giới hạn tốc độ
    tasks = [_dedupe_classify_store(ctx, article) for article in extracted]
    article_results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in article_results:
        if isinstance(res, Exception):
            logger.error("[PIPELINE-ERROR] Exception không mong đợi: %s", res)
        else:
            results.append(res)

    return results


async def _dedupe_classify_store(ctx: PipelineContext, article: ExtractedArticle) -> PipelineResult:
    """Dedupe → Classify → Insert article + chunks trong 1 atomic transaction.

    BUG-2 fix:
    - Gọi embed() TRƯỚC khi mở DB transaction (tránh giữ connection trong lúc call API)
    - Dùng session.begin() để bọc insert_article_no_commit + insert_chunks_no_commit
      → nếu bất kỳ bước nào lỗi, toàn bộ rollback (article không tồn tại mà không có chunks)
    - IntegrityError (content_hash duplicate) được bắt ngoài transaction → trả về "duplicate"
    """
    content_hash = ctx.fingerprinter.fingerprint(article.text)

    # Bước 1: Quick check trùng lặp (in-memory, O(1))
    if ctx.seen_hashes is not None and content_hash in ctx.seen_hashes:
        return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)
    
    # Fallback kiểm tra DB nếu chưa init seen_hashes
    if ctx.seen_hashes is None:
        async with ctx.session_factory() as session:
            is_duplicate = await storage.exists(session, url=article.url, content_hash=content_hash)
        if is_duplicate:
            return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)

    # Bước 2: Classify (external API call — ngoài transaction)
    try:
        logger.debug("[CLASSIFY] Đang gọi LLM phân loại: %s", article.url)
        classification = await ctx.classifier.classify(article.title, article.text)
        logger.debug("[CLASSIFY-OK] %s -> %s", article.url, [t.value for t in classification.topics])
    except ClassificationError as e:
        logger.warning("[CLASSIFY-FAIL] %s: %s", article.url, e)
        return PipelineResult(
            url=article.url, status="classification_failed", content_hash=content_hash, detail=str(e),
        )

    # Hot news bypass: 1 bài có thể không khớp topic thị trường nào (is_relevant=false
    # hoặc topics=[]) nhưng vẫn khớp tiêu chí HOT NEWS (vd: 1 nước lớn tuyên bố rút
    # khỏi decarbonization) — vẫn phải lưu lại để lên chuông thông báo, không skip.
    if not classification.is_hot_news and (not classification.is_relevant or not classification.topics):
        status = "irrelevant" if not classification.is_relevant else "classification_failed"
        detail = "Bài không liên quan energy/carbon" if not classification.is_relevant else "No topic found"
        logger.info("[SKIP] %s bỏ qua: %s", article.url, detail)
        return PipelineResult(
            url=article.url, status=status, content_hash=content_hash, detail=detail
        )


    # Bước 3: Embed chunks (external API call — ngoài transaction, với retry)
    chunks = chunk_text(article.text)
    embeddings: list = []
    if chunks:
        try:
            embeddings = await ctx.embedder.embed(chunks)
        except Exception as e:
            # Graceful degradation: vẫn lưu article nhưng không có chunk
            # Article được đánh dấu là stored, có thể re-embed sau
            logger.warning(
                "[EMBED-FAIL] %s: %s — lưu article không có chunk/embedding", article.url, e
            )
            chunks = []

    # Bước 4: Atomic insert — article + chunks trong 1 transaction
    try:
        async with ctx.session_factory() as session:
            async with session.begin():
                article_id = await storage.insert_article_no_commit(
                    session,
                    url=article.url,
                    source_domain=article.source_domain,
                    tier=article.tier,
                    title=article.title,
                    content=article.text,
                    content_hash=content_hash,
                    published_at=article.published_at,
                    date_confidence=article.date_confidence,
                    is_relevant=True,
                    topics=classification.topics,
                    is_hot_news=classification.is_hot_news,
                    hot_news_reason=classification.hot_news_reason,
                )
                if article_id is None:
                    # ON CONFLICT DO NOTHING — URL đã tồn tại
                    return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)

                if chunks and embeddings:
                    await storage.insert_chunks_no_commit(
                        session,
                        source_type="article",
                        source_id=article_id,
                        chunks=chunks,
                        embeddings=embeddings,
                    )

                if classification.is_hot_news:
                    # Bắn NOTIFY TRONG transaction — chỉ thực sự gửi đi nếu commit
                    # thành công ở dưới, rollback thì tự huỷ theo (xem hot_news_broadcast.py).
                    await notify_hot_news(session, article_id)
            # session.begin() commit ở đây — atomic

    except IntegrityError:
        # content_hash unique constraint: URL khác nhưng nội dung giống
        logger.info("[DUP] content_hash đã tồn tại (url khác), bỏ qua: %s", article.url)
        return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)

    # Cập nhật in-memory cache để các bài tiếp theo không bị trùng
    if ctx.seen_hashes is not None:
        ctx.seen_hashes.add(content_hash)

    logger.info("[STORED] Đã lưu bài %s -> %s", article.url, [t.value for t in classification.topics])
    if classification.is_hot_news:
        logger.warning("[HOT-NEWS] %s — %s", article.url, classification.hot_news_reason)

    return PipelineResult(
        url=article.url,
        status="stored",
        article_id=article_id,
        topics=classification.topics,
        confidence=classification.confidence,
        content_hash=content_hash,
        is_hot_news=classification.is_hot_news,
    )
