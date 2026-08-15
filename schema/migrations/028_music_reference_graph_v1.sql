-- ===========================================================================
-- 028_music_reference_graph_v1.sql
-- ===========================================================================
-- MUSIC_REFERENCE_GRAPH_AND_PRO_WORKFLOW_V1 — connect the series spine to
-- events, performers and places.
--
-- Migration 027 gave us 6,228 canonical event series; this migration adds the
-- raw MusicBrainz event/place observations and two MATERIALIZED link tables
-- that turn those series shells into a queryable graph:
--
--   core.series_events     series -> events (Festival/Tour membership)
--   core.event_performers  event -> artists (main performer / support act /
--                          guest performer / ... — role semantics preserved)
--
-- Typed provenance edges still flow through core.entity_relationships.
-- MusicBrainz is CROWD_CURATED_REFERENCE, never an OFFICIAL_PRIMARY_SOURCE;
-- its observations do not silently upgrade existing research seeds.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Raw MusicBrainz event observations.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.musicbrainz_event (
    mbid                VARCHAR PRIMARY KEY,
    name                VARCHAR,
    event_type          VARCHAR,
    begin_date          VARCHAR,
    end_date            VARCHAR,
    event_time          VARCHAR,
    cancelled           BOOLEAN,
    disambiguation      VARCHAR,
    payload             JSON,
    dump_source_id      VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mb_event_type ON raw.musicbrainz_event (event_type);

-- ---------------------------------------------------------------------------
-- 2. Raw MusicBrainz place observations.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.musicbrainz_place (
    mbid                VARCHAR PRIMARY KEY,
    name                VARCHAR,
    place_type          VARCHAR,
    address             VARCHAR,
    latitude            DOUBLE,
    longitude           DOUBLE,
    area                VARCHAR,
    disambiguation      VARCHAR,
    payload             JSON,
    dump_source_id      VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mb_place_type ON raw.musicbrainz_place (place_type);

-- ---------------------------------------------------------------------------
-- 3. Series -> event materialization (Festival/Tour membership).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.series_events (
    series_event_key    VARCHAR PRIMARY KEY,   -- hash(series_mbid, event_mbid)
    series_key          VARCHAR NOT NULL,
    series_mbid         VARCHAR NOT NULL,
    event_mbid          VARCHAR NOT NULL,
    event_name          VARCHAR,
    event_type          VARCHAR,
    event_begin_date    VARCHAR,
    event_end_date      VARCHAR,
    relationship_type   VARCHAR NOT NULL,      -- "part of" (from MusicBrainz)
    source_system       VARCHAR NOT NULL,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_series_events_series ON core.series_events (series_key, event_begin_date);
CREATE INDEX IF NOT EXISTS idx_series_events_event ON core.series_events (event_mbid);

-- ---------------------------------------------------------------------------
-- 4. Event -> performer materialization (role semantics preserved).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.event_performers (
    performer_key       VARCHAR PRIMARY KEY,   -- hash(event_mbid, artist_mbid, role)
    event_mbid          VARCHAR NOT NULL,
    artist_mbid         VARCHAR NOT NULL,
    artist_name         VARCHAR,
    performer_role      VARCHAR NOT NULL,      -- "main performer" | "support act" | "guest performer" | ...
    direction           VARCHAR,               -- forward | backward (MB relation direction)
    source_system       VARCHAR NOT NULL,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_event_performers_event ON core.event_performers (event_mbid);
CREATE INDEX IF NOT EXISTS idx_event_performers_artist ON core.event_performers (artist_mbid);
