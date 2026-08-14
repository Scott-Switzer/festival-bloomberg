-- ===========================================================================
-- 014_design_partner_retrospective_v1.sql
-- ===========================================================================
-- Design Partner Retrospective V1: the private-data flywheel.
--
-- Lets a promoter/venue/festival hand us historical show data and receive an
-- immediate data audit + blind-retrospective package. Private outcomes are
-- written into economics.event_outcome_claims as OBSERVED_PRIVATE (migration
-- 013) and are NEVER pooled with public observations by default.
--
-- This migration adds the tenant/dataset lineage, the outcome vault (hidden
-- outcomes), retrospective study objects, and training-row eligibility.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Tenant / dataset boundary
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS economics.customer_datasets (
    dataset_id              VARCHAR PRIMARY KEY,
    customer_id             VARCHAR NOT NULL,
    sharing_policy          VARCHAR NOT NULL,  -- PRIVATE_ONLY | ANONYMIZED_POOL_OPT_IN | AGGREGATE_BENCHMARK_OPT_IN
    source_system           VARCHAR,
    notes                   VARCHAR,
    created_at              TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS economics.customer_source_files (
    file_id                 VARCHAR PRIMARY KEY,
    dataset_id              VARCHAR NOT NULL,
    file_name               VARCHAR,
    format                  VARCHAR,          -- csv | tsv | xlsx
    row_count               INTEGER,
    raw_content_hash        VARCHAR,
    created_at              TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS economics.import_ingestion_runs (
    ingestion_run_id        VARCHAR PRIMARY KEY,
    dataset_id              VARCHAR NOT NULL,
    software_version        VARCHAR,
    created_at              TIMESTAMP NOT NULL
);

-- Quarantined PII columns (buyer name/email/phone/address/card data). These
-- are never ingested into analytical tables; only the column name + reason is
-- recorded (never the value).
CREATE TABLE IF NOT EXISTS economics.pii_quarantine (
    quarantine_id           VARCHAR PRIMARY KEY,
    file_id                 VARCHAR NOT NULL,
    column_name             VARCHAR NOT NULL,
    reason                  VARCHAR,
    sample_count            INTEGER,
    created_at              TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------------------
-- Retrospective study object (no model training; configuration only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS economics.retrospective_studies (
    study_id                VARCHAR PRIMARY KEY,
    customer_id             VARCHAR NOT NULL,
    dataset_id              VARCHAR NOT NULL,
    target                  VARCHAR NOT NULL,  -- controlled outcome type
    study_population        VARCHAR,          -- JSON list of eligibility filters
    decision_cutoff_type    VARCHAR NOT NULL, -- BOOKING | ANNOUNCEMENT | ONSALE | EVENT
    hidden_outcomes         VARCHAR NOT NULL, -- JSON list of outcome types hidden from feature side
    allowed_private_inputs  VARCHAR,          -- JSON list of outcome types usable as inputs
    event_ids               VARCHAR,          -- JSON list of canonical event ids
    feature_policy_version  VARCHAR,
    source_policy_version   VARCHAR,
    status                  VARCHAR NOT NULL, -- DRAFT | VALIDATING | FROZEN | READY_FOR_BASELINES | BLOCKED | SCORED
    created_at              TIMESTAMP NOT NULL,
    frozen_at               TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Outcome vault: realized outcomes are stored here and logically separated
-- from retrospective inputs. A claim is "hidden" from the feature side until
-- it is revealed for scoring.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS economics.outcome_vault (
    vault_id                VARCHAR PRIMARY KEY,
    study_id                VARCHAR NOT NULL,
    canonical_event_id      VARCHAR NOT NULL,
    claim_id                VARCHAR NOT NULL,
    outcome_type            VARCHAR NOT NULL,
    hidden                  BOOLEAN NOT NULL DEFAULT TRUE,
    revealed_at             TIMESTAMP,
    created_at              TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------------------
-- Training-row eligibility (model-free): why an event can/cannot become a
-- future out-of-sample row. No model is trained; this is an audit.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS economics.training_row_eligibility (
    study_id                VARCHAR NOT NULL,
    canonical_event_id      VARCHAR NOT NULL,
    eligible                BOOLEAN NOT NULL,
    exclusion_reason        VARCHAR,
    evaluated_at            TIMESTAMP NOT NULL,
    PRIMARY KEY (study_id, canonical_event_id)
);

CREATE INDEX IF NOT EXISTS idx_customer_source_files_dataset
  ON economics.customer_source_files (dataset_id);
CREATE INDEX IF NOT EXISTS idx_outcome_vault_study
  ON economics.outcome_vault (study_id, hidden);
CREATE INDEX IF NOT EXISTS idx_training_row_eligibility_study
  ON economics.training_row_eligibility (study_id, eligible);
