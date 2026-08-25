-- Migration 040: Marketplace Event URL Mappings V1
-- Resolves a canonical event ONCE to its exact marketplace event page,
-- so the recurring observation is a targeted fetch, not a search.
--
-- Search is a DISCOVERY operation (one-time).
-- Fetching is the RECURRING operation (daily snapshots).
--
-- Statuses: MATCHED_EXACT | MATCHED_HIGH_CONFIDENCE | AMBIGUOUS | NOT_FOUND | STALE
-- Never auto-promote fuzzy candidates.

CREATE TABLE IF NOT EXISTS acquisition.marketplace_event_mappings (
    mapping_id               VARCHAR PRIMARY KEY,

    -- Canonical side
    event_key                VARCHAR NOT NULL,
    artist_key               VARCHAR,
    venue_key                VARCHAR,
    market_key               VARCHAR,

    -- Marketplace side
    marketplace              VARCHAR NOT NULL,      -- seatgeek.com, vividseats.com, stubhub.com, ...
    marketplace_event_id     VARCHAR,
    marketplace_event_url    VARCHAR,

    -- Resolution provenance
    resolution_method        VARCHAR,               -- MONID_TINYFISH_SEARCH, MONID_CONTEXT_SEARCH, ...
    resolution_status        VARCHAR NOT NULL,      -- MATCHED_EXACT, MATCHED_HIGH_CONFIDENCE, AMBIGUOUS, NOT_FOUND, STALE
    resolution_confidence    FLOAT,
    validation_checked       VARCHAR,               -- which of artist/venue/city/date were validated
    source_query             VARCHAR,
    source_result_url        VARCHAR,

    -- Timing
    resolved_at              TIMESTAMP,
    last_verified_at         TIMESTAMP,

    -- Governance
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    notes                    VARCHAR,

    -- Bookkeeping
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mapping_event
    ON acquisition.marketplace_event_mappings(event_key, marketplace);
CREATE INDEX IF NOT EXISTS idx_mapping_url
    ON acquisition.marketplace_event_mappings(marketplace_event_url);
