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

Requires `DATABASE_URL` and `ANTHROPIC_API_KEY` in the environment (`.env` is loaded
automatically via `python-dotenv` — copy `.env.example` to start). There is no test
suite, linter, or CI config in this repo yet. `main.py` is an unrelated uv-scaffold
placeholder ("Hello from ai-cabon-analyst!") — the real entry points are under
`scripts/`.

## Architecture

Everything lives in the `carbon_analyst/` package; `scripts/` holds thin CLI entry
points. Async throughout (httpx, asyncpg, the Anthropic SDK), matching the original
crawler's asyncio-first design.

**Pipeline** (`carbon_analyst/pipeline.py::process_url` / `process_source`), in a
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
   the MVP implementation (normalize text, SHA-256). `storage.exists()` checks the DB
   for that hash *before* classification runs. `EmbeddingFingerprinter` is a stubbed
   `NotImplementedError` placeholder for later semantic (paraphrase-catching) dedupe —
   same "raise clearly instead of silently wrong" pattern as `market_data.py`'s
   `ManualOrVendorProvider`.
4. **Classify** — `classification.py::AnthropicClassifier` calls Claude (default
   `claude-haiku-4-5`, overridable via `CLASSIFIER_MODEL` env var) via
   `client.messages.parse(..., output_format=CategoryClassification)` — a Pydantic
   model, so no manual JSON parsing/retry loop. On any `anthropic.APIError` after the
   SDK's own retries, raises `ClassificationError`; the pipeline logs it and skips
   storing — the article is simply picked up again on the next crawl since nothing
   was written.
5. **Store** — `storage.py::insert_news()` does `INSERT ... ON CONFLICT (url) DO
   NOTHING RETURNING id`, plus a caught `UniqueViolationError` on the separate
   `content_hash` unique index (covers same-content-different-URL races). This is the
   second, atomic line of defense beyond the pre-classification `exists()` check —
   needed because multiple crawl runs could race.

**Category enum** (`models.py::NewsCategory`, also the Postgres `CHECK` constraint in
`db/schema.sql`): `energy_fossil_fuels` (Năng lượng & nhiên liệu hóa thạch),
`carbon_credits` (Hạn ngạch & Tín chỉ carbon), `policy` (Chính sách).

**`PipelineContext`** (`pipeline.py`) bundles one `PoliteFetcher`, one `Classifier`,
one `Fingerprinter`, and one `asyncpg.Pool` — constructed once per script run and
passed through, rather than using module-level singletons.

**Schema**: `db/schema.sql` is the source of truth for production migrations;
`storage.py::ensure_schema()` runs the identical DDL idempotently
(`CREATE TABLE/INDEX IF NOT EXISTS`) so dev/test can self-initialize.

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
