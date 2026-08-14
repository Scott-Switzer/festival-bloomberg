-- ===========================================================================
-- 006_canonical_pit_temporal_fields_v1.sql
-- ===========================================================================
-- Adds point-in-time temporal and provenance fields to the canonical metrics
-- and raw tables so the FestivalRepository write methods match the schema.
--
-- knowledge_time: the earliest defensible timestamp at which the information
-- is evidenced to have been knowable for the decision process. For newly
-- ingested public observations, retrieval time is a valid conservative
-- fallback. If that cannot be established, the write must fail — do not
-- invent an earlier timestamp.
--
-- This migration is idempotent and safe to re-run. Each ALTER is a separate
-- statement so DuckDB's migration runner can execute them individually.
-- ===========================================================================

-- -- metrics.artist_metrics -------------------------------------------------
-- Add PIT temporal/provenance columns. Existing rows get NULL for new fields.

ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_publication_time TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_as_of TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS valid_to TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS knowledge_time TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS calculated_at TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_url VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_record_id VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS confidence DOUBLE;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS quality_flags JSON;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS license_class VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS commercial_use_status VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS feature_version VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS model_version VARCHAR;

CREATE INDEX IF NOT EXISTS idx_artist_metrics_knowledge_time
  ON metrics.artist_metrics (knowledge_time);

-- -- raw.lineup_observations -------------------------------------------------
-- Add PIT temporal/provenance columns.

ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS source_publication_time TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS source_as_of TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS valid_to TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS knowledge_time TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS confidence DOUBLE;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS quality_flags JSON;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS license_class VARCHAR;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS commercial_use_status VARCHAR;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS feature_version VARCHAR;

CREATE INDEX IF NOT EXISTS idx_lineup_obs_knowledge_time
  ON raw.lineup_observations (knowledge_time);

-- -- core.secondary_ticket_observations --------------------------------------
-- Add provenance columns produced by the SeatGeek adapter so existing
-- databases (migrated from current main) match the canonical schema.

ALTER TABLE core.secondary_ticket_observations ADD COLUMN IF NOT EXISTS content_hash VARCHAR;
ALTER TABLE core.secondary_ticket_observations ADD COLUMN IF NOT EXISTS provenance VARCHAR;
ALTER TABLE core.secondary_ticket_observations ADD COLUMN IF NOT EXISTS retrieval_metadata JSON;
ALTER TABLE core.secondary_ticket_observations ADD COLUMN IF NOT EXISTS quality_flags JSON;

-- -- Point-in-time feature store tables --------------------------------------
-- The canonical repository (python/festival_bloomberg/warehouse/repository.py)
-- writes derived features, factors, expected billing, relative value and
-- portfolio analytics. These tables are part of the canonical PIT foundation
-- and must exist after upgrading a database created from current main.

CREATE TABLE IF NOT EXISTS metrics.artist_feature_store (
    feature_key             VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    festival_key            VARCHAR,
    edition_key             VARCHAR,
    edition_year            INTEGER,
    feature_name            VARCHAR NOT NULL,
    feature_type            VARCHAR NOT NULL,
    feature_value           DOUBLE,
    feature_category        VARCHAR,
    feature_date            DATE,
    source_publication_time TIMESTAMP,
    source_as_of            TIMESTAMP,
    retrieved_at            TIMESTAMP,
    valid_from              TIMESTAMP,
    valid_to                TIMESTAMP,
    knowledge_time          TIMESTAMP,
    calculated_at           TIMESTAMP,
    feature_version         VARCHAR,
    model_version           VARCHAR,
    formula                 VARCHAR,
    input_features          JSON,
    confidence              DOUBLE,
    quality_flags           JSON,
    source_system           VARCHAR,
    source_url              VARCHAR,
    source_record_id        VARCHAR,
    license_class           VARCHAR,
    commercial_use_status   VARCHAR,
    evidence_json           JSON,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feature_store_artist
  ON metrics.artist_feature_store (artist_key, feature_date);
CREATE INDEX IF NOT EXISTS idx_feature_store_knowledge_time
  ON metrics.artist_feature_store (knowledge_time);

CREATE TABLE IF NOT EXISTS metrics.artist_factors (
    factor_key              VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    festival_key            VARCHAR,
    edition_key             VARCHAR,
    edition_year            INTEGER,
    momentum_score          DOUBLE,
    relevance_score         DOUBLE,
    audience_fit_score      DOUBLE,
    value_proposition_score DOUBLE,
    booking_complexity_score DOUBLE,
    risk_score              DOUBLE,
    momentum_components     JSON,
    relevance_components    JSON,
    audience_components     JSON,
    value_components        JSON,
    complexity_components   JSON,
    risk_components         JSON,
    factor_model_version    VARCHAR,
    scoring_method          VARCHAR,
    confidence              DOUBLE,
    quality_flags           JSON,
    feature_date            DATE,
    source_as_of            TIMESTAMP,
    calculated_at           TIMESTAMP,
    valid_from              TIMESTAMP,
    valid_to                TIMESTAMP,
    knowledge_time          TIMESTAMP,
    source_system           VARCHAR,
    evidence_json           JSON,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_artist_factors_artist
  ON metrics.artist_factors (artist_key, feature_date);
CREATE INDEX IF NOT EXISTS idx_artist_factors_knowledge_time
  ON metrics.artist_factors (knowledge_time);

CREATE TABLE IF NOT EXISTS metrics.expected_billing (
    billing_key             VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    festival_key            VARCHAR,
    edition_key             VARCHAR,
    edition_year            INTEGER,
    expected_billing_tier   VARCHAR,
    expected_billing_order  INTEGER,
    billing_confidence      DOUBLE,
    booking_probability     DOUBLE,
    expected_day            INTEGER,
    expected_stage          VARCHAR,
    billing_reasoning       VARCHAR,
    billing_factors         JSON,
    model_version           VARCHAR,
    training_period         VARCHAR,
    confidence              DOUBLE,
    quality_flags           JSON,
    feature_date            DATE,
    source_as_of            TIMESTAMP,
    calculated_at           TIMESTAMP,
    valid_from              TIMESTAMP,
    valid_to                TIMESTAMP,
    knowledge_time          TIMESTAMP,
    source_system           VARCHAR,
    evidence_json           JSON,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_expected_billing_artist
  ON metrics.expected_billing (artist_key, edition_key, feature_date);
CREATE INDEX IF NOT EXISTS idx_expected_billing_knowledge_time
  ON metrics.expected_billing (knowledge_time);

CREATE TABLE IF NOT EXISTS metrics.relative_value (
    value_key               VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    festival_key            VARCHAR,
    edition_key             VARCHAR,
    edition_year            INTEGER,
    relative_value_score    DOUBLE,
    value_category          VARCHAR,
    value_percentile        DOUBLE,
    current_billing_tier    VARCHAR,
    expected_billing_tier   VARCHAR,
    billing_gap             DOUBLE,
    momentum_vs_billing     DOUBLE,
    audience_vs_billing     DOUBLE,
    peer_group              VARCHAR,
    peer_comparison         JSON,
    market_position         VARCHAR,
    value_model_version     VARCHAR,
    scoring_method          VARCHAR,
    confidence              DOUBLE,
    quality_flags           JSON,
    feature_date            DATE,
    source_as_of            TIMESTAMP,
    calculated_at           TIMESTAMP,
    valid_from              TIMESTAMP,
    valid_to                TIMESTAMP,
    knowledge_time          TIMESTAMP,
    source_system           VARCHAR,
    evidence_json           JSON,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_relative_value_artist
  ON metrics.relative_value (artist_key, edition_key, feature_date);
CREATE INDEX IF NOT EXISTS idx_relative_value_knowledge_time
  ON metrics.relative_value (knowledge_time);

CREATE TABLE IF NOT EXISTS metrics.festival_portfolio (
    portfolio_key           VARCHAR PRIMARY KEY,
    festival_key            VARCHAR NOT NULL,
    edition_key             VARCHAR NOT NULL,
    edition_year            INTEGER NOT NULL,
    total_artists           INTEGER,
    headliner_count         INTEGER,
    sub_headliner_count     INTEGER,
    supporting_count        INTEGER,
    early_day_count         INTEGER,
    portfolio_momentum_avg  DOUBLE,
    portfolio_momentum_median DOUBLE,
    portfolio_risk_avg      DOUBLE,
    portfolio_value_avg     DOUBLE,
    portfolio_diversity_score DOUBLE,
    total_budget            DOUBLE,
    headliner_budget        DOUBLE,
    supporting_budget       DOUBLE,
    budget_utilization      DOUBLE,
    cost_per_momentum       DOUBLE,
    cost_per_attendance     DOUBLE,
    roi_score               DOUBLE,
    efficiency_score        DOUBLE,
    portfolio_version       VARCHAR,
    optimization_method     VARCHAR,
    confidence              DOUBLE,
    quality_flags           JSON,
    feature_date            DATE,
    source_as_of            TIMESTAMP,
    calculated_at           TIMESTAMP,
    valid_from              TIMESTAMP,
    valid_to                TIMESTAMP,
    knowledge_time          TIMESTAMP,
    source_system           VARCHAR,
    evidence_json           JSON,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_festival_portfolio_edition
  ON metrics.festival_portfolio (festival_key, edition_key);
CREATE INDEX IF NOT EXISTS idx_festival_portfolio_knowledge_time
  ON metrics.festival_portfolio (knowledge_time);
