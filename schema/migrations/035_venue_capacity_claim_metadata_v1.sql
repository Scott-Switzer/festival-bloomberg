-- VENUE_CONFIGURATION_CAPACITY_V2
-- Preserve the complete original source field value and the parser that
-- produced the claim. These are additive metadata columns; they never change
-- the semantics of existing claims.
--
-- NOTE: DuckDB ART indexes can be invalidated when columns are added to a
-- table. The existing index on (canonical_venue_id, knowledge_time) is dropped
-- before the ALTER and recreated afterward so deletes still work.

DROP INDEX IF EXISTS idx_econ_capacity_venue;

ALTER TABLE economics.venue_capacity_claims
  ADD COLUMN IF NOT EXISTS raw_value VARCHAR;

ALTER TABLE economics.venue_capacity_claims
  ADD COLUMN IF NOT EXISTS parser_version VARCHAR;

CREATE INDEX IF NOT EXISTS idx_econ_capacity_venue
  ON economics.venue_capacity_claims (canonical_venue_id, knowledge_time);