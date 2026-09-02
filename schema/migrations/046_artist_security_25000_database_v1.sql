-- ARTIST_SECURITY_25000_DATABASE_V1
-- Tiered security coverage over the existing canonical artist/reference graph.

CREATE SCHEMA IF NOT EXISTS security;

CREATE TABLE IF NOT EXISTS security.artist_security_tiers (
    artist_key VARCHAR NOT NULL,
    tier VARCHAR NOT NULL, -- HOT_1000 | CORE_5000 | COVERAGE_25000 | IDENTITY_100K_PLUS
    selection_bucket VARCHAR NOT NULL,
    selection_reason VARCHAR NOT NULL,
    evidence_ref VARCHAR,
    as_of DATE NOT NULL,
    source_version VARCHAR NOT NULL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (artist_key, tier)
);
CREATE INDEX IF NOT EXISTS idx_artist_tiers_tier ON security.artist_security_tiers(tier, artist_key);

CREATE TABLE IF NOT EXISTS security.artist_security_universe_25000 (
    artist_key VARCHAR PRIMARY KEY,
    artist_name VARCHAR,
    mbid VARCHAR,
    tier VARCHAR NOT NULL,
    selection_bucket VARCHAR NOT NULL,
    selection_reason VARCHAR NOT NULL,
    evidence_refs JSON,
    as_of DATE NOT NULL,
    source_version VARCHAR NOT NULL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_artist_25k_tier ON security.artist_security_universe_25000(tier);

CREATE TABLE IF NOT EXISTS security.bulk_dataset_manifests (
    manifest_key VARCHAR PRIMARY KEY,
    dataset VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    source_version VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    artist_count BIGINT,
    date_min DATE,
    date_max DATE,
    raw_bytes BIGINT,
    normalized_bytes BIGINT,
    partition_count INTEGER,
    partitions JSON,
    checksums JSON,
    rights_status VARCHAR NOT NULL,
    commercial_use_status VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS security.artist_security_scale_reports (
    report_key VARCHAR PRIMARY KEY,
    as_of DATE NOT NULL,
    canonical_identity_count BIGINT,
    coverage_security_count BIGINT,
    core_security_count BIGINT,
    hot_security_count BIGINT,
    canonical_event_count BIGINT,
    future_active_event_count BIGINT,
    active_ticket_pair_count BIGINT,
    venue_count BIGINT,
    capacity_evidenced_venue_count BIGINT,
    artist_market_row_count BIGINT,
    factor_observation_count BIGINT,
    report_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
