-- Migration 045: MARKET_LIQUIDITY_TAPE_V1
-- Turns 33,000+ EVENT IDENTITIES into longitudinal MARKET LIQUIDITY evidence
-- via legitimate official structured APIs (Ticketmaster Discovery first).
--
-- The prior milestone (044) left a canonical event tape
-- (acquisition.event_tape_scale, ~33k events). This migration adds:
--
--   1. market_price_observations  — the ONE neutral observation contract (P4).
--        A single schema captures standard primary ranges, current available
--        inventory ranges, availability state, price basis and inventory basis.
--        STANDARD_PRICE_RANGE and CURRENT_AVAILABLE_INVENTORY_PRICE are kept
--        as DISTINCT semantics — never merged into one generic price.
--   2. artist_marketplace_links   — deterministic, evidence-backed artist →
--        event links for the bootstrap cohort (ID-based via Ticketmaster
--        attraction IDs, NOT bare normalized-name matching).
--   3. source_auth_status         — the P10 rights/cost scorecard: explicit
--        per-provider credential + API authorization state.
--
-- FORBIDDEN SEMANTICS (mirrored from the milestone):
--   * listing-count change is NOT a sale.
--   * listing disappearance is NOT a sale.
--   * public accessibility ≠ automatic commercial permission.
--   * UNKNOWN stays NULL.

-- ============================================================================
-- 1.  Canonical market price observation contract (P4)
-- ============================================================================
CREATE TABLE IF NOT EXISTS acquisition.market_price_observations (
    observation_id           VARCHAR PRIMARY KEY,

    -- Canonical identity
    event_key                VARCHAR NOT NULL,
    artist_key               VARCHAR,
    market_key               VARCHAR,
    marketplace              VARCHAR NOT NULL,      -- ticketmaster | seatgeek | stubhub | ...
    provider_event_id        VARCHAR,

    -- Observation timing (all UTC ISO-8601)
    observed_at              TIMESTAMP NOT NULL,    -- conceptual time of the state
    available_at             TIMESTAMP,             -- period the state applied to (e.g. onsale window)
    retrieved_at             TIMESTAMP NOT NULL,    -- when we actually pulled it
    knowledge_time           TIMESTAMP NOT NULL,    -- retrieval-time knowledge (no backfill of futures)

    -- STANDARD PRIMARY RANGE (listed face-value price band from the primary seller)
    standard_primary_min     DOUBLE,
    standard_primary_max     DOUBLE,
    primary_currency         VARCHAR,

    -- CURRENT AVAILABLE INVENTORY PRICE (min/max of currently-available tickets)
    -- Only populated where the marketplace legitimately exposes current
    -- inventory pricing. NOT the same semantics as the standard primary range.
    current_available_min    DOUBLE,
    current_available_max    DOUBLE,
    inventory_currency       VARCHAR,
    listings_extend_beyond_max  BOOLEAN,            -- equivalent of listingsExtendBeyondMax where exposed

    -- Public market statistics (event-level aggregate; never seat-level)
    listing_count            INTEGER,               -- marketplace LISTING proxy, NOT tickets sold
    average_public_offer     DOUBLE,
    lowest_public_offer      DOUBLE,
    highest_public_offer     DOUBLE,

    -- State / basis semantics (never overstate)
    availability_state       VARCHAR,               -- ONSALE | OFFSALE | CANCELLED | POSTPONED | RESCHEDULED | SOLD_OUT | UNKNOWN
    event_status             VARCHAR,
    price_basis              VARCHAR,               -- STANDARD_PRICE_RANGE | CURRENT_AVAILABLE_INVENTORY_PRICE | PUBLIC_MARKET_STATS
    inventory_basis          VARCHAR,               -- NONE | AGGREGATE_PUBLIC_STATS | NOT_EXPOSED

    -- Provenance / governance
    source                   VARCHAR,               -- ticketmaster_discovery_v2 | seatgeek_official_api | ...
    source_origin            VARCHAR,               -- provider record's own source
    raw_evidence_ref         VARCHAR,               -- provider raw payload hash / canonical_url
    canonical_url            VARCHAR,
    promoter                 VARCHAR,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    account_id               VARCHAR,               -- which key/account produced this (empty = anonymous)
    software_version         VARCHAR,
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mpo_event_market
    ON acquisition.market_price_observations(event_key, marketplace, observed_at);
CREATE INDEX IF NOT EXISTS idx_mpo_event_time
    ON acquisition.market_price_observations(event_key, observed_at);
CREATE INDEX IF NOT EXISTS idx_mpo_market
    ON acquisition.market_price_observations(marketplace, observed_at);

-- ============================================================================
-- 2.  Artist ↔ marketplace event links (bootstrap cohort identity, P3/P5)
-- ============================================================================
-- Defensible artist→event linkage, stored with method + confidence + status.
-- An accepted link MUST be evidence-backed (e.g. exact Ticketmaster attraction
-- ID match between the identity master and the event's attractions). Bare
-- normalized-name matching may only produce a CANDIDATE, never a VERIFIED row.
CREATE TABLE IF NOT EXISTS acquisition.artist_marketplace_links (
    link_key                 VARCHAR PRIMARY KEY,
    artist_key               VARCHAR NOT NULL,
    artist_name              VARCHAR,
    event_key                VARCHAR NOT NULL,
    market_key               VARCHAR,
    event_date               DATE,
    marketplace              VARCHAR NOT NULL,      -- source of the link evidence
    link_basis               VARCHAR NOT NULL,      -- TICKETMASTER_ATTRACTION_ID | NAME_CANDIDATE_ONLY | ...
    link_status              VARCHAR NOT NULL,      -- VERIFIED | CANDIDATE | AMBIGUOUS
    confidence               FLOAT,
    evidence_ref             VARCHAR,               -- provider event id / attraction id used
    first_seen_at            TIMESTAMP,
    last_verified_at         TIMESTAMP,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (artist_key, event_key, marketplace)
);

CREATE INDEX IF NOT EXISTS idx_aml_event
    ON acquisition.artist_marketplace_links(event_key);
CREATE INDEX IF NOT EXISTS idx_aml_artist
    ON acquisition.artist_marketplace_links(artist_key, market_key);

-- ============================================================================
-- 3.  Source auth status / rights + cost scorecard (P10)
-- ============================================================================
CREATE TABLE IF NOT EXISTS acquisition.source_auth_status (
    status_id                VARCHAR PRIMARY KEY,
    provider                 VARCHAR NOT NULL,      -- ticketmaster | seatgeek | stubhub | youtube | ...
    provider_kind            VARCHAR,               -- discovery_api | inventory_api | platform_api | listing_api
    credential_state         VARCHAR NOT NULL,      -- CONFIGURED | ABSENT | INVALID | NOT_PROBED
    auth_state               VARCHAR NOT NULL,      -- AUTHORIZED | NOT_AUTHORIZED | BLOCKED | ENDPOINT_UNREACHABLE | NOT_APPLICABLE
    api_calls                INTEGER DEFAULT 0,
    browser_calls            INTEGER DEFAULT 0,
    monid_calls              INTEGER DEFAULT 0,
    cost_usd                 DOUBLE DEFAULT 0.0,
    useful_observations      INTEGER DEFAULT 0,
    detail                   VARCHAR,
    checked_at               TIMESTAMP NOT NULL,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, provider_kind)
);

-- ============================================================================
-- 4.  Forward artist tape (P7) — latest daily observation per artist per feed
-- ============================================================================
CREATE TABLE IF NOT EXISTS metrics.artist_forward_tape (
    tape_key                 VARCHAR PRIMARY KEY,
    artist_key               VARCHAR NOT NULL,
    artist_name              VARCHAR,
    feed                     VARCHAR NOT NULL,      -- wiki_daily | listenbrainz | youtube_channel | artist_security
    period_date              DATE,                  -- the daily observation's period date
    period_start             TIMESTAMP,
    period_end               TIMESTAMP,
    value                    DOUBLE,
    value_unit               VARCHAR,
    metric_kind              VARCHAR,
    retrieved_at             TIMESTAMP,
    freshness_days           INTEGER,               -- days between period and retrieval
    status                   VARCHAR NOT NULL,      -- OBSERVED | NO_DATA | BLOCKED
    detail                   VARCHAR,
    software_version         VARCHAR,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (artist_key, feed, period_date)
);

-- ============================================================================
-- 5.  Product join columns on artist × market security (P8)
-- ============================================================================
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS marketplace_count INTEGER;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS price_observation_count INTEGER;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS latest_tm_standard_min DOUBLE;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS latest_tm_standard_max DOUBLE;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS latest_tm_onsale_state VARCHAR;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS latest_market_evidence_at TIMESTAMP;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS price_evidence_freshness_days INTEGER;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS seatgeek_listing_count INTEGER;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS seatgeek_lowest_price DOUBLE;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS seatgeek_average_price DOUBLE;
ALTER TABLE asm.artist_market_security_v1 ADD COLUMN IF NOT EXISTS seatgeek_highest_price DOUBLE;