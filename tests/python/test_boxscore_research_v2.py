"""Offline regressions for Public Boxscore Research Corpus V2.

Covers the new V2 pieces without a network:

* Touring Data 2024+ block parser (reported vs TBA vs estimated)
* cross-source engagement resolution (DISTINCT / EXACT / PROBABLE)
* cross-source agreement (reported, never reconciled)
* diversity / Herfindahl concentration
* deterministic, leakage-safe split manifests (tour never split)
* forward inventory snapshot classification (never a settled outcome)
* baseline-readiness verdict thresholds
"""

from __future__ import annotations

import pytest

from festival_bloomberg.research.audit import (
    VERDICT_NARROW,
    VERDICT_NOT_READY,
    VERDICT_READY,
    baseline_readiness,
    build_research_splits,
    corpus_diversity,
    hhi,
)
from festival_bloomberg.research.boxscore import (
    HEADCOUNT_PAID_TICKETS,
    HEADCOUNT_REPORTED_ATTENDANCE,
    BoxofficeEngagement,
    parse_touring_data_blocks,
)
from festival_bloomberg.research.inventory import (
    ForwardInventorySnapshot,
    assert_not_promoted_to_outcome,
)
from festival_bloomberg.research.resolution import (
    RESOLUTION_DISTINCT,
    RESOLUTION_EXACT_MATCH,
    RESOLUTION_PROBABLE_MATCH,
    cross_source_agreement,
    resolve_engagements,
)


def _engagement(engagement_id: str, **overrides) -> dict:
    kwargs = dict(
        engagement_id=engagement_id,
        reporting_source="billboard",
        artist="Artist A",
        venue="Venue A",
        city="Chicago",
        headcount_definition=HEADCOUNT_REPORTED_ATTENDANCE,
        number_of_shows=1,
        is_multi_show=False,
        is_reported=True,
        is_estimated=False,
    )
    kwargs.update(overrides)
    return BoxofficeEngagement.build(**kwargs).to_row()


# ---------------------------------------------------------------------------
# Touring Data block parser
# ---------------------------------------------------------------------------
BLOCK_TEXT = (
    "March 5-7, 2024\nZach Bryan\nUnited Center\nChicago, United States\n"
    "$12,648,557\n56,931 (100%)\n3 shows\n"
    "March 9, 2024\nZach Bryan\nPPG Paints Arena\nPittsburgh, United States\n"
    "$4,179,722\n17,927 (100%)\n1 show\n"
    "July 6-9, 2026\nAriana Grande\nState Farm Arena\nAtlanta, United States\n"
    "TBA\nTBA\n3 shows\n"
    "August 1, 2026\nAriana Grande\nUnited Center\nChicago, United States\n"
    "~$5,000,000\n~30,000 (95%)\n1 show\n"
)


def test_block_parser_reported_multi_and_single():
    engagements, skipped = parse_touring_data_blocks(BLOCK_TEXT, tour="zach-bryan-quittin-time-tour")
    reported = [e for e in engagements if e.is_reported]
    assert skipped["unreported"] == 1
    assert skipped["estimated"] == 1

    multi = next(e for e in reported if e.venue == "United Center")
    assert multi.number_of_shows == 3
    assert multi.is_multi_show is True
    assert multi.start_date == "2024-03-05"
    assert multi.end_date == "2024-03-07"
    assert multi.headcount_total == 56931.0
    assert multi.sell_through_pct == 100.0
    assert multi.headcount_definition == HEADCOUNT_REPORTED_ATTENDANCE
    assert multi.tour == "zach-bryan-quittin-time-tour"

    single = next(e for e in reported if e.venue == "PPG Paints Arena")
    assert single.is_multi_show is False
    assert single.number_of_shows == 1

    # estimated block is preserved but never reported
    estimated = [e for e in engagements if e.is_estimated]
    assert len(estimated) == 1
    assert estimated[0].is_reported is False


# ---------------------------------------------------------------------------
# Cross-source resolution
# ---------------------------------------------------------------------------
def test_resolution_distinct_when_no_overlap():
    rows = [
        _engagement("e1", artist="Taylor Swift", venue="United Center", city="Chicago", start_date="2013-10-26", end_date="2013-10-26"),
        _engagement("e2", artist="Taylor Swift", venue="United Center", city="Chicago", start_date="2013-10-27", end_date="2013-10-27"),
    ]
    canonicals, resolutions, stats = resolve_engagements(rows)
    assert stats["raw_engagements"] == 2
    assert stats["canonical_engagements"] == 2
    assert all(r["resolution_status"] == RESOLUTION_DISTINCT for r in resolutions)


def test_resolution_exact_match_across_sources():
    rows = [
        _engagement("e1", artist="Taylor Swift", venue="United Center", city="Chicago", start_date="2013-10-26", end_date="2013-10-26", reporting_source="billboard"),
        _engagement("e2", artist="Taylor Swift", venue="United Center", city="Chicago", start_date="2013-10-26", end_date="2013-10-26", reporting_source="pollstar", headcount_definition=HEADCOUNT_PAID_TICKETS),
    ]
    canonicals, resolutions, stats = resolve_engagements(rows)
    assert stats["canonical_engagements"] == 1
    assert stats["canonicals_with_multiple_sources"] == 1
    assert all(r["resolution_status"] == RESOLUTION_EXACT_MATCH for r in resolutions)
    assert canonicals[0]["resolution_confidence"] == "EXACT"


def test_resolution_probable_on_overlapping_range():
    rows = [
        _engagement("e1", artist="Zach Bryan", venue="United Center", city="Chicago", start_date="2024-03-05", end_date="2024-03-07", number_of_shows=3, is_multi_show=True),
        _engagement("e2", artist="Zach Bryan", venue="United Center", city="Chicago", start_date="2024-03-06", end_date="2024-03-07", number_of_shows=3, is_multi_show=True, reporting_source="touring_data"),
    ]
    canonicals, resolutions, stats = resolve_engagements(rows)
    assert stats["canonical_engagements"] == 1
    assert all(r["resolution_status"] == RESOLUTION_PROBABLE_MATCH for r in resolutions)


def test_resolution_null_dates_do_not_collide_canonical_ids():
    # two engagements with unparsed (None) dates in the same identity group
    # must still resolve to distinct canonicals (ids are derived from the
    # unique raw engagement ids, never a NULL-date identity hash).
    rows = [
        _engagement("eA", artist="Excision", venue="Tacoma Dome", city="Tacoma", start_date=None, number_of_shows=3, is_multi_show=True),
        _engagement("eB", artist="Excision", venue="Tacoma Dome", city="Tacoma", start_date=None, number_of_shows=3, is_multi_show=True),
    ]
    canonicals, resolutions, stats = resolve_engagements(rows)
    assert stats["canonical_engagements"] == 2
    ids = {c["canonical_engagement_id"] for c in canonicals}
    assert len(ids) == 2  # no collision


def test_resolution_raw_rows_never_mutated():
    rows = [
        _engagement("e1", headcount_total=15000.0, ticket_gross_total=1234567.0, start_date="2013-10-26", end_date="2013-10-26"),
        _engagement("e2", headcount_total=14500.0, ticket_gross_total=1200000.0, start_date="2013-10-26", end_date="2013-10-26", reporting_source="pollstar", headcount_definition=HEADCOUNT_PAID_TICKETS),
    ]
    canonicals, resolutions, _ = resolve_engagements(rows)
    # agreement is reported, not reconciled: both raw values survive
    agreement = cross_source_agreement(rows, resolutions, canonicals)
    assert agreement["matched_canonicals_compared"] == 1
    hc = agreement["field_agreement"]["headcount_total"]
    assert hc["comparisons"] == 1
    assert hc["exact_agreements"] == 0
    assert hc["mean_abs_diff"] == 500.0


# ---------------------------------------------------------------------------
# concentration
# ---------------------------------------------------------------------------
def test_hhi_bounds_and_perfect_concentration():
    assert hhi([]) == 0.0
    assert hhi(["a", "b", "c", "d"]) < 0.3  # spread
    assert hhi(["a", "a", "a", "a"]) == 1.0  # perfectly concentrated


def test_corpus_diversity_reports_independent_counts():
    rows = [
        _engagement("e1", artist="A1", venue="V1", city="Chicago"),
        _engagement("e2", artist="A1", venue="V1", city="Chicago"),
        _engagement("e3", artist="A2", venue="V2", city="New York"),
    ]
    d = corpus_diversity(rows)
    assert d["rows"] == 3
    assert d["distinct_artists"] == 2
    assert d["distinct_venues"] == 2
    assert d["distinct_markets"] == 2
    assert d["hhi_artist"] > 0.5  # A1 dominates


# ---------------------------------------------------------------------------
# split manifests
# ---------------------------------------------------------------------------
def test_splits_are_deterministic_and_cover_every_canonical():
    rows = [_engagement(f"e{i}", artist=f"Artist{i % 3}", venue=f"Venue{i % 4}", city="Chicago", start_date=f"2013-01-{i % 28 + 1:02d}") for i in range(10)]
    canonicals, _, _ = resolve_engagements(rows)
    splits_a, summary_a = build_research_splits(canonicals)
    splits_b, _ = build_research_splits(canonicals)
    assert [s["fold"] for s in splits_a] == [s["fold"] for s in splits_b]

    cids = {c["canonical_engagement_id"] for c in canonicals}
    for split_type in ("ARTIST_GROUP", "VENUE_GROUP", "MARKET_GROUP", "TOUR_GROUP"):
        subset = [s for s in splits_a if s["split_type"] == split_type]
        assert {s["canonical_engagement_id"] for s in subset} == cids
        assert all(s["fold"] in ("TRAIN", "TEST") for s in subset)
        assert all(s["deterministic"] is True for s in subset)


def test_tour_group_split_never_splits_a_tour():
    canonicals = [
        {"canonical_engagement_id": "c1", "artist": "Zach Bryan", "venue": "V1", "city": "Chicago", "tour": "zach-bryan-quittin-time-tour", "start_date": "2024-03-05", "number_of_shows": 3, "is_multi_show": True},
        {"canonical_engagement_id": "c2", "artist": "Zach Bryan", "venue": "V2", "city": "Pittsburgh", "tour": "zach-bryan-quittin-time-tour", "start_date": "2024-03-09", "number_of_shows": 1, "is_multi_show": False},
    ]
    splits, _ = build_research_splits(canonicals)
    tour_splits = [s for s in splits if s["split_type"] == "TOUR_GROUP"]
    folds = {s["fold"] for s in tour_splits}
    assert len(folds) == 1  # both engagements of the same tour land in one fold


# ---------------------------------------------------------------------------
# forward inventory classification
# ---------------------------------------------------------------------------
def test_inventory_snapshot_never_becomes_settled_outcome():
    snapshot = ForwardInventorySnapshot.build(
        event_external_id="evt_1",
        artist="Artist",
        venue="Venue",
        event_date="2026-10-11",
        estimated_capacity=18000.0,
        tickets_available=4200.0,
        tickets_distributed_or_sold_as_reported=13800.0,
        source_methodology="Ticketmaster seat-map inventory scrape (authorized)",
    )
    assert snapshot.classification == "INFERRED_INVENTORY_SIGNAL"
    with pytest.raises(ValueError):
        assert_not_promoted_to_outcome("PAID_TICKETS")
    with pytest.raises(ValueError):
        assert_not_promoted_to_outcome("TICKETS_SOLD")
    assert_not_promoted_to_outcome("INFERRED_INVENTORY_SIGNAL") is None


def test_inventory_snapshot_requires_methodology():
    with pytest.raises(ValueError):
        ForwardInventorySnapshot.build(event_external_id="evt_1", classification="INFERRED_INVENTORY_SIGNAL")


# ---------------------------------------------------------------------------
# baseline readiness
# ---------------------------------------------------------------------------
def _single_engagement(eid: str, artist: str, venue: str, year: str) -> dict:
    return _engagement(
        eid, artist=artist, venue=venue, city="Chicago",
        start_date=f"{year}-01-01", end_date=f"{year}-01-01",
        headcount_total=10000.0,
    )


def test_readiness_not_ready_when_too_few():
    rows = [_single_engagement(f"e{i}", f"A{i}", f"V{i}", "2013") for i in range(5)]
    assert baseline_readiness(rows)["verdict"] == VERDICT_NOT_READY


def test_readiness_ready_for_diverse_multi_year_panel():
    rows = []
    for i in range(120):
        rows.append(_single_engagement(
            f"e{i}", f"A{i % 30}", f"V{i % 25}", f"{2013 + (i % 6)}",
        ))
    verdict = baseline_readiness(rows)
    assert verdict["verdict"] == VERDICT_READY


def test_readiness_narrow_when_artist_concentrated():
    rows = []
    for i in range(80):
        rows.append(_single_engagement(
            f"e{i}", "Same Artist", f"V{i % 20}", f"{2013 + (i % 4)}",
        ))
    verdict = baseline_readiness(rows)
    assert verdict["verdict"] in (VERDICT_NARROW, VERDICT_NOT_READY)
    assert verdict["artist_hhi"] > 0.5
