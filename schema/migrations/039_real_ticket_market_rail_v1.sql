-- Migration 039: Real Ticket Market Rail V1
-- Normalized, append-only ticket-market observations for the watch universe.
--
-- Design:
--   * watch_universe        — immutable frozen set of events under observation
--   * ticket_market_snapshots — per (event, source, wave) normalized market state
--   * source_health_ledger  — per-source run/failure/latency/cost history
--
-- The raw scrape payloads live in acquisition.external_event_observations
-- (migration 038). This migration adds the normalized, queryable layer that
-- feeds the buyer workspace and the information-moat dashboard.
--
-- MARKET DATA SEMANTICS (never overstate):
--   listing_count / ticket_count are MARKETPLACE LISTING PROXIES.
--   They are NOT tickets sold. Do not derive sales from listing deltas.
--   sold_out_flag is a marketplace availability state, not demand.

-- ==========================================================================
-- 1.  Watch universe (frozen, immutable)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.watch_universe (
    watch_universe_version   VARCHAR NOT NULL,
    event_key                VARCHAR NOT NULL,
    provider_event_id        VARCHAR,
    artist_key               VARCHAR,
    artist_name              VARCHAR,
    venue_key                VARCHAR,
    venue_name               VARCHAR,
    market_key               VARCHAR,
    city                     VARCHAR,
    state                    VARCHAR,
    event_date               DATE,
    event_time               VARCHAR,
    timezone                 VARCHAR,
    latitude                 DOUBLE,
    longitude                DOUBLE,
    tm_price_min             DOUBLE,
    tm_price_max             DOUBLE,
    tm_currency              VARCHAR,
    promoter                 VARCHAR,
    genre                    VARCHAR,
    subgenre                 VARCHAR,
    canonical_url            VARCHAR,
    selection_reason         VARCHAR,
    frozen_at                TIMESTAMP NOT NULL,
    content_hash             VARCHAR,
    PRIMARY KEY (watch_universe_version, event_key)
);

-- ==========================================================================
-- 2.  Ticket market snapshots (normalized, append-only per wave)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.ticket_market_snapshots (
    snapshot_id              VARCHAR PRIMARY KEY,

    -- Identity
    watch_universe_version   VARCHAR,
    event_key                VARCHAR,                -- canonical event key (NULL = UNRESOLVED; preserved but not driving buyer series)
    provider_event_id        VARCHAR,                -- TM provider id (universe)
    source_platform          VARCHAR NOT NULL,       -- seatgeek.com, vividseats.com, ...
    actor_or_endpoint        VARCHAR,
    source_record_id         VARCHAR,                -- marketplace's own event id

    -- Wave / timing (all UTC ISO-8601)
    wave_label               VARCHAR,
    observed_at              TIMESTAMP NOT NULL,
    retrieved_at             TIMESTAMP NOT NULL,
    knowledge_time           TIMESTAMP NOT NULL,

    -- Normalized market state (marketplace availability proxies)
    currency                 VARCHAR,
    resale_min_price         DOUBLE,
    resale_median_price      DOUBLE,
    resale_avg_price         DOUBLE,
    resale_max_price         DOUBLE,
    listing_count            INTEGER,
    ticket_count             INTEGER,
    sold_out_flag            BOOLEAN,
    availability_flag        BOOLEAN,
    face_value               DOUBLE,
    all_in_price             DOUBLE,
    section                  VARCHAR,
    row_label                VARCHAR,
    quantity                 INTEGER,

    -- Identity resolution
    identity_match_status    VARCHAR DEFAULT 'UNRESOLVED',  -- MATCHED, AMBIGUOUS, UNRESOLVED
    identity_match_method    VARCHAR,
    identity_match_confidence FLOAT,

    -- Provenance
    source_url               VARCHAR,
    raw_payload_hash         VARCHAR,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    parser_version           VARCHAR,

    -- Bookkeeping
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tm_snap_event_source
    ON acquisition.ticket_market_snapshots(event_key, source_platform, observed_at);
CREATE INDEX IF NOT EXISTS idx_tm_snap_wave
    ON acquisition.ticket_market_snapshots(wave_label, observed_at);

-- ==========================================================================
-- 3.  Source health ledger (per-source run history)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.source_health_ledger (
    run_id                   VARCHAR PRIMARY KEY,
    source_platform          VARCHAR NOT NULL,
    actor_or_endpoint        VARCHAR,
    wave_label               VARCHAR,
    started_at               TIMESTAMP NOT NULL,
    finished_at              TIMESTAMP,
    status                   VARCHAR NOT NULL,       -- SUCCESS, PARTIAL, FAILED
    error_category           VARCHAR,                -- AUTH, RATE_LIMIT, RUN_FAILED, PARSE, TIMEOUT, ...
    error_detail             VARCHAR,
    events_requested         INTEGER,
    events_resolved          INTEGER,
    observations_ingested    INTEGER,
    latency_ms               INTEGER,
    cost_usd                 DOUBLE,
    schema_hash              VARCHAR,
    schema_version           VARCHAR,
    records_returned         INTEGER,
    notes                    VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_health_source_wave
    ON acquisition.source_health_ledger(source_platform, wave_label);
