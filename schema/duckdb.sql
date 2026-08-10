-- Canonical Festival Bloomberg DuckDB schema.
--
-- This file is the single schema source used by both the TypeScript and Python
-- warehouse clients. Statements are deliberately idempotent so the file can be
-- applied to an empty database or to a database created by the legacy schema.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name VARCHAR NOT NULL,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS observations (
  id VARCHAR PRIMARY KEY,
  source_url VARCHAR NOT NULL,
  canonical_url VARCHAR,
  raw_content VARCHAR,
  normalized_content VARCHAR,
  content_hash VARCHAR,
  dedup_key VARCHAR,
  retrieved_at TIMESTAMP NOT NULL,
  first_seen_at TIMESTAMP,
  last_seen_at TIMESTAMP,
  seen_count INTEGER NOT NULL DEFAULT 1 CHECK (seen_count >= 1),
  winner_key VARCHAR,
  status VARCHAR NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error')),
  kind VARCHAR NOT NULL,
  festival_id VARCHAR,
  edition_id VARCHAR,
  source_domain VARCHAR NOT NULL,
  tier VARCHAR,
  evidence_json VARCHAR,
  payload_json VARCHAR
);

-- Upgrade databases created by the pre-ingestion schema without losing rows.
ALTER TABLE observations ADD COLUMN IF NOT EXISTS canonical_url VARCHAR;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS normalized_content VARCHAR;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS dedup_key VARCHAR;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS seen_count INTEGER DEFAULT 1;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS winner_key VARCHAR;

UPDATE observations SET canonical_url = source_url WHERE canonical_url IS NULL;
UPDATE observations
SET normalized_content = COALESCE(raw_content, payload_json, '')
WHERE normalized_content IS NULL;
UPDATE observations SET first_seen_at = retrieved_at WHERE first_seen_at IS NULL;
UPDATE observations SET last_seen_at = retrieved_at WHERE last_seen_at IS NULL;
UPDATE observations SET seen_count = 1 WHERE seen_count IS NULL OR seen_count < 1;

CREATE UNIQUE INDEX IF NOT EXISTS observations_dedup_key_uq
  ON observations (dedup_key);
CREATE INDEX IF NOT EXISTS observations_festival_idx
  ON observations (festival_id, edition_id, retrieved_at);
CREATE INDEX IF NOT EXISTS observations_canonical_url_idx
  ON observations (canonical_url);
CREATE INDEX IF NOT EXISTS observations_content_hash_idx
  ON observations (content_hash);

CREATE TABLE IF NOT EXISTS lineups (
  id VARCHAR PRIMARY KEY,
  festival_id VARCHAR NOT NULL,
  edition_id VARCHAR NOT NULL,
  raw_artists VARCHAR,
  parsed_artists VARCHAR,
  confidence DOUBLE CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  extracted_at TIMESTAMP,
  source_domain VARCHAR NOT NULL,
  announced_at TIMESTAMP,
  UNIQUE (festival_id, edition_id)
);

CREATE INDEX IF NOT EXISTS lineups_festival_edition_idx
  ON lineups (festival_id, edition_id);

CREATE TABLE IF NOT EXISTS costs (
  id VARCHAR PRIMARY KEY,
  provider VARCHAR NOT NULL,
  endpoint VARCHAR,
  input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
  estimated_cost_usd DOUBLE CHECK (
    estimated_cost_usd IS NULL OR estimated_cost_usd >= 0
  ),
  timestamp TIMESTAMP NOT NULL,
  operation VARCHAR NOT NULL,
  units DOUBLE CHECK (units IS NULL OR units >= 0),
  unit_cost_usd DOUBLE CHECK (unit_cost_usd IS NULL OR unit_cost_usd >= 0),
  currency VARCHAR,
  meta_json VARCHAR
);

CREATE INDEX IF NOT EXISTS costs_timestamp_idx ON costs (timestamp);

CREATE TABLE IF NOT EXISTS telemetry (
  id VARCHAR PRIMARY KEY,
  event_type VARCHAR NOT NULL,
  duration_ms DOUBLE CHECK (duration_ms IS NULL OR duration_ms >= 0),
  status VARCHAR,
  error VARCHAR,
  timestamp TIMESTAMP NOT NULL,
  level VARCHAR,
  domain VARCHAR,
  url VARCHAR,
  tier VARCHAR,
  meta_json VARCHAR
);

CREATE INDEX IF NOT EXISTS telemetry_timestamp_idx ON telemetry (timestamp);
CREATE INDEX IF NOT EXISTS telemetry_event_type_idx ON telemetry (event_type);

CREATE TABLE IF NOT EXISTS sentiment_scores (
  id VARCHAR PRIMARY KEY,
  source_id VARCHAR,
  festival_id VARCHAR,
  text VARCHAR NOT NULL,
  compound DOUBLE NOT NULL,
  pos DOUBLE NOT NULL,
  neu DOUBLE NOT NULL,
  neg DOUBLE NOT NULL,
  label VARCHAR NOT NULL,
  scored_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS sentiment_source_idx
  ON sentiment_scores (source_id, scored_at);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id VARCHAR PRIMARY KEY,
  source VARCHAR NOT NULL,
  idempotency_key VARCHAR NOT NULL,
  request_hash VARCHAR NOT NULL,
  adapter_version VARCHAR NOT NULL,
  status VARCHAR NOT NULL CHECK (
    status IN ('running', 'succeeded', 'partial', 'failed')
  ),
  started_at TIMESTAMP NOT NULL,
  completed_at TIMESTAMP,
  attempted_count INTEGER NOT NULL DEFAULT 0 CHECK (attempted_count >= 0),
  inserted_count INTEGER NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
  duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
  failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
  error_code VARCHAR,
  error_message VARCHAR,
  metadata_json VARCHAR NOT NULL DEFAULT '{}',
  UNIQUE (source, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ingestion_runs_status_idx
  ON ingestion_runs (status, started_at);

CREATE TABLE IF NOT EXISTS ingestion_logs (
  id VARCHAR PRIMARY KEY,
  -- Logical reference maintained by IngestionStore. DuckDB currently rejects
  -- updates to a referenced run row even when its key is unchanged.
  run_id VARCHAR NOT NULL,
  source VARCHAR NOT NULL,
  source_record_id VARCHAR NOT NULL,
  input_hash VARCHAR NOT NULL,
  status VARCHAR NOT NULL CHECK (
    status IN ('inserted', 'duplicate', 'failed', 'skipped')
  ),
  observation_id VARCHAR,
  canonical_url VARCHAR,
  content_hash VARCHAR,
  duplicate_of VARCHAR,
  error_code VARCHAR,
  error_message VARCHAR,
  metadata_json VARCHAR NOT NULL DEFAULT '{}',
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  UNIQUE (run_id, source_record_id)
);

CREATE INDEX IF NOT EXISTS ingestion_logs_run_idx
  ON ingestion_logs (run_id, source_record_id);
CREATE INDEX IF NOT EXISTS ingestion_logs_status_idx
  ON ingestion_logs (status, updated_at);
CREATE INDEX IF NOT EXISTS ingestion_logs_observation_idx
  ON ingestion_logs (observation_id);

INSERT INTO schema_migrations (version, name)
VALUES (1, 'canonical_ingestion_dedup_v1')
ON CONFLICT (version) DO NOTHING;
