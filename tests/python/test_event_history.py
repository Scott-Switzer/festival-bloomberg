"""Offline regressions for Ticketmaster + Setlist.fm event history."""

from __future__ import annotations

from datetime import datetime, timezone

from festival_bloomberg.acquisition.contracts import AcquisitionStatus
from festival_bloomberg.acquisition.providers.setlistfm import SetlistFmProvider
from festival_bloomberg.acquisition.providers.ticketmaster import TicketmasterProvider
from festival_bloomberg.events.fan_link import event_linked_fan_status, link_video_to_events
from festival_bloomberg.events.features import build_artist_market_vector
from festival_bloomberg.events.identity import (
    merge_identity,
    resolve_setlist_artists,
    resolve_ticketmaster_attractions,
)
from festival_bloomberg.events.reconcile import ProviderEvent, reconcile_events
from festival_bloomberg.events.repository import EventRepository, provider_event_from_record
from festival_bloomberg.evidence.provenance import retrieval_knowledge_time
from festival_bloomberg.evidence.repository import EvidenceRepository
from festival_bloomberg.evidence.semantics import ContentRole
from festival_bloomberg.markets.chicago import chicago_from_structured_geo
from festival_bloomberg.social.sampling import CAPPED, COMPLETE, sampling_status
from festival_bloomberg.warehouse.repository import FestivalRepository

from conftest import FakeTransport, make_request


def _tm_event(city="Chicago", state_code="IL", venue="United Center", date="2026-08-20", event_id="TM1"):
    return {
        "id": event_id,
        "name": "Bad Bunny Chicago",
        "url": f"https://www.ticketmaster.com/event/{event_id}",
        "dates": {
            "start": {"localDate": date, "localTime": "19:00:00", "dateTime": f"{date}T00:00:00Z"},
            "status": {"code": "onsale"},
            "timezone": "America/Chicago",
        },
        "classifications": [{"primary": True, "type": {"name": "Concert"}, "segment": {"name": "Music"}}],
        "priceRanges": [{"currency": "USD", "min": 50.0, "max": 200.0}],
        "_embedded": {
            "venues": [
                {
                    "id": "V1",
                    "name": venue,
                    "city": {"name": city},
                    "state": {"name": "Illinois", "stateCode": state_code},
                    "country": {"name": "United States", "countryCode": "US"},
                    "location": {"latitude": "41.88", "longitude": "-87.67"},
                }
            ],
            "attractions": [{"id": "K8vZ917", "name": "Bad Bunny"}],
        },
    }


def _setlist(city="Chicago", venue="United Center", date="23-08-2024", setlist_id="SL1"):
    return {
        "id": setlist_id,
        "versionId": "ver1",
        "eventDate": date,
        "lastUpdated": "2024-09-01T00:00:00.000+0000",
        "url": f"https://www.setlist.fm/setlist/{setlist_id}",
        "artist": {"mbid": "mbid-bb", "name": "Bad Bunny", "sortName": "Bunny, Bad", "disambiguation": ""},
        "venue": {
            "id": "sv1",
            "name": venue,
            "city": {
                "id": "5128581",
                "name": city,
                "state": "Illinois",
                "stateCode": "IL",
                "coords": {"lat": 41.85, "long": -87.65},
                "country": {"code": "US", "name": "United States"},
            },
        },
        "tour": {"name": "Most Wanted Tour"},
        "set": [{"song": [{"name": "A"}, {"name": "B"}], "encore": 1}],
    }


def test_ticketmaster_absent_key_not_configured():
    provider = TicketmasterProvider(transport=FakeTransport([]), env={})
    result = provider.acquire(make_request(platform="ticketmaster", operation="SEARCH_EVENTS"))
    assert result.status == AcquisitionStatus.NOT_CONFIGURED


def test_ticketmaster_fixture_event_normalization():
    payload = {"_embedded": {"events": [_tm_event()]}, "page": {"totalElements": 1, "totalPages": 1, "number": 0}}
    provider = TicketmasterProvider(
        transport=FakeTransport([(200, payload)]),
        env={"TICKETMASTER_API_KEY": "k"},
    )
    result = provider.acquire(make_request(platform="ticketmaster", operation="SEARCH_EVENTS", query="Bad Bunny"))
    assert result.status == AcquisitionStatus.SUCCESS
    event = result.records[0]
    assert event["ticketmaster_event_id"] == "TM1"
    assert event["city"] == "Chicago"
    assert event["knowledge_time_source"] == "retrieval"
    assert event["content_role"] == ContentRole.EVENT_LISTING.value
    assert event["price_ranges"][0]["min"] == 50.0


def test_ticketmaster_pagination():
    page0 = {
        "_embedded": {"events": [_tm_event(event_id="A")]},
        "page": {"totalElements": 2, "totalPages": 2, "number": 0, "size": 1},
    }
    page1 = {
        "_embedded": {"events": [_tm_event(event_id="B")]},
        "page": {"totalElements": 2, "totalPages": 2, "number": 1, "size": 1},
    }
    transport = FakeTransport([(200, page0), (200, page1)])
    provider = TicketmasterProvider(transport=transport, env={"TICKETMASTER_API_KEY": "k"})
    result = provider.acquire(make_request(platform="ticketmaster", max_records=10))
    assert result.record_count == 2
    assert result.provider_metadata["pagination"]["pages_fetched"] == 2


def test_ticketmaster_api_failure_is_not_empty_success():
    provider = TicketmasterProvider(
        transport=FakeTransport([(500, {"fault": "nope"})]),
        env={"TICKETMASTER_API_KEY": "k"},
    )
    result = provider.acquire(make_request(platform="ticketmaster"))
    assert result.status != AcquisitionStatus.SUCCESS
    assert result.record_count == 0


def test_setlist_absent_key_not_configured():
    provider = SetlistFmProvider(transport=FakeTransport([]), env={})
    result = provider.acquire(make_request(platform="setlistfm", operation="SEARCH_SETLISTS"))
    assert result.status == AcquisitionStatus.NOT_CONFIGURED


def test_setlist_artist_mbid_normalization():
    payload = {
        "artist": [{"mbid": "mbid-bb", "name": "Bad Bunny", "sortName": "Bunny, Bad", "disambiguation": ""}],
        "total": 1,
        "page": 1,
    }
    provider = SetlistFmProvider(transport=FakeTransport([(200, payload)]), env={"SETLISTFM_API_KEY": "k"})
    result = provider.acquire(make_request(platform="setlistfm", operation="SEARCH_ARTISTS", query="Bad Bunny"))
    artist = result.records[0]
    assert artist["artist_mbid"] == "mbid-bb"
    assert artist["content_role"] == ContentRole.PERFORMANCE_HISTORY.value
    assert artist["content_role"] != ContentRole.FAN_GENERATED.value


def test_setlist_search_setlists_normalization():
    payload = {"setlist": [_setlist()], "total": 1, "page": 1, "itemsPerPage": 20}
    provider = SetlistFmProvider(transport=FakeTransport([(200, payload)]), env={"SETLISTFM_API_KEY": "k"})
    result = provider.acquire(
        make_request(platform="setlistfm", operation="SEARCH_SETLISTS", external_id="mbid-bb", market_id="Chicago, IL")
    )
    rec = result.records[0]
    assert rec["local_date"] == "2024-08-23"
    assert rec["source_updated_at"]
    assert rec["knowledge_time"] != rec["local_date"]
    assert rec["knowledge_time"] != rec["source_updated_at"]
    assert rec["event_type"] == "TOUR_DATE"


def test_setlist_pagination():
    page1 = {"setlist": [_setlist(setlist_id="a")], "total": 2, "page": 1, "itemsPerPage": 1}
    page2 = {"setlist": [_setlist(setlist_id="b")], "total": 2, "page": 2, "itemsPerPage": 1}
    provider = SetlistFmProvider(transport=FakeTransport([(200, page1), (200, page2)]), env={"SETLISTFM_API_KEY": "k"})
    result = provider.acquire(make_request(platform="setlistfm", max_records=10, external_id="mbid"))
    assert result.record_count == 2
    assert result.provider_metadata["pagination"]["pages_fetched"] == 2


def test_event_date_is_not_knowledge_time():
    retrieved = datetime(2026, 8, 14, tzinfo=timezone.utc)
    kt = retrieval_knowledge_time(retrieved)
    assert kt.year == 2026
    event_time = datetime(2025, 6, 1, tzinfo=timezone.utc)
    assert event_time != kt


def test_old_event_fetched_today_excluded_from_historical_pit(tmp_path):
    repo = FestivalRepository(str(tmp_path / "pit.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        provider = SetlistFmProvider(
            transport=FakeTransport(
                [(200, {"setlist": [_setlist(date="01-06-2019")], "total": 1, "page": 1, "itemsPerPage": 20})]
            ),
            env={"SETLISTFM_API_KEY": "k"},
        )
        request = make_request(
            platform="setlistfm",
            entity_id="bad-bunny",
            operation="SEARCH_SETLISTS",
            correlation_id="evt-oa",
        )
        result = provider.acquire(request)
        # Force retrieval timestamp via ingest completed_at
        from dataclasses import replace

        result = replace(result, completed_at=datetime(2026, 8, 14, tzinfo=timezone.utc))
        evidence.ingest(request, result)
        cutoff = datetime(2020, 1, 1, tzinfo=timezone.utc)
        visible = evidence.query_observations(artist_id="bad-bunny", cutoff=cutoff, correlation_id="evt-oa")
        assert visible == []
        later = evidence.query_observations(
            artist_id="bad-bunny",
            cutoff=datetime(2026, 8, 14, tzinfo=timezone.utc),
            correlation_id="evt-oa",
        )
        assert later
    finally:
        repo.close()


def test_exact_mbid_artist_resolution():
    match, method, amb = resolve_setlist_artists(
        "Bad Bunny",
        [{"artist_name": "Bad Bunny", "artist_mbid": "mbid-bb"}],
    )
    assert match["artist_mbid"] == "mbid-bb"
    assert method == "EXACT_MBID"
    assert not amb


def test_exact_ticketmaster_attraction_resolution():
    match, method, amb = resolve_ticketmaster_attractions(
        "Bad Bunny",
        [{"attraction_name": "Bad Bunny", "ticketmaster_attraction_id": "K8"}],
    )
    assert match["ticketmaster_attraction_id"] == "K8"
    assert method == "EXACT_PLATFORM_ID"
    assert not amb


def test_ambiguous_artist_remains_unresolved():
    match, method, amb = resolve_setlist_artists(
        "Drake",
        [
            {"artist_name": "Drake", "artist_mbid": "a"},
            {"artist_name": "Drake", "artist_mbid": "b"},
        ],
    )
    assert match is None
    assert method == "UNRESOLVED"
    assert amb
    identity = merge_identity("Drake", setlist=(match, method, amb), ticketmaster=(None, "UNRESOLVED", []))
    assert identity.resolution_method == "UNRESOLVED"


def test_same_provider_id_dedups(tmp_path):
    repo = FestivalRepository(str(tmp_path / "dedup.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        payload = {"_embedded": {"events": [_tm_event()]}, "page": {"totalElements": 1, "totalPages": 1, "number": 0}}
        provider = TicketmasterProvider(transport=FakeTransport([(200, payload), (200, payload)]), env={"TICKETMASTER_API_KEY": "k"})
        req = make_request(platform="ticketmaster", entity_id="bad-bunny")
        evidence.ingest(req, provider.acquire(req))
        evidence.ingest(req, provider.acquire(req))
        rows = evidence.conn.execute("SELECT COUNT(*) FROM acquisition.social_observations").fetchone()[0]
        assert rows == 1
    finally:
        repo.close()


def _pe(**kwargs):
    defaults = dict(
        provider="ticketmaster",
        platform="ticketmaster",
        platform_object_id="TM1",
        artist_id="bad-bunny",
        event_name="Show",
        local_date="2024-08-23",
        venue_id=None,
        venue_name="United Center",
        city="Chicago",
        event_type="UNKNOWN",
        tour_name=None,
        festival_name=None,
        raw_observation_id="raw1",
        knowledge_time="2026-08-14T00:00:00+00:00",
        payload={"market_id": "Chicago, IL", "state": "Illinois", "country_code": "US"},
    )
    defaults.update(kwargs)
    return ProviderEvent(**defaults)


def test_same_artist_date_venue_reconciles():
    clusters = reconcile_events(
        [
            _pe(provider="ticketmaster", platform="ticketmaster", platform_object_id="TM1"),
            _pe(provider="setlistfm_official_api", platform="setlistfm", platform_object_id="SL1", venue_name="United Center"),
        ]
    )
    matched = [c for c in clusters if c.match_gate.startswith("GATE_")]
    assert len(matched) == 1
    assert len(matched[0].members) == 2


def test_artist_date_different_venue_does_not_auto_reconcile():
    clusters = reconcile_events(
        [
            _pe(venue_name="United Center", platform_object_id="TM1"),
            _pe(
                provider="setlistfm_official_api",
                platform="setlistfm",
                platform_object_id="SL1",
                venue_name="Soldier Field",
            ),
        ]
    )
    matched = [c for c in clusters if c.match_gate.startswith("GATE_2") or c.match_gate.startswith("GATE_3")]
    assert matched == []
    assert len(clusters) == 2


def test_both_raw_observations_survive_and_disagreements_preserved(tmp_path):
    repo = FestivalRepository(str(tmp_path / "rec.duckdb"))
    try:
        events_repo = EventRepository(repo.conn)
        clusters = reconcile_events(
            [
                _pe(event_name="TM name", tour_name="Tour A"),
                _pe(
                    provider="setlistfm_official_api",
                    platform="setlistfm",
                    platform_object_id="SL1",
                    event_name="SL name",
                    tour_name="Tour B",
                ),
            ]
        )
        cluster = [c for c in clusters if c.match_gate.startswith("GATE_")][0]
        events_repo.store_reconciled(cluster, artist_id="bad-bunny", retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc))
        obs = events_repo.query_provider_observations(cluster.event_id)
        assert len(obs) == 2
        disagreements = events_repo.query_disagreements(cluster.event_id)
        assert any(d["dimension"] == "tour" for d in disagreements)
    finally:
        repo.close()


def test_chicago_city_strictness_and_nearby_excluded():
    chi = chicago_from_structured_geo(city="Chicago", state_code="IL", country_code="US")
    assert chi.is_chicago
    rose = chicago_from_structured_geo(city="Rosemont", state_code="IL", country_code="US")
    assert not rose.is_chicago
    tinley = chicago_from_structured_geo(city="Tinley Park", state_code="IL", country_code="US")
    assert not tinley.is_chicago


def test_real_chicago_event_artist_market_relation_is_not_demand(tmp_path):
    repo = FestivalRepository(str(tmp_path / "rel.duckdb"))
    try:
        events_repo = EventRepository(repo.conn)
        cluster = reconcile_events([_pe()])[0]
        events_repo.store_reconciled(cluster, artist_id="bad-bunny", retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc))
        stored = events_repo.query_events(artist_id="bad-bunny", market_id="Chicago, IL")
        events_repo.upsert_artist_market_relation(
            artist_id="bad-bunny",
            market_id="Chicago, IL",
            events=stored,
            knowledge_time=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )
        row = repo.conn.execute(
            "SELECT relation_type FROM events.artist_market_relations WHERE artist_id = ?",
            ["bad-bunny"],
        ).fetchone()
        assert row[0] == "PERFORMED_IN_MARKET"
        vector = build_artist_market_vector(
            events_repo,
            artist_id="bad-bunny",
            market_id="Chicago, IL",
            as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )
        assert "demand_score" not in vector
        assert vector["no_demand_score"] is True
        assert vector["supporting_observation_ids"]
    finally:
        repo.close()


def test_event_linked_video_requires_explicit_evidence_not_search_query():
    events = [
        {
            "event_id": "e1",
            "venue_name": "United Center",
            "festival_name": None,
            "local_date": "2024-08-23",
            "canonical_url": "https://example.com/e1",
        }
    ]
    video_query_only = {
        "platform_object_id": "vid1",
        "text": "Bad Bunny new album",
        "search_query": "Bad Bunny Chicago",
    }
    assert link_video_to_events(video_query_only, events, artist_name="Bad Bunny", search_query="Bad Bunny Chicago") == []
    video_explicit = {
        "platform_object_id": "vid2",
        "text": "Bad Bunny live at the United Center 2024-08-23",
    }
    links = link_video_to_events(video_explicit, events, artist_name="Bad Bunny")
    assert links and links[0]["link_method"] == "EXPLICIT_VENUE_AND_DATE"


def test_event_linked_fan_requires_fan_generated_comments():
    events = [{"event_id": "e1"}]
    links = [{"youtube_video_id": "vid2", "canonical_event_id": "e1"}]
    assert event_linked_fan_status(events=events, links=links, fan_comments=[]) == "INSUFFICIENT_EVIDENCE"
    fans = [{"video_id": "vid2", "content_role": "FAN_GENERATED"}]
    assert event_linked_fan_status(events=events, links=links, fan_comments=fans) == "PASS"


def test_youtube_sample_cap_not_marked_complete():
    assert sampling_status(comment_count_cap_hit=True, comments_retrieved=500, comments_reported=9000) == CAPPED
    assert sampling_status(comments_retrieved=14, comments_reported=14) == COMPLETE
    capped = sampling_status(comment_count_cap_hit=True, comments_retrieved=500)
    assert capped != COMPLETE


def test_derived_features_preserve_lineage(tmp_path):
    repo = FestivalRepository(str(tmp_path / "feat.duckdb"))
    try:
        events_repo = EventRepository(repo.conn)
        cluster = reconcile_events([_pe(raw_observation_id="raw_abc")])[0]
        events_repo.store_reconciled(cluster, artist_id="bad-bunny", retrieved_at=datetime(2026, 8, 14, tzinfo=timezone.utc))
        vector = build_artist_market_vector(
            events_repo,
            artist_id="bad-bunny",
            market_id="Chicago, IL",
            as_of=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )
        assert "raw_abc" in vector["supporting_observation_ids"]
    finally:
        repo.close()
