"""Market 404 + search disambiguation API contracts (offline fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from festival_bloomberg.terminal.mvp_server import MvpTerminalApp


@pytest.fixture()
def app(tmp_path):
    db = tmp_path / "serving.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(
        """
        CREATE TABLE artists (
            artist_key VARCHAR PRIMARY KEY, name VARCHAR, normalized_name VARCHAR,
            musicbrainz_id VARCHAR, tier VARCHAR, artist_type VARCHAR, area VARCHAR,
            historical_event_count INTEGER, market_count INTEGER);
        CREATE TABLE artist_search_terms (
            search_term_key VARCHAR PRIMARY KEY, artist_key VARCHAR NOT NULL,
            term VARCHAR NOT NULL, normalized_term VARCHAR, term_type VARCHAR,
            source_system VARCHAR NOT NULL, source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP, status VARCHAR NOT NULL);
        CREATE TABLE artist_markets (
            row_key VARCHAR PRIMARY KEY, artist_key VARCHAR NOT NULL,
            market_key VARCHAR NOT NULL, observed_shows INTEGER,
            venue_count INTEGER, first_play_date DATE, last_play_date DATE,
            future_events INTEGER, ticket_evidence_count INTEGER,
            source_system VARCHAR NOT NULL, source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP, status VARCHAR NOT NULL,
            explanation VARCHAR NOT NULL);
        """
    )
    conn.execute(
        "INSERT INTO artists VALUES "
        "('mbid::aaa', 'Alice Cooper', 'alice cooper', 'aaa', 'HOT_1000', NULL, NULL, 1450, 57),"
        "('mbid::bbb', 'Alice Cooper', 'alice cooper', 'bbb', 'COVERAGE_25000', NULL, NULL, 8, 4)")
    conn.execute(
        "INSERT INTO artist_search_terms VALUES "
        "('t1', 'mbid::aaa', 'Alice Cooper', 'alice cooper', 'canonical_name', 's', 's', NULL, 'PRESENT'),"
        "('t2', 'mbid::bbb', 'Alice Cooper', 'alice cooper', 'canonical_name', 's', 's', NULL, 'PRESENT')")
    conn.execute(
        "INSERT INTO artist_markets VALUES "
        "('r1', 'mbid::aaa', 'chicago-il', 12, NULL, '2019-05-01', '2024-06-15', 2, NULL,"
        " 'est', 'scope', NULL, 'OBSERVED_SUMMARY', 'x')")
    conn.commit()
    current = tmp_path / "CURRENT.json"
    current.write_text(json.dumps({"generation": "test_v1", "sha256": "x" * 64,
                                   "artifact": "artist_security_terminal_v1"}))
    ws = duckdb.connect(str(tmp_path / "ws.duckdb"))
    yield MvpTerminalApp(conn, ws, db_path=db, current_json_path=current)
    conn.close()
    ws.close()


def _body(res):
    return json.loads(res["body"].decode())


def test_valid_market_returns_200_with_timing(app):
    res = app.dispatch("GET", "/api/market/chicago-il")
    assert res["status"] == 200
    payload = _body(res)
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["first_play_date"] == "2019-05-01"
    assert item["last_play_date"] == "2024-06-15"


def test_nonexistent_market_returns_404_not_synthesized(app):
    res = app.dispatch("GET", "/api/market/nope-not-a-market")
    assert res["status"] == 404
    assert _body(res) == {"error": "not found"}


def test_duplicate_names_return_distinguishing_metadata(app):
    res = app.dispatch("GET", "/api/search", query="q=Alice+Cooper&limit=25")
    assert res["status"] == 200
    hits = _body(res)
    assert len(hits) == 2
    counts = sorted(h["historical_event_count"] for h in hits)
    assert counts == [8, 1450]
    # No invented type/area: NULLs stay NULL, tiers differ.
    assert {h["tier"] for h in hits} == {"HOT_1000", "COVERAGE_25000"}
    assert all(h["artist_type"] is None and h["area"] is None for h in hits)
