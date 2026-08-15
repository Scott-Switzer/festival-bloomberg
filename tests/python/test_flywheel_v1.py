"""Offline regressions for Data Flywheel & Coverage V1.

Covers the flywheel foundation without a network:

* coverage objectives match the milestone brief (KPI-corrected vocabulary:
  OUTCOME_CLAIMS != UNIQUE_EVENTS_WITH_OUTCOMES != FULLY_SETTLED_EVENTS)
* MusicBrainz identity resolution (normalize / select / build / client)
* OUTCOME_HUNTER plan construction, claim validation, semantic guards,
  execution statistics
* coverage measurement against a seeded warehouse (never fabricated),
  including PIT prior-result and decision-rate metrics
* coverage snapshot persistence + PIT cutoff reads
* CONTEXT_PANEL pageview parsing, attention derivation, series rows, vintage
* FORWARD_WATCH milestone ladder, registration idempotency, inventory deltas
* the live OA driver end-to-end through a scripted transport (gates reported)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from festival_bloomberg.acquisition.contracts import utc_now
from festival_bloomberg.economics.outcome_claims import (
    OBSERVED_PRIVATE,
    OBSERVED_PUBLIC,
    PROMOTER_CONTRIBUTION,
    REPORTED_ATTENDANCE,
    SETTLEMENT_GROSS,
    TICKET_GROSS,
    VENUE_CAPACITY,
    OutcomeClaim,
    OutcomeClaimSemanticError,
)
from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.flywheel.coverage import (
    BELOW_TARGET,
    measure_coverage,
    snapshot_id,
)
from festival_bloomberg.flywheel.event_graph import (
    MusicBrainzClient,
    MusicBrainzRateLimited,
    build_identity_row,
    name_similarity,
    normalize_name,
    select_best_match,
)
from festival_bloomberg.flywheel.forward_watch import (
    compute_milestones,
    inventory_change,
    register_event_row,
)
from festival_bloomberg.flywheel.objectives import (
    MEDIUM_TERM_OBJECTIVES_V1,
    OBJECTIVES_BY_KEY_V1,
    objective_rows,
)
from festival_bloomberg.flywheel.outcome_hunter import (
    HUNT_TARGET_FIELDS,
    TASK_CLAIM_FOUND,
    TASK_NOT_FOUND,
    TASK_PENDING,
    TASK_SEARCHING,
    build_hunt_plan,
    claim_from_hunt_finding,
    event_key,
    hunt_execution_stats,
    hunt_status_allowed,
    summarize_hunt_tasks,
)
from festival_bloomberg.flywheel.repository import FlywheelRepository
from festival_bloomberg.research.boxscore import (
    HEADCOUNT_REPORTED_ATTENDANCE,
    BoxofficeEngagement,
)
from festival_bloomberg.research.repository import ResearchRepository

from conftest import FakeTransport


# ---------------------------------------------------------------------------
# Objectives (KPI-corrected vocabulary)
# ---------------------------------------------------------------------------
def test_objectives_match_memo_targets():
    keys = {obj.objective_id for obj in MEDIUM_TERM_OBJECTIVES_V1}
    core = {
        "CANONICAL_BOXSCORE_ENGAGEMENTS",
        "SINGLE_SHOW_ENGAGEMENTS",
        "CANONICAL_PERFORMANCES",
        "OUTCOME_CLAIMS",
        "UNIQUE_EVENTS_WITH_OUTCOMES",
        "FULLY_SETTLED_EVENTS",
        "ARTISTS_WITH_3_PLUS_OUTCOMES",
        "MARKETS",
        "CANONICAL_VENUES",
        "CONTINUOUS_USEFUL_PERIOD",
        "FORWARD_TRACKED_FUTURE_EVENTS",
        "PRIVATE_EVENTS_WITH_SETTLEMENT_EVIDENCE",
    }
    assert core <= keys
    targets = {obj.objective_id: obj.target for obj in MEDIUM_TERM_OBJECTIVES_V1}
    assert targets["CANONICAL_BOXSCORE_ENGAGEMENTS"] == 50_000.0
    assert targets["SINGLE_SHOW_ENGAGEMENTS"] == 45_000.0
    assert targets["CANONICAL_PERFORMANCES"] == 50_000.0
    assert targets["OUTCOME_CLAIMS"] == 5_000.0
    assert targets["UNIQUE_EVENTS_WITH_OUTCOMES"] == 2_500.0
    assert targets["FULLY_SETTLED_EVENTS"] == 500.0
    assert targets["ARTISTS_WITH_3_PLUS_OUTCOMES"] == 1_000.0
    assert targets["MARKETS"] == 50.0
    assert targets["CANONICAL_VENUES"] == 1_000.0
    assert targets["CONTINUOUS_USEFUL_PERIOD"] == 8.0
    assert targets["FORWARD_TRACKED_FUTURE_EVENTS"] == 2_000.0
    assert targets["PRIVATE_EVENTS_WITH_SETTLEMENT_EVIDENCE"] == 500.0
    # the four decision rates
    assert targets["WARM_START_RATE"] == 0.5
    assert targets["OFFER_TIME_RECONSTRUCTABLE_RATE"] == 0.8
    assert targets["TICKET_PACE_COVERAGE"] == 0.6
    assert targets["SETTLEMENT_COVERAGE"] == 0.5
    rows = objective_rows()
    assert len(rows) == len(MEDIUM_TERM_OBJECTIVES_V1) == 32
    assert all(row["objective_version"] == "data_flywheel_and_coverage_v1" for row in rows)


def test_kpi_vocabulary_is_distinct():
    """OUTCOME_CLAIMS, UNIQUE_EVENTS_WITH_OUTCOMES and FULLY_SETTLED_EVENTS
    are three different KPIs with different definitions."""
    defs = {obj.objective_id: obj.definition for obj in MEDIUM_TERM_OBJECTIVES_V1}
    assert "CLAIMS" in defs["OUTCOME_CLAIMS"].upper()
    assert "not events" in defs["OUTCOME_CLAIMS"].lower()
    assert "Distinct canonical events" in defs["UNIQUE_EVENTS_WITH_OUTCOMES"]
    assert "settlement" in defs["FULLY_SETTLED_EVENTS"].lower()


# ---------------------------------------------------------------------------
# Event graph (MusicBrainz identity)
# ---------------------------------------------------------------------------
def test_normalize_name_and_similarity():
    assert normalize_name("Radiohead") == "radiohead"
    assert normalize_name("  The  Weeknd! ") == "the weeknd"
    assert name_similarity("Radiohead", "radiohead") == 1.0
    assert name_similarity("Olivia Rodrigo", "olivia-rodrigo") >= 0.99
    assert name_similarity("Taylor Swift", "Taylor Swift (born 1989)") < 1.0


def test_select_best_match_exact_and_unresolved():
    results = [{"id": "mbid-1", "name": "Bad Bunny", "type": "Person", "country": "PR"}]
    exact = select_best_match("Bad Bunny", results)
    assert exact["musicbrainz_id"] == "mbid-1"
    assert exact["resolution_method"] == "EXACT_MBID"
    assert exact["match_confidence"] == 1.0

    none = select_best_match("Some Completely Different Artist", results)
    assert none["musicbrainz_id"] is None
    assert none["resolution_method"] == "UNRESOLVED"


def test_build_identity_row_carries_cc0_license():
    selection = {
        "musicbrainz_id": "mbid-9",
        "musicbrainz_name": "Billie Eilish",
        "musicbrainz_type": "Person",
        "musicbrainz_country": "US",
        "resolution_method": "EXACT_MBID",
        "match_confidence": 1.0,
    }
    row = build_identity_row(entity_name="Billie Eilish", selection=selection)
    assert row["musicbrainz_id"] == "mbid-9"
    assert row["license"] == "CC0 (MusicBrainz data)"
    assert row["rights_status"] == "OPEN_COMMERCIAL_OK"
    assert row["resolution_method"] == "EXACT_MBID"
    assert row["knowledge_time"] is not None
    # deterministic identity id (same input -> same id)
    assert row["identity_id"] == build_identity_row(entity_name="Billie Eilish", selection=selection)["identity_id"]


def test_musicbrainz_client_search_and_rate_limit():
    transport = FakeTransport(
        responses=[
            (200, {"artists": [{"id": "mbid-1", "name": "Bad Bunny"}]}),
            (503, {"error": "rate limited"}),
        ]
    )
    client = MusicBrainzClient(transport=transport, rate_limit_seconds=0.0)
    results = client.search_artist("Bad Bunny")
    assert results[0]["id"] == "mbid-1"
    url = transport.requests[0]["url"]
    assert "/ws/2/artist?" in url
    assert "fmt=json" in url
    with pytest.raises(MusicBrainzRateLimited):
        client.search_artist("Taylor Swift")


# ---------------------------------------------------------------------------
# OUTCOME_HUNTER
# ---------------------------------------------------------------------------
def _engagement(engagement_id: str, **overrides) -> BoxofficeEngagement:
    kwargs = dict(
        engagement_id=engagement_id,
        reporting_source="billboard",
        artist="Zach Bryan",
        venue="United Center",
        city="Chicago",
        market="Chicago",
        start_date="2024-03-05",
        end_date="2024-03-05",
        headcount_definition=HEADCOUNT_REPORTED_ATTENDANCE,
        number_of_shows=1,
        is_multi_show=False,
        is_reported=True,
        is_estimated=False,
    )
    kwargs.update(overrides)
    return BoxofficeEngagement.build(**kwargs)


def test_hunt_plan_covers_all_target_fields():
    engagement = _engagement("e1", headcount_total=56931.0, ticket_gross_total=12648557.0).to_row()
    plan, tasks = build_hunt_plan(engagement)
    assert plan["status"] == "PLANNED"
    assert plan["target_fields"] == list(HUNT_TARGET_FIELDS)
    assert len(tasks) == len(HUNT_TARGET_FIELDS)
    assert {t["target_field"] for t in tasks} == set(HUNT_TARGET_FIELDS)
    assert all(t["status"] == TASK_PENDING for t in tasks)


def test_hunt_event_key_matches_research_convention():
    engagement = _engagement("e1").to_row()
    assert event_key(engagement) == "boxoffice_zach-bryan_united-center_2024-03-05"
    plan, _ = build_hunt_plan(engagement)
    assert plan["canonical_event_id"] == event_key(engagement)


def test_claim_from_hunt_finding_valid():
    claim = claim_from_hunt_finding(
        canonical_event_id="boxoffice_zach-bryan_united-center_2024-03-05",
        target_field="attendance",
        value_numeric=56931.0,
        source_provider="billboard",
        source_quality="C_OTHER_PUBLIC_REPORT",
        rights_status="RESEARCH_ONLY",
        commercial_use_status="RESEARCH_ONLY",
    )
    assert claim.outcome_type == REPORTED_ATTENDANCE
    assert claim.value_numeric == 56931.0
    assert claim.observation_class == OBSERVED_PUBLIC


def test_claim_from_hunt_finding_rejects_attribute_fields():
    with pytest.raises(ValueError):
        claim_from_hunt_finding(
            canonical_event_id="evt",
            target_field="promoter",
            source_provider="x",
            source_quality="C_OTHER_PUBLIC_REPORT",
            rights_status="RESEARCH_ONLY",
            commercial_use_status="RESEARCH_ONLY",
        )


def test_claim_semantic_guard_capacity_with_attendance_definition():
    with pytest.raises(OutcomeClaimSemanticError):
        claim_from_hunt_finding(
            canonical_event_id="evt",
            target_field="capacity",
            outcome_type=VENUE_CAPACITY,
            value_numeric=20000.0,
            attendance_definition="REPORTED_ATTENDANCE",
            source_provider="x",
            source_quality="C_OTHER_PUBLIC_REPORT",
            rights_status="RESEARCH_ONLY",
            commercial_use_status="RESEARCH_ONLY",
        )


def test_hunt_status_transitions():
    assert hunt_status_allowed(TASK_PENDING, TASK_SEARCHING)
    assert hunt_status_allowed(TASK_SEARCHING, TASK_CLAIM_FOUND)
    assert not hunt_status_allowed(TASK_CLAIM_FOUND, TASK_SEARCHING)
    with pytest.raises(ValueError):
        hunt_status_allowed("BOGUS", TASK_SEARCHING)


def test_hunt_execution_stats_never_reward_plans():
    # 100 planned PENDING tasks -> 0 attempted, 0 successful (plans != work).
    tasks = [
        {"status": TASK_PENDING, "target_field": f"f{i}"} for i in range(100)
    ]
    stats = hunt_execution_stats(tasks=tasks)
    assert stats["tasks_planned"] == 100
    assert stats["tasks_attempted"] == 0
    assert stats["tasks_successful"] == 0

    # a worked task set reports honest progress.
    tasks[0]["status"] = TASK_SEARCHING
    tasks[1]["status"] = TASK_CLAIM_FOUND
    tasks[2]["status"] = TASK_NOT_FOUND
    stats = hunt_execution_stats(tasks=tasks, claims_created=1, unique_new_events=1)
    assert stats["tasks_attempted"] == 3
    assert stats["tasks_successful"] == 1
    assert stats["not_found"] == 1
    assert stats["claims_created"] == 1

    summary = summarize_hunt_tasks(tasks)
    assert summary["pending"] == 97


# ---------------------------------------------------------------------------
# Coverage measurement against a seeded warehouse
# ---------------------------------------------------------------------------
def _pub(start: str) -> str:
    """Publication time = 7 days before the event (strict PIT test data)."""
    return (date.fromisoformat(start) - timedelta(days=7)).isoformat()


@pytest.fixture()
def seeded_db(tmp_path):
    import duckdb

    conn = duckdb.connect(str(tmp_path / "flywheel_seed.duckdb"))
    flywheel = FlywheelRepository(conn)
    research = ResearchRepository(conn)
    econ = EconomicsRepository(conn)

    engagements = [
        _engagement("e1", artist="Zach Bryan", venue="United Center", city="Chicago", market="Chicago",
                    start_date="2024-03-01", headcount_total=55000.0, ticket_gross_total=12000000.0,
                    source_publication_time=_pub("2024-03-01")),
        _engagement("e2", artist="Zach Bryan", venue="PPG Paints Arena", city="Pittsburgh", market="Pittsburgh",
                    start_date="2024-03-05", headcount_total=17927.0, ticket_gross_total=4179722.0,
                    source_publication_time=_pub("2024-03-05")),
        _engagement("e3", artist="Zach Bryan", venue="State Farm Arena", city="Atlanta", market="Atlanta",
                    start_date="2024-03-09", headcount_total=15000.0, ticket_gross_total=1000000.0,
                    source_publication_time=_pub("2024-03-09")),
        _engagement("e4", artist="Zach Bryan", venue="United Center", city="Chicago", market="Chicago",
                    start_date="2024-03-12", headcount_total=56931.0, ticket_gross_total=12648557.0,
                    source_publication_time=_pub("2024-03-12")),
        _engagement("e5", artist="Ariana Grande", venue="United Center", city="Chicago", market="Chicago",
                    start_date="2024-06-01", headcount_total=20000.0, ticket_gross_total=2500000.0,
                    source_publication_time=_pub("2024-06-01")),
        _engagement("e6", artist="Zach Bryan", venue="Madison Square Garden", city="New York", market="New York",
                    start_date="2024-04-01", number_of_shows=3, is_multi_show=True, headcount_total=40000.0),
        _engagement("e7", artist="Estimated Act", venue="Small Hall", city="Austin", market="Austin",
                    start_date="2024-05-01", headcount_total=1000.0, ticket_gross_total=30000.0, is_estimated=True),
    ]
    for engagement in engagements:
        research.insert_engagement(engagement)

    # Promote single-show reported engagements into the claim ledger.
    research.promote_single_show_engagements(econ)

    e4_key = event_key(_engagement("e4").to_row())
    e5_key = event_key(_engagement("e5", artist="Ariana Grande").to_row())

    # Capacity claim on e4 + private gross + settlement on customer_evt_1.
    econ.insert_outcome_claim(
        OutcomeClaim.build(
            canonical_event_id=e4_key,
            outcome_type=VENUE_CAPACITY,
            value_numeric=21000.0,
            source_provider="venue-master",
            source_quality="B_REPUTABLE_INDUSTRY_REPORT",
            rights_status="OPEN_WITH_ATTRIBUTION",
            commercial_use_status="OPEN_WITH_ATTRIBUTION",
        )
    )
    econ.insert_outcome_claim(
        OutcomeClaim.build(
            canonical_event_id="customer_evt_1",
            outcome_type=TICKET_GROSS,
            value_numeric=999999.0,
            currency="USD",
            source_provider="customer",
            source_quality="A_PRIMARY_SETTLEMENT",
            observation_class=OBSERVED_PRIVATE,
            rights_status="OPEN_COMMERCIAL_OK",
            commercial_use_status="OPEN_COMMERCIAL_OK",
        )
    )
    econ.insert_outcome_claim(
        OutcomeClaim.build(
            canonical_event_id="customer_evt_1",
            outcome_type=PROMOTER_CONTRIBUTION,
            value_numeric=500000.0,
            currency="USD",
            source_provider="customer",
            source_quality="A_PRIMARY_SETTLEMENT",
            observation_class=OBSERVED_PRIVATE,
            rights_status="OPEN_COMMERCIAL_OK",
            commercial_use_status="OPEN_COMMERCIAL_OK",
        )
    )

    # Decision cutoffs for e4 (booking/announcement/onsale known).
    econ.upsert_decision_cutoffs(
        {
            "event_id": f"cut_{e4_key}",
            "canonical_event_id": e4_key,
            "booking_cutoff": "2023-12-01T00:00:00Z",
            "announcement_cutoff": "2024-01-10T00:00:00Z",
            "onsale_cutoff": "2024-01-15T00:00:00Z",
            "software_version": "data_flywheel_and_coverage_v1",
        }
    )

    # Ticket pace: two primary snapshots for e5.
    now = utc_now().isoformat()
    for i in range(2):
        conn.execute(
            """
            INSERT INTO economics.primary_ticket_snapshots
                (snapshot_id, canonical_event_id, provider, retrieved_at,
                 knowledge_time, snapshot_bucket, fees_included)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [f"snap_e5_{i}", e5_key, "ticketmaster", now, now, "bucket", "unknown"],
        )

    # Venue graph rows.
    for vid, name in [("v1", "United Center"), ("v2", "PPG Paints Arena"), ("v3", "State Farm Arena")]:
        conn.execute(
            "INSERT INTO events.venues (venue_id, venue_name) VALUES (?, ?)",
            [vid, name],
        )

    # Forward watch: two TRACKING future events + one SETTLED.
    future = (date.today() + timedelta(days=30)).isoformat()
    past = (date.today() - timedelta(days=10)).isoformat()
    for idx, (provider_id, event_date, status) in enumerate(
        [("tm-1", future, "TRACKING"), ("tm-2", future, "TRACKING"), ("tm-3", past, "SETTLED")]
    ):
        row = register_event_row(
            provider="ticketmaster",
            provider_event_id=provider_id,
            artist_name="Artist",
            event_date=date.fromisoformat(event_date),
            rights_status="TERMS_REVIEW_REQUIRED",
            commercial_use_status="TERMS_REVIEW_REQUIRED",
            observation_class=OBSERVED_PUBLIC,
            tracking_status=status,
        )
        flywheel.register_forward_event(row)

    yield conn
    conn.close()


def test_canonical_performances_exclude_multi_show(seeded_db):
    """Engagement != performance. Multi-show aggregates (e6) are bookings,
    never performances; the performance denominator is single-show only."""
    from festival_bloomberg.flywheel.coverage import (
        count_canonical_engagements,
        count_canonical_performances,
        count_single_show_engagements,
    )

    assert count_canonical_engagements(seeded_db) == 7
    assert count_single_show_engagements(seeded_db) == 6
    assert count_canonical_performances(seeded_db) == 6


def test_coverage_measurement_on_seeded_warehouse(seeded_db):
    rows = measure_coverage(seeded_db, as_of=datetime(2026, 8, 1))
    by_key = {row["objective_key"]: row for row in rows}
    assert len(rows) == 32

    # Core scale: engagements (incl. aggregates) vs performances (single-show).
    assert by_key["CANONICAL_BOXSCORE_ENGAGEMENTS"]["actual_value"] == 7.0
    assert by_key["SINGLE_SHOW_ENGAGEMENTS"]["actual_value"] == 6.0
    assert by_key["CANONICAL_PERFORMANCES"]["actual_value"] == 6.0
    # OUTCOME_CLAIMS = defensible outcome types only (capacity + settlement are
    # tracked separately and are NOT outcome claims).
    assert by_key["OUTCOME_CLAIMS"]["actual_value"] == 11.0
    assert by_key["UNIQUE_EVENTS_WITH_OUTCOMES"]["actual_value"] == 6.0
    assert by_key["FULLY_SETTLED_EVENTS"]["actual_value"] == 1.0
    assert by_key["ARTISTS_WITH_3_PLUS_OUTCOMES"]["actual_value"] == 1.0
    assert by_key["MARKETS"]["actual_value"] == 5.0
    assert by_key["CANONICAL_VENUES"]["actual_value"] == 3.0
    assert by_key["CONTINUOUS_USEFUL_PERIOD"]["actual_value"] == 1.0
    assert by_key["FORWARD_TRACKED_FUTURE_EVENTS"]["actual_value"] == 2.0
    assert by_key["PRIVATE_EVENTS_WITH_SETTLEMENT_EVIDENCE"]["actual_value"] == 1.0

    # Per-dimension outcome coverage.
    assert by_key["EVENTS_WITH_ATTENDANCE"]["actual_value"] == 5.0
    assert by_key["EVENTS_WITH_PAID_TICKETS"]["actual_value"] == 0.0
    assert by_key["EVENTS_WITH_GROSS"]["actual_value"] == 6.0
    assert by_key["EVENTS_WITH_SELLOUT"]["actual_value"] == 0.0
    assert by_key["EVENTS_WITH_CAPACITY"]["actual_value"] == 1.0
    assert by_key["EVENTS_WITH_ONSALE_DATE"]["actual_value"] == 1.0
    assert by_key["EVENTS_WITH_ANNOUNCEMENT_DATE"]["actual_value"] == 1.0
    # e4 has 3 PIT knowable priors (e1/e2/e3 published before 2024-03-12).
    assert by_key["EVENTS_WITH_3PLUS_PRIOR_ARTIST_RESULTS"]["actual_value"] == 1.0
    # Chicago: e4 (prior e1) and e5 (priors e1, e4).
    assert by_key["EVENTS_WITH_PRIOR_MARKET_RESULT"]["actual_value"] == 2.0
    # United Center: e4 (prior e1) and e5 (priors e1, e4).
    assert by_key["EVENTS_WITH_PRIOR_VENUE_RESULT"]["actual_value"] == 2.0
    assert by_key["EVENTS_WITH_TICKET_PACE"]["actual_value"] == 1.0
    assert by_key["EVENTS_WITH_OFFER_OR_BOOKING_CUTOFF"]["actual_value"] == 1.0

    # Rates are fractions of CANONICAL_PERFORMANCES (single-show denominator).
    assert by_key["WARM_START_RATE"]["actual_value"] == pytest.approx(1.0 / 6.0)
    assert by_key["OFFER_TIME_RECONSTRUCTABLE_RATE"]["actual_value"] == pytest.approx(1.0 / 6.0)
    assert by_key["TICKET_PACE_COVERAGE"]["actual_value"] == pytest.approx(1.0 / 6.0)
    assert by_key["SETTLEMENT_COVERAGE"]["actual_value"] == pytest.approx(1.0 / 6.0)

    # All below their targets; a genuinely-zero dimension has ratio 0.0 (never
    # fabricated into a positive number).
    for row in rows:
        assert row["status"] == BELOW_TARGET
        assert 0.0 <= row["coverage_ratio"] < 1.0


def test_private_settlement_requires_settlement_types(tmp_path):
    """A private attendance-only import is NOT settlement evidence: only
    OBSERVED_PRIVATE claims of settlement type (PROMOTER_CONTRIBUTION /
    SETTLEMENT_GROSS / SETTLEMENT_NET) count — and even then it is settlement
    EVIDENCE, not full settlement completeness."""
    import duckdb

    from festival_bloomberg.flywheel.coverage import (
        count_private_events_with_settlement_evidence,
    )

    conn = duckdb.connect(str(tmp_path / "private_settle.duckdb"))
    try:
        econ = EconomicsRepository(conn)
        econ.insert_outcome_claim(
            OutcomeClaim.build(
                canonical_event_id="partner_evt_1",
                outcome_type=REPORTED_ATTENDANCE,
                value_numeric=8000.0,
                source_provider="customer",
                source_quality="A_PRIMARY_SETTLEMENT",
                observation_class=OBSERVED_PRIVATE,
                rights_status="OPEN_COMMERCIAL_OK",
                commercial_use_status="OPEN_COMMERCIAL_OK",
            )
        )
        assert count_private_events_with_settlement_evidence(conn) == 0

        econ.insert_outcome_claim(
            OutcomeClaim.build(
                canonical_event_id="partner_evt_1",
                outcome_type=SETTLEMENT_GROSS,
                value_numeric=900000.0,
                currency="USD",
                source_provider="customer",
                source_quality="A_PRIMARY_SETTLEMENT",
                observation_class=OBSERVED_PRIVATE,
                rights_status="OPEN_COMMERCIAL_OK",
                commercial_use_status="OPEN_COMMERCIAL_OK",
            )
        )
        assert count_private_events_with_settlement_evidence(conn) == 1
    finally:
        conn.close()


def test_prior_results_are_strict_pit(seeded_db):
    """A prior with unknown publication time never counts (fail closed)."""
    from festival_bloomberg.flywheel.coverage import count_events_with_prior_results

    conn = seeded_db
    conn.execute(
        "UPDATE research.boxoffice_engagements SET source_publication_time = NULL "
        "WHERE engagement_id = 'e2'"
    )
    # e4 loses one knowable prior (e2) -> drops below 3.
    assert count_events_with_prior_results(conn, dimension="artist", min_prior=3) == 0
    # e4 still has >=1 same-market/venue prior (e1).
    assert count_events_with_prior_results(conn, dimension="market", min_prior=1) == 2
    assert count_events_with_prior_results(conn, dimension="venue", min_prior=1) == 2


def test_coverage_snapshot_persistence_and_pit(seeded_db):
    flywheel = FlywheelRepository(seeded_db)
    rows = measure_coverage(seeded_db, as_of=datetime(2026, 8, 1))
    for row in rows:
        row["snapshot_id"] = snapshot_id(datetime(2026, 8, 1), row["objective_key"])
        flywheel.insert_coverage_snapshot(row)

    stored = flywheel.query_coverage_snapshots()
    assert len(stored) == 32
    assert len(flywheel.latest_coverage()) == 32

    # Second run at a later time appends, never overwrites.
    rows2 = measure_coverage(seeded_db, as_of=datetime(2026, 9, 1))
    for row in rows2:
        row["snapshot_id"] = snapshot_id(datetime(2026, 9, 1), row["objective_key"])
        flywheel.insert_coverage_snapshot(row)
    assert len(flywheel.query_coverage_snapshots()) == 64

    # PIT: a cutoff before the September run sees only the August snapshot.
    august_only = flywheel.query_coverage_snapshots(cutoff=datetime(2026, 8, 15))
    assert len(august_only) == 32
    assert all("20260801" in row["snapshot_id"] for row in august_only)


# ---------------------------------------------------------------------------
# CONTEXT_PANEL
# ---------------------------------------------------------------------------
def _daily_series(days: int, views: int, end: date | None = None) -> dict[date, int]:
    anchor = end or date(2026, 8, 1)
    return {anchor - timedelta(days=i): views for i in range(days)}


def test_parse_pageviews_items():
    from festival_bloomberg.flywheel.context_panel import parse_pageviews_items

    items = [
        {"timestamp": "2026070100", "views": 100},
        {"timestamp": "2026070200", "views": 150},
        {"timestamp": "notadate", "views": 5},
    ]
    parsed = parse_pageviews_items({"items": items})
    assert [p["observed_date"] for p in parsed] == [date(2026, 7, 1), date(2026, 7, 2)]
    assert parsed[0]["views"] == 100


def test_derive_attention_stats_constant_series():
    from festival_bloomberg.flywheel.context_panel import derive_attention_stats

    daily = _daily_series(30, 100)
    stats = derive_attention_stats(daily, as_of=date(2026, 8, 1))
    assert stats["total_views"] == 3000.0
    assert stats["views_7d"] == 700.0
    assert stats["views_30d"] == 3000.0
    assert stats["velocity_7d"] == 100.0
    assert stats["velocity_30d"] == 100.0
    assert stats["acceleration"] == 0.0
    assert stats["volatility_cv"] == 0.0
    assert stats["zscore_7d"] is None  # zero variance -> no z-score
    assert stats["yoy_change"] is None  # no year-ago window


def test_derive_attention_stats_spike():
    from festival_bloomberg.flywheel.context_panel import derive_attention_stats

    daily = _daily_series(30, 100)
    anchor = date(2026, 8, 1)
    for i in range(7):
        daily[anchor - timedelta(days=i)] = 200
    stats = derive_attention_stats(daily, as_of=anchor)
    assert stats["velocity_7d"] == 200.0
    assert stats["acceleration"] > 0
    assert stats["zscore_7d"] is not None and stats["zscore_7d"] > 0


def test_build_pageview_series_rows():
    from festival_bloomberg.flywheel.context_panel import build_pageview_series_rows

    series = [{"observed_date": date(2026, 8, 1), "views": 100}]
    rows = build_pageview_series_rows(entity_name="Bad Bunny", series=series)
    assert len(rows) == 1
    row = rows[0]
    assert row["series_type"] == "ATTENTION_PAGEVIEWS"
    assert row["provider"] == "wikimedia"
    assert row["value"] == 100.0
    assert row["rights_status"] == "OPEN_WITH_ATTRIBUTION"
    assert row["knowledge_time"] is not None
    assert row["raw_payload_hash"] is not None


def test_census_vintage_semantics():
    from festival_bloomberg.flywheel.context_panel import (
        census_acs_5year_label,
        census_acs_publication_time,
        census_api_url,
    )

    assert census_acs_5year_label(2022) == "ACS 5-Year 2018-2022"
    assert census_acs_publication_time(2022) == date(2023, 7, 1)
    url = census_api_url(series="acs/acs5", vintage=2022, key="secret")
    assert "api.census.gov/data/2022/acs/acs5" in url
    assert "key=secret" in url
    with pytest.raises(ValueError):
        census_api_url(series="acs/acs5", vintage=2022, key="")


# ---------------------------------------------------------------------------
# FORWARD_WATCH
# ---------------------------------------------------------------------------
def test_forward_milestones_with_onsale():
    milestones = {
        m["milestone"]: m
        for m in compute_milestones(
            date(2026, 8, 20), onsale_date=date(2026, 6, 1), first_seen=datetime(2026, 5, 1)
        )
    }
    assert milestones["DISCOVERED"]["basis"] == "first_seen"
    assert milestones["ONSALE"]["due_at"] == "2026-06-01"
    assert milestones["D+7"]["due_at"] == "2026-06-08"
    assert milestones["D+7"]["basis"] == "onsale+offset"
    assert milestones["T-7"]["due_at"] == "2026-08-13"
    assert milestones["SHOW"]["due_at"] == "2026-08-20"
    assert milestones["SETTLEMENT"]["due_at"] == "2026-09-19"
    assert "assumption" in milestones["SETTLEMENT"]["basis"]


def test_forward_milestones_without_onsale_never_misleading():
    """D+N means DAYS AFTER ONSALE. Unknown onsale -> D+N timestamps are
    UNKNOWN (basis=onsale_unknown); the event-relative T-N ladder still runs
    independently from event_date and is never conflated with it."""
    milestones = {
        m["milestone"]: m
        for m in compute_milestones(date(2026, 8, 20), onsale_date=None)
    }
    assert milestones["D+1"]["due_at"] is None
    assert milestones["D+7"]["due_at"] is None
    assert milestones["D+14"]["due_at"] is None
    assert milestones["D+7"]["basis"] == "onsale_unknown"
    # event-relative ladder is unaffected by the unknown onsale
    assert milestones["T-7"]["due_at"] == "2026-08-13"
    assert milestones["T-7"]["basis"] == "event-relative"
    assert milestones["SHOW"]["due_at"] == "2026-08-20"
    assert milestones["ONSALE"]["due_at"] is None
    assert milestones["ONSALE"]["basis"] == "observed_live"


def test_forward_watch_registration_idempotent(seeded_db):
    flywheel = FlywheelRepository(seeded_db)
    row = register_event_row(
        provider="ticketmaster",
        provider_event_id="tm-x",
        artist_name="Artist",
        event_date=date(2026, 10, 1),
        rights_status="TERMS_REVIEW_REQUIRED",
        commercial_use_status="TERMS_REVIEW_REQUIRED",
        observation_class=OBSERVED_PUBLIC,
    )
    assert flywheel.register_forward_event(row) is True
    assert flywheel.register_forward_event(row) is False
    events = flywheel.query_forward_events(tracking_status="TRACKING")
    matches = [e for e in events if e["provider_event_id"] == "tm-x"]
    assert len(matches) == 1


def test_inventory_change():
    assert inventory_change(None, 100) is None
    assert inventory_change(100, None) is None
    assert inventory_change(4000, 3700) == -300.0


# ---------------------------------------------------------------------------
# Live OA driver (hermetic via scripted transport)
# ---------------------------------------------------------------------------
def test_flywheel_oa_end_to_end(tmp_path):
    from festival_bloomberg.oa.flywheel_v1 import run_flywheel_v1_oa

    today = date.today()
    responses = []
    for name in ("Bad Bunny", "Billie Eilish", "Kendrick Lamar"):
        responses.append(
            (200, {"artists": [{"id": f"mbid-{name.lower().replace(' ', '-')}", "name": name, "type": "Person", "country": "US"}]})
        )
    items = []
    for i in range(30):
        d = today - timedelta(days=29 - i)
        items.append({"timestamp": d.strftime("%Y%m%d00"), "views": 100})
    responses.append((200, {"items": items}))

    transport = FakeTransport(responses=responses)

    db_path = tmp_path / "flywheel_oa.duckdb"
    report_path = tmp_path / "flywheel_oa_manifest.json"
    manifest = run_flywheel_v1_oa(
        str(db_path),
        report_path=str(report_path),
        transport=transport,
    )

    assert manifest["software_version"] == "data_flywheel_and_coverage_v1"
    assert manifest["sources"]["total"] == 21
    assert manifest["objectives_registered"] == 32
    assert len(manifest["coverage"]["rows"]) == 32
    assert manifest["coverage"]["objectives_total"] == 32

    event_graph = manifest["pipelines"]["EVENT_GRAPH"]
    assert event_graph["status"] == "PASS"
    assert event_graph["identities_inserted"] == 3

    context_panel = manifest["pipelines"]["CONTEXT_PANEL"]
    # Only Wikimedia is implemented -> deliberately PARTIAL, never complete.
    assert context_panel["status"] == "PARTIAL"
    assert context_panel["series_rows"] == 30
    assert context_panel["providers"]["wikimedia"] == "IMPLEMENTED"
    assert context_panel["providers"]["census"] == "KEY_REQUIRED"

    # No research corpus / no future events in a fresh DB -> honest NOT_EVALUATED.
    assert manifest["pipelines"]["OUTCOME_HUNTER"]["status"] == "NOT_EVALUATED"
    assert manifest["pipelines"]["FORWARD_WATCH"]["status"] == "NOT_EVALUATED"

    # OUTCOME_HUNTER execution stats: plans are real, execution is honest.
    execution = manifest["pipelines"]["OUTCOME_HUNTER"]["execution"]
    assert execution["tasks_planned"] == 0
    assert execution["tasks_attempted"] == 0
    assert execution["claims_created"] == 0

    # Gates summary mirrors the pipeline statuses.
    assert manifest["gates"] == {
        "EVENT_GRAPH": "PASS",
        "OUTCOME_HUNTER": "NOT_EVALUATED",
        "CONTEXT_PANEL": "PARTIAL",
        "FORWARD_WATCH": "NOT_EVALUATED",
    }
    assert manifest["provider_cost_usd"] == 0.0
    assert report_path.is_file()


def test_oa_hunt_execution_stats_on_research_corpus(tmp_path):
    """With a seeded research corpus the OA reports planned tasks and honest
    zero execution (plans != acquisitions completed)."""
    import duckdb

    from festival_bloomberg.oa.flywheel_v1 import run_flywheel_v1_oa

    db_path = tmp_path / "hunt_oa.duckdb"
    conn = duckdb.connect(str(db_path))
    research = ResearchRepository(conn)
    research.insert_engagement(_engagement("e1", headcount_total=55000.0, ticket_gross_total=12000000.0))
    conn.close()

    manifest = run_flywheel_v1_oa(str(db_path), transport=FakeTransport([]))

    hunt = manifest["pipelines"]["OUTCOME_HUNTER"]
    assert hunt["status"] == "PASS"
    assert hunt["plans_created"] == 1
    execution = hunt["execution"]
    assert execution["tasks_planned"] == len(HUNT_TARGET_FIELDS) == 11
    assert execution["tasks_attempted"] == 0
    assert execution["tasks_successful"] == 0
    assert execution["claims_created"] == 0
    # ledger exists and is real (empty for the fresh OA db)
    assert isinstance(hunt["ledger_by_type"], dict)


def test_source_registry_size():
    from festival_bloomberg.flywheel.sources import SOURCE_REGISTRY_V1, source_rows

    assert len(SOURCE_REGISTRY_V1) == 21
    rows = source_rows()
    assert len(rows) == len(SOURCE_REGISTRY_V1)
    assert all(row["access_status"] in (
        "AVAILABLE", "KEY_REQUIRED", "TERMS_REVIEW", "REGISTRATION_REQUIRED", "PARTNER_GATED", "NOT_AVAILABLE"
    ) for row in rows)
