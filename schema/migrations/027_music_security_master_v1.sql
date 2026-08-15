-- ===========================================================================
-- 027_music_security_master_v1.sql
-- ===========================================================================
-- MUSIC_SECURITY_MASTER_AND_MONITORING_V1 — identity architecture.
--
-- The system already has core.artists / core.venues / core.festivals /
-- core.festival_editions / core.promoters. This migration does NOT create a
-- parallel identity system; it extends the SAME canonical graph with the
-- object families the music security master is missing:
--
--   CATALOG    core.release_groups / core.releases / core.recordings /
--              core.works            (recording != work != release !=
--                                     release-group; ISRC != ISWC)
--   LIVE       core.event_series     (TOUR | FESTIVAL | RESIDENCY | RUN |
--                                     EVENT_SERIES; a series != an event)
--   INDUSTRY   core.labels / core.companies
--   GRAPH      core.entity_relationships (typed, source-backed edges)
--   ID MASTER  core.entity_external_ids gains namespace / resolution /
--              first_seen / last_seen / knowledge_time columns.
--   RAW        raw.musicbrainz_dump_source + raw.musicbrainz_series for
--              bulk CC0 dump ingestion with source/checksum lineage.
--
-- Non-negotiable semantics: external IDs are MAPPINGS, never primary keys;
-- AMBIGUOUS is never forced to MATCHED; every relationship row carries its
-- source + knowledge_time.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. External ID master: generalize core.entity_external_ids (migration 004).
-- ---------------------------------------------------------------------------
ALTER TABLE core.entity_external_ids ADD COLUMN namespace VARCHAR;
ALTER TABLE core.entity_external_ids ADD COLUMN resolution_status VARCHAR;
ALTER TABLE core.entity_external_ids ADD COLUMN resolution_method VARCHAR;
ALTER TABLE core.entity_external_ids ADD COLUMN first_seen_at TIMESTAMP;
ALTER TABLE core.entity_external_ids ADD COLUMN last_seen_at TIMESTAMP;
ALTER TABLE core.entity_external_ids ADD COLUMN knowledge_time TIMESTAMP;

-- ---------------------------------------------------------------------------
-- 2. Catalog objects (recorded music).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.release_groups (
    release_group_key   VARCHAR PRIMARY KEY,
    musicbrainz_id      VARCHAR UNIQUE,
    name                VARCHAR NOT NULL,
    normalized_name     VARCHAR,
    primary_type        VARCHAR,               -- Album|Single|EP|Broadcast|Other
    secondary_types     JSON,
    first_release_date  DATE,
    artist_keys         JSON,
    disambiguation      VARCHAR,
    source_system       VARCHAR,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_release_groups_mbid ON core.release_groups (musicbrainz_id);
CREATE INDEX IF NOT EXISTS idx_release_groups_norm ON core.release_groups (normalized_name);

CREATE TABLE IF NOT EXISTS core.releases (
    release_key         VARCHAR PRIMARY KEY,
    musicbrainz_id      VARCHAR UNIQUE,
    release_group_key   VARCHAR,
    name                VARCHAR NOT NULL,
    release_date        DATE,
    country             VARCHAR,
    release_status      VARCHAR,               -- Official|Promotion|Bootleg|Pseudo-Release
    label_key           VARCHAR,
    catalog_number      VARCHAR,
    barcode             VARCHAR,
    source_system       VARCHAR,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_releases_mbid ON core.releases (musicbrainz_id);
CREATE INDEX IF NOT EXISTS idx_releases_group ON core.releases (release_group_key);

CREATE TABLE IF NOT EXISTS core.recordings (
    recording_key       VARCHAR PRIMARY KEY,
    musicbrainz_id      VARCHAR UNIQUE,
    name                VARCHAR NOT NULL,
    artist_keys         JSON,
    isrc                VARCHAR,               -- ISRC belongs to RECORDINGS only
    duration_ms         BIGINT,
    first_release_date  DATE,
    disambiguation      VARCHAR,
    source_system       VARCHAR,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_recordings_mbid ON core.recordings (musicbrainz_id);
CREATE INDEX IF NOT EXISTS idx_recordings_isrc ON core.recordings (isrc);

CREATE TABLE IF NOT EXISTS core.works (
    work_key            VARCHAR PRIMARY KEY,
    musicbrainz_id      VARCHAR UNIQUE,
    name                VARCHAR NOT NULL,
    iswc                VARCHAR,               -- ISWC belongs to WORKS only
    writers             JSON,
    composers           JSON,
    source_system       VARCHAR,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_works_mbid ON core.works (musicbrainz_id);
CREATE INDEX IF NOT EXISTS idx_works_iswc ON core.works (iswc);

-- ---------------------------------------------------------------------------
-- 3. Live object: event series (Tour / Festival / Residency / Run).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.event_series (
    series_key          VARCHAR PRIMARY KEY,
    musicbrainz_id      VARCHAR UNIQUE,
    name                VARCHAR NOT NULL,
    normalized_name     VARCHAR,
    series_type         VARCHAR NOT NULL,      -- TOUR|FESTIVAL|RESIDENCY|RUN|EVENT_SERIES
    artist_key          VARCHAR,
    disambiguation      VARCHAR,
    begin_date          VARCHAR,
    end_date            VARCHAR,
    source_system       VARCHAR,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_event_series_mbid ON core.event_series (musicbrainz_id);
CREATE INDEX IF NOT EXISTS idx_event_series_type ON core.event_series (series_type);

-- ---------------------------------------------------------------------------
-- 4. Industry objects: labels and companies (companies distinct from labels
--    and from promoters).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.labels (
    label_key           VARCHAR PRIMARY KEY,
    musicbrainz_id      VARCHAR UNIQUE,
    name                VARCHAR NOT NULL,
    normalized_name     VARCHAR,
    label_type          VARCHAR,
    country             VARCHAR,
    life_span_begin     VARCHAR,
    life_span_end       VARCHAR,
    source_system       VARCHAR,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_labels_mbid ON core.labels (musicbrainz_id);
CREATE INDEX IF NOT EXISTS idx_labels_norm ON core.labels (normalized_name);

CREATE TABLE IF NOT EXISTS core.companies (
    company_key         VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    normalized_name     VARCHAR,
    legal_name          VARCHAR,
    cik                 VARCHAR,               -- SEC EDGAR CIK
    ticker              VARCHAR,
    company_type        VARCHAR,               -- promoter|label|venue_operator|platform|other
    parent_company_key  VARCHAR,               -- explicit ownership evidence only
    source_system       VARCHAR,
    source_url          VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_companies_norm ON core.companies (normalized_name);
CREATE INDEX IF NOT EXISTS idx_companies_cik ON core.companies (cik);
CREATE INDEX IF NOT EXISTS idx_companies_ticker ON core.companies (ticker);

-- ---------------------------------------------------------------------------
-- 5. Typed relationship graph (subject --predicate--> object).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.entity_relationships (
    relationship_key    VARCHAR PRIMARY KEY,   -- hash(subject,predicate,object,source)
    subject_entity_type VARCHAR NOT NULL,
    subject_key         VARCHAR NOT NULL,
    predicate           VARCHAR NOT NULL,      -- ARTIST_PERFORMED_AT_EVENT | EVENT_PART_OF_SERIES | ARTIST_MEMBER_OF_GROUP | RECORDING_PERFORMS_WORK | ...
    object_entity_type  VARCHAR NOT NULL,
    object_key          VARCHAR NOT NULL,
    source_system       VARCHAR NOT NULL,
    source_url          VARCHAR,
    evidence_class      VARCHAR,
    event_time          TIMESTAMP,
    knowledge_time      TIMESTAMP NOT NULL,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_relationships_subject
  ON core.entity_relationships (subject_entity_type, subject_key);
CREATE INDEX IF NOT EXISTS idx_relationships_predicate
  ON core.entity_relationships (predicate);
CREATE INDEX IF NOT EXISTS idx_relationships_object
  ON core.entity_relationships (object_entity_type, object_key);

-- ---------------------------------------------------------------------------
-- 6. MusicBrainz bulk-dump source lineage (CC0 core data).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.musicbrainz_dump_source (
    dump_source_id          VARCHAR PRIMARY KEY,  -- hash(snapshot, entity_type, url)
    entity_type             VARCHAR NOT NULL,     -- artist|event|series|place|label|release_group
    snapshot_date           VARCHAR NOT NULL,     -- e.g. 20260715-001001
    download_url            VARCHAR NOT NULL,
    compressed_size_bytes   BIGINT,
    local_path              VARCHAR,
    checksum_sha256         VARCHAR,
    license                 VARCHAR NOT NULL DEFAULT 'CC0',
    downloaded_at           TIMESTAMP,
    parsed_rows             BIGINT,
    ingested_at             TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mb_dump_source_type
  ON raw.musicbrainz_dump_source (entity_type, snapshot_date);

-- ---------------------------------------------------------------------------
-- 7. MusicBrainz series raw observations (the festival/tour spine).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.musicbrainz_series (
    mbid                VARCHAR PRIMARY KEY,
    name                VARCHAR,
    series_type         VARCHAR,
    disambiguation      VARCHAR,
    artist_mbids        JSON,
    begin_date          VARCHAR,
    end_date            VARCHAR,
    payload             JSON,
    dump_source_id      VARCHAR,
    knowledge_time      TIMESTAMP,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mb_series_type ON raw.musicbrainz_series (series_type);
