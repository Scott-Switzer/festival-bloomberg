"""Regression coverage for LIVE_DATA_ACTIVATION_AND_INTELLIGENCE_SCALE_V1.

Covers:
- Spotify identity resolution: deterministic matching, no forced merge,
  append-only external ids, EXACT-only promotion.
- Ticketmaster normalization: presales/public onsale separated, offsale !=
  sold_out, price range is an observation, promoter captured.
- Provider event snapshots -> activity tape derivation (idempotent).
- NWS weather snapshot semantics (generation time != validity window).
- NVIDIA router defect fix (catalog-aware routing, fail-closed).
"""

from __future__ import annotations

import json

import duckdb
import pytest

from festival_bloomberg.acquisition.contracts import AcquisitionRequest
from festival_bloomberg.acquisition.providers.ticketmaster import TicketmasterProvider
from festival_bloomberg.identity.spotify import (
    build_rows,
    classify,
    normalize_name,
    persist_exact_external_ids,
    persist_resolutions,
)
from festival_bloomberg.intelligence.llm import ModelRouter
from festival_bloomberg.intelligence.tape import (
    derive_provider_event_tape_entries,
    insert_tape_entries,
)
from festival_bloomberg.migrations import apply_pending_migrations

from conftest import FakeTransport, make_request


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "live.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Spotify identity resolution
# ---------------------------------------------------------------------------
def test_normalize_name():
    assert normalize_name("The Jimi Hendrix Experience") == "jimi hendrix experience"
    assert normalize_name("Blood, Sweat & Tears") == "blood sweat tears"
    assert normalize_name("  Guns N' Roses  ") == "guns n roses"


def test_classify_exact_ambiguous_no_match():
    exact = classify("Taylor Swift", [{"id": "x", "name": "Taylor Swift", "external_urls": {"spotify": "s"}}])
    assert exact[0]["resolution_status"] == "EXACT"
    assert exact[0]["match_similarity"] == 1.0

    amb = classify("Bob Wilber Sextet", [{"id": "y", "name": "Bob Wilber"}])
    assert all(r["resolution_status"] == "AMBIGUOUS" for r in amb)

    none = classify("Nobody At All", [])
    assert none[0]["resolution_status"] == "NO_MATCH"
    assert none[0]["spotify_id"] is None


def test_resolution_persistence_is_append_only_and_exact_only(conn):
    retrieved = "2026-08-15T12:00:00+00:00"
    exact = build_rows("core.lineup_slots", "Jimi Hendrix", [
        {"id": "sp1", "name": "Jimi Hendrix", "uri": "spotify:artist:sp1",
         "external_urls": {"spotify": "https://open.spotify.com/artist/sp1"}},
    ], retrieved)
    amb = build_rows("core.lineup_slots", "Jimi Hendrix Experience", [
        {"id": "sp2", "name": "Jimi Hendrix and Friends"},
    ], retrieved)

    assert persist_resolutions(conn, exact + amb) == len(exact + amb)
    # idempotent
    assert persist_resolutions(conn, exact + amb) == 0
    assert persist_exact_external_ids(conn, exact + amb) == 1  # only EXACT promoted

    # Only the EXACT match is a spotify external id; the ambiguous one is not.
    n = conn.execute(
        "SELECT COUNT(*) FROM core.entity_external_ids WHERE id_type='spotify'"
    ).fetchone()[0]
    assert n == 1
    # The ambiguous resolution is persisted as a candidate but never merged.
    amb_rows = conn.execute(
        "SELECT resolution_status FROM identity.spotify_artist_resolutions "
        "WHERE normalized_local_name = 'jimi hendrix experience'"
    ).fetchall()
    assert [r[0] for r in amb_rows] == ["AMBIGUOUS"]


# ---------------------------------------------------------------------------
# Ticketmaster normalization
# ---------------------------------------------------------------------------
def _tm_payload(status="onsale"):
    return {
        "id": "tm-evt-1",
        "name": "Taylor Swift | The Eras Tour",
        "url": "https://www.ticketmaster.com/event/tm-evt-1",
        "source": "universe",
        "dates": {
            "start": {"localDate": "2026-11-01", "localTime": "20:00:00",
                      "dateTime": "2026-11-02T01:00:00Z"},
            "status": {"code": status},
            "timezone": "America/Chicago",
        },
        "sales": {
            "public": {"startDateTime": "2026-05-01T10:00:00Z",
                       "endDateTime": "2026-11-01T00:00:00Z"},
            "presales": [{"name": "Fan Presale", "startDateTime": "2026-04-28T10:00:00Z",
                          "endDateTime": "2026-04-30T10:00:00Z"}],
        },
        "priceRanges": [{"type": "standard", "currency": "USD", "min": 49.5, "max": 499.5}],
        "promoter": {"name": "Live Nation"},
        "classifications": [{"primary": True, "segment": {"name": "Music"},
                             "genre": {"name": "Rock"}, "subgenre": {"name": "Pop"},
                             "type": {"name": "Concert"}}],
        "_embedded": {
            "venues": [{"id": "v1", "name": "United Center",
                        "city": {"name": "Chicago"},
                        "state": {"name": "Illinois", "stateCode": "IL"},
                        "country": {"name": "United States", "countryCode": "US"},
                        "location": {"latitude": "41.8807", "longitude": "-87.6742"}}],
            "attractions": [{"id": "a1", "name": "Taylor Swift"}],
        },
    }


def test_ticketmaster_normalize_presales_onsale_price_promoter():
    provider = TicketmasterProvider(transport=FakeTransport(), env={})
    req = make_request(query="", platform="ticketmaster", operation="SEARCH_EVENTS")
    rec = provider._normalize_event(_tm_payload(), "2026-08-15T12:00:00+00:00", req)
    assert rec["onsale_start"] == "2026-05-01T10:00:00Z"
    assert rec["presales"][0]["name"] == "Fan Presale"  # presale independent of public onsale
    assert rec["price_min"] == 49.5 and rec["price_max"] == 499.5
    assert rec["price_type"] == "standard"
    assert rec["promoter"] == "Live Nation"
    assert rec["event_status"] == "onsale"


def test_ticketmaster_offsale_is_not_sold_out():
    provider = TicketmasterProvider(transport=FakeTransport(), env={})
    req = make_request(query="", platform="ticketmaster", operation="SEARCH_EVENTS")
    rec = provider._normalize_event(_tm_payload(status="offsale"), "2026-08-15T12:00:00+00:00", req)
    assert rec["event_status"] == "offsale"  # literal provider status, never invented
    assert rec["event_status"] != "sold_out"


def test_ticketmaster_search_includes_classification_and_country():
    provider = TicketmasterProvider(
        transport=FakeTransport([
            (200, {"page": {"totalElements": 0, "totalPages": 0}, "_embedded": {"events": []}}),
        ]),
        env={"TICKETMASTER_API_KEY": "test-key"},
    )
    req = AcquisitionRequest.new(
        entity_id="chicago", entity_type="market", platform="ticketmaster", query="",
        market_id="Chicago,IL,US", classification_name="Music", max_records=10,
        operation="SEARCH_EVENTS", commercial_context="research",
    )
    provider.acquire(req)
    params = provider.transport.requests[0]["params"]
    assert params["classificationName"] == "Music"
    assert params["countryCode"] == "US"
    assert params["city"] == "Chicago"


# ---------------------------------------------------------------------------
# Provider event snapshots -> tape derivation
# ---------------------------------------------------------------------------
def _insert_snapshot(conn, *, key, event_id, retrieved, status=None, onsale=None,
                     presales=None, pmin=None, pmax=None, promoter=None, ldate=None):
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, artist_name,
             venue_name, city, state_code, country_code, local_date, event_status,
             onsale_start, presales, price_min, price_max, promoter, retrieved_at,
             knowledge_time, rights_status, commercial_use_status, software_version, ingested_at)
        VALUES (?, 'ticketmaster', ?, 'Test Event', 'Test Artist', 'Test Venue',
                'Chicago', 'IL', 'US', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RESEARCH_ONLY',
                'PROTOTYPE_ONLY', 'test', CURRENT_TIMESTAMP)
        """,
        [key, event_id, ldate, status, onsale, json.dumps(presales or []),
         pmin, pmax, promoter, retrieved, retrieved],
    )


def test_tape_derivation_discovers_event_onsale_price_promoter(conn):
    _insert_snapshot(conn, key="s1", event_id="tm-1", retrieved="2026-08-15T10:00:00+00:00",
                     status="onsale", onsale="2026-05-01T10:00:00Z",
                     presales=[{"name": "Fan"}], pmin=49.5, pmax=99.5,
                     promoter="Live Nation", ldate="2026-11-01")
    rows = derive_provider_event_tape_entries(conn)
    types = {r["activity_type"] for r in rows}
    assert "EVENT_DISCOVERED" in types
    assert "ONSALE_DISCOVERED" in types
    assert "PRESALE_DISCOVERED" in types
    assert "PRICE_RANGE_DISCOVERED" in types
    assert "PROMOTER_IDENTIFIED" in types


def test_tape_derivation_is_idempotent_and_detects_status_change(conn):
    _insert_snapshot(conn, key="s1", event_id="tm-2", retrieved="2026-08-15T10:00:00+00:00",
                     status="onsale", ldate="2026-11-01")
    first = insert_tape_entries(conn, derive_provider_event_tape_entries(conn))
    conn.commit()
    # unchanged re-derivation writes nothing new
    assert insert_tape_entries(conn, derive_provider_event_tape_entries(conn)) == 0
    # a later snapshot with a cancellation status emits EVENT_CANCELLED
    _insert_snapshot(conn, key="s2", event_id="tm-2", retrieved="2026-08-16T10:00:00+00:00",
                     status="cancelled", ldate="2026-11-01")
    rows = derive_provider_event_tape_entries(conn)
    types = {r["activity_type"] for r in rows}
    assert "EVENT_CANCELLED" in types
    assert first > 0


# ---------------------------------------------------------------------------
# Weather snapshot semantics
# ---------------------------------------------------------------------------
def test_weather_snapshot_keeps_generation_separate_from_validity(conn):
    conn.execute(
        """
        INSERT INTO events.weather_forecast_snapshots
            (forecast_key, event_ref, venue_latitude, venue_longitude, generation_time,
             valid_start, valid_end, temperature, temperature_unit,
             precipitation_probability, wind_speed, short_forecast, source_url,
             retrieved_at, knowledge_time, rights_status, commercial_use_status,
             software_version, ingested_at)
        VALUES ('fk1', 'tm-1', 41.88, -87.67, TIMESTAMP '2026-08-15 12:00:00',
                TIMESTAMP '2026-08-16 00:00:00', TIMESTAMP '2026-08-16 12:00:00',
                72, 'F', 20, '10 mph', 'Partly Cloudy', 'https://api.weather.gov/forecast',
                TIMESTAMP '2026-08-15 12:05:00', TIMESTAMP '2026-08-15 12:05:00',
                'PUBLIC_DOMAIN', 'RESEARCH_ONLY', 'test', CURRENT_TIMESTAMP)
        """
    )
    row = conn.execute(
        "SELECT generation_time, valid_start, valid_end FROM events.weather_forecast_snapshots WHERE forecast_key='fk1'"
    ).fetchone()
    gen, start, end = row
    # forecast issue time is NOT collapsed into the validity window
    assert gen != start
    assert start != end


# ---------------------------------------------------------------------------
# NVIDIA router defect fix (catalog-aware, fail-closed)
# ---------------------------------------------------------------------------
def test_router_never_returns_off_catalog_model():
    catalog = ["deepseek-ai/deepseek-v4-flash-0731"]
    router = ModelRouter(catalog=catalog)
    # DEEP_REASON hint matches the catalog model
    assert router.route("DEEP_REASON") == "deepseek-ai/deepseek-v4-flash-0731"
    # RERANK has no catalog model and no fallback -> fail closed
    assert router.route("RERANK") == ModelRouter.UNAVAILABLE


def test_router_override_absent_from_catalog_is_skipped():
    router = ModelRouter(catalog=["meta/llama-3.3-70b-instruct"],
                         tasks={"FAST_EXTRACT": "ghost/model"})
    assert router.route("FAST_EXTRACT") == "meta/llama-3.3-70b-instruct"
