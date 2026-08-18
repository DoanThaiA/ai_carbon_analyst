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

from services import storage
from services.chunking import chunk_text
from crawl_services.classification import AnthropicClassifier
from core.config import Settings
from crawl_services.dedupe import Sha256Fingerprinter
from services.embedding import CohereEmbedder
from crawl_services.extraction import extract_article
from crawl_services.fetcher import PoliteFetcher
from schemas.crawl_models import CrawledItem
from db.session import build_sessionmaker, create_engine

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
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    classifier = AnthropicClassifier(
        api_key=settings.anthropic_api_key,
        model=settings.classifier_model,
        concurrency=settings.classify_concurrency,
    )
    fingerprinter = Sha256Fingerprinter()
    embedder = CohereEmbedder()

    try:
        print(f"\n[1/6] Fetching {args.url} ...")
        html = await fetcher.fetch(args.url)
        if html is None:
            print("  -> FAILED: fetch trả về None (403/404/timeout — xem log ở trên)")
            return
        print(f"  -> OK, {len(html)} ký tự HTML")

        print("[2/6] Extracting content (trafilatura) ...")
        item = CrawledItem(
            url=args.url, source_domain=args.domain, tier=args.tier,
            title=None, raw_html=html, discovered_at=datetime.now(timezone.utc),
        )
        article = extract_article(item)
        if article is None:
            print("  -> FAILED: không trích xuất được nội dung đủ dài (site có thể chặn bot)")
            return
        print(f"  -> title: {article.title!r}")
        print(f"  -> published_at: {article.published_at} (date_confidence={article.date_confidence})")
        print(f"  -> text length: {len(article.text)} ký tự")
        print(f"  -> preview: {article.text[:300]!r}...")

        print("[3/6] Fingerprinting (SHA-256) ...")
        content_hash = fingerprinter.fingerprint(article.text)
        print(f"  -> content_hash: {content_hash}")

        print("[4/6] Checking duplicate trong DB ...")
        async with session_factory() as session:
            is_dup = await storage.exists(session, url=article.url, content_hash=content_hash)
        if is_dup:
            print("  -> DUPLICATE: bài (hoặc nội dung giống hệt) đã có trong DB — dừng, không gọi LLM.")
            return
        print("  -> chưa có trong DB, tiếp tục")

        print("[5/6] Classifying (Claude API) ...")
        classification = await classifier.classify(article.title, article.text)
        print(f"  -> category: {classification.category.value}")
        print(f"  -> confidence: {classification.confidence:.2f}")

        print("[6/6] Chunking + embedding (cho hybrid search) ...")
        chunks = chunk_text(article.text)
        print(f"  -> {len(chunks)} chunk")

        if not args.store:
            print("\n(dry-run — không ghi DB. Chạy lại với --store để lưu thật.)")
            return

        async with session_factory() as session:
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
                print("\n-> DUPLICATE khi insert (race condition) — không có id mới.")
                return

            print(f"\n-> STORED — articles.id = {article_id}")
            embeddings = await embedder.embed(chunks)
            await storage.insert_chunks(
                session,
                source_type="article",
                source_id=article_id,
                chunks=chunks,
                embeddings=embeddings,
            )
        print(f"-> STORED — {len(chunks)} chunk vào bảng chunks")
    finally:
        await fetcher.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
