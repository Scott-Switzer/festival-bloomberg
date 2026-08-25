-- MARKET_COMPETITIVE_CALENDAR_V1
-- Preserve the raw Ticketmaster classification structure (IDs + family flag)
-- on every event snapshot so the competitive calendar can be explained by
-- segment/genre/subgenre without re-resolving names. Additive columns only;
-- existing snapshots keep NULL IDs (name-only evidence).
--
-- NOTE: DuckDB ART indexes can be invalidated when columns are added to a
-- table (see migration 035). The snapshot table's indexes are dropped before
-- the ALTERs and recreated afterward.

DROP INDEX IF EXISTS idx_provider_snap_event;
DROP INDEX IF EXISTS idx_provider_snap_market;
DROP INDEX IF EXISTS idx_provider_snap_status;

ALTER TABLE events.provider_event_snapshots
  ADD COLUMN IF NOT EXISTS segment_id VARCHAR;

ALTER TABLE events.provider_event_snapshots
  ADD COLUMN IF NOT EXISTS genre_id VARCHAR;

ALTER TABLE events.provider_event_snapshots
  ADD COLUMN IF NOT EXISTS subgenre_id VARCHAR;

ALTER TABLE events.provider_event_snapshots
  ADD COLUMN IF NOT EXISTS family VARCHAR;

CREATE INDEX IF NOT EXISTS idx_provider_snap_event
  ON events.provider_event_snapshots (platform_object_id, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_provider_snap_market
  ON events.provider_event_snapshots (country_code, state_code, city, local_date);
CREATE INDEX IF NOT EXISTS idx_provider_snap_status
  ON events.provider_event_snapshots (event_status);
