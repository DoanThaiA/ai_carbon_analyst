-- Schema for the `news` table. Applied idempotently in dev via
-- carbon_analyst.storage.ensure_schema(); this file is the source of truth to
-- run against a real Postgres instance in production (e.g. via `psql -f`).

CREATE TABLE IF NOT EXISTS news (
    id BIGSERIAL PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    source_domain TEXT NOT NULL,
    tier CHAR(1) NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    published_at TIMESTAMPTZ,
    category TEXT NOT NULL CHECK (category IN ('energy_fossil_fuels', 'carbon_credits', 'policy')),
    category_confidence REAL,
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS news_content_hash_idx ON news (content_hash);
CREATE INDEX IF NOT EXISTS news_category_idx ON news (category);
CREATE INDEX IF NOT EXISTS news_published_at_idx ON news (published_at DESC);
