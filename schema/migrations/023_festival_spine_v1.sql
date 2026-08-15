-- ===========================================================================
-- 023_festival_spine_v1.sql
-- ===========================================================================
-- INTELLIGENCE_DATA_ESTATE_AND_FESTIVAL_SPINE_V1 — activate the festival spine
-- that ``schema/duckdb.sql`` already defined but never populated.
--
-- The canonical festival dimension already exists (core.festivals,
-- core.festival_editions, core.festival_stages, core.lineup_slots,
-- raw.lineup_observations). This migration adds the two pieces the
-- source-specific billing doctrine requires and that were missing:
--
--   1. core.festival_billing_observations — one row per SOURCE-specific
--      billing claim. Billing is an OBSERVATION, never a universal truth:
--      a poster, programme, website, and retrospective can disagree, and
--      conflicting claims must coexist. This is the append-only table that
--      backs "how has this artist's billing changed?"
--
--   2. Two columns on existing tables:
--        core.festival_editions.date_precision  (day|month|year|circa|unknown)
--        core.lineup_slots.performance_status   (announced|scheduled|
--                                                performed|cancelled|
--                                                substituted|surprise|unverified)
--        core.lineup_slots.identity_confidence  (0-1; NULL = unresolved)
--
-- Non-negotiable semantics preserved:
--   - announced != scheduled != performed; cancelled is never merged away.
--   - unresolved artists stay unresolved (artist_key NULL).
--   - a billing tier is an observation with a source, not a fact.
--   - UNKNOWN is never encoded as 0 (NULL columns stay NULL).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Source-specific billing observations (append-only).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.festival_billing_observations (
    observation_id          VARCHAR PRIMARY KEY,
    festival_key            VARCHAR NOT NULL,
    edition_key             VARCHAR NOT NULL,
    artist_key              VARCHAR,              -- resolved identity (NULL until resolved)
    raw_artist_name         VARCHAR NOT NULL,     -- raw string exactly as the source prints it
    billing_context         VARCHAR NOT NULL,     -- poster|flyer|programme|website|press_release|ticket|schedule|announcement|retrospective
    printed_order           INTEGER,              -- top-to-bottom/left-to-right order after layout rules
    printed_tier            INTEGER,              -- 1 = top billing (analytical tiering only where explicit)
    billing_group           VARCHAR,              -- "with", "plus", "special guest", ...
    headline_flag           BOOLEAN,
    co_headliner_flag       BOOLEAN,
    first_line_flag         BOOLEAN,
    closing_act_flag        BOOLEAN,
    stage_name              VARCHAR,
    day_label               VARCHAR,
    set_time_order          INTEGER,
    extraction_method       VARCHAR,              -- manual|OCR|layout_model|official_structured_data|research_seed
    extraction_version      VARCHAR,
    identity_confidence     DOUBLE,
    source_provider         VARCHAR NOT NULL,
    source_url              VARCHAR,
    source_document_id      VARCHAR,
    publication_date        DATE,
    retrieved_at            TIMESTAMP,
    knowledge_time          TIMESTAMP,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    evidence_class          VARCHAR NOT NULL,     -- OBSERVED_DAY|RESEARCH_DISCOVERY_SEED|ARCHIVE_CAPTURE_UPPER_BOUND|...
    notes                   VARCHAR,
    dedupe_key              VARCHAR NOT NULL,
    software_version        VARCHAR,
    ingested_at             TIMESTAMP,
    CHECK (identity_confidence IS NULL OR (identity_confidence >= 0.0 AND identity_confidence <= 1.0)),
    CHECK (printed_order IS NULL OR printed_order >= 0),
    CHECK (printed_tier IS NULL OR printed_tier >= 0)
);

CREATE INDEX IF NOT EXISTS idx_festival_billing_edition
  ON core.festival_billing_observations (edition_key, printed_order);
CREATE INDEX IF NOT EXISTS idx_festival_billing_artist
  ON core.festival_billing_observations (raw_artist_name, edition_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_festival_billing_dedupe
  ON core.festival_billing_observations (dedupe_key);

-- ---------------------------------------------------------------------------
-- 2. Edition date precision (day|month|year|circa|unknown).
-- ---------------------------------------------------------------------------
ALTER TABLE core.festival_editions
  ADD COLUMN IF NOT EXISTS date_precision VARCHAR;

-- ---------------------------------------------------------------------------
-- 3. Performance status + identity confidence on lineup slots.
-- ---------------------------------------------------------------------------
ALTER TABLE core.lineup_slots
  ADD COLUMN IF NOT EXISTS performance_status VARCHAR;
ALTER TABLE core.lineup_slots
  ADD COLUMN IF NOT EXISTS identity_confidence DOUBLE;
