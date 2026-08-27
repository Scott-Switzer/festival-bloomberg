-- ===========================================================================
-- 043_artist_security_master_v1.sql
-- ===========================================================================
-- ARTIST_SECURITY_MASTER_V1 — the artist as a tradable security.
--
-- The event/ticket tape is one half of the Bloomberg analogy. This migration
-- builds the other half: a canonical SECURITY object per artist carrying
-- factor families (demand, momentum, live, market, pricing, touring, catalog,
-- network, festival fit, risk, relative value, evidence quality) instead of a
-- single opaque "artist score".
--
-- DESIGN RULES
--   * artist_key is OUR canonical key (mbid::<mbid> or name::<name>) — the
--     same key used across core.artists / metrics.attention / the tape.
--   * Every observation carries the FULL PIT contract:
--         as_of (the observation date) != available_at (source publication)
--         != retrieved_at (when we fetched it) != knowledge_time (ingest).
--     archive/backfill capture is NEVER the publication time.
--   * value + unit + confidence; UNKNOWN stays NULL — never a fabricated zero.
--   * rights_status / commercial_use_status are REQUIRED on every row.
--   * Never infer attendance/sales/sell-through from listing/offer changes.
--   * Factor families are labeled EVIDENCE-BACKED factor observations, not an
--     opaque score. No GO/HOLD/PASS.
--
-- RELATIONSHIP TO EXISTING TABLES
--   * core.artists / core.entity_external_ids (027) remain the identity layer.
--   * metrics.artist_attention_observations (006) remains the raw attention
--     tape; artist_factor_observations DERIVES factor families from it.
--   * core.event_performers / raw.musicbrainz_event (028) feed live statistics.
--   * core.releases / core.release_groups (027) feed catalog statistics.
--   * core.entity_relationships (027) feeds network edges.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 0. Schema (idempotent; the base schema also declares it).
--    NOTE: named `asm` (Artist Security Master). DuckDB names the catalog
--    after the database FILE (e.g. a file `artist_security.duckdb` creates
--    catalog `artist_security`), so a schema named `artist_security` would
--    be ambiguous; `security` clashes with DuckDB's built-in catalog.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS asm;

-- ---------------------------------------------------------------------------
-- 1. Canonical security master — one row per artist security object.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asm.artist_security_master (
    artist_key           VARCHAR PRIMARY KEY,
    security_status      VARCHAR NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | STALE | RETIRED
    primary_name         VARCHAR,
    factor_families      JSON,           -- families that have observations
    last_snapshot_at     TIMESTAMP,
    data_confidence      DOUBLE,
    first_ingested_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- 2. Artist factor observations — the core factor tape (one row per factor).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics.artist_factor_observations (
    factor_observation_key VARCHAR PRIMARY KEY,
    artist_key           VARCHAR NOT NULL,
    factor_family        VARCHAR NOT NULL,  -- DEMAND | MOMENTUM | LIVE | MARKET |
                                            -- PRICING | TOURING | CATALOG | NETWORK |
                                            -- FESTIVAL_FIT | RISK | RELATIVE_VALUE | EVIDENCE
    factor_name          VARCHAR NOT NULL,  -- e.g. LB_LISTENS_28D, WIKI_VIEWS_7D,
                                            -- SHOWS_365D, YT_SUBSCRIBERS, ...
    value                DOUBLE,
    value_unit           VARCHAR,
    as_of                DATE NOT NULL,     -- observation date (event_time / market date)
    available_at         TIMESTAMP,         -- source publication / availability bound
    retrieved_at         TIMESTAMP NOT NULL,
    period_start         DATE,
    period_end           DATE,
    source_system        VARCHAR NOT NULL,
    source_version       VARCHAR NOT NULL,
    source_url           VARCHAR,
    rights_status        VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    confidence           DOUBLE,
    evidence_json        JSON,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_factor_obs_artist
  ON metrics.artist_factor_observations (artist_key, as_of);
CREATE INDEX IF NOT EXISTS idx_factor_obs_name
  ON metrics.artist_factor_observations (factor_name, as_of);

-- ---------------------------------------------------------------------------
-- 3. Artist × market factor observations — the market snapshot object.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics.artist_market_factor_observations (
    observation_key      VARCHAR PRIMARY KEY,
    artist_key           VARCHAR NOT NULL,
    market_key           VARCHAR NOT NULL,  -- e.g. chicago-il, las-vegas-nv
    factor_family        VARCHAR NOT NULL,
    factor_name          VARCHAR NOT NULL,
    value                DOUBLE,
    value_unit           VARCHAR,
    as_of                DATE NOT NULL,
    available_at         TIMESTAMP,
    retrieved_at         TIMESTAMP NOT NULL,
    period_start         DATE,
    period_end           DATE,
    source_system        VARCHAR NOT NULL,
    source_version       VARCHAR NOT NULL,
    source_url           VARCHAR,
    rights_status        VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    confidence           DOUBLE,
    evidence_json        JSON,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_factor_obs
  ON metrics.artist_market_factor_observations (artist_key, market_key, as_of);

-- ---------------------------------------------------------------------------
-- 4. Peer edges — comparable/substitute artists (relative-value universe).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.artist_peer_edges (
    edge_key             VARCHAR PRIMARY KEY,  -- hash(subject, peer, edge_type, source)
    subject_key          VARCHAR NOT NULL,
    peer_key             VARCHAR NOT NULL,
    edge_type            VARCHAR NOT NULL,     -- CO_BILLED | SIMILAR | SUBSTITUTE
    strength             DOUBLE,               -- co-bill count / similarity evidence
    source_system        VARCHAR NOT NULL,
    source_url           VARCHAR,
    knowledge_time       TIMESTAMP NOT NULL,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_peer_edges_subject ON core.artist_peer_edges (subject_key);
CREATE INDEX IF NOT EXISTS idx_peer_edges_peer ON core.artist_peer_edges (peer_key);

-- ---------------------------------------------------------------------------
-- 5. Collaboration edges — artist collab graph (network family).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.artist_collaboration_edges (
    edge_key             VARCHAR PRIMARY KEY,
    subject_key          VARCHAR NOT NULL,
    collaborator_key     VARCHAR NOT NULL,
    collaboration_type   VARCHAR,              -- RECORDING | PERFORMANCE | FEATURE
    evidence             VARCHAR,
    source_system        VARCHAR NOT NULL,
    source_url           VARCHAR,
    knowledge_time       TIMESTAMP NOT NULL,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_collab_edges_subject ON core.artist_collaboration_edges (subject_key);

-- ---------------------------------------------------------------------------
-- 6. Live statistics — the live-strength factor family.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics.artist_live_statistics (
    stat_key             VARCHAR PRIMARY KEY,  -- hash(artist_key, as_of, version)
    artist_key           VARCHAR NOT NULL,
    as_of                DATE NOT NULL,
    shows_30d            INTEGER,
    shows_90d            INTEGER,
    shows_365d           INTEGER,
    markets_365d         INTEGER,
    unique_venues_365d   INTEGER,
    festival_appearances_365d INTEGER,
    days_since_last_show INTEGER,
    venue_progression    JSON,                 -- historical venue-size progression
    source_system        VARCHAR NOT NULL,
    source_version       VARCHAR NOT NULL,
    retrieved_at         TIMESTAMP NOT NULL,
    rights_status        VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    evidence_json        JSON,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_live_stats_artist ON metrics.artist_live_statistics (artist_key, as_of);

-- ---------------------------------------------------------------------------
-- 7. Catalog statistics — the fundamentals factor family.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics.artist_catalog_statistics (
    stat_key             VARCHAR PRIMARY KEY,
    artist_key           VARCHAR NOT NULL,
    as_of                DATE NOT NULL,
    releases_12m         INTEGER,
    releases_36m         INTEGER,
    days_since_last_release INTEGER,
    catalog_depth        INTEGER,
    collaboration_centrality DOUBLE,
    recent_release_intensity DOUBLE,
    source_system        VARCHAR NOT NULL,
    source_version       VARCHAR NOT NULL,
    retrieved_at         TIMESTAMP NOT NULL,
    rights_status        VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    evidence_json        JSON,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_catalog_stats_artist ON metrics.artist_catalog_statistics (artist_key, as_of);

-- ---------------------------------------------------------------------------
-- 8. Security snapshots — computed per-artist security snapshot (display object).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics.artist_security_snapshots (
    snapshot_key         VARCHAR PRIMARY KEY,  -- hash(artist_key, snapshot_date, version)
    artist_key           VARCHAR NOT NULL,
    snapshot_date        DATE NOT NULL,
    factor_summary       JSON,                 -- the terminal display object (per-family)
    demand_percentile    DOUBLE,
    momentum_percentile  DOUBLE,
    live_percentile      DOUBLE,
    data_confidence      DOUBLE,
    snapshot_version     VARCHAR NOT NULL,
    calculated_at        TIMESTAMP NOT NULL,
    ingested_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_security_snapshots_artist
  ON metrics.artist_security_snapshots (artist_key, snapshot_date);
