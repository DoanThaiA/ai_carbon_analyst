"""
Các cấu trúc dữ liệu dùng chung cho crawler + pipeline xử lý tin tức.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

Tier = Literal["A", "B", "C"]
DateConfidence = Literal["metadata", "url", "unknown"]


@dataclass
class SourceConfig:
    domain: str
    name: str
    tier: Tier
    category: str  # ví dụ: energy_official, eu_climate_policy, carbon_market_voluntary...
    type: Literal["rss", "html"] = "html"
    rss_url: Optional[str] = None
    listing_url: Optional[str] = None
    exclude_path_patterns: List[str] = field(
        default_factory=lambda: [
            "/tag/", "/category/", "/author/", "/about",
            "/contact", "/login", "/search", "/page/",
        ]
    )
    # Metadata bổ sung từ sources.yaml (dùng để lọc/log, không ảnh hưởng crawl)
    group: List[int] = field(default_factory=list)       # nhóm chủ đề [1, 2, 3]
    confidence: str = ""                                  # high / medium / low (độ tin cậy URL)
    note: str = ""                                        # ghi chú ngắn về nguồn
    link_pattern: Optional[str] = None                    # regex khớp URL bài viết (None = dùng heuristic)


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
    date_confidence: DateConfidence
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
    article_id: Optional[int] = None
    category: Optional[NewsCategory] = None
    confidence: Optional[float] = None
    content_hash: Optional[str] = None
    detail: Optional[str] = None
