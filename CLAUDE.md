# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A news-ingestion pipeline for a carbon derivatives intelligence desk. It crawls news
from a curated list of ~47 sources (tiered A/B/C by reliability, per the project's
JD/spec), extracts and deduplicates the content, classifies each article into one of
3 categories via the Claude API, and stores it in Postgres. It also fetches market
prices for 6 instruments (WTI, Brent, EUA, TTF, German Power, NEWC).

The codebase and comments are written in Vietnamese; keep that convention when editing
existing files (docstrings, log messages, comments).

## Commands

```bash
# Install deps (uv is the primary tool — pyproject.toml + uv.lock are present)
uv sync
# or
pip install -r requirements.txt

# Test the full pipeline on ONE url before trusting a full crawl (dry-run by default)
python -m scripts.test_url "<url>" --domain <source_domain> --tier A
python -m scripts.test_url "<url>" --domain <source_domain> --tier A --store   # actually persist

# Run the crawl job end-to-end (news pipeline + prices)
python -m scripts.run_daily_crawl
python -m scripts.run_daily_crawl --source-domain <domain> --limit 5   # test one source first

# Sanity-check a file compiles
python -m py_compile <file>.py
```

```bash
# Apply DB migrations — REQUIRED before running any script the first time,
# and after pulling changes that touch db/models.py
alembic upgrade head
```

Requires `DATABASE_URL` and `ANTHROPIC_API_KEY` in the environment (`.env` is loaded
automatically via `python-dotenv` — copy `.env.example` to start). The Postgres
instance must support the `pgvector` extension (`chunks.embedding` is
`VECTOR(384)`) — `docker-compose.yml` provisions this locally via the
`pgvector/pgvector:pg16` image; migration `0001` runs
`CREATE EXTENSION IF NOT EXISTS vector` itself, but the extension binary must exist
in the Postgres image. There is no test suite, linter, or CI config in this repo
yet. `main.py` is a demo/ad-hoc runner (not the original uv-scaffold
placeholder anymore) that duplicates most of `scripts/run_daily_crawl.py` —
runs every source in `sources.yaml` with no `--limit`; prefer
`scripts/run_daily_crawl.py` for anything beyond a full local demo run.

## Architecture

Pipeline logic lives in the `crawl_news/` package; `scripts/` holds thin CLI
entry points. `db/` is a **separate, top-level package** (sibling to
`crawl_news/`, not nested inside it) — it's the shared SQLAlchemy
engine/session/ORM-model layer for the whole project's Postgres database, not
something private to the news pipeline. Anything else added to this repo later
(a report generator, a RAG query service, ...) that needs the DB imports `db`
directly, not through `crawl_news`. `crawl_news/storage.py` is the news
pipeline's own data-access layer built on top of `db` — it knows about
`ExtractedArticle`/`NewsCategory`, `db.models` does not. Async throughout (httpx,
SQLAlchemy's asyncio engine on top of asyncpg, the Anthropic SDK), matching the
original crawler's asyncio-first design.

**Pipeline** (`crawl_news/pipeline.py::process_url` / `process_source`), in a
deliberately cost-conscious order — dedupe check runs *before* the LLM call so a
duplicate never costs an API call:

1. **Fetch** — `fetcher.py::PoliteFetcher` (per-domain concurrency=2, per-domain
   delay=1s, treats 403/404 as permanent skips per the source spec's "don't use
   sources that error 403/404" requirement). `crawler.py::crawl_source()` dispatches
   RSS (`feedparser`) vs HTML-listing scraping (`selectolax`) per source, returning
   raw HTML only — never touches content extraction.
2. **Extract** — `extraction.py::extract_article()` uses `trafilatura` to pull main
   content + metadata (title, published date) out of raw HTML. Returns `None` (logged,
   pipeline stops here) if the extracted text is too short — usually means the site
   blocked the bot or the page isn't an article.
3. **Dedupe (fingerprint)** — `dedupe.py::Fingerprinter` ABC; `Sha256Fingerprinter` is
   the MVP implementation (normalize text, SHA-256). `storage.exists()` (a `SELECT`
   via SQLAlchemy) checks the DB for that hash *before* classification runs.
   `EmbeddingFingerprinter` is a stubbed `NotImplementedError` placeholder for later
   semantic (paraphrase-catching) dedupe — same "raise clearly instead of silently
   wrong" pattern as `market_data.py`'s `ManualOrVendorProvider`.
4. **Classify** — `classification.py::AnthropicClassifier` / `CohereClassifier` gọi LLM
   (default `command-r-plus-08-2024` cho Cohere, `claude-haiku-5-5` cho Anthropic — bị từ chối vì bộ lọc an toàn thì gọi lại 1 lần bằng `claude-haiku-4-5`; so sánh 2 model: `python -m scripts.compare_classifier`) qua
   `client.messages.create`. Input text được cắt tại `MAX_TEXT_CHARS_FOR_CLASSIFICATION = 6000`
   ký tự (tăng từ 4000 — đủ bắt được phần phân tích của bài dài). LLM trả về JSON gồm
   `topics` (1–3 giá trị từ `NewsTopic`), `confidence` (0–1), và `is_relevant` (bool).
   - `is_relevant=true` CHỈ khi bài tác động trực tiếp tới giá/cung/cầu EUA hoặc trực tiếp lên 1 yếu tố dẫn dắt EUA,
     và LLM phải trả `eua_channel` thuộc `EUA_CHANNELS` (eua_direct, gas_eu, coal_eu, power_eu, industry_eu, oil_shock,
     geopolitics_energy_eu, cbam; ngoại lệ hẹp vn_carbon / carbon_link cho Mục 4 + gợi ý SIM) — thiếu/sai kênh thì
     `_parse_json_response` ép thành không liên quan.
   - `is_relevant=false`: pipeline trả về `status="irrelevant"` và không lưu DB — kể cả tin nóng (không còn ngoại lệ lưu
     tin nóng không liên quan; `is_hot_news` bị ép false khi không liên quan).
   - Parse JSON lenient: strip/lowercase từng topic trước validate — topic sai
     bị skip (log warning), không raise — tránh drop bài vì whitespace/casing.
   - Lỗi API sau retry: raise `ClassificationError`; pipeline log và skip storing
     (bài sẽ được pick up lại lần crawl sau).
5. **Store** — `storage.py::insert_article()` builds a Postgres-dialect
   `insert(Article)...on_conflict_do_nothing(index_elements=["url"]).returning(Article.id)`
   (SQLAlchemy Core), plus a caught `IntegrityError` on the separate `content_hash`
   unique constraint (covers same-content-different-URL races). Articles marked
   `irrelevant` are NOT stored.
6. **Chunk + embed** — `chunking.py::chunk_text()` splits the stored article text
   into ~1000-char chunks (paragraph-aware, falls back to sentence splitting for long
   paragraphs); `embedding.py::Embedder` (default `FastEmbedEmbedder`, local
   `sentence-transformers/all-MiniLM-L6-v2`, 384 dims — must match `Vector(384)` in
   `db/models.py` if ever changed) embeds each chunk; `storage.py::insert_chunks()`
   bulk-inserts them into `chunks` with `source_type='article'` and
   `source_id=articles.id`. Runs *after* the article is already committed (same
   `AsyncSession`, separate `on_conflict_do_nothing`), so a failure here (model not
   downloaded yet, transient DB error) is caught, the session is rolled back, and
   it's logged — not raised — leaving the article row intact but without chunks.
   Because dedupe checks `articles`, a retried crawl will see the URL/hash as a
   duplicate and skip re-chunking, so a backfill script would be needed to catch up
   any articles left without chunks.

**Category enum** (`crawl_news/models.py::NewsCategory`, also the Postgres
`CHECK` constraint on `Article.category` in `db/models.py`): `energy_fossil_fuels`
(Năng lượng & nhiên liệu hóa thạch), `carbon_credits` (Hạn ngạch & Tín chỉ carbon),
`policy` (Chính sách).

**`PipelineContext`** (`pipeline.py`) bundles one `PoliteFetcher`, one `Classifier`,
one `Fingerprinter`, one `Embedder`, and one
`async_sessionmaker[AsyncSession]` (`session_factory`) — constructed once per
script run and passed through, rather than using module-level singletons. Each DB
operation opens its own short-lived session (`async with ctx.session_factory() as
session:`) rather than holding one open for the whole `_dedupe_classify_store` call,
so a connection isn't held idle while awaiting the Claude API.

**`db/` package** (top-level, shared across the project — see Architecture intro):
- `db/base.py` — the single `Base(DeclarativeBase)` every model inherits from.
- `db/models.py` — `Article` and `Chunk` ORM models. `Chunk.embedding` uses
  `pgvector.sqlalchemy.Vector(384)`; `Chunk.content_tsv` is a `Computed(...)`
  generated column (`to_tsvector('english', content)`), matching the `chunks` table's
  hybrid-search design (`VECTOR` for semantic search + `TSVECTOR`/GIN for full-text).
  Two tables: `articles` (1 row = 1 full article, dedup key + source of truth for
  reports/audits) and `chunks` (many rows per article, for hybrid semantic/full-text
  search over RAG — `source_type`/`source_id` let the same table later hold chunks
  from a `daily_reports`-style table too, not just articles).
- `db/session.py` — `create_engine()` builds the async engine (`postgresql+asyncpg`,
  `pool_pre_ping=True`, recycled every 30 min) and registers the pgvector codec via a
  SQLAlchemy `connect` event on `engine.sync_engine` (`pgvector.asyncpg.register_vector`
  needs the raw asyncpg connection, which is why it's wired at the `sync_engine`
  level, not the async one). `build_sessionmaker()` returns an
  `async_sessionmaker[AsyncSession]` with `expire_on_commit=False`.
- **Migrations are Alembic, not `Base.metadata.create_all()`** — `alembic/env.py`
  reads `DATABASE_URL` from `core.config.Settings` (same `.env`, not
  duplicated in `alembic.ini`) and runs migrations through the async engine.
  `alembic/versions/0001_initial.py` creates the `vector` extension + both tables +
  all indexes (partial index on `published_at`, `hnsw`/`vector_cosine_ops` on
  `embedding`, `gin` on `content_tsv`). Run `alembic upgrade head` before running any
  script for the first time, and after any change to `db/models.py` (create a new
  revision with `alembic revision -m "..."`, don't hand-edit `0001`). Nothing in
  `crawl_news/` or `scripts/` creates tables at runtime anymore — a missing
  table/column is a signal migrations haven't been applied, not a bug to code around.

**Lịch crawl** (`scheduler.py`): giá 06:00 VN; tin tức đầu mỗi giờ 08:00–17:00 MỌI ngày (nhóm `vietnam` thêm đợt 06:00 VN trước báo cáo 07:00), tách
theo `news_crawl_sources.region` (`NEWS_CRAWL_SCHEDULES`): `vietnam` theo giờ VN, `international`
theo giờ New York (`America/New_York`, tự theo giờ mùa hè Mỹ); mỗi job `max_instances=1` +
`coalesce=True` — 2 đợt cùng nhóm không chạy chồng; auto report NHÁP 07:00 VN (`AUTO_REPORT_TIME`), Jenny tự sửa theo QC 08:00 (`AUTO_QC_FIX_TIME`), tự xuất bản 08:15 (`AUTO_PUBLISH_TIME`, xem "Biên bản QC Claude"). `is_noon_crawl` chỉ còn dùng khi chạy tay `main.main(noon_only=True)`.
Để crawl hàng giờ không gọi LLM lặp lại, URL đã xử lý nhưng KHÔNG lưu thành article
(irrelevant, ngoài cửa sổ ngày, trùng hash, extract/classify lỗi, link Bloomberg gốc) được
ghi vào bảng `crawl_seen_urls` (`pipeline/crawl_pipeline.py::process_source` →
`storage.record_seen_urls`) và gộp vào `seen_urls` bởi `storage.load_recent_urls`; trạng thái
lỗi tạm thời được thử lại tới `SEEN_MAX_ATTEMPTS` lần. Lọc ngày đăng là cửa sổ TRƯỢT
`CRAWL_LOOKBACK_HOURS` (mặc định 36h) — việc chia bài vào báo cáo ngày nào dựa vào
`crawled_at`, không dựa vào cửa sổ này.

**Báo cáo Chủ Nhật/Thứ Hai = chỉ tin tức** (`report_generator.py::is_no_price_report`): ngày dữ liệu
là Thứ Bảy/Chủ Nhật, không có phiên → KHÔNG lấy giá (kể cả CBAM), không gọi Mục "2" (market_drivers), Mục 1/3/dev/biz
dùng prompt `news_only`, Mục 3 không có `instrument_notes`/`trading_scenarios`; `content["2"]` giữ khoá rỗng + cờ
`no_price_data`/`no_price_notice` để frontend (`ReportDocument.tsx`/`ReportEditor.tsx`), Jenny và Jack QC ẩn/bỏ qua phần giá.
Lưu ý `AUTO_REPORT_DAYS` mặc định `tue-sat` — muốn tự sinh báo cáo CN/T2 phải đặt lại trong `.env`.

**Bản tiếng Anh của báo cáo** (`services/report_translation.py`): dịch `reports.content` → `reports.content_en`.
`REPORT_TRANSLATION_PROVIDER=claude` (mặc định, `ClaudeTranslator`, model `REPORT_TRANSLATION_MODEL`, mặc định Sonnet 5.5 effort low): MỌI chữ hiển thị (tiêu đề mục, tiêu đề khối, nhãn chủ đề, tên nguồn, văn xuôi) do Claude TỰ dịch nguyên chuỗi theo lô có ngữ cảnh và được nhận NGUYÊN VĂN (`translate_strings_llm`) — không bảng dịch cứng, không kiểm tra/dịch lại (quyết định 22/10/2026: để Claude tự dịch). Prompt chỉ dặn vài neo frontend nhận diện (**Summary:**, [POSITIVE], **Target:**, tiền tố nhóm...) — sửa regex ở `ReportDocument.tsx` thì sửa cả prompt. Giữ tiếng Việt: khoá enum `PROTECTED_KEYS` (trừ `topics`), `ENUM_HEADINGS`, `KEEP_VERBATIM`, cây `UNTRANSLATED_SUBTREES`. `=libretranslate` = Argos tự host
(`LIBRETRANSLATE_URL`, không gọi LLM, chỉ đường cắt mảnh); `=off` = tắt. Chạy sau khi sinh báo cáo, sau khi admin sửa (PUT/gỡ gợi ý biz) và trước khi gửi email publish; lỗi dịch chỉ log.
`content_en_source_hash` = hash của `content` lúc dịch → lệch thì `fresh_content_en()` trả None (API trả `content_en: null`,
web hiện bản tiếng Việt kèm thông báo). Khoá enum (`PROTECTED_KEYS`: horizon/direction/impact/... + tiêu đề khối `ENUM_HEADINGS`) giữ tiếng Việt (tên nguồn/tên mã/tiêu đề khối khác vẫn dịch),
frontend tự đổi nhãn; neo cấu trúc (`**Tổng hợp:**`, `[TÍCH CỰC]`, tiền tố nhóm Mục 3, `**Mục tiêu:**`...) dịch bằng
`GLOSSARY` cố định — các regex trong `ReportDocument.tsx` và `report_qc.parse_strategy` nhận cả 2 ngôn ngữ; thêm neo mới
phải cập nhật cả 3 nơi. Email publish kèm 2 PDF (vi + `-en.pdf`, `render_report_pdf(lang="en")` đặt cookie `NEXT_LOCALE`).
Argos hay làm SAI SỐ/mã/ký hiệu ("79,0"→"720", "→"→"(10)") nên số, mã, đơn vị, ký hiệu và `TERMS` được che bằng placeholder rồi trả lại (đổi sang kiểu số tiếng Anh); bản dịch có chữ số lạ hoặc mất placeholder bị loại; dịch nhiều tầng đoạn → câu → vế, chỉ vế vẫn hỏng mới giữ tiếng Việt (log liệt kê đủ từng vế). Dịch bù: `python -m scripts.translate_reports --missing` (hoặc `--from/--to`, `--last N`, `--force`).

**Hot news email digest** (`services/hot_news_email.py`): at the end of every
`main.main()` crawl run (mỗi đợt crawl của cả 2 nhóm nguồn — nhóm quốc tế chạy ban đêm giờ VN; bỏ qua trong `HOT_NEWS_EMAIL_QUIET_HOURS`), all `is_hot_news` articles with
`hot_news_emailed_at IS NULL` (crawled within `HOT_NEWS_EMAIL_MAX_AGE_HOURS`) are
sent as ONE digest to every active `users` row via Resend (`services/email_sender.py`, httpx) —
each recipient gets their own email via `POST /emails/batch` (batched by `HOT_NEWS_EMAIL_BATCH_SIZE`, ≤100).
Rows are claimed with `FOR UPDATE SKIP LOCKED` and marked only after a successful
send, so a failed send retries next crawl. Tiêu chí tin nóng (classifier, `crawl_news/classification.py::HOT_NEWS_CATEGORIES`): CHỈ 4 nhóm —
chính trị gây sốc thị trường, cắt nguồn cung, đảo chiều giá, chính sách chưa từng có; mặc định false, model phải trả
`hot_news_category` hợp lệ (không thì bỏ cờ), lý do lưu kèm nhãn "[Cắt nguồn cung] ...". Digest bỏ bài trùng sự kiện
(so tiêu đề, `services/title_similarity.py`) với nhau hoặc với tin đã gửi trong `HOT_NEWS_DUP_LOOKBACK_HOURS` (72h) — bài
bị bỏ vẫn đánh dấu `hot_news_emailed_at`. Real-time UI alerts are separate (Postgres
NOTIFY + SSE, `services/hot_news_broadcast.py`). Manual: `python -m scripts.send_hot_news_digest --dry-run`.

**Kết nối Claude Desktop (MCP)**: user bấm "Hỏi Claude" trên quote → web tạo handoff
(`POST /api/reports/{date}/quote-chat/handoff`, `api/routers/claude_connect.py`) và trả prompt
ngắn để dán vào Claude Desktop; Claude Desktop chạy MCP server cục bộ (`mcp_client/`, gói
`carbon-analyst-mcp`, stdio) gọi `/api/mcp-gateway/*` (`api/routers/mcp_gateway.py`) bằng API token
cá nhân (`api_tokens`, bearer `cat_...`, chỉ lưu SHA-256). Gateway chỉ đọc và TÁI DÙNG
`CLIENT_TOOLS`/`_execute_client_tool` của `services/quote_chat.py` — client lấy danh sách tool động qua
`GET /tools` và ngữ cảnh chung (đưa vào `instructions` của MCP server) qua `GET /instructions` (`DESKTOP_SERVER_INSTRUCTIONS` trong `services/claude_connect.py`), nên thêm tool ở đó không cần cập nhật gói trên từng máy. Mô tả tool cho Desktop đi qua `adapt_tools_for_desktop` (thay cụm chỉ có ở prompt Jenny như "LỊCH THAM CHIẾU"); client 0.1.0 không gửi header `X-Carbon-MCP-Client` nên vẫn nhận mô tả kèm ghi chú ngữ cảnh lặp ở từng tool. Instructions có `DESKTOP_ROLE_RULES`: Desktop/mobile LUÔN trả lời dưới danh nghĩa 1 nhân viên trong `TEAM_INTRO` theo loại tác vụ, kể cả không gọi tool (Sonic: thu thập/tra cứu; Jenny: phân tích & đề xuất, mặc định; Jack: kiểm tra/QC + lên kế hoạch/action plan) — khối này CHỈ cho kênh MCP, Jenny chat web vẫn luôn là Jenny; MCP prompt `jenny` ("Trò chuyện với Jenny", `GET/POST /prompts`, `DESKTOP_PROMPTS` + `_JENNY_DESKTOP_PERSONA`) cho người dùng chủ động chọn để Claude đóng vai Jenny — persona là bản rút gọn khối GIAO TIẾP của `_build_static_instructions` (sửa bên đó thì xem lại bên này) và CỐ Ý không chứa khung phân tích EUA nội bộ (có test chặn). Chỉ role `user` (email+OTP),
không phải admin. Kết quả Claude sinh ra KHÔNG lưu ngược về hệ thống. `mcp_client` ghim `mcp<2`
(API decorator của `mcp` 1.x bị bỏ ở 2.x). Test: `python -m pytest tests` (pytest trần không thấy
package top-level vì repo chưa cấu hình pythonpath).
**MCP qua HTTP cho mobile** (`api/mcp_http.py`, mount tại `/mcp/{token}` trong `api/main.py`): MCP Streamable HTTP
stateless (`json_response=True`) cho app Claude mobile/claude.ai (Connectors — không đặt được header nên token `cat_...` nằm
trong URL, coi như mật khẩu; log truy cập của uvicorn/proxy sẽ chứa nó). Tái dùng `mcp_gateway.execute_gateway_tool` (cùng tool,
rate limit, quyền published-only) và `DESKTOP_SERVER_INSTRUCTIONS`. Stateless nên KHÔNG nhớ `report_date` của handoff như client
stdio — kết quả `get_handoff` dặn Claude tự truyền `report_date`. Lifespan phải chạy `session_manager().run()`. Test: `tests/test_mcp_http.py`.
Frontend: trang `/connect-claude` (`frontend/src/app/connect-claude/`, tạo/thu hồi token + sinh sẵn
config Claude Desktop), modal `ClaudeHandoffModal` mở từ nút "Hỏi Claude" cạnh "Hỏi AI" khi bôi đen
và từ menu avatar Jenny (`QuoteChat.tsx`; chỉ hiện với role `user`). File cài MCP client được web phục
vụ tại `/downloads/carbon_analyst_mcp-<ver>-py3-none-any.whl` (`frontend/public/downloads/`): phát hành
bản mới = tăng version ở `mcp_client/pyproject.toml` + `MCP_CLIENT_VERSION` trong
`frontend/src/lib/claudeConnect.ts`, rồi chạy `scripts/build_mcp_client.sh`. Tên file wheel BẮT BUỘC có
phiên bản hợp lệ (uv đọc phiên bản từ tên file) — không dùng kiểu `-latest.whl`.

**Jenny chat — token**: prompt tĩnh (`_build_static_instructions`) chỉ giữ INFERENCE_RULES + FUEL_SWITCHING + CONFLICT_RESOLUTION; các khối khung EUA khác (`FRAMEWORK_ON_DEMAND_BLOCKS`) model tra qua tool `get_eua_framework` (`FRAMEWORK_TOOL`, CỐ Ý ngoài `CLIENT_TOOLS` nên MCP gateway không bao giờ phục vụ — có test). Không liệt kê lại tool trong prompt (mô tả nằm ở chính tool). Cache: system tĩnh TTL theo `QUOTE_CHAT_CACHE_TTL` (1h|5m — chọn bằng `python -m scripts.analyze_chat_cache_ttl`), lượt lịch sử cuối, và `cache_control` cấp request cho vòng lặp tool. Báo cáo ngày KHÔNG cache system (mỗi prompt chỉ dùng 1 lần/ngày).

**Bài học từ góp ý người đọc** (`services/report_lessons.py`, bảng `report_lessons`): Jenny là người viết báo cáo — bị góp ý
thì kiểm tra bằng tool trước, CHỈ khi sai/thiếu THẬT mới gọi `record_report_lesson` (`LESSON_TOOL`, CỐ Ý ngoài `CLIENT_TOOLS` —
MCP chỉ đọc, có test). `generate_report_content` nạp bài học còn hiệu lực (`LESSON_MEMORY_DAYS`, về báo cáo trước ngày đang
sinh) và `_process_section` nối `lessons_prompt_block` (bài học của mục đó + "general") vào user prompt từng mục. Tắt bài
học sai: `python -m scripts.report_lessons --dismiss ID`. Đoạn chat tham khảo admin chọn (`quote_chat_examples`, tối đa
`MAX_INJECTED_EXAMPLES` mới nhất) vừa vào system prompt Jenny chat, vừa được `build_report_reference_block` nối vào CUỐI
system prompt từng mục báo cáo (chỉ học cách suy luận, không chép số, không theo định dạng chat).

**Tài liệu tri thức (.pdf/.docx)** (`services/knowledge.py`, bảng `knowledge_documents`, migration `e8b4d2f6a1c9` — revision
này GỘP 2 head cũ `a4c7e2f9b1d5`/`a9e4c7b2d5f1`): admin tải lên ở `/admin/knowledge` (`api/routers/admin_knowledge.py`; presigned PUT
lên MinIO prefix `knowledge/` — `GET /api/upload/view-url` từ chối prefix này) → `create_document` kiểm file THẬT (dung lượng,
magic bytes, sha256 trùng → 409) → job nền (`BackgroundTasks`, kẹt khi restart → lifespan `recover_stuck_documents` chuyển
`failed`) trích chữ bằng `services/knowledge_extraction.py` (hàm thuần; DOCX đúng thứ tự đoạn+bảng, heading → đề mục; PDF bằng
`pdfplumber`, bảng → "ô | ô", bỏ header/footer lặp; NFC; chặn zip bomb/macro; PDF scan CHƯA có OCR → ≥50% trang không lớp chữ =
lỗi) trong process riêng có timeout (`_extract_isolated`) → `chunk_document` (không vượt đề mục, `chunks.meta` = trang/đề mục)
→ embed theo lô 96 (Cohere `embed-v4.0`, `VECTOR_DIMENSION`=1536 — embedder/chunker/retrieval thật nằm ở `services/`, không
phải FastEmbed 384 như đoạn Architecture cũ) → `chunks.source_type='knowledge'`. Truy hồi `RetrievalService.retrieve_knowledge`:
index MỘT PHẦN riêng (HNSW + GIN `to_tsvector('simple', …)`, SQL phải ghi literal `source_type = 'knowledge'`), rerank ngưỡng
`MIN_KNOWLEDGE_SCORE`, ≤`MAX_HITS_PER_DOC` đoạn/tài liệu, lọc `is_active`/hiệu lực (`valid_from/valid_to` so ngày dữ liệu báo
cáo, chat = hôm nay)/phạm vi (`use_in_report` + `report_sections`, `use_in_chat`) — giữ khớp `is_applicable`. Báo cáo:
`load_report_knowledge` (không có tài liệu → không gọi API; lỗi → {} trong SAVEPOINT) → `knowledge_prompt_block` nối cuối user
prompt mục (sau bài học), trích bằng `[TLk]` (Mục 3, `_CITE_RE`; watchpoint chỉ có TL bị xoá) hoặc `"source_index": "TLk"` (Mục
1/4/biz → `knowledge_ref` {chunk_id}); dev/2/8 chỉ làm bối cảnh. `content["knowledge_used"]` = đoạn đã đưa vào prompt; Mục 9 thêm
dòng `kind='knowledge'` không link. Jack: `check_facts` đối chiếu số với chữ đúng các đoạn đó, `check_knowledge_refs` (tài liệu
đã xoá/tắt), Mục 9 knowledge không báo "thiếu URL"; Claude QC đọc `get_qc_assignment(part="knowledge")`. Chat: tool
`search_knowledge` (`KNOWLEDGE_TOOL`, CỐ Ý ngoài `CLIENT_TOOLS` → MCP không phục vụ, có test) chỉ khai báo khi có tài liệu bật cho
chat + danh mục ngắn trong prompt tĩnh (`build_chat_index_block`). `knowledge_used`/`knowledge_ref` nằm trong `SKIP_KEYS`
(tự sửa) và `UNTRANSLATED_SUBTREES` (dịch). Test: `tests/test_knowledge_extraction.py`, `tests/test_knowledge.py`.

**Jack QC báo cáo** (`services/report_qc.py`): tool `jack_qc`: trong Jenny chat web CHỈ bật cho admin
(`ADMIN_TOOLS` trong `services/quote_chat.py`; `_execute_client_tool(..., allow_admin_tools=...)` chặn thêm ở tầng
thực thi, admin QC được cả bản draft). Trên MCP/Claude Desktop, gateway thêm riêng `JACK_QC_DESKTOP_TOOL`
(`services/claude_connect.py`, mô tả bảo Claude hoá vai Jack) cho mọi user có token và gọi với `allow_jack_qc=True` →
chỉ QC báo cáo `published` (`qc_report_text(..., published_only=True)`). Khi gọi, UI hiện trạng thái
"Đang giao cho Jack QC báo cáo — Jack đang đối chiếu dữ liệu trên hệ thống..."; người dùng gọi tên "Jack" là Jenny gọi tool (xem mô tả tool) (`JACK_TOOLS` → `StatusEvent`). Toàn bộ là Python
rule-based, KHÔNG gọi LLM: giá Mục 2 ↔ bảng `prices`, kịch bản Mục 3 ↔ nhãn "**Tổng hợp:**" + số học (R:R ≥ 1,5,
điều kiện ↔ Entry, cắt lỗ sát đáy/đỉnh phiên — `_check_strategy_math`; bộ sinh dùng lại check này để tự sửa Mục 3 1 lần:
`_revise_scenarios_if_invalid`), URL nguồn Mục 1/4/9 ↔ `articles` (+ tin cũ hơn khung `STALE_NEWS_MAX_AGE_DAYS`, nhãn nguồn ≠
nhà xuất bản thật `news_source_label`), lịch EIA/Baker Hughes Mục 8 ↔ `_compute_recurring_calendar_events` (cửa sổ
`CALENDAR_WINDOW_DAYS`=7, lùi lịch theo ngày nghỉ lễ liên bang Mỹ; số liệu phải đúng kỳ — `data_matches_event`), gợi ý biz ↔
`biz_suggestions.trigger_rule` (+ gợi ý cũ đánh dấu ĐÃ KÍCH HOẠT phải chạm ngưỡng thật; ngưỡng giá thiếu rule thì tự dựng
bằng `biz_memory.infer_price_rule`, mã không có trong bảng giá thì không bao giờ kích hoạt qua tin tức; gợi ý mới phải có nguồn nằm trong Mục 6/9 — `check_biz_sources`, ngưỡng giá được miễn), độ phủ tin tức ↔ bài crawl trong khung tin của báo cáo (cùng khung với
`get_news_for_report`), số liệu ↔ tin gốc (`check_facts`, hạng mục `fact`: mọi số trong câu có trích nguồn ở Mục 1/Diễn biến chính/Mục 4/yếu tố thị trường phải có trong `articles.content` của chính bài đó — miễn năm, ngày, số đếm ≤10, số của bảng giá/khối CBAM; bullet tự ghi "chưa khớp" cũng bị báo), số học "đã dùng X/Y … còn Z" + "còn Z" lệch bài khác ở Mục 6 (`_check_remaining_math`/`_check_remaining_vs_section6` — bộ sinh dùng lại để tự sửa bullet Mục 1/Diễn biến chính sau khi Mục 6 tóm tắt xong: `_revise_figure_conflicts`), lệnh 2 chiều phải đủ mục tiêu + cắt lỗ từng chân (`_check_two_sided_legs`, gộp vào hạng mục kịch bản → bộ sinh tự sửa qua `_revise_scenarios_if_invalid`), "API2 tăng" xếp vào yếu tố hỗ trợ tăng giá là sai chiều (`check_fuel_direction`), nhãn mã ↔ giá thật (`check_instrument_labels`, gộp vào hạng mục giá: số gắn nhãn mã X mà là giá đóng cửa của mã Y → error; nêu giá mã không có trong bảng giá → warning). Điểm mỗi check = 100 − 25/error − 10/warning − 2/info; tổng = trung bình 7 check.
`qc_report_text()` trả văn bản (điểm, thống kê tin, vấn đề theo mục) làm tool_result. Các hàm `check_*` là
hàm thuần — test ở `tests/test_report_qc.py` (fixture `GOLDEN` = báo cáo sạch đúng định dạng bộ sinh, PHẢI ra
0 vấn đề — đổi định dạng output của `report_generator.py` thì cập nhật fixture này). `parse_strategy` /
`parse_signal_levels` (nằm ở `services/strategy_levels.py`, `report_qc` re-export) là bản port 1-1 parser thẻ KHUYẾN NGHỊ VỊ THẾ (trước là TÍN HIỆU HÔM NAY) trong `ReportDocument.tsx` — sửa bên nào
phải sửa cả bên kia (cả `is_breakout_entry` ↔ `isBreakoutEntry`). Chỉ số EUA (Δ lấy từ dòng EUA của bảng giá; biên 30 phiên/ATR 14
phiên/vị trí trong biên tính lại từ `chart_data` đúng công thức `report_consistency` — `computeEuaStats`; track record từ
`forecast_review.items`) nằm ở khối mở đầu (bố cục 24/10/2026: thanh menu gọn + số liệu chính chạy dọc `PriceTicker` [giá CBAM ước tính, TTF, API2, điện Đức, Brent]; dải "Tin nóng" `HotNewsStrip` = `GET /api/news/hot?report_date=` [tin is_hot_news ngày báo cáo + ngày trước], lần lượt đổi tin, không có tin thì ẩn; rồi `HeroSummary` [Tóm tắt điều hành — prompt Mục 1 giữ CẢ MỤC 60–90 chữ] | `HeroVerdict` [Nhận định tổng quan + giá EUA + biên/ATR/KL] | cột `HeroOrders`/`HeroHedge`/`HeroForecast`; chia cột theo `HERO_SPANS`). Thẻ
(`PositionCard`) gộp CẢ 3 kịch bản: thang giá, thẻ từng khung (`HorizonCards` — thay cả bảng lệnh lẫn bảng "Kịch bản hành động" cũ: chiều,
xác suất, vùng giá, lệnh R:R tính từ điểm giữa vùng Entry / lệnh phá vỡ từ mức kích hoạt, chuẩn `rules.min_rr`, điều kiện, rủi ro, nguyên văn
chiến lược) và thanh phòng hộ CBAM dựng từ `cbam_estimate` (không đọc câu `hedge_action.core`). Xem bản tiếng Anh: mức giá/loại lệnh vẫn
tách từ kịch bản tiếng Việt (`content_vi` do `lib/reportLocale.ts` giữ lại), câu chữ lấy bản Anh. Bố cục nhiều cột của trang báo cáo theo
container query `@container/report` (bề rộng thật của báo cáo, không theo viewport); in A4 (~650px) tự về 1 cột — thêm khối mới thì giữ quy
ước này và `print:` cho phần thu gọn. **Xuất PDF** (popup `PdfExportModal.tsx` ở nút "Tải PDF" trang user + admin; KHÔNG dùng hộp thoại in của trình duyệt): người dùng chọn thiết bị
(mobile/pc/tv — tự gợi ý khổ) × khổ (A4/vuông/16:9) × hướng, popup tự render + hiện bản xem trước (`PdfPagesPreview.tsx`, pdf.js vẽ từng trang ra
canvas — không dùng iframe vì Chrome Android/Safari iOS không hiện PDF nhúng; cache theo tổ hợp, "Tải xuống" lưu đúng file đang xem) → `GET /api/reports/{date}/pdf` (published) hoặc
`/api/admin/reports/{date}/pdf` (cả nháp) (`api/routers/report_pdf.py`) → `services/report_pdf.py::render_report_pdf` (Chromium,
tối đa 2 bản cùng lúc) mở trang `/reports/[date]/export` với khổ/lề/zoom do backend tính (`PdfLayout`, `PAGE_SIZES`/`DEVICE_SCALES` —
nguồn DUY NHẤT; popup chỉ vẽ xem trước). Trang export đặt `@page`, ép khung báo cáo = bề rộng vùng in / zoom (container query dàn lại
cột), chờ media print + font, gọi `tunePrintBreaks(shell, chiều cao trang)` rồi gắn `data-pdf-ready` (`data-pdf-error` nếu không tải
được). PDF email publish đi cùng đường này (mặc định A4 dọc, pc). Class `print:` ép cột chỉ áp ở khung 576–767px
(`print:@xl/report:@max-3xl/report:`) để khổ hẹp (điện thoại) về 1 cột, khổ rộng theo bố cục màn hình; hàng bảng chỉ
`print:break-after-avoid` khi thật có hàng chi tiết đi kèm (không thì cả bảng thành 1 khối bị đẩy sang trang sau). APP_BASE_URL phải trỏ được
tới frontend từ backend. Ngắt trang PDF: `lib/usePrintBreakTuning.ts` đo khối/thẻ theo khổ in lúc in — khối ≤ 18% trang giữ trọn (`data-print-keep`), khối lớn hơn (kể cả đang `print:break-inside-avoid`) được ngắt giữa các phần con (`data-print-split`), để không đẩy nguyên khối lớn sang trang sau; `data-print-whole` = luôn giữ trọn (biểu đồ nến, biểu đồ dự báo, tiêu đề Phần 3 + tin đầu). Khi in: lưới thẻ 2 cột (tóm tắt, động lực, nhóm phân tích, cần theo dõi, diễn biến | lịch) chảy dạng khối 1 cột (grid không ngắt trang được giữa hàng), bảng ETS in dạng thẻ, khung bị ngắt tự lặp viền (`box-decoration-break: clone`), bản EN dùng `@page report-en` (đầu/chân trang tiếng Anh). Khối đứng cùng hàng phải cao bằng nhau (yêu cầu 22/10/2026): lưới để mặc định stretch, không thêm
`items-start`; cặp khối chữ dài dùng khung `PANEL`; danh sách thẻ (nhóm Phân tích, Cần theo dõi, tin Phần 3) xếp lưới 2 cột theo hàng
thay vì 2 cột chảy độc lập; Phần 1: biểu đồ nến EUA | 5 ô tóm tắt xếp dọc (giãn theo chiều cao biểu đồ), Bảng giá nhanh dạng gọn | biểu đồ chỉ số giá (`PriceIndexPanel` tự giãn bằng bảng) — ngang từ khung ≥ 768px (`@3xl/report`). Mục 1 bullet không có nguồn là HỢP LỆ (prompt cho phép), không báo lỗi. Bảng `report_qc_results` (bản nút bấm cũ) không còn được ghi,
giữ lại để không phá chuỗi migration.

**Biên bản QC Claude (MCP) + tự xuất bản** (`services/report_qc_review.py`, bảng `report_qc_reviews` + `report_qc_findings`,
migration `a9e4c7b2d5f1`): 07:00 sinh nháp → 07:30 lịch Claude Jack QC (routine NGOÀI repo — giờ chạy đặt ở Claude, prompt `QC_ROUTINE_PROMPT` / MCP prompt
`jack_qc_review`) dùng token `api_tokens.scope='qc_agent'` (admin tạo ở `/admin/qc-results`, không gắn bảng `users`, hạn
`QC_AGENT_TOKEN_TTL_DAYS`) gọi `get_qc_assignment` (phiếu giao + `assignment_id` = `content_hash(content)` + Jack rule-based +
lỗi biên bản trước; `part=` đọc JSON bản nháp) rồi `submit_qc_review` (prompt dặn gửi trước 07:55) → 08:00
`scheduler.run_auto_qc_fix_job`: Jenny tự sửa = chạy đúng luồng nút "Sửa báo cáo theo QC" (`report_qc_fix.auto_fix_from_review`,
mọi lỗi `open`, chỉ áp thay đổi `selected`, actor "Jenny (tự động)") rồi dịch lại EN; BỎ QUA khi nội dung lệch
`review.content_hash` (= admin đã sửa tay/sửa theo QC sau lần QC — `auto_fix_skip_reason`) → 08:15 `run_auto_publish_job` xuất bản +
gửi email (chung `_REPORT_FINALIZE_LOCK` với job sửa — chờ sửa xong). Cổng `publish_gate`/`gate_decision` (quyết định 23/10/2026):
chưa có biên bản → xuất bản (C2); `fail` → chặn, TRỪ KHI đã có lỗi `fixed`, không còn lỗi critical/major `open` (`wont_fix` =
admin bỏ qua) VÀ điểm sau sửa (`score_after_fixes`) ≥ `FAIL_SCORE`; admin duyệt tay không bị chặn. Server KHÔNG tin số Claude: `overall_score` = Σ điểm × trọng số `QC_CRITERIA` (7 tiêu chí, tổng 100),
thang chấm Claude theo `SCORING_GUIDE` (khoan dung, 23/10/2026: CHỈ sai rõ ràng về logic/số liệu mới là lỗi, mỗi tiêu chí bắt đầu từ 9,0; phần còn THIẾU không nêu, không trừ), `decide_verdict` chỉ nghiêm hơn Claude (điểm < `FAIL_SCORE` hoặc critical đã kiểm chứng → fail); `assignment_id` lệch nội dung
hiện tại → từ chối; lỗi định dạng trả văn bản liệt kê đủ để Claude gửi lại. `fingerprint` lỗi → `repeat_count` trên
`REPEAT_LOOKBACK_REPORTS` báo cáo trước. Token qc_agent: tool QC chỉ hiện với scope này (gateway `tools_for_token`, MCP HTTP
list_tools theo token), tool neo ngày được đọc bản nháp, jack_qc chạy trên nháp; đây là đường GHI duy nhất của gateway. Chỉ
admin xem: `api/routers/admin_qc_reviews.py`, tool Jenny `get_qc_review` (ADMIN_TOOLS). Frontend: nút "Kết quả QC" + panel
`QcReviewPanel` ở `/admin/reports/[date]` CHỈ khi status `draft`; trang tổng hợp `/admin/qc-results` (+ `[id]`, quản lý token,
prompt routine). Test: `tests/test_report_qc_review.py`.
**Sửa báo cáo theo QC** (`services/report_qc_fix.py`, nút trong `QcReviewPanel` → `QcFixModal.tsx`, chỉ bản nháp, ẩn khi
đang sửa tay): `POST .../report/{date}/fix-preview` KHÔNG ghi gì — Python neo từng lỗi vào đúng chuỗi văn xuôi qua
`field_path` + nguyên văn `current_text` (bỏ `SKIP_KEYS`, tiêu đề gốc Mục 6; không neo được/mơ hồ → "cần sửa tay", không
gửi LLM), LLM (`FIX_MODEL`) chỉ trả cặp `{old → new}` trong các chuỗi được giao, `validate_edits` loại thay thế không khớp
đúng 1 lần/chồng nhau/đụng "(Nguồn: …)"/URL/số mới không có trong biên bản hoặc đoạn gốc; rồi `apply_consistency_fixes`
chỉ trên đoạn vừa sửa + Jack trước/sau (đoạn phát sinh lỗi Jack mới bị bỏ chọn sẵn). `POST .../fix-apply` ghi khi
`base_hash` và `before` từng đoạn còn khớp, đánh dấu lỗi `fixed`, dịch lại bản EN; `undo=true` = áp ngược, lỗi về `open`.
Biên bản cũ giữ verdict; fail hết chặn khi lỗi critical/major đã sửa hết (xem `gate_decision`). Điểm sau sửa (`score_after_fixes`, tính lúc đọc, không ghi DB): điểm bị trừ của mỗi tiêu chí (10 − điểm) chia cho lỗi cùng tiêu chí theo trọng số mức độ 3/2/1, lỗi `fixed` được cộng lại phần của mình; `overall_score` gốc giữ nguyên — panel/trang tổng hợp hiện cả 2. Test: `tests/test_report_qc_fix.py`.

**Tự sửa số học/trình bày** (`services/report_consistency.py`, hàm thuần): cuối `generate_report_content` chạy
`apply_consistency_fixes` trên toàn bộ văn xuôi (bỏ qua `SKIP_KEYS`): tính lại nhãn "X (±p% so với đóng cửa Y)" (và
"±p% ≈ X" trong kịch bản EUA) → sửa nhãn, giữ mức giá; sửa "nửa trên/dưới biên độ A–B", "X (giữa biên)" theo vị trí thật;
gộp vùng độ rộng 0, đổi "85.84"→"85,84", "5 October 2026"→"05/10/2026". Jack dùng lại qua `check_consistency`. Prompt
nhận sẵn vị trí trong biên 30 phiên (`describe_range_position`) và hình dạng phiên (`_eua_session_shape`: biên độ so ATR,
gap, một chiều vs đảo chiều). Mục 3 trả 1 `hedge_action` DUY NHẤT (`buy_now|buy_scheduled|hold|defer` + text, đối chiếu
thẻ CBAM) thay cho `hedge_note` từng kịch bản (bị xoá khi sinh; báo cáo cũ vẫn hiển thị), và `open_positions` = lệnh
mô phỏng đang mở (`forecast_review.open_trades`) + quyết định `keep|move|close` của LLM (`merge_open_positions`) — Jack
`check_hedge_action`. Tin Mục 3 đánh số [N]; watchpoint "Cần theo dõi" không có `[N]`/`[giá]` bị xoá
(`_resolve_section3_citations`), nguồn đã trích vào `content["3"].sources` → Mục 9. Mục 6 chia VIỆT NAM/QUỐC TẾ theo
NƠI ĐĂNG (region của nguồn crawl), không theo chủ đề bài (quyết định 18/10/2026). Δ tuyệt đối ở bảng giá tính từ giá đóng cửa kỳ
so sánh trong DB (`_comparison_closes`), không suy ngược từ % đã làm tròn.
Cũng tự sửa/kiểm: số phiên tăng/giảm liên tiếp của EUA (`eua_streak`/`fix_streak_claims`; prompt nhận khối "CHUỖI PHIÊN"
kèm khối lượng TỪNG phiên trong nhịp so TB 20 phiên — không kết luận cả nhịp chỉ từ phiên cuối), mục tiêu dạng
"X (Y × f)" (cơ sở Y phải là giá đóng cửa/mốc kỹ thuật; price_zone và Mục tiêu cùng kịch bản phải trùng —
`derived_target_issues`, đi vào vòng `_revise_scenarios_if_invalid`). "Diễn biến chính" chạy TRƯỚC Mục 2/3, chiều của
từng tin được chốt và đưa vào prompt 2/3 (`_dev_verdict_block`); động lực Mục 2 trái chiều cùng bài bị bỏ
(`_drop_conflicting_drivers`) — Jack `check_dev_driver_consistency`.
**Fuel switching** (`fuel_spread_state`, dùng chung prompt + Jack `check_fuel_direction`): chiều xếp theo THAY ĐỔI RÒNG
chênh khí − than, ĐỐI XỨNG — khi than đã rẻ hơn khí rõ rệt (> `FUEL_NEAR_BREAKEVEN`) cả "TTF tăng" lẫn "API2 tăng" đều
trung lập; giả định mô hình hiển thị cho người đọc ở `content["3"].fuel_model_note`. Crack spread gasoil kèm mức tuyệt
đối + TB 60 phiên (`_crack_spread_history`) để không suy "biên giảm → hạ công suất" khi biên vẫn rất cao.
"Diễn biến chính" `impact` = chiều RÒNG lên EUA (không phải hợp đồng năng lượng), prompt nhận khối fuel switching; Jack báo
tiền đề "chuyển sang than → EUA tăng" khi đã bão hòa.
Từ 20/10/2026: khung `eua_causal_chains.FUEL_SWITCHING` có bước (0) BẮT BUỘC xác định VÙNG (bão hòa / sát hòa vốn / khí rẻ hơn)
trước nhánh (1)/(2)/(5) — POWER_EUA_TWO_WAY (a), OIL_GASOIL (1), GEOPOLITICS (a), WEATHER, RELATIVE_FUEL_ECONOMICS,
CONFLICT_RESOLUTION đều điều kiện theo vùng (hedge theo SẢN LƯỢNG, giá điện không quyết định nhiên liệu đốt). Jenny chat nhận
vùng qua `fuel_regime_brief` (dòng "TRẠNG THÁI FUEL SWITCHING" trong get_market_prices + get_report_section Mục 2). Jack
`check_coal_switch_premise` quét Mục 1/Phân tích/instrument_notes/kịch bản/biz (Mục 3 đi vào vòng tự sửa); `check_fuel_direction`
thêm "điện Đức tăng" (không do RES/phụ tải) và "than cạnh tranh hơn" ở cột tăng. Test: `tests/test_fuel_switching_saturation.py`. Prompt Mục 2/3 nhận kết quả đấu giá EEX gần nhất
(`_latest_auction_summary`). ATR = TB True Range 14 phiên (`report_consistency.atr_true_range`, dùng chung bộ sinh + Jack).
Mốc kỹ thuật có cả mục tiêu đo biên độ chiều xuống. Jack thêm `check_scenario_basis` (trung/dài hạn có chiều chỉ dựa ngưỡng
chưa xảy ra khi Tổng hợp TRUNG LẬP) và `check_horizon_overlap` (kích hoạt trung hạn nằm trong cắt lỗ ngắn hạn) — cả 2 đi vào
vòng tự sửa kịch bản; `derived_target_issues` báo nhãn % tính từ đóng cửa khi Entry là mức kích hoạt khác. Lịch Mục 8: sự
kiện định kỳ mang `released_at` (giờ VN) — gắn kết quả nếu đã công bố trước lúc sinh báo cáo; giờ đấu giá EEX quy đổi giờ VN
theo Europe/Berlin. `check_facts` chấp nhận số tự tính khi phép tính a/b, a×b hiện ngay trong câu. Cụm "(Nguồn: …)" luôn tách bằng `report_consistency.inline_source_spans` (đếm ngoặc cân bằng — tên hồ sơ như "COM(2026) 616" có ngoặc; KHÔNG dùng regex `[^)]+`); `check_facts` bỏ số trong mã văn bản (`LEGAL_DOC_ID_RE`) và đối chiếu số câu trích hồ sơ với chính nội dung hồ sơ yaml.
**Phòng vệ CBAM — 1 nhãn**: `cbam_estimate.signal` (increase/hold/reduce) là nhãn DUY NHẤT, dùng chung thẻ CBAM và
`content["3"].hedge_action` (`_finalize_hedge_fields` ghi đè nhãn LLM). Phần LÕI (≥ 100 − `TACTICAL_MAX_PCT`% phần chưa
chốt) luôn bám lịch đấu giá (chi phí bám VWAP = giá CBAM, `hedge_core_text`); vùng giá/lệnh giới hạn chỉ cho phần chiến
thuật ≤20%. SIM chịu CBAM gián tiếp (`CBAM_DECLARANT_RULE`: bên mua chứng chỉ là declarant EU). Jack thêm
`check_breakout_levels` (lệnh phá biên 30 phiên: mục tiêu đo biên độ, cắt lỗ 0,5–1,5 ATR), kịch bản "đi ngang" mà chiến
lược 1 chiều phá vỡ bị báo + kiểm số học theo chiều thật, `check_legislation_claims`, `check_fact_tags`. Fuel switching là
suy luận MÔ HÌNH (không có dữ liệu sản lượng điện) + độ nhạy TTF (`ttf_breakeven`, `spread_per_10pct_ttf`); crack spread và
tỷ lệ đặt mua/chào bán đấu giá chỉ so với lịch sử của chính hệ thống. Mục 6 luôn giữ bài đã trích ở mục khác. Chấm dự báo:
mục tiêu tới trước khi khớp → `direction_only` (đếm riêng, không vào tỷ lệ đúng).

**Sửa theo bản chấm 17/10/2026** (test: `tests/test_report_review_1710.py`):
- Fuel switching: `fuel_fleet_thresholds` — dải giá EUA hòa vốn theo hiệu suất đội nhà máy (`FUEL_FLEET_GAS_EFF`/`_COAL_EFF`) +
  NGƯỠNG THẬT (mức TTF/API2 làm đầu thấp dải = giá EUA). Còn xa (`fuel_threshold_far`) thì cấm điều kiện/watchpoint
  "chênh ±X EUR/MWh" — Jack `check_fuel_thresholds` (phần kịch bản đi vào vòng tự sửa).
- Kỹ thuật (`report_consistency`): `streak_momentum` + `fix_momentum_claims` (sửa "mức giảm thu hẹp dần" sai mẫu hình),
  `session_signals` (cạn lực = biên ≤0,8 ATR VÀ KL ≤−20%; gap < `GAP_MIN_ATR` ATR là nhiễu) — Jack `check_technical_claims`;
  40–60% biên = "vùng giữa", không gọi nửa trên/dưới; `fix_stray_quotes`; tiêu đề gốc Mục 6 không đổi dấu thập phân.
- Khối "Theo dõi chính sách ETS/MSR": `get_ets_policy_news` quét tin chính sách `ETS_POLICY_LOOKBACK_DAYS` (21) ngày, tiếp
  số [N] của Mục 3, LLM trả `content["3"].ets_policy` (frontend `EtsPolicyTable`; Jack không báo "tin cũ" cho nguồn khối này).
- Mục 8: COT EUA ICE Endex (Thứ Tư) + `policy_calendar.yaml` (admin ghi ngày họp chính sách ĐÃ xác nhận); sự kiện đã tới giờ
  công bố mà không lấy được số → `RELEASED_NO_DATA_OUTCOME` (không còn nằm ở "sắp tới").
- Đấu giá: tỷ lệ đặt mua/chào bán so TB 90 ngày của CÙNG nền tảng; không suy cầu từ chênh giá khớp spot ↔ futures Dec.
- Phòng hộ CBAM: lõi = MUA futures Dec ngay rồi BÁN rải theo lịch đấu giá còn lại (`hedge_structure_text`; mua EUA rải theo
  lịch KHÔNG khóa được gì — Jack `_SCHEDULE_BUY_RE`); % lõi ghi theo khối lượng quý (`core_share_of_quarter_pct`);
  `product_exposure` = chi phí CBAM/tấn sản phẩm theo GIẢ ĐỊNH `CBAM_PRODUCT_ASSUMPTIONS` × `CBAM_FREE_ALLOCATION_FACTOR`.
- Kịch bản: `_enforce_scenario_rules` (đi ngang + lệnh phá biên → đổi chiều, xác suất Thấp; chưa có dữ liệu vị thế quỹ
  `POSITIONING_DATA_AVAILABLE=False` → tối đa "Trung bình"); Jack `check_long_term_basis` (dài hạn không dùng đo biên độ 30
  phiên/giá đóng cửa làm cận).
- Gợi ý SIM: `METALS_LENS_RULE` + trường `kpi/owner` (ngắn hạn), `pilot/financials/kpi/owner` (dài hạn); Jack `BIZ_OFF_LENS`.
  Nguồn: Jack `check_inline_attribution` (tên hãng tin trong câu ≠ bài trích, truyền thông nhà nước).
- Lần chấm 2 (test `tests/test_report_review_1710_v2.py`): `ets_policy_dossiers.yaml` = hồ sơ ETS/MSR admin xác nhận, luôn đứng
  đầu bảng chính sách và trích bằng `[HSk]`; "Cần theo dõi" tối đa `WATCH_MAX_ITEMS`, bỏ watchpoint lặp cùng nguồn; lịch đấu giá
  đã áp dụng không vào bảng chính sách. Nhãn "Tổng hợp" phải khớp kịch bản ngắn hạn (Jack + vòng tự sửa). Phần chiến thuật CBAM
  do `hedge_tactical_text` chốt ("hold" = không dùng; cấm so với ước tính giá CBAM — vòng). "KÊNH TTF → EUA DÙNG CHUNG" khi bão
  hòa (Jack `check_ttf_channel`). Dài hạn cấm mục tiêu % tròn so với đóng cửa (`LONG_TERM_ROUND_PCT_RE`, tự thay bằng "chưa đủ cơ
  sở định lượng"). Bảng chấm hiện ngày các báo cáo trong cửa sổ (`calibration_window`) + tỷ lệ lũy kế (`calibration_all`).
  Mục 6: bài đã trích ở mục khác chỉ hiện tiêu đề + link (`cited_elsewhere`); `vietnam_considered`. TASS/RIA... bị bỏ khỏi
  câu (`strip_state_media`) và khỏi chuỗi nguồn gốc.
- Lần chấm 3: `cbam_products.yaml` (benchmark CBAM theo mã CN — QĐ thực thi (EU) 2025/2620, CSCF, markup giá trị mặc định,
  ví dụ giá trị mặc định theo xuất xứ; số đang từ nguồn thứ cấp, `benchmark_verified: false`) → `product_exposure` khấu trừ =
  hệ số CBAM × CSCF × benchmark + kịch bản giá trị mặc định. Phòng hộ ghi "% nghĩa vụ CBAM quý", hợp đồng Dec-yy + phiên đấu giá
  cuối quý (`last_auction_date`). Hồ sơ yaml có `cbam_effect`/`structural` (dài hạn phải 2 nhánh; gợi ý SIM nhận
  `_biz_policy_block`, trích nguồn "HSk" → `source_ref`). `policy_calendar.yaml` hỗ trợ `outcome`; Mục 8 bỏ bản LLM trùng họp
  đã có. TB cover ratio 90 ngày loại chính phiên đang so. Bảng chấm ghi Đ/S từng báo cáo + `calibration_drift`. Cắt cứng: Tóm
  tắt ≤ `EXEC_SUMMARY_MAX_BULLETS`, Diễn biến chính ≤ `KEY_DEV_MAX_ITEMS`, Cần theo dõi ≤ `WATCH_MAX_ITEMS` (4).
- Lần chấm 4 (test `tests/test_report_review_1710_v3.py`): hồ sơ yaml có `kind` (event = sự kiện, không chiều riêng),
  `mechanism`, `verified`; prompt có khối "CƠ CHẾ MSR" (nâng ngưỡng vô hiệu hóa / giảm chuyển vào MSR = NỚI cung) + Jack
  `check_msr_direction`. Bảng chính sách hiển thị "Tác động lên giá EUA". Mẫu số lũy kế = báo cáo có kết luận Đúng/Sai
  (`calibration_all_excluded` tách riêng), chấm lại toàn bộ theo quy tắc từ `SCORING_METHOD_SINCE`. Thẻ "Dự báo trước" CHỈ hiện
  bảng + "Nguyên nhân & điều chỉnh" (tỷ lệ đúng theo xác suất — cả cửa sổ 7 báo cáo, bỏ hiển thị 23/10/2026 — lũy kế/chuẩn so sánh/quy tắc vẫn tính, chỉ dùng trong prompt).
  Giá trị mặc định CBAM áp theo nước xuất xứ (số TQ/UAE = minh họa; `vn: true` khi admin thêm số VN). `futures_expiry` trong
  cbam_products.yaml chốt câu đáo hạn Dec. Fuel: không có ngưỡng TTF chiều lên (Jack `_UPWARD_THRESHOLD_RE`). Mục 1 nhận
  `_exec_policy_block` (bắt buộc bullet "Chính sách ETS"). `_trim_section3_blocks` bỏ "Quan điểm thị trường" chỉ lặp nguồn bảng
  chính sách + gạch đầu dòng thừa. Mục 6 luôn có tóm tắt; bài nguồn VN về chính sách carbon VN/CBAM luôn giữ
  (`_section6_must_keep`). Jack `check_facts` kiểm cả số trong Mục 3 với bài nguồn (`content["3"].sources`).
- `frontend/messages/parts/*.json` đang CŨ hơn `vi.json`/`en.json` (đã sửa thẳng file gộp) — KHÔNG chạy
  `scripts/merge-messages.mjs` khi chưa đồng bộ parts, sẽ mất key.

**Dự báo trước: đúng hay sai** (`services/forecast_review.py`): khối cố định ngay sau Phần 3 — Chi tiết tin tức, là phần 1 của mục
"Review dự báo và khuyến nghị" (`#section-forecast-review`; phần 2 = "Jenny cập nhật đề xuất trước đây" = `content["biz"].reminders`, chuyển từ
khối Gợi ý kinh doanh xuống đây ngày 21/10/2026) (`content["2"].forecast_review`,
frontend `ForecastReviewCard.tsx`, đoạn `content["3"].vs_previous` nằm bên trong làm "Nguyên nhân & điều chỉnh"). Python
chấm `REVIEW_MAX_REPORTS` báo cáo gần nhất có kịch bản ngắn hạn (còn trong `REVIEW_MAX_SESSIONS` phiên): %Δ EUA luỹ kế từ giá
báo cáo đó in + mô phỏng lệnh của thẻ chiến lược (khớp khi biên độ phiên chạm vùng Entry, lệnh giới hạn mở gap qua vùng khớp tại giá mở;
phiên khớp chỉ xét giá đóng cửa; 1 phiên chạm cả cắt lỗ lẫn mục tiêu → tính cắt lỗ) → chạm cắt lỗ = SAI, chạm mục tiêu =
ĐÚNG, còn lại theo chiều %Δ — chưa hết `REVIEW_MAX_SESSIONS` phiên thì chỉ là `on_track`/`diverging`. Lệnh chờ hết hiệu
lực sau `ORDER_VALID_SESSIONS` phiên (`expired`); giá chạm mục tiêu trước khi khớp → `cancelled_target` (ĐÚNG hướng). Khối
kèm `calibration` (tỷ lệ đúng theo mức xác suất của `CALIBRATION_REPORTS`=7 báo cáo gần nhất ĐÃ CÓ KẾT LUẬN — báo cáo còn trong khung không chiếm suất) và `rules` (ngưỡng chấm,
hiển thị ở `ForecastReviewCard`). Ngưỡng chung `DIRECTION_THRESHOLD_PCT` = `SIDEWAYS_MAX_PCT` (1%, không chồng lấn). Lệnh
dừng/giới hạn phân biệt bằng `strategy_levels.is_breakout_entry` (chỉ cụm có điều kiện "khi/nếu … trên/vượt", "phá đỉnh" —
chữ "trên" lẻ không tính); lệnh giới hạn khớp do mở gap thì xét cắt lỗ theo đáy/đỉnh của chính phiên đó; đủ N phiên chưa
khớp là `expired` ngay. Bảng hiện MỌI báo cáo còn trong khung (`REVIEW_MAX_ITEMS`). Jack
`check_forecast_review`: có dự báo SAI mà `vs_previous` trống → error, không nhắc ngày báo cáo sai → warning.
Từ 18/10/2026: "đi ngang" chấm cả hành trình (`SIDEWAYS_PATH_MAX_PCT`), mỗi dòng có `graded_by` (trade/direction/path),
`design_flaws` (R:R tại giá khớp < `MIN_RR`, cắt lỗ < `MIN_STOP_ATR` ATR — Jack đòi `vs_previous` nêu) và khối `baseline`
(dự báo ngây thơ "luôn đi ngang"/"lặp chiều 5 phiên" trên cùng các báo cáo đã có kết luận; `_calibration` trả tuple).
Tool chat/MCP: `get_forecast_scorecard` đọc nguyên khối này từ báo cáo published; `review_past_forecast` chấm 1 báo cáo bằng
CHÍNH `forecast_review.evaluate_forecast` (khung `REVIEW_MAX_SESSIONS`) — không tự chấm riêng, để không trái với bảng "Dự báo trước".

**Nhãn nguồn tin**: `news_source_label` = nhà xuất bản thật (domain URL), KHÔNG ghi kèm nguồn cấu hình crawl (kênh thu tin,
vd Bloomberg). Tóm tắt Mục 6 trả thêm `origin` (hãng gốc khi bài dẫn lại, vd "Zelenskyy told the FT" trên Politico) →
`apply_source_chains` đổi mọi nhãn của bài đó thành "Financial Times (gốc) → politico.com". Gợi ý kinh doanh không có nguồn
(và không phải ngưỡng giá) bị loại ngay khi sinh báo cáo.

**Giá API2 & CBAM**: API2 lấy thẳng từ ICE (`crawl_prices/crawl_ice.py`, symbol `ICE:<productId>:<hubId>[:<pageId>]` trong
`price_crawl_sources` — Barchart ngừng cập nhật ITF* từ 2023-08-18); crawler bỏ qua + báo lỗi mọi mã có phiên mới nhất cũ quá
`STALE_PRICE_MAX_DAYS`. Lịch + kết quả đấu giá sơ cấp EUA trên EEX → bảng `eua_auctions` (`crawl_prices/crawl_eex_auctions.py`,
chạy trong job giá 06:00). Giá CBAM EC công bố theo quý = VWAP (gia quyền khối lượng) giá khớp mọi phiên EEX trong quý (khớp
Q1–Q3/2026 tới 0,01) → `services/cbam_estimate.py` ước tính quý đang chạy + khoảng tin cậy 90% + tín hiệu phòng vệ
(`HEDGE_GAP_PCT`/`HEDGE_FIXED_SHARE_HOLD`), lưu ở `content["2"].cbam_estimate` (chỉ số + enum ASCII, frontend
`CbamEstimateCard.tsx` dựng chữ theo ngôn ngữ). "EUA lũy kế quý" (`qtd_avg`) ưu tiên VWAP đấu giá, thiếu thì TB giá đóng cửa và
ghi "xấp xỉ" (`qtd_basis`). Biểu đồ "So sánh hiệu suất" (`PriceCharts.tsx::CompareChart`): `build_price_dashboard` trả thêm
`auction_vwap` (VWAP lũy kế quý theo ngày, `auction_vwap_series`) → khi có EUA vẽ đường VWAP đỏ nét đứt quy về GỐC CHỈ SỐ CỦA EUA
(ngắt khi sang quý) + mốc ước tính CBAM cuối quý kèm KTC ở lề phải (từ `cbam_estimate` của báo cáo, chỉ khi cùng quý). Mục 8 có 1 sự kiện gộp các phiên đấu giá 7 ngày tới (`_auction_calendar_events`). Tool chat/MCP:
`get_cbam_estimate` (thẻ CBAM + ô phòng hộ Mục 3, đọc từ báo cáo published) và `get_eua_auctions` (bảng `eua_auctions` theo
khoảng ngày, không neo báo cáo). `get_report_section` Mục 2/3 trả đủ ghi chú bảng giá, `cbam_estimate`, `forecast_review`,
`vs_previous`, `hedge_action`, `open_positions`, `fuel_model_note`, `sources` (test: `tests/test_quote_chat_report_tools.py`).

**Market data**: unchanged from the original crawler — `market_data.py` defines a
`PriceProvider` ABC with `YFinanceProvider` (real data, WTI/Brent only) and
`ManualOrVendorProvider` (deliberately `NotImplementedError` for EUA/TTF/German
Power/NEWC — needs a paid vendor like Bloomberg/Refinitiv/ICE Data Vault/Barchart,
not wired up).

### Known limitations to keep in mind when modifying this code

- The link-extraction heuristic in `crawler.py::_extract_article_links()` will
  silently return 0 articles for sources with unusual markup (SPA/JS-rendered pages,
  login-gated sites like Bloomberg/FT). There's no per-source override mechanism yet
  (`link_pattern` field mentioned in comments but not implemented) — if adding one, it
  belongs on `SourceConfig` in `models.py`, wired through `load_sources()` in
  `scripts/run_daily_crawl.py`.
- `httpx` (used by `PoliteFetcher`) does not execute JavaScript. Sources needing JS
  rendering need a different fetch path (e.g. Playwright), not a fix to the heuristic.
- Adding a new source: add an entry to `sources.yaml` (see README.md for the field
  format); no code change needed unless the site needs special-case link extraction.
- Adding a new instrument with real vendor data: implement a new `PriceProvider`
  subclass in `market_data.py` and register it in `INSTRUMENT_PROVIDERS`.
- `sources.yaml` has no "already crawled" state persisted between runs — every
  `run_daily_crawl` invocation re-crawls every source's listing page (no extra LLM
  cost since dedupe runs before classify, but still extra HTTP requests). A
  cross-process `seen_urls` store (e.g. Redis) would reduce that if needed.
- Content-hash dedupe only catches exact/near-exact duplicates (whitespace/case
  differences) — it does not catch the same story paraphrased across sources. That's
  what `dedupe.py::EmbeddingFingerprinter` is reserved for.
- Chunk/embed failures after an article is already stored are swallowed (logged,
  not raised — see step 6 above), and a subsequent crawl won't retry them because
  dedupe matches on the already-stored `articles` row. If chunk/embed failures show
  up in logs, a one-off backfill (find `articles.id` with no matching `chunks.source_id`)
  is needed to catch them up.
- `FastEmbedEmbedder` (`embedding.py`) downloads
  `sentence-transformers/all-MiniLM-L6-v2` from Hugging Face on first use and caches
  it locally — the first run in a fresh environment (including CI) needs network
  access for that.
