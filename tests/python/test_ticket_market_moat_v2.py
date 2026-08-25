"""Tests for TICKET_MARKET_DATA_MOAT_V2.

Covers:
  - deterministic routing policy (MONID_FAST / TICKETS_DEV_DEEP / APIFY_FALLBACK)
  - monthly cost projection
  - tickets.dev adapter: catalog mapping, capture normalization, listing
    lifecycle persistence (no network — fixtures mirroring the sandbox schema)
  - migration 041 tables (event_identifiers, marketplace_listings,
    raw_evidence_store, source_health_by_method)
  - buyer-view additions: market_history columns, cross-market spread,
    event identifiers drill-down, source health by method

No network calls. Sandbox fixtures use the documented tickets.dev schema.
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

from festival_bloomberg.evidence_rails.router import (
    route_observation,
    monthly_cost,
    deep_cadence,
    MEASURED_COST,
)
from festival_bloomberg.evidence_rails.tickets_dev import (
    normalize_capture_snapshot,
    listings_from_snapshot,
    persist_listings,
    mark_disappeared_listings,
    catalog_mappings_for_event,
)
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.schema_paths import split_sql_statements
from festival_bloomberg.planning.proposed_show import (
    buyer_decision_view,
    create_proposed_show,
)
from festival_bloomberg.evidence_rails.ticket_market import persist_snapshot


@pytest.fixture()
def conn():
    c = duckdb.connect(":memory:")
    apply_pending_migrations(c)
    yield c
    c.close()


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


# ── Routing policy ─────────────────────────────────────────────────────

def test_route_fast_known_url_uses_monid():
    r = route_observation(marketplace="seatgeek.com", has_mapped_url=True,
                          needs_listings=False, cadence="daily")
    assert r["method"] == "MONID_FAST"
    assert r["rail"] == "FAST"
    assert r["provider"] == "monid"
    assert r["cost_per_call"] == MEASURED_COST["MONID_HTML"]


def test_route_deep_with_live_key_uses_tickets_dev():
    r = route_observation(marketplace="stubhub.com", has_mapped_url=True,
                          needs_listings=True, cadence="weekly",
                          tickets_dev_live_key=True)
    assert r["method"] == "TICKETS_DEV_DEEP"
    assert r["rail"] == "DEEP"
    assert r["cost_per_call"] == MEASURED_COST["TICKETS_DEV_CAPTURE"]


def test_route_deep_without_live_key_falls_back_to_monid():
    r = route_observation(marketplace="vividseats.com", has_mapped_url=True,
                          needs_listings=True, cadence="weekly",
                          tickets_dev_live_key=False)
    assert r["method"] == "MONID_FAST_DEEP_FALLBACK"


def test_route_fallback_when_monid_unavailable():
    r = route_observation(marketplace="tickpick.com", has_mapped_url=False,
                          needs_listings=False, cadence="daily",
                          monid_available=False)
    assert r["method"] == "APIFY_FALLBACK"


def test_monthly_cost_projection():
    m = monthly_cost(100, tickets_dev_live_key=False)
    # FAST: 100 events x 1/day x 30 x $0.0009 = $2.70
    assert m["fast"]["cost_usd"] == pytest.approx(2.70, abs=0.01)
    assert m["deep"]["provider"] == "monid_fallback"
    assert m["cost_per_event_month"] > 0
    assert "availability proxies" in m["disclaimer"]


def test_deep_cadence_buckets():
    assert deep_cadence("2026-08-26", now="2026-08-25") == "daily"
    assert deep_cadence("2026-09-01", now="2026-08-25") == "T-7"
    assert deep_cadence("2026-09-20", now="2026-08-25") == "T-30"
    assert deep_cadence(None) == "weekly"


# ── tickets.dev adapter (fixtures only) ────────────────────────────────

def _sandbox_snapshot():
    """Fixture mirroring the documented tickets.dev capture schema."""
    return {
        "source": "seatgeek",
        "eventId": "18515703",
        "eventName": "Weatherday at Bottom Lounge",
        "venue": {
            "name": "Bottom Lounge",
            "city": "Chicago",
            "state": "IL",
            "timezone": "America/Chicago",
        },
        "performers": [{"performerId": "123", "name": "Weatherday", "master": True}],
        "eventDateUtc": "2026-11-06T19:30:00+00:00",
        "eventDateLocal": "2026-11-06T13:30:00-06:00",
        "currency": "USD",
        "sourceUrl": "https://seatgeek.com/weatherday-tickets/2026-11-06/18515703",
        "capturedAt": "2026-08-26T09:00:00+00:00",
        "note": "",
        "stats": {
            "listingCount": 4,
            "ticketCount": 7,
            "getInPrice": 402.0,
            "medianPrice": 418.0,
            "avgPrice": 418.75,
            "maxPrice": 437.0,
        },
        "listings": [
            {"listingId": "A6rs27JVemV", "inventoryType": "resale", "section": "110",
             "row": "37", "quantity": 1, "ticketPrice": 344.0, "fee": 57.85,
             "totalPrice": 402.0, "sellableQuantities": "1", "seats": ""},
            {"listingId": "B7st28KVfnW", "inventoryType": "resale", "section": "110",
             "row": "39", "quantity": 2, "ticketPrice": 360.0, "fee": 58.0,
             "totalPrice": 836.0, "sellableQuantities": "1,2", "seats": ""},
        ],
    }


def test_normalize_capture_snapshot_contract():
    snap = _sandbox_snapshot()
    norm = normalize_capture_snapshot(
        snap, event_key="event::tm:test1", wave_label="wave_v2_1",
    )
    assert norm["source_platform"] == "seatgeek.com"
    assert norm["actor_or_endpoint"] == "tickets_dev_capture:seatgeek"
    assert norm["source_record_id"] == "18515703"
    assert norm["resale_min_price"] == 402.0
    assert norm["resale_median_price"] == 418.0
    assert norm["resale_max_price"] == 437.0
    assert norm["listing_count"] == 4
    assert norm["ticket_count"] == 7
    assert norm["identity_match_status"] == "MATCHED"
    assert norm["parser_version"] == "tickets_dev_v1"
    assert norm["raw_payload_hash"]


def test_listings_from_snapshot_all_in_derivation():
    snap = _sandbox_snapshot()
    rows = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w")
    assert len(rows) == 2
    # Listing 1: 1 x $402 all-in; listing 2: 2 x $418 all-in.
    assert rows[0]["all_in_price"] == pytest.approx(402.0)
    assert rows[1]["all_in_price"] == pytest.approx(418.0)
    assert rows[0]["inventory_type"] == "resale"
    assert rows[0]["section"] == "110"


def test_persist_listings_lifecycle(conn):
    snap = _sandbox_snapshot()
    rows1 = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w1",
                                   observed_at="2026-08-26T09:00:00+00:00")
    n1 = persist_listings(conn, rows1)
    assert n1 == 2

    # Second wave: same listing ids, new prices.
    snap2 = json.loads(json.dumps(snap))
    snap2["listings"][0]["totalPrice"] = 425.0  # price rose
    rows2 = listings_from_snapshot(snap2, event_key="event::tm:test1", wave_label="w2",
                                   observed_at="2026-08-27T09:00:00+00:00")
    persist_listings(conn, rows2)

    row = conn.execute(
        "SELECT status, all_in_price FROM acquisition.marketplace_listings "
        "WHERE provider_listing_id = 'A6rs27JVemV'"
    ).fetchone()
    assert row[0] == "PRICE_CHANGED"
    assert row[1] == pytest.approx(425.0)

    # Price history appended (2 entries).
    hist = conn.execute(
        "SELECT price_history_json FROM acquisition.marketplace_listings "
        "WHERE provider_listing_id = 'A6rs27JVemV'"
    ).fetchone()[0]
    assert len(json.loads(hist)) == 2


def test_mark_disappeared_not_sale(conn):
    snap = _sandbox_snapshot()
    rows = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w1",
                                  observed_at="2026-08-26T09:00:00+00:00")
    persist_listings(conn, rows)
    # Next wave only carries one of the two listings.
    snap2 = json.loads(json.dumps(snap))
    snap2["listings"] = [snap2["listings"][0]]
    rows2 = listings_from_snapshot(snap2, event_key="event::tm:test1", wave_label="w2",
                                   observed_at="2026-08-27T09:00:00+00:00")
    persist_listings(conn, rows2)
    seen = {r["provider_listing_id"] for r in rows2}
    n = mark_disappeared_listings(conn, "event::tm:test1", "seatgeek.com", seen,
                                  "2026-08-27T09:00:00+00:00")
    assert n == 1
    status = conn.execute(
        "SELECT status FROM acquisition.marketplace_listings "
        "WHERE provider_listing_id = 'B7st28KVfnW'"
    ).fetchone()[0]
    assert status == "LISTING_DISAPPEARED"


def test_catalog_mapping_no_network_mocked():
    """catalog_mappings_for_event degrades gracefully without network."""
    # We monkeypatch _request to return a synthetic catalog response.
    import festival_bloomberg.evidence_rails.tickets_dev as td

    def fake_request(method, path, query=None):
        return {
            "events": [{
                "id": "cat1",
                "name": "Weatherday",
                "eventDateUtc": "2026-11-06T19:30:00+00:00",
                "venue": {"name": "Bottom Lounge", "city": "Chicago", "state": "IL"},
                "performers": [{"name": "Weatherday"}],
                "sources": [
                    {"marketplace": "ticketmaster", "eventId": "TM123",
                     "url": "https://www.ticketmaster.com/weatherday"},
                    {"marketplace": "vividseats", "eventId": "VS456",
                     "url": "https://www.vividseats.com/weatherday"},
                ],
            }],
            "total": 1,
        }
    orig = td._request
    td._request = fake_request
    try:
        res = catalog_mappings_for_event(
            "Weatherday", "Bottom Lounge", "Chicago", "2026-11-06",
        )
    finally:
        td._request = orig
    assert res["status"] == "MATCHED_EXACT"
    mps = {m["marketplace"]: m["marketplace_event_id"] for m in res["mappings"]}
    assert mps == {"ticketmaster": "TM123", "vividseats": "VS456"}


# ── Migration 041 tables ───────────────────────────────────────────────

def test_migration_041_tables_exist(conn):
    for table in (
        "acquisition.event_identifiers",
        "acquisition.marketplace_listings",
        "acquisition.raw_evidence_store",
        "acquisition.source_health_by_method",
    ):
        n = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            ["acquisition", table.split(".")[1]],
        ).fetchone()[0]
        assert n == 1, f"{table} missing"


def test_event_identifiers_roundtrip(conn):
    conn.execute(
        """INSERT INTO acquisition.event_identifiers (
            identifier_id, event_key, marketplace, marketplace_event_id,
            marketplace_event_url, mapping_status, mapping_method, confidence,
            first_resolved_at, rights_status, commercial_use_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ["id1", "event::tm:test1", "seatgeek", "18515703",
         "https://seatgeek.com/weatherday", "EXACT_PROVIDER_ID",
         "TICKETS_DEV_CATALOG", 1.0, "2026-08-26T00:00:00+00:00",
         "TERMS_REVIEW_REQUIRED", "PROTOTYPE_ONLY"],
    )
    row = conn.execute(
        "SELECT marketplace, marketplace_event_id, mapping_status FROM acquisition.event_identifiers"
    ).fetchone()
    assert row == ("seatgeek", "18515703", "EXACT_PROVIDER_ID")


def test_raw_evidence_dedup_key(conn):
    """Same payload hash reuses one raw row; ref_count increments."""
    conn.execute(
        """INSERT INTO acquisition.raw_evidence_store (
            payload_hash, marketplace, event_key, payload_type, payload,
            byte_size, first_seen_at, last_seen_at, ref_count,
            rights_status, commercial_use_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        ["hash1", "seatgeek.com", "event::tm:test1", "JSONLD", b"<html/>",
         8, "2026-08-26T09:00:00+00:00", "2026-08-26T09:00:00+00:00",
         "TERMS_REVIEW_REQUIRED", "PROTOTYPE_ONLY"],
    )
    n = conn.execute(
        "SELECT COUNT(*) FROM acquisition.raw_evidence_store WHERE payload_hash = 'hash1'"
    ).fetchone()[0]
    assert n == 1


def test_source_health_by_method_roundtrip(conn):
    conn.execute(
        """INSERT INTO acquisition.source_health_by_method (
            health_id, method, marketplace, wave_label, started_at, finished_at,
            status, events_requested, events_resolved, observations_ingested,
            latency_ms, cost_usd, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ["h1", "TICKETS_DEV_DEEP", "seatgeek.com", "wave_v2_1",
         "2026-08-26T09:00:00+00:00", "2026-08-26T09:01:00+00:00",
         "SUCCESS", 1, 1, 1, 900, 0.03, "v2_20260825"],
    )
    row = conn.execute(
        "SELECT method, status, cost_usd FROM acquisition.source_health_by_method"
    ).fetchone()
    assert row == ("TICKETS_DEV_DEEP", "SUCCESS", 0.03)


# ── Buyer-view additions (V2) ──────────────────────────────────────────

def _seed_watch_universe(conn) -> str:
    conn.execute(
        """INSERT INTO acquisition.watch_universe (
            watch_universe_version, event_key, provider_event_id, artist_key,
            artist_name, venue_key, venue_name, market_key, city, state,
            event_date, timezone, frozen_at, selection_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ["watch_universe_v1", "event::tm:test1", "rZ7HnEZ1Afv8o7",
         "artist::weatherday", "Weatherday", "venue::tm:test", "Bottom Lounge",
         "chicago-il", "Chicago", "IL", "2026-11-06", "America/Chicago",
         "2026-08-25T00:00:00+00:00", "ticketmaster-music-upcoming-7to90d"],
    )
    return "event::tm:test1"


def _seed_multi_source_observations(conn, event_key: str) -> None:
    """Two marketplaces, two waves each — drives cross-market spread + history."""
    for platform, prices, listings in (
        ("seatgeek.com", [37.0, 45.0], [3, 5]),
        ("vividseats.com", [40.0, 48.0], [5, 7]),
    ):
        for i in range(2):
            day = 25 + i
            persist_snapshot(conn, {
                "watch_universe_version": "watch_universe_v1",
                "event_key": event_key,
                "source_platform": platform,
                "actor_or_endpoint": "tickets_dev_capture:seatgeek" if "seatgeek" in platform else "monid_context_dev",
                "source_record_id": f"rec-{platform}-{i}",
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


def test_buyer_view_v2_market_history_and_cross_market(evidence_conn, workspace_conn):
    event_key = _seed_watch_universe(evidence_conn)
    _seed_multi_source_observations(evidence_conn, event_key)

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

    # market_history: NOW / 1D columns per source.
    mh = tm["market_history"]
    assert "seatgeek.com" in mh and "vividseats.com" in mh
    sg = mh["seatgeek.com"]
    assert sg["now"]["min_price"] == 45.0
    assert sg["observations"] == 2
    assert "1d" in sg or "7d" in sg  # window present with real timestamps

    # cross_market spread: seatgeek 45 vs vivid 48.
    cm = tm["cross_market"]
    assert cm["status"] == "OBSERVED"
    assert cm["lowest_observed_price"] == 45.0
    assert cm["highest_observed_price"] == 48.0
    assert cm["absolute_spread"] == pytest.approx(3.0)
    assert "per_source" in cm

    # event_identifiers + source_health are present (graceful when empty).
    assert "event_identifiers" in tm
    assert "source_health" in tm


def test_buyer_view_v2_graceful_without_041_tables(workspace_conn):
    """Without migration 041 tables the V2 additions must not crash the view."""
    # Use an evidence connection that only has migration 039 applied.
    c = duckdb.connect(":memory:")
    # Apply only up to 039 by replaying files 001..039.
    from festival_bloomberg.schema_paths import load_migration_files, load_schema_sql

    for stmt in split_sql_statements(load_schema_sql()):
        c.execute(stmt)
    for _version, _name, path in sorted(load_migration_files(), key=lambda t: t[0]):
        if path.name.startswith("040") or path.name.startswith("041"):
            continue
        for stmt in split_sql_statements(path.read_text(encoding="utf-8")):
            c.execute(stmt)
    c.execute(
        """INSERT INTO acquisition.watch_universe (
            watch_universe_version, event_key, provider_event_id, artist_key,
            artist_name, venue_key, venue_name, market_key, city, state,
            event_date, timezone, frozen_at, selection_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ["watch_universe_v1", "event::tm:test1", "rZ7HnEZ1Afv8o7",
         "artist::weatherday", "Weatherday", "venue::tm:test", "Bottom Lounge",
         "chicago-il", "Chicago", "IL", "2026-11-06", "America/Chicago",
         "2026-08-25T00:00:00+00:00", "ticketmaster-music-upcoming-7to90d"],
    )
    persist_snapshot(c, {
        "watch_universe_version": "watch_universe_v1",
        "event_key": "event::tm:test1",
        "source_platform": "seatgeek.com",
        "source_record_id": "r1",
        "wave_label": "wave0",
        "observed_at": "2026-08-25T09:00:00+00:00",
        "retrieved_at": "2026-08-25T09:00:00+00:00",
        "knowledge_time": "2026-08-25T09:00:00+00:00",
        "currency": "USD",
        "resale_min_price": 37.0,
        "identity_match_status": "MATCHED",
        "rights_status": "TERMS_REVIEW_REQUIRED",
        "commercial_use_status": "PROTOTYPE_ONLY",
    })
    result = create_proposed_show(
        workspace_conn,
        project_key="p1", artist_name="Weatherday", market="Chicago, IL",
        city="Chicago", state_code="IL", venue_key="venue::tm:test",
        venue_name="Bottom Lounge", proposed_date="2026-11-06",
    )
    view = buyer_decision_view(
        c, workspace_conn,
        proposed_show_key=result["proposed_show_key"],
        evidence_conn=c,
    )
    tm = view["ticket_market"]
    assert tm["status"] == "OBSERVED"
    assert tm["event_identifiers"]["status"] == "UNKNOWN"
    assert tm["source_health"]["status"] == "UNKNOWN"
    assert tm["market_history"]["seatgeek.com"]["now"]["min_price"] == 37.0
    c.close()
