-- ===========================================================================
-- 026_national_coverage_entity_master_v1.sql
-- ===========================================================================
-- NATIONAL_COVERAGE_ENTITY_MASTER_AND_FESTIVAL_HISTORY_V1.
--
--   1. terminal.acquisition_partitions — extend the partition manifest into a
--      real PARTITION TREE: every leaf/sub-partition records its parent and
--      depth so a recursive date-window subdivision of an oversized market is
--      auditable (COMPLETE / SPLIT / TRUNCATED_BY_CAP / RATE_LIMITED ...).
--
--   2. core.entity_external_ids already exists (migration 004); this
--      migration adds a canonical promoter/company spine and an external-id
--      resolution ledger so incoming provider entities can resolve to
--      MATCHED / AMBIGUOUS / UNMATCHED without ever silently duplicating.
--
-- Non-negotiable semantics: entity resolution is append-only and never
-- auto-merges ambiguous identities; every row carries provenance.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Partition-tree columns.
-- ---------------------------------------------------------------------------
ALTER TABLE terminal.acquisition_partitions ADD COLUMN parent_partition_id VARCHAR;
ALTER TABLE terminal.acquisition_partitions ADD COLUMN depth INTEGER;
ALTER TABLE terminal.acquisition_partitions ADD COLUMN split_reason VARCHAR;

-- ---------------------------------------------------------------------------
-- 2. Canonical promoter / company spine.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.promoters (
    promoter_key            VARCHAR PRIMARY KEY,
    name                    VARCHAR NOT NULL,
    normalized_name         VARCHAR NOT NULL,
    aliases                 JSON,
    company_type            VARCHAR,               -- promoter|venue_operator|ticketing|label|other
    parent_company_key      VARCHAR,               -- explicit ownership evidence only
    parent_company_name     VARCHAR,
    website                 VARCHAR,
    external_ids            JSON,
    source_system           VARCHAR,
    source_url              VARCHAR,
    knowledge_time          TIMESTAMP,
    rights_status           VARCHAR,
    commercial_use_status   VARCHAR,
    ingested_at             TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_promoters_normalized ON core.promoters (normalized_name);

-- ---------------------------------------------------------------------------
-- 3. Append-only external-id resolution ledger (MATCHED/AMBIGUOUS/UNMATCHED).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.entity_resolution_ledger (
    resolution_id           VARCHAR PRIMARY KEY,   -- hash(entity_type, entity_key, id_type, id_value)
    entity_type             VARCHAR NOT NULL,      -- ARTIST|VENUE|PROMOTER|FESTIVAL|...
    entity_key              VARCHAR NOT NULL,      -- canonical entity key (may be name::<normalized>)
    source_system           VARCHAR NOT NULL,
    id_type                 VARCHAR NOT NULL,      -- ticketmaster|spotify|musicbrainz|wikidata|...
    id_value                VARCHAR NOT NULL,
    display_name            VARCHAR,
    normalized_name         VARCHAR NOT NULL,
    resolution_status       VARCHAR NOT NULL,      -- MATCHED|AMBIGUOUS|UNMATCHED
    match_method            VARCHAR,
    match_confidence        DOUBLE,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    rights_status           VARCHAR NOT NULL,
    software_version        VARCHAR,
    ingested_at             TIMESTAMP NOT NULL,

    CHECK (match_confidence IS NULL OR (match_confidence >= 0.0 AND match_confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_entity_resolution_entity
  ON core.entity_resolution_ledger (entity_type, entity_key, resolution_status);
CREATE INDEX IF NOT EXISTS idx_entity_resolution_id
  ON core.entity_resolution_ledger (id_type, id_value);
