-- ===========================================================================
-- 025_live_entertainment_data_fabric_v1.sql
-- ===========================================================================
-- LIVE_ENTERTAINMENT_DATA_FABRIC_V1 — quota-aware acquisition accounting.
--
--   1. terminal.acquisition_partitions — the per-partition manifest for
--      quota-aware provider sweeps (Ticketmaster DMA x date-window first).
--      One row per attempted partition, recording the provider-reported
--      total, how many records came back, how many were persisted, and
--      whether the partition hit the deep-paging/retrieval cap. This is the
--      ONLY honest way to assert ">=95% COMPLETE non-truncated": a row that
--      is truncated or PARTIAL/RATE_LIMITED is NOT a complete partition.
--
--   2. terminal.news_mentions already exists (022); this migration adds an
--      index for provider+publication-time coverage so the NEWS view and
--      per-entity news tape read cheaply.
--
-- Non-negotiable semantics: a partition is never reported COMPLETE when its
-- retrieval was truncated or error-terminated; UNKNOWN counts stay NULL.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. Provider acquisition partition manifest.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS terminal.acquisition_partitions (
    partition_key           VARCHAR PRIMARY KEY,   -- hash(provider, partition_id)
    provider                VARCHAR NOT NULL,
    partition_id            VARCHAR NOT NULL,      -- stable id: market + window
    market_id               VARCHAR,
    classification_name     VARCHAR,
    window_start            VARCHAR,
    window_end              VARCHAR,
    total_expected          INTEGER,               -- provider-reported totalElements (may be NULL)
    records_received        INTEGER NOT NULL DEFAULT 0,
    records_persisted       INTEGER NOT NULL DEFAULT 0,
    truncated               BOOLEAN NOT NULL,      -- TRUE when the retrieval cap was hit
    status                  VARCHAR NOT NULL,      -- COMPLETE|PARTIAL|RATE_LIMITED|ERROR|NOT_CONFIGURED|SKIPPED
    error_category          VARCHAR,
    started_at              TIMESTAMP,
    finished_at             TIMESTAMP,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    software_version        VARCHAR,
    ingested_at             TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_acq_partitions_provider
  ON terminal.acquisition_partitions (provider, status, ingested_at);

-- ---------------------------------------------------------------------------
-- 2. News-mention coverage index (provider + publication time).
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_news_mentions_provider_time
  ON terminal.news_mentions (provider, publication_time DESC);
