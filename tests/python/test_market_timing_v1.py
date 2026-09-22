"""Market-timing materialization contracts (offline, fixture DBs).

Covers the serving-build timing fill shared by the snapshot and R2-parquet
paths: area→market mapping, first/last play derivation, future-event
separation, PIT/as-of cutoff, UNKNOWN preservation, and no-invented-links.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from build_talent_buyer_terminal_v1 import (
    create_area_market_map,
    fill_market_futures,
    fill_market_timing_from_evidence,
)

ESTATE_EXPL = "Estate provides market/show summary only."


@pytest.fixture()
def conn():
    c = duckdb.connect(":memory:")
    c.execute(
        """
        CREATE TABLE artist_markets (
            row_key VARCHAR PRIMARY KEY, artist_key VARCHAR NOT NULL,
            market_key VARCHAR NOT NULL, observed_shows INTEGER,
            venue_count INTEGER, first_play_date DATE, last_play_date DATE,
            future_events INTEGER, ticket_evidence_count INTEGER,
            source_system VARCHAR NOT NULL, source_scope VARCHAR NOT NULL,
            knowledge_time TIMESTAMP, status VARCHAR NOT NULL,
            explanation VARCHAR NOT NULL);
        CREATE TABLE future_events (
            future_event_key VARCHAR PRIMARY KEY, artist_key VARCHAR NOT NULL,
            event_date DATE, city VARCHAR, venue_name VARCHAR);
        """
    )
    rows = [
        ("a|x|t", "a", "chicago-il", 5, None, None, None, None, None,
         "est", "scope", None, "OBSERVED_SUMMARY", ESTATE_EXPL),
        ("a|y|t", "a", "london-uk", 2, None, None, None, None, None,
         "est", "scope", None, "OBSERVED_SUMMARY", ESTATE_EXPL),
        ("b|x|t", "b", "austin-tx", 1, None, None, None, None, None,
         "est", "scope", None, "OBSERVED_SUMMARY", ESTATE_EXPL),
    ]
    c.executemany("INSERT INTO artist_markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    yield c
    c.close()


def _ev(conn, rows):
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE ev (artist_key VARCHAR, market_key VARCHAR, event_date DATE)")
    conn.executemany("INSERT INTO ev VALUES (?,?,?)", rows)


def test_area_map_resolves_cities_states_and_unicode(conn):
    assert create_area_market_map(conn) > 50
    got = dict(conn.execute(
        "SELECT area_norm, market_key FROM area_market_map WHERE area_norm IN "
        "('chicago', 'london', 'hollywood', 'münchen', 'illinois', 'xx')").fetchall())
    assert got["chicago"] == "chicago-il"
    assert got["london"] == "london-uk"
    assert got["hollywood"] == "los-angeles-ca"  # city wins over state fallback
    assert got["münchen"] == "munich-de"
    assert got["illinois"] == "chicago-il"  # state-name fallback
    assert "xx" not in got


def test_first_last_play_derived_and_deduped(conn):
    _ev(conn, [
        ("a", "chicago-il", "2020-05-01"),
        ("a", "chicago-il", "2020-05-01"),  # same-day dupe
        ("a", "chicago-il", "2024-03-05"),
        ("a", "chicago-il", "2019-01-10"),
    ])
    out = fill_market_timing_from_evidence(
        conn, "SELECT artist_key, market_key, event_date FROM ev", "2026-08-28")
    assert out["rows_updated"] == 1
    row = conn.execute(
        "SELECT first_play_date, last_play_date, observed_shows FROM artist_markets "
        "WHERE artist_key='a' AND market_key='chicago-il'").fetchone()
    assert str(row[0]) == "2019-01-10" and str(row[1]) == "2024-03-05"
    assert row[2] == 5  # estate count untouched
    expl = conn.execute(
        "SELECT explanation FROM artist_markets WHERE artist_key='a' AND market_key='chicago-il'").fetchone()[0]
    assert "derived from dated event evidence" in expl


def test_future_dates_never_become_last_play(conn):
    _ev(conn, [
        ("a", "london-uk", "2030-06-01"),  # after as_of
        ("b", "austin-tx", "2030-01-01"),  # future-only link
    ])
    fill_market_timing_from_evidence(
        conn, "SELECT artist_key, market_key, event_date FROM ev", "2026-08-28")
    assert conn.execute(
        "SELECT last_play_date FROM artist_markets WHERE artist_key='a' AND market_key='london-uk'").fetchone()[0] is None
    row = conn.execute(
        "SELECT first_play_date, last_play_date FROM artist_markets WHERE artist_key='b'").fetchone()
    assert row == (None, None)  # UNKNOWN, never zeroed or future-filled


def test_no_invented_links_and_unknown_preserved(conn):
    _ev(conn, [("zzz", "nowhere-xx", "2020-01-01")])
    out = fill_market_timing_from_evidence(
        conn, "SELECT artist_key, market_key, event_date FROM ev", "2026-08-28")
    assert out["rows_updated"] == 0
    assert conn.execute("SELECT COUNT(*) FROM artist_markets").fetchone()[0] == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM artist_markets WHERE first_play_date IS NULL").fetchone()[0] == 3


def test_future_events_separated_by_cutoff_and_market(conn):
    conn.executemany("INSERT INTO future_events VALUES (?,?,?,?,?)", [
        ("f1", "a", "2027-06-15", "Chicago", "United Center"),
        ("f2", "a", "2027-06-16", "Chicago", "United Center"),
        ("f3", "a", "2020-01-01", "Chicago", "Metro"),  # past: not future
        ("f4", "a", "2027-01-01", "Nowhere", "Barn"),  # unmapped: ignored
        ("f5", "b", "2027-02-01", "Austin", "Stubbs"),
    ])
    out = fill_market_futures(conn, "2026-08-28")
    assert out["rows_updated"] == 2
    assert conn.execute(
        "SELECT future_events FROM artist_markets WHERE artist_key='a' AND market_key='chicago-il'").fetchone()[0] == 2
    assert conn.execute(
        "SELECT future_events FROM artist_markets WHERE artist_key='b'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT future_events FROM artist_markets WHERE artist_key='a' AND market_key='london-uk'").fetchone()[0] is None
