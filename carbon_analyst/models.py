"""
Các cấu trúc dữ liệu dùng chung cho crawler + pipeline xử lý tin tức.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

Tier = Literal["A", "B", "C"]


@dataclass
class SourceConfig:
    domain: str
    name: str
    tier: Tier
    category: str  # ví dụ: energy_official, carbon_news, vn_market...
    type: Literal["rss", "html"] = "html"
    rss_url: Optional[str] = None
    listing_url: Optional[str] = None
    exclude_path_patterns: List[str] = field(
        default_factory=lambda: [
            "/tag/", "/category/", "/author/", "/about",
            "/contact", "/login", "/search", "/page/",
        ]
    )


@dataclass
class CrawledItem:
    url: str
    source_domain: str
    tier: Tier
    title: Optional[str]
    raw_html: str
    discovered_at: datetime


@dataclass
class PriceQuote:
    instrument: str
    ticker: str
    price: float
    change_pct_day: Optional[float]
    as_of: datetime
    source: str


class NewsCategory(str, Enum):
    """3 loại tin tức cần phân loại (theo yêu cầu JD)."""

    ENERGY_FOSSIL_FUELS = "energy_fossil_fuels"  # Năng lượng & nhiên liệu hóa thạch
    CARBON_CREDITS = "carbon_credits"  # Hạn ngạch & Tín chỉ carbon
    POLICY = "policy"  # Chính sách


@dataclass
class ExtractedArticle:
    """Kết quả trích xuất content + metadata từ HTML thô của 1 bài viết."""

    url: str
    source_domain: str
    tier: Tier
    title: Optional[str]
    text: str
    published_at: Optional[datetime]
    extracted_at: datetime


@dataclass
class ClassificationResult:
    category: NewsCategory
    confidence: float


PipelineStatus = Literal[
    "stored", "duplicate", "extraction_failed", "classification_failed",
]


@dataclass
class PipelineResult:
    url: str
    status: PipelineStatus
    news_id: Optional[int] = None
    category: Optional[NewsCategory] = None
    confidence: Optional[float] = None
    content_hash: Optional[str] = None
    detail: Optional[str] = None
