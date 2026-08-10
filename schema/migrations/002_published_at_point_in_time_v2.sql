-- Point-in-time correctness: distinguish source publication time from retrieval.
ALTER TABLE observations ADD COLUMN IF NOT EXISTS published_at TIMESTAMP;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS published_at_precision VARCHAR;

CREATE INDEX IF NOT EXISTS observations_festival_effective_idx
  ON observations (festival_id, edition_id, published_at, retrieved_at);
