-- ===========================================================================
-- 016_public_boxscore_research_corpus_v2.sql
-- ===========================================================================
-- Public Boxscore Research Corpus V2.
--
-- V1 proved the acquisition path; V2 turns the corpus into a statistically
-- auditable research panel. This migration adds:
--
--   1. research.boxoffice_sources               -- selection-metadata per source doc
--   2. research.canonical_boxoffice_engagements -- cross-source engagement identity
--   3. research.boxoffice_engagement_resolutions-- raw -> canonical mapping (graded)
--   4. research.forward_ticket_inventory_snapshots -- forward watchlist (inferred only)
--   5. research.research_splits                 -- deterministic, leakage-safe split manifests
--
-- It also adds additive columns to the existing append-only
-- research.boxoffice_engagements table (never rewrites existing rows):
--
--   tour                  -- tour identity (Touring Data page slug, else NULL)
--   headcount_source_label-- the literal source field label (honest provenance)
--   sell_through_pct      -- the "(100%)" sell-through the source printed, if any
--
-- RESEARCH CORPUS ONLY. Every row keeps RESEARCH_ONLY / TERMS_REVIEW_REQUIRED
-- rights; the commercial-eligible corpus stays empty (fail closed).
-- ===========================================================================

ALTER TABLE research.boxoffice_engagements ADD COLUMN IF NOT EXISTS tour VARCHAR;
ALTER TABLE research.boxoffice_engagements ADD COLUMN IF NOT EXISTS headcount_source_label VARCHAR;
ALTER TABLE research.boxoffice_engagements ADD COLUMN IF NOT EXISTS sell_through_pct DOUBLE;

-- ---------------------------------------------------------------------------
-- 1. Source documents + explicit selection metadata
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.boxoffice_sources (
    source_id               VARCHAR PRIMARY KEY,
    reporting_source        VARCHAR NOT NULL,   -- billboard | pollstar | touring_data
    source_url              VARCHAR NOT NULL,
    publication_date        DATE,
    retrieved_at            TIMESTAMP NOT NULL,
    content_hash            VARCHAR,
    record_count            INTEGER,
    selection_method        VARCHAR,            -- BILLBOARD_BOXSCORE_CHART | POLLSTAR_HOT_TICKETS_CHART | TOURING_DATA_REPORTED_TOUR
    ranking_or_chart_status VARCHAR,
    known_threshold         VARCHAR,
    unknown_threshold       VARCHAR,
    coverage_scope          VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_boxoffice_sources_src
  ON research.boxoffice_sources (reporting_source, publication_date);

-- ---------------------------------------------------------------------------
-- 2. Canonical engagement identity (cross-source)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.canonical_boxoffice_engagements (
    canonical_engagement_id VARCHAR PRIMARY KEY,
    artist                  VARCHAR NOT NULL,
    venue                   VARCHAR,
    market                  VARCHAR,
    city                    VARCHAR,
    state                   VARCHAR,
    country                 VARCHAR,
    tour                    VARCHAR,
    start_date              DATE,
    end_date                DATE,
    number_of_shows         INTEGER,
    is_multi_show           BOOLEAN,
    resolution_confidence   VARCHAR,            -- EXACT | PROBABLE | REVIEW_REQUIRED | UNIQUE
    source_count            INTEGER,
    software_version        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_canonical_artist
  ON research.canonical_boxoffice_engagements (artist, venue, start_date);

-- ---------------------------------------------------------------------------
-- 3. Raw -> canonical resolution mapping (graded, append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.boxoffice_engagement_resolutions (
    resolution_id           VARCHAR PRIMARY KEY,
    raw_engagement_id       VARCHAR NOT NULL,
    canonical_engagement_id VARCHAR NOT NULL,
    resolution_status       VARCHAR NOT NULL,    -- EXACT_MATCH | PROBABLE_MATCH | REVIEW_REQUIRED | DISTINCT
    match_key               VARCHAR,
    created_at              TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resolution_raw
  ON research.boxoffice_engagement_resolutions (raw_engagement_id);
CREATE INDEX IF NOT EXISTS idx_resolution_canonical
  ON research.boxoffice_engagement_resolutions (canonical_engagement_id);

-- ---------------------------------------------------------------------------
-- 4. Forward ticket-inventory watchlist (inferred signals, never outcomes)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.forward_ticket_inventory_snapshots (
    snapshot_id                            VARCHAR PRIMARY KEY,
    event_external_id                      VARCHAR,
    artist                                 VARCHAR,
    venue                                  VARCHAR,
    market                                 VARCHAR,
    event_date                             DATE,
    general_sale_date                      DATE,
    snapshot_time                          TIMESTAMP,
    retrieved_at                           TIMESTAMP NOT NULL,
    estimated_capacity                     DOUBLE,
    tickets_available                      DOUBLE,
    tickets_distributed_or_sold_as_reported DOUBLE,
    source_methodology                     VARCHAR,
    classification                         VARCHAR NOT NULL,  -- INFERRED_INVENTORY_SIGNAL (never PAID_TICKETS)
    source_url                             VARCHAR,
    source_publication_time                TIMESTAMP,
    rights_status                          VARCHAR NOT NULL,
    commercial_use_status                  VARCHAR NOT NULL,
    observation_class                      VARCHAR NOT NULL,
    software_version                       VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_inventory_event
  ON research.forward_ticket_inventory_snapshots (event_external_id, snapshot_time);

-- ---------------------------------------------------------------------------
-- 5. Deterministic, leakage-safe research split manifests
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.research_splits (
    split_id                VARCHAR PRIMARY KEY,
    split_type              VARCHAR NOT NULL,  -- TIME | ARTIST_GROUP | VENUE_GROUP | MARKET_GROUP | TOUR_GROUP
    canonical_engagement_id VARCHAR NOT NULL,
    fold                    VARCHAR NOT NULL,  -- TRAIN | TEST
    group_key               VARCHAR,
    created_at              TIMESTAMP NOT NULL,
    seed                    INTEGER,
    deterministic           BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_splits_type
  ON research.research_splits (split_type, fold);
