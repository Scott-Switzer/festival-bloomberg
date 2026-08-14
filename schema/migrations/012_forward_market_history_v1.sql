-- ===========================================================================
-- 012_forward_market_history_v1.sql
-- ===========================================================================
-- Tracked event registry, collector runs, venue identity canonicalization,
-- and source rights decisions for recurring market history collection.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS economics;

-- Tracked event registry for recurring collection
CREATE TABLE IF NOT EXISTS economics.tracked_events (
    canonical_event_id      VARCHAR PRIMARY KEY,
    artist_id               VARCHAR NOT NULL,
    venue_id                VARCHAR NOT NULL,
    event_time              TIMESTAMP NOT NULL,
    tracking_started_at     TIMESTAMP NOT NULL,
    tracking_status         VARCHAR NOT NULL,
    providers               JSON,
    reason                  VARCHAR,
    last_snapshot_at        TIMESTAMP,
    knowledge_time          TIMESTAMP NOT NULL
);

-- Collector run logs for operational monitoring
CREATE TABLE IF NOT EXISTS economics.collector_runs (
    run_id                  VARCHAR PRIMARY KEY,
    started_at              TIMESTAMP NOT NULL,
    finished_at             TIMESTAMP,
    events_attempted        INTEGER,
    events_succeeded        INTEGER,
    provider_status_json    JSON,
    snapshots_appended      INTEGER,
    snapshots_deduped       INTEGER,
    errors_json             JSON,
    cost_usd                DOUBLE,
    quota_metadata_json     JSON,
    exit_code               INTEGER,
    software_version        VARCHAR,
    knowledge_time          TIMESTAMP NOT NULL
);

-- Venue identity merge audit trail
CREATE TABLE IF NOT EXISTS economics.venue_merge_actions (
    merge_action_id         VARCHAR PRIMARY KEY,
    source_venue_ids        JSON NOT NULL,
    target_canonical_venue_id VARCHAR NOT NULL,
    resolution_method       VARCHAR NOT NULL,
    supporting_observations_json JSON,
    merged_at               TIMESTAMP NOT NULL,
    software_version        VARCHAR NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL
);

-- Venue aliases for identity resolution
CREATE TABLE IF NOT EXISTS economics.venue_aliases (
    alias_id                VARCHAR PRIMARY KEY,
    canonical_venue_id      VARCHAR NOT NULL,
    alias_venue_id          VARCHAR NOT NULL,
    alias_name              VARCHAR,
    alias_provider          VARCHAR,
    alias_provider_venue_id VARCHAR,
    superseded_at           TIMESTAMP NOT NULL,
    superseded_by           VARCHAR,
    knowledge_time          TIMESTAMP NOT NULL
);

-- Source rights decisions for capacity and venue enrichment
CREATE TABLE IF NOT EXISTS economics.source_rights_decisions (
    decision_id             VARCHAR PRIMARY KEY,
    source_url              VARCHAR NOT NULL,
    source_name             VARCHAR NOT NULL,
    rights_decision         VARCHAR NOT NULL,
    decision_rationale      VARCHAR,
    decided_at              TIMESTAMP NOT NULL,
    decided_by              VARCHAR NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL
);

-- Add venue canonicalization columns to events.venues
ALTER TABLE events.venues ADD COLUMN IF NOT EXISTS superseded_by VARCHAR;
ALTER TABLE events.venues ADD COLUMN IF NOT EXISTS canonical_method VARCHAR;

-- Indexes for tracked events
CREATE INDEX IF NOT EXISTS idx_tracked_events_status
  ON economics.tracked_events (tracking_status, event_time);
CREATE INDEX IF NOT EXISTS idx_tracked_events_venue
  ON economics.tracked_events (venue_id, tracking_status);

-- Indexes for collector runs
CREATE INDEX IF NOT EXISTS idx_collector_runs_started
  ON economics.collector_runs (started_at DESC);

-- Indexes for venue aliases
CREATE INDEX IF NOT EXISTS idx_venue_aliases_canonical
  ON economics.venue_aliases (canonical_venue_id);
CREATE INDEX IF NOT EXISTS idx_venue_aliases_alias
  ON economics.venue_aliases (alias_venue_id);

-- Indexes for source rights
CREATE INDEX IF NOT EXISTS idx_source_rights_decision
  ON economics.source_rights_decisions (rights_decision, source_name);
