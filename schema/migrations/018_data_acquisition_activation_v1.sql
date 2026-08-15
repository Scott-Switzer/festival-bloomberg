-- ===========================================================================
-- 018_data_acquisition_activation_v1.sql
-- ===========================================================================
-- Data Acquisition Activation V1.
--
-- DATA_FLYWHEEL_AND_COVERAGE_V1 built the measurement machinery; this
-- milestone turns it into an OPERATING acquisition system. Success is
-- measured by NEW decision-useful evidence, not by schema or plan counts.
--
-- This migration adds the four persistence layers the acquisition process
-- needs (nothing here invents evidence):
--
--   1. flywheel.pit_reconstruction_evidence -- HOW a publication time became
--      knowable. The taxonomy separates a fact from its availability proof:
--      OBSERVED_EXACT / OBSERVED_DAY / OBSERVED_MONTH /
--      ARCHIVE_CAPTURE_UPPER_BOUND / SOURCE_PERIOD_BOUND /
--      ESTIMATED_RESEARCH_ONLY / UNKNOWN. Only classes that prove the claim
--      was knowable before a cutoff may enter STRICT_PIT; archive captures
--      prove availability BY the capture time, never original publication.
--
--   2. flywheel.outcome_hunt_attempts -- append-only execution ledger. Every
--      hunt task attempt is a row; NOT_FOUND means the retrieval genuinely
--      succeeded with no qualifying evidence (a 429 is RATE_LIMITED, a 403
--      is RIGHTS_BLOCKED/AUTH_FAILED, a parse exception is PARSER_FAILED).
--      Prior attempts are never destroyed.
--
--   3. flywheel.provider_acquisition_runs -- per-provider acquisition
--      accounting (requests, successes, new claims, new cutoffs, new warm
--      starts, failures by class, cost, latency, quota).
--
--   4. flywheel.provider_acquisition_metrics -- derived yield per run
--      (new_claims_per_1000_requests, cost_per_new_claim, ...). These are
--      DERIVED rows, never hand-entered; no composite score is invented.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS flywheel;

-- ---------------------------------------------------------------------------
-- 1. PIT reconstruction evidence
-- ---------------------------------------------------------------------------
-- One row per (canonical event, evidence class, source document). Conflicting
-- or complementary evidence classes coexist; they are never collapsed into a
-- single timestamp.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.pit_reconstruction_evidence (
    evidence_id             VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR NOT NULL,
    evidence_class          VARCHAR NOT NULL,
        -- OBSERVED_EXACT | OBSERVED_DAY | OBSERVED_MONTH |
        -- ARCHIVE_CAPTURE_UPPER_BOUND | SOURCE_PERIOD_BOUND |
        -- ESTIMATED_RESEARCH_ONLY | UNKNOWN
    source_publication_time TIMESTAMP,
    archive_capture_time    TIMESTAMP,
    source_period_start     DATE,
    source_period_end       DATE,
    source_url              VARCHAR,
    source_provider         VARCHAR,
    source_document_id      VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    software_version        VARCHAR,
    UNIQUE (canonical_event_id, evidence_class, source_document_id)
);

CREATE INDEX IF NOT EXISTS idx_pit_evidence_event
  ON flywheel.pit_reconstruction_evidence (canonical_event_id);
CREATE INDEX IF NOT EXISTS idx_pit_evidence_class
  ON flywheel.pit_reconstruction_evidence (evidence_class, knowledge_time);

-- ---------------------------------------------------------------------------
-- 2. OUTCOME_HUNTER attempt ledger (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.outcome_hunt_attempts (
    attempt_id              VARCHAR PRIMARY KEY,
    plan_id                 VARCHAR NOT NULL,
    task_id                 VARCHAR NOT NULL,
    target_field            VARCHAR NOT NULL,
    provider                VARCHAR NOT NULL,
    status                  VARCHAR NOT NULL,
        -- SEARCHING | CLAIM_FOUND | NOT_FOUND | RIGHTS_BLOCKED | RATE_LIMITED |
        -- PARSER_FAILED | HTTP_FAILED | AUTH_FAILED | OTHER_FAILURE
    started_at              TIMESTAMP NOT NULL,
    finished_at             TIMESTAMP,
    request_count           INTEGER NOT NULL DEFAULT 0,
    source_url              VARCHAR,
    capture_count           INTEGER,
    claim_id                VARCHAR,
    detail                  VARCHAR,
    raw_payload_hash        VARCHAR,
    software_version        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_hunt_attempts_task
  ON flywheel.outcome_hunt_attempts (task_id, started_at);
CREATE INDEX IF NOT EXISTS idx_hunt_attempts_status
  ON flywheel.outcome_hunt_attempts (status, started_at);

-- ---------------------------------------------------------------------------
-- 3. Per-provider acquisition runs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.provider_acquisition_runs (
    run_id                  VARCHAR PRIMARY KEY,
    provider                VARCHAR NOT NULL,
    pipeline                VARCHAR NOT NULL,
    started_at              TIMESTAMP NOT NULL,
    finished_at             TIMESTAMP,
    requests                INTEGER NOT NULL DEFAULT 0,
    successful_responses    INTEGER NOT NULL DEFAULT 0,
    records_parsed          INTEGER NOT NULL DEFAULT 0,
    new_claims              INTEGER NOT NULL DEFAULT 0,
    new_unique_events_improved INTEGER NOT NULL DEFAULT 0,
    new_cutoffs             INTEGER NOT NULL DEFAULT 0,
    new_warm_start_events   INTEGER NOT NULL DEFAULT 0,
    new_forward_observations INTEGER NOT NULL DEFAULT 0,
    new_ticket_pace_events  INTEGER NOT NULL DEFAULT 0,
    duplicates              INTEGER NOT NULL DEFAULT 0,
    conflicts               INTEGER NOT NULL DEFAULT 0,
    not_found               INTEGER NOT NULL DEFAULT 0,
    rights_blocked          INTEGER NOT NULL DEFAULT 0,
    rate_limited            INTEGER NOT NULL DEFAULT 0,
    parser_failed           INTEGER NOT NULL DEFAULT 0,
    http_failed             INTEGER NOT NULL DEFAULT 0,
    auth_failed             INTEGER NOT NULL DEFAULT 0,
    other_failure           INTEGER NOT NULL DEFAULT 0,
    latency_ms_total        INTEGER NOT NULL DEFAULT 0,
    quota_consumed          INTEGER NOT NULL DEFAULT 0,
    monetary_cost_usd       DOUBLE NOT NULL DEFAULT 0.0,
    detail                  VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_acq_runs_provider
  ON flywheel.provider_acquisition_runs (provider, started_at);

-- ---------------------------------------------------------------------------
-- 4. Derived acquisition-yield metrics (per run, computed, never hand-entered)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.provider_acquisition_metrics (
    metric_id               VARCHAR PRIMARY KEY,
    run_id                  VARCHAR NOT NULL,
    provider                VARCHAR NOT NULL,
    successes_per_1000_requests DOUBLE,
    new_claims_per_1000_requests DOUBLE,
    new_cutoffs_per_1000_requests DOUBLE,
    new_usable_events_per_1000_requests DOUBLE,
    new_warm_starts_per_1000_requests DOUBLE,
    cost_per_new_claim      DOUBLE,
    cost_per_new_usable_event DOUBLE,
    cost_per_new_warm_start DOUBLE,
    knowledge_time          TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_acq_metrics_run
  ON flywheel.provider_acquisition_metrics (run_id);
