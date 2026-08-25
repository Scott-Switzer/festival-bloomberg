-- Migration 038: Live Entertainment Evidence Rails v1
-- Common append-only observation contract for all external sources.
--
-- All scrapers, APIs, and external providers write into ONE observation table.
-- No source-specific tables.  No overwrites.  Every observation is immutable.
--
-- Repeated scrapes of the same event from the same source produce new rows
-- with new observation timestamps — this is what enables change detection
-- and the long-term information moat.

-- ==========================================================================
-- 1.  External event observations (the core contract)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.external_event_observations (
    observation_id             VARCHAR PRIMARY KEY,
    
    -- Source identity
    source_platform            VARCHAR NOT NULL,       -- songkick.com, dice.fm, eventbrite.com, ...
    acquisition_provider       VARCHAR NOT NULL,       -- apify, monid, ticketmaster_api, ...
    actor_or_endpoint          VARCHAR,                -- scrapesage~eventbrite-scraper (Apify actor ID)
    source_record_id           VARCHAR,                -- external platform's own event ID
    
    -- Our canonical entities (may be NULL until resolved)
    artist_key                 VARCHAR,
    venue_key                  VARCHAR,
    event_key                  VARCHAR,
    market_key                 VARCHAR,
    
    -- Temporal fields
    -- ALL times are UTC ISO-8601.
    observed_at                TIMESTAMP NOT NULL,     -- when we captured this observation
    retrieved_at               TIMESTAMP NOT NULL,     -- when the provider returned the data
    source_publication_time    TIMESTAMP,              -- when the source published the event listing
    announcement_time          TIMESTAMP,              -- when the event was announced
    onsale_time                TIMESTAMP,              -- when tickets went on sale
    event_time                 TIMESTAMP,              -- when the event occurs
    knowledge_time             TIMESTAMP NOT NULL,     -- PIT: what was knowable when (per source)
    
    -- Observation metadata
    observation_type           VARCHAR NOT NULL,       -- EVENT_DISCOVERY, TICKET_PRICE, TICKET_AVAILABILITY, ...
    observation_category       VARCHAR,                -- PRIMARY, RESALE, CAPACITY, LINEUP, PROMOTER, ...
    
    -- Raw data (append-only — never update-in-place)
    raw_payload                JSON,                   -- full raw record from the source
    raw_payload_hash           VARCHAR NOT NULL,       -- content-addressable integrity
    normalized_fields          JSON,                   -- our normalized extraction
    parser_version             VARCHAR,
    
    -- Rights and governance
    rights_status              VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status      VARCHAR DEFAULT 'PROTOTYPE_ONLY',
    
    -- Identity resolution (may be backfilled)
    identity_match_status      VARCHAR DEFAULT 'UNRESOLVED',  -- RESOLVED, AMBIGUOUS, UNRESOLVED
    identity_match_confidence  FLOAT,
    identity_match_method      VARCHAR,                -- ARTIST_NAME_EXACT, VENUE_GEO_FUZZY, EVENT_ID_DIRECT, ...
    
    -- Bookkeeping
    software_version           VARCHAR,
    ingested_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================================================
-- 2.  Change detection ledger
-- ==========================================================================
-- When a new observation differs from a prior one for the same source record,
-- record the DELTA here.  This IS the proprietary information estate.
CREATE TABLE IF NOT EXISTS acquisition.observation_changes (
    change_id                  VARCHAR PRIMARY KEY,
    
    -- What changed
    observation_id_current     VARCHAR NOT NULL REFERENCES acquisition.external_event_observations(observation_id),
    observation_id_previous    VARCHAR NOT NULL REFERENCES acquisition.external_event_observations(observation_id),
    source_record_id           VARCHAR NOT NULL,
    
    -- The entity
    event_key                  VARCHAR,
    source_platform            VARCHAR NOT NULL,
    
    -- What kind of change
    change_type                VARCHAR NOT NULL,       -- PRICE_CHANGED, SOLD_OUT, LISTING_COUNT_CHANGED, ...
    change_category            VARCHAR NOT NULL,       -- TICKET_MARKET, EVENT_METADATA, LINEUP, ...
    
    -- Before and after
    field_name                 VARCHAR NOT NULL,
    value_previous             JSON,
    value_current              JSON,
    change_magnitude           FLOAT,                  -- for numeric fields
    change_direction           VARCHAR,                -- INCREASED, DECREASED, ADDED, REMOVED, CHANGED
    
    -- Timing
    observed_at                TIMESTAMP NOT NULL,
    previous_observed_at       TIMESTAMP NOT NULL,
    hours_between_observations FLOAT,
    
    -- Bookkeeping
    ingested_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================================================
-- 3.  Coverage tracking
-- ==========================================================================
-- Per-event, how many sources cover each dimension?
-- Updated by materialized view or periodic aggregation.
CREATE TABLE IF NOT EXISTS acquisition.event_coverage_snapshot (
    event_key                  VARCHAR PRIMARY KEY,
    snapshot_time              TIMESTAMP NOT NULL,
    
    -- Source counts
    total_sources              INTEGER DEFAULT 0,
    ticketmaster_covered       BOOLEAN DEFAULT FALSE,
    dice_covered               BOOLEAN DEFAULT FALSE,
    eventbrite_covered         BOOLEAN DEFAULT FALSE,
    songkick_covered           BOOLEAN DEFAULT FALSE,
    bandsintown_covered        BOOLEAN DEFAULT FALSE,
    resident_advisor_covered   BOOLEAN DEFAULT FALSE,
    allevents_covered          BOOLEAN DEFAULT FALSE,
    fever_covered              BOOLEAN DEFAULT FALSE,
    
    -- Field completeness
    has_price                  BOOLEAN DEFAULT FALSE,
    has_coordinates            BOOLEAN DEFAULT FALSE,
    has_promoter               BOOLEAN DEFAULT FALSE,
    has_lineup                 BOOLEAN DEFAULT FALSE,
    has_announcement_time      BOOLEAN DEFAULT FALSE,
    has_onsale_time            BOOLEAN DEFAULT FALSE,
    has_capacity               BOOLEAN DEFAULT FALSE,
    has_ticket_url             BOOLEAN DEFAULT FALSE,
    has_sold_out_status        BOOLEAN DEFAULT FALSE,
    
    -- Information advantage scores
    sources_with_price         INTEGER DEFAULT 0,
    sources_with_coordinates   INTEGER DEFAULT 0,
    sources_corroborating      INTEGER DEFAULT 0,
    sources_conflicting        INTEGER DEFAULT 0,
    
    -- Historical depth
    earliest_observation       TIMESTAMP,
    latest_observation         TIMESTAMP,
    observation_count           INTEGER DEFAULT 0,
    days_with_observations      INTEGER DEFAULT 0,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================================================
-- Indexes for performance
-- ==========================================================================
CREATE INDEX IF NOT EXISTS idx_ext_obs_source_record
    ON acquisition.external_event_observations(source_platform, source_record_id);
CREATE INDEX IF NOT EXISTS idx_ext_obs_event
    ON acquisition.external_event_observations(event_key);
CREATE INDEX IF NOT EXISTS idx_ext_obs_observed
    ON acquisition.external_event_observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_ext_obs_type
    ON acquisition.external_event_observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_obs_changes_event
    ON acquisition.observation_changes(event_key, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_changes_type
    ON acquisition.observation_changes(change_type, source_platform);