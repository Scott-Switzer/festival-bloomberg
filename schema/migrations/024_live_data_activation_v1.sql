-- ===========================================================================
-- 024_live_data_activation_v1.sql
-- ===========================================================================
-- LIVE_DATA_ACTIVATION_AND_INTELLIGENCE_SCALE_V1 — turn validated providers
-- into append-only source-backed data:
--
--   1. identity.spotify_artist_resolutions — bounded Spotify identity
--      resolution. One row per (local artist name, Spotify candidate). A
--      resolution is EXACT / HIGH_CONFIDENCE / AMBIGUOUS / NO_MATCH; the
--      Spotify id is NEVER force-merged into a canonical artist on string
--      similarity alone. Append-only (a fresh retrieval never overwrites
--      history).
--
--   2. events.provider_event_snapshots — append-only provider event
--      snapshots (Ticketmaster and future providers). Captures status,
--      public onsale, presales, price ranges, promoter, classification and
--      venue coordinates so the terminal can show what CHANGED between
--      polls. offsale != sold_out; a price range is an observation.
--
--   3. events.weather_forecast_snapshots — NWS forecast snapshots keyed to
--      events with coordinates. Forecast generation time is kept separate
--      from the validity window; realized weather never backfills an
--      earlier forecast state.
--
-- Non-negotiable semantics: every row carries retrieved_at / knowledge_time,
-- rights_status and commercial_use_status; UNKNOWN is NULL, never 0.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS identity;

-- ---------------------------------------------------------------------------
-- 1. Spotify identity resolutions (append-only).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identity.spotify_artist_resolutions (
    resolution_key          VARCHAR PRIMARY KEY,   -- hash(source_table, name, spotify_id, retrieved_at)
    local_artist_name       VARCHAR NOT NULL,
    normalized_local_name   VARCHAR NOT NULL,
    source_table            VARCHAR NOT NULL,      -- which corpus the name came from
    spotify_id              VARCHAR,               -- NULL for NO_MATCH
    spotify_name            VARCHAR,
    spotify_uri             VARCHAR,
    spotify_url             VARCHAR,
    resolution_status       VARCHAR NOT NULL,      -- EXACT|HIGH_CONFIDENCE|AMBIGUOUS|NO_MATCH
    match_method            VARCHAR NOT NULL,      -- deterministic_normalized_name etc.
    match_similarity        DOUBLE,
    match_features          JSON,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    software_version        VARCHAR,
    ingested_at             TIMESTAMP NOT NULL,

    CHECK (match_similarity IS NULL
           OR (match_similarity >= 0.0 AND match_similarity <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_spotify_res_name
  ON identity.spotify_artist_resolutions (normalized_local_name);
CREATE INDEX IF NOT EXISTS idx_spotify_res_spotify
  ON identity.spotify_artist_resolutions (spotify_id);
CREATE INDEX IF NOT EXISTS idx_spotify_res_status
  ON identity.spotify_artist_resolutions (resolution_status);

-- ---------------------------------------------------------------------------
-- 2. Provider event snapshots (append-only; Ticketmaster first).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events.provider_event_snapshots (
    snapshot_key            VARCHAR PRIMARY KEY,   -- hash(provider, platform_object_id, retrieved_at)
    provider                VARCHAR NOT NULL,
    platform_object_id      VARCHAR NOT NULL,
    event_name              VARCHAR,
    artist_name             VARCHAR,               -- first attraction (may be multi-act)
    attractions             JSON,
    venue_id                VARCHAR,
    venue_name              VARCHAR,
    city                    VARCHAR,
    state_code              VARCHAR,
    country_code            VARCHAR,
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),
    local_date              VARCHAR,
    local_time              VARCHAR,
    event_time              VARCHAR,
    timezone                VARCHAR,
    event_status            VARCHAR,               -- onsale|offsale|cancelled|postponed|rescheduled|...
    onsale_start            VARCHAR,
    onsale_end              VARCHAR,
    presales                JSON,                  -- one entry per independent presale
    price_min               DECIMAL(12,2),
    price_max               DECIMAL(12,2),
    price_currency          VARCHAR,
    price_type              VARCHAR,
    promoter                VARCHAR,
    segment                 VARCHAR,
    genre                   VARCHAR,
    subgenre                VARCHAR,
    event_type              VARCHAR,
    canonical_url           VARCHAR,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    content_hash            VARCHAR,
    raw_payload_hash        VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    software_version        VARCHAR,
    ingested_at             TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_snap_event
  ON events.provider_event_snapshots (platform_object_id, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_provider_snap_market
  ON events.provider_event_snapshots (country_code, state_code, city, local_date);
CREATE INDEX IF NOT EXISTS idx_provider_snap_status
  ON events.provider_event_snapshots (event_status);

-- ---------------------------------------------------------------------------
-- 3. NWS forecast snapshots keyed to events with coordinates.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events.weather_forecast_snapshots (
    forecast_key            VARCHAR PRIMARY KEY,   -- hash(event_ref, generation_time)
    event_ref               VARCHAR NOT NULL,      -- provider event id or canonical event id
    venue_latitude          DECIMAL(9,6),
    venue_longitude         DECIMAL(9,6),
    generation_time         TIMESTAMP NOT NULL,    -- when the forecast was ISSUED
    valid_start             TIMESTAMP,
    valid_end               TIMESTAMP,
    temperature             INTEGER,
    temperature_unit        VARCHAR,
    precipitation_probability INTEGER,
    wind_speed              VARCHAR,
    short_forecast          VARCHAR,
    source_url              VARCHAR,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    software_version        VARCHAR,
    ingested_at             TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_weather_forecast_event
  ON events.weather_forecast_snapshots (event_ref, generation_time);
