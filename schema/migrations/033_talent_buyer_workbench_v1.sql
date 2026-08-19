-- ===========================================================================
-- 033_talent_buyer_workbench_v1.sql
-- ===========================================================================
-- TALENT_BUYER_WORKBENCH_V1 — a versioned festival-planning workspace.
--
-- This migration adds PLANNING entities ONLY. Historical festival records
-- (core.festivals / core.festival_editions / core.lineup_slots) are never
-- mutated. A planning project is a hypothetical workspace — synthetic
-- scenarios are explicitly marked, never presented as official festival data.
--
-- Semantics preserved from the product:
--   UNKNOWN != 0 (availability: NO_CONFLICT_OBSERVED != AVAILABLE)
--   every row carries provenance / knowledge_time
--   no fabricated economics (talent_budget stays NULL when unknown)
--   scenario board validates conflicts but does NOT optimize
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS planning;

-- ---------------------------------------------------------------------------
-- 1. Festival planning projects (synthetic or research workspaces).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.festival_projects (
    project_key         VARCHAR PRIMARY KEY,   -- hash(name, city, created_at)
    name                VARCHAR NOT NULL,
    city                VARCHAR,
    market              VARCHAR,
    venue_site          VARCHAR,
    start_date          DATE,
    end_date            DATE,
    num_days            INTEGER,
    num_stages          INTEGER,
    talent_budget_usd   DOUBLE,                -- NULL = UNKNOWN (never 0)
    genre_objectives    JSON,                  -- ["rock","electronic",...] if known
    target_audience     VARCHAR,
    min_billing_tier    VARCHAR,               -- e.g. "MID_CARD"
    max_billing_tier    VARCHAR,               -- e.g. "HEADLINE"
    notes               VARCHAR,
    scenario_class      VARCHAR NOT NULL DEFAULT 'SYNTHETIC_PLANNING_SCENARIO',
                                                -- SYNTHETIC_PLANNING_SCENARIO|RESEARCH
    is_official         BOOLEAN NOT NULL DEFAULT FALSE,  -- never TRUE for synthetic
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_projects_market ON planning.festival_projects (market);

-- ---------------------------------------------------------------------------
-- 2. Project stages (hypothetical schedule skeleton).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.festival_project_stages (
    stage_key           VARCHAR PRIMARY KEY,   -- hash(project_key, stage_name)
    project_key         VARCHAR NOT NULL,
    stage_name          VARCHAR NOT NULL,
    capacity_claim      DOUBLE,                -- claimed stage capacity (a CLAIM)
    capacity_evidence_class VARCHAR,           -- OBSERVED|DERIVED|ESTIMATED|UNKNOWN
    indoor_outdoor      VARCHAR,               -- INDOOR|OUTDOOR|UNKNOWN
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_stages_project ON planning.festival_project_stages (project_key);

-- ---------------------------------------------------------------------------
-- 3. Candidate artist universe per project.
--    Deterministic + explainable: every candidate carries inclusion reasons
--    with their evidence. Availability is never invented.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.festival_candidate_artists (
    candidate_key       VARCHAR PRIMARY KEY,   -- hash(project_key, artist_key)
    project_key         VARCHAR NOT NULL,
    artist_key          VARCHAR,
    artist_name         VARCHAR NOT NULL,
    musicbrainz_id      VARCHAR,
    inclusion_reasons   JSON,                  -- [{reason, evidence, source}]
    availability_status VARCHAR NOT NULL DEFAULT 'UNKNOWN',
                                                -- CONFIRMED_CONFLICT|POSSIBLE_CONFLICT
                                                -- |NO_CONFLICT_OBSERVED|UNKNOWN
    availability_evidence JSON,                -- [{type, detail, source, knowledge_time}]
    scorecard_snapshot  JSON,                  -- snapshot of the scorecard at add time
    added_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_candidates_project ON planning.festival_candidate_artists (project_key);
CREATE INDEX IF NOT EXISTS idx_planning_candidates_artist ON planning.festival_candidate_artists (artist_key);

-- ---------------------------------------------------------------------------
-- 4. Professional shortlists (upgraded watchlists for planning).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.festival_shortlists (
    shortlist_key       VARCHAR PRIMARY KEY,   -- hash(project_key, artist_key)
    project_key         VARCHAR NOT NULL,
    artist_key          VARCHAR,
    artist_name         VARCHAR NOT NULL,
    status              VARCHAR NOT NULL DEFAULT 'DISCOVERED',
                                                -- DISCOVERED|RESEARCHING|INTEREST|HOLD
                                                -- |CONTACTED|PASSED|SHORTLIST|UNKNOWN
    candidate_day       INTEGER,
    candidate_stage     VARCHAR,
    candidate_billing_tier VARCHAR,
    notes               VARCHAR,
    evidence_snapshot   JSON,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_shortlists_project ON planning.festival_shortlists (project_key);

-- ---------------------------------------------------------------------------
-- 5. Planning constraints (declared, not fabricated).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.festival_constraints (
    constraint_key      VARCHAR PRIMARY KEY,   -- hash(project_key, type, description)
    project_key         VARCHAR NOT NULL,
    constraint_type     VARCHAR NOT NULL,      -- STAGE_CAPACITY|ARTIST_AVAILABILITY|ROUTING|BUDGET|GENRE|BILLING_TIER|MARKET
    description         VARCHAR,
    payload             JSON,
    source              VARCHAR,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_constraints_project ON planning.festival_constraints (project_key);

-- ---------------------------------------------------------------------------
-- 6. Scenario boards (NON-OPTIMIZING). Slots are hypothetical placements;
--    warnings are shown, never fabricated hard constraints.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning.festival_scenarios (
    scenario_key        VARCHAR PRIMARY KEY,   -- hash(project_key, name)
    project_key         VARCHAR NOT NULL,
    name                VARCHAR NOT NULL,
    notes               VARCHAR,
    slots               JSON,                  -- [{artist, artist_key, day, stage, slot_label, billing_tier}]
    warnings            JSON,                  -- [{severity, type, detail}]
    summaries           JSON,                  -- counts/distributions over slots
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_planning_scenarios_project ON planning.festival_scenarios (project_key);
