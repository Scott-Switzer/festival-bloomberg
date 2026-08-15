-- ===========================================================================
-- 021_historical_decision_evidence_engine_v1.sql
-- ===========================================================================
-- HISTORICAL_DECISION_EVIDENCE_ENGINE_V1.
--
-- The cutoff taxonomy (migration 020) is now correct, but historical
-- ANNOUNCEMENT / PRESALE / GENERAL_ONSALE coverage is still 0 because the
-- system stores evidence once found but is not yet able to FIND the missing
-- evidence with auditable provenance. This migration adds the two layers that
-- make extraction auditable end-to-end:
--
--   1. flywheel.evidence_documents — IMMUTABLE DOCUMENT STORE. Every fetched
--      source document is persisted once, content-addressed. The content hash
--      + crawl/publication metadata prove exactly WHAT was read, WHEN, and
--      under WHICH rights. A document is never rewritten.
--
--   2. flywheel.evidence_claims — CLAIM SUPPORT GRAPH. Every candidate claim
--      (from a deterministic extractor OR an LLM) points back at the exact
--      document and character span that supports it. LLM output NEVER writes
--      evidence directly: a claim can only be persisted here, with
--      verification_status set by DETERMINISTIC code (ACCEPTED / REJECTED).
--
-- An accepted claim can then be promoted into
-- flywheel.pre_event_cutoff_evidence by the deterministic verifier; that
-- promotion is append-only and never bypasses verification.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS flywheel;

-- ---------------------------------------------------------------------------
-- Immutable document store (append-only, content-addressed)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.evidence_documents (
    document_id             VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR NOT NULL,
    source_url              VARCHAR NOT NULL,
    archive_url             VARCHAR,
    archive_capture_time    TIMESTAMP,
        -- archive/first-seen capture proves existence BY this time, never
        -- original publication
    document_content_hash   VARCHAR NOT NULL,
    content_kind            VARCHAR,
        -- JSONLD | OPENTABLE | HTML | ARTICLE | TICKET_JSON | RSS | OTHER
    source_publication_time TIMESTAMP,
    retrieved_at            TIMESTAMP NOT NULL,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    parser_version          VARCHAR,
    knowledge_time          TIMESTAMP NOT NULL,
    UNIQUE (document_content_hash, source_url)
);

CREATE INDEX IF NOT EXISTS idx_evidence_documents_event
  ON flywheel.evidence_documents (canonical_event_id, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_evidence_documents_hash
  ON flywheel.evidence_documents (document_content_hash);

-- ---------------------------------------------------------------------------
-- Claim support graph (append-only; verification is deterministic)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.evidence_claims (
    claim_id                VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR NOT NULL,
    cutoff_type             VARCHAR NOT NULL,
        -- BOOKING_OR_OFFER | ANNOUNCEMENT | PRESALE | GENERAL_ONSALE |
        -- TICKET_PRICE_OBSERVATION | EVENT_DATE | RESULT_PUBLICATION |
        -- SETTLEMENT
    candidate_value         VARCHAR,
    lower_bound             TIMESTAMP,
    upper_bound             TIMESTAMP,
    granularity             VARCHAR NOT NULL,
        -- EXACT | DAY | MONTH
    evidence_class          VARCHAR NOT NULL,
        -- PIT taxonomy (OBSERVED_EXACT/DAY/MONTH, ARCHIVE_CAPTURE_UPPER_BOUND,
        -- SOURCE_PERIOD_BOUND, ESTIMATED_RESEARCH_ONLY, UNKNOWN)
    source_provider         VARCHAR,
    source_url              VARCHAR,
    archive_url             VARCHAR,
    source_document_id      VARCHAR NOT NULL,
        -- MUST reference an evidence_documents row (provenance)
    document_content_hash   VARCHAR,
    source_publication_time TIMESTAMP,
    archive_capture_time    TIMESTAMP,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    evidence_span_start     INTEGER,
    evidence_span_end       INTEGER,
    evidence_span_hash      VARCHAR,
    extractor_kind          VARCHAR NOT NULL,
        -- DETERMINISTIC_JSONLD | DETERMINISTIC_OPENTABLE |
        -- DETERMINISTIC_DATE_LANG | DEEPSEEK_V4_PRO
    extractor_version       VARCHAR,
    model_provider          VARCHAR,
    model_name              VARCHAR,
    model_response_hash     VARCHAR,
    entity_resolution_confidence VARCHAR,
    semantic_confidence     VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    verification_status     VARCHAR NOT NULL,
        -- PENDING | ACCEPTED | REJECTED (set by deterministic code ONLY)
    rejection_reason        VARCHAR,
    software_version        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_evidence_claims_event
  ON flywheel.evidence_claims (canonical_event_id, verification_status);
CREATE INDEX IF NOT EXISTS idx_evidence_claims_document
  ON flywheel.evidence_claims (source_document_id);
