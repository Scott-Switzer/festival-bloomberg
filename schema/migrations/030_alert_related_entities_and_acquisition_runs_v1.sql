-- ---------------------------------------------------------------------------
-- 030: ALERT RELATED ENTITIES + EXPLICIT ACQUISITION RUNS
--
-- Two upgrades that turn TODAY into a genuinely personalized view:
--
--   1. core.alert_related_entities — every alert links to EVERY entity it
--      touches (EVENT, ARTIST, VENUE, MARKET, PROMOTER, TOUR, FESTIVAL).
--      A watchlist holding ARTIST mbid::X can then see NEW_EVENT alerts on
--      EVENT tm::123 whose related graph includes that artist. No guessed
--      relationships: only source-backed mappings (resolved attractions,
--      venue/city columns, promoter column) are attached.
--
--   2. provider_acquisition_runs — every Ticketmaster snapshot becomes
--      attributable to an explicit logical acquisition run instead of
--      inferred timestamp batches. NEW_EVENT = present in latest COMPLETE
--      run AND absent from all prior runs.
--
-- Both are additive; historical snapshots keep NULL acquisition_run_id and
-- the timestamp-batch fallback remains for them.
-- ---------------------------------------------------------------------------

-- 1. Explicit acquisition runs.
CREATE TABLE IF NOT EXISTS audit.provider_acquisition_runs (
    run_id          VARCHAR PRIMARY KEY,   -- e.g. 'tm::2026-08-18T09:00:00Z'
    provider        VARCHAR NOT NULL,
    operation       VARCHAR NOT NULL,      -- e.g. 'national_music_events_refresh'
    started_at      TIMESTAMP NOT NULL DEFAULT now(),
    completed_at    TIMESTAMP,
    status          VARCHAR NOT NULL DEFAULT 'RUNNING',  -- RUNNING|COMPLETE|FAILED
    request_count   BIGINT NOT NULL DEFAULT 0,
    record_count    BIGINT NOT NULL DEFAULT 0,
    error_count     BIGINT NOT NULL DEFAULT 0,
    note            VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_acq_runs_provider
  ON audit.provider_acquisition_runs (provider, status, started_at);

-- 2. Snapshots carry an explicit run id (NULL for historical rows).
ALTER TABLE events.provider_event_snapshots
  ADD COLUMN IF NOT EXISTS acquisition_run_id VARCHAR;

-- 3. Alert -> related-entity graph (the personalization fabric).
CREATE TABLE IF NOT EXISTS core.alert_related_entities (
    relation_key    VARCHAR PRIMARY KEY,   -- hash(alert_key, entity_type, entity_key, relationship)
    alert_key       VARCHAR NOT NULL,
    entity_type     VARCHAR NOT NULL,      -- EVENT|ARTIST|VENUE|MARKET|PROMOTER|TOUR|FESTIVAL
    entity_key      VARCHAR NOT NULL,
    relationship    VARCHAR NOT NULL,      -- EVENT_PRIMARY_ARTIST|EVENT_VENUE|EVENT_MARKET|EVENT_PROMOTER|EVENT_ARTIST|EVENT_SELF
    entity_name     VARCHAR,
    source          VARCHAR NOT NULL DEFAULT 'ticketmaster',
    ingested_at     TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alert_related_entity
  ON core.alert_related_entities (entity_type, entity_key, alert_key);
CREATE INDEX IF NOT EXISTS idx_alert_related_alert
  ON core.alert_related_entities (alert_key);
