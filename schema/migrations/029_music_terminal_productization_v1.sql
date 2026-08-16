-- ===========================================================================
-- 029_music_terminal_productization_v1.sql
-- ===========================================================================
-- MUSIC_TERMINAL_PRODUCTIZATION_V1 — turn the reference graph into a
-- daily-usable information terminal.
--
-- This migration adds the OBJECTS the terminal workflow needs without
-- touching the existing canonical graph:
--
--   REFERENCE   reference.musicbrainz_artists (compact local search estate
--               for the FULL artist dump; only relevant entities are promoted
--               into core.artists) + reference.musicbrainz_areas (area/ISO
--               reference for venue/artist geography).
--   WATCHLISTS  core.watchlists / core.watchlist_items (single-user local
--               product: named lists over ARTIST/FESTIVAL/TOUR/EVENT/VENUE/
--               PROMOTER/MARKET/COMPANY entities).
--   MONITORS    terminal.saved_monitors (named saved views: entity type,
--               watchlist, filters, columns, sorting, time horizon).
--   ALERTS      core.alerts (deterministic, idempotent, traceable; one
--               logical change -> one logical alert via dedupe_key).
--   IDENTITY    identity.ticketmaster_artist_resolutions (attraction ->
--               canonical artist resolution ledger with special-attraction
--               classification; NO_MATCH/AMBIGUOUS are never forced).
--   CROSS-LINK  core.event_cross_links (MusicBrainz <-> Ticketmaster event
--               bridges; MATCHED only on multiple agreeing signals).
--   DEPRECATION core.deprecated_columns (quarantine registry for stale
--               popularity semantics; columns stay for history but read
--               models must not present them as live facts).
--
-- Semantics preserved: external IDs are MAPPINGS never primary keys; missing
-- stays NULL; conflicting claims coexist; every row carries provenance.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Reference layer: full-universe local search estate (kept OUT of
--    core.artists so the user-facing canonical table stays relevant + fast).
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS reference;

CREATE TABLE IF NOT EXISTS reference.musicbrainz_artists (
    mbid                VARCHAR PRIMARY KEY,
    name                VARCHAR,
    normalized_name     VARCHAR,
    sort_name           VARCHAR,
    artist_type         VARCHAR,               -- Person|Group|Orchestra|Choir|Character|...
    area_mbid           VARCHAR,
    area_name           VARCHAR,
    begin_date          VARCHAR,
    end_date            VARCHAR,
    disambiguation      VARCHAR,
    isni                JSON,                  -- explicit ISNI identifiers only
    ipi                 JSON,                  -- explicit IPI identifiers only
    aliases             JSON,                  -- [{name, locale, type, begin, end, primary}]
    urls                JSON,                  -- [{type, resource}] typed relationships
    dump_source_id      VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ref_mb_artists_norm ON reference.musicbrainz_artists (normalized_name);
CREATE INDEX IF NOT EXISTS idx_ref_mb_artists_type ON reference.musicbrainz_artists (artist_type);
CREATE INDEX IF NOT EXISTS idx_ref_mb_artists_area ON reference.musicbrainz_artists (area_mbid);

CREATE TABLE IF NOT EXISTS reference.musicbrainz_areas (
    mbid                VARCHAR PRIMARY KEY,
    name                VARCHAR,
    normalized_name     VARCHAR,
    area_type           VARCHAR,               -- Country|City|Subdivision|...
    iso_3166_1          JSON,                  -- ISO country codes
    iso_3166_2          JSON,                  -- ISO subdivision codes
    iso_3166_3          JSON,                  -- ISO former country codes
    parent_mbid         VARCHAR,               -- containment edge (explicit only)
    begin_date          VARCHAR,
    end_date            VARCHAR,
    disambiguation      VARCHAR,
    dump_source_id      VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ref_mb_areas_norm ON reference.musicbrainz_areas (normalized_name);
CREATE INDEX IF NOT EXISTS idx_ref_mb_areas_parent ON reference.musicbrainz_areas (parent_mbid);

-- Raw area observations (payload preserved; dump lineage in dump_source).
CREATE TABLE IF NOT EXISTS raw.musicbrainz_area (
    mbid                VARCHAR PRIMARY KEY,
    name                VARCHAR,
    area_type           VARCHAR,
    payload             JSON,
    dump_source_id      VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- 2. Watchlists (single-user local product; no auth complexity).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.watchlists (
    watchlist_key       VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    description         VARCHAR,
    entity_type         VARCHAR,               -- NULL = mixed entity watchlist
    is_system           BOOLEAN NOT NULL DEFAULT FALSE,  -- shipped example list
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.watchlist_items (
    item_key            VARCHAR PRIMARY KEY,   -- hash(watchlist_key, entity_type, entity_key)
    watchlist_key       VARCHAR NOT NULL,
    entity_type         VARCHAR NOT NULL,      -- ARTIST|FESTIVAL|TOUR|EVENT|VENUE|PROMOTER|MARKET|COMPANY
    entity_key          VARCHAR NOT NULL,
    entity_name         VARCHAR,
    notes               VARCHAR,
    tags                JSON,
    added_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    removed_at          TIMESTAMP,             -- soft delete (item stays traceable)
    source_system       VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_watchlist_items_list ON core.watchlist_items (watchlist_key, removed_at);
CREATE INDEX IF NOT EXISTS idx_watchlist_items_entity ON core.watchlist_items (entity_type, entity_key);

-- ---------------------------------------------------------------------------
-- 3. Saved monitor views (persisted column/filter/sort configuration).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS terminal.saved_monitors (
    monitor_key         VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    entity_type         VARCHAR NOT NULL,      -- ARTIST|FESTIVAL|TOUR|EVENT|MARKET|...
    watchlist_key       VARCHAR,
    filters             JSON,                  -- [{field, op, value}]
    visible_columns     JSON,                  -- ordered list of column names
    sort                JSON,                  -- [{field, direction}]
    time_horizon        VARCHAR,               -- e.g. 7D|30D|90D|ALL
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- 4. Deterministic alert engine (idempotent via dedupe_key; one logical
--    change -> one logical alert; re-running never duplicates).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.alerts (
    alert_key           VARCHAR PRIMARY KEY,   -- hash(alert_type, entity, provider, dedupe_key)
    alert_type          VARCHAR NOT NULL,      -- NEW_EVENT|NEW_TOUR|NEW_FESTIVAL_APPEARANCE|PRESALE_DISCOVERED|ONSALE_DISCOVERED|EVENT_CANCELLED|EVENT_POSTPONED|EVENT_RESCHEDULED|PRICE_RANGE_CHANGED|FESTIVAL_LINEUP_ADDITION|FESTIVAL_LINEUP_REMOVAL|NEWS_MENTION|DATA_PROVIDER_STALE|ATTENTION_THRESHOLD
    entity_type         VARCHAR NOT NULL,
    entity_key          VARCHAR NOT NULL,
    entity_name         VARCHAR,
    provider            VARCHAR,
    observed_at         TIMESTAMP,
    detail              JSON,
    dedupe_key          VARCHAR NOT NULL,
    source_record_id    VARCHAR,
    status              VARCHAR NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE|DISMISSED
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alerts_entity ON core.alerts (entity_type, entity_key, observed_at);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON core.alerts (alert_type, observed_at);

-- ---------------------------------------------------------------------------
-- 5. Ticketmaster attraction -> canonical artist resolution ledger.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS identity.ticketmaster_artist_resolutions (
    resolution_key          VARCHAR PRIMARY KEY,  -- hash(attraction_id|attraction_name, snapshot)
    attraction_id           VARCHAR,
    attraction_name         VARCHAR NOT NULL,
    normalized_name         VARCHAR NOT NULL,
    artist_key              VARCHAR,              -- canonical artist (may stay NULL)
    artist_mbid             VARCHAR,
    matched_name            VARCHAR,
    resolution_status       VARCHAR NOT NULL,     -- MATCHED_ARTIST|MATCHED_EVENT_OR_PACKAGE|AMBIGUOUS|NO_MATCH|REJECTED_NON_ARTIST
    match_method            VARCHAR,              -- EXACT_EXTERNAL_ID|EXISTING_MAPPING|MB_EXACT_NAME|MB_EXACT_ALIAS|NORMALIZED_EXACT|MULTI_SIGNAL|FUZZY_CANDIDATE
    match_similarity        DOUBLE,
    match_features          JSON,
    special_classification  VARCHAR,              -- FESTIVAL_NAME|TOUR_PACKAGE|TRIBUTE_ACT|COVER_BAND|DJ_EVENT|DANCE_PARTY|COMEDIAN|SPORTS_NON_MUSIC|COLLABORATION_BILLING|PRESENTATION|SPECIAL_EVENT
    source_table            VARCHAR,
    knowledge_time          TIMESTAMP,
    software_version        VARCHAR,
    ingested_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tm_resolutions_name ON identity.ticketmaster_artist_resolutions (normalized_name);
CREATE INDEX IF NOT EXISTS idx_tm_resolutions_status ON identity.ticketmaster_artist_resolutions (resolution_status);

-- ---------------------------------------------------------------------------
-- 6. MusicBrainz <-> Ticketmaster event cross-links (MATCHED only when
--    multiple strong signals agree; never name alone).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.event_cross_links (
    cross_link_key          VARCHAR PRIMARY KEY,  -- hash(mb_event_mbid, tm_event_id)
    musicbrainz_event_mbid  VARCHAR NOT NULL,
    ticketmaster_event_id   VARCHAR NOT NULL,
    match_method            VARCHAR NOT NULL,     -- MULTI_SIGNAL|DATE_ARTIST_VENUE|DATE_ARTIST_MARKET|...
    match_score             DOUBLE,
    match_signals           JSON,
    resolution_status       VARCHAR NOT NULL,     -- MATCHED|AMBIGUOUS|UNMATCHED
    source_system           VARCHAR NOT NULL,
    knowledge_time          TIMESTAMP,
    ingested_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cross_links_mb ON core.event_cross_links (musicbrainz_event_mbid);
CREATE INDEX IF NOT EXISTS idx_cross_links_tm ON core.event_cross_links (ticketmaster_event_id);

-- ---------------------------------------------------------------------------
-- 7. Deprecated-column quarantine registry.
--    Columns stay (migration history safety) but read models must not present
--    them as live facts; new writes to them are forbidden by the OA driver.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.identity_conflicts (
    conflict_key         VARCHAR PRIMARY KEY,   -- hash(entity_key, provider_a, provider_b, value_a, value_b)
    entity_type          VARCHAR NOT NULL,
    entity_key           VARCHAR NOT NULL,
    provider_a           VARCHAR NOT NULL,
    provider_b           VARCHAR NOT NULL,
    value_a              VARCHAR,
    value_b              VARCHAR,
    issue                VARCHAR,               -- e.g. 'MB spotify URL disagrees with acquired spotify ID'
    resolution_status    VARCHAR NOT NULL DEFAULT 'UNRESOLVED',  -- UNRESOLVED|RESOLVED|ACCEPTED_DISAGREEMENT
    observed_at          TIMESTAMP NOT NULL DEFAULT now(),
    ingested_at          TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_identity_conflicts_entity
  ON core.identity_conflicts (entity_type, entity_key);

CREATE TABLE IF NOT EXISTS core.deprecated_columns (
    deprecated_key      VARCHAR PRIMARY KEY,   -- hash(table_name, column_name)
    table_name          VARCHAR NOT NULL,
    column_name         VARCHAR NOT NULL,
    deprecated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason              VARCHAR,
    replacement         VARCHAR,
    status              VARCHAR NOT NULL DEFAULT 'DEPRECATED'  -- DEPRECATED|REINSTATED
);
