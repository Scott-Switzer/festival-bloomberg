-- ===========================================================================
-- 020_pre_event_cutoff_acquisition_v1.sql
-- ===========================================================================
-- PRE_EVENT_CUTOFF_ACQUISITION_V1.
--
-- The binding research question is now PRE-EVENT KNOWABILITY:
--
--     P(Y_show | information available at booking/offer time)
--
-- not the post-show result corpus. The previous milestone reconstructed WHEN
-- results became public (RESULT availability) but not what could have been
-- known BEFORE a promoter decided to book the show.
--
-- This table is the append-only DECISION-TIME CUTOFF evidence ledger. One row
-- per (canonical event, decision cutoff, evidence kind). Cutoff types are
-- NEVER collapsed:
--
--     BOOKING_OR_OFFER | ANNOUNCEMENT | PRESALE | GENERAL_ONSALE |
--     TICKET_PRICE_OBSERVATION | EVENT_DATE | RESULT_PUBLICATION | SETTLEMENT
--
-- The PIT evidence taxonomy is reused (OBSERVED_EXACT / OBSERVED_DAY /
-- OBSERVED_MONTH / ARCHIVE_CAPTURE_UPPER_BOUND / SOURCE_PERIOD_BOUND /
-- ESTIMATED_RESEARCH_ONLY / UNKNOWN) and governs STRICT vs CONSERVATIVE
-- validation exactly as in flywheel.pit_reconstruction_evidence.
--
-- Booking/offer is the hardest field and is NEVER fabricated:
--
--     OBSERVED_BOOKING_DATE / OBSERVED_OFFER_DATE / CONTRACT_DATE /
--     INTERNAL_FIRST_PARTY_BOOKING_DATE   exact first-party evidence
--     ANNOUNCEMENT_UPPER_BOUND            booking happened NO LATER THAN the
--                                         announcement (a BOUND, not a date)
--     FIRST_SEEN_UPPER_BOUND              the event listing existed no later
--                                         than our first retrieval (a BOUND)
--     ESTIMATED_RESEARCH_ONLY / UNKNOWN   never enter strict validation
--
-- Interval evidence is preserved as (lower_bound, upper_bound, bound_semantics)
-- and is NEVER collapsed to a midpoint. ``cutoff_timestamp`` carries only an
-- exact observed instant; a bound-only row leaves it NULL.
--
-- Append-only: prior rows are never rewritten; ``knowledge_time`` keeps every
-- row PIT-queryable. Conflicting cutoff claims coexist.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS flywheel;

CREATE TABLE IF NOT EXISTS flywheel.pre_event_cutoff_evidence (
    cutoff_id             VARCHAR PRIMARY KEY,
    canonical_event_id    VARCHAR NOT NULL,
    source_event_id       VARCHAR,
        -- forward watch_event_id / research engagement_id (provenance)
    cutoff_type           VARCHAR NOT NULL,
        -- BOOKING_OR_OFFER | ANNOUNCEMENT | PRESALE | GENERAL_ONSALE |
        -- TICKET_PRICE_OBSERVATION | EVENT_DATE | RESULT_PUBLICATION | SETTLEMENT
    cutoff_kind           VARCHAR NOT NULL,
        -- OBSERVED | OBSERVED_BOOKING_DATE | OBSERVED_OFFER_DATE |
        -- CONTRACT_DATE | INTERNAL_FIRST_PARTY_BOOKING_DATE |
        -- ANNOUNCEMENT_UPPER_BOUND | FIRST_SEEN_UPPER_BOUND |
        -- ARCHIVE_CAPTURE_UPPER_BOUND | ESTIMATED_RESEARCH_ONLY | UNKNOWN
    evidence_class        VARCHAR NOT NULL,
        -- PIT taxonomy (OBSERVED_EXACT/DAY/MONTH, ARCHIVE_CAPTURE_UPPER_BOUND,
        -- SOURCE_PERIOD_BOUND, ESTIMATED_RESEARCH_ONLY, UNKNOWN)
    granularity           VARCHAR NOT NULL,
        -- EXACT | DAY | MONTH  (precision of the cutoff instant itself)
    cutoff_timestamp      TIMESTAMP,
        -- exact observed instant (NULL for a bound-only / interval / unknown)
    lower_bound           TIMESTAMP,
        -- interval evidence: true decision time >= lower_bound
    upper_bound           TIMESTAMP,
        -- interval evidence: true decision time <= upper_bound
    bound_semantics       VARCHAR,
        -- e.g. 'booking_no_later_than_announcement',
        --      'announcement_no_later_than_first_seen',
        --      'result_available_no_later_than_publication'
    source_provider       VARCHAR,
    source_url            VARCHAR,
    source_document_id    VARCHAR,
    archive_capture_time  TIMESTAMP,
        -- capture/retrieval time proving content existed by then (never the
        -- original publication time)
    retrieved_at          TIMESTAMP,
    knowledge_time        TIMESTAMP NOT NULL,
    rights_status         VARCHAR NOT NULL,
    commercial_use_status VARCHAR NOT NULL,
    confidence            VARCHAR,
    software_version      VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_pre_event_cutoff_event
  ON flywheel.pre_event_cutoff_evidence (canonical_event_id, knowledge_time);
CREATE INDEX IF NOT EXISTS idx_pre_event_cutoff_type
  ON flywheel.pre_event_cutoff_evidence (cutoff_type, evidence_class);
