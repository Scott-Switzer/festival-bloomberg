-- ===========================================================================
-- 011_market_economics_v1.sql
-- ===========================================================================
-- Venue capacity claims, append-only ticket-market snapshots, and event
-- outcome observations. References the PR #11 event/venue graph.
--
-- Capacity is a CLAIM, not a single venue number.
-- Ticket snapshots are CURRENT observations, not reconstructed history.
-- OFFSALE is not SOLD_OUT. Zero listings is not SOLD_OUT.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS economics;

CREATE TABLE IF NOT EXISTS economics.venue_source_ids (
    mapping_id              VARCHAR PRIMARY KEY,
    canonical_venue_id      VARCHAR NOT NULL,
    venue_name              VARCHAR,
    wikidata_qid            VARCHAR,
    osm_type                VARCHAR,
    osm_id                  VARCHAR,
    ticketmaster_venue_id   VARCHAR,
    setlistfm_venue_id      VARCHAR,
    seatgeek_venue_id       VARCHAR,
    resolution_status       VARCHAR NOT NULL,
    resolution_method       VARCHAR,
    ambiguities_json        JSON,
    knowledge_time          TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS economics.venue_capacity_claims (
    claim_id                VARCHAR PRIMARY KEY,
    canonical_venue_id      VARCHAR NOT NULL,
    capacity_value          DOUBLE,
    capacity_kind           VARCHAR NOT NULL,
    configuration_description VARCHAR,
    effective_from          VARCHAR,
    effective_to            VARCHAR,
    provider                VARCHAR NOT NULL,
    source                  VARCHAR NOT NULL,
    source_url              VARCHAR,
    source_publication_time TIMESTAMP,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    source_observation_id   VARCHAR,
    claim_status            VARCHAR NOT NULL,
    wikidata_qid            VARCHAR,
    wikidata_rank           VARCHAR,
    wikidata_unit           VARCHAR,
    wikidata_qualifiers_json JSON,
    osm_type                VARCHAR,
    osm_id                  VARCHAR,
    osm_tags_json           JSON,
    usage_label             VARCHAR
);

CREATE TABLE IF NOT EXISTS economics.primary_ticket_snapshots (
    snapshot_id             VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR,
    provider                VARCHAR NOT NULL,
    provider_event_id       VARCHAR,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    snapshot_bucket         VARCHAR NOT NULL,
    currency                VARCHAR,
    price_type              VARCHAR,
    minimum_price           DOUBLE,
    maximum_price           DOUBLE,
    fees_included           VARCHAR NOT NULL,
    event_status            VARCHAR,
    public_onsale_start     VARCHAR,
    public_onsale_end       VARCHAR,
    source_url              VARCHAR,
    raw_observation_id      VARCHAR,
    raw_payload_hash        VARCHAR
);

CREATE TABLE IF NOT EXISTS economics.secondary_ticket_snapshots (
    snapshot_id             VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR,
    provider                VARCHAR NOT NULL,
    provider_event_id       VARCHAR,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL,
    snapshot_bucket         VARCHAR NOT NULL,
    currency                VARCHAR,
    listing_count           INTEGER,
    lowest_price            DOUBLE,
    average_price           DOUBLE,
    highest_price           DOUBLE,
    median_price            DOUBLE,
    provider_score          DOUBLE,
    source_url              VARCHAR,
    raw_observation_id      VARCHAR,
    raw_payload_hash        VARCHAR
);

CREATE TABLE IF NOT EXISTS economics.event_outcome_observations (
    outcome_id              VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR NOT NULL,
    event_status            VARCHAR NOT NULL,
    performance_recorded_by_setlistfm BOOLEAN,
    sold_out_status         VARCHAR NOT NULL,
    attendance_value        DOUBLE,
    attendance_source       VARCHAR,
    attendance_context      VARCHAR,
    capacity_utilization    DOUBLE,
    utilization_status      VARCHAR NOT NULL,
    supporting_claim_ids    JSON,
    supporting_observation_ids JSON,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS economics.price_comparisons (
    comparison_id           VARCHAR PRIMARY KEY,
    canonical_event_id      VARCHAR,
    concept                 VARCHAR NOT NULL,
    status                  VARCHAR NOT NULL,
    primary_snapshot_id     VARCHAR,
    secondary_snapshot_id   VARCHAR,
    timestamp_delta_seconds INTEGER,
    currency_consistency    VARCHAR,
    fee_comparability       VARCHAR,
    class_comparability     VARCHAR,
    fx_conversion           VARCHAR,
    retrieved_at            TIMESTAMP NOT NULL,
    knowledge_time          TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_econ_capacity_venue
  ON economics.venue_capacity_claims (canonical_venue_id, knowledge_time);
CREATE INDEX IF NOT EXISTS idx_econ_primary_event
  ON economics.primary_ticket_snapshots (canonical_event_id, knowledge_time);
CREATE INDEX IF NOT EXISTS idx_econ_secondary_event
  ON economics.secondary_ticket_snapshots (canonical_event_id, knowledge_time);
CREATE INDEX IF NOT EXISTS idx_econ_outcome_event
  ON economics.event_outcome_observations (canonical_event_id, knowledge_time);
