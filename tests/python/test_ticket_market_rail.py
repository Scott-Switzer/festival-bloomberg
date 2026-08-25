"""Tests for the REAL_TICKET_MARKET_RAIL_V1 evidence rail.

Covers normalization, event resolution, snapshot persistence, change
detection, and the buyer-view ticket-market section. No network calls —
fixtures only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from festival_bloomberg.evidence_rails.ticket_market import (
    MARKET_SOURCES,
    build_source_input,
    normalize_market_record,
    resolve_to_universe,
    persist_snapshot,
    record_source_health,
    load_universe,
)
from festival_bloomberg.evidence_rails.contract import (
    ingest_observation,
    detect_changes,
    ObservationRecord,
)
from festival_bloomberg.migrations import apply_pending_migrations


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture()
def conn():
    c = duckdb.connect(":memory:")
    apply_pending_migrations(c)
    yield c
    c.close()


def _seatgeek_record():
    return {
        "event_id": 18515703,
        "title": "Weatherday at Bottom Lounge",
        "url": "https://seatgeek.com/weatherday-tickets/2026-11-06/18515703",
        "datetime_local": "2026-11-06T19:30:00",
        "currency": "USD",
        "lowest_price": 37,
        "median_price": 38,
        "average_price": 38.5,
        "highest_price": 41,
        "listing_count": 3,
        "ticket_count": 8,
        "status": "normal",
        "primary_performer": "Weatherday",
        "venue_name": "Bottom Lounge",
        "venue_city": "Chicago",
        "venue_state": "IL",
        "scrapedAt": "2026-08-25T07:06:05",
    }


def _vivid_record():
    return {
        "id": 999001,
        "name": "Weatherday",
        "localDate": "2026-11-06T19:00:00",
        "venue": {"name": "Bottom Lounge", "city": "Chicago", "state": "IL"},
        "minPrice": 40.0,
        "maxPrice": 60.0,
        "avgPrice": 48.0,
        "medianPrice": 47.0,
        "listingCount": 5,
        "ticketCount": 12,
        "soldOut": False,
        "currency": "USD",
    }


def _universe_events():
    return [
        {
            "watch_universe_version": "watch_universe_v1",
            "event_key": "event::tm:test1",
            "artist_name": "Weatherday",
            "venue_name": "Bottom Lounge",
            "city": "Chicago",
            "state": "IL",
            "event_date": "2026-11-06",
            "market_key": "chicago-il",
        },
        {
            "watch_universe_version": "watch_universe_v1",
            "event_key": "event::tm:test2",
            "artist_name": "Jodeci",
            "venue_name": "Arie Crown Theater",
            "city": "Chicago",
            "state": "IL",
            "event_date": "2026-11-07",
            "market_key": "chicago-il",
        },
    ]


# ── Normalization ──────────────────────────────────────────────────────

def test_normalize_seatgeek_fields():
    n = normalize_market_record(_seatgeek_record(), "seatgeek")
    assert n["source_record_id"] == "18515703"
    assert n["resale_min_price"] == 37.0
    assert n["resale_median_price"] == 38.0
    assert n["resale_avg_price"] == 38.5
    assert n["resale_max_price"] == 41.0
    assert n["listing_count"] == 3
    assert n["ticket_count"] == 8
    assert n["currency"] == "USD"
    assert n["artist_name"] == "Weatherday"
    assert n["event_date"] == "2026-11-06"


def test_normalize_vivid_fields():
    n = normalize_market_record(_vivid_record(), "vividseats")
    assert n["source_record_id"] == "999001"
    assert n["resale_min_price"] == 40.0
    assert n["resale_median_price"] == 47.0
    assert n["resale_max_price"] == 60.0
    assert n["listing_count"] == 5
    assert n["ticket_count"] == 12
    assert n["venue_name"] == "Bottom Lounge"


def test_normalize_unknown_source_is_safe():
    n = normalize_market_record({}, "unknown_platform")
    assert n["source_record_id"] == ""


# ── Input builders ─────────────────────────────────────────────────────

def test_build_source_input_seatgeek_is_bounded():
    inp = build_source_input(
        "seatgeek", artist_name="Jodeci", city="Chicago", state="IL",
        event_date="2026-11-07", max_items=5,
    )
    assert inp["searchQuery"] == "Jodeci"
    assert inp["city"] == "Chicago"
    assert inp["dateFrom"] == inp["dateTo"] == "2026-11-07"
    assert inp["maxItems"] == 5


def test_build_source_input_gametime_is_url_only():
    inp = build_source_input(
        "gametime", artist_name="Jodeci", city="Chicago", state="IL",
        event_date="2026-11-07", max_items=5,
    )
    assert inp["startUrls"] == []


# ── Event resolution ───────────────────────────────────────────────────

def test_resolve_matched_artist_venue_date():
    n = normalize_market_record(_seatgeek_record(), "seatgeek")
    status, key, conf = resolve_to_universe(n, _universe_events())
    assert status == "MATCHED"
    assert key == "event::tm:test1"
    assert conf >= 0.7


def test_resolve_ambiguous_artist_only():
    n = {
        "artist_name": "Weatherday",
        "venue_name": None,
        "event_date": "2026-01-01",  # wrong date, missing venue
    }
    status, key, conf = resolve_to_universe(n, _universe_events())
    # artist + (venue or date) missing => not MATCHED; artist alone => AMBIGUOUS
    assert status in ("AMBIGUOUS", "UNRESOLVED")


def test_resolve_unresolved_wrong_artist():
    n = {"artist_name": "Nobody Famous", "venue_name": "X", "event_date": "2026-11-06"}
    status, key, conf = resolve_to_universe(n, _universe_events())
    assert status == "UNRESOLVED"
    assert key is None


def test_resolve_never_counts_ambiguous_as_matched():
    """An ambiguous row must never be counted as a unique match."""
    n = {
        "artist_name": "Weatherday",
        "venue_name": "Completely Different Venue",
        "event_date": "2026-11-06",
    }
    status, _, _ = resolve_to_universe(n, _universe_events())
    assert status != "MATCHED"


# ── Persistence ────────────────────────────────────────────────────────

def test_persist_snapshot_roundtrip(conn):
    sid = persist_snapshot(conn, {
        "event_key": "event::tm:test1",
        "source_platform": "seatgeek.com",
        "actor_or_endpoint": "axlymxp~seatgeek-event-scraper",
        "source_record_id": "18515703",
        "wave_label": "waveA",
        "observed_at": "2026-08-25T17:00:00+00:00",
        "retrieved_at": "2026-08-25T17:00:00+00:00",
        "resale_min_price": 37.0,
        "resale_median_price": 38.0,
        "listing_count": 3,
        "ticket_count": 8,
        "identity_match_status": "MATCHED",
        "identity_match_confidence": 0.9,
        "rights_status": "TERMS_REVIEW_REQUIRED",
        "commercial_use_status": "PROTOTYPE_ONLY",
    })
    row = conn.execute(
        "SELECT event_key, source_platform, resale_min_price, identity_match_status "
        "FROM acquisition.ticket_market_snapshots WHERE snapshot_id = ?",
        [sid],
    ).fetchone()
    assert row == ("event::tm:test1", "seatgeek.com", 37.0, "MATCHED")


def test_persist_unresolved_snapshot_allows_null_event_key(conn):
    """UNRESOLVED observations are preserved but have no canonical event key."""
    sid = persist_snapshot(conn, {
        "event_key": None,
        "source_platform": "seatgeek.com",
        "actor_or_endpoint": "axlymxp~seatgeek-event-scraper",
        "source_record_id": "99999",
        "wave_label": "wave0",
        "observed_at": "2026-08-25T07:06:00+00:00",
        "retrieved_at": "2026-08-25T07:06:00+00:00",
        "resale_min_price": 17.0,
        "identity_match_status": "UNRESOLVED",
        "rights_status": "TERMS_REVIEW_REQUIRED",
        "commercial_use_status": "PROTOTYPE_ONLY",
    })
    row = conn.execute(
        "SELECT event_key, identity_match_status FROM acquisition.ticket_market_snapshots WHERE snapshot_id = ?",
        [sid],
    ).fetchone()
    assert row == (None, "UNRESOLVED")


def test_source_health_ledger_append(conn):
    record_source_health(conn, {
        "source_platform": "seatgeek.com",
        "actor_or_endpoint": "axlymxp~seatgeek-event-scraper",
        "wave_label": "waveA",
        "started_at": "2026-08-25T17:00:00+00:00",
        "finished_at": "2026-08-25T17:05:00+00:00",
        "status": "SUCCESS",
        "events_requested": 3,
        "events_resolved": 2,
        "observations_ingested": 5,
        "latency_ms": 1000,
        "cost_usd": 0.0,
    })
    n = conn.execute("SELECT COUNT(*) FROM acquisition.source_health_ledger").fetchone()[0]
    assert n == 1


# ── Change detection over sequential observations ──────────────────────

def test_detect_changes_price_and_listing(conn):
    # Wave A
    ingest_observation(conn, ObservationRecord(
        source_platform="seatgeek.com", acquisition_provider="apify",
        source_record_id="18515703", observation_type="TICKET_PRICE",
        observation_category="RESALE", raw_payload={"lowest_price": 37, "listing_count": 3},
        event_key="event::tm:test1", observed_at="2026-08-25T17:00:00+00:00",
        knowledge_time="2026-08-25T17:00:00+00:00",
        rights_status="TERMS_REVIEW_REQUIRED", commercial_use_status="PROTOTYPE_ONLY",
    ))
    # Wave B — price rose, listings rose
    ingest_observation(conn, ObservationRecord(
        source_platform="seatgeek.com", acquisition_provider="apify",
        source_record_id="18515703", observation_type="TICKET_PRICE",
        observation_category="RESALE", raw_payload={"lowest_price": 45, "listing_count": 5},
        event_key="event::tm:test1", observed_at="2026-08-26T09:00:00+00:00",
        knowledge_time="2026-08-26T09:00:00+00:00",
        rights_status="TERMS_REVIEW_REQUIRED", commercial_use_status="PROTOTYPE_ONLY",
    ))
    changes = detect_changes(conn, "seatgeek.com")
    assert len(changes) == 2
    types = {c.change_type for c in changes}
    assert "PRICE_CHANGED" in types
    assert "LISTING_COUNT_CHANGED" in types
    price_change = next(c for c in changes if c.change_type == "PRICE_CHANGED")
    assert price_change.change_direction == "INCREASED"
    assert price_change.change_magnitude == 8.0


def test_detect_changes_no_false_positive_identical(conn):
    payload = {"lowest_price": 37, "listing_count": 3}
    for ts in ("2026-08-25T17:00:00+00:00", "2026-08-26T09:00:00+00:00"):
        ingest_observation(conn, ObservationRecord(
            source_platform="seatgeek.com", acquisition_provider="apify",
            source_record_id="18515703", observation_type="TICKET_PRICE",
            observation_category="RESALE", raw_payload=payload,
            event_key="event::tm:test1", observed_at=ts, knowledge_time=ts,
            rights_status="TERMS_REVIEW_REQUIRED", commercial_use_status="PROTOTYPE_ONLY",
        ))
    changes = detect_changes(conn, "seatgeek.com")
    assert changes == []


def test_observe_universe_fixture_drive():
    """The rail module imports and the source registry is complete."""
    assert set(MARKET_SOURCES.keys()) == {
        "seatgeek", "vividseats", "stubhub", "gametime", "tickpick",
    }
    for key, src in MARKET_SOURCES.items():
        assert src["platform"]
        assert "~" in src["actor"]


def test_watch_universe_file_loads():
    """The frozen universe file loads and has real events."""
    p = PROJECT_ROOT / "data" / "workspace" / "watch_universe_v1.json"
    if not p.exists():
        pytest.skip("watch universe not present in this checkout")
    events = load_universe(p)
    assert len(events) == 100
    keys = {e["event_key"] for e in events}
    assert len(keys) == 100
    # All must be real, not synthetic: have dates + artists.
    assert all(e.get("artist_name") for e in events)
    assert all(e.get("event_date") for e in events)
