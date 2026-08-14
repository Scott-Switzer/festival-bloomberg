-- ===========================================================================
-- 015_public_boxscore_research_corpus_v1.sql
-- ===========================================================================
-- Public Boxscore Research Corpus V1.
--
-- A first-class BOXOFFICE_ENGAGEMENT: one reported record from a public
-- box-office source (Billboard Boxscore, Pollstar Hot Tickets, Touring Data,
-- etc.). An engagement may span ONE show or MULTIPLE shows; multi-show
-- aggregates are never divided across nights unless the source itself does.
--
-- RESEARCH CORPUS ONLY: these public sources carry RESEARCH_ONLY /
-- TERMS_REVIEW_REQUIRED rights and never enter the commercial-eligible
-- corpus. observation_class = OBSERVED_PUBLIC (publicly available), but
-- commercial_use_status is deliberately fail-closed.
--
-- Semantic honesty: the "headcount" numerator differs by source.
--   Billboard "Attend/Capacity"   -> REPORTED_ATTENDANCE (unspecified paid vs scanned)
--   Pollstar "Tickets Sold"       -> PAID_TICKETS (per Pollstar reporting policy)
--   Touring Data "(attendance – $gross)" -> REPORTED_ATTENDANCE (reported rows only)
-- headcount_definition records which one a row actually is; a value is never
-- relabeled into a stronger category to inflate coverage.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS research;

CREATE TABLE IF NOT EXISTS research.boxoffice_engagements (
    engagement_id            VARCHAR PRIMARY KEY,
    rank                     INTEGER,
    artist                   VARCHAR NOT NULL,
    venue                    VARCHAR,
    market                   VARCHAR,
    city                     VARCHAR,
    state                    VARCHAR,
    country                  VARCHAR,
    promoter                 VARCHAR,

    start_date               DATE,
    end_date                 DATE,
    dates_raw                VARCHAR,
    number_of_shows          INTEGER,

    headcount_total          DOUBLE,
    headcount_definition     VARCHAR,   -- PAID_TICKETS | REPORTED_ATTENDANCE | UNSPECIFIED
    capacity_total           DOUBLE,
    sellable_capacity_per_show DOUBLE,
    reported_sellouts        INTEGER,
    capacity_tier            VARCHAR,   -- Pollstar capacity-bucket label (metadata only)

    ticket_gross_total       DOUBLE,
    currency                 VARCHAR,
    price_min                DOUBLE,
    price_max                DOUBLE,
    prices_raw               VARCHAR,

    reporting_source         VARCHAR NOT NULL,  -- billboard | pollstar | touring_data | openicpsr | openmuse
    source_url               VARCHAR,
    source_publication_time  TIMESTAMP,
    retrieved_at             TIMESTAMP NOT NULL,

    rights_status            VARCHAR NOT NULL,
    commercial_use_status    VARCHAR NOT NULL,
    observation_class        VARCHAR NOT NULL,

    is_multi_show            BOOLEAN,
    is_reported              BOOLEAN,
    is_estimated             BOOLEAN,

    raw_payload_hash         VARCHAR,
    software_version         VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_boxoffice_source
  ON research.boxoffice_engagements (reporting_source, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_boxoffice_venue
  ON research.boxoffice_engagements (venue, city);
CREATE INDEX IF NOT EXISTS idx_boxoffice_headcount
  ON research.boxoffice_engagements (headcount_definition, is_reported);
