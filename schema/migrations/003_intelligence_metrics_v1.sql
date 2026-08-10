-- Intelligence metrics v1: Wikimedia attention observations + derived edition metrics.
-- Idempotent upgrades for databases created before this slice.

CREATE TABLE IF NOT EXISTS metrics.artist_attention_observations (
    observation_key         VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    festival_key            VARCHAR,
    edition_key             VARCHAR,
    edition_year            INTEGER,
    source_system           VARCHAR NOT NULL,
    metric_kind             VARCHAR NOT NULL,
    project                 VARCHAR,
    access_method           VARCHAR,
    agent                   VARCHAR,
    article_title           VARCHAR,
    granularity             VARCHAR,
    period_start            DATE,
    period_end              DATE,
    value                   DOUBLE,
    value_sum               DOUBLE,
    value_unit              VARCHAR,
    status                  VARCHAR NOT NULL CHECK (
        status IN ('ok', 'error', 'missing')
    ),
    error_code              VARCHAR,
    error_message           VARCHAR,
    source_url              VARCHAR NOT NULL,
    retrieved_at            TIMESTAMP NOT NULL,
    raw_response_json       JSON,
    provenance_json         JSON,
    metric_version          VARCHAR NOT NULL,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_attention_obs_artist
  ON metrics.artist_attention_observations (artist_key, period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_attention_obs_edition
  ON metrics.artist_attention_observations (edition_key, edition_year);
CREATE INDEX IF NOT EXISTS idx_attention_obs_source
  ON metrics.artist_attention_observations (source_system, status, retrieved_at);

CREATE TABLE IF NOT EXISTS metrics.tour_date_observations (
    observation_key         VARCHAR PRIMARY KEY,
    artist_key              VARCHAR NOT NULL,
    event_date              DATE NOT NULL,
    venue_name              VARCHAR,
    city                    VARCHAR,
    region                  VARCHAR,
    country                 VARCHAR,
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),
    source_system           VARCHAR NOT NULL,
    source_url              VARCHAR,
    retrieved_at            TIMESTAMP,
    status                  VARCHAR NOT NULL CHECK (
        status IN ('ok', 'error', 'missing')
    ),
    error_code              VARCHAR,
    error_message           VARCHAR,
    raw_response_json       JSON,
    metric_version          VARCHAR NOT NULL,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tour_date_artist_date
  ON metrics.tour_date_observations (artist_key, event_date);
CREATE INDEX IF NOT EXISTS idx_tour_date_geo
  ON metrics.tour_date_observations (latitude, longitude);

CREATE TABLE IF NOT EXISTS metrics.ticket_price_observations (
    observation_key         VARCHAR PRIMARY KEY,
    festival_key            VARCHAR,
    edition_key             VARCHAR,
    edition_year            INTEGER,
    market_side             VARCHAR NOT NULL CHECK (
        market_side IN ('primary', 'secondary')
    ),
    price                   DOUBLE,
    currency                VARCHAR,
    tier_name               VARCHAR,
    source_system           VARCHAR NOT NULL,
    source_url              VARCHAR,
    retrieved_at            TIMESTAMP,
    status                  VARCHAR NOT NULL CHECK (
        status IN ('ok', 'error', 'missing')
    ),
    error_code              VARCHAR,
    error_message           VARCHAR,
    raw_response_json       JSON,
    metric_version          VARCHAR NOT NULL,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ticket_price_edition
  ON metrics.ticket_price_observations (edition_key, edition_year, market_side);
CREATE INDEX IF NOT EXISTS idx_ticket_price_festival
  ON metrics.ticket_price_observations (festival_key, market_side);

CREATE TABLE IF NOT EXISTS metrics.edition_analytical_metrics (
    metric_key              VARCHAR PRIMARY KEY,
    festival_key            VARCHAR NOT NULL,
    edition_key             VARCHAR NOT NULL,
    edition_year            INTEGER NOT NULL,
    metric_version          VARCHAR NOT NULL,

    attention_hhi           DOUBLE,
    attention_share_json    JSON,
    attention_artist_count  INTEGER,
    attention_coverage_ratio DOUBLE,
    attention_missing_flag  BOOLEAN,

    billing_arbitrage_score DOUBLE,
    billing_arbitrage_spearman DOUBLE,
    billing_arbitrage_coverage_ratio DOUBLE,
    billing_arbitrage_missing_flag BOOLEAN,

    promoter_shared_inventory_jaccard DOUBLE,
    promoter_comparison_edition_key VARCHAR,
    promoter_comparison_festival_key VARCHAR,
    promoter_comparison_year INTEGER,
    promoter_jaccard_missing_flag BOOLEAN,

    exclusivity_gap_km      DOUBLE,
    exclusivity_conflict_count INTEGER,
    exclusivity_radius_km   DOUBLE,
    exclusivity_window_days INTEGER,
    exclusivity_missing_flag BOOLEAN,

    secondary_spread_abs    DOUBLE,
    secondary_spread_pct    DOUBLE,
    primary_price           DOUBLE,
    secondary_price         DOUBLE,
    primary_currency        VARCHAR,
    secondary_currency      VARCHAR,
    secondary_spread_missing_flag BOOLEAN,

    input_hash              VARCHAR,
    evidence_json           JSON,
    flags_json              JSON,
    computed_at             TIMESTAMP NOT NULL,
    ingested_at             TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_edition_analytical_identity
  ON metrics.edition_analytical_metrics (
    edition_key, metric_version, promoter_comparison_edition_key
  );
CREATE INDEX IF NOT EXISTS idx_edition_analytical_festival_year
  ON metrics.edition_analytical_metrics (festival_key, edition_year);
CREATE INDEX IF NOT EXISTS idx_edition_analytical_version
  ON metrics.edition_analytical_metrics (metric_version, computed_at);
