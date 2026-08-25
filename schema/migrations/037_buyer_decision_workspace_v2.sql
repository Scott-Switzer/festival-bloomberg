-- ===========================================================================
-- 037_buyer_decision_workspace_v2.sql
-- ===========================================================================
-- BUYER_DECISION_WORKSPACE_V2 — unified proposed-show object for talent buyers.
--
-- A proposed show organizes existing evidence around one coherent object:
-- ARTIST × MARKET × DATE × VENUE × DEAL
--
-- The workspace presents evidence status (known/assumed/unknown/conflicting),
-- competitive calendar, comparable events, venue capacity, show economics,
-- artist/attention context, risks/warnings, and provenance — all without
-- any opaque recommendation score.
--
-- Semantics preserved:
--   UNKNOWN != 0
--   retrieved_at != publication_time
--   current scrape != historical availability
--   current followers != historical followers
--   source existence today != knowable at decision cutoff
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Proposed shows (one coherent underwriting object).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.proposed_shows (
    proposed_show_key   VARCHAR PRIMARY KEY,   -- hash(project_key, artist_key, market, date)
    project_key         VARCHAR NOT NULL,
    artist_key          VARCHAR,
    artist_name         VARCHAR NOT NULL,
    musicbrainz_id      VARCHAR,
    market              VARCHAR NOT NULL,       -- "Chicago, IL"
    city                VARCHAR,
    state_code          VARCHAR,
    venue_key           VARCHAR,
    venue_name          VARCHAR,
    venue_configuration VARCHAR,               -- e.g. "SEATED", "CONCERT", "SPORTS"
    proposed_date       DATE NOT NULL,
    deal_type           VARCHAR,               -- FLAT_GUARANTEE|GUARANTEE_VS_PERCENTAGE|PERCENTAGE
    artist_guarantee    DOUBLE,
    backend_percentage  DOUBLE,
    backend_basis       VARCHAR,
    decision_cutoff     TIMESTAMP,             -- when the buyer must decide
    research_cutoff     TIMESTAMP,             -- PIT anchor (earliest knowledge cutoff)
    scenario_version    INTEGER NOT NULL DEFAULT 1,
    notes               VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_proposed_shows_project
  ON planning.proposed_shows (project_key);
CREATE INDEX IF NOT EXISTS idx_proposed_shows_date
  ON planning.proposed_shows (proposed_date);

-- ---------------------------------------------------------------------------
-- 2. Proposal comparisons (stored snapshots of a buyer comparing scenarios).
--    Each comparison links 2+ proposed shows and records the assembled
--    evidence snapshot at compare time, so it is replayable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.proposal_comparisons (
    comparison_key      VARCHAR PRIMARY KEY,
    project_key         VARCHAR NOT NULL,
    name                VARCHAR NOT NULL,
    proposed_show_keys   JSON NOT NULL,         -- [proposed_show_key, ...]
    evidence_snapshot   JSON,                   -- frozen comparison evidence
    assumptions_ledger  JSON,                   -- explicit assumptions per scenario
    notes               VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_proposal_comparisons_project
  ON planning.proposal_comparisons (project_key);

-- ---------------------------------------------------------------------------
-- 3. External source evidence log (Apify/Monid observations).
--    One row per provider call; raw payload preserved for provenance.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.source_evaluation_log (
    eval_key            VARCHAR PRIMARY KEY,   -- hash(source, actor, query)
    source              VARCHAR NOT NULL,       -- "apify"|"monid"
    actor_endpoint      VARCHAR NOT NULL,       -- e.g. "solidcode/eventbrite-scraper"
    query_context       VARCHAR NOT NULL,       -- "eventbrite_la_events_2026-10"
    retrieved_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_payload         JSON,                   -- immutable raw response
    record_count        INTEGER,
    cost_usd            DOUBLE,
    latency_ms          DOUBLE,
    success             BOOLEAN NOT NULL,
    error_category      VARCHAR,
    fields_observed     JSON,                   -- list of field names in response
    null_rate           JSON,                   -- {field: null_fraction}
    verdict             VARCHAR,                -- ADOPT|PILOT_ONLY|RESEARCH_ONLY|REJECT|TERMS_REVIEW_REQUIRED
    verdict_rationale   VARCHAR,
    rights_status       VARCHAR,                -- CLEARED|TERMS_REVIEW_REQUIRED|RESEARCH_ONLY|UNKNOWN
    commercial_use_ok   BOOLEAN,
    retention_notes     VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_source_eval_source
  ON planning.source_evaluation_log (source, actor_endpoint);