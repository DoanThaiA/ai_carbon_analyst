"""
Các cấu trúc dữ liệu dùng chung cho crawler + pipeline xử lý tin tức.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

Tier = Literal["A", "B", "C"]
DateConfidence = Literal["metadata", "url", "unknown"]
Region = Literal["vietnam", "international"]


@dataclass
class SourceConfig:
    domain: str
    name: str
    tier: Tier
    category: str  # ví dụ: energy_official, eu_climate_policy, carbon_market_voluntary...
    region: Region = "international"  # phân biệt nguồn Việt Nam / quốc tế — dùng cho Mục 6 báo cáo
    type: Literal["rss", "html"] = "html"
    rss_url: Optional[str] = None
    listing_url: Optional[str] = None
    exclude_path_patterns: List[str] = field(
        default_factory=lambda: [
            "/tag/", "/category/", "/author/", "/about",
            "/contact", "/login", "/search", "/page/",
            "/registration", "/subscribe", "/newsletter",
        ]
    )
    # Metadata bổ sung từ sources.yaml (dùng để lọc/log, không ảnh hưởng crawl)
    group: List[int] = field(default_factory=list)       # nhóm chủ đề [1, 2, 3]
    confidence: str = ""                                  # high / medium / low (độ tin cậy URL)
    note: str = ""                                        # ghi chú ngắn về nguồn
    link_pattern: Optional[str] = None                    # regex khớp URL bài viết (None = dùng heuristic)
    max_articles: Optional[int] = None                    # giới hạn bài/lần crawl (None = dùng MAX_LINKS_PER_LISTING_PAGE)
    use_playwright: bool = False                          # True → dùng Playwright để render JS trước khi parse link


@dataclass
class CrawledItem:
    url: str
    source_domain: str
    tier: Tier
    title: Optional[str]
    raw_html: str
    discovered_at: datetime
    region: Region = "international"


@dataclass
class PriceQuote:
    instrument: str
    ticker: str
    price: float
    change_pct_day: Optional[float]
    as_of: datetime
    source: str


class NewsTopic(str, Enum):
    """
    13 topic phân loại tin tức — 1 bài có thể gắn tối đa 3 topic.

    Mapping sang mục báo cáo:
      eua_ets              → Mục 1, 2, 3, 5, 7
      energy_gas           → Mục 1, 3, 5
      energy_power_eu      → Mục 1, 3, 5
      energy_coal          → Mục 1, 3, 5
      energy_oil           → Mục 1, 3
      energy_renewable     → Mục 3, 5
      energy_hydrogen      → Mục 3, 5
      geopolitics          → Mục 1, 3, 5, 7
      eu_policy            → Mục 1, 3
      cbam                 → Mục 1, 4, 5
      vcm                  → Mục 4
      global_carbon_market → Mục 4
      vietnam_carbon_policy→ Mục 4
    """
    EUA_ETS               = "eua_ets"               # Giá EUA, EU ETS, auction, open interest
    ENERGY_GAS            = "energy_gas"             # TTF, Henry Hub, LNG, khí tự nhiên
    ENERGY_POWER_EU       = "energy_power_eu"        # Điện Đức DEBY1, merit order châu Âu
    ENERGY_COAL           = "energy_coal"            # API2, API4, NEWC, than nhiệt/cốc
    ENERGY_OIL            = "energy_oil"             # WTI, Brent, Gasoil, OPEC+
    ENERGY_RENEWABLE      = "energy_renewable"       # Gió, mặt trời, tăng trưởng RE
    ENERGY_HYDROGEN       = "energy_hydrogen"        # Hydrogen xanh, thép xanh, H2
    GEOPOLITICS           = "geopolitics"            # Nga-Ukraine, Trung Đông, TQ, thương mại
    EU_POLICY             = "eu_policy"              # Fit-for-55, ETS reform, MSR, EU Commission
    CBAM                  = "cbam"                   # EU CBAM, UK CBAM, lộ trình các nước
    VCM                   = "vcm"                    # Verra, Gold Standard, ACR, CAR, Article 6
    GLOBAL_CARBON_MARKET  = "global_carbon_market"   # Korea ETS, China ETS, California, CORSIA
    VIETNAM_CARBON_POLICY = "vietnam_carbon_policy"  # Chính sách carbon VN, VETS


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
    region: Region = "international"


@dataclass
class ClassificationResult:
    """Kết quả phân loại từ LLM — 1–3 topic, kèm confidence trung bình.

    is_relevant=False: LLM nhận định bài không liên quan đến energy/carbon —
    topics sẽ là [] và pipeline sẽ skip lưu DB (status='irrelevant').

    is_hot_news=True: bài khớp 1 trong 4 tiêu chí HOT NEWS (đảo chiều giá EUA,
    CBAM thay đổi đột ngột, địa chính trị tiềm ẩn xung đột, nước lớn rút khỏi
    decarbonization) — pipeline sẽ đẩy lên chuông thông báo trên header.
    """
    topics: List[NewsTopic]
    confidence: float
    is_relevant: bool = True
    is_hot_news: bool = False
    hot_news_reason: Optional[str] = None


PipelineStatus = Literal[
    "stored",
    "duplicate",
    "extraction_failed",
    "classification_failed",
    "skipped_old",    # bài đăng trước ngày crawl, bị date filter loại
    "irrelevant",     # LLM xác nhận bài không liên quan energy/carbon
]


@dataclass
class PipelineResult:
    url: str
    status: PipelineStatus
    article_id: Optional[int] = None
    topics: List[NewsTopic] = field(default_factory=list)
    confidence: Optional[float] = None
    content_hash: Optional[str] = None
    detail: Optional[str] = None
    is_hot_news: bool = False
