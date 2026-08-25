"""Buyer-decision-view integration tests for the TICKET MARKET section.

Proves the proposed-show view renders ticket-market evidence from the
evidence estate (migration 039) — with real MATCHED snapshots driving the
time series, UNRESOLVED rows preserved but excluded, and change deltas.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from festival_bloomberg.evidence_rails.ticket_market import (
    persist_snapshot,
    record_source_health,
)
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.planning.proposed_show import (
    buyer_decision_view,
    create_proposed_show,
)


@pytest.fixture()
def evidence_conn():
    c = duckdb.connect(":memory:")
    apply_pending_migrations(c)
    yield c
    c.close()


@pytest.fixture()
def workspace_conn():
    c = duckdb.connect(":memory:")
    apply_pending_migrations(c)
    yield c
    c.close()


def _seed_watch_universe(conn) -> str:
    """Insert one real watch-universe event; returns event_key."""
    conn.execute(
        """
        INSERT INTO acquisition.watch_universe (
            watch_universe_version, event_key, provider_event_id, artist_key,
            artist_name, venue_key, venue_name, market_key, city, state,
            event_date, timezone, frozen_at, selection_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "watch_universe_v1",
            "event::tm:test1",
            "rZ7HnEZ1Afv8o7",
            "artist::weatherday",
            "Weatherday",
            "venue::tm:test",
            "Bottom Lounge",
            "chicago-il",
            "Chicago",
            "IL",
            "2026-11-06",
            "America/Chicago",
            "2026-08-25T00:00:00+00:00",
            "ticketmaster-music-upcoming-7to90d",
        ],
    )
    return "event::tm:test1"


def _seed_market_observations(conn, event_key: str, n_waves: int = 2) -> None:
    """Seed 2 real observation waves for the event from SeatGeek."""
    prices = [37.0, 45.0]
    listings = [3, 5]
    for i in range(n_waves):
        day = 25 + i
        persist_snapshot(conn, {
            "watch_universe_version": "watch_universe_v1",
            "event_key": event_key,
            "provider_event_id": "rZ7HnEZ1Afv8o7",
            "source_platform": "seatgeek.com",
            "actor_or_endpoint": "axlymxp~seatgeek-event-scraper",
            "source_record_id": "18515703",
            "wave_label": f"wave{i}",
            "observed_at": f"2026-08-{day}T09:00:00+00:00",
            "retrieved_at": f"2026-08-{day}T09:00:00+00:00",
            "knowledge_time": f"2026-08-{day}T09:00:00+00:00",
            "currency": "USD",
            "resale_min_price": prices[i],
            "resale_median_price": prices[i] + 1,
            "listing_count": listings[i],
            "ticket_count": listings[i] * 3,
            "identity_match_status": "MATCHED",
            "identity_match_method": "ARTIST_VENUE_DATE",
            "identity_match_confidence": 0.9,
            "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY",
        })
    # An UNRESOLVED row: preserved but must not drive the series.
    persist_snapshot(conn, {
        "event_key": None,
        "source_platform": "seatgeek.com",
        "source_record_id": "99999",
        "wave_label": "wave0",
        "observed_at": "2026-08-25T07:06:00+00:00",
        "retrieved_at": "2026-08-25T07:06:00+00:00",
        "resale_min_price": 17.0,
        "identity_match_status": "UNRESOLVED",
        "rights_status": "TERMS_REVIEW_REQUIRED",
        "commercial_use_status": "PROTOTYPE_ONLY",
    })


def test_buyer_view_without_evidence_conn_is_unknown(evidence_conn, workspace_conn):
    result = create_proposed_show(
        workspace_conn,
        project_key="p1", artist_name="Weatherday", market="Chicago, IL",
        city="Chicago", state_code="IL", venue_key="venue::tm:test",
        venue_name="Bottom Lounge", proposed_date="2026-11-06",
    )
    view = buyer_decision_view(
        evidence_conn, workspace_conn,
        proposed_show_key=result["proposed_show_key"],
    )
    assert view["status"] == "OBSERVED"
    tm = view["ticket_market"]
    assert tm["status"] == "UNKNOWN"
    assert tm["reason"] == "evidence estate not connected"
    assert "ticket_market" in view["evidence_status"]["UNKNOWN"]


def test_buyer_view_ticket_market_matched(evidence_conn, workspace_conn):
    event_key = _seed_watch_universe(evidence_conn)
    _seed_market_observations(evidence_conn, event_key, n_waves=2)

    result = create_proposed_show(
        workspace_conn,
        project_key="p1", artist_name="Weatherday", market="Chicago, IL",
        city="Chicago", state_code="IL", venue_key="venue::tm:test",
        venue_name="Bottom Lounge", proposed_date="2026-11-06",
    )
    view = buyer_decision_view(
        evidence_conn, workspace_conn,
        proposed_show_key=result["proposed_show_key"],
        evidence_conn=evidence_conn,
    )
    tm = view["ticket_market"]
    assert tm["status"] == "OBSERVED"
    assert tm["event_key"] == event_key
    assert len(tm["sources"]) == 1
    src = tm["sources"][0]
    assert src["source_platform"] == "seatgeek.com"
    # Latest wave values.
    assert src["current"]["min_price"] == 45.0
    assert src["current"]["median_price"] == 46.0
    assert src["current"]["listing_count"] == 5
    # Change detection across 2 real observations.
    assert src["change"]["observation_count"] == 2
    assert src["change"]["min_price"]["previous"] == 37.0
    assert src["change"]["min_price"]["current"] == 45.0
    assert src["change"]["min_price"]["absolute_change"] == 8.0
    assert src["change"]["min_price"]["percent_change"] == pytest.approx(21.62, abs=0.1)
    assert src["change"]["listing_count"]["previous"] == 3
    # History coverage.
    hc = tm["history_coverage"]
    assert hc["observations"] == 2
    assert hc["source_count"] == 1
    assert hc["sources"] == ["seatgeek.com"]
    assert hc["first_observed"] is not None
    assert hc["last_observed"] is not None
    # Evidence status.
    assert "ticket_market" in view["evidence_status"]["KNOWN"]


def test_buyer_view_unresolved_rows_do_not_drive_series(evidence_conn, workspace_conn):
    """UNRESOLVED snapshots (event_key NULL) must not appear as sources."""
    event_key = _seed_watch_universe(evidence_conn)
    _seed_market_observations(evidence_conn, event_key, n_waves=1)

    result = create_proposed_show(
        workspace_conn,
        project_key="p1", artist_name="Weatherday", market="Chicago, IL",
        city="Chicago", state_code="IL", venue_key="venue::tm:test",
        venue_name="Bottom Lounge", proposed_date="2026-11-06",
    )
    view = buyer_decision_view(
        evidence_conn, workspace_conn,
        proposed_show_key=result["proposed_show_key"],
        evidence_conn=evidence_conn,
    )
    tm = view["ticket_market"]
    assert tm["status"] == "OBSERVED"
    assert tm["history_coverage"]["observations"] == 1
    # UNRESOLVED row (event_key NULL) is not among sources.
    for src in tm["sources"]:
        assert src["source_platform"] != "UNRESOLVED_PLACEHOLDER"
    assert all(s["source_platform"] == "seatgeek.com" for s in tm["sources"])


def test_buyer_view_show_not_in_universe(evidence_conn, workspace_conn):
    result = create_proposed_show(
        workspace_conn,
        project_key="p1", artist_name="Someone Else", market="New York, NY",
        city="New York", state_code="NY", venue_name="Madison Square Garden",
        proposed_date="2026-10-01",
    )
    view = buyer_decision_view(
        evidence_conn, workspace_conn,
        proposed_show_key=result["proposed_show_key"],
        evidence_conn=evidence_conn,
    )
    tm = view["ticket_market"]
    assert tm["status"] == "UNKNOWN"
    assert "not in frozen watch universe" in tm["reason"]


def test_source_health_ledger_queryable(evidence_conn):
    record_source_health(evidence_conn, {
        "source_platform": "seatgeek.com",
        "actor_or_endpoint": "axlymxp~seatgeek-event-scraper",
        "wave_label": "wave0",
        "started_at": "2026-08-25T07:05:00+00:00",
        "finished_at": "2026-08-25T07:07:00+00:00",
        "status": "SUCCESS",
        "events_requested": 100,
        "events_resolved": 0,
        "observations_ingested": 100,
        "latency_ms": 120000,
        "cost_usd": 0.0,
        "records_returned": 100,
    })
    row = evidence_conn.execute(
        "SELECT source_platform, status, observations_ingested FROM acquisition.source_health_ledger"
    ).fetchone()
    assert row == ("seatgeek.com", "SUCCESS", 100)
