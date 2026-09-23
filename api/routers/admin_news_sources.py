"""
Admin CRUD cho bảng news_crawl_sources — thay thế sources.yaml hardcode.
main.py::load_sources_from_db() đọc các nguồn is_active=True từ bảng này;
scheduler.py::noon_news_crawl_job() lọc thêm is_noon_crawl=True (xem
scripts/migrate_sources.py cho việc backfill dữ liệu từ sources.yaml cũ).

POST /test-crawl chạy thử crawl_source() với cấu hình DRAFT gửi từ form (CHƯA
lưu vào DB), giới hạn TEST_CRAWL_LIMIT bài, KHÔNG qua extract/dedupe/classify/
store — chỉ để admin xác nhận listing_url/rss_url/link_pattern có bắt được
bài viết hay không trước khi bấm "Lưu".
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from crawl_news.crawler import crawl_source
from crawl_news.fetcher import PoliteFetcher
from crawl_news.playwright_fetcher import PlaywrightFetcher
from db.models import NewsCrawlSource
from schemas.crawl_models import SourceConfig

router = APIRouter(
    prefix="/api/admin/news-sources",
    tags=["admin-news-sources"],
    dependencies=[Depends(get_current_admin)],
)

# Số bài tối đa lấy khi test-crawl — độc lập với max_articles đã cấu hình
# (test chỉ cần đủ để admin xác nhận cấu hình đúng, không cần lấy hết).
TEST_CRAWL_LIMIT = 5


class NewsSourceCrawlConfig(BaseModel):
    """Các field ảnh hưởng trực tiếp tới việc crawl — dùng chung cho tạo/sửa
    nguồn (NewsSourceIn/Update) và cho payload test-crawl."""

    domain: str
    name: str
    tier: Literal["A", "B", "C"]
    category: str
    region: Literal["vietnam", "international"] = "international"
    source_type: Literal["html", "rss", "bloomberg_rss"] = "html"
    listing_url: Optional[str] = None
    rss_url: Optional[str] = None
    link_pattern: Optional[str] = None
    exclude_path_patterns: Optional[List[str]] = None
    group: List[int] = []
    bloomberg_feeds: List[str] = []
    confidence: Optional[str] = None
    note: Optional[str] = None
    max_articles: Optional[int] = None
    use_playwright: bool = False

    @model_validator(mode="after")
    def _check_required_by_type(self):
        if self.source_type == "html" and not self.listing_url:
            raise ValueError("Loại nguồn 'html' bắt buộc phải có listing_url.")
        if self.source_type == "rss" and not self.rss_url:
            raise ValueError("Loại nguồn 'rss' bắt buộc phải có rss_url.")
        if self.source_type == "bloomberg_rss" and not self.bloomberg_feeds:
            raise ValueError("Loại nguồn 'bloomberg_rss' bắt buộc phải có bloomberg_feeds.")
        return self

    def to_source_config(self, *, max_articles_override: Optional[int] = None) -> SourceConfig:
        kwargs = dict(
            domain=self.domain,
            name=self.name,
            tier=self.tier,
            category=self.category,
            region=self.region,
            type=self.source_type,
            rss_url=self.rss_url,
            listing_url=self.listing_url,
            group=self.group,
            link_pattern=self.link_pattern,
            max_articles=max_articles_override if max_articles_override is not None else self.max_articles,
            use_playwright=self.use_playwright,
            bloomberg_feeds=self.bloomberg_feeds,
            confidence=self.confidence or "",
            note=self.note or "",
        )
        # Bỏ qua nếu None để SourceConfig dùng default_factory (danh sách exclude
        # path mặc định) thay vì ghi đè bằng None.
        if self.exclude_path_patterns is not None:
            kwargs["exclude_path_patterns"] = self.exclude_path_patterns
        return SourceConfig(**kwargs)


class NewsSourceIn(NewsSourceCrawlConfig):
    is_active: bool = True
    is_noon_crawl: bool = False


class NewsSourceUpdate(NewsSourceIn):
    pass


def _serialize(row: NewsCrawlSource) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "name": row.name,
        "category": row.category,
        "tier": row.tier,
        "region": row.region,
        "source_type": row.source_type,
        "listing_url": row.listing_url,
        "rss_url": row.rss_url,
        "link_pattern": row.link_pattern,
        "exclude_path_patterns": row.exclude_path_patterns,
        "group": row.group,
        "bloomberg_feeds": row.bloomberg_feeds,
        "confidence": row.confidence,
        "note": row.note,
        "max_articles": row.max_articles,
        "use_playwright": row.use_playwright,
        "is_active": row.is_active,
        "is_noon_crawl": row.is_noon_crawl,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("")
async def list_news_sources(session: AsyncSession = Depends(get_db)):
    rows = (
        await session.execute(select(NewsCrawlSource).order_by(NewsCrawlSource.tier, NewsCrawlSource.name))
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("")
async def create_news_source(body: NewsSourceIn, session: AsyncSession = Depends(get_db)):
    row = NewsCrawlSource(
        domain=body.domain,
        name=body.name,
        category=body.category,
        tier=body.tier,
        region=body.region,
        source_type=body.source_type,
        listing_url=body.listing_url,
        rss_url=body.rss_url,
        link_pattern=body.link_pattern,
        exclude_path_patterns=body.exclude_path_patterns,
        group=body.group,
        bloomberg_feeds=body.bloomberg_feeds,
        confidence=body.confidence,
        note=body.note,
        max_articles=body.max_articles,
        use_playwright=body.use_playwright,
        is_active=body.is_active,
        is_noon_crawl=body.is_noon_crawl,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Tên nguồn (name) đã tồn tại.")
    await session.refresh(row)
    return _serialize(row)


@router.put("/{source_id}")
async def update_news_source(
    source_id: int, body: NewsSourceUpdate, session: AsyncSession = Depends(get_db)
):
    row = await session.get(NewsCrawlSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn tin.")

    row.domain = body.domain
    row.name = body.name
    row.category = body.category
    row.tier = body.tier
    row.region = body.region
    row.source_type = body.source_type
    row.listing_url = body.listing_url
    row.rss_url = body.rss_url
    row.link_pattern = body.link_pattern
    row.exclude_path_patterns = body.exclude_path_patterns
    row.group = body.group
    row.bloomberg_feeds = body.bloomberg_feeds
    row.confidence = body.confidence
    row.note = body.note
    row.max_articles = body.max_articles
    row.use_playwright = body.use_playwright
    row.is_active = body.is_active
    row.is_noon_crawl = body.is_noon_crawl

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Tên nguồn (name) đã tồn tại.")
    await session.refresh(row)
    return _serialize(row)


@router.delete("/{source_id}")
async def delete_news_source(source_id: int, session: AsyncSession = Depends(get_db)):
    row = await session.get(NewsCrawlSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy nguồn tin.")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/test-crawl")
async def test_crawl_news_source(body: NewsSourceCrawlConfig):
    source = body.to_source_config(max_articles_override=TEST_CRAWL_LIMIT)

    fetcher = PoliteFetcher()
    playwright_fetcher: Optional[PlaywrightFetcher] = None
    try:
        if source.use_playwright:
            playwright_fetcher = PlaywrightFetcher()
            await playwright_fetcher.start()
        items = await crawl_source(fetcher, source, seen_urls=set(), playwright_fetcher=playwright_fetcher)
    finally:
        if playwright_fetcher is not None:
            await playwright_fetcher.stop()
        await fetcher.close()

    items = items[:TEST_CRAWL_LIMIT]
    if not items:
        raise HTTPException(
            status_code=422,
            detail="Không crawl được bài nào — kiểm tra lại listing_url/rss_url/link_pattern, "
            "hoặc trang có thể chặn bot (thử bật use_playwright).",
        )

    return {
        "count": len(items),
        "articles": [
            {"title": item.title, "url": item.url, "html_length": len(item.raw_html)}
            for item in items
        ],
    }
