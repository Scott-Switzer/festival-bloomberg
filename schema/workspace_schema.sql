-- ===========================================================================
-- workspace_schema.sql — mutable analyst-state schema for the terminal.
-- ===========================================================================
-- The terminal WORKSPACE holds ONLY user/analyst state. It must NOT contain
-- canonical evidence schemas (raw.*, reference.*, metrics.*, economics.*,
-- events.*). This keeps accidental evidence writes structurally impossible.
--
-- This file is applied by terminal/storage.create_workspace_db(). It is a
-- deliberately narrow subset of the table definitions also present in the
-- canonical migration stack (029 + 033); drift is guarded by workspace_meta.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS terminal;
CREATE SCHEMA IF NOT EXISTS planning;

-- ---------------------------------------------------------------------------
-- Watchlists (single-user local lists).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.watchlists (
    watchlist_key       VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    description         VARCHAR,
    entity_type         VARCHAR,
    is_system           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.watchlist_items (
    item_key            VARCHAR PRIMARY KEY,
    watchlist_key       VARCHAR NOT NULL,
    entity_type         VARCHAR NOT NULL,
    entity_key          VARCHAR NOT NULL,
    entity_name         VARCHAR,
    notes               VARCHAR,
    tags                JSON,
    added_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    removed_at          TIMESTAMP,
    source_system       VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_watchlist_items_list ON core.watchlist_items (watchlist_key, removed_at);
CREATE INDEX IF NOT EXISTS idx_watchlist_items_entity ON core.watchlist_items (entity_type, entity_key);

-- ---------------------------------------------------------------------------
-- Saved monitors (persisted column/filter/sort configuration).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS terminal.saved_monitors (
    monitor_key         VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    entity_type         VARCHAR NOT NULL,
    watchlist_key       VARCHAR,
    filters             JSON,
    visible_columns     JSON,
    sort                JSON,
    time_horizon        VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Planning workspace (projects / stages / candidates / shortlists /
-- constraints / scenarios). Non-optimizing; UNKNOWN != 0.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.festival_projects (
    project_key         VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    city                VARCHAR,
    market              VARCHAR,
    venue_site          VARCHAR,
    start_date          DATE,
    end_date            DATE,
    num_days            INTEGER,
    num_stages          INTEGER,
    talent_budget_usd   DOUBLE,
    genre_objectives    JSON,
    target_audience     VARCHAR,
    min_billing_tier    VARCHAR,
    max_billing_tier    VARCHAR,
    notes               VARCHAR,
    scenario_class      VARCHAR NOT NULL DEFAULT 'SYNTHETIC_PLANNING_SCENARIO',
    is_official         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_projects_market ON planning.festival_projects (market);

CREATE TABLE IF NOT EXISTS planning.festival_project_stages (
    stage_key           VARCHAR PRIMARY KEY,
    project_key         VARCHAR NOT NULL,
    stage_name          VARCHAR NOT NULL,
    capacity_claim      DOUBLE,
    capacity_evidence_class VARCHAR,
    indoor_outdoor      VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_stages_project ON planning.festival_project_stages (project_key);

CREATE TABLE IF NOT EXISTS planning.festival_candidate_artists (
    candidate_key       VARCHAR PRIMARY KEY,
    project_key         VARCHAR NOT NULL,
    artist_key          VARCHAR,
    artist_name         VARCHAR NOT NULL,
    musicbrainz_id      VARCHAR,
    inclusion_reasons   JSON,
    availability_status VARCHAR NOT NULL DEFAULT 'UNKNOWN',
    availability_evidence JSON,
    scorecard_snapshot  JSON,
    added_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_candidates_project ON planning.festival_candidate_artists (project_key);
CREATE INDEX IF NOT EXISTS idx_planning_candidates_artist ON planning.festival_candidate_artists (artist_key);

CREATE TABLE IF NOT EXISTS planning.festival_shortlists (
    shortlist_key       VARCHAR PRIMARY KEY,
    project_key         VARCHAR NOT NULL,
    artist_key          VARCHAR,
    artist_name         VARCHAR NOT NULL,
    status              VARCHAR NOT NULL DEFAULT 'DISCOVERED',
    candidate_day       INTEGER,
    candidate_stage     VARCHAR,
    candidate_billing_tier VARCHAR,
    notes               VARCHAR,
    evidence_snapshot   JSON,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_shortlists_project ON planning.festival_shortlists (project_key);

CREATE TABLE IF NOT EXISTS planning.festival_constraints (
    constraint_key      VARCHAR PRIMARY KEY,
    project_key         VARCHAR NOT NULL,
    constraint_type     VARCHAR NOT NULL,
    description         VARCHAR,
    payload             JSON,
    source              VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_constraints_project ON planning.festival_constraints (project_key);

CREATE TABLE IF NOT EXISTS planning.festival_scenarios (
    scenario_key        VARCHAR PRIMARY KEY,
    project_key         VARCHAR NOT NULL,
    name                VARCHAR NOT NULL,
    notes               VARCHAR,
    slots               JSON,
    warnings            JSON,
    summaries           JSON,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_scenarios_project ON planning.festival_scenarios (project_key);

-- ---------------------------------------------------------------------------
-- Workspace metadata (schema version + provenance).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspace_meta (
    key                 VARCHAR PRIMARY KEY,
    value               VARCHAR
);
INSERT OR REPLACE INTO workspace_meta (key, value) VALUES
    ('schema_version', 'terminal_workspace_v1'),
    ('created_at', CURRENT_TIMESTAMP::VARCHAR);
