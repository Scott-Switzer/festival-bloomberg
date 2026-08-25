-- ===========================================================================
-- 037_buyer_decision_workspace_v2.sql
-- ===========================================================================
-- BUYER_DECISION_WORKSPACE_V2 — unified proposed-show object for talent buyers.
--
-- A proposed show organizes existing evidence around one coherent object:
-- ARTIST × MARKET × DATE × VENUE × DEAL
--
-- Identity model: proposed_show_key now includes venue + deal dimensions,
-- so same artist/market/date + different venue or deal do NOT collide.
--
-- Immutable revisions: each update snapshots the previous state into
-- planning.proposed_show_revisions before overwriting.
--
-- Evidence provenance: deal_provenance, guarantee_provenance, backend_provenance
-- capture whether values are USER_ASSUMPTION, OBSERVED_PUBLIC, etc.
-- USER_ASSUMPTION is NEVER classified as KNOWN.
-- ===========================================================================

-- Drop old table to allow schema change (columns added, type changed).
DROP TABLE IF EXISTS planning.proposal_comparisons;
DROP TABLE IF EXISTS planning.proposed_show_revisions;
DROP TABLE IF EXISTS planning.proposed_shows;

-- ---------------------------------------------------------------------------
-- 1. Proposed shows (one coherent underwriting object).
-- ---------------------------------------------------------------------------
CREATE TABLE planning.proposed_shows (
    proposed_show_key   VARCHAR PRIMARY KEY,   -- hash(project, artist, market, date, venue, deal)
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
    deal_type           VARCHAR,               -- FLAT_GUARANTEE|GUARANTEE_VS_PERCENTAGE|...
    artist_guarantee    DOUBLE,
    backend_percentage  DOUBLE,
    backend_basis       VARCHAR,
    deal_provenance     VARCHAR NOT NULL DEFAULT 'USER_ASSUMPTION',
    guarantee_provenance VARCHAR NOT NULL DEFAULT 'USER_ASSUMPTION',
    backend_provenance  VARCHAR NOT NULL DEFAULT 'USER_ASSUMPTION',
    decision_cutoff     TIMESTAMP,             -- when the buyer must decide
    research_cutoff     TIMESTAMP,             -- PIT anchor (earliest knowledge cutoff)
    current_revision    INTEGER NOT NULL DEFAULT 1,
    notes               VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_proposed_shows_project
  ON planning.proposed_shows (project_key);
CREATE INDEX idx_proposed_shows_date
  ON planning.proposed_shows (proposed_date);

-- ---------------------------------------------------------------------------
-- 2. Immutable revision history.
-- ---------------------------------------------------------------------------
CREATE TABLE planning.proposed_show_revisions (
    scenario_key        VARCHAR PRIMARY KEY,   -- hash(proposed_show_key, revision_number, created_at)
    proposed_show_key   VARCHAR NOT NULL,
    revision_number     INTEGER NOT NULL,
    snapshot_json       JSON NOT NULL,         -- frozen show state at this revision
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes               VARCHAR,
    FOREIGN KEY (proposed_show_key) REFERENCES planning.proposed_shows(proposed_show_key)
);
CREATE INDEX idx_revisions_show
  ON planning.proposed_show_revisions (proposed_show_key, revision_number);

-- ---------------------------------------------------------------------------
-- 3. Proposal comparisons (stored snapshots of a buyer comparing scenarios).
-- ---------------------------------------------------------------------------
CREATE TABLE planning.proposal_comparisons (
    comparison_key      VARCHAR PRIMARY KEY,
    project_key         VARCHAR NOT NULL,
    name                VARCHAR NOT NULL,
    proposed_show_keys   JSON NOT NULL,         -- [proposed_show_key, ...]
    evidence_snapshot   JSON,                   -- frozen comparison evidence
    assumptions_ledger  JSON,                   -- explicit assumptions per scenario
    notes               VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_proposal_comparisons_project
  ON planning.proposal_comparisons (project_key);

-- ---------------------------------------------------------------------------
-- 4. External source evidence log (Apify/Monid observations).
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