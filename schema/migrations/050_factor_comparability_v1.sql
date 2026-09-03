-- 050_factor_comparability_v1.sql
-- FACTOR COMPARABILITY V1 — financial-grade delta discipline.
--
-- A percentage change on the artist tape is only meaningful when the two
-- observations were measured the same way. These columns capture the
-- measurement context so deltas can be gated:
--
--   measurement_basis      what the number counts (e.g. TOTAL_LISTENS_IN_WINDOW)
--   measurement_window     the time window the value covers (e.g. 28 days)
--   population_scope       who/what is included (e.g. ALL_LISTENBRAINZ_USERS)
--   geographic_scope       geography of the observation (e.g. GLOBAL, US)
--   methodology_version    how the value was produced (provider method + version)
--   coverage_generation    the immutable collection generation the row belongs to
--
-- Rows without a full context are still served as observations, but they are
-- never used to compute a percentage change: the read path reports
-- NOT_COMPARABLE instead. Unknown rights never default to reviewed.

ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS measurement_basis VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS measurement_window VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS population_scope VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS geographic_scope VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS methodology_version VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS coverage_generation VARCHAR;

-- Backfill context for rows that already carry it in evidence_json so the
-- serving read path can gate on first-class columns going forward.
UPDATE metrics.artist_factor_observations
SET measurement_basis = COALESCE(measurement_basis, CAST(evidence_json ->> 'measurement_basis' AS VARCHAR)),
    measurement_window = COALESCE(measurement_window, CAST(evidence_json ->> 'measurement_window' AS VARCHAR)),
    population_scope = COALESCE(population_scope, CAST(evidence_json ->> 'population_scope' AS VARCHAR)),
    geographic_scope = COALESCE(geographic_scope, CAST(evidence_json ->> 'geographic_scope' AS VARCHAR)),
    methodology_version = COALESCE(methodology_version, CAST(evidence_json ->> 'methodology_version' AS VARCHAR)),
    coverage_generation = COALESCE(coverage_generation, CAST(evidence_json ->> 'coverage_generation' AS VARCHAR))
WHERE measurement_basis IS NULL
   OR measurement_window IS NULL
   OR population_scope IS NULL
   OR geographic_scope IS NULL
   OR methodology_version IS NULL
   OR coverage_generation IS NULL;

CREATE INDEX IF NOT EXISTS idx_artist_factor_tape_comparability
    ON metrics.artist_factor_observations (artist_key, factor_name, measurement_basis, measurement_window);