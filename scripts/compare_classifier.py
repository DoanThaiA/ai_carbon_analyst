"""
So sánh kết quả phân loại tin của 2 model (mặc định Haiku 4.5 cũ ↔ Haiku 5.5 mới) trên các bài
ĐÃ lưu trong DB — chạy trước khi tin tưởng đổi CLASSIFIER_MODEL. KHÔNG ghi gì vào DB.

Mỗi bài gọi API 2 lần (1 lần/model) → tốn phí thật: ~100 bài ≈ dưới $1 (chủ yếu phần Haiku 4.5).

Lưu ý: bài trong `articles` đều đã được model cũ coi là liên quan (bài không liên quan không được
lưu), nên tỉ lệ "is_relevant khớp" ở đây chỉ đo được việc model mới LOẠI NHẦM bài đáng giữ.

Usage:
    python -m scripts.compare_classifier                 # 50 bài mới nhất
    python -m scripts.compare_classifier --limit 100
    python -m scripts.compare_classifier --old claude-haiku-4-5 --new claude-haiku-5-5
"""
import argparse
import asyncio
import logging
from collections import Counter

from sqlalchemy import desc, select

from core.config import Settings
from crawl_news.classification import (
    DEFAULT_CLASSIFIER_MODEL,
    REFUSAL_FALLBACK_MODEL,
    AnthropicClassifier,
    ClassificationError,
)
from db.models import Article
from db.session import build_sessionmaker, create_engine

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--old", default=REFUSAL_FALLBACK_MODEL)
    parser.add_argument("--new", default=DEFAULT_CLASSIFIER_MODEL)
    return parser.parse_args()


async def _classify(clf: AnthropicClassifier, title: str, text: str):
    try:
        return await clf.classify(title, text)
    except ClassificationError as e:
        return e


async def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    async with session_factory() as session:
        rows = (await session.execute(
            select(Article.title, Article.content).order_by(desc(Article.crawled_at)).limit(args.limit)
        )).all()
    await engine.dispose()

    old = AnthropicClassifier(settings.anthropic_api_key, model=args.old, concurrency=settings.classify_concurrency)
    new = AnthropicClassifier(settings.anthropic_api_key, model=args.new, concurrency=settings.classify_concurrency)
    print(f"So sánh {len(rows)} bài: {args.old} (cũ) ↔ {args.new} (mới)\n")

    results = await asyncio.gather(*(
        asyncio.gather(_classify(old, r.title, r.content), _classify(new, r.title, r.content)) for r in rows
    ))

    stats = Counter()
    for r, (a, b) in zip(rows, results):
        if isinstance(a, Exception) or isinstance(b, Exception):
            stats["lỗi"] += 1
            print(f"[LỖI] {r.title[:70]}\n   cũ: {a if isinstance(a, Exception) else 'ok'} | mới: {b if isinstance(b, Exception) else 'ok'}")
            continue
        stats["tổng"] += 1
        same_rel = a.is_relevant == b.is_relevant
        same_hot = a.is_hot_news == b.is_hot_news
        ta, tb = {t.value for t in a.topics}, {t.value for t in b.topics}
        stats["is_relevant khớp"] += same_rel
        stats["is_hot_news khớp"] += same_hot
        stats["topic chính khớp"] += bool(a.topics and b.topics and a.topics[0] == b.topics[0])
        stats["topic giao nhau"] += bool(ta & tb)
        stats["topic giống hệt"] += ta == tb
        if not (same_rel and same_hot and ta & tb):
            print(f"[KHÁC] {r.title[:90]}")
            print(f"   cũ : relevant={a.is_relevant} hot={a.is_hot_news} topics={sorted(ta)}")
            print(f"   mới: relevant={b.is_relevant} hot={b.is_hot_news} topics={sorted(tb)}")

    total = stats["tổng"] or 1
    print("\n=== TỔNG KẾT ===")
    print(f"Bài so sánh được: {stats['tổng']} (lỗi: {stats['lỗi']})")
    for key in ("is_relevant khớp", "is_hot_news khớp", "topic chính khớp", "topic giao nhau", "topic giống hệt"):
        print(f"{key:<18}: {stats[key]}/{stats['tổng']} ({stats[key] / total:.0%})")


if __name__ == "__main__":
    asyncio.run(main())
