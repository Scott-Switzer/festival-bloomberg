-- Festival Intelligence Terminal - DuckDB warehouse schema
--
-- This file is the single source of truth for the DuckDB warehouse DDL. It is
-- loaded and applied by ``warehouse.schema_loader`` (used by
-- ``warehouse.repository.FestivalRepository``), so every statement must be
-- idempotent: re-applying the file against an existing database is a no-op.
--
-- Storage layers (mirrors the Festival Bloomberg spec):
--     raw     - evidence-backed scrape/parse records, pre-resolution
--     core    - canonical dimensions (artists, festivals, venues, lineups)
--     metrics - point-in-time observations (momentum, sentiment, social)
--     model   - model outputs and feature snapshots
--     audit   - ingestion run/error bookkeeping
--
-- Conventions:
--     *_key                surrogate/natural key owned by this warehouse
--     normalized_*         lowercased, punctuation-stripped matching form
--     *_confidence         0.0-1.0 confidence score
--     evidence / *_url     provenance for every extracted claim
--     JSON columns         stored via ``json.dumps`` and read back with
--                          ``FestivalRepository._coerce_json``
--
-- The PostgreSQL variant of the analytical schema lives in
-- ``warehouse/schema.sql``; this file is the DuckDB (zero-infra) equivalent
-- and is the one exercised by the test suite.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS metrics;
CREATE SCHEMA IF NOT EXISTS model;
CREATE SCHEMA IF NOT EXISTS audit;

-- ===========================================================================
-- core.artists - canonical artist dimension (MBID-resolved)
-- ===========================================================================
-- musicbrainz_id is the natural key; artist_key falls back to
-- ``name::<normalized_name>`` when no MBID has been resolved yet.
CREATE TABLE IF NOT EXISTS core.artists (
    artist_key              VARCHAR PRIMARY KEY,
    musicbrainz_id          VARCHAR UNIQUE,
    name                    VARCHAR NOT NULL,
    normalized_name         VARCHAR NOT NULL,
    sort_name               VARCHAR,
    disambiguation          VARCHAR,

    -- Identity / origin
    aliases                 JSON,
    country                 VARCHAR,
    origin_city             VARCHAR,
    origin_region           VARCHAR,
    area                    VARCHAR,

    -- Classification
    type                    VARCHAR,
    primary_genre           VARCHAR,
    genres                  JSON,
    subgenres               JSON,
    tags                    JSON,

    -- Lifecycle
    life_span_begin         VARCHAR,
    life_span_end           VARCHAR,
    formation_date          DATE,
    disband_date            DATE,
    active_status           VARCHAR,
    is_active               BOOLEAN,

    -- Composition and business relationships
    members                 JSON,
    member_count            INTEGER,
    labels                  JSON,
    current_label           VARCHAR,
    management              JSON,
    manager_name            VARCHAR,
    booking_agency          VARCHAR,

    -- Web presence
    official_website        VARCHAR,
    official_domains        JSON,
    social_handles          JSON,

    -- External identifiers (see core.entity_external_ids for the long tail)
    wikidata_id             VARCHAR,
    spotify_id              VARCHAR,
    apple_music_id          VARCHAR,
    youtube_channel_id      VARCHAR,
    soundcloud_id           VARCHAR,
    bandcamp_id             VARCHAR,
    discogs_id              VARCHAR,
    songkick_id             VARCHAR,
    bandsintown_id          VARCHAR,
    setlistfm_id            VARCHAR,
    ticketmaster_id         VARCHAR,
    isni                    VARCHAR,
    ipi                     VARCHAR,
    external_ids            JSON,

    -- Popularity (point-in-time snapshot; history lives in metrics.*)
    popularity_score        DOUBLE,
    popularity_rank         INTEGER,
    popularity_source       VARCHAR,
    popularity_observed_at  TIMESTAMP,
    spotify_popularity      INTEGER,
    spotify_followers       BIGINT,
    monthly_listeners       BIGINT,
    listener_countries      JSON,

    -- Evidence / provenance
    evidence                JSON,
    evidence_url            VARCHAR,
    extraction_confidence   DOUBLE,
    extraction_method       VARCHAR,
    source_system           VARCHAR,
    source_url              VARCHAR,
    source_retrieved_at     TIMESTAMP,

    -- Entity resolution
    blocking_key            VARCHAR,
    match_confidence        DOUBLE,
    match_method            VARCHAR,
    resolution_status       VARCHAR,
    manually_reviewed       BOOLEAN,

    ingested_at             TIMESTAMP,
    updated_at              TIMESTAMP,

    CHECK (extraction_confidence IS NULL
           OR (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0)),
    CHECK (match_confidence IS NULL
           OR (match_confidence >= 0.0 AND match_confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_artists_normalized_name ON core.artists (normalized_name);
CREATE INDEX IF NOT EXISTS idx_artists_country ON core.artists (country);
CREATE INDEX IF NOT EXISTS idx_artists_primary_genre ON core.artists (primary_genre);
CREATE INDEX IF NOT EXISTS idx_artists_blocking_key ON core.artists (blocking_key);
CREATE INDEX IF NOT EXISTS idx_artists_spotify_id ON core.artists (spotify_id);
CREATE INDEX IF NOT EXISTS idx_artists_wikidata_id ON core.artists (wikidata_id);
CREATE INDEX IF NOT EXISTS idx_artists_ticketmaster_id ON core.artists (ticketmaster_id);

-- ---------------------------------------------------------------------------
-- core.artist_aliases - alias index used for fuzzy-match blocking
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.artist_aliases (
    alias_key               VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    alias                   VARCHAR NOT NULL,
    normalized_alias        VARCHAR NOT NULL,
    alias_type              VARCHAR,
    locale                  VARCHAR,
    is_primary              BOOLEAN,
    begin_date              DATE,
    end_date                DATE,
    source_system           VARCHAR,
    evidence_url            VARCHAR,
    confidence              DOUBLE,
    ingested_at             TIMESTAMP,

    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_artist_aliases_artist ON core.artist_aliases (artist_key);
CREATE INDEX IF NOT EXISTS idx_artist_aliases_normalized ON core.artist_aliases (normalized_alias);

-- ---------------------------------------------------------------------------
-- core.artist_members - band membership over time
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.artist_members (
    member_key              VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    member_name             VARCHAR NOT NULL,
    normalized_member_name  VARCHAR,
    member_musicbrainz_id   VARCHAR,
    member_role             VARCHAR,
    instruments             JSON,
    joined_date             DATE,
    left_date               DATE,
    is_current              BOOLEAN,
    source_system           VARCHAR,
    evidence_url            VARCHAR,
    confidence              DOUBLE,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_artist_members_artist ON core.artist_members (artist_key);
CREATE INDEX IF NOT EXISTS idx_artist_members_name ON core.artist_members (normalized_member_name);

-- ---------------------------------------------------------------------------
-- core.artist_labels - label / publisher / management relationships
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.artist_labels (
    artist_label_key        VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    label_name              VARCHAR NOT NULL,
    normalized_label_name   VARCHAR,
    label_musicbrainz_id    VARCHAR,
    relationship_type       VARCHAR,
    territory               VARCHAR,
    contact_name            VARCHAR,
    contact_email           VARCHAR,
    start_date              DATE,
    end_date                DATE,
    is_current              BOOLEAN,
    source_system           VARCHAR,
    evidence_url            VARCHAR,
    confidence              DOUBLE,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_artist_labels_artist ON core.artist_labels (artist_key);
CREATE INDEX IF NOT EXISTS idx_artist_labels_name ON core.artist_labels (normalized_label_name);

-- ---------------------------------------------------------------------------
-- core.artist_social_handles - platform handles (also an ER matching signal)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.artist_social_handles (
    handle_key              VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    platform                VARCHAR NOT NULL,
    handle                  VARCHAR NOT NULL,
    normalized_handle       VARCHAR NOT NULL,
    url                     VARCHAR,
    is_verified             BOOLEAN,
    follower_count          BIGINT,
    engagement_rate         DOUBLE,
    observed_at             TIMESTAMP,
    source_system           VARCHAR,
    evidence_url            VARCHAR,
    confidence              DOUBLE,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_artist_handles_artist ON core.artist_social_handles (artist_key);
CREATE INDEX IF NOT EXISTS idx_artist_handles_lookup ON core.artist_social_handles (platform, normalized_handle);

-- ---------------------------------------------------------------------------
-- core.artist_contacts - booking and management contact information
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.artist_contacts (
    contact_key             VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    agency_name             VARCHAR,
    agent_name              VARCHAR,
    contact_email           VARCHAR,
    contact_phone           VARCHAR,
    role                    VARCHAR,
    verified                BOOLEAN,
    source_url              VARCHAR,
    retrieved_at            TIMESTAMP,
    source_system           VARCHAR,
    evidence_url            VARCHAR,
    confidence              DOUBLE,
    ingested_at             TIMESTAMP,

    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_artist_contacts_artist ON core.artist_contacts (artist_key);
CREATE INDEX IF NOT EXISTS idx_artist_contacts_agency ON core.artist_contacts (agency_name);

-- ===========================================================================
-- core.venues - physical sites hosting festivals and editions
-- ===========================================================================
CREATE TABLE IF NOT EXISTS core.venues (
    venue_key               VARCHAR PRIMARY KEY,
    name                    VARCHAR NOT NULL,
    normalized_name         VARCHAR NOT NULL,
    venue_type              VARCHAR,
    address                 VARCHAR,
    city                    VARCHAR,
    region                  VARCHAR,
    country                 VARCHAR,
    postal_code             VARCHAR,
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),
    time_zone               VARCHAR,
    capacity                INTEGER,
    is_outdoor              BOOLEAN,
    website                 VARCHAR,
    wikidata_id             VARCHAR,
    musicbrainz_id          VARCHAR,
    ticketmaster_id         VARCHAR,
    openstreetmap_id        VARCHAR,
    external_ids            JSON,
    source_system           VARCHAR,
    source_url              VARCHAR,
    source_retrieved_at     TIMESTAMP,
    evidence_url            VARCHAR,
    extraction_confidence   DOUBLE,
    ingested_at             TIMESTAMP,
    updated_at              TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_venues_normalized_name ON core.venues (normalized_name);
CREATE INDEX IF NOT EXISTS idx_venues_location ON core.venues (country, city);

-- ===========================================================================
-- core.festivals - canonical festival dimension
-- ===========================================================================
CREATE TABLE IF NOT EXISTS core.festivals (
    festival_key            VARCHAR PRIMARY KEY,
    name                    VARCHAR NOT NULL,
    normalized_name         VARCHAR NOT NULL,
    aliases                 JSON,

    -- Location
    location_country        VARCHAR,
    location_city           VARCHAR,
    location_region         VARCHAR,
    postal_code             VARCHAR,
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),
    time_zone               VARCHAR,

    -- Venue
    venue_key               VARCHAR,
    venue_name              VARCHAR,
    venue_address           VARCHAR,
    venue_type              VARCHAR,

    -- Scale
    capacity                INTEGER,
    daily_capacity          INTEGER,
    total_capacity          INTEGER,
    capacity_basis          VARCHAR,
    duration_days           INTEGER,
    typical_month           INTEGER,

    -- Classification
    genre_focus             JSON,
    subgenre_focus          JSON,
    festival_type           VARCHAR,

    -- Organisation
    organizer               VARCHAR,
    organizers              JSON,
    promoter                VARCHAR,
    promoters               JSON,
    parent_company          VARCHAR,
    booking_contact         VARCHAR,

    -- Stages and ticketing (denormalized snapshot; detail tables below)
    stage_count             INTEGER,
    stages                  JSON,
    ticket_tiers            JSON,
    currency                VARCHAR,
    ticket_price_min        DECIMAL(12,2),
    ticket_price_max        DECIMAL(12,2),
    on_sale_date            DATE,
    sellout_status          VARCHAR,
    sold_out                BOOLEAN,
    sold_out_at             TIMESTAMP,
    sellout_duration_hours  DOUBLE,

    -- Lineup announcement state
    lineup_status           VARCHAR,
    lineup_announced_at     TIMESTAMP,
    lineup_announcement_url VARCHAR,
    lineup_announcements    JSON,

    -- Web presence
    official_website        VARCHAR,
    official_domains        JSON,
    social_handles          JSON,

    -- History
    first_edition_year      INTEGER,
    latest_edition_year     INTEGER,
    edition_count           INTEGER,
    historical_editions     JSON,
    is_active               BOOLEAN,
    active_status           VARCHAR,

    -- External identifiers
    wikidata_id             VARCHAR,
    musicbrainz_id          VARCHAR,
    ticketmaster_id         VARCHAR,
    songkick_id             VARCHAR,
    edmtrain_id             VARCHAR,
    external_ids            JSON,

    -- Evidence / provenance
    evidence                JSON,
    evidence_url            VARCHAR,
    extraction_confidence   DOUBLE,
    extraction_method       VARCHAR,
    source_system           VARCHAR,
    source_url              VARCHAR,
    source_retrieved_at     TIMESTAMP,
    source_last_modified    TIMESTAMP,

    ingested_at             TIMESTAMP,
    updated_at              TIMESTAMP,

    CHECK (typical_month IS NULL OR (typical_month >= 1 AND typical_month <= 12)),
    CHECK (extraction_confidence IS NULL
           OR (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_festivals_normalized_name ON core.festivals (normalized_name);
CREATE INDEX IF NOT EXISTS idx_festivals_location ON core.festivals (location_country, location_city);
CREATE INDEX IF NOT EXISTS idx_festivals_venue ON core.festivals (venue_key);
CREATE INDEX IF NOT EXISTS idx_festivals_wikidata_id ON core.festivals (wikidata_id);

-- ---------------------------------------------------------------------------
-- core.festival_editions - one row per (festival, year[, weekend])
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.festival_editions (
    edition_key             VARCHAR PRIMARY KEY,
    festival_key            VARCHAR NOT NULL,
    year                    INTEGER NOT NULL,
    edition_name            VARCHAR,
    edition_label           VARCHAR,
    weekend_number          INTEGER,

    -- Dates
    start_date              DATE,
    end_date                DATE,
    duration_days           INTEGER,
    time_zone               VARCHAR,

    -- Location (editions can move between venues/cities)
    venue_key               VARCHAR,
    venue_name              VARCHAR,
    location_city           VARCHAR,
    location_region         VARCHAR,
    location_country        VARCHAR,
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),

    -- Scale and outcome
    capacity                INTEGER,
    daily_capacity          INTEGER,
    attendance              INTEGER,
    headliner_count         INTEGER,
    total_artists           INTEGER,
    stage_count             INTEGER,

    -- Ticketing
    ticket_tiers            JSON,
    currency                VARCHAR,
    ticket_price_min        DECIMAL(12,2),
    ticket_price_max        DECIMAL(12,2),
    on_sale_date            DATE,
    sellout_status          VARCHAR,
    sold_out                BOOLEAN,
    sold_out_at             TIMESTAMP,
    sellout_duration_hours  DOUBLE,
    tickets_sold            INTEGER,
    gross_revenue           DECIMAL(14,2),

    -- Lineup announcement state
    lineup_status           VARCHAR,
    lineup_announced_at     TIMESTAMP,
    lineup_announcement_url VARCHAR,
    lineup_announcements    JSON,
    poster_url              VARCHAR,

    -- Organisation
    organizer               VARCHAR,
    promoter                VARCHAR,

    -- Status
    is_cancelled            BOOLEAN,
    cancellation_reason     VARCHAR,
    weather_summary         VARCHAR,

    -- Evidence / provenance
    evidence                JSON,
    evidence_url            VARCHAR,
    extraction_confidence   DOUBLE,
    source_system           VARCHAR,
    source_url              VARCHAR,
    source_retrieved_at     TIMESTAMP,

    ingested_at             TIMESTAMP,
    updated_at              TIMESTAMP,

    CHECK (extraction_confidence IS NULL
           OR (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_editions_festival_year ON core.festival_editions (festival_key, year);
CREATE INDEX IF NOT EXISTS idx_editions_year ON core.festival_editions (year);
CREATE INDEX IF NOT EXISTS idx_editions_start_date ON core.festival_editions (start_date);

-- ---------------------------------------------------------------------------
-- core.festival_stages - stage roster per festival edition
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.festival_stages (
    stage_key               VARCHAR PRIMARY KEY,
    festival_key            VARCHAR NOT NULL,
    edition_key             VARCHAR,
    year                    INTEGER,
    stage_name              VARCHAR NOT NULL,
    normalized_stage_name   VARCHAR NOT NULL,
    stage_type              VARCHAR,
    stage_rank              INTEGER,
    capacity                INTEGER,
    is_indoor               BOOLEAN,
    sponsor                 VARCHAR,
    host                    VARCHAR,
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),
    source_system           VARCHAR,
    source_url              VARCHAR,
    evidence_url            VARCHAR,
    extraction_confidence   DOUBLE,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stages_festival_year ON core.festival_stages (festival_key, year);
CREATE INDEX IF NOT EXISTS idx_stages_normalized_name ON core.festival_stages (normalized_stage_name);

-- ---------------------------------------------------------------------------
-- core.festival_ticket_tiers - priced inventory per edition
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.festival_ticket_tiers (
    tier_key                VARCHAR PRIMARY KEY,
    festival_key            VARCHAR NOT NULL,
    edition_key             VARCHAR,
    year                    INTEGER,
    tier_name               VARCHAR NOT NULL,
    normalized_tier_name    VARCHAR,
    tier_type               VARCHAR,
    tier_rank               INTEGER,
    price                   DECIMAL(12,2),
    fees                    DECIMAL(12,2),
    price_with_fees         DECIMAL(12,2),
    currency                VARCHAR,
    quantity                INTEGER,
    quantity_sold           INTEGER,
    on_sale_at              TIMESTAMP,
    sold_out_at             TIMESTAMP,
    sold_out                BOOLEAN,
    tier_status             VARCHAR,
    inclusions              JSON,
    source_system           VARCHAR,
    source_url              VARCHAR,
    evidence_url            VARCHAR,
    extraction_confidence   DOUBLE,
    ingested_at             TIMESTAMP,

    CHECK (price IS NULL OR price >= 0)
);

CREATE INDEX IF NOT EXISTS idx_ticket_tiers_festival_year ON core.festival_ticket_tiers (festival_key, year);
CREATE INDEX IF NOT EXISTS idx_ticket_tiers_edition ON core.festival_ticket_tiers (edition_key);

-- ===========================================================================
-- core.lineup_slots - resolved lineup (one row per artist performance slot)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS core.lineup_slots (
    slot_key                VARCHAR PRIMARY KEY,
    festival_key            VARCHAR NOT NULL,
    edition_key             VARCHAR,
    year                    INTEGER,

    -- Artist
    artist_key              VARCHAR,
    artist_name             VARCHAR NOT NULL,
    normalized_artist_name  VARCHAR,
    musicbrainz_id          VARCHAR,

    -- Billing
    billing_order           INTEGER,
    billing_tier            VARCHAR,
    poster_line             INTEGER,
    poster_position         INTEGER,
    is_headliner            BOOLEAN,

    -- Stage and scheduling
    stage_key               VARCHAR,
    stage_name              VARCHAR,
    performance_date        DATE,
    day_of_festival         INTEGER,
    day_label               VARCHAR,
    start_time              TIMESTAMP,
    end_time                TIMESTAMP,
    local_start_time        TIME,
    local_end_time          TIME,
    time_zone               VARCHAR,
    set_duration_minutes    INTEGER,

    -- Performance shape
    artist_role             VARCHAR,
    set_type                VARCHAR,
    is_b2b                  BOOLEAN,
    collaborators           JSON,
    genre                   VARCHAR,
    subgenres               JSON,

    -- Announcement lifecycle
    announcement_date       DATE,
    announced_at            TIMESTAMP,
    announcement_wave       VARCHAR,
    announcement_url        VARCHAR,
    is_cancelled            BOOLEAN,
    replaced_artist_name    VARCHAR,

    -- Evidence / provenance
    evidence                JSON,
    evidence_url            VARCHAR,
    evidence_snippet        VARCHAR,
    extraction_confidence   DOUBLE,
    extraction_method       VARCHAR,
    parser_version          VARCHAR,
    source_system           VARCHAR,
    source_url              VARCHAR,
    source_retrieved_at     TIMESTAMP,

    -- Entity resolution
    match_confidence        DOUBLE,
    match_method            VARCHAR,
    manually_reviewed       BOOLEAN,

    ingested_at             TIMESTAMP,
    updated_at              TIMESTAMP,

    CHECK (extraction_confidence IS NULL
           OR (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0)),
    CHECK (match_confidence IS NULL
           OR (match_confidence >= 0.0 AND match_confidence <= 1.0)),
    CHECK (billing_order IS NULL OR billing_order >= 0)
);

CREATE INDEX IF NOT EXISTS idx_lineup_slots_festival_year ON core.lineup_slots (festival_key, year);
CREATE INDEX IF NOT EXISTS idx_lineup_slots_artist ON core.lineup_slots (artist_key);
CREATE INDEX IF NOT EXISTS idx_lineup_slots_artist_name ON core.lineup_slots (normalized_artist_name);
CREATE INDEX IF NOT EXISTS idx_lineup_slots_date ON core.lineup_slots (performance_date);
CREATE INDEX IF NOT EXISTS idx_lineup_slots_billing ON core.lineup_slots (billing_tier, billing_order);

-- ---------------------------------------------------------------------------
-- core.lineup_qualification_metrics - headliner vs support act analytics
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.lineup_qualification_metrics (
    metric_key              VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    festival_edition_key    VARCHAR,
    billing_tier            INTEGER,
    billing_order           INTEGER,
    stage_name              VARCHAR,
    time_slot_minutes       INTEGER,
    is_headliner            BOOLEAN,
    repeat_booking_count    INTEGER,
    sentiment_score_pre_festival DOUBLE,
    source_system           VARCHAR,
    evidence_url            VARCHAR,
    confidence              DOUBLE,
    ingested_at             TIMESTAMP,

    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_lineup_qualification_artist ON core.lineup_qualification_metrics (artist_key);
CREATE INDEX IF NOT EXISTS idx_lineup_qualification_edition ON core.lineup_qualification_metrics (festival_edition_key);
CREATE INDEX IF NOT EXISTS idx_lineup_qualification_tier ON core.lineup_qualification_metrics (billing_tier);

-- ===========================================================================
-- raw.lineup_observations - pre-resolution lineup evidence
-- ===========================================================================
CREATE TABLE IF NOT EXISTS raw.lineup_observations (
    observation_key         VARCHAR PRIMARY KEY,
    festival_key            VARCHAR,
    festival_name           VARCHAR,
    edition_year            INTEGER,
    artist_name             VARCHAR NOT NULL,
    normalized_artist_name  VARCHAR,
    position                VARCHAR,
    billing_order           INTEGER,
    billing_tier            VARCHAR,
    stage                   VARCHAR,
    day                     VARCHAR,
    performance_date        DATE,
    start_time              TIMESTAMP,
    end_time                TIMESTAMP,
    artist_role             VARCHAR,
    genre                   VARCHAR,
    announcement_date       DATE,
    source_url              VARCHAR,
    source_system           VARCHAR,
    source_retrieved_at     TIMESTAMP,
    parser_version          VARCHAR,
    extraction_method       VARCHAR,
    extraction_confidence   DOUBLE,
    evidence_url            VARCHAR,
    evidence_snippet        VARCHAR,
    observed_raw            JSON,

    -- Resolution outcome (populated once the observation is matched)
    resolved_artist_key     VARCHAR,
    match_confidence        DOUBLE,
    match_method            VARCHAR,
    requires_review         BOOLEAN,

    ingested_at             TIMESTAMP,

    CHECK (extraction_confidence IS NULL
           OR (extraction_confidence >= 0.0 AND extraction_confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_lineup_obs_festival_year ON raw.lineup_observations (festival_key, edition_year);
CREATE INDEX IF NOT EXISTS idx_lineup_obs_artist_name ON raw.lineup_observations (normalized_artist_name);
CREATE INDEX IF NOT EXISTS idx_lineup_obs_resolved ON raw.lineup_observations (resolved_artist_key);

-- ===========================================================================
-- Entity resolution support
-- ===========================================================================
-- Generic external identifier index. Exact matches here are the strongest
-- signal available to the weighted-fuzzy matcher (MBID, Spotify ID, ISNI...).
CREATE TABLE IF NOT EXISTS core.entity_external_ids (
    external_id_key         VARCHAR PRIMARY KEY,
    entity_type             VARCHAR NOT NULL,
    entity_key              VARCHAR NOT NULL,
    id_type                 VARCHAR NOT NULL,
    id_value                VARCHAR NOT NULL,
    url                     VARCHAR,
    is_primary              BOOLEAN,
    confidence              DOUBLE,
    source_system           VARCHAR,
    evidence_url            VARCHAR,
    ingested_at             TIMESTAMP,

    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_entity_external_ids_lookup ON core.entity_external_ids (id_type, id_value);
CREATE INDEX IF NOT EXISTS idx_entity_external_ids_entity ON core.entity_external_ids (entity_type, entity_key);

-- Per-feature weights and decision thresholds for the weighted-fuzzy matcher.
CREATE TABLE IF NOT EXISTS core.entity_match_weights (
    weight_key              VARCHAR PRIMARY KEY,
    entity_type             VARCHAR NOT NULL,
    feature_name            VARCHAR NOT NULL,
    weight                  DOUBLE NOT NULL,
    threshold_accept        DOUBLE,
    threshold_review        DOUBLE,
    model_version           VARCHAR,
    is_active               BOOLEAN,
    notes                   VARCHAR,
    updated_at              TIMESTAMP,

    CHECK (weight >= 0.0 AND weight <= 1.0)
);

CREATE INDEX IF NOT EXISTS idx_entity_match_weights_entity ON core.entity_match_weights (entity_type, feature_name);

-- Scored candidate pairs produced during resolution, kept for auditability.
CREATE TABLE IF NOT EXISTS core.entity_match_candidates (
    candidate_key           VARCHAR PRIMARY KEY,
    entity_type             VARCHAR NOT NULL,
    source_record_key       VARCHAR NOT NULL,
    source_name             VARCHAR,
    normalized_source_name  VARCHAR,
    blocking_key            VARCHAR,

    candidate_entity_key    VARCHAR,
    candidate_name          VARCHAR,
    candidate_musicbrainz_id VARCHAR,

    -- Weighted-fuzzy match features (each 0.0-1.0 unless noted)
    name_similarity         DOUBLE,
    alias_similarity        DOUBLE,
    external_id_match       BOOLEAN,
    musicbrainz_id_match    BOOLEAN,
    country_match           BOOLEAN,
    genre_similarity        DOUBLE,
    social_handle_match     BOOLEAN,
    domain_match            BOOLEAN,
    date_proximity          DOUBLE,
    context_similarity      DOUBLE,
    weighted_score          DOUBLE,

    match_method            VARCHAR,
    match_confidence        DOUBLE,
    decision                VARCHAR,
    requires_review         BOOLEAN,
    reviewed_by             VARCHAR,
    reviewed_at             TIMESTAMP,
    model_version           VARCHAR,
    feature_scores          JSON,
    evidence                JSON,
    created_at              TIMESTAMP,

    CHECK (weighted_score IS NULL OR (weighted_score >= 0.0 AND weighted_score <= 1.0)),
    CHECK (match_confidence IS NULL
           OR (match_confidence >= 0.0 AND match_confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_match_candidates_source ON core.entity_match_candidates (entity_type, source_record_key);
CREATE INDEX IF NOT EXISTS idx_match_candidates_target ON core.entity_match_candidates (candidate_entity_key);
CREATE INDEX IF NOT EXISTS idx_match_candidates_blocking ON core.entity_match_candidates (blocking_key);
CREATE INDEX IF NOT EXISTS idx_match_candidates_score ON core.entity_match_candidates (weighted_score);

-- Default artist matching weights. INSERT OR IGNORE keeps operator overrides.
INSERT OR IGNORE INTO core.entity_match_weights
    (weight_key, entity_type, feature_name, weight, threshold_accept,
     threshold_review, model_version, is_active, notes, updated_at)
VALUES
    ('artist::musicbrainz_id_match', 'artist', 'musicbrainz_id_match', 1.0, 0.90, 0.70, 'v1', TRUE, 'Exact MBID match is decisive', NULL),
    ('artist::external_id_match', 'artist', 'external_id_match', 0.90, 0.90, 0.70, 'v1', TRUE, 'Any exact external identifier match', NULL),
    ('artist::name_similarity', 'artist', 'name_similarity', 0.45, 0.90, 0.70, 'v1', TRUE, 'Normalized name similarity', NULL),
    ('artist::alias_similarity', 'artist', 'alias_similarity', 0.25, 0.90, 0.70, 'v1', TRUE, 'Best similarity across known aliases', NULL),
    ('artist::social_handle_match', 'artist', 'social_handle_match', 0.20, 0.90, 0.70, 'v1', TRUE, 'Shared verified social handle', NULL),
    ('artist::genre_similarity', 'artist', 'genre_similarity', 0.10, 0.90, 0.70, 'v1', TRUE, 'Genre/subgenre overlap', NULL),
    ('artist::country_match', 'artist', 'country_match', 0.05, 0.90, 0.70, 'v1', TRUE, 'Country of origin agreement', NULL),
    ('festival::name_similarity', 'festival', 'name_similarity', 0.50, 0.88, 0.68, 'v1', TRUE, 'Normalized festival name similarity', NULL),
    ('festival::alias_similarity', 'festival', 'alias_similarity', 0.20, 0.88, 0.68, 'v1', TRUE, 'Best similarity across known aliases', NULL),
    ('festival::country_match', 'festival', 'country_match', 0.15, 0.88, 0.68, 'v1', TRUE, 'Country agreement', NULL),
    ('festival::domain_match', 'festival', 'domain_match', 0.15, 0.88, 0.68, 'v1', TRUE, 'Shared official domain', NULL);

-- Flattened lookup keys (name, alias, external id, social handle) used to
-- generate blocking candidates before scoring.
CREATE OR REPLACE VIEW core.artist_resolution_keys AS
SELECT
    a.artist_key,
    a.musicbrainz_id,
    'name' AS key_type,
    a.normalized_name AS key_value,
    a.country,
    a.primary_genre,
    1.0 AS key_confidence
FROM core.artists AS a
UNION ALL
SELECT
    al.artist_key,
    a.musicbrainz_id,
    'alias' AS key_type,
    al.normalized_alias AS key_value,
    a.country,
    a.primary_genre,
    COALESCE(al.confidence, 0.8) AS key_confidence
FROM core.artist_aliases AS al
LEFT JOIN core.artists AS a ON a.artist_key = al.artist_key
UNION ALL
SELECT
    x.entity_key AS artist_key,
    a.musicbrainz_id,
    'external_id:' || x.id_type AS key_type,
    x.id_value AS key_value,
    a.country,
    a.primary_genre,
    COALESCE(x.confidence, 1.0) AS key_confidence
FROM core.entity_external_ids AS x
LEFT JOIN core.artists AS a ON a.artist_key = x.entity_key
WHERE x.entity_type = 'artist'
UNION ALL
SELECT
    h.artist_key,
    a.musicbrainz_id,
    'social:' || h.platform AS key_type,
    h.normalized_handle AS key_value,
    a.country,
    a.primary_genre,
    COALESCE(h.confidence, 0.9) AS key_confidence
FROM core.artist_social_handles AS h
LEFT JOIN core.artists AS a ON a.artist_key = h.artist_key;

-- ===========================================================================
-- metrics - point-in-time observations
-- ===========================================================================
CREATE TABLE IF NOT EXISTS metrics.artist_metrics (
    metric_key              VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    source_system           VARCHAR NOT NULL,
    metric_type             VARCHAR NOT NULL,
    value                   DOUBLE,
    observed_date           DATE,
    fetched_at              TIMESTAMP,
    meta_data               JSON
);

CREATE INDEX IF NOT EXISTS idx_artist_metrics_artist ON metrics.artist_metrics (artist_key);
CREATE INDEX IF NOT EXISTS idx_artist_metrics_observed ON metrics.artist_metrics (observed_date);

-- Aggregated sentiment per artist from the scraper ensemble (VADER + sources).
CREATE TABLE IF NOT EXISTS metrics.artist_sentiment (
    artist_key              VARCHAR PRIMARY KEY,
    sentiment_label         VARCHAR,
    compound                DOUBLE,
    positive                DOUBLE,
    neutral                 DOUBLE,
    negative                DOUBLE,
    sample_size             INTEGER,
    mention_volume          INTEGER,
    attention_score         DOUBLE,
    top_topics              JSON,
    top_positive            JSON,
    top_negative            JSON,
    llm_summary             VARCHAR,
    sources_used            JSON,
    generated_at            TIMESTAMP
);

-- Social / web signal observations (per-source mention counts + text refs).
CREATE TABLE IF NOT EXISTS metrics.social_signals (
    signal_key              VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    source_system           VARCHAR NOT NULL,
    mention_count           INTEGER,
    points                  DOUBLE,
    comments                DOUBLE,
    pageviews_30d           DOUBLE,
    news_mentions           DOUBLE,
    fetched_at              TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_social_signals_artist ON metrics.social_signals (artist_key);

-- Popularity history for the snapshot columns on core.artists.
CREATE TABLE IF NOT EXISTS metrics.artist_popularity (
    popularity_key          VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    source_system           VARCHAR NOT NULL,
    observed_date           DATE,
    popularity_score        DOUBLE,
    popularity_rank         INTEGER,
    followers               BIGINT,
    monthly_listeners       BIGINT,
    playcount               BIGINT,
    evidence_url            VARCHAR,
    fetched_at              TIMESTAMP,

    CHECK (popularity_score IS NULL
           OR (popularity_score >= 0.0 AND popularity_score <= 100.0))
);

CREATE INDEX IF NOT EXISTS idx_artist_popularity_artist ON metrics.artist_popularity (artist_key, observed_date);

-- ===========================================================================
-- audit - ingestion bookkeeping (written by warehouse.duckdb_manager)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit.ingest_run (
    run_id                  VARCHAR PRIMARY KEY,
    source_system           VARCHAR NOT NULL,
    started_at              TIMESTAMP,
    finished_at             TIMESTAMP,
    status                  VARCHAR,
    records_read            INTEGER,
    records_written         INTEGER,
    error_count             INTEGER,
    parser_version          VARCHAR,
    parameters              VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_ingest_run_source ON audit.ingest_run (source_system, started_at);

CREATE TABLE IF NOT EXISTS audit.ingest_error (
    run_id                  VARCHAR,
    source_url              VARCHAR,
    record_key              VARCHAR,
    error_type              VARCHAR,
    error_message           VARCHAR,
    payload                 VARCHAR,
    created_at              TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ingest_error_run ON audit.ingest_error (run_id);
