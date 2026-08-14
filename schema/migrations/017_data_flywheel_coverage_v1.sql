-- ===========================================================================
-- 017_data_flywheel_coverage_v1.sql
-- ===========================================================================
-- Data Flywheel & Coverage V1.
--
-- Transforms Festival Intelligence from a 657-engagement research corpus into
-- a continuously growing live-event research warehouse. Four simultaneous
-- pipelines share this layer:
--
--   EVENT_GRAPH       -> flywheel.event_graph_identities (MusicBrainz identity
--                        backbone) + flywheel.source_registry
--   OUTCOME_HUNTER    -> flywheel.outcome_hunt_plans / _tasks, writing claims
--                        into economics.event_outcome_claims (migration 013)
--   CONTEXT_PANEL     -> flywheel.context_panel_series (attention, market,
--                        weather; PIT-vintaged, never backdated)
--   FORWARD_WATCH     -> flywheel.forward_watch_events / _observations
--                        (time-sensitive evidence that can never be
--                        reconstructed later)
--
-- Acquisition is measured, not counted: flywheel.objectives holds the
-- medium-term coverage targets; flywheel.coverage_snapshots appends the
-- actual-vs-target measurement on every run. Every row keeps the canonical
-- PIT evidence columns (event_time / source_publication_time / retrieved_at /
-- knowledge_time / rights / raw hash / parser version).
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS flywheel;

-- ---------------------------------------------------------------------------
-- 1. Source registry — every provider/source, its rights, quota, and what
--    decision coverage it improves. The acquisition metric is "how much does
--    this source improve decision coverage", not "how many rows did we get".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.source_registry (
    source_id               VARCHAR PRIMARY KEY,
    source_name             VARCHAR NOT NULL,
    source_kind             VARCHAR NOT NULL,   -- EVENT_GRAPH | OUTCOME | CONTEXT | FORWARD | PRIVATE | IDENTITY
    pipeline                VARCHAR NOT NULL,   -- EVENT_GRAPH | OUTCOME_HUNTER | CONTEXT_PANEL | FORWARD_WATCH | BACKTEST
    provider                VARCHAR,
    access_status           VARCHAR NOT NULL,   -- AVAILABLE | KEY_REQUIRED | TERMS_REVIEW | REGISTRATION_REQUIRED | NOT_AVAILABLE | PARTNER_GATED
    documented_quota        VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    license                 VARCHAR,
    coverage_contribution   VARCHAR,
    notes                   VARCHAR,
    registered_at           TIMESTAMP NOT NULL,
    updated_at              TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- 2. Medium-term coverage objectives (the memo's product-development targets).
--    Versioned so later revisions never rewrite history.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.objectives (
    objective_key           VARCHAR PRIMARY KEY,
    objective_version       VARCHAR NOT NULL,
    metric_name             VARCHAR NOT NULL,
    metric_definition       VARCHAR NOT NULL,
    medium_term_target      DOUBLE,
    unit                    VARCHAR,
    registered_at           TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------------------
-- 3. Append-only coverage snapshots: actual vs target per objective.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.coverage_snapshots (
    snapshot_id             VARCHAR PRIMARY KEY,
    objective_version       VARCHAR NOT NULL,
    measured_at             TIMESTAMP NOT NULL,
    objective_key           VARCHAR NOT NULL,
    metric_name             VARCHAR NOT NULL,
    actual_value            DOUBLE,
    target_value            DOUBLE,
    coverage_ratio          DOUBLE,
    unit                    VARCHAR,
    status                  VARCHAR NOT NULL,   -- BELOW_TARGET | AT_TARGET | ABOVE_TARGET
    delta                   DOUBLE,
    evidence_query          VARCHAR,
    notes                   VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_coverage_snapshot_time
  ON flywheel.coverage_snapshots (measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_coverage_snapshot_objective
  ON flywheel.coverage_snapshots (objective_key, measured_at);

-- ---------------------------------------------------------------------------
-- 4. Event-graph identities — MusicBrainz identity backbone. One row per
--    resolved entity; the same entity may carry several provider identity
--    rows (each with its own evidence + license).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.event_graph_identities (
    identity_id             VARCHAR PRIMARY KEY,
    entity_type             VARCHAR NOT NULL,   -- ARTIST | VENUE | FESTIVAL
    entity_key              VARCHAR,
    entity_name             VARCHAR NOT NULL,
    normalized_name         VARCHAR NOT NULL,
    musicbrainz_id          VARCHAR,
    musicbrainz_name        VARCHAR,
    musicbrainz_type        VARCHAR,
    musicbrainz_country     VARCHAR,
    wikidata_id             VARCHAR,
    ticketmaster_id         VARCHAR,
    resolution_method       VARCHAR NOT NULL,   -- EXACT_MBID | NORMALIZED_NAME_MATCH | FUZZY_MATCH | MANUAL | UNRESOLVED
    match_confidence        DOUBLE,
    source_provider         VARCHAR NOT NULL,   -- musicbrainz | ticketmaster | jambase | manual
    source_url              VARCHAR,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    license                 VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    raw_payload_hash        VARCHAR,
    parser_version          VARCHAR,
    software_version        VARCHAR,

    CHECK (match_confidence IS NULL
           OR (match_confidence >= 0.0 AND match_confidence <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_graph_identities_entity
  ON flywheel.event_graph_identities (entity_type, entity_name);
CREATE INDEX IF NOT EXISTS idx_graph_identities_mbid
  ON flywheel.event_graph_identities (musicbrainz_id);
CREATE INDEX IF NOT EXISTS idx_graph_identities_knowledge
  ON flywheel.event_graph_identities (knowledge_time);

-- ---------------------------------------------------------------------------
-- 5. Forward watchlist — every discovered future event enters the watch. The
--    milestone ladder (DISCOVERED -> ANNOUNCEMENT -> PRESALE -> ONSALE -> D+1
--    ... -> T-1 -> SHOW -> SETTLEMENT) preserves observations that can never
--    be reconstructed later.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.forward_watch_events (
    watch_event_id          VARCHAR PRIMARY KEY,
    provider                VARCHAR NOT NULL,
    provider_event_id       VARCHAR NOT NULL,
    artist_name             VARCHAR,
    venue_name              VARCHAR,
    market                  VARCHAR,
    event_date              DATE,
    event_time              TIMESTAMP,
    event_status            VARCHAR,
    first_seen_at           TIMESTAMP NOT NULL,
    tracking_started_at     TIMESTAMP NOT NULL,
    tracking_status         VARCHAR NOT NULL,   -- TRACKING | SETTLED | CANCELLED | DROPPED
    knowledge_time          TIMESTAMP NOT NULL,
    source_url              VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    observation_class       VARCHAR NOT NULL,
    software_version        VARCHAR,
    UNIQUE (provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_forward_watch_date
  ON flywheel.forward_watch_events (event_date, tracking_status);
CREATE INDEX IF NOT EXISTS idx_forward_watch_provider
  ON flywheel.forward_watch_events (provider, provider_event_id);

CREATE TABLE IF NOT EXISTS flywheel.forward_watch_observations (
    observation_id          VARCHAR PRIMARY KEY,
    watch_event_id          VARCHAR NOT NULL,
    observed_at             TIMESTAMP NOT NULL,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    milestone               VARCHAR,            -- DISCOVERED | ANNOUNCEMENT | PRESALE | ONSALE | D+1 | D+3 | D+7 | D+14 | WEEKLY | T-30 | T-14 | T-7 | T-3 | T-1 | SHOW | SETTLEMENT
    event_status            VARCHAR,
    price_min               DOUBLE,
    price_max               DOUBLE,
    currency                VARCHAR,
    ticket_classes          JSON,
    listing_count           INTEGER,
    secondary_lowest_price  DOUBLE,
    secondary_median_price  DOUBLE,
    inventory_available     DOUBLE,
    inventory_change_since_last DOUBLE,
    venue_configuration     VARCHAR,
    source_provider         VARCHAR,
    source_url              VARCHAR,
    raw_payload_hash        VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    observation_class       VARCHAR NOT NULL,
    software_version        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_forward_watch_obs_event
  ON flywheel.forward_watch_observations (watch_event_id, knowledge_time);

-- ---------------------------------------------------------------------------
-- 6. OUTCOME_HUNTER plan/task ledger. A plan is created per known engagement;
--    tasks hunt the memo's target fields. Claims land in
--    economics.event_outcome_claims (append-only, conflicts coexist).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.outcome_hunt_plans (
    plan_id                 VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR NOT NULL,
    artist_name             VARCHAR,
    venue_name              VARCHAR,
    market                  VARCHAR,
    event_date              DATE,
    status                  VARCHAR NOT NULL,   -- PLANNED | IN_PROGRESS | COMPLETE | BLOCKED
    target_fields           JSON NOT NULL,
    created_at              TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    software_version        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_hunt_plan_event
  ON flywheel.outcome_hunt_plans (canonical_event_id);

CREATE TABLE IF NOT EXISTS flywheel.outcome_hunt_tasks (
    task_id                 VARCHAR PRIMARY KEY,
    plan_id                 VARCHAR NOT NULL,
    target_field            VARCHAR NOT NULL,   -- attendance | paid_tickets | gross | sellout | capacity | ticket_price | promoter | tour | announcement | onsale | show_count
    outcome_type            VARCHAR,            -- economics.event_outcome_claims taxonomy when claimed
    status                  VARCHAR NOT NULL,   -- PENDING | SEARCHING | CLAIM_FOUND | NOT_FOUND | BLOCKED
    claim_id                VARCHAR,
    source_provider         VARCHAR,
    source_url              VARCHAR,
    retrieved_at            TIMESTAMP,
    knowledge_time          TIMESTAMP,
    notes                   VARCHAR,
    UNIQUE (plan_id, target_field)
);

CREATE INDEX IF NOT EXISTS idx_hunt_task_status
  ON flywheel.outcome_hunt_tasks (status, plan_id);

-- ---------------------------------------------------------------------------
-- 7. Context panel — PIT series rows (attention, market, weather). A row is
--    only usable at cutoff T if knowledge_time <= T. Vintages are stored so a
--    2022 booking model never receives a 2026-revised statistic.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flywheel.context_panel_series (
    series_id               VARCHAR PRIMARY KEY,
    entity_type             VARCHAR NOT NULL,   -- ARTIST | MARKET | VENUE | EVENT
    entity_key              VARCHAR,
    entity_name             VARCHAR,
    series_type             VARCHAR NOT NULL,   -- ATTENTION_PAGEVIEWS | MARKET_CENSUS | MARKET_LABOR | MARKET_INCOME | WEATHER_HISTORICAL | NEWS_COUNT
    provider                VARCHAR NOT NULL,   -- wikimedia | census | bls | bea | noaa | era5 | gdelt
    observed_date           DATE NOT NULL,
    value                   DOUBLE,
    unit                    VARCHAR,
    metric_name             VARCHAR,
    vintage                 VARCHAR,            -- e.g. "ACS 5-Year 2018-2022" / BLS series vintage
    source_publication_time TIMESTAMP,
    source_as_of            TIMESTAMP,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    source_url              VARCHAR,
    raw_payload_hash        VARCHAR,
    license                 VARCHAR,
    rights_status           VARCHAR NOT NULL,
    commercial_use_status   VARCHAR NOT NULL,
    parser_version          VARCHAR,
    software_version        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_context_panel_lookup
  ON flywheel.context_panel_series (entity_type, entity_name, series_type, observed_date);
CREATE INDEX IF NOT EXISTS idx_context_panel_knowledge
  ON flywheel.context_panel_series (knowledge_time);
