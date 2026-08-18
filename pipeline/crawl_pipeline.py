
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services import storage
from services.chunking import chunk_text
from crawl_services.classification import Classifier, ClassificationError
from crawl_services.crawler import crawl_source
from crawl_services.date_filter import filter_today
from crawl_services.dedupe import Fingerprinter
from services.embedding import Embedder
from crawl_services.extraction import extract_article
from crawl_services.fetcher import PoliteFetcher
from schemas.crawl_models import CrawledItem, ExtractedArticle, PipelineResult, SourceConfig, Tier

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    fetcher: PoliteFetcher
    classifier: Classifier
    fingerprinter: Fingerprinter
    embedder: Embedder
    session_factory: async_sessionmaker[AsyncSession]


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
        reason = "không extract được nội dung nào" if not today_only else "không có bài mới trong ngày"
        logger.info("[SKIP] %s: %s, bỏ qua.", source.domain, reason)
        return results

    if limit is not None:
        extracted = extracted[:limit]

    for article in extracted:
        results.append(await _dedupe_classify_store(ctx, article))

    return results


async def _dedupe_classify_store(ctx: PipelineContext, article: ExtractedArticle) -> PipelineResult:
    """Phần chung sau khi đã có content trích xuất: fingerprint -> dedupe
    check -> classify (LLM) -> lưu DB -> chunk + embedding cho RAG. Dedupe
    check luôn chạy trước classify để không tốn tiền gọi Claude cho bài đã
    lưu. Mở session riêng cho từng bước DB (thay vì giữ 1 session xuyên suốt
    cả lúc gọi Claude) để không giữ connection rảnh trong lúc chờ LLM."""
    content_hash = ctx.fingerprinter.fingerprint(article.text)

    async with ctx.session_factory() as session:
        is_duplicate = await storage.exists(session, url=article.url, content_hash=content_hash)
    if is_duplicate:
        return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)

    try:
        classification = await ctx.classifier.classify(article.title, article.text)
    except ClassificationError as e:
        logger.warning("[CLASSIFY-FAIL] %s: %s", article.url, e)
        return PipelineResult(
            url=article.url, status="classification_failed", content_hash=content_hash, detail=str(e),
        )

    async with ctx.session_factory() as session:
        article_id = await storage.insert_article(
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
            category=[classification.category],
        )
        if article_id is None:
            return PipelineResult(url=article.url, status="duplicate", content_hash=content_hash)

        await _chunk_and_embed(ctx, session, article_id, article.text)

    return PipelineResult(
        url=article.url,
        status="stored",
        article_id=article_id,
        category=classification.category,
        confidence=classification.confidence,
        content_hash=content_hash,
    )


async def _chunk_and_embed(ctx: PipelineContext, session: AsyncSession, article_id: int, text: str) -> None:
    """Chia bài viết thành chunk + sinh embedding, lưu vào bảng chunks phục
    vụ hybrid search cho RAG. Lỗi ở bước này KHÔNG làm mất bài viết đã lưu
    (article đã commit ở insert_article) — chỉ log, rollback riêng phần
    chunk và bỏ qua, vì chunks là dữ liệu bổ trợ (search), không phải dữ
    liệu gốc."""
    chunks = chunk_text(text)
    if not chunks:
        return
    try:
        embeddings = await ctx.embedder.embed(chunks)
        await storage.insert_chunks(
            session,
            source_type="article",
            source_id=article_id,
            chunks=chunks,
            embeddings=embeddings,
        )
    except Exception as e:  # noqa: BLE001 — lỗi embedding không được làm hỏng pipeline chính
        await session.rollback()
        logger.warning("[CHUNK-EMBED-FAIL] article_id=%s: %s", article_id, e)
