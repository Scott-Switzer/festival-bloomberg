"""Tests for TICKET_MARKET_DATA_MOAT_V2 (review-corrected).

Covers:
  - deterministic routing policy (MONID_FAST / TICKETS_DEV_DEEP / APIFY_FALLBACK)
  - monthly cost projection with cost-basis labels
  - tickets.dev adapter: catalog mapping, capture normalization, listing
    lifecycle persistence (no network — fixtures mirroring the sandbox schema)
  - PRICE SEMANTICS: totalPrice is ALL-IN PER TICKET — all_in_price = totalPrice,
    never divided by quantity
  - append-only marketplace_listing_observations (immutable history; no truncation)
  - lifecycle semantics: last_seen_at preserved on disappearance,
    first_missing_at/disappeared_at, LISTING_REAPPEARED, unchanged never resets
  - raw evidence store hash-dedup (1 row, ref_count=2 for identical payload)
  - collector: sandbox fixtures NEVER enter the warehouse; hard budget
    pre-authorization; retry wrapper
  - buyer view: 1D/7D windows use the LATEST observation <= cutoff (not oldest);
    source health scoped to the event's marketplaces
  - one canonical identity contract: persist_mapping → event_identifiers is the
    row the collector and buyer view both read

No network calls. Sandbox fixtures use the documented tickets.dev schema.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
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
    COST_BASIS,
)
from festival_bloomberg.evidence_rails.tickets_dev import (
    normalize_capture_snapshot,
    listings_from_snapshot,
    persist_listings,
    mark_disappeared_listings,
    catalog_mappings_for_event,
    persist_catalog_mappings,
    persist_raw_evidence,
)
from festival_bloomberg.evidence_rails.url_resolver import persist_mapping
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.schema_paths import split_sql_statements
from festival_bloomberg.planning.proposed_show import (
    buyer_decision_view,
    create_proposed_show,
)
from festival_bloomberg.evidence_rails.ticket_market import persist_snapshot


def _load_collector():
    spec = importlib.util.spec_from_file_location(
        "collect_ticket_market_mod",
        PROJECT_ROOT / "scripts" / "collect_ticket_market.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


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
    assert r.get("cost_basis") == "MEASURED"


def test_route_deep_with_live_key_uses_tickets_dev():
    r = route_observation(marketplace="stubhub.com", has_mapped_url=True,
                          needs_listings=True, cadence="weekly",
                          tickets_dev_live_key=True)
    assert r["method"] == "TICKETS_DEV_DEEP"
    assert r["rail"] == "DEEP"
    assert r["cost_per_call"] == MEASURED_COST["TICKETS_DEV_CAPTURE"]
    assert r.get("cost_basis") == "PUBLISHED_PRICE_ASSUMPTION"


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


def test_monthly_cost_projection_labels_basis():
    m = monthly_cost(100, tickets_dev_live_key=False)
    assert m["fast"]["cost_basis"] == "MEASURED"
    assert m["deep"]["cost_basis"] == "MEASURED"  # monid fallback is measured
    m_live = monthly_cost(100, tickets_dev_live_key=True)
    assert m_live["deep"]["cost_basis"] == "PUBLISHED_PRICE_ASSUMPTION"
    assert COST_BASIS["TICKETS_DEV_CAPTURE"] == "PUBLISHED_PRICE_ASSUMPTION"


def test_deep_cadence_buckets():
    assert deep_cadence("2026-08-26", now="2026-08-25") == "daily"
    assert deep_cadence("2026-09-01", now="2026-08-25") == "T-7"
    assert deep_cadence("2026-09-20", now="2026-08-25") == "T-30"
    assert deep_cadence(None) == "weekly"


# ── tickets.dev adapter (fixtures only) ────────────────────────────────

def _sandbox_snapshot():
    """Fixture mirroring the documented tickets.dev capture schema.

    totalPrice is the ALL-IN price PER TICKET (excl. sales tax), normalized
    across marketplaces. quantity does NOT scale totalPrice.
    """
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
             "totalPrice": 418.0, "sellableQuantities": "1,2", "seats": ""},
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


def test_listings_from_snapshot_all_in_is_per_ticket():
    """totalPrice is the ALL-IN price PER TICKET — quantity must NOT divide it."""
    snap = _sandbox_snapshot()
    rows = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w")
    assert len(rows) == 2
    # Listing 1: 1 ticket at $402 all-in.
    assert rows[0]["all_in_price"] == pytest.approx(402.0)
    # Listing 2: 2 tickets at $418 all-in PER TICKET (NOT $836/2, NOT $209).
    assert rows[1]["all_in_price"] == pytest.approx(418.0)
    assert rows[1]["quantity"] == 2
    assert rows[1]["ticket_price"] == pytest.approx(360.0)
    assert rows[1]["fee"] == pytest.approx(58.0)


def test_quantity_gt_one_does_not_alter_all_in_price():
    """Regression: a multi-ticket listing keeps totalPrice as all-in per ticket."""
    snap = _sandbox_snapshot()
    snap["listings"][1]["quantity"] = 4  # even more seats, same per-ticket price
    rows = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w")
    assert rows[1]["all_in_price"] == pytest.approx(418.0)
    assert rows[1]["quantity"] == 4


def test_persist_listings_appends_observations_and_tracks_lifecycle(conn):
    snap = _sandbox_snapshot()
    rows1 = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w1",
                                   observed_at="2026-08-26T09:00:00+00:00")
    n1 = persist_listings(conn, rows1, source_snapshot_id="snap1")
    assert n1 == 2

    # Second wave: same listing ids, new price.
    snap2 = json.loads(json.dumps(snap))
    snap2["listings"][0]["totalPrice"] = 425.0  # price rose
    rows2 = listings_from_snapshot(snap2, event_key="event::tm:test1", wave_label="w2",
                                   observed_at="2026-08-27T09:00:00+00:00")
    persist_listings(conn, rows2, source_snapshot_id="snap2")

    # Current-state cache: PRICE_CHANGED + latest price.
    row = conn.execute(
        "SELECT status, all_in_price FROM acquisition.marketplace_listings "
        "WHERE provider_listing_id = 'A6rs27JVemV'"
    ).fetchone()
    assert row[0] == "LISTING_PRICE_CHANGED"
    assert row[1] == pytest.approx(425.0)

    # Immutable history: 2 observation rows for that listing (one per wave).
    obs = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT observed_at) FROM acquisition.marketplace_listing_observations "
        "WHERE provider_listing_id = 'A6rs27JVemV'"
    ).fetchone()
    assert obs[0] == 2
    assert obs[1] == 2


def test_unchanged_listing_does_not_reset_to_appeared(conn):
    """A repeated UNCHANGED listing keeps its previous status."""
    snap = _sandbox_snapshot()
    rows1 = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w1",
                                   observed_at="2026-08-26T09:00:00+00:00")
    persist_listings(conn, rows1)
    # Same wave again: no changes.
    rows2 = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w2",
                                   observed_at="2026-08-27T09:00:00+00:00")
    persist_listings(conn, rows2)
    status = conn.execute(
        "SELECT status FROM acquisition.marketplace_listings "
        "WHERE provider_listing_id = 'A6rs27JVemV'"
    ).fetchone()[0]
    assert status == "LISTING_APPEARED"  # unchanged: still the first appearance


def test_mark_disappeared_preserves_last_seen_and_sets_missing(conn):
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
    row = conn.execute(
        "SELECT status, last_seen_at, first_missing_at, disappeared_at "
        "FROM acquisition.marketplace_listings WHERE provider_listing_id = 'B7st28KVfnW'"
    ).fetchone()
    assert row[0] == "LISTING_DISAPPEARED"
    # last_seen_at stays the last observation CONTAINING the listing (Aug 26),
    # never overwritten by the disappearance observation (Aug 27).
    assert str(row[1]).startswith("2026-08-26")
    assert str(row[2]).startswith("2026-08-27")  # first missing
    assert str(row[3]).startswith("2026-08-27")  # disappeared
    # Immutable log records the transition.
    gone = conn.execute(
        "SELECT COUNT(*) FROM acquisition.marketplace_listing_observations "
        "WHERE provider_listing_id = 'B7st28KVfnW' AND status = 'DISAPPEARED'"
    ).fetchone()[0]
    assert gone == 1


def test_reappeared_listing_transition(conn):
    snap = _sandbox_snapshot()
    rows1 = listings_from_snapshot(snap, event_key="event::tm:test1", wave_label="w1",
                                   observed_at="2026-08-26T09:00:00+00:00")
    persist_listings(conn, rows1)
    # Disappear both.
    mark_disappeared_listings(conn, "event::tm:test1", "seatgeek.com", set(),
                              "2026-08-27T09:00:00+00:00")
    # Reappear listing 2 in wave 3.
    snap3 = json.loads(json.dumps(snap))
    snap3["listings"] = [snap3["listings"][1]]
    rows3 = listings_from_snapshot(snap3, event_key="event::tm:test1", wave_label="w3",
                                   observed_at="2026-08-28T09:00:00+00:00")
    persist_listings(conn, rows3)
    row = conn.execute(
        "SELECT status, first_missing_at, disappeared_at "
        "FROM acquisition.marketplace_listings WHERE provider_listing_id = 'B7st28KVfnW'"
    ).fetchone()
    assert row[0] == "LISTING_REAPPEARED"
    assert row[1] is None  # missing-state cleared on reappearance
    assert row[2] is None


def test_catalog_mapping_no_network_mocked():
    """catalog_mappings_for_event degrades gracefully without network."""
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


def test_persist_catalog_mappings_writes_canonical_master(conn):
    n = persist_catalog_mappings(
        conn,
        event_key="event::tm:test1",
        mappings=[
            {"marketplace": "ticketmaster", "marketplace_event_id": "TM123",
             "marketplace_event_url": "https://www.ticketmaster.com/weatherday", "matched": True},
        ],
    )
    assert n == 1
    row = conn.execute(
        "SELECT mapping_status, mapping_method FROM acquisition.event_identifiers "
        "WHERE event_key = 'event::tm:test1' AND marketplace = 'ticketmaster'"
    ).fetchone()
    assert row == ("EXACT_PROVIDER_ID", "TICKETS_DEV_CATALOG")


# ── Migration 041/042 tables ───────────────────────────────────────────

def test_migration_041_042_tables_exist(conn):
    for table in (
        "acquisition.event_identifiers",
        "acquisition.marketplace_listings",
        "acquisition.raw_evidence_store",
        "acquisition.source_health_by_method",
        "acquisition.marketplace_listing_observations",
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


def test_raw_evidence_dedup_same_payload_one_row_refcount_two(conn):
    """Identical payload → 1 raw evidence row, ref_count=2."""
    payload = {"eventId": "18515703", "stats": {"getInPrice": 402.0}}
    r1 = persist_raw_evidence(conn, event_key="event::tm:test1", marketplace="seatgeek.com",
                              payload=payload, payload_type="SNAPSHOT_JSON")
    r2 = persist_raw_evidence(conn, event_key="event::tm:test1", marketplace="seatgeek.com",
                              payload=payload, payload_type="SNAPSHOT_JSON")
    assert r1["payload_hash"] == r2["payload_hash"]
    assert r1["is_new"] is True
    assert r2["is_new"] is False
    assert r2["ref_count"] == 2
    n = conn.execute(
        "SELECT COUNT(*), MAX(ref_count) FROM acquisition.raw_evidence_store "
        "WHERE payload_hash = ?", [r1["payload_hash"]],
    ).fetchone()
    assert n == (1, 2)


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


# ── Collector: sandbox invariant + hard budget ─────────────────────────

def _seed_identity(conn, event_key: str = "event::tm:test1") -> None:
    conn.execute(
        """INSERT INTO acquisition.event_identifiers (
            identifier_id, event_key, marketplace, marketplace_event_id,
            marketplace_event_url, mapping_status, mapping_method, confidence,
            first_resolved_at, rights_status, commercial_use_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ["id::sg", event_key, "seatgeek.com", "18515703",
         "https://seatgeek.com/weatherday-tickets/2026-11-06/18515703",
         "EXACT_PAGE_MATCH", "MONID_SEARCH", 1.0,
         "2026-08-25T00:00:00+00:00", "TERMS_REVIEW_REQUIRED", "PROTOTYPE_ONLY"],
    )


def test_collector_deep_without_live_key_never_touches_sandbox(conn):
    """Without a live tickets.dev key the DEEP rail is DEEP_UNAVAILABLE —
    sandbox fixtures must NEVER enter the warehouse."""
    _seed_identity(conn)
    universe = [{
        "event_key": "event::tm:test1", "watch_universe_version": "watch_universe_v1",
        "market_key": "chicago-il",
    }]
    col = _load_collector()
    # If sandbox capture were attempted this would raise.
    def _boom(*a, **k):
        raise AssertionError("tickets.dev capture called without a live key")

    col._tickets_dev_deep = _boom
    report = col.run_collect(
        conn, universe,
        source=None, fast=False, deep=True, max_cost=2.00, max_fetch=None,
        wave_label="wave_test_deep", tickets_dev_live_key=False,
    )
    assert report["totals"]["snapshots"] == 0
    assert report["totals"]["skipped_deep_no_live_key"] == 1
    assert report["methods"].get("DEEP_UNAVAILABLE", {}).get("calls") == 1
    # No tickets.dev rows anywhere.
    n_snap = conn.execute(
        "SELECT COUNT(*) FROM acquisition.ticket_market_snapshots "
        "WHERE actor_or_endpoint LIKE 'tickets_dev_capture%'"
    ).fetchone()[0]
    assert n_snap == 0
    n_obs = conn.execute(
        "SELECT COUNT(*) FROM acquisition.marketplace_listing_observations"
    ).fetchone()[0]
    assert n_obs == 0


def test_collector_hard_budget_preauthorizes_each_call(conn):
    """Budget guard: spent + expected_cost must fit BEFORE the call."""
    _seed_identity(conn)
    universe = [{"event_key": "event::tm:test1", "watch_universe_version": "watch_universe_v1"}]
    col = _load_collector()

    def _boom(*a, **k):
        raise AssertionError("network call happened despite budget")

    col._monid_fast_snapshot = _boom
    # FAST cost is $0.0009; a $0.0005 budget must block the call.
    report = col.run_collect(
        conn, universe,
        source=None, fast=True, deep=False, max_cost=0.0005, max_fetch=None,
        wave_label="wave_test_budget", tickets_dev_live_key=False,
    )
    assert report["totals"]["snapshots"] == 0
    assert report["totals"]["fetches"] == 0
    assert report["totals"]["skipped_budget"] >= 1


def test_collector_budget_boundary_allows_exactly_fitting_call(conn):
    _seed_identity(conn)
    universe = [{"event_key": "event::tm:test1", "watch_universe_version": "watch_universe_v1"}]
    col = _load_collector()

    def _fake_fast(*a, **k):
        return {"snapshot": {
            "watch_universe_version": "watch_universe_v1",
            "event_key": "event::tm:test1",
            "source_platform": "seatgeek.com",
            "actor_or_endpoint": "monid_context.dev",
            "wave_label": "wave_test_budget",
            "observed_at": "2026-08-25T09:00:00+00:00",
            "retrieved_at": "2026-08-25T09:00:00+00:00",
            "knowledge_time": "2026-08-25T09:00:00+00:00",
            "currency": "USD",
            "resale_min_price": 101.0,
            "identity_match_status": "MATCHED",
            "identity_match_method": "MONID_URL_RESOLVED_JSONLD",
            "source_url": "https://seatgeek.com/x",
            "raw_payload_hash": None,
            "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY",
        }, "cost_usd": 0.0009, "provider": "monid_context.dev", "extracted": {}}

    col._monid_fast_snapshot = _fake_fast
    report = col.run_collect(
        conn, universe,
        source=None, fast=True, deep=False, max_cost=0.0009, max_fetch=None,
        wave_label="wave_test_budget2", tickets_dev_live_key=False,
    )
    assert report["totals"]["snapshots"] == 1
    assert report["totals"]["cost_usd"] == pytest.approx(0.0009)


def test_collector_reads_canonical_identity_master(conn):
    """The collector reads acquisition.event_identifiers — the same contract
    persist_mapping writes and the buyer view reads."""
    persist_mapping(conn, {
        "event_key": "event::tm:test1",
        "marketplace": "vividseats.com",
        "marketplace_event_id": "VS456",
        "marketplace_event_url": "https://www.vividseats.com/weatherday",
        "resolution_method": "monid_tinyfish_search",
        "resolution_status": "MATCHED_EXACT",
        "resolution_confidence": 0.9,
        "resolved_at": "2026-08-25T00:00:00+00:00",
    })
    col = _load_collector()
    mappings = col._load_mappings(conn)
    assert ("event::tm:test1", "vividseats.com") in mappings
    m = mappings[("event::tm:test1", "vividseats.com")]
    assert m["marketplace_event_url"] == "https://www.vividseats.com/weatherday"
    assert m["resolution_status"] == "EXACT_PAGE_MATCH"
    # The legacy table is written through, but canonical reads go to event_identifiers.
    n_legacy = conn.execute(
        "SELECT COUNT(*) FROM acquisition.marketplace_event_mappings WHERE marketplace = 'vividseats.com'"
    ).fetchone()[0]
    assert n_legacy == 1


def test_retry_wrapper_retries_once_then_succeeds():
    col = _load_collector()
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"error": "HTTP 503 capture_unavailable"}
        return {"status": "OK"}

    result, attempts = col._with_retry(flaky)
    assert result == {"status": "OK"}
    assert attempts == 2
    assert calls["n"] == 2


def test_retry_wrapper_does_not_retry_auth_failures():
    col = _load_collector()
    calls = {"n": 0}

    def auth_fail(*a, **k):
        calls["n"] += 1
        return {"error": "HTTP 401 invalid_key"}

    result, attempts = col._with_retry(auth_fail)
    assert attempts == 1
    assert calls["n"] == 1
    assert "invalid_key" in str(result.get("detail") or result.get("error"))


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


def test_buyer_1d_7d_windows_use_latest_obs_at_cutoff(evidence_conn, workspace_conn):
    """1D compares against the LATEST observation <= NOW-24h (T-1d), 7D
    against the LATEST <= NOW-7d (T-7d) — never the oldest ever (T-10d)."""
    event_key = _seed_watch_universe(evidence_conn)
    now = datetime(2026, 8, 25, 9, 0, 0, tzinfo=timezone.utc)
    # Observations at T-10d, T-7d, T-2d, T-1d and NOW, price rising each time.
    offsets = [timedelta(days=10), timedelta(days=7), timedelta(days=2),
               timedelta(days=1), timedelta(days=0)]
    for i, off in enumerate(offsets):
        ts = (now - off).isoformat()
        persist_snapshot(evidence_conn, {
            "watch_universe_version": "watch_universe_v1",
            "event_key": event_key,
            "source_platform": "seatgeek.com",
            "actor_or_endpoint": "monid_context_dev",
            "source_record_id": f"r{i}",
            "wave_label": f"w{i}",
            "observed_at": ts,
            "retrieved_at": ts,
            "knowledge_time": ts,
            "currency": "USD",
            "resale_min_price": float(10 + i * 10),  # 10,20,30,40,50
            "identity_match_status": "MATCHED",
            "identity_match_method": "ARTIST_VENUE_DATE",
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
        evidence_conn, workspace_conn,
        proposed_show_key=result["proposed_show_key"],
        evidence_conn=evidence_conn,
    )
    mh = view["ticket_market"]["market_history"]["seatgeek.com"]
    assert mh["now"]["min_price"] == 50.0
    # 1D baseline: T-1d (price 40), NOT T-10d (price 10).
    assert mh["1d"]["available"] is True
    assert mh["1d"]["previous"]["min_price"] == 40.0
    assert mh["1d"]["delta"]["min_price"]["previous"] == 40.0
    # 7D baseline: T-7d (price 20), NOT T-10d (price 10).
    assert mh["7d"]["available"] is True
    assert mh["7d"]["previous"]["min_price"] == 20.0


def test_buyer_source_health_scoped_to_event(evidence_conn, workspace_conn):
    """Source health on an event page is filtered to the event's marketplaces."""
    event_key = _seed_watch_universe(evidence_conn)
    _seed_multi_source_observations(evidence_conn, event_key)
    # Health rows for THIS event's marketplace + an unrelated marketplace.
    for mp in ("seatgeek.com", "stubhub.com"):
        evidence_conn.execute(
            """INSERT INTO acquisition.source_health_by_method (
                health_id, method, marketplace, wave_label, started_at, finished_at,
                status, events_requested, events_resolved, observations_ingested,
                latency_ms, cost_usd, schema_version
            ) VALUES (?, 'MONID_FAST', ?, 'w', ?, ?, 'SUCCESS', 1, 1, 1, 100, 0.0009, 'v2')""",
            [f"h::{mp}", mp, "2026-08-25T09:00:00+00:00", "2026-08-25T09:01:00+00:00"],
        )
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
    sh = view["ticket_market"]["source_health"]
    assert sh["status"] == "OBSERVED"
    assert sh["scope"] == "EVENT_MARKETPLACES"
    mps = {r["marketplace"] for r in sh["runs"]}
    assert mps == {"seatgeek.com"}  # stubhub health is NOT shown for this event


def test_buyer_view_identity_master_drilldown(evidence_conn, workspace_conn):
    """The buyer view's security-master drill-down reads the SAME
    event_identifiers row persist_mapping writes."""
    event_key = _seed_watch_universe(evidence_conn)
    persist_mapping(evidence_conn, {
        "event_key": event_key,
        "marketplace": "vividseats.com",
        "marketplace_event_id": "VS456",
        "marketplace_event_url": "https://www.vividseats.com/weatherday",
        "resolution_method": "monid_tinyfish_search",
        "resolution_status": "MATCHED_EXACT",
        "resolution_confidence": 0.9,
        "resolved_at": "2026-08-25T00:00:00+00:00",
    })
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
    ids = view["ticket_market"]["event_identifiers"]
    assert ids["status"] == "OBSERVED"
    row = [r for r in ids["identifiers"] if r["marketplace"] == "vividseats.com"][0]
    assert row["marketplace_event_url"] == "https://www.vividseats.com/weatherday"
    assert row["mapping_status"] == "EXACT_PAGE_MATCH"


def test_buyer_view_v2_graceful_without_041_tables(workspace_conn):
    """Without migration 041/042 tables the V2 additions must not crash the view."""
    # Use an evidence connection that only has migration 039 applied.
    c = duckdb.connect(":memory:")
    from festival_bloomberg.schema_paths import load_migration_files, load_schema_sql

    for stmt in split_sql_statements(load_schema_sql()):
        c.execute(stmt)
    for _version, _name, path in sorted(load_migration_files(), key=lambda t: t[0]):
        if path.name.startswith("040") or path.name.startswith("041") or path.name.startswith("042"):
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
