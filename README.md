# Carbon Analyst — Crawl & News Ingestion Pipeline

Crawl tin tức theo danh mục nguồn ưu tiên (Hạng A/B/C), trích xuất nội dung,
dedupe, phân loại category bằng Claude API, lưu vào Postgres — cùng với giá
thị trường theo đúng yêu cầu trong JD.

## Cài đặt

```bash
uv sync
# hoặc
pip install -r requirements.txt
```

Copy `.env.example` → `.env` và điền:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
ANTHROPIC_API_KEY=sk-ant-...
CLASSIFIER_MODEL=claude-haiku-4-5   # mặc định, có thể đổi
CLASSIFY_CONCURRENCY=5
```

Cần một Postgres có extension `pgvector` (dùng cho bảng `chunks`) — cho
local/test dùng `docker-compose.yml` sẵn trong repo (image `pgvector/pgvector:pg16`):

```bash
docker compose up -d
```

Sau đó chạy migration (Alembic) — bắt buộc trước khi chạy bất kỳ script nào,
DB sẽ chưa có bảng nào nếu bỏ qua bước này:

```bash
alembic upgrade head
```

## Test từng link trước khi chạy cả loạt

Trước khi bật crawl toàn bộ ~47 nguồn, test pipeline (crawl → extract → dedupe
→ classify → lưu DB) trên từng URL cụ thể:

```bash
# Dry-run — in ra từng bước, KHÔNG ghi DB
python -m scripts.test_url "https://www.iea.org/news/<slug>" --domain iea.org --tier A

# Ghi thật vào DB
python -m scripts.test_url "https://www.iea.org/news/<slug>" --domain iea.org --tier A --store
```

Script in ra: độ dài HTML fetch được, title/ngày đăng/preview nội dung trích
xuất được, content hash, kết quả kiểm tra trùng lặp, category + confidence từ
Claude, số chunk sinh ra, và trạng thái lưu DB (bảng `articles` + `chunks`).

## Chạy crawl hằng ngày

```bash
# Toàn bộ nguồn trong sources.yaml
python -m scripts.run_daily_crawl

# Test 1 nguồn trước khi bật toàn bộ, giới hạn số bài để tiết kiệm gọi LLM
python -m scripts.run_daily_crawl --source-domain eia.gov --limit 5
```

Log in ra số bài crawl/lưu/trùng/lỗi theo từng trạng thái, và giá các
instrument lấy được (hiện tại chỉ WTI/Brent có dữ liệu thật qua yfinance).

## Cấu trúc thư mục

| Path | Vai trò |
|---|---|
| `crawl_news/models.py` | Dataclass/enum dùng chung: `SourceConfig`, `CrawledItem`, `ExtractedArticle`, `NewsCategory`, `PipelineResult`... |
| `crawl_news/config.py` | `Settings` đọc từ biến môi trường (`.env`) |
| `crawl_news/fetcher.py` | HTTP fetch lịch sự: giới hạn tốc độ theo domain, retry, bỏ qua 403/404 |
| `crawl_news/crawler.py` | Crawl RSS (feedparser) hoặc HTML listing (selectolax) tuỳ theo config nguồn — chỉ trả về HTML thô |
| `crawl_news/extraction.py` | Trích xuất content + metadata (title, ngày đăng) bằng trafilatura |
| `crawl_news/dedupe.py` | `Fingerprinter` — SHA-256 content hash (MVP), interface sẵn sàng cắm embedding dedupe sau |
| `crawl_news/classification.py` | Phân loại 3 category bằng Claude API (structured output) |
| `crawl_news/chunking.py` | Chia nội dung bài viết thành chunk (~1000 ký tự) trước khi embedding |
| `crawl_news/embedding.py` | `Embedder` — sinh vector 384 chiều cho chunk (mặc định fastembed/MiniLM, local, không tốn tiền API) |
| `crawl_news/storage.py` | Data-access layer của pipeline tin tức (SQLAlchemy async) — lưu bài vào `articles`, chunk+embedding vào `chunks`, dedupe atomic qua unique constraint |
| `crawl_news/pipeline.py` | Orchestrator: crawl → extract → dedupe → classify → store → chunk + embed |
| `crawl_news/market_data.py` | Lấy giá instrument — yfinance cho WTI/Brent, interface chờ cắm vendor thật cho EUA/TTF/... |
| `db/` | **Package riêng, dùng chung cho cả dự án** (không nằm trong `crawl_news/`) — engine/session SQLAlchemy async (`db/session.py`) + ORM model `Article`/`Chunk` (`db/models.py`) |
| `alembic/`, `alembic.ini` | Migration schema (Alembic) — nguồn chân lý cho DDL, chạy `alembic upgrade head` để tạo/cập nhật bảng |
| `sources.yaml` | Danh mục ~47 nguồn từ JD, đã gắn tier A/B/C |
| `instruments.yaml` | Danh sách 6 instrument cần theo dõi giá |
| `scripts/test_url.py` | Test pipeline trên 1 URL, dry-run mặc định |
| `scripts/run_daily_crawl.py` | Entry point crawl + xử lý hằng ngày |

## Category phân loại

1. `energy_fossil_fuels` — Năng lượng & nhiên liệu hóa thạch
2. `carbon_credits` — Hạn ngạch & Tín chỉ carbon
3. `policy` — Chính sách

## Thêm nguồn mới

Thêm entry vào `sources.yaml`:

```yaml
- domain: example.com
  name: "Tên nguồn"
  tier: B
  category: carbon_news
  region: international   # hoặc "vietnam" — quyết định bài viết rơi vào nhóm nào ở Mục 6 báo cáo
  type: html          # hoặc "rss" nếu có feed
  listing_url: "https://example.com/news"   # dùng khi type: html
  # rss_url: "https://example.com/feed"     # dùng khi type: rss
```

## Kết nối vào Celery (khi lên production)

```python
from celery import shared_task
from scripts.run_daily_crawl import main
import asyncio

@shared_task
def daily_crawl_task():
    return asyncio.run(main())
```

Ghép với lịch `celery beat` đã thiết kế ở phần trước (chạy mỗi sáng trước
giờ mở cửa).

## Giới hạn hiện tại — cần biết trước khi chạy thật

1. **Heuristic tìm link bài viết** (`_extract_article_links` trong
   `crawl_news/crawler.py`) hoạt động tốt với site dạng blog/WordPress
   phổ biến, nhưng ~47 nguồn trong JD có cấu trúc rất khác nhau. Sau lần chạy
   đầu, xem log nguồn nào trả về 0 bài rồi bổ sung `link_pattern` riêng cho
   nguồn đó (chưa implement field này — cần thêm nếu heuristic chung không
   đủ tốt).

2. **`sources.yaml` không lưu trạng thái "đã crawl"** — mỗi lần chạy
   `run_daily_crawl` sẽ crawl lại toàn bộ listing page của từng nguồn (không
   tốn thêm tiền LLM vì dedupe check chạy trước classify, nhưng vẫn tốn
   request HTTP). Nếu cần giảm tải, có thể lưu `seen_urls` ra ngoài process
   (vd Redis) thay vì set trong bộ nhớ.

3. **`market_data.py`**: EUA, TTF, German Power, NEWC/API 2-4-5 chưa có
   nguồn dữ liệu thật (`ManualOrVendorProvider` raise `NotImplementedError`
   có chủ đích) — cần công ty xác nhận nguồn (Bloomberg/Refinitiv/ICE Data
   Vault/Barchart OnDemand) trước khi phần "Bảng giá nhanh" và biểu đồ nến
   EUA trong báo cáo có dữ liệu thật.

4. **Một số site có thể chặn bot** (Bloomberg, FT thường yêu cầu đăng nhập
   hoặc chặn scraper) — với các nguồn này nên ưu tiên dùng web search tool
   của Claude ở bước phân tích thay vì cố crawl trực tiếp.

5. **Site cần JS render** (ít gặp trong danh sách JD nhưng có thể phát
   sinh) — `httpx` không chạy JS; nếu 1 nguồn liên tục trả về 0 bài dù có
   `listing_url` đúng, khả năng cao cần đổi sang Playwright cho riêng
   nguồn đó.

6. **Dedupe bằng content hash** chỉ bắt được trùng chính xác/gần chính xác
   (khác whitespace, khác case). Không bắt được bài paraphrase/rewrite giữa
   các nguồn — cắm `EmbeddingFingerprinter` trong `dedupe.py` khi cần.

7. **`is_relevant`/`category` trong bảng `articles`**: classifier hiện tại
   luôn ép bài vào đúng 1 trong 3 category (không có khái niệm "không liên
   quan"), nên pipeline luôn set `is_relevant=true` và `category` là mảng 1
   phần tử. Schema đã để sẵn `is_relevant` (boolean) và `category` (TEXT[])
   cho việc mở rộng đa nhãn/lọc-không-liên-quan sau này mà không cần đổi
   schema lần nữa.

8. **Chunk + embedding (`crawl_news/chunking.py`, `embedding.py`)** chạy
   SAU khi bài đã insert vào `articles` — nếu bước này lỗi (model chưa tải
   được, DB tạm gián đoạn...), bài viết vẫn được lưu nhưng KHÔNG có chunk.
   Do dedupe check chạy trên `articles`, lần crawl sau sẽ coi bài này là
   trùng và không tự retry chunk/embed — cần chạy lại thủ công (script
   backfill chunk cho các `articles.id` chưa có `chunks` tương ứng) nếu gặp
   trường hợp này trên diện rộng.

9. **`FastEmbedEmbedder`** tải model `sentence-transformers/all-MiniLM-L6-v2`
   (384 chiều, khớp `Vector(384)` khai báo ở `db/models.py::Chunk.embedding`) từ
   HuggingFace ở lần chạy đầu — cần mạng để tải lần đầu, sau đó cache local.

10. **Schema do Alembic quản lý, không tự tạo lúc chạy** — khác với bản cũ
    (`ensure_schema()` gọi mỗi lần chạy script), giờ phải chạy
    `alembic upgrade head` thủ công (1 lần khi setup, và mỗi khi
    `db/models.py` đổi + có migration mới). Script sẽ lỗi rõ ràng (bảng/cột
    không tồn tại) nếu quên bước này — đây là chủ đích, không phải bug.
