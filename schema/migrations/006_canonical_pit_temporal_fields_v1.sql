-- ===========================================================================
-- 006_canonical_pit_temporal_fields_v1.sql
-- ===========================================================================
-- Adds point-in-time temporal and provenance fields to the canonical metrics
-- and raw tables so the FestivalRepository write methods match the schema.
--
-- knowledge_time: the earliest defensible timestamp at which the information
-- is evidenced to have been knowable for the decision process. For newly
-- ingested public observations, retrieval time is a valid conservative
-- fallback. If that cannot be established, the write must fail — do not
-- invent an earlier timestamp.
--
-- This migration is idempotent and safe to re-run. Each ALTER is a separate
-- statement so DuckDB's migration runner can execute them individually.
-- ===========================================================================

-- -- metrics.artist_metrics -------------------------------------------------
-- Add PIT temporal/provenance columns. Existing rows get NULL for new fields.

ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_publication_time TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_as_of TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS valid_to TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS knowledge_time TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS calculated_at TIMESTAMP;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_url VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS source_record_id VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS confidence DOUBLE;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS quality_flags JSON;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS license_class VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS commercial_use_status VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS feature_version VARCHAR;
ALTER TABLE metrics.artist_metrics ADD COLUMN IF NOT EXISTS model_version VARCHAR;

CREATE INDEX IF NOT EXISTS idx_artist_metrics_knowledge_time
  ON metrics.artist_metrics (knowledge_time);

-- -- raw.lineup_observations -------------------------------------------------
-- Add PIT temporal/provenance columns.

ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS source_publication_time TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS source_as_of TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS valid_to TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS knowledge_time TIMESTAMP;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS confidence DOUBLE;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS quality_flags JSON;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS license_class VARCHAR;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS commercial_use_status VARCHAR;
ALTER TABLE raw.lineup_observations ADD COLUMN IF NOT EXISTS feature_version VARCHAR;

CREATE INDEX IF NOT EXISTS idx_lineup_obs_knowledge_time
  ON raw.lineup_observations (knowledge_time);
