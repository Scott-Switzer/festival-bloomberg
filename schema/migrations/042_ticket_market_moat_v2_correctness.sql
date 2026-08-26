-- Migration 042: TICKET_MARKET_MOAT_V2 — append-only listing history + lifecycle semantics
--
-- Review corrections (PR #46):
--   1. marketplace_listing_observations — the IMMUTABLE listing history. One row
--      per observed listing per capture, never updated or deleted. Historical
--      truth lives here; marketplace_listings is only a CURRENT-STATE cache.
--      No 50-entry truncation anywhere.
--   2. first_missing_at / disappeared_at on marketplace_listings — the first
--      observation where a listing was NOT seen must never overwrite
--      last_seen_at (which stays the last observation CONTAINING the listing).
--   3. Lifecycle transitions: LISTING_APPEARED, LISTING_PRICE_CHANGED,
--      LISTING_QUANTITY_CHANGED, LISTING_DISAPPEARED, LISTING_REAPPEARED.
--      A repeated unchanged listing never resets to LISTING_APPEARED.
--      Disappearance is NOT a sale (withdrawal/repricing/transfer all possible).

CREATE TABLE IF NOT EXISTS acquisition.marketplace_listing_observations (
    listing_observation_id   VARCHAR PRIMARY KEY,
    event_key                VARCHAR NOT NULL,
    marketplace              VARCHAR NOT NULL,      -- seatgeek.com, vividseats.com, ...
    provider_listing_id      VARCHAR,               -- source listing id ("" if not exposed)
    listing_key              VARCHAR NOT NULL,      -- stable key (provider id or synthetic)
    observed_at              TIMESTAMP NOT NULL,
    inventory_type           VARCHAR,
    section                  VARCHAR,
    row_label                VARCHAR,
    seats                    VARCHAR,
    quantity                 INTEGER,
    ticket_price             DOUBLE,                -- per-ticket face price
    fee                      DOUBLE,                -- per-ticket fee
    all_in_price             DOUBLE,                -- per-ticket all-in (= totalPrice)
    currency                 VARCHAR,
    status                   VARCHAR NOT NULL DEFAULT 'OBSERVED',  -- OBSERVED | DISAPPEARED
    source_snapshot_id       VARCHAR,               -- link to acquisition.ticket_market_snapshots
    raw_payload_hash         VARCHAR,
    rights_status            VARCHAR NOT NULL DEFAULT 'TERMS_REVIEW_REQUIRED',
    commercial_use_status    VARCHAR NOT NULL DEFAULT 'PROTOTYPE_ONLY',
    ingested_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_listing_obs_event
    ON acquisition.marketplace_listing_observations(event_key, marketplace, observed_at);
CREATE INDEX IF NOT EXISTS idx_listing_obs_key
    ON acquisition.marketplace_listing_observations(listing_key, observed_at);

-- Lifecycle semantics on the current-state cache: preserve last_seen_at
-- (last observation CONTAINING the listing) and record when it went missing.
ALTER TABLE acquisition.marketplace_listings ADD COLUMN IF NOT EXISTS first_missing_at TIMESTAMP;
ALTER TABLE acquisition.marketplace_listings ADD COLUMN IF NOT EXISTS disappeared_at TIMESTAMP;
