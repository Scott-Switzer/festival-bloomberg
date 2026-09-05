"""PUBLIC TICKET MARKET panel: honest missing prices, chronological priors."""

from __future__ import annotations

import duckdb

from festival_bloomberg.terminal.artist_security import _public_ticket_market


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE artists (
          artist_key VARCHAR, name VARCHAR, musicbrainz_id VARCHAR,
          artist_type VARCHAR, area VARCHAR, tier VARCHAR,
          historical_event_count INTEGER, festival_appearance_count INTEGER,
          market_count INTEGER, venues_played INTEGER
        );
        INSERT INTO artists VALUES (
          'mbid::test', 'Test Artist', 'test', 'Person', 'US', 'HOT_1000',
          1, 0, 1, 1
        );
        CREATE TABLE artist_external_ids (artist_key VARCHAR, id_type VARCHAR, source_system VARCHAR, id_value VARCHAR);
        CREATE TABLE attention_observations (artist_key VARCHAR);
        CREATE TABLE artist_peers (subject_key VARCHAR);
        CREATE TABLE artist_markets (artist_key VARCHAR);
        CREATE TABLE event_history (artist_key VARCHAR);
        CREATE TABLE festival_appearances (artist_key VARCHAR);
        CREATE TABLE future_events (artist_key VARCHAR);
        CREATE TABLE ticket_market_observations (
          observation_key VARCHAR PRIMARY KEY,
          artist_key VARCHAR,
          event_key VARCHAR NOT NULL,
          provider_event_id VARCHAR,
          artist_name VARCHAR,
          marketplace VARCHAR NOT NULL,
          venue_name VARCHAR,
          city VARCHAR,
          market_key VARCHAR,
          event_date DATE,
          source_url VARCHAR,
          observed_at TIMESTAMP,
          retrieved_at TIMESTAMP,
          knowledge_time TIMESTAMP,
          currency VARCHAR,
          face_value DOUBLE,
          all_in_price DOUBLE,
          resale_min_price DOUBLE,
          resale_median_price DOUBLE,
          resale_max_price DOUBLE,
          listing_count BIGINT,
          price_basis VARCHAR,
          evidence_status VARCHAR,
          evidence_ref VARCHAR,
          raw_payload_hash VARCHAR,
          rights_status VARCHAR,
          commercial_use_status VARCHAR,
          identity_match_status VARCHAR,
          parser_version VARCHAR,
          wave_label VARCHAR,
          cohort_version VARCHAR
        );
        INSERT INTO ticket_market_observations VALUES
          ('obs1', 'mbid::test', 'event::tm:1', '1', 'Test Artist', 'ticketmaster.com',
           'Venue', 'City', 'city-st', DATE '2026-09-10', 'https://example.com',
           TIMESTAMP '2026-09-01 12:00:00', TIMESTAMP '2026-09-01 12:00:00', TIMESTAMP '2026-09-01 12:00:00',
           'USD', NULL, NULL, NULL, NULL, NULL, NULL,
           'NOT_EXPOSED', 'NOT_EXPOSED', 'hash1', 'hash1', 'TERMS_REVIEW_REQUIRED',
           'PROTOTYPE_ONLY', 'MATCHED', 'v1', 'wave', 'TICKET_MARKET_COHORT_V2_20260905'),
          ('obs2', 'mbid::test', 'event::tm:1', '1', 'Test Artist', 'ticketmaster.com',
           'Venue', 'City', 'city-st', DATE '2026-09-10', 'https://example.com',
           TIMESTAMP '2026-09-05 12:00:00', TIMESTAMP '2026-09-05 12:00:00', TIMESTAMP '2026-09-05 12:00:00',
           'USD', NULL, 45.0, 45.0, NULL, NULL, NULL,
           'PUBLIC_PAGE_JSON_LD_OFFER', 'OBSERVED', 'hash2', 'hash2', 'TERMS_REVIEW_REQUIRED',
           'PROTOTYPE_ONLY', 'MATCHED', 'v1', 'wave', 'TICKET_MARKET_COHORT_V2_20260905');
        """
    )


def test_public_ticket_market_preserves_null_prices_and_chronology():
    conn = duckdb.connect(":memory:")
    _seed(conn)
    panel = _public_ticket_market(conn, "mbid::test")
    assert panel["status"] == "OBSERVED"
    assert panel["label"] == "PUBLIC TICKET MARKET"
    assert "TICKET DEMAND" not in panel["note"]
    assert len(panel["events"]) == 1
    ev = panel["events"][0]
    assert ev["current"]["price"] == 45.0
    assert ev["current"]["price_basis"] == "PUBLIC_PAGE_JSON_LD_OFFER"
    assert len(ev["prior_observations"]) == 1
    prior = ev["prior_observations"][0]
    assert prior["price"] is None  # missing stays missing — never 0
    assert prior["price_basis"] == "NOT_EXPOSED"
    # Chronological: prior earlier than current
    assert str(prior["observed_at"]) < str(ev["current"]["observed_at"])


def test_public_ticket_market_unknown_without_table():
    conn = duckdb.connect(":memory:")
    panel = _public_ticket_market(conn, "mbid::missing")
    assert panel["status"] == "UNKNOWN"
    assert panel["label"] == "PUBLIC TICKET MARKET"
    assert panel["events"] == []

