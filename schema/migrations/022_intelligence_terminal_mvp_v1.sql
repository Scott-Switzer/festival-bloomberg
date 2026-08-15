-- ===========================================================================
-- 022_intelligence_terminal_mvp_v1.sql
-- ===========================================================================
-- FESTIVAL_INTELLIGENCE_TERMINAL_MVP_V1 — the first information product.
--
-- The terminal is read-only over the canonical warehouse. This migration adds
-- the two pieces of WRITE-side infrastructure the product needs:
--
--   1. terminal.activity_tape — the append-only "what changed" ledger.
--      Every meaningful, externally observable transition becomes one row.
--      UNCHANGED provider polls NEVER become tape entries. Rows are
--      append-only and deduplicated by a stable dedupe_key, so re-deriving
--      the tape from the warehouse is idempotent and never rewrites history.
--
--   2. terminal.provider_health — the operational freshness ledger for the
--      DATA page. Linked to flywheel.source_registry (rights / commercial
--      status stay in one place); this table only adds the OPERATIONAL
--      freshness counters that the registry does not track.
--
-- Non-negotiable semantics preserved: UNKNOWN != 0, append-only, source /
-- knowledge-time provenance on every row, no fabricated facts.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS terminal;

-- ---------------------------------------------------------------------------
-- 1. The activity tape ("what changed in live entertainment?").
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS terminal.activity_tape (
    activity_id         VARCHAR PRIMARY KEY,
    observed_at         TIMESTAMP NOT NULL,   -- when WE observed the change
    effective_at        TIMESTAMP,            -- when the change took effect (may differ from observed_at)
    entity_type         VARCHAR NOT NULL,     -- ARTIST | EVENT | VENUE | MARKET | FESTIVAL | PROMOTER | TOUR
    entity_id           VARCHAR NOT NULL,
    activity_type       VARCHAR NOT NULL,     -- closed enum, see below
    artist_id           VARCHAR,
    event_id            VARCHAR,
    venue_id            VARCHAR,
    market_id           VARCHAR,
    festival_id         VARCHAR,
    source_provider     VARCHAR NOT NULL,
    source_record_id    VARCHAR,
    old_value_json      JSON,
    new_value_json      JSON,
    evidence_class      VARCHAR NOT NULL,     -- OBSERVED_PUBLIC | OBSERVED_PRIVATE | ARCHIVE_CAPTURE_UPPER_BOUND | ...
    rights_status       VARCHAR NOT NULL,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP NOT NULL,
    dedupe_key          VARCHAR NOT NULL,
    software_version    VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_activity_tape_time
  ON terminal.activity_tape (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_tape_entity
  ON terminal.activity_tape (entity_type, entity_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_tape_type
  ON terminal.activity_tape (activity_type, observed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_tape_dedupe
  ON terminal.activity_tape (dedupe_key);

-- ---------------------------------------------------------------------------
-- 2. Provider health (the DATA page freshness ledger).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS terminal.provider_health (
    provider                VARCHAR PRIMARY KEY,   -- == flywheel.source_registry.source_id
    operational_status      VARCHAR NOT NULL,      -- OPERATIONAL | NOT_CONFIGURED | DEGRADED | ERROR | PARTIAL | BLOCKED
    last_success_at         TIMESTAMP,
    last_attempt_at         TIMESTAMP,
    latest_knowledge_time   TIMESTAMP,
    records_total           INTEGER,
    entities_covered        INTEGER,
    failure_count           INTEGER,
    rate_limit_count        INTEGER,
    freshness_note          VARCHAR,
    measured_at             TIMESTAMP NOT NULL,
    software_version        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_provider_health_measured
  ON terminal.provider_health (measured_at DESC);

-- ---------------------------------------------------------------------------
-- 3. News-mention tape (GDELT / news discovery). Metadata only: article URL,
--    domain, title, publication time, entity match. Full copyrighted article
--    text is NEVER persisted or redistributed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS terminal.news_mentions (
    mention_id          VARCHAR PRIMARY KEY,
    entity_type         VARCHAR NOT NULL,
    entity_name         VARCHAR NOT NULL,
    entity_id           VARCHAR NOT NULL,
    article_url         VARCHAR NOT NULL,
    domain              VARCHAR,
    title               VARCHAR,
    publication_time    TIMESTAMP,
    query_or_match      VARCHAR,
    provider            VARCHAR NOT NULL,
    retrieved_at        TIMESTAMP NOT NULL,
    knowledge_time      TIMESTAMP NOT NULL,
    rights_status       VARCHAR NOT NULL,
    dedupe_key          VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_mentions_entity
  ON terminal.news_mentions (entity_id, publication_time DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_mentions_dedupe
  ON terminal.news_mentions (dedupe_key);
