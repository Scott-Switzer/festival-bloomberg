-- Migration 041: TICKET_MARKET_DATA_MOAT_V2
-- Deepens the cross-market event security master and adds the listing-level
-- lifecycle layer needed for the DEEP rail.
--
-- 1. event_identifiers      — one row per (event_key, marketplace): the
--                             provider-side event ID/URL. This is the
--                             cross-market security master (Bloomberg-style):
--                             FI event_key → { TM id, SeatGeek id, Vivid id, ... }.
-- 2. marketplace_listings   — listing-level lifecycle history (DEEP rail):
--                             listing_id per (event, marketplace), first/last
--                             seen, section/row/quantity/price/fees.
--                             Listing disappearance is NOT a sale — classify
--                             only APPEARED / DISAPPEARED / PRICE_CHANGED /
--                             QUANTITY_CHANGED.
-- 3. raw_evidence_store     — content-addressed raw payloads (hash dedup).
--                             Identical payloads reuse one row; new observation
--                             timestamps still get new snapshot rows.
-- 4. source_health_by_method— health ledger split by acquisition method
--                             (MONID_HTML, MONID_FETCH, TICKETS_DEV, APIFY_ACTOR),
--                             because method matters more than marketplace.

-- ==========================================================================
-- 1.  Event identifiers — cross-market security master
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.event_identifiers (
    identifier_id            VARCHAR PRIMARY KEY,
    event_key                VARCHAR NOT NULL,      -- canonical FI event
    marketplace              VARCHAR NOT NULL,      -- ticketmaster, seatgeek, vividseats, stubhub, gametime, tickpick, viagogo, dice, eventbrite, songkick, bandsintown
    marketplace_event_id     VARCHAR,
    marketplace_event_url    VARCHAR,
    mapping_status           VARCHAR NOT NULL,      -- EXACT_PROVIDER_ID, EXACT_PAGE_MATCH, HIGH_CONFIDENCE, AMBIGUOUS, STALE, NOT_FOUND
    mapping_method           VARCHAR,               -- TICKETS_DEV_CATALOG, MONID_SEARCH, PROVIDER_CROSS_ID, MANUAL
    confidence               FLOAT,
    first_resolved_at        TIMESTAMP,
    last_verified_at         TIMESTAMP,
    source_evidence          VARCHAR,               -- source catalog id / query / url that produced this
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_key, marketplace)
);

CREATE INDEX IF NOT EXISTS idx_evt_ids_event
    ON acquisition.event_identifiers(event_key);
CREATE INDEX IF NOT EXISTS idx_evt_ids_marketplace
    ON acquisition.event_identifiers(marketplace, marketplace_event_id);

-- ==========================================================================
-- 2.  Marketplace listings — DEEP rail (listing-level lifecycle)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.marketplace_listings (
    listing_id               VARCHAR PRIMARY KEY,   -- marketplace's own listing id if stable, else synthetic
    event_key                VARCHAR NOT NULL,
    marketplace              VARCHAR NOT NULL,      -- seatgeek.com, stubhub.com, vividseats.com, ...
    provider_listing_id      VARCHAR,               -- source listing id ("" if platform doesn't expose)
    inventory_type           VARCHAR,               -- primary | resale
    section                  VARCHAR,
    row_label                VARCHAR,
    seats                    VARCHAR,
    quantity                 INTEGER,
    ticket_price             DOUBLE,                -- per-ticket base price
    fee                      DOUBLE,                -- per-ticket fee
    all_in_price             DOUBLE,                -- per-ticket all-in (total / quantity)
    currency                 VARCHAR,
    first_seen_at            TIMESTAMP NOT NULL,
    last_seen_at             TIMESTAMP NOT NULL,
    last_observed_at         TIMESTAMP NOT NULL,
    status                   VARCHAR NOT NULL DEFAULT 'LISTING_APPEARED',  -- APPEARED | DISAPPEARED | PRICE_CHANGED | QUANTITY_CHANGED
    price_history_json       JSON,                  -- [{price, fee, all_in, observed_at}]
    source_snapshot_id       VARCHAR,               -- link to acquisition.ticket_market_snapshots
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_listings_event_market
    ON acquisition.marketplace_listings(event_key, marketplace, provider_listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_seen
    ON acquisition.marketplace_listings(last_seen_at);

-- ==========================================================================
-- 3.  Raw evidence store — content-addressed, hash-deduped
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.raw_evidence_store (
    payload_hash             VARCHAR PRIMARY KEY,   -- sha256 of canonical payload
    marketplace              VARCHAR,
    event_key                VARCHAR,
    payload_type             VARCHAR,               -- HTML, JSONLD, EMBEDDED_JSON, SNAPSHOT_JSON
    payload                  BLOB,                  -- raw page content / structured payload
    byte_size                INTEGER,
    first_seen_at            TIMESTAMP NOT NULL,
    last_seen_at             TIMESTAMP NOT NULL,
    ref_count                INTEGER DEFAULT 1,     -- how many observations reference it
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY'
);

CREATE INDEX IF NOT EXISTS idx_raw_evidence_event
    ON acquisition.raw_evidence_store(event_key, marketplace);

-- ==========================================================================
-- 4.  Source health by method
-- ==========================================================================
CREATE TABLE IF NOT EXISTS acquisition.source_health_by_method (
    health_id                VARCHAR PRIMARY KEY,
    method                   VARCHAR NOT NULL,      -- MONID_HTML, MONID_FETCH, TICKETS_DEV, APIFY_ACTOR
    marketplace              VARCHAR NOT NULL,
    wave_label               VARCHAR,
    started_at               TIMESTAMP NOT NULL,
    finished_at              TIMESTAMP,
    status                   VARCHAR NOT NULL,      -- SUCCESS, PARTIAL, FAILED, BLOCKED
    error_category           VARCHAR,               -- AUTH, RATE_LIMIT, BLOCKED, PARSE_FAILURE, TIMEOUT, SCHEMA_DRIFT
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

CREATE INDEX IF NOT EXISTS idx_health_method
    ON acquisition.source_health_by_method(method, marketplace, started_at);
