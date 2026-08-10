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
