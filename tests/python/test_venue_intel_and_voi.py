"""Tests for venue intelligence + data coverage + VOI ranking."""

from __future__ import annotations

from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.intelligence.coverage_voi import coverage_dashboard, voi_ranking
from festival_bloomberg.intelligence.venue_intel import (
    venue_capacity_profile,
    venue_coverage,
)
from festival_bloomberg.warehouse.repository import FestivalRepository


def _fresh(tmp_path, name: str) -> FestivalRepository:
    repo = FestivalRepository(str(tmp_path / name))
    EventRepository(repo.conn)
    return repo


def test_venue_profile_unknown_when_absent(tmp_path):
    repo = _fresh(tmp_path, "vp.duckdb")
    try:
        profile = venue_capacity_profile(repo.conn, venue_key="nope")
        assert profile["status"] == "UNKNOWN"
    finally:
        repo.close()


def test_venue_capacity_claims_never_merged(tmp_path):
    repo = _fresh(tmp_path, "vc.duckdb")
    try:
        repo.conn.execute(
            """
            INSERT INTO core.venues
                (venue_key, name, normalized_name, city, latitude, longitude)
            VALUES ('v1', 'Grant Park', 'grant park', 'Chicago', 41.8756, -87.6244)
            """
        )
        for i, (val, kind, src) in enumerate([
            (60000, "MAXIMUM_VENUE_CAPACITY", "wikidata"),
            (55000, "MAXIMUM_VENUE_CAPACITY", "official"),
            (20000, "EVENT_USABLE_CAPACITY", "ticketmaster"),
        ]):
            repo.conn.execute(
                """
                INSERT INTO economics.venue_capacity_claims
                    (claim_id, canonical_venue_id, capacity_value, capacity_kind,
                     configuration_description, provider, source, source_url,
                     retrieved_at, knowledge_time, claim_status)
                VALUES (?, 'v1', ?, ?, NULL, ?, ?, NULL, now(), now(), 'ACTIVE')
                """,
                [f"c{i}", val, kind, src, src],
            )
        profile = venue_capacity_profile(repo.conn, venue_key="v1")
        assert profile["status"] == "OBSERVED"
        assert profile["capacity"]["claim_count"] == 3
        assert profile["capacity"]["conflicting_claims"] is True
        kinds = profile["capacity"]["by_kind"]
        assert len(kinds["MAXIMUM_VENUE_CAPACITY"]["values"]) == 2
        assert kinds["MAXIMUM_VENUE_CAPACITY"]["latest"] == 55000
        assert kinds["EVENT_USABLE_CAPACITY"]["latest"] == 20000
    finally:
        repo.close()


def test_venue_coverage_percentages(tmp_path):
    repo = _fresh(tmp_path, "vcov.duckdb")
    try:
        repo.conn.execute(
            """
            INSERT INTO core.venues (venue_key, name, normalized_name, capacity, latitude, city)
            VALUES ('a', 'A', 'a', 1000, 1.0, 'X'),
                   ('b', 'B', 'b', NULL, NULL, NULL),
                   ('c', 'C', 'c', 500, NULL, 'Y'),
                   ('d', 'D', 'd', NULL, 2.0, 'Z')
            """
        )
        cov = venue_coverage(repo.conn)
        assert cov["venue_count"] == 4
        assert cov["coverage"]["capacity_column"] == 0.5
        assert cov["coverage"]["coordinates"] == 0.5
        assert cov["coverage"]["market"] == 0.75
    finally:
        repo.close()


def test_coverage_dashboard_and_voi(tmp_path):
    repo = _fresh(tmp_path, "voi.duckdb")
    try:
        repo.conn.execute(
            """
            INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name)
            VALUES ('mbid::a', 'a', 'Artist A', 'artist a')
            """
        )
        repo.conn.execute(
            """
            INSERT INTO core.venues (venue_key, name, normalized_name, capacity)
            VALUES ('v1', 'V1', 'v1', 1000)
            """
        )
        cov = coverage_dashboard(repo.conn)
        assert cov["coverage"]["artist_mbid_pct"] == 1.0
        assert cov["coverage"]["venue_capacity_pct"] == 1.0
        rank = voi_ranking(repo.conn)
        assert len(rank["ranking"]) >= 8
        # strictly decreasing VOI
        vois = [r["voi"] for r in rank["ranking"]]
        assert vois == sorted(vois, reverse=True)
        # commercial outcomes is the top gap (coverage_gap = 1.0)
        assert rank["ranking"][0]["category"] == "commercial"
        # every action states its measured gap + cost
        for r in rank["ranking"]:
            assert "coverage_gap" in r and "cost" in r and "voi" in r
    finally:
        repo.close()
