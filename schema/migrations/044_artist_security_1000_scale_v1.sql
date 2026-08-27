-- ===========================================================================
-- 044_artist_security_1000_scale_v1.sql
-- ===========================================================================
-- ARTIST_SECURITY_1000_SCALE_V1 — real dataset at 1000-artist scale.
--
-- Phase 0 of this milestone proved the SCHEMA. This migration adds the
-- OBJECTS the scale pass needs — nothing abstract:
--
--   1. identity.artist_provider_linkages — the CROSS-PROVIDER IDENTITY
--      MASTER. One row per (artist_key, provider) linkage with the full
--      resolution contract (link_method, confidence, evidence_ref,
--      first_seen_at, last_verified_at, rights_status, commercial_use_status).
--      Resolution policy: a provider ID is NEVER silently resolved by
--      normalized artist name alone; name match generates a CANDIDATE only,
--      ambiguous links FAIL CLOSED (status CANDIDATE/AMBIGUOUS, never
--      promoted).
--
--   2. identity.identity_coverage_scorecard — materialized coverage by
--      provider over the security universe (rebuilt each pass).
--
--   3. asm.artist_market_security_v1 — the ARTIST × MARKET security object
--      (P10): observable factors only (historical shows, days since last
--      market show, market venues played, venue progression, upcoming market
--      events, nearby competing events, ticket evidence). No demand forecast,
--      no booking recommendation.
--
--   4. acquisition.event_tape_scale — the EVENT_TAPE_2000 tracking object
--      (P9): per-event PIT marketplace depth (PIT_EVENT_MARKETPLACE_DAYS,
--      OBSERVATION_DEPTH, MULTI_MARKETPLACE_EVENTS, PAIRS_3_PLUS/5_PLUS/10_PLUS).
--
-- All rights/commercial state travels with every row; UNKNOWN stays NULL.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Cross-provider artist identity master
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identity.artist_provider_linkages (
    linkage_key          VARCHAR PRIMARY KEY,  -- hash(artist_key, provider, provider_id, link_method, version)
    artist_key           VARCHAR NOT NULL,     -- canonical mbid:: / name::
    provider             VARCHAR NOT NULL,     -- MUSICBRAINZ | TICKETMASTER | SPOTIFY | YOUTUBE |
                                               -- WIKIDATA | WIKIPEDIA | LISTENBRAINZ | SOUNDCLOUD | APPLE_MUSIC
    provider_id          VARCHAR NOT NULL,     -- the provider-native identifier
    provider_url         VARCHAR,              -- canonical provider URL when known
    link_method          VARCHAR NOT NULL,     -- LAKE_EXTERNAL_ID | MB_ARTIST_DUMP_URL | PROVIDER_SEARCH_CANDIDATE |
                                               -- MBID_DERIVED | FESTIVAL_DB_REFERENCE | ...
    confidence           DOUBLE,               -- resolution confidence (candidate only; 1.0 only on exact verified match)
    evidence_ref         VARCHAR,              -- evidence URL / source row reference
    resolution_status    VARCHAR NOT NULL DEFAULT 'CANDIDATE',  -- VERIFIED | CANDIDATE | AMBIGUOUS | FAILED
    first_seen_at        TIMESTAMP NOT NULL,
    last_verified_at     TIMESTAMP,
    rights_status        VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    notes                VARCHAR,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_provider_link_artist ON identity.artist_provider_linkages (artist_key, provider);
CREATE INDEX IF NOT EXISTS idx_provider_link_provider ON identity.artist_provider_linkages (provider, provider_id);

-- ---------------------------------------------------------------------------
-- 2. Identity coverage scorecard (per-provider coverage over the universe)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identity.identity_coverage_scorecard (
    scorecard_key        VARCHAR PRIMARY KEY,  -- hash(as_of, universe_version, pass_version)
    as_of                DATE NOT NULL,
    universe_size        INTEGER NOT NULL,
    universe_version     VARCHAR NOT NULL,
    provider             VARCHAR NOT NULL,
    verified_count       INTEGER NOT NULL,     -- resolution_status = 'VERIFIED'
    candidate_count      INTEGER NOT NULL,     -- CANDIDATE (name match, not verified)
    ambiguous_count      INTEGER NOT NULL,     -- AMBIGUOUS (failed closed)
    missing_count        INTEGER NOT NULL,     -- no candidate at all
    coverage_pct         DOUBLE,               -- verified / universe_size
    pass_version         VARCHAR NOT NULL,
    generated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_identity_scorecard ON identity.identity_coverage_scorecard (as_of, provider);

-- ---------------------------------------------------------------------------
-- 3. Artist × market security object (P10) — observable factors only
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asm.artist_market_security_v1 (
    row_key                  VARCHAR PRIMARY KEY,  -- hash(artist_key, market_key, as_of, version)
    artist_key               VARCHAR NOT NULL,
    market_key               VARCHAR NOT NULL,     -- e.g. chicago-il, las-vegas-nv, new-york-ny
    as_of                    DATE NOT NULL,
    -- historical (observable) evidence in the market
    historical_shows         INTEGER,              -- shows with a date < as_of in market
    days_since_last_market_show INTEGER,           -- as_of - last show date (NULL when none)
    market_venues_played     INTEGER,              -- distinct venues in market with shows
    venue_progression        JSON,                 -- historical venue-size/name progression
    -- forward (observable) evidence in the market
    upcoming_market_events   INTEGER,              -- provider events with date >= as_of in market
    nearby_competing_events  INTEGER,              -- other-artist events in market around window
    ticket_evidence_count    INTEGER,              -- marketplace listing observations for artist events in market
    -- provenance
    source_system            VARCHAR NOT NULL,
    source_version           VARCHAR NOT NULL,
    retrieved_at             TIMESTAMP NOT NULL,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    evidence_json            JSON,
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_security_artist ON asm.artist_market_security_v1 (artist_key, market_key, as_of);
CREATE INDEX IF NOT EXISTS idx_market_security_market ON asm.artist_market_security_v1 (market_key, as_of);

-- ---------------------------------------------------------------------------
-- 4. Performance history (P5) — real SHOW observations (dates + venues +
--    cities/markets) from SetlistFM / MusicBrainz events. One row per artist
--    per show. Powers SHOWS_30D/90D/365D, MARKETS_PLAYED, VENUES_PLAYED,
--    DAYS_SINCE_LAST_SHOW and venue progression. Never attendance.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics.artist_performance_observations (
    performance_key      VARCHAR PRIMARY KEY,  -- hash(artist_key, show_date, venue_name, source)
    artist_key           VARCHAR NOT NULL,
    show_date            DATE NOT NULL,        -- EVENT_TIME
    venue_name           VARCHAR,
    venue_key            VARCHAR,
    city                 VARCHAR,
    state_code           VARCHAR,
    country_code         VARCHAR,
    market_key           VARCHAR,              -- derived from city/state where mappable
    event_type           VARCHAR,              -- CONCERT | FESTIVAL | TOUR_DATE | UNKNOWN
    source_system        VARCHAR NOT NULL,     -- setlistfm | musicbrainz
    source_url           VARCHAR,
    retrieved_at         TIMESTAMP NOT NULL,
    rights_status        VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    evidence_json        JSON,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_perf_obs_artist ON metrics.artist_performance_observations (artist_key, show_date);
CREATE INDEX IF NOT EXISTS idx_perf_obs_market ON metrics.artist_performance_observations (market_key, show_date);

-- ---------------------------------------------------------------------------
-- 5. Event tape scale tracking (P9) — EVENT_TAPE_2000
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS acquisition.event_tape_scale (
    event_key                  VARCHAR PRIMARY KEY,
    artist_key                 VARCHAR,
    market_key                 VARCHAR,
    venue_key                  VARCHAR,
    event_date                 DATE,
    marketplace_count          INTEGER,              -- distinct marketplaces observed
    observation_depth          INTEGER,              -- distinct observation days
    pit_event_marketplace_days INTEGER,              -- distinct (marketplace, day) pairs
    multi_marketplace_events   BOOLEAN,              -- marketplace_count >= 2
    pairs_3_plus               BOOLEAN,              -- marketplace_count >= 3
    pairs_5_plus               BOOLEAN,
    pairs_10_plus              BOOLEAN,
    first_observed_at          TIMESTAMP,
    last_observed_at           TIMESTAMP,
    source_system              VARCHAR NOT NULL,
    source_version             VARCHAR NOT NULL,
    rights_status              VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status      VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    evidence_json              JSON,
    ingested_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_event_tape_scale_market ON acquisition.event_tape_scale (market_key, event_date);
CREATE INDEX IF NOT EXISTS idx_event_tape_scale_artist ON acquisition.event_tape_scale (artist_key, event_date);
