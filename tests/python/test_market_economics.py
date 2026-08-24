"""Offline regressions for market-economics evidence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from festival_bloomberg.acquisition.contracts import AcquisitionStatus
from festival_bloomberg.acquisition.providers import default_providers
from festival_bloomberg.acquisition.providers.seatgeek import SeatGeekProvider
from festival_bloomberg.acquisition.providers.ticketmaster import TicketmasterProvider
from festival_bloomberg.economics.capacity import (
    CapacityClaim,
    average_capacity,
    claim_from_wikidata,
    compute_utilization,
    mark_conflicts,
    select_applicable_capacity,
)
from festival_bloomberg.economics.compare import compare_primary_secondary
from festival_bloomberg.economics.outcomes import (
    historical_setlist_outcome,
    infer_sold_out_from_listing_count,
    infer_sold_out_from_offsale,
)
from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.economics.resolve import match_ticketmaster_seatgeek
from festival_bloomberg.economics.snapshots import (
    primary_snapshots_from_ticketmaster,
    secondary_snapshot_from_seatgeek,
)
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.warehouse.repository import FestivalRepository
from metrics.spread_calculator import MODULE_STATUS as SPREAD_STATUS
from scraper.seatgeek_adapter import MODULE_STATUS as ADAPTER_STATUS
from scraper.seatgeek_adapter import OFFICIAL_API as ADAPTER_OFFICIAL
from scraper.seatgeek_adapter import SeatGeekAdapter

from conftest import FakeTransport, make_request

T1 = datetime(2026, 8, 14, 15, 20, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 14, 16, 22, tzinfo=timezone.utc)
CUTOFF_2020 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _sg_event(**overrides):
    payload = {
        "id": 9001,
        "title": "Olivia Rodrigo",
        "url": "https://seatgeek.com/olivia-rodrigo-tickets/chicago",
        "datetime_local": "2026-10-11T19:30:00",
        "datetime_utc": "2026-10-12T00:30:00",
        "score": 0.42,
        "stats": {
            "listing_count": 80,
            "average_price": 175,
            "lowest_price": 90,
            "highest_price": 640,
        },
        "venue": {
            "id": 12,
            "name": "United Center",
            "city": "Chicago",
            "state": "IL",
            "country": "US",
            "location": {"lat": 41.88, "lon": -87.67},
        },
        "performers": [{"id": 77, "name": "Olivia Rodrigo", "primary": True}],
    }
    payload.update(overrides)
    return payload


def _tm_event(status="onsale"):
    return {
        "id": "TMOL1",
        "name": "Olivia Rodrigo: The Unraveled Tour",
        "url": "https://www.ticketmaster.com/event/TMOL1",
        "dates": {
            "start": {"localDate": "2026-10-11", "dateTime": "2026-10-12T00:30:00Z"},
            "status": {"code": status},
        },
        "sales": {"public": {"startDateTime": "2026-03-01T15:00:00Z", "endDateTime": "2026-10-11T23:00:00Z"}},
        "priceRanges": [{"type": "standard", "currency": "USD", "min": 49.5, "max": 299.0}],
        "_embedded": {
            "venues": [
                {
                    "id": "KovZpa2M7e",
                    "name": "United Center",
                    "city": {"name": "Chicago"},
                    "state": {"stateCode": "IL"},
                    "country": {"countryCode": "US"},
                }
            ],
            "attractions": [{"id": "K8vZ917_Su0", "name": "Olivia Rodrigo"}],
        },
    }


@pytest.fixture
def seatgeek_automation_enabled(monkeypatch):
    """Provider contract tests may run only after changing canonical disposition."""
    from festival_bloomberg.acquisition import automation

    monkeypatch.setitem(
        automation._DISPOSITIONS,
        "seatgeek",
        automation.AutomationStatus.ENABLED,
    )


def test_legacy_seatgeek_adapter_is_not_official_api():
    assert ADAPTER_OFFICIAL is False
    assert ADAPTER_STATUS == "LEGACY_EXTERNAL_LISTING_ADAPTER"
    assert SeatGeekAdapter.official_api is False
    assert default_providers()["seatgeek"].official_api is True


def test_official_seatgeek_absent_key_not_configured():
    provider = SeatGeekProvider(transport=FakeTransport([]), env={})
    result = provider.acquire(make_request(platform="seatgeek", operation="SEARCH_EVENTS"))
    assert result.status == AcquisitionStatus.POLICY_DENIED
    assert result.error_category == "automation_disabled"
    assert result.provider_metadata["automation_status"] == "AUTOMATION_DISABLED"
    assert provider.transport.requests == []


def test_official_seatgeek_event_stats_normalize(seatgeek_automation_enabled):
    payload = {"events": [_sg_event()], "meta": {"total": 1, "page": 1, "per_page": 20}}
    provider = SeatGeekProvider(
        transport=FakeTransport([(200, payload)]),
        env={"SEATGEEK_CLIENT_ID": "cid"},
    )
    result = provider.acquire(make_request(platform="seatgeek", operation="SEARCH_EVENTS", query="Olivia Rodrigo"))
    assert result.status == AcquisitionStatus.SUCCESS
    event = result.records[0]
    assert event["listing_count"] == 80
    assert event["lowest_price"] == 90
    assert event["provider_score"] == 0.42
    assert event["knowledge_time_source"] == "retrieval"
    assert "listing_id" not in event
    assert "section" not in event
    assert "row" not in event
    assert "quantity" not in event
    assert event.get("median_price") is None


def test_seatgeek_pagination_and_api_failure(seatgeek_automation_enabled):
    page1 = {"events": [_sg_event(id=1)], "meta": {"total": 2, "page": 1, "per_page": 1}}
    page2 = {"events": [_sg_event(id=2)], "meta": {"total": 2, "page": 2, "per_page": 1}}
    provider = SeatGeekProvider(
        transport=FakeTransport([(200, page1), (200, page2)]),
        env={"SEATGEEK_CLIENT_ID": "cid"},
    )
    result = provider.acquire(make_request(platform="seatgeek", max_records=10))
    assert result.record_count == 2
    fail = SeatGeekProvider(
        transport=FakeTransport([(500, {"error": "nope"})]),
        env={"SEATGEEK_CLIENT_ID": "cid"},
    )
    bad = fail.acquire(make_request(platform="seatgeek"))
    assert bad.status == AcquisitionStatus.PROVIDER_ERROR
    assert bad.record_count == 0


def test_two_seatgeek_snapshots_append_only(tmp_path):
    repo = FestivalRepository(str(tmp_path / "e.duckdb"))
    try:
        EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        record = {
            "seatgeek_event_id": 9001,
            "listing_count": 80,
            "lowest_price": 90,
            "average_price": 175,
            "highest_price": 640,
            "provider_score": 0.4,
            "canonical_url": "https://seatgeek.com/x",
            "retrieved_at": T1.isoformat(),
            "knowledge_time": T1.isoformat(),
        }
        first = secondary_snapshot_from_seatgeek(record, canonical_event_id="evt1", raw_observation_id="a")
        assert economics.insert_secondary_snapshot(first)
        later = dict(record)
        later["listing_count"] = 70
        later["retrieved_at"] = T2.isoformat()
        later["knowledge_time"] = T2.isoformat()
        second = secondary_snapshot_from_seatgeek(later, canonical_event_id="evt1", raw_observation_id="b")
        assert economics.insert_secondary_snapshot(second)
        rows = economics.query_secondary_snapshots(event_id="evt1")
        assert len(rows) == 2
        assert rows[0]["listing_count"] == 80
        assert rows[1]["listing_count"] == 70
        same_bucket = dict(later)
        same_bucket["listing_count"] = 1
        duplicate = secondary_snapshot_from_seatgeek(same_bucket, canonical_event_id="evt1", raw_observation_id="c")
        assert economics.insert_secondary_snapshot(duplicate) is False
        assert len(economics.query_secondary_snapshots(event_id="evt1")) == 2
    finally:
        repo.close()


def test_ticketmaster_price_ranges_and_status_normalize():
    payload = {"_embedded": {"events": [_tm_event("offsale")]}, "page": {"totalElements": 1, "totalPages": 1, "number": 0}}
    provider = TicketmasterProvider(
        transport=FakeTransport([(200, payload)]),
        env={"TICKETMASTER_API_KEY": "k"},
    )
    result = provider.acquire(make_request(platform="ticketmaster", operation="SEARCH_EVENTS"))
    event = result.records[0]
    assert event["price_ranges"][0]["min"] == 49.5
    assert event["event_status"] == "offsale"
    snaps = primary_snapshots_from_ticketmaster(event, canonical_event_id="evt1", raw_observation_id="raw")
    assert snaps[0].minimum_price == 49.5
    assert snaps[0].maximum_price == 299.0
    assert snaps[0].fees_included == "UNKNOWN"
    assert snaps[0].event_status == "offsale"
    assert snaps[0].knowledge_time == event["knowledge_time"]


def test_offsale_and_zero_listings_are_not_sold_out():
    assert infer_sold_out_from_offsale("OFFSALE") == "UNKNOWN"
    assert infer_sold_out_from_listing_count(0) == "UNKNOWN"


def test_capacity_claims_append_only_and_conflicts_preserved(tmp_path):
    repo = FestivalRepository(str(tmp_path / "c.duckdb"))
    try:
        economics = EconomicsRepository(repo.conn)
        a = CapacityClaim(
            claim_id="cap_a",
            canonical_venue_id="united-center",
            capacity_value=23500,
            capacity_kind="MAX_PERSONS",
            configuration_description=None,
            effective_from=None,
            effective_to=None,
            provider="wikidata_official_api",
            source="wikidata_p1083",
            source_url="https://www.wikidata.org/wiki/Q185578",
            source_publication_time=None,
            retrieved_at=T1.isoformat(),
            knowledge_time=T1.isoformat(),
            source_observation_id="Q185578",
            claim_status="OBSERVED",
            usage_label="MAXIMUM_CAPACITY_UPPER_BOUND",
        )
        b = CapacityClaim(
            claim_id="cap_b",
            canonical_venue_id="united-center",
            capacity_value=20917,
            capacity_kind="CONCERT",
            configuration_description="concert",
            effective_from=None,
            effective_to=None,
            provider="wikidata_official_api",
            source="wikidata_p1083",
            source_url="https://www.wikidata.org/wiki/Q185578",
            source_publication_time=None,
            retrieved_at=T1.isoformat(),
            knowledge_time=T1.isoformat(),
            source_observation_id="Q185578-concert",
            claim_status="OBSERVED",
        )
        assert economics.insert_capacity_claim(a)
        assert economics.insert_capacity_claim(a) is False
        assert economics.insert_capacity_claim(b)
        stored = economics.query_capacity_claims(venue_id="united-center")
        assert len(stored) == 2
        values = {row["capacity_value"] for row in stored}
        assert values == {23500.0, 20917.0}
        objects = [
            CapacityClaim(
                claim_id=row["claim_id"],
                canonical_venue_id=row["canonical_venue_id"],
                capacity_value=row["capacity_value"],
                capacity_kind=row["capacity_kind"],
                configuration_description=row.get("configuration_description"),
                effective_from=row.get("effective_from"),
                effective_to=row.get("effective_to"),
                provider=row["provider"],
                source=row["source"],
                source_url=row.get("source_url"),
                source_publication_time=None,
                retrieved_at=str(row["retrieved_at"]),
                knowledge_time=str(row["knowledge_time"]),
                source_observation_id=row.get("source_observation_id"),
                claim_status=row["claim_status"],
            )
            for row in stored
        ]
        marked = mark_conflicts(objects)
        assert {c.claim_status for c in marked} == {"CONFLICTING"}
        with pytest.raises(RuntimeError):
            average_capacity(marked)
        selected = select_applicable_capacity(marked, event_configuration="CONCERT")
        assert selected["capacity_value"] == 20917.0
        upper = select_applicable_capacity(
            [c for c in marked if c.capacity_kind == "MAX_PERSONS"],
            event_configuration=None,
        )
        assert upper["usage_label"] == "MAXIMUM_CAPACITY_UPPER_BOUND"
    finally:
        repo.close()


def test_utilization_requires_attendance_and_compatible_capacity():
    concert = CapacityClaim(
        claim_id="c1",
        canonical_venue_id="v",
        capacity_value=20000,
        capacity_kind="CONCERT",
        configuration_description="concert",
        effective_from=None,
        effective_to=None,
        provider="wikidata_official_api",
        source="wikidata_p1083",
        source_url=None,
        source_publication_time=None,
        retrieved_at=T1.isoformat(),
        knowledge_time=T1.isoformat(),
        source_observation_id="x",
        claim_status="OBSERVED",
    )
    applicable = select_applicable_capacity([concert], event_configuration="CONCERT")
    missing = compute_utilization(attendance_value=None, applicable_capacity=applicable)
    assert missing["status"] == "UNKNOWN"
    present = compute_utilization(attendance_value=18000, applicable_capacity=applicable)
    assert present["status"] == "COMPUTED"
    assert present["utilization"] == 0.9


def test_historical_setlist_does_not_fabricate_price_or_attendance():
    outcome = historical_setlist_outcome(
        event_id="evt_hist",
        has_setlist_observation=True,
        retrieved_at=T1.isoformat(),
        knowledge_time=T1.isoformat(),
        observation_ids=["obs1"],
    )
    assert outcome.performance_recorded_by_setlistfm is True
    assert outcome.attendance_value is None
    assert outcome.sold_out_status == "UNKNOWN"
    assert outcome.event_status == "COMPLETED_UNKNOWN"


def test_source_event_time_is_not_knowledge_time():
    record = {
        "ticketmaster_event_id": "TM1",
        "event_time": "2025-08-20T00:00:00Z",
        "event_status": "onsale",
        "price_ranges": [{"currency": "USD", "min": 10, "max": 20}],
        "retrieved_at": T1.isoformat(),
        "knowledge_time": T1.isoformat(),
        "canonical_url": "https://ticketmaster.com/e",
    }
    snap = primary_snapshots_from_ticketmaster(record, canonical_event_id="e", raw_observation_id="r")[0]
    assert snap.knowledge_time == T1.isoformat()
    assert "2025-08-20" not in snap.knowledge_time


def test_current_snapshots_excluded_from_old_pit_cutoff(tmp_path):
    repo = FestivalRepository(str(tmp_path / "pit.duckdb"))
    try:
        economics = EconomicsRepository(repo.conn)
        tm = primary_snapshots_from_ticketmaster(
            {
                "ticketmaster_event_id": "TM1",
                "price_ranges": [{"currency": "USD", "min": 10, "max": 20}],
                "event_status": "onsale",
                "retrieved_at": T1.isoformat(),
                "knowledge_time": T1.isoformat(),
            },
            canonical_event_id="e1",
            raw_observation_id="r",
        )[0]
        economics.insert_primary_snapshot(tm)
        sg = secondary_snapshot_from_seatgeek(
            {
                "seatgeek_event_id": 1,
                "listing_count": 3,
                "lowest_price": 11,
                "retrieved_at": T1.isoformat(),
                "knowledge_time": T1.isoformat(),
            },
            canonical_event_id="e1",
            raw_observation_id="s",
        )
        economics.insert_secondary_snapshot(sg)
        assert economics.query_primary_snapshots(event_id="e1", cutoff=CUTOFF_2020) == []
        assert economics.query_secondary_snapshots(event_id="e1", cutoff=CUTOFF_2020) == []
        assert len(economics.query_primary_snapshots(event_id="e1", cutoff=T1)) == 1
        claim = CapacityClaim(
            claim_id="cap_now",
            canonical_venue_id="v",
            capacity_value=100,
            capacity_kind="MAX_PERSONS",
            configuration_description=None,
            effective_from=None,
            effective_to=None,
            provider="wikidata_official_api",
            source="wikidata_p1083",
            source_url=None,
            source_publication_time=None,
            retrieved_at=T1.isoformat(),
            knowledge_time=T1.isoformat(),
            source_observation_id="q",
            claim_status="OBSERVED",
        )
        economics.insert_capacity_claim(claim)
        assert economics.query_capacity_claims(venue_id="v", cutoff=CUTOFF_2020) == []
    finally:
        repo.close()


def test_primary_secondary_comparison_incompatible_fees_and_fx():
    primary = {
        "snapshot_id": "p",
        "currency": "USD",
        "fees_included": "UNKNOWN",
        "minimum_price": 50,
        "maximum_price": 200,
        "retrieved_at": T1.isoformat(),
    }
    secondary = {
        "snapshot_id": "s",
        "currency": "USD",
        "fees_included": "UNKNOWN",
        "lowest_price": 90,
        "average_price": 175,
        "highest_price": 640,
        "retrieved_at": T2.isoformat(),
    }
    cmp_ = compare_primary_secondary(primary, secondary)
    assert cmp_["concept"] == "PRIMARY_SECONDARY_MARKET_COMPARISON"
    assert cmp_["arbitrage_candidate"] is False
    assert cmp_["fee_comparability"] == "UNKNOWN"
    assert cmp_["status"] == "PARTIALLY_COMPARABLE"
    fx_missing = compare_primary_secondary(
        {**primary, "currency": "USD"},
        {**secondary, "currency": "EUR"},
    )
    assert fx_missing["fx_conversion"] == "UNKNOWN"
    assert fx_missing["status"] == "NOT_COMPARABLE"
    assert fx_missing["no_1_to_1_fx_fallback"] is True


def test_legacy_arbitrage_excluded_from_production_economics():
    assert SPREAD_STATUS == "LEGACY_EXPERIMENTAL"
    import festival_bloomberg.economics as econ

    assert not hasattr(econ, "calculate_spread")
    assert not hasattr(econ, "arbitrage_candidate")


def test_artist_date_different_venue_does_not_match():
    tm = {
        "ticketmaster_event_id": "A",
        "local_date": "2026-10-11",
        "venue_name": "United Center",
        "city": "Chicago",
    }
    sg = {
        "seatgeek_event_id": "B",
        "local_date": "2026-10-11",
        "venue_name": "Wrigley Field",
        "city": "Chicago",
    }
    assert (
        match_ticketmaster_seatgeek(
            ticketmaster=tm,
            seatgeek=sg,
            canonical_event_id="e",
            artist_id="olivia-rodrigo",
        )
        is None
    )


def test_same_artist_date_venue_matches():
    tm = {
        "ticketmaster_event_id": "A",
        "local_date": "2026-10-11",
        "venue_name": "United Center",
        "city": "Chicago",
    }
    sg = {
        "seatgeek_event_id": "B",
        "local_date": "2026-10-11",
        "venue_name": "United Center",
        "city": "Chicago",
    }
    match = match_ticketmaster_seatgeek(
        ticketmaster=tm,
        seatgeek=sg,
        canonical_event_id="e",
        artist_id="olivia-rodrigo",
    )
    assert match is not None
    assert match.gate == "GATE_2_ARTIST_DATE_VENUE"


def test_wikidata_claim_parser_preserves_upper_bound():
    record = {
        "capacity_value": 23500,
        "capacity_kind": "MAX_PERSONS",
        "wikidata_qid": "Q185578",
        "wikidata_rank": "normal",
        "wikidata_unit": "1",
        "wikidata_qualifiers": {},
        "retrieved_at": T1.isoformat(),
        "knowledge_time": T1.isoformat(),
        "platform_object_id": "Q185578$1",
        "canonical_url": "https://www.wikidata.org/wiki/Q185578",
    }
    claim = claim_from_wikidata(record, venue_id="united-center")
    assert claim is not None
    assert claim.usage_label == "MAXIMUM_CAPACITY_UPPER_BOUND"
    assert claim.capacity_value == 23500
