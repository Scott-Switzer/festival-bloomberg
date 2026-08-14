-- ===========================================================================
-- 013_historical_outcome_laboratory_v1.sql
-- ===========================================================================
-- Append-only OUTCOME CLAIM ledger for the Historical Laboratory.
--
-- The existing economics.event_outcome_observations table is a coarse,
-- single-row-per-event summary. This layer records individual source-backed
-- CLAIMS: one row per source assertion, never overwritten. Conflicting
-- sources coexist; supersession is recorded, not deletion.
--
-- Semantics are deliberately separated:
--   attendance  != capacity
--   paid attendance != scanned attendance != reported attendance
--   tickets sold  != attendance
--   OFFSALE      != SOLD_OUT
--   permit capacity != actual attendance
--   expected attendance != actual attendance
--   setlist presence != attendance
-- ===========================================================================

CREATE TABLE IF NOT EXISTS economics.event_outcome_claims (
    claim_id                VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR NOT NULL,

    outcome_type            VARCHAR NOT NULL,

    value_numeric           DOUBLE,
    value_text              VARCHAR,
    unit                    VARCHAR,
    currency                VARCHAR,

    attendance_definition   VARCHAR,
    ticket_definition       VARCHAR,
    revenue_definition      VARCHAR,
    capacity_definition     VARCHAR,

    source_provider         VARCHAR,
    source_name             VARCHAR,
    source_url              VARCHAR,
    source_document_id      VARCHAR,

    event_time              TIMESTAMP,
    source_publication_time TIMESTAMP,
    source_as_of            TIMESTAMP,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    valid_from              TIMESTAMP,
    valid_to                TIMESTAMP,

    evidence_observation_id VARCHAR,
    raw_payload_hash        VARCHAR,

    source_quality          VARCHAR NOT NULL,
    claim_confidence        VARCHAR,
    entity_resolution_confidence VARCHAR,

    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    observation_class       VARCHAR NOT NULL,

    is_censored             BOOLEAN,
    censoring_type          VARCHAR,
    censoring_threshold     VARCHAR,

    conflict_group_id       VARCHAR,
    supersedes_claim_id     VARCHAR,

    notes                   VARCHAR,
    software_version        VARCHAR
);

-- PIT decision cutoffs: what was knowable before each historical decision
-- point. These are per-event reconstructions, never backdated knowledge.
CREATE TABLE IF NOT EXISTS economics.event_decision_cutoffs (
    event_id                VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR NOT NULL,
    booking_cutoff          TIMESTAMP,
    announcement_cutoff     TIMESTAMP,
    onsale_cutoff           TIMESTAMP,
    event_cutoff            TIMESTAMP,
    cutoff_notes            VARCHAR,
    software_version        VARCHAR,
    knowledge_time          TIMESTAMP NOT NULL
);

-- Private (customer) outcome imports are stored in the same ledger with
-- observation_class = OBSERVED_PRIVATE. Public observations are
-- OBSERVED_PUBLIC. They are never merged or cross-classified implicitly.
CREATE INDEX IF NOT EXISTS idx_outcome_claims_event
  ON economics.event_outcome_claims (canonical_event_id, knowledge_time);
CREATE INDEX IF NOT EXISTS idx_outcome_claims_type
  ON economics.event_outcome_claims (outcome_type, source_quality);
CREATE INDEX IF NOT EXISTS idx_outcome_claims_conflict
  ON economics.event_outcome_claims (conflict_group_id);
CREATE INDEX IF NOT EXISTS idx_decision_cutoffs_event
  ON economics.event_decision_cutoffs (canonical_event_id, knowledge_time);
