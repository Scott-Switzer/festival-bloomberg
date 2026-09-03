-- 049_artist_intelligence_tape_v1.sql
-- ARTIST_INTELLIGENCE_TAPE_V1 — temporal factor and sentiment observations.
--
-- Existing artist factor rows remain valid. The alias columns below make the
-- factor tape contract explicit without rewriting historical measurements.
-- New snapshots must use a new generation and never update an old observation.

ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS platform VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS unit VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS observation_time TIMESTAMP;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS knowledge_time TIMESTAMP;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS source VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS evidence_ref VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS source_scope VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS quality_status VARCHAR;
ALTER TABLE metrics.artist_factor_observations
    ADD COLUMN IF NOT EXISTS generation VARCHAR;

-- Complete metadata aliases for rows created by migrations 043-048. This does
-- not change the observed value or its historical key.
UPDATE metrics.artist_factor_observations
SET platform = COALESCE(platform, source_system),
    unit = COALESCE(unit, value_unit),
    observation_time = COALESCE(observation_time, CAST(as_of AS TIMESTAMP)),
    knowledge_time = COALESCE(knowledge_time, available_at, retrieved_at),
    source = COALESCE(source, source_system),
    evidence_ref = COALESCE(evidence_ref, source_url),
    source_scope = COALESCE(source_scope, 'LEGACY_ARTIST_SECURITY_FACTOR'),
    quality_status = COALESCE(quality_status, CASE
        WHEN value IS NULL THEN 'UNKNOWN'
        ELSE 'OBSERVED'
    END),
    generation = COALESCE(generation, source_version)
WHERE platform IS NULL
   OR unit IS NULL
   OR observation_time IS NULL
   OR knowledge_time IS NULL
   OR source IS NULL
   OR evidence_ref IS NULL
   OR source_scope IS NULL
   OR quality_status IS NULL
   OR generation IS NULL;

CREATE INDEX IF NOT EXISTS idx_artist_factor_tape_lookup
    ON metrics.artist_factor_observations (artist_key, factor_name, observation_time);
CREATE INDEX IF NOT EXISTS idx_artist_factor_tape_generation
    ON metrics.artist_factor_observations (generation, source);

-- Daily aggregate grain. No raw usernames, user IDs, post IDs, or comment
-- text are stored here. Those remain in the evidence/ingestion boundary.
CREATE TABLE IF NOT EXISTS metrics.artist_sentiment_observations (
    observation_key                VARCHAR PRIMARY KEY,
    artist_key                     VARCHAR NOT NULL,
    platform                       VARCHAR NOT NULL,
    "date"                         DATE NOT NULL,
    mention_count                  BIGINT NOT NULL,
    analyzed_count                 BIGINT NOT NULL,
    positive_share                 DOUBLE,
    neutral_share                  DOUBLE,
    negative_share                 DOUBLE,
    sentiment_mean                 DOUBLE,
    engagement_weighted_sentiment  DOUBLE,
    engagement_total               BIGINT,
    topic_distribution              JSON,
    language_distribution           JSON,
    sample_quality                 VARCHAR NOT NULL,
    source_generation               VARCHAR NOT NULL,
    model_name                     VARCHAR NOT NULL,
    model_version                  VARCHAR NOT NULL,
    deduplicated_count             BIGINT,
    spam_filtered_count            BIGINT,
    source                         VARCHAR NOT NULL,
    evidence_ref                   VARCHAR,
    source_scope                   VARCHAR NOT NULL,
    rights_status                  VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status          VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    quality_status                 VARCHAR NOT NULL DEFAULT 'OBSERVED',
    retrieved_at                   TIMESTAMP NOT NULL,
    knowledge_time                 TIMESTAMP,
    ingested_at                    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (mention_count >= 0),
    CHECK (analyzed_count >= 0 AND analyzed_count <= mention_count),
    CHECK (positive_share IS NULL OR (positive_share >= 0 AND positive_share <= 1)),
    CHECK (neutral_share IS NULL OR (neutral_share >= 0 AND neutral_share <= 1)),
    CHECK (negative_share IS NULL OR (negative_share >= 0 AND negative_share <= 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_artist_sentiment_daily_generation
    ON metrics.artist_sentiment_observations (artist_key, platform, "date", source_generation);
CREATE INDEX IF NOT EXISTS idx_artist_sentiment_artist_date
    ON metrics.artist_sentiment_observations (artist_key, "date");
CREATE INDEX IF NOT EXISTS idx_artist_sentiment_platform_date
    ON metrics.artist_sentiment_observations (platform, "date");
