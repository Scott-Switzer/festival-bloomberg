-- ===========================================================================
-- 007_acquisition_evidence_v1.sql
-- ===========================================================================
-- Immutable evidence model for the Festival Signal Fabric.
--
-- Layering:
--   acquisition.acquisition_runs            - one row per provider invocation
--   acquisition.raw_observations           - immutable per-provider observations
--   acquisition.social_observations        - canonical deduplicated objects
--   acquisition.social_engagement_snapshots- timestamped per-provider metrics
--   acquisition.text_inferences            - versioned NLP inference records
--   governance.policy_decisions            - auditable policy gate outcomes
--
-- Every raw observation stores knowledge_time (the earliest defensible
-- timestamp at which the information was knowable) and validity windows.
-- Historical queries MUST filter on knowledge_time <= cutoff and the
-- validity window; this migration only provides the storage, enforcement
-- lives in the repository and feature builder.
--
-- Missing data is NULL. Engagement metrics are never zero-filled.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS acquisition;
CREATE SCHEMA IF NOT EXISTS governance;

-- -- acquisition runs --------------------------------------------------------
CREATE TABLE IF NOT EXISTS acquisition.acquisition_runs (
    run_id                  VARCHAR PRIMARY KEY,
    request_id              VARCHAR NOT NULL,
    provider                VARCHAR NOT NULL,
    provider_endpoint       VARCHAR,
    started_at              TIMESTAMP,
    completed_at            TIMESTAMP,
    status                  VARCHAR NOT NULL,
    record_count            INTEGER,
    cost_usd                DOUBLE,
    latency_ms              INTEGER,
    policy_decision_id      VARCHAR,
    error_category          VARCHAR,
    raw_manifest_hash       VARCHAR,
    metadata_json           JSON
);

CREATE INDEX IF NOT EXISTS idx_acq_runs_request
  ON acquisition.acquisition_runs (request_id);
CREATE INDEX IF NOT EXISTS idx_acq_runs_provider
  ON acquisition.acquisition_runs (provider, started_at);

-- -- raw observations (immutable) -------------------------------------------
CREATE TABLE IF NOT EXISTS acquisition.raw_observations (
    observation_id          VARCHAR PRIMARY KEY,
    run_id                  VARCHAR,
    canonical_observation_id VARCHAR,
    source_platform         VARCHAR NOT NULL,
    provider                VARCHAR NOT NULL,
    provider_endpoint       VARCHAR,
    platform_object_id      VARCHAR,
    parent_object_id        VARCHAR,
    source_url              VARCHAR,
    entity_id               VARCHAR,
    entity_type             VARCHAR,
    event_time              TIMESTAMP,
    published_at            TIMESTAMP,
    source_as_of            TIMESTAMP,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    valid_from              TIMESTAMP,
    valid_to                TIMESTAMP,
    content_hash            VARCHAR NOT NULL,
    raw_payload_hash        VARCHAR,
    raw_payload_location    VARCHAR,
    parser_version          VARCHAR,
    provider_version        VARCHAR,
    evidence_class          VARCHAR NOT NULL,
    license_status          VARCHAR,
    commercial_use_status   VARCHAR,
    policy_decision_id      VARCHAR,
    cost_usd                DOUBLE,
    metadata_json           JSON
);

CREATE INDEX IF NOT EXISTS idx_raw_obs_platform_object
  ON acquisition.raw_observations (source_platform, platform_object_id);
CREATE INDEX IF NOT EXISTS idx_raw_obs_canonical
  ON acquisition.raw_observations (canonical_observation_id);
CREATE INDEX IF NOT EXISTS idx_raw_obs_knowledge_time
  ON acquisition.raw_observations (knowledge_time);

-- -- canonical social observations ------------------------------------------
CREATE TABLE IF NOT EXISTS acquisition.social_observations (
    observation_id          VARCHAR PRIMARY KEY,
    artist_id               VARCHAR,
    platform                VARCHAR NOT NULL,
    platform_object_id      VARCHAR NOT NULL,
    author_public_id        VARCHAR,
    text                    VARCHAR,
    language                VARCHAR,
    published_at            TIMESTAMP,
    parent_object_id        VARCHAR,
    thread_id               VARCHAR,
    media_type              VARCHAR,
    hashtags                JSON,
    mentions                JSON,
    market_id               VARCHAR,
    geographic_confidence   VARCHAR,
    entity_resolution_confidence DOUBLE,
    canonical_url           VARCHAR,
    content_hash            VARCHAR,
    source_count            INTEGER,
    provider_count          INTEGER,
    created_at              TIMESTAMP,
    UNIQUE (platform, platform_object_id)
);

CREATE INDEX IF NOT EXISTS idx_social_obs_artist
  ON acquisition.social_observations (artist_id, platform);
CREATE INDEX IF NOT EXISTS idx_social_obs_platform
  ON acquisition.social_observations (platform, platform_object_id);

-- -- timestamped engagement snapshots (mutable metrics, immutable history) --
CREATE TABLE IF NOT EXISTS acquisition.social_engagement_snapshots (
    social_observation_id   VARCHAR NOT NULL,
    provider                VARCHAR NOT NULL,
    retrieved_at            TIMESTAMP NOT NULL,
    likes                   BIGINT,
    comments                BIGINT,
    shares                  BIGINT,
    reposts                 BIGINT,
    views                   BIGINT,
    follower_count_at_observation BIGINT,
    verified_author         BOOLEAN,
    PRIMARY KEY (social_observation_id, provider, retrieved_at)
);

-- -- versioned NLP inference records ----------------------------------------
CREATE TABLE IF NOT EXISTS acquisition.text_inferences (
    inference_id            VARCHAR PRIMARY KEY,
    observation_id          VARCHAR NOT NULL,
    task                    VARCHAR NOT NULL,
    model_name              VARCHAR NOT NULL,
    model_version           VARCHAR NOT NULL,
    label                   VARCHAR,
    probabilities_json      JSON,
    emotion_json            JSON,
    inference_time          TIMESTAMP NOT NULL,
    knowledge_cutoff        TIMESTAMP,
    input_text_hash         VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_text_inf_observation
  ON acquisition.text_inferences (observation_id, task);
CREATE INDEX IF NOT EXISTS idx_text_inf_model
  ON acquisition.text_inferences (model_name, model_version);

-- -- auditable policy decisions ---------------------------------------------
CREATE TABLE IF NOT EXISTS governance.policy_decisions (
    decision_id             VARCHAR PRIMARY KEY,
    source_platform         VARCHAR,
    commercial_context      VARCHAR,
    decision                VARCHAR NOT NULL,
    rationale               VARCHAR,
    decided_at              TIMESTAMP
);
