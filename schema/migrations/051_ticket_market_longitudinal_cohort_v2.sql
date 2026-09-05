-- Migration 051: TICKET_MARKET_LONGITUDINAL_COHORT_V2
-- Versioned cohort freeze + collection run ledger for the longitudinal tape.
--
-- Does NOT replace event_identifiers (canonical security master) or
-- ticket_market_snapshots / marketplace_listing_observations (append-only tape).
-- Adds operational accounting required for production-proven collection.

-- ==========================================================================
-- 1. Cohort versions (frozen selection artifacts)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.ticket_market_cohort_versions (
    cohort_version           VARCHAR PRIMARY KEY,   -- e.g. TICKET_MARKET_COHORT_V2_20260905
    generated_at             TIMESTAMP NOT NULL,
    code_commit              VARCHAR,
    selection_rules_json     JSON NOT NULL,
    cohort_hash              VARCHAR NOT NULL,       -- sha256 of sorted pair identities
    n_events                 INTEGER NOT NULL,
    n_pairs                  INTEGER NOT NULL,
    n_marketplaces           INTEGER NOT NULL,
    n_markets                INTEGER NOT NULL,
    lifecycle_json           JSON,
    marketplace_json         JSON,
    market_json              JSON,
    notes                    VARCHAR
);

CREATE TABLE IF NOT EXISTS acquisition.ticket_market_cohort_pairs (
    cohort_version           VARCHAR NOT NULL,
    event_key                VARCHAR NOT NULL,
    marketplace              VARCHAR NOT NULL,
    provider_event_id        VARCHAR,
    marketplace_event_url    VARCHAR,
    mapping_status           VARCHAR NOT NULL,
    mapping_method           VARCHAR,
    confidence               FLOAT,
    lifecycle_bucket         VARCHAR,
    market_key               VARCHAR,
    city                     VARCHAR,
    event_date               DATE,
    artist_name              VARCHAR,
    venue_name               VARCHAR,
    genre                    VARCHAR,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    evidence_json            JSON,
    PRIMARY KEY (cohort_version, event_key, marketplace)
);

CREATE INDEX IF NOT EXISTS idx_cohort_pairs_mp
    ON acquisition.ticket_market_cohort_pairs(cohort_version, marketplace);
CREATE INDEX IF NOT EXISTS idx_cohort_pairs_life
    ON acquisition.ticket_market_cohort_pairs(cohort_version, lifecycle_bucket);

-- ==========================================================================
-- 2. Collection run ledger (logical tasks != HTTP requests)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.ticket_market_collection_runs (
    run_id                   VARCHAR PRIMARY KEY,
    started_at               TIMESTAMP NOT NULL,
    completed_at             TIMESTAMP,
    code_commit              VARCHAR,
    deployment_identity      VARCHAR,
    cohort_version           VARCHAR,
    rail                     VARCHAR NOT NULL,      -- FAST | DEEP
    wave_label               VARCHAR,

    candidate_pairs          INTEGER,
    due_pairs                INTEGER,
    queued_pairs             INTEGER,
    attempted_pairs          INTEGER,
    succeeded_pairs          INTEGER,
    failed_pairs             INTEGER,
    retry_count              INTEGER,
    http_request_count       INTEGER,               -- distinct from logical tasks
    provider_call_count      INTEGER,
    bytes_downloaded         INTEGER,
    raw_evidence_objects     INTEGER,
    normalized_observations  INTEGER,

    spend_usd                DOUBLE,
    budget_cap_usd           DOUBLE,
    budget_remaining_usd     DOUBLE,

    error_classes_json       JSON,
    notes                    VARCHAR,
    status                   VARCHAR NOT NULL DEFAULT 'RUNNING'  -- RUNNING | COMPLETE | ABORTED | BUDGET_STOPPED
);

CREATE INDEX IF NOT EXISTS idx_tm_runs_cohort
    ON acquisition.ticket_market_collection_runs(cohort_version, started_at);

-- ==========================================================================
-- 3. Pair observation schedule state (lifecycle cadence)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.ticket_market_pair_schedule (
    event_key                VARCHAR NOT NULL,
    marketplace              VARCHAR NOT NULL,
    cohort_version           VARCHAR,
    lifecycle_bucket         VARCHAR,
    cadence_label            VARCHAR,               -- weekly | 2x_week | daily | 2x_day | ...
    next_due_at              TIMESTAMP,
    last_attempted_at        TIMESTAMP,
    last_succeeded_at        TIMESTAMP,
    observation_count        INTEGER DEFAULT 0,
    consecutive_failures     INTEGER DEFAULT 0,
    last_error_class         VARCHAR,
    PRIMARY KEY (event_key, marketplace)
);

CREATE INDEX IF NOT EXISTS idx_tm_sched_due
    ON acquisition.ticket_market_pair_schedule(next_due_at);

-- Status taxonomy already on event_identifiers; ensure RIGHTS_BLOCKED / UNSUPPORTED
-- are documented as valid mapping_status values (no schema change required).
-- Disappearance semantics remain LISTING_NO_LONGER_OBSERVED / LISTING_DISAPPEARED
-- — never SOLD.
