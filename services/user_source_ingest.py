"""Sonic crawl 1 link do NGƯỜI DÙNG đưa và lưu vào kho tin (`articles` + `chunks`) — tool MCP
`crawl_user_source` (services/claude_connect.py::SONIC_CRAWL_DESKTOP_TOOL, chạy ở
api/routers/mcp_gateway.py). Là đường GHI thứ 2 của gateway (sau biên bản QC) nên chỉ cho token
role user, có rate limit riêng.

Đi đúng các bước của pipeline crawl (pipeline/crawl_pipeline.py::_dedupe_classify_store): trích nội dung
(trafilatura) → lọc trùng url/hash → phân loại EUA bằng LLM → embed → lưu. Bài không liên quan EUA
KHÔNG được lưu (cùng tiêu chí với crawl định kỳ). Khác crawl định kỳ:
  - Fetch bằng `source_reader.fetch_source_page` (chặn SSRF + 2 tầng curl/Playwright) vì URL do người dùng đưa,
    không qua PoliteFetcher.
  - Không lọc cửa sổ ngày đăng (người dùng chủ động muốn lưu) — tin cũ hơn STALE_NEWS_MAX_AGE_DAYS vẫn bị
    get_news_for_report bỏ khỏi báo cáo ngày, chỉ tra được qua search_news/get_article.
  - Không ghi crawl_seen_urls (bảng đó theo nguồn crawl cấu hình).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from crawl_news.classification import Classifier, build_classifier
from crawl_news.dedupe import Sha256Fingerprinter
from crawl_news.extraction import extract_article
from db.models import Article, NewsCrawlSource
from pipeline.crawl_pipeline import PipelineContext, _dedupe_classify_store
from schemas.crawl_models import CrawledItem
from services.embedding import CohereEmbedder
from services.report_generator import STALE_NEWS_MAX_AGE_DAYS
from services.source_reader import SourceUnreadable, fetch_source_page

logger = logging.getLogger(__name__)

# Client MCP stdio timeout 60s/tool (mcp_client/.../client.py::REQUEST_TIMEOUT_SECONDS) — Playwright ngắn hơn
# fetch_user_source để còn thời gian phân loại + embed.
INGEST_PLAYWRIGHT_TIMEOUT = 25.0
# Nguồn không có trong news_crawl_sources = chưa phân hạng độ tin cậy → hạng thấp nhất.
DEFAULT_USER_SOURCE_TIER = "C"
_VN_TZ = timezone(timedelta(hours=7))

_classifier: Optional[Classifier] = None
_embedder: Optional[CohereEmbedder] = None


def _get_classifier() -> Classifier:
    global _classifier
    if _classifier is None:
        settings = Settings.from_env()
        _classifier = build_classifier(
            backend=settings.classifier_backend,
            anthropic_api_key=settings.anthropic_api_key,
            model=settings.classifier_model,
            concurrency=settings.classify_concurrency,
        )
    return _classifier


def _get_embedder() -> CohereEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = CohereEmbedder()
    return _embedder


def source_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


async def _source_meta(session: AsyncSession, domain: str) -> tuple[str, str]:
    """(tier, region): theo nguồn crawl đã cấu hình cùng domain nếu có, không thì hạng C + đoán vùng theo đuôi .vn."""
    row = (
        await session.execute(
            select(NewsCrawlSource.tier, NewsCrawlSource.region)
            .where(NewsCrawlSource.domain.in_([domain, f"www.{domain}"]))
            .order_by(NewsCrawlSource.is_active.desc(), NewsCrawlSource.tier)
            .limit(1)
        )
    ).first()
    if row:
        return row.tier, row.region
    return DEFAULT_USER_SOURCE_TIER, "vietnam" if domain.endswith(".vn") else "international"


async def _existing_article(
    session: AsyncSession, urls: list[str], content_hash: Optional[str] = None
) -> Optional[Article]:
    cond = Article.url.in_(urls)
    if content_hash:
        cond = cond | (Article.content_hash == content_hash)
    return (await session.execute(select(Article).where(cond).limit(1))).scalars().first()


def _fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "không rõ"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_VN_TZ).strftime("%d/%m/%Y %H:%M (giờ VN)")


def _already_stored_text(article: Article) -> str:
    return (
        "ĐÃ CÓ TRONG KHO TIN — không lưu lại (trùng đường dẫn hoặc trùng nội dung).\n"
        f"- ID bài: {article.id}\n- Tiêu đề: {article.title or '(không có)'}\n- Nguồn: {article.source}\n"
        f"- URL đã lưu: {article.url}\n- Ngày đăng: {_fmt_dt(article.published_at)}\n"
        f"- Thu thập lúc: {_fmt_dt(article.crawled_at)}\n- Chủ đề: {', '.join(article.topic or []) or 'không có'}\n"
        "Đọc toàn văn bằng get_article(article_id)."
    )


def _report_note(published_at: Optional[datetime], now_utc: datetime) -> str:
    if published_at is not None:
        pub = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        if pub < now_utc - timedelta(days=STALE_NEWS_MAX_AGE_DAYS):
            return (
                f"Bài đăng cách đây hơn {STALE_NEWS_MAX_AGE_DAYS} ngày nên KHÔNG được đưa vào báo cáo ngày; vẫn tra "
                "được qua search_news/get_article."
            )
    # Tin chia vào báo cáo theo crawled_at: crawl ngày D (giờ UTC) → khung tin của báo cáo ngày D+1
    # (services/report_generator.py::get_news_for_report).
    report_day = (now_utc.date() + timedelta(days=1)).isoformat()
    return f"Bài nằm trong khung tin của báo cáo ngày {report_day} (nếu ngày đó có báo cáo)."


async def ingest_user_url(
    session_factory: async_sessionmaker[AsyncSession], url: str, *, submitted_by: str
) -> str:
    """Crawl + lưu 1 URL; luôn trả chuỗi kết quả cho model (lỗi cũng là chuỗi hướng dẫn)."""
    url = (url or "").strip()
    async with session_factory() as session:
        if url:
            existing = await _existing_article(session, [url])
            if existing is not None:
                return _already_stored_text(existing)

    try:
        page = await fetch_source_page(url, playwright_timeout=INGEST_PLAYWRIGHT_TIMEOUT)
    except SourceUnreadable as e:
        logger.info("[USER-INGEST] user=%s url=%s -> không đọc được", submitted_by, url)
        return f"KHÔNG LƯU — {e}"
    if page.ctype not in ("text/html", "application/xhtml+xml"):
        return (
            f"KHÔNG LƯU — nguồn là '{page.ctype}', chỉ lưu được trang bài viết (HTML) vào kho tin. Muốn đọc nội dung "
            "thì dùng fetch_user_source."
        )

    domain = source_domain(page.final_url)
    async with session_factory() as session:
        if page.final_url != url:
            existing = await _existing_article(session, [page.final_url])
            if existing is not None:
                return _already_stored_text(existing)
        tier, region = await _source_meta(session, domain)

    article = extract_article(
        CrawledItem(
            url=page.final_url, source_domain=domain, tier=tier, title=None, raw_html=page.content,
            discovered_at=datetime.now(timezone.utc), region=region,
        )
    )
    if article is None:
        return (
            "KHÔNG LƯU — không trích được nội dung bài viết đủ dài từ trang này (có thể là trang danh mục/trang chủ, "
            "không phải 1 bài, hoặc nội dung bị ẩn). Mỗi lần chỉ crawl được 1 link bài viết cụ thể."
        )

    ctx = PipelineContext(
        fetcher=None,
        classifier=_get_classifier(),
        fingerprinter=Sha256Fingerprinter(),
        embedder=_get_embedder(),
        session_factory=session_factory,
    )
    res = await _dedupe_classify_store(ctx, article)
    logger.info(
        "[USER-INGEST] user=%s url=%s -> %s (id=%s, hash=%s)",
        submitted_by, article.url, res.status, res.article_id, res.content_hash,
    )

    if res.status == "stored":
        hot = (
            "\n- TIN NÓNG: có — sẽ hiện trên chuông thông báo và vào email tin nóng của đợt crawl kế tiếp."
            if res.is_hot_news else ""
        )
        return (
            "ĐÃ LƯU VÀO KHO TIN.\n"
            f"- ID bài: {res.article_id}\n- Tiêu đề: {article.title or '(không có)'}\n"
            f"- Nguồn: {domain} (hạng {tier}, {'Việt Nam' if region == 'vietnam' else 'quốc tế'})\n"
            f"- URL: {article.url}\n- Ngày đăng: {_fmt_dt(article.published_at)}\n"
            f"- Chủ đề: {', '.join(t.value for t in res.topics)}{hot}\n"
            f"- {_report_note(article.published_at, datetime.now(timezone.utc))}\n"
            "Bài đã được phân loại, lọc trùng và lập chỉ mục — tra lại bằng search_news hoặc get_article(article_id)."
        )
    if res.status == "duplicate":
        async with session_factory() as session:
            existing = await _existing_article(session, [article.url], res.content_hash)
        if existing is not None:
            return _already_stored_text(existing)
        return "ĐÃ CÓ TRONG KHO TIN — không lưu lại (trùng nội dung với bài đã lưu)."
    if res.status == "irrelevant":
        return (
            "KHÔNG LƯU — bộ phân loại đánh giá bài này KHÔNG tác động trực tiếp tới giá/cung/cầu EUA hay 1 yếu tố "
            "dẫn dắt EUA (khí/than/điện châu Âu, ngành thuộc EU ETS, CBAM, chính sách carbon VN...), nên không đưa vào "
            "kho tin — cùng tiêu chí với crawl tự động. Vẫn đọc được nội dung bằng fetch_user_source."
        )
    return (
        f"KHÔNG LƯU — lỗi khi phân loại bài ({res.detail or 'không rõ'}). Đây có thể là lỗi tạm thời; thử lại sau ít phút."
    )
