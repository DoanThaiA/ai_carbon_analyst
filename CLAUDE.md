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
   (default `command-r-plus-08-2024` cho Cohere, `claude-haiku-4-5` cho Anthropic) qua
   `client.messages.create`. Input text được cắt tại `MAX_TEXT_CHARS_FOR_CLASSIFICATION = 6000`
   ký tự (tăng từ 4000 — đủ bắt được phần phân tích của bài dài). LLM trả về JSON gồm
   `topics` (1–3 giá trị từ `NewsTopic`), `confidence` (0–1), và `is_relevant` (bool).
   - `is_relevant=false`: LLM xác nhận bài không liên quan energy/carbon — pipeline
     trả về `status="irrelevant"` và không lưu DB.
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
