-- IDENTITY_GRAPH_V2 — deterministic, additive identity graph.
--
-- This migration creates audit/output tables only.  The V2 builder is
-- read-only with respect to core/identity source tables and can emit an
-- in-memory/report result without applying this migration.

CREATE SCHEMA IF NOT EXISTS identity;

CREATE TABLE IF NOT EXISTS identity.graph_v2_runs (
    run_key                 VARCHAR PRIMARY KEY,
    run_version             VARCHAR NOT NULL,
    as_of                   TIMESTAMP NOT NULL,
    canonical_limit         INTEGER NOT NULL,
    canonical_count         INTEGER NOT NULL,
    broader_count           INTEGER NOT NULL,
    evidence_count          BIGINT NOT NULL,
    edge_count              BIGINT NOT NULL,
    conflict_count          BIGINT NOT NULL,
    provider_count          INTEGER NOT NULL,
    input_digest             VARCHAR NOT NULL,
    source_tables            JSON NOT NULL,
    rights_status            VARCHAR NOT NULL,
    commercial_use_status    VARCHAR NOT NULL,
    knowledge_time_basis     VARCHAR NOT NULL,
    build_status              VARCHAR NOT NULL,
    source_artifacts          JSON NOT NULL,
    estate_identity           VARCHAR NOT NULL,
    resource_warning          VARCHAR NOT NULL,
    available_broader_artist_count BIGINT NOT NULL,
    dense_edge_count_avoided  BIGINT NOT NULL,
    created_at               TIMESTAMP NOT NULL,
    CHECK (canonical_limit > 0),
    CHECK (canonical_count >= 0),
    CHECK (broader_count >= 0)
);

CREATE TABLE IF NOT EXISTS identity.graph_v2_evidence (
    evidence_key             VARCHAR PRIMARY KEY,
    run_key                  VARCHAR NOT NULL,
    artist_key               VARCHAR NOT NULL,
    scope                    VARCHAR NOT NULL, -- CANONICAL_25K | BROADER_CANONICAL
    provider                 VARCHAR NOT NULL,
    provider_id              VARCHAR NOT NULL,
    source_table              VARCHAR NOT NULL,
    source_ref                VARCHAR,
    source_url                VARCHAR,
    evidence_kind             VARCHAR NOT NULL,
    evidence_status           VARCHAR NOT NULL,
    claimed_status            VARCHAR,
    source_system             VARCHAR,
    source_version            VARCHAR,
    source_checksum           VARCHAR,
    retrieved_at              TIMESTAMP,
    trust_class               VARCHAR NOT NULL,
    resolution_basis          VARCHAR NOT NULL,
    rights_status             VARCHAR NOT NULL,
    commercial_use_status     VARCHAR NOT NULL,
    knowledge_time            TIMESTAMP,
    payload_json              JSON,
    created_at                TIMESTAMP NOT NULL,
    CHECK (evidence_status IN (
        'VERIFIED_EXACT', 'SUPPORTED_MULTI_SOURCE', 'CANDIDATE',
        'AMBIGUOUS', 'CONFLICT', 'MISSING'
    )),
    CHECK (scope IN ('CANONICAL_25K', 'BROADER_CANONICAL')),
    CHECK (provider IN (
        'MUSICBRAINZ', 'WIKIDATA', 'YOUTUBE', 'SPOTIFY', 'DISCOGS', 'ISNI',
        'VIAF', 'TICKETMASTER', 'OFFICIAL_WEBSITE', 'LISTENBRAINZ',
        'WIKIPEDIA', 'SOUNDCLOUD', 'APPLE_MUSIC', 'BANDCAMP', 'SONGKICK',
        'BANDSINTOWN', 'SETLISTFM', 'ALLMUSIC', 'LASTFM', 'MYSPACE', 'IPI'
    ))
);
CREATE INDEX IF NOT EXISTS idx_graph_v2_evidence_artist
    ON identity.graph_v2_evidence (run_key, artist_key, provider);

CREATE TABLE IF NOT EXISTS identity.graph_v2_edges (
    edge_key                 VARCHAR PRIMARY KEY,
    run_key                  VARCHAR NOT NULL,
    artist_key               VARCHAR NOT NULL,
    scope                    VARCHAR NOT NULL,
    provider                 VARCHAR NOT NULL,
    provider_id              VARCHAR,
    resolution_status        VARCHAR NOT NULL,
    evidence_keys            JSON NOT NULL,
    evidence_count           INTEGER NOT NULL,
    source_refs              JSON NOT NULL,
    rights_status            VARCHAR NOT NULL,
    commercial_use_status    VARCHAR NOT NULL,
    knowledge_time           TIMESTAMP,
    source_system            VARCHAR,
    source_version           VARCHAR,
    source_checksum           VARCHAR,
    retrieved_at              TIMESTAMP,
    trust_class               VARCHAR NOT NULL,
    resolution_basis          VARCHAR NOT NULL,
    created_at               TIMESTAMP NOT NULL,
    CHECK (resolution_status IN (
        'VERIFIED_EXACT', 'SUPPORTED_MULTI_SOURCE', 'CANDIDATE',
        'AMBIGUOUS', 'CONFLICT', 'MISSING'
    )),
    CHECK (scope IN ('CANONICAL_25K', 'BROADER_CANONICAL')),
    CHECK (provider IN (
        'MUSICBRAINZ', 'WIKIDATA', 'YOUTUBE', 'SPOTIFY', 'DISCOGS', 'ISNI',
        'VIAF', 'TICKETMASTER', 'OFFICIAL_WEBSITE', 'LISTENBRAINZ',
        'WIKIPEDIA', 'SOUNDCLOUD', 'APPLE_MUSIC', 'BANDCAMP', 'SONGKICK',
        'BANDSINTOWN', 'SETLISTFM', 'ALLMUSIC', 'LASTFM', 'MYSPACE', 'IPI'
    ))
);
CREATE INDEX IF NOT EXISTS idx_graph_v2_edges_lookup
    ON identity.graph_v2_edges (run_key, provider, provider_id);

CREATE TABLE IF NOT EXISTS identity.graph_v2_conflicts (
    conflict_key             VARCHAR PRIMARY KEY,
    run_key                  VARCHAR NOT NULL,
    conflict_type            VARCHAR NOT NULL, -- SHARED_PROVIDER_ID | MULTIPLE_PROVIDER_IDS | INVALID_PROVIDER_ID
    provider                 VARCHAR NOT NULL,
    provider_id              VARCHAR,
    artist_keys              JSON NOT NULL,
    evidence_keys            JSON NOT NULL,
    source_refs               JSON NOT NULL,
    explanation              VARCHAR NOT NULL,
    rights_status            VARCHAR NOT NULL,
    commercial_use_status    VARCHAR NOT NULL,
    knowledge_time           TIMESTAMP,
    source_system             VARCHAR,
    source_version            VARCHAR,
    source_checksum           VARCHAR,
    retrieved_at              TIMESTAMP,
    created_at                TIMESTAMP NOT NULL,
    CHECK (provider IN (
        'MUSICBRAINZ', 'WIKIDATA', 'YOUTUBE', 'SPOTIFY', 'DISCOGS', 'ISNI',
        'VIAF', 'TICKETMASTER', 'OFFICIAL_WEBSITE', 'LISTENBRAINZ',
        'WIKIPEDIA', 'SOUNDCLOUD', 'APPLE_MUSIC', 'BANDCAMP', 'SONGKICK',
        'BANDSINTOWN', 'SETLISTFM', 'ALLMUSIC', 'LASTFM', 'MYSPACE', 'IPI', 'UNKNOWN_PROVIDER'
    ))
);
CREATE INDEX IF NOT EXISTS idx_graph_v2_conflicts_run
    ON identity.graph_v2_conflicts (run_key, provider, conflict_type);

CREATE TABLE IF NOT EXISTS identity.graph_v2_scorecard (
    scorecard_key            VARCHAR PRIMARY KEY,
    run_key                  VARCHAR NOT NULL,
    scope                    VARCHAR NOT NULL,
    provider                 VARCHAR NOT NULL,
    universe_count           INTEGER NOT NULL,
    verified_exact_count     INTEGER NOT NULL,
    supported_multi_source_count INTEGER NOT NULL,
    candidate_count          INTEGER NOT NULL,
    ambiguous_count          INTEGER NOT NULL,
    conflict_count           INTEGER NOT NULL,
    missing_count            INTEGER NOT NULL,
    invalid_count            INTEGER NOT NULL,
    coverage_pct             DOUBLE,
    rights_status            VARCHAR NOT NULL,
    commercial_use_status    VARCHAR NOT NULL,
    knowledge_time           TIMESTAMP,
    created_at               TIMESTAMP NOT NULL,
    CHECK (scope IN ('CANONICAL_25K', 'BROADER_CANONICAL')),
    CHECK (provider IN (
        'MUSICBRAINZ', 'WIKIDATA', 'YOUTUBE', 'SPOTIFY', 'DISCOGS', 'ISNI',
        'VIAF', 'TICKETMASTER', 'OFFICIAL_WEBSITE', 'LISTENBRAINZ',
        'WIKIPEDIA', 'SOUNDCLOUD', 'APPLE_MUSIC', 'BANDCAMP', 'SONGKICK',
        'BANDSINTOWN', 'SETLISTFM', 'ALLMUSIC', 'LASTFM', 'MYSPACE', 'IPI'
    ))
);
CREATE INDEX IF NOT EXISTS idx_graph_v2_scorecard_lookup
    ON identity.graph_v2_scorecard (run_key, scope, provider);

CREATE TABLE IF NOT EXISTS identity.graph_v2_nodes (
    node_key                 VARCHAR PRIMARY KEY,
    run_key                  VARCHAR NOT NULL,
    artist_key               VARCHAR NOT NULL,
    artist_name              VARCHAR,
    musicbrainz_id           VARCHAR,
    scope                    VARCHAR NOT NULL,
    estate_tier              VARCHAR,
    provider_status_json     JSON NOT NULL,
    rights_status            VARCHAR NOT NULL,
    commercial_use_status    VARCHAR NOT NULL,
    knowledge_time           TIMESTAMP,
    created_at               TIMESTAMP NOT NULL,
    CHECK (scope IN ('CANONICAL_25K', 'BROADER_CANONICAL'))
);
CREATE INDEX IF NOT EXISTS idx_graph_v2_nodes_lookup
    ON identity.graph_v2_nodes (run_key, scope, artist_key);
