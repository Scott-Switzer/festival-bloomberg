-- ===========================================================================
-- 010_event_performance_v1.sql
-- ===========================================================================
-- Normalized artist × market × venue × event layer.
--
-- RAW PROVIDER OBSERVATION
--         ↓
-- CANONICAL EVIDENCE OBJECT
--         ↓
-- NORMALIZED EVENT / PERFORMANCE RELATION
--         ↓
-- DESCRIPTIVE FEATURE
--
-- event_time is a source fact (when the show happened).
-- knowledge_time is when Festival Bloomberg learned the observation.
-- Derived features at cutoff T may only consume knowledge_time <= T.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS events;

CREATE TABLE IF NOT EXISTS events.artist_identities (
    canonical_artist_id     VARCHAR PRIMARY KEY,
    display_name            VARCHAR NOT NULL,
    musicbrainz_mbid        VARCHAR,
    ticketmaster_attraction_id VARCHAR,
    youtube_channel_id      VARCHAR,
    setlistfm_mbid          VARCHAR,
    resolution_method       VARCHAR NOT NULL,
    ambiguities_json        JSON,
    resolved_at             TIMESTAMP,
    supporting_observation_ids JSON
);

CREATE TABLE IF NOT EXISTS events.venues (
    venue_id                VARCHAR PRIMARY KEY,
    venue_name              VARCHAR,
    city                    VARCHAR,
    state                   VARCHAR,
    state_code              VARCHAR,
    country                 VARCHAR,
    country_code            VARCHAR,
    market_id               VARCHAR,
    latitude                DOUBLE,
    longitude               DOUBLE,
    ticketmaster_venue_id   VARCHAR,
    setlistfm_venue_id      VARCHAR,
    first_observed_at       TIMESTAMP,
    last_observed_at        TIMESTAMP,
    supporting_observation_ids JSON
);

CREATE TABLE IF NOT EXISTS events.events (
    event_id                VARCHAR PRIMARY KEY,
    event_type              VARCHAR NOT NULL,
    event_name              VARCHAR,
    event_time              TIMESTAMP,
    local_date              DATE,
    venue_id                VARCHAR,
    venue_name              VARCHAR,
    market_id               VARCHAR,
    city                    VARCHAR,
    state                   VARCHAR,
    country                 VARCHAR,
    festival_name           VARCHAR,
    tour_name               VARCHAR,
    event_status            VARCHAR,
    provider_support_count  INTEGER,
    first_observed_at       TIMESTAMP,
    last_observed_at        TIMESTAMP,
    knowledge_time          TIMESTAMP NOT NULL,
    match_gate              VARCHAR,
    supporting_observation_ids JSON
);

CREATE TABLE IF NOT EXISTS events.artist_event_relations (
    relation_id             VARCHAR PRIMARY KEY,
    artist_id               VARCHAR NOT NULL,
    event_id                VARCHAR NOT NULL,
    role                    VARCHAR,
    knowledge_time          TIMESTAMP NOT NULL,
    supporting_observation_ids JSON,
    UNIQUE (artist_id, event_id)
);

CREATE TABLE IF NOT EXISTS events.artist_market_relations (
    relation_id             VARCHAR PRIMARY KEY,
    artist_id               VARCHAR NOT NULL,
    market_id               VARCHAR NOT NULL,
    relation_type           VARCHAR NOT NULL,
    first_event_date        DATE,
    last_event_date         DATE,
    event_count             INTEGER,
    knowledge_time          TIMESTAMP NOT NULL,
    supporting_observation_ids JSON,
    UNIQUE (artist_id, market_id, relation_type)
);

CREATE TABLE IF NOT EXISTS events.provider_event_observations (
    observation_id          VARCHAR PRIMARY KEY,
    event_id                VARCHAR,
    provider                VARCHAR NOT NULL,
    platform                VARCHAR NOT NULL,
    platform_object_id      VARCHAR,
    provider_event_name     VARCHAR,
    provider_venue_name     VARCHAR,
    provider_date           VARCHAR,
    provider_tour_name      VARCHAR,
    provider_festival_name  VARCHAR,
    raw_observation_id      VARCHAR,
    knowledge_time          TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS events.event_disagreements (
    disagreement_id         VARCHAR PRIMARY KEY,
    event_id                VARCHAR NOT NULL,
    dimension               VARCHAR NOT NULL,
    left_provider           VARCHAR,
    right_provider          VARCHAR,
    left_value              VARCHAR,
    right_value             VARCHAR
);

CREATE TABLE IF NOT EXISTS events.event_fan_links (
    link_id                 VARCHAR PRIMARY KEY,
    youtube_video_id        VARCHAR NOT NULL,
    canonical_event_id      VARCHAR NOT NULL,
    link_method             VARCHAR NOT NULL,
    supporting_evidence     VARCHAR,
    confidence_state        VARCHAR,
    knowledge_time          TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_market_date
  ON events.events (market_id, local_date);
CREATE INDEX IF NOT EXISTS idx_events_knowledge
  ON events.events (knowledge_time);
CREATE INDEX IF NOT EXISTS idx_artist_event_artist
  ON events.artist_event_relations (artist_id);
CREATE INDEX IF NOT EXISTS idx_provider_event_event
  ON events.provider_event_observations (event_id, provider);
