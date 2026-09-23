"""
Script dùng 1 lần: đọc sources.yaml và insert vào bảng news_crawl_sources, để
main.py/scheduler.py có thể chuyển từ đọc sources.yaml sang đọc DB (xem
main.py::load_sources_from_db). Idempotent — chạy lại nhiều lần chỉ insert
thêm nguồn CHƯA có trong DB (khớp theo `name`, cột unique), không update
nguồn đã tồn tại (sửa nguồn sau khi migrate thì dùng API admin CRUD, không
chạy lại script này).

is_noon_crawl=True được set cho các domain thuộc NOON_TIER_A_DOMAINS cũ (bản
sao của scheduler.py::NOON_TIER_A_DOMAINS tại thời điểm viết script này) —
sau khi chạy xong và xác nhận scheduler.py đã đổi sang query is_noon_crawl từ
DB, danh sách hardcode trong scheduler.py có thể xoá.

Dùng:
    python -m scripts.migrate_sources
    python -m scripts.migrate_sources --sources-file sources.yaml
"""
import argparse
import asyncio

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import Settings
from db.models import NewsCrawlSource
from db.session import build_sessionmaker, create_engine

# Bản sao NOON_TIER_A_DOMAINS từ scheduler.py tại thời điểm viết migration này.
NOON_TIER_A_DOMAINS = [
    "eia.gov",
    "ieta.org",
    "opec.org",
    "nasdaq.com",
    "commission.europa.eu",
    "esma.europa.eu",
    "ice.com",
    "eex.com",
    "climate.ec.europa.eu",
]


def _load_yaml_sources(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("sources", [])


async def main(sources_file: str) -> None:
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)

    entries = _load_yaml_sources(sources_file)
    print(f"Đọc được {len(entries)} nguồn từ {sources_file}.")

    async with session_factory() as session:
        existing_names = set(
            (await session.execute(select(NewsCrawlSource.name))).scalars().all()
        )
        new_entries = [e for e in entries if e["name"] not in existing_names]

        if not new_entries:
            print("Không có nguồn mới — DB đã có đủ theo `name`, không insert gì thêm.")
            await engine.dispose()
            return

        rows = [
            {
                "domain": e["domain"],
                "name": e["name"],
                "category": e["category"],
                "tier": e["tier"],
                "region": e.get("region", "international"),
                "source_type": e.get("type", "html"),
                "listing_url": e.get("listing_url"),
                "rss_url": e.get("rss_url"),
                "link_pattern": e.get("link_pattern"),
                "exclude_path_patterns": e.get("exclude_path_patterns"),
                "group": e.get("group", []),
                "bloomberg_feeds": e.get("bloomberg_feeds", []),
                "confidence": e.get("confidence") or None,
                "note": e.get("note") or None,
                "max_articles": e.get("max_articles"),
                "use_playwright": e.get("use_playwright", False),
                "is_noon_crawl": e["domain"] in NOON_TIER_A_DOMAINS,
            }
            for e in new_entries
        ]

        stmt = pg_insert(NewsCrawlSource).on_conflict_do_nothing(index_elements=["name"])
        await session.execute(stmt, rows)
        await session.commit()
        print(f"Đã insert {len(rows)} nguồn mới vào news_crawl_sources.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources-file", default="sources.yaml")
    args = parser.parse_args()
    asyncio.run(main(args.sources_file))
