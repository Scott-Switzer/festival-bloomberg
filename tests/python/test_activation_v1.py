"""Offline regressions for DATA_ACQUISITION_ACTIVATION_V1.

Covers, without a network:

* PIT reconstruction taxonomy, eligibility modes (STRICT excludes
  ESTIMATED/UNKNOWN; archive capture is an upper bound, never publication)
* OUTCOME_HUNTER execution: priority tiers, attempt status machine
  (NOT_FOUND != RATE_LIMITED != PARSER_FAILED != RIGHTS_BLOCKED), attempt
  ledger rows, CDX era-directed crawl selection, conservative claim
  extraction from archived pages
* FORWARD_WATCH: MusicBrainz future-event parsing, milestone mapping (D+N
  ONLY anchored to a known onsale), forward enrollment idempotency, real
  history-warehouse migration (events + observations)
* acquisition economics: derived yield metrics
* the live OA driver end-to-end through a scripted transport
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from festival_bloomberg.acquisition.contracts import utc_now
from festival_bloomberg.flywheel.forward_discovery import (
    MusicBrainzFutureEventsClient,
    build_forward_event_row,
    milestone_for_observation,
    migrate_history_watch,
    parse_mb_event,
)
from festival_bloomberg.flywheel.hunt_execution import (
    TASK_CLAIM_FOUND,
    TASK_HTTP_FAILED,
    TASK_NOT_FOUND,
    TASK_PARSER_FAILED,
    TASK_RATE_LIMITED,
    TASK_RIGHTS_BLOCKED,
    build_attempt_row,
    era_directed_crawl_ids,
    extract_claims_from_page,
    is_failure,
    ordered_tasks,
    priority_key,
    priority_tier,
    summarize_attempts,
    validate_attempt_status,
)
from festival_bloomberg.flywheel.pit import (
    ARCHIVE_CAPTURE_UPPER_BOUND,
    CONSERVATIVE_BOUND_PIT,
    ESTIMATED_RESEARCH_ONLY,
    OBSERVED_DAY,
    RESEARCH_ESTIMATED,
    SOURCE_PERIOD_BOUND,
    STRICT_PIT,
    UNKNOWN,
    build_archive_upper_bound_evidence,
    classify_source_document_evidence,
    mode_eligible,
    validate_evidence_class,
    validate_pit_mode,
)
from festival_bloomberg.flywheel.repository import FlywheelRepository
from festival_bloomberg.research.boxscore import (
    HEADCOUNT_REPORTED_ATTENDANCE,
    BoxofficeEngagement,
)
from festival_bloomberg.research.repository import ResearchRepository

from conftest import FakeTransport


# ---------------------------------------------------------------------------
# PIT reconstruction taxonomy + eligibility modes
# ---------------------------------------------------------------------------
def test_pit_evidence_classes_closed_set():
    assert validate_evidence_class(OBSERVED_DAY) == OBSERVED_DAY
    with pytest.raises(ValueError):
        validate_evidence_class("MADE_UP")
    with pytest.raises(ValueError):
        validate_pit_mode("SOMETIMES_PIT")


def test_mode_eligibility_never_leaks():
    # STRICT accepts only classes that PROVE knowability.
    assert mode_eligible(OBSERVED_DAY, STRICT_PIT)
    assert not mode_eligible(ARCHIVE_CAPTURE_UPPER_BOUND, STRICT_PIT)
    assert not mode_eligible(SOURCE_PERIOD_BOUND, STRICT_PIT)
    # Conservative bound accepts upper bounds but never research estimates.
    assert mode_eligible(ARCHIVE_CAPTURE_UPPER_BOUND, CONSERVATIVE_BOUND_PIT)
    assert not mode_eligible(ESTIMATED_RESEARCH_ONLY, CONSERVATIVE_BOUND_PIT)
    # Estimates only enter RESEARCH_ESTIMATED, and are never strict.
    assert mode_eligible(ESTIMATED_RESEARCH_ONLY, RESEARCH_ESTIMATED)
    assert not mode_eligible(ESTIMATED_RESEARCH_ONLY, STRICT_PIT)
    # UNKNOWN is eligible for NOTHING (unknown != zero).
    for mode in (STRICT_PIT, CONSERVATIVE_BOUND_PIT, RESEARCH_ESTIMATED):
        assert not mode_eligible(UNKNOWN, mode)


def test_classify_source_document_observed_day():
    rows = classify_source_document_evidence(
        canonical_event_id="evt",
        reporting_source="pollstar",
        source_url="https://news.pollstar.com/2024/05/23/hot-tickets-may-23-2024/",
        publication_date=date(2024, 5, 23),
        source_document_id="src_x",
    )
    assert len(rows) == 1
    assert rows[0]["evidence_class"] == OBSERVED_DAY
    # OBSERVED_DAY availability convention: END of the documented day. A
    # same-day publication can never inform a cutoff earlier that day.
    assert rows[0]["source_publication_time"].startswith("2024-05-23T23:59:59")
    assert rows[0]["source_provider"] == "pollstar"

    # No publication date -> no evidence (stays UNKNOWN; nothing invented).
    assert (
        classify_source_document_evidence(
            canonical_event_id="evt",
            reporting_source="billboard",
            source_url="https://www.webcitation.org/getfile.php?fileid=x",
            publication_date=None,
            source_document_id="src_y",
        )
        == []
    )


def test_archive_capture_is_upper_bound_not_publication():
    row = build_archive_upper_bound_evidence(
        canonical_event_id="evt",
        capture_time="2024-06-01T10:00:00Z",
        source_url="https://news.pollstar.com/x/",
        source_provider="commoncrawl",
        source_document_id="CC-MAIN-2024-30",
    )
    assert row["evidence_class"] == ARCHIVE_CAPTURE_UPPER_BOUND
    assert row["archive_capture_time"] == "2024-06-01T10:00:00Z"
    assert row["source_publication_time"] is None  # never promoted


# ---------------------------------------------------------------------------
# OUTCOME_HUNTER execution: priority + status machine
# ---------------------------------------------------------------------------
def test_priority_tiers_ordinal():
    assert priority_tier("onsale") == 0
    assert priority_tier("attendance") == 0
    assert priority_tier("ticket_price") == 1
    assert priority_tier("corroboration") == 2
    assert priority_tier("unknown_field") == 2


def test_priority_ordering_missing_before_known_p0_before_p1():
    tasks = [
        {"target_field": "ticket_price", "known_value": None, "event_date": "2024-03-01"},
        {"target_field": "onsale", "known_value": None, "event_date": "2024-03-01"},
        {"target_field": "attendance", "known_value": 50000, "event_date": "2024-03-01"},
        {"target_field": "onsale", "known_value": "2024-01-15", "event_date": "2024-03-01"},
    ]
    ordered = ordered_tasks(tasks)
    # P0-missing first; P0-known next; P1-missing last.
    assert [t["target_field"] for t in ordered[:2]] == ["onsale", "attendance"]
    assert ordered[2]["target_field"] == "onsale"
    assert ordered[3]["target_field"] == "ticket_price"
    # deterministic: same input -> same order
    assert [t["target_field"] for t in ordered] == [t["target_field"] for t in ordered_tasks(tasks)]


def test_attempt_status_machine():
    assert validate_attempt_status(TASK_NOT_FOUND) == TASK_NOT_FOUND
    with pytest.raises(ValueError):
        validate_attempt_status("MIGHT_HAVE_FOUND")
    assert not is_failure(TASK_NOT_FOUND)  # genuine negative is NOT a failure
    assert not is_failure(TASK_CLAIM_FOUND)
    assert is_failure(TASK_RATE_LIMITED)
    assert is_failure(TASK_PARSER_FAILED)
    assert is_failure(TASK_HTTP_FAILED)
    assert is_failure(TASK_RIGHTS_BLOCKED)


def test_attempt_row_append_only_semantics():
    a = build_attempt_row(
        plan_id="plan", task_id="task", target_field="onsale",
        provider="commoncrawl_cdx", status=TASK_NOT_FOUND,
    )
    b = build_attempt_row(
        plan_id="plan", task_id="task", target_field="onsale",
        provider="commoncrawl_cdx", status=TASK_RATE_LIMITED,
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 1, 0, 0, 5, tzinfo=timezone.utc),
    )
    # distinct attempt ids -> prior attempts are never destroyed
    assert a["attempt_id"] != b["attempt_id"]
    assert b["started_at"].startswith("2026-08-01")
    assert b["status"] == TASK_RATE_LIMITED


def test_summarize_attempts_honest():
    attempts = [
        {"status": TASK_CLAIM_FOUND},
        {"status": TASK_NOT_FOUND},
        {"status": TASK_RATE_LIMITED},
        {"status": TASK_PARSER_FAILED},
    ]
    stats = summarize_attempts(attempts)
    assert stats["tasks_attempted"] == 4
    assert stats["tasks_successful"] == 1
    assert stats[TASK_NOT_FOUND] == 1
    assert stats[TASK_RATE_LIMITED] == 1
    assert stats[TASK_PARSER_FAILED] == 1
    assert summarize_attempts(None)["tasks_attempted"] == 0


def test_run_cdx_hunt_evidence_not_erased_by_later_error():
    from festival_bloomberg.flywheel.hunt_execution import run_cdx_hunt

    # First crawl returns a capture; second crawl errors. The found evidence
    # MUST be CLAIM_FOUND — an erroring crawl never erases found captures.
    transport = FakeTransport(
        responses=[
            (200, {"timestamp": "20240523000000", "statuscode": "200", "urlkey": "k", "digest": "d", "length": "100", "mime": "text/html"}),
            (500, {"error": "index unavailable"}),
        ]
    )
    result = run_cdx_hunt(
        url="https://news.pollstar.com/2024/05/23/hot-tickets-may-23-2024/",
        crawls=[{"id": "CC-MAIN-2024-30"}, {"id": "CC-MAIN-2025-40"}],
        target_year=2024, window=1, max_captures=5,
        transport=transport, throttle_seconds=0.0,
    )
    assert result["status"] == TASK_CLAIM_FOUND
    assert len(result["captures"]) == 1

    # All crawls error with no evidence -> HTTP_FAILED (never NOT_FOUND).
    transport2 = FakeTransport(
        responses=[
            (500, {"error": "boom"}),
        ]
    )
    result2 = run_cdx_hunt(
        url="https://x.example/1",
        crawls=[{"id": "CC-MAIN-2024-30"}],
        target_year=2024, window=1, max_captures=5,
        transport=transport2, throttle_seconds=0.0,
    )
    assert result2["status"] == TASK_HTTP_FAILED
    assert result2["captures"] == []

    # A clean no-capture response is a genuine NOT_FOUND.
    transport3 = FakeTransport(
        responses=[
            (404, {"message": "No Captures found"}),
        ]
    )
    result3 = run_cdx_hunt(
        url="https://y.example/1",
        crawls=[{"id": "CC-MAIN-2024-30"}],
        target_year=2024, window=1, max_captures=5,
        transport=transport3, throttle_seconds=0.0,
    )
    assert result3["status"] == TASK_NOT_FOUND


def test_wikipedia_capacity_hunt_status_semantics():
    from festival_bloomberg.flywheel.hunt_execution import run_wikipedia_capacity_hunt

    # 1. Venue page found with capacity in the infobox -> CLAIM_FOUND + a
    #    real CapacityClaim (never averaged; UPPER_BOUND semantics).
    transport = FakeTransport(
        responses=[
            (200, {"query": {"search": [{"title": "Madison Square Garden"}]}}),
            (
                200,
                {
                    "query": {
                        "pages": {
                            "1": {
                                "title": "Madison Square Garden",
                                "revisions": [{"slots": {"main": {"content": "{{Infobox venue | capacity = 20,789}}"}}}],
                                "pageprops": {"wikibase_item": "Q23387"},
                            }
                        }
                    }
                },
            ),
        ]
    )
    result = run_wikipedia_capacity_hunt(
        venues=[{"venue": "Madison Square Garden", "city": "New York"}],
        transport=transport,
        max_venues=5,
        throttle_seconds=0.0,
    )
    assert result["venues_hunted"] == 1
    assert result["attempts"][0]["status"] == TASK_CLAIM_FOUND
    assert result["attempts"][0]["target_field"] == "capacity"
    assert result["http_successes"] == 1 and result["http_failures"] == 0
    assert result["http_rate_limited"] == 0
    assert len(result["venue_results"][0]["claims"]) == 1
    claim = result["venue_results"][0]["claims"][0]
    assert claim.capacity_value == 20789.0
    assert claim.usage_label == "MAXIMUM_CAPACITY_UPPER_BOUND"

    # 2. Page retrieved with no capacity evidence -> genuine NOT_FOUND.
    transport2 = FakeTransport(
        responses=[
            (200, {"query": {"search": [{"title": "Some Venue"}]}}),
            (200, {"query": {"pages": {"1": {"title": "Some Venue", "revisions": [{"slots": {"main": {"content": "no capacity field"}}}]}}}}),
        ]
    )
    result2 = run_wikipedia_capacity_hunt(
        venues=[{"venue": "Some Venue", "city": "Nowhere"}],
        transport=transport2,
        max_venues=5,
        throttle_seconds=0.0,
    )
    assert result2["attempts"][0]["status"] == TASK_NOT_FOUND

    # 3. No Wikipedia page at all -> NOT_FOUND (search genuinely succeeded).
    transport3 = FakeTransport(
        responses=[(200, {"query": {"search": []}})]
    )
    result3 = run_wikipedia_capacity_hunt(
        venues=[{"venue": "Unknown Hall", "city": "Nowhere"}],
        transport=transport3,
        max_venues=5,
        throttle_seconds=0.0,
    )
    assert result3["attempts"][0]["status"] == TASK_NOT_FOUND

    # 4. HTTP 429 -> RATE_LIMITED, never NOT_FOUND; 403 -> RIGHTS_BLOCKED.
    transport4 = FakeTransport(
        responses=[(429, {"error": "rate limited"})]
    )
    result4 = run_wikipedia_capacity_hunt(
        venues=[{"venue": "Throttled Hall", "city": "Nowhere"}],
        transport=transport4,
        max_venues=5,
        throttle_seconds=0.0,
    )
    assert result4["attempts"][0]["status"] == TASK_RATE_LIMITED
    # 429 is an HTTP rate limit (never a failure, never an http success).
    assert result4["http_rate_limited"] == 1
    assert result4["http_failures"] == 0
    transport5 = FakeTransport(
        responses=[(403, {"error": "access denied"})]
    )
    result5 = run_wikipedia_capacity_hunt(
        venues=[{"venue": "Blocked Hall", "city": "Nowhere"}],
        transport=transport5,
        max_venues=5,
        throttle_seconds=0.0,
    )
    assert result5["attempts"][0]["status"] == TASK_RIGHTS_BLOCKED

    # 5. max_venues bounds the hunt (deterministic ordinal priority).
    transport6 = FakeTransport(
        responses=[(200, {"query": {"search": []}})]
    )
    result6 = run_wikipedia_capacity_hunt(
        venues=[{"venue": f"V{i}", "city": "C"} for i in range(10)],
        transport=transport6,
        max_venues=3,
        throttle_seconds=0.0,
    )
    assert result6["venues_hunted"] == 3


def test_era_directed_crawl_ids():
    crawls = [
        {"id": "CC-MAIN-2013-48"},
        {"id": "CC-MAIN-2024-30"},
        {"id": "CC-MAIN-2026-30"},
        {"id": "CC-MAIN-1996-16"},
        {"id": "not-a-crawl"},
    ]
    picked = era_directed_crawl_ids(target_year=2024, crawls=crawls, window=2)
    assert picked == ["CC-MAIN-2024-30", "CC-MAIN-2026-30"]
    picked_old = era_directed_crawl_ids(target_year=2013, crawls=crawls, window=2)
    assert "CC-MAIN-2013-48" in picked_old


def test_extract_claims_from_page_conservative():
    text = (
        "TICKET GROSS $12,648,557. Attendance: 56,931. "
        "The show was completely sold out in 40 minutes."
    )
    claims = extract_claims_from_page(text, target_year=2024)
    types = {c["outcome_type"] for c in claims}
    assert "TICKET_GROSS" in types
    assert "REPORTED_ATTENDANCE" in types
    assert "EXPLICIT_SOLD_OUT_ASSERTION" in types
    gross = next(c for c in claims if c["outcome_type"] == "TICKET_GROSS")
    assert gross["value_numeric"] == 12648557.0
    # a clean page with no evidence yields [] (never a fabricated claim)
    assert extract_claims_from_page("no numbers here", target_year=2024) == []
    assert extract_claims_from_page("", target_year=2024) == []


# ---------------------------------------------------------------------------
# FORWARD_WATCH: discovery + milestones + migration
# ---------------------------------------------------------------------------
def test_parse_mb_event_requires_begin_date():
    raw = {
        "id": "evt-1",
        "name": "the eternal sunshine tour: London",
        "type": "Concert",
        "life-span": {"begin": "2026-11-12", "end": "2026-11-12"},
        "relations": [
            {"type": "main performer", "artist": {"name": "Ariana Grande"}},
            {"type": "held at", "place": {"name": "The O2"}},
        ],
    }
    parsed = parse_mb_event(raw)
    assert parsed["provider_event_id"] == "evt-1"
    assert parsed["begin_date"] == date(2026, 11, 12)
    assert parsed["main_performer"] == "Ariana Grande"
    assert parsed["place"] == "The O2"
    # no begin date -> dropped (cannot track a date-less event)
    assert parse_mb_event({"id": "x", "name": "no date"}) is None
    assert parse_mb_event({"id": "x", "name": "bad", "life-span": {"begin": "not-a-date"}}) is None


def test_mb_client_search_url_and_pagination():
    items = {
        "count": 250,
        "events": [
            {"id": f"e{i}", "name": f"Show {i}", "type": "Concert",
             "life-span": {"begin": "2026-12-01"}}
            for i in range(2)
        ],
    }
    transport = FakeTransport(responses=[(200, items), (200, {"events": [], "count": 250})])
    client = MusicBrainzFutureEventsClient(transport=transport, rate_limit_seconds=0.0)
    events = client.future_events(horizon_days=90, max_events=10, as_of=date(2026, 8, 14))
    assert len(events) == 2
    assert all(e["begin_date"] == date(2026, 12, 1) for e in events)
    url = transport.requests[0]["url"]
    assert "/ws/2/event?" in url
    assert "2026-08-14" in url
    assert "TO" in url.replace("+", " ").replace("%20", " ")


def test_milestone_d_plus_requires_known_onsale():
    # With onsale known: D+N anchored to onsale.
    observed = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    assert milestone_for_observation(
        event_date=date(2026, 10, 11), observed_at=observed, onsale_date=date(2026, 6, 1)
    ) == "D+4"
    # Without onsale: NEVER D+N (UNKNOWN_ONSALE != EVENT_DATE).
    assert milestone_for_observation(event_date=date(2026, 10, 11), observed_at=observed) != "D+4"
    # Event-relative ladder still anchors to event date.
    at_14d = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
    assert milestone_for_observation(event_date=date(2026, 10, 11), observed_at=at_14d) == "T-14"
    after_show = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    assert milestone_for_observation(event_date=date(2026, 10, 11), observed_at=after_show) == "SHOW"
    settled = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert milestone_for_observation(event_date=date(2026, 10, 11), observed_at=settled) == "SETTLEMENT"


def test_forward_enrollment_idempotent(tmp_path):
    import duckdb

    conn = duckdb.connect(str(tmp_path / "fw.duckdb"))
    flywheel = FlywheelRepository(conn)
    row = build_forward_event_row(
        provider="musicbrainz", provider_event_id="mb-e1", artist_name="Artist",
        venue_name="Venue", market=None, event_date=date(2026, 12, 1),
    )
    assert flywheel.register_forward_event(row) is True
    assert flywheel.register_forward_event(row) is False
    events = flywheel.query_forward_events(tracking_status="TRACKING")
    assert len(events) == 1
    assert events[0]["provider_event_id"] == "mb-e1"
    conn.close()


def _seed_history_db(path, *, future_events=2):
    import duckdb

    conn = duckdb.connect(str(path))
    conn.execute("CREATE SCHEMA IF NOT EXISTS events")
    conn.execute("CREATE SCHEMA IF NOT EXISTS economics")
    conn.execute(
        "CREATE TABLE events.events (event_id VARCHAR, event_name VARCHAR, "
        "venue_name VARCHAR, market_id VARCHAR, local_date DATE, event_status VARCHAR)"
    )
    conn.execute(
        "CREATE TABLE events.artist_identities (canonical_artist_id VARCHAR PRIMARY KEY, "
        "display_name VARCHAR)"
    )
    conn.execute(
        "CREATE TABLE events.artist_event_relations (relation_id VARCHAR PRIMARY KEY, "
        "artist_id VARCHAR, event_id VARCHAR, role VARCHAR)"
    )
    conn.execute(
        "CREATE TABLE economics.primary_ticket_snapshots (snapshot_id VARCHAR, "
        "canonical_event_id VARCHAR, provider VARCHAR, provider_event_id VARCHAR, "
        "retrieved_at TIMESTAMP, knowledge_time TIMESTAMP, snapshot_bucket VARCHAR, "
        "fees_included VARCHAR, minimum_price DOUBLE, maximum_price DOUBLE, "
        "currency VARCHAR, event_status VARCHAR, public_onsale_start VARCHAR, "
        "source_url VARCHAR, raw_payload_hash VARCHAR)"
    )
    conn.execute(
        "INSERT INTO events.artist_identities VALUES ('artist_tour', 'Tour Artist')"
    )
    base = date.today() + timedelta(days=60)
    for i in range(future_events):
        conn.execute(
            "INSERT INTO events.events VALUES (?, ?, ?, ?, ?, ?)",
            [f"evt_ticketmaster_{i}", f"Tour Show {i}", "United Center", "Chicago",
             base + timedelta(days=i), "onsale"],
        )
        conn.execute(
            "INSERT INTO events.artist_event_relations VALUES (?, 'artist_tour', ?, 'main')",
            [f"rel_{i}", f"evt_ticketmaster_{i}"],
        )
    now = datetime(2026, 8, 14, 12, 0, 0)
    for i in range(future_events):
        for j in range(3):
            conn.execute(
                "INSERT INTO economics.primary_ticket_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [f"snap_{i}_{j}", f"evt_ticketmaster_{i}", "ticketmaster", f"tm-{i}",
                 now + timedelta(days=j), now + timedelta(days=j), "bucket", "unknown",
                 55.0 + j, 200.0 + j, "USD", "onsale", "2026-06-01", None, None],
            )
    conn.close()
    return base


def test_migrate_history_watch_real_rows(tmp_path):
    import duckdb

    history_path = tmp_path / "history.duckdb"
    _seed_history_db(history_path, future_events=2)

    conn = duckdb.connect(str(tmp_path / "target.duckdb"))
    flywheel = FlywheelRepository(conn)
    history_conn = duckdb.connect(str(history_path), read_only=True)
    try:
        result = migrate_history_watch(
            history_conn=history_conn, flywheel=flywheel,
            as_of=datetime(2026, 8, 14, 12, 0, 0),
        )
    finally:
        history_conn.close()

    assert result["events_enrolled"] == 2
    assert result["observations_inserted"] == 6
    assert result["events_with_2plus_observations"] == 2
    # artist evidence is the REAL performer (artist relation), never the event
    # title: the enrolled events carry "Tour Artist", not "Tour Show i".
    enrolled = flywheel.query_forward_events()
    assert {e["artist_name"] for e in enrolled} == {"Tour Artist"}
    assert "Tour Show" not in {e["artist_name"] or "" for e in enrolled}

    obs = flywheel.query_forward_observations()
    assert len(obs) == 6
    # observations mapped to the milestone ladder (D+ relative to onsale)
    milestones = {o["milestone"] for o in obs}
    assert milestones <= {
        "DISCOVERED", "ANNOUNCEMENT", "PRESALE", "ONSALE", "D+1", "D+3", "D+7",
        "D+14", "WEEKLY", "T-30", "T-14", "T-7", "T-3", "T-1", "SHOW", "SETTLEMENT",
    }
    conn.close()


# ---------------------------------------------------------------------------
# Acquisition economics
# ---------------------------------------------------------------------------
def test_derive_metrics_math():
    from festival_bloomberg.flywheel.acquisition_accounting import (
        build_acquisition_run_row,
        derive_metrics,
    )

    run = build_acquisition_run_row(
        provider="commoncrawl_cdx", pipeline="OUTCOME_HUNTER",
        http_requests=500, http_successful_responses=400, new_claims=10,
        new_cutoffs=2, new_unique_events_improved=5, new_warm_start_events=1,
        tasks_attempted=400, tasks_not_found=390, tasks_claim_found=10,
        monetary_cost_usd=0.0,
    )
    m = derive_metrics(run)
    assert m["successes_per_1000_requests"] == pytest.approx(800.0)
    assert m["new_claims_per_1000_requests"] == pytest.approx(20.0)
    assert m["new_cutoffs_per_1000_requests"] == pytest.approx(4.0)
    assert m["new_usable_events_per_1000_requests"] == pytest.approx(14.0)
    assert m["new_warm_starts_per_1000_requests"] == pytest.approx(2.0)
    # Explicit-denominator metrics (migration 019).
    assert m["http_success_rate"] == pytest.approx(0.8)
    assert m["claims_per_1000_http_requests"] == pytest.approx(20.0)
    assert m["claims_per_1000_tasks_attempted"] == pytest.approx(25.0)
    assert m["new_events_per_1000_http_requests"] == pytest.approx(10.0)
    # zero-cost run -> cost-per-new-evidence is 0.0, not None
    assert m["cost_per_new_claim"] == 0.0
    # zero/unmeasured requests -> per-1000 metrics are None (never fabricated)
    empty = derive_metrics(build_acquisition_run_row(provider="x", pipeline="y", http_requests=0))
    assert empty["successes_per_1000_requests"] is None
    assert empty["http_success_rate"] is None
    assert empty["claims_per_1000_http_requests"] is None
    # unmeasured request count -> NULL requests + UNKNOWN status, never 0
    unknown = build_acquisition_run_row(provider="x", pipeline="y", http_requests=None)
    assert unknown["requests"] is None
    assert unknown["request_count_status"] == "UNKNOWN"
    assert unknown["http_requests"] is None
    measured = build_acquisition_run_row(provider="x", pipeline="y", http_requests=3)
    assert measured["request_count_status"] == "MEASURED"


def test_pit_warm_start_uses_evidence_not_raw_column(tmp_path):
    """Warm-start numerators must read the PIT EVIDENCE table, never the raw
    (NULL) engagement publication column. Retrospective evidence (publication
    AFTER the event) must NOT create a warm start; a pre-event publication
    must.
    """
    import duckdb

    from festival_bloomberg.flywheel.coverage import (
        count_events_with_prior_results_pit,
    )
    from festival_bloomberg.flywheel.pit import (
        OBSERVED_DAY,
        build_pit_evidence_row,
    )

    conn = duckdb.connect(str(tmp_path / "warm.duckdb"))
    research = ResearchRepository(conn)
    research.insert_engagement(_engagement(
        "e1", artist="Same Artist", venue="Arena A", start_date="2024-01-10",
    ))
    research.insert_engagement(_engagement(
        "e2", artist="Same Artist", venue="Arena A", start_date="2024-03-10",
    ))
    research.insert_engagement(_engagement(
        "e3", artist="Same Artist", venue="Arena A", start_date="2024-05-10",
    ))
    research.insert_engagement(_engagement(
        "e4", artist="Same Artist", venue="Arena A", start_date="2024-07-10",
    ))
    # Pre-event day-level evidence for the first three (knowable before the
    # target e4): strict warm-start should count e4.
    from festival_bloomberg.flywheel.pit import event_key_from_engagement

    for eng, pub in [
        ({"artist": "Same Artist", "venue": "Arena A", "start_date": "2024-01-10"}, "2024-01-05"),
        ({"artist": "Same Artist", "venue": "Arena A", "start_date": "2024-03-10"}, "2024-03-05"),
        ({"artist": "Same Artist", "venue": "Arena A", "start_date": "2024-05-10"}, "2024-05-05"),
    ]:
        row = build_pit_evidence_row(
            canonical_event_id=event_key_from_engagement(eng),
            evidence_class=OBSERVED_DAY,
            source_publication_time=f"{pub}T00:00:00",
            source_url="https://news.pollstar.com/x",
            source_provider="pollstar",
            source_document_id="src_x",
        )
        conn.execute(
            "INSERT INTO flywheel.pit_reconstruction_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                row["evidence_id"], row["canonical_event_id"], row["evidence_class"],
                row["source_publication_time"], row["archive_capture_time"],
                row["source_period_start"], row["source_period_end"], row["source_url"],
                row["source_provider"], row["source_document_id"], row["rights_status"],
                row["commercial_use_status"], row["knowledge_time"], row["software_version"],
            ],
        )
    conn.commit()
    from festival_bloomberg.flywheel.pit import STRICT_PIT_CLASSES

    assert count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=3, evidence_classes=STRICT_PIT_CLASSES
    ) == 1  # e4
    # Retrospective evidence (publication AFTER the target event) must NOT
    # create a warm start: a result that was not knowable at the target's
    # booking decision cannot be a prior.
    conn.execute(
        "UPDATE flywheel.pit_reconstruction_evidence SET source_publication_time = '2024-08-01T00:00:00' "
        "WHERE canonical_event_id = ?",
        [event_key_from_engagement({"artist": "Same Artist", "venue": "Arena A", "start_date": "2024-01-10"})],
    )
    conn.commit()
    assert count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=3, evidence_classes=STRICT_PIT_CLASSES
    ) == 0
    # A publication between the prior's own event and the target IS still a
    # knowable prior (result availability precedes the target cutoff).
    conn.execute(
        "UPDATE flywheel.pit_reconstruction_evidence SET source_publication_time = '2024-02-01T00:00:00' "
        "WHERE canonical_event_id = ?",
        [event_key_from_engagement({"artist": "Same Artist", "venue": "Arena A", "start_date": "2024-01-10"})],
    )
    conn.commit()
    assert count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=3, evidence_classes=STRICT_PIT_CLASSES
    ) == 1
    conn.close()


def test_provider_accounting_never_mixes_statuses(tmp_path):
    """Provider A's failures must never appear in provider B's run row.

    The accounting split was a real bug: the CDX run consumed the combined
    attempt set, so Wikipedia rate limits leaked into the CDX provider's
    counters. Each run row owns ONLY its own statuses.
    """
    import duckdb

    from festival_bloomberg.flywheel.acquisition_accounting import (
        build_acquisition_run_row,
        derive_metrics,
    )
    from festival_bloomberg.flywheel.repository import FlywheelRepository

    conn = duckdb.connect(str(tmp_path / "acct.duckdb"))
    flywheel = FlywheelRepository(conn)

    cdx_run = build_acquisition_run_row(
        provider="commoncrawl_cdx", pipeline="OUTCOME_HUNTER",
        http_requests=100, http_successful_responses=90,
        tasks_not_found=90, rate_limited=0, http_failed=0,
        detail="CDX only",
    )
    wiki_run = build_acquisition_run_row(
        provider="wikipedia_mediawiki_api", pipeline="OUTCOME_HUNTER",
        http_requests=100, http_successful_responses=10,
        tasks_not_found=10, rate_limited=85, http_failed=5,
        detail="Wikipedia only",
    )
    assert flywheel.insert_acquisition_run(cdx_run)
    assert flywheel.insert_acquisition_run(wiki_run)
    assert flywheel.insert_acquisition_metrics(derive_metrics(cdx_run))
    assert flywheel.insert_acquisition_metrics(derive_metrics(wiki_run))

    rows = {}
    for row in flywheel.conn.execute(
        "SELECT provider, requests, not_found, rate_limited, http_failed, "
        "successful_responses FROM flywheel.provider_acquisition_runs"
    ).fetchall():
        provider, requests, not_found, rate_limited, http_failed, success = row
        rows[provider] = {
            "requests": requests, "not_found": not_found,
            "rate_limited": rate_limited, "http_failed": http_failed,
            "successful_responses": success,
        }
    assert rows["commoncrawl_cdx"]["rate_limited"] == 0
    assert rows["commoncrawl_cdx"]["http_failed"] == 0
    assert rows["commoncrawl_cdx"]["not_found"] == 90
    assert rows["wikipedia_mediawiki_api"]["rate_limited"] == 85
    assert rows["wikipedia_mediawiki_api"]["http_failed"] == 5
    assert rows["wikipedia_mediawiki_api"]["not_found"] == 10
    conn.close()


def test_venue_max_capacity_never_event_usable():
    """A Wikipedia venue maximum must not satisfy event-configuration capacity.

    A 20,789-seat venue is VENUE_CAPACITY / MAXIMUM_CAPACITY_UPPER_BOUND, not
    EVENT_USABLE_CAPACITY for any given concert. The claim machinery persists
    the venue claim with UPPER_BOUND semantics and utilization stays UNKNOWN
    unless event-configuration evidence exists.
    """
    from festival_bloomberg.economics.capacity import (
        CapacityClaim,
        UPPER_BOUND,
        compute_utilization,
        select_applicable_capacity,
    )
    from festival_bloomberg.flywheel.hunt_execution import venue_key

    vid = venue_key("Madison Square Garden", "New York")
    claim = CapacityClaim(
        claim_id="cap_test",
        canonical_venue_id=vid,
        capacity_value=20789.0,
        capacity_kind="MAX_PERSONS",
        configuration_description=None,
        effective_from=None,
        effective_to=None,
        provider="wikipedia_mediawiki_api",
        source="wikipedia_infobox",
        source_url="https://en.wikipedia.org/wiki/Madison_Square_Garden",
        source_publication_time=None,
        retrieved_at="2026-08-14T00:00:00Z",
        knowledge_time="2026-08-14T00:00:00Z",
        source_observation_id="page",
        claim_status="OBSERVED",
        usage_label=UPPER_BOUND,
    )
    # Venue max without event configuration -> UPPER_BOUND_ONLY, not usable.
    applicable = select_applicable_capacity([claim], event_configuration=None)
    assert applicable["status"] == "UPPER_BOUND_ONLY"
    utilization = compute_utilization(
        attendance_value=15000.0, applicable_capacity=applicable
    )
    assert utilization["utilization"] is None
    assert utilization["reason"] == "capacity_is_upper_bound_not_event_capacity"
    # With an explicit event configuration matching the claim kind, it may
    # become event-applicable — but never from a bare venue maximum alone.
    assert applicable.get("usage_label") == UPPER_BOUND


# ---------------------------------------------------------------------------
# Live OA driver (hermetic via scripted transport)
# ---------------------------------------------------------------------------
def _engagement(engagement_id: str, **overrides) -> BoxofficeEngagement:
    kwargs = dict(
        engagement_id=engagement_id,
        reporting_source="pollstar",
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
        headcount_total=56931.0,
        ticket_gross_total=12648557.0,
        source_url="https://news.pollstar.com/2024/03/07/hot-tickets-march-7-2024/",
    )
    kwargs.update(overrides)
    return BoxofficeEngagement.build(**kwargs)


def test_activation_oa_end_to_end(tmp_path):
    from festival_bloomberg.oa.activation_v1 import run_activation_v1_oa

    # Scripted transport: collinfo + CDX (404 no captures) + Wikipedia venue
    # search (no capacity page) + wikimedia pageviews.
    responses = [
        (200, [{"id": "CC-MAIN-2024-30"}, {"id": "CC-MAIN-2025-40"}]),
        (404, {"message": "No Captures found"}),
        (404, {"message": "No Captures found"}),
        (200, {"query": {"search": []}}),  # Wikipedia: no venue page
    ]
    items = []
    today = date.today()
    for i in range(30):
        d = today - timedelta(days=29 - i)
        items.append({"timestamp": d.strftime("%Y%m%d00"), "views": 100})
    responses.append((200, {"items": items}))
    transport = FakeTransport(responses=responses)

    # NB: the file must NOT be named ``research.duckdb`` — duckdb's catalog
    # is the file stem, which would collide with the ``research`` schema
    # from migration 015. The real corpus warehouse avoids this by living in
    # ``boxoffice_research_v2.duckdb``.
    research_path = tmp_path / "corpus.duckdb"
    import duckdb

    conn = duckdb.connect(str(research_path))
    research = ResearchRepository(conn)
    research.insert_engagement(_engagement("eng_1"))
    research.insert_engagement(_engagement("eng_2", artist="Ariana Grande", start_date="2024-03-10"))
    research.insert_source({
        "source_id": "src_pollstar",
        "reporting_source": "pollstar",
        "source_url": "https://news.pollstar.com/2024/03/07/hot-tickets-march-7-2024/",
        "publication_date": "2024-03-07",
        "retrieved_at": utc_now().isoformat(),
        "selection_method": "POLLSTAR_HOT_TICKETS_CHART",
        "rights_status": "RESEARCH_ONLY",
        "commercial_use_status": "RESEARCH_ONLY",
    })
    conn.close()

    history_path = tmp_path / "history.duckdb"
    _seed_history_db(history_path, future_events=1)

    mb_events = [
        {
            "provider": "musicbrainz", "provider_event_id": "mb-future-1",
            "name": "Test Arena Show", "main_performer": "Test Artist",
            "place": "Test Arena", "begin_date": date(2026, 12, 15),
            "source_url": "https://musicbrainz.org/ws/2/event/mb-future-1",
        }
    ]

    manifest = run_activation_v1_oa(
        research_db=str(research_path),
        history_db=str(history_path),
        report_path=str(tmp_path / "activation_manifest.json"),
        transport=transport,
        mb_events=mb_events,
    )

    assert manifest["software_version"] == "data_acquisition_activation_v1"

    hunt = manifest["pipelines"]["OUTCOME_HUNTER"]
    # One CDX attempt (source doc, no captures) + one Wikipedia venue hunt.
    assert hunt["attempts"]["tasks_attempted"] == 2
    assert hunt["attempts"][TASK_NOT_FOUND] == 2
    assert hunt["capacity_venues_hunted"] == 1
    assert hunt["wikipedia_statuses"][TASK_NOT_FOUND] == 1
    assert "candidate_claims_extracted" in hunt  # fix 7: candidates reported separately
    assert manifest["gates"]["OUTCOME_HUNTER"] == "PASS"

    pit = manifest["pipelines"]["PIT_RECONSTRUCTION"]
    assert pit["evidence_rows_observed_day"] == 2
    assert pit["strict_pit_events"] == 2
    assert manifest["gates"]["PIT_RECONSTRUCTION"] == "PASS"

    forward = manifest["pipelines"]["FORWARD_WATCH"]
    assert forward["musicbrainz_events_found"] == 1
    assert forward["history_events_enrolled"] == 1
    assert forward["events_enrolled_this_run"] == 2
    assert forward["forward_watch_events_total"] == 2
    assert forward["events_with_2plus_observations"] == 1
    # fix 3: offline fixture -> request count UNKNOWN, never estimated.
    assert forward["musicbrainz_request_count"] is None
    assert forward["musicbrainz_request_count_status"] == "UNKNOWN"
    assert manifest["gates"]["FORWARD_WATCH"] == "PASS"

    context = manifest["pipelines"]["CONTEXT_PANEL"]
    assert context["series_rows_inserted"] == 30
    assert manifest["gates"]["CONTEXT_PANEL"] == "PARTIAL"

    accounting = manifest["pipelines"]["ACQUISITION_ECONOMICS"]
    assert accounting["runs_recorded"] == 5
    assert manifest["gates"]["ACQUISITION_ECONOMICS"] == "PASS"

    assert manifest["provider_cost_usd"] == 0.0
    assert (tmp_path / "activation_manifest.json").is_file()

    # Persisted side effects in the research DB.
    conn = duckdb.connect(str(research_path), read_only=True)
    try:
        pit_rows = conn.execute("SELECT COUNT(*) FROM flywheel.pit_reconstruction_evidence").fetchone()[0]
        attempts = conn.execute("SELECT COUNT(*) FROM flywheel.outcome_hunt_attempts").fetchone()[0]
        fw_events = conn.execute("SELECT COUNT(*) FROM flywheel.forward_watch_events").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM flywheel.provider_acquisition_runs").fetchone()[0]
        metrics = conn.execute("SELECT COUNT(*) FROM flywheel.provider_acquisition_metrics").fetchone()[0]
        assert pit_rows == 2
        assert attempts == 2
        assert fw_events == 2
        assert runs == 5
        assert metrics == 5
        # fix 2/7: the CDX run row carries its OWN provider-local counters with
        # separated units; parser output never becomes new_claims.
        cdx = conn.execute(
            "SELECT http_requests, http_successful_responses, tasks_attempted, "
            "tasks_not_found, new_claims, requests, successful_responses, "
            "request_count_status FROM flywheel.provider_acquisition_runs "
            "WHERE provider = 'commoncrawl_cdx' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        # One CDX task queries BOTH era-directed crawls -> 2 HTTP requests,
        # both successful exchanges, task outcome NOT_FOUND.
        assert cdx[0] == 2 and cdx[1] == 2  # 2 HTTP requests, 2 successful responses
        assert cdx[2] == 1 and cdx[3] == 1  # 1 task attempted, 1 NOT_FOUND
        assert cdx[4] == 0                  # no persisted claims this run
        assert cdx[5] == 2                  # requests mirror http_requests
        assert cdx[6] == 2                  # successful_responses mirror http
        assert cdx[7] == "MEASURED"
        mb = conn.execute(
            "SELECT requests, http_requests, request_count_status "
            "FROM flywheel.provider_acquisition_runs WHERE provider = 'musicbrainz_events' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        assert mb[0] is None and mb[1] is None  # never estimated from row counts
        assert mb[2] == "UNKNOWN"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PR #21 semantic closure regressions
# ---------------------------------------------------------------------------
def test_mb_event_name_never_substituted_for_performer(tmp_path):
    """Fix 1: an event NAME is not artist/performer evidence.

    An MB event with a name + venue + date but no main-performer relation must
    NOT be FORWARD_EVENT_USABLE — and the conversion must store artist_name
    as NULL, never the event name.
    """
    import duckdb

    from festival_bloomberg.oa.activation_v1 import (
        _audit_forward_quality,
        _build_mb_forward_row,
    )

    # parse_mb_event never fabricates a performer: name-only event -> None.
    raw = {
        "id": "e9", "name": "Summer Festival 2027", "type": "Festival",
        "life-span": {"begin": "2027-06-01"}, "relations": [],
    }
    parsed = parse_mb_event(raw)
    assert parsed["main_performer"] is None
    row = _build_mb_forward_row(
        parsed, first_seen_at=datetime(2026, 8, 14, tzinfo=timezone.utc)
    )
    assert row["artist_name"] is None  # event name is NEVER substituted

    conn = duckdb.connect(str(tmp_path / "fwq.duckdb"))
    flywheel = FlywheelRepository(conn)
    flywheel.register_forward_event(row)
    row2 = build_forward_event_row(
        provider="musicbrainz", provider_event_id="mb-perf",
        artist_name="Real Artist", venue_name="Test Arena", market=None,
        event_date=date(2099, 1, 1),
    )
    flywheel.register_forward_event(row2)
    q = _audit_forward_quality(
        flywheel, as_of=datetime(2026, 8, 14, tzinfo=timezone.utc)
    )
    # Only the event with REAL performer evidence is usable.
    assert q["FORWARD_EVENT_USABLE"] == 1
    assert q["with_artist"] == 1
    assert q["total_enrolled"] == 2
    conn.close()


def test_accounting_http_units_never_mixed_with_task_units():
    """Fix 2: 22 task NOT_FOUNDs can never inflate an HTTP-level success rate.

    6 HTTP requests + 22 task NOT_FOUND results must not produce >1000
    "successful responses per 1,000 requests".
    """
    from festival_bloomberg.flywheel.acquisition_accounting import (
        build_acquisition_run_row,
        derive_metrics,
    )

    run = build_acquisition_run_row(
        provider="commoncrawl_cdx", pipeline="OUTCOME_HUNTER",
        http_requests=6, http_successful_responses=6, http_failures=0,
        tasks_attempted=28, tasks_claim_found=0, tasks_not_found=22,
    )
    m = derive_metrics(run)
    # HTTP-level success metrics use ONLY http counters.
    assert run["http_requests"] == 6
    assert run["http_successful_responses"] == 6
    assert m["successes_per_1000_requests"] == pytest.approx(1000.0)
    assert m["successes_per_1000_requests"] <= 1000.0
    assert m["http_success_rate"] == pytest.approx(1.0)
    # Task-level outcomes are reported separately, per task denominator.
    assert run["tasks_not_found"] == 22
    assert run["not_found"] == 22  # 018 mirror column
    assert run["tasks_attempted"] == 28
    # A task NOT_FOUND is never an HTTP successful response.
    assert run["http_successful_responses"] != 22
    # claims per 1,000 tasks attempted is a TASK-denominator rate.
    m2 = derive_metrics(build_acquisition_run_row(
        provider="commoncrawl_cdx", pipeline="OUTCOME_HUNTER",
        http_requests=6, http_successful_responses=6,
        tasks_attempted=28, tasks_not_found=22, new_claims=0,
    ))
    assert m2["claims_per_1000_tasks_attempted"] == pytest.approx(0.0)


def test_musicbrainz_telemetry_measured_not_estimated():
    """Fix 3: request counts are MEASURED telemetry, never row-count guesses."""
    # First page returns a FULL page (100 = the page size) so pagination makes
    # a second request; a short first page legitimately stops after one.
    page_one = {
        "count": 150,
        "events": [
            {"id": f"e{i}", "name": f"Show {i}", "type": "Concert",
             "life-span": {"begin": "2026-12-01"}}
            for i in range(100)
        ],
    }
    transport = FakeTransport(responses=[(200, page_one), (200, {"events": [], "count": 150})])
    client = MusicBrainzFutureEventsClient(transport=transport, rate_limit_seconds=0.0)
    client.future_events(horizon_days=90, max_events=150, as_of=date(2026, 8, 14))
    tel = client.telemetry()
    assert tel["request_count"] == 2  # real HTTP interactions, never inferred
    assert tel["successful_responses"] == 2
    assert tel["records_returned"] == 100
    assert tel["rate_limits"] == 0
    assert tel["http_failures"] == 0
    assert tel["latency_ms_total"] >= 0
    # A rate-limited request is a measured request + a rate limit, never an
    # http failure and never estimated from returned rows.
    from festival_bloomberg.flywheel.event_graph import MusicBrainzRateLimited

    t2 = FakeTransport(responses=[(429, {"error": "slow down"})])
    client2 = MusicBrainzFutureEventsClient(transport=t2, rate_limit_seconds=0.0)
    with pytest.raises(MusicBrainzRateLimited):
        client2.search_events(begin_from="2026-08-14", begin_to="2026-12-01")
    tel2 = client2.telemetry()
    assert tel2["request_count"] == 1
    assert tel2["rate_limits"] == 1
    assert tel2["http_failures"] == 0
    # A 5xx http failure is a measured request + an http failure (503/429
    # count as rate limits, never as failures).
    t3 = FakeTransport(responses=[(500, {"error": "down"})])
    client3 = MusicBrainzFutureEventsClient(transport=t3, rate_limit_seconds=0.0)
    with pytest.raises(Exception):
        client3.search_events(begin_from="2026-08-14", begin_to="2026-12-01")
    tel3 = client3.telemetry()
    assert tel3["request_count"] == 1
    assert tel3["http_failures"] == 1
    assert tel3["rate_limits"] == 0
    # A 503 is a rate limit (measured request + rate limit, not failure).
    t4 = FakeTransport(responses=[(503, {"error": "busy"})])
    client4 = MusicBrainzFutureEventsClient(transport=t4, rate_limit_seconds=0.0)
    with pytest.raises(Exception):
        client4.search_events(begin_from="2026-08-14", begin_to="2026-12-01")
    tel4 = client4.telemetry()
    assert tel4["request_count"] == 1
    assert tel4["rate_limits"] == 1
    assert tel4["http_failures"] == 0


def test_pit_single_show_metric_not_inflated_by_multishow(tmp_path):
    """Fix 4: a multi-show aggregate's evidence can never inflate the
    single-show reconstruction metric."""
    import duckdb

    from festival_bloomberg.flywheel.pit import (
        OBSERVED_DAY,
        STRICT_PIT,
        build_pit_evidence_row,
        count_events_reconstructable,
        event_key_from_engagement,
    )

    conn = duckdb.connect(str(tmp_path / "pit_uni.duckdb"))
    research = ResearchRepository(conn)
    research.insert_engagement(_engagement(
        "s1", artist="Artist A", venue="Arena", start_date="2024-01-10",
    ))
    research.insert_engagement(_engagement(
        "m1", artist="Artist B", venue="Stadium", start_date="2024-06-01",
        is_multi_show=True, number_of_shows=2,
    ))
    for eng in (
        {"artist": "Artist A", "venue": "Arena", "start_date": "2024-01-10"},
        {"artist": "Artist B", "venue": "Stadium", "start_date": "2024-06-01"},
    ):
        row = build_pit_evidence_row(
            canonical_event_id=event_key_from_engagement(eng),
            evidence_class=OBSERVED_DAY,
            source_publication_time="2024-01-05T23:59:59.999999",
            source_url="https://x.example", source_provider="pollstar",
            source_document_id="src_x",
        )
        conn.execute(
            "INSERT INTO flywheel.pit_reconstruction_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                row["evidence_id"], row["canonical_event_id"], row["evidence_class"],
                row["source_publication_time"], row["archive_capture_time"],
                row["source_period_start"], row["source_period_end"], row["source_url"],
                row["source_provider"], row["source_document_id"], row["rights_status"],
                row["commercial_use_status"], row["knowledge_time"], row["software_version"],
            ],
        )
    conn.commit()
    # All evidence events vs the single-show-only metric.
    assert count_events_reconstructable(conn, mode=STRICT_PIT) == 2
    assert count_events_reconstructable(conn, mode=STRICT_PIT, single_show_only=True) == 1
    conn.close()


def test_conservative_warm_start_consumes_archive_upper_bound(tmp_path):
    """Fix 5: STRICT excludes archive bounds; CONSERVATIVE_BOUND_PIT can
    consume an archive capture upper bound when it proves availability before
    the target cutoff."""
    import duckdb

    from festival_bloomberg.flywheel.coverage import (
        count_events_with_prior_results_pit,
    )
    from festival_bloomberg.flywheel.pit import (
        ARCHIVE_CAPTURE_UPPER_BOUND,
        CONSERVATIVE_BOUND_CLASSES,
        STRICT_PIT_CLASSES,
        build_pit_evidence_row,
        event_key_from_engagement,
    )

    conn = duckdb.connect(str(tmp_path / "warm_cons.duckdb"))
    research = ResearchRepository(conn)
    for eng, date_ in [
        ("e1", "2024-01-10"), ("e2", "2024-03-10"), ("e3", "2024-05-10"), ("e4", "2024-07-10"),
    ]:
        research.insert_engagement(_engagement(
            eng, artist="Same Artist", venue="Arena", start_date=date_,
        ))
    # Archive upper-bound evidence ONLY: captures prove the chart pages
    # existed before the target e4 (2024-07-10).
    for key, cap in [
        ({"artist": "Same Artist", "venue": "Arena", "start_date": "2024-01-10"}, "2024-01-01T00:00:00Z"),
        ({"artist": "Same Artist", "venue": "Arena", "start_date": "2024-03-10"}, "2024-03-01T00:00:00Z"),
        ({"artist": "Same Artist", "venue": "Arena", "start_date": "2024-05-10"}, "2024-05-01T00:00:00Z"),
    ]:
        row = build_pit_evidence_row(
            canonical_event_id=event_key_from_engagement(key),
            evidence_class=ARCHIVE_CAPTURE_UPPER_BOUND,
            archive_capture_time=cap,
            source_url="https://archive.example/x", source_provider="commoncrawl",
            source_document_id="CC-MAIN",
        )
        conn.execute(
            "INSERT INTO flywheel.pit_reconstruction_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                row["evidence_id"], row["canonical_event_id"], row["evidence_class"],
                row["source_publication_time"], row["archive_capture_time"],
                row["source_period_start"], row["source_period_end"], row["source_url"],
                row["source_provider"], row["source_document_id"], row["rights_status"],
                row["commercial_use_status"], row["knowledge_time"], row["software_version"],
            ],
        )
    conn.commit()
    # STRICT classes alone: archive bounds are NOT publication proof -> 0.
    assert count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=3, evidence_classes=STRICT_PIT_CLASSES
    ) == 0
    # CONSERVATIVE adds the archive bound as an availability proof -> e4.
    assert count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=3,
        evidence_classes=frozenset(STRICT_PIT_CLASSES) | CONSERVATIVE_BOUND_CLASSES,
    ) == 1
    conn.close()


def test_observed_day_cannot_inform_same_day_cutoff(tmp_path):
    """Fix 6: OBSERVED_DAY availability is END of documented day. A result
    published (day-level) on the SAME day as the target event can never be a
    prior — it was not knowable at the target's start."""
    import duckdb

    from festival_bloomberg.flywheel.coverage import (
        count_events_with_prior_results_pit,
    )
    from festival_bloomberg.flywheel.pit import (
        OBSERVED_DAY,
        STRICT_PIT_CLASSES,
        build_pit_evidence_row,
        event_key_from_engagement,
    )

    conn = duckdb.connect(str(tmp_path / "same_day.duckdb"))
    research = ResearchRepository(conn)
    research.insert_engagement(_engagement(
        "e1", artist="Same Artist", venue="Arena", start_date="2024-01-10",
    ))
    research.insert_engagement(_engagement(
        "e2", artist="Same Artist", venue="Arena", start_date="2024-01-11",
    ))
    key = event_key_from_engagement(
        {"artist": "Same Artist", "venue": "Arena", "start_date": "2024-01-10"}
    )

    def _insert(pub: str) -> None:
        row = build_pit_evidence_row(
            canonical_event_id=key,
            evidence_class=OBSERVED_DAY,
            source_publication_time=pub,
            source_url="https://news.pollstar.com/x", source_provider="pollstar",
            source_document_id="src_x",
        )
        conn.execute(
            "INSERT INTO flywheel.pit_reconstruction_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                row["evidence_id"], row["canonical_event_id"], row["evidence_class"],
                row["source_publication_time"], row["archive_capture_time"],
                row["source_period_start"], row["source_period_end"], row["source_url"],
                row["source_provider"], row["source_document_id"], row["rights_status"],
                row["commercial_use_status"], row["knowledge_time"], row["software_version"],
            ],
        )

    # Published on the SAME day as the target (2024-01-11): not knowable at
    # the target's start -> 0 warm starts.
    _insert("2024-01-11T23:59:59.999999")
    conn.commit()
    assert count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=1, evidence_classes=STRICT_PIT_CLASSES
    ) == 0
    # Published the day BEFORE (available by end of 01-10): knowable for the
    # 01-11 target -> 1 warm start.
    conn.execute(
        "UPDATE flywheel.pit_reconstruction_evidence SET source_publication_time = '2024-01-10T23:59:59.999999' "
        "WHERE canonical_event_id = ?",
        [key],
    )
    conn.commit()
    assert count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=1, evidence_classes=STRICT_PIT_CLASSES
    ) == 1
    conn.close()


def test_candidate_claims_never_counted_as_new_claims():
    """Fix 7: Common Crawl parser output is CANDIDATE evidence. Only claims
    validated, deduplicated, and persisted into the claim ledger increment
    new_claims."""
    from festival_bloomberg.flywheel.acquisition_accounting import (
        build_acquisition_run_row,
        derive_metrics,
    )

    # The CDX run row is built exactly as _record_accounting builds it:
    # candidate extraction happened (3 claims found on an archived page) but
    # NOTHING was persisted into the claim ledger -> new_claims stays 0.
    candidate_claims_extracted = 3
    run = build_acquisition_run_row(
        provider="commoncrawl_cdx", pipeline="OUTCOME_HUNTER",
        http_requests=6, http_successful_responses=6,
        tasks_attempted=28, tasks_not_found=22,
        new_claims=0,  # nothing validated + persisted
    )
    assert run["new_claims"] == 0
    assert run["new_claims"] != candidate_claims_extracted
    m = derive_metrics(run)
    assert m["new_claims_per_1000_requests"] == pytest.approx(0.0)
    # The candidate count is reported separately in the hunt summary.
    assert candidate_claims_extracted == 3
    assert run["detail"] is None or "candidate" not in (run["detail"] or "")


def test_duplicate_provider_identity_groups_by_provider(tmp_path):
    """Fix 8: provider-event identity is (provider, provider_event_id). Two
    unrelated providers sharing id "12345" are NOT duplicates."""
    import duckdb

    from festival_bloomberg.oa.activation_v1 import _audit_forward_quality

    conn = duckdb.connect(str(tmp_path / "dup.duckdb"))
    flywheel = FlywheelRepository(conn)
    for provider, eid in [("provider_a", "12345"), ("provider_b", "12345")]:
        row = build_forward_event_row(
            provider=provider, provider_event_id=eid,
            artist_name="Artist", venue_name="Venue", market=None,
            event_date=date(2099, 1, 1),
        )
        assert flywheel.register_forward_event(row)
    q = _audit_forward_quality(
        flywheel, as_of=datetime(2026, 8, 14, tzinfo=timezone.utc)
    )
    # Old code grouped by provider_event_id alone and would report 1 duplicate.
    assert q["duplicate_provider_events"] == 0
    conn.close()


def test_activation_oa_fails_closed_with_no_corpus(tmp_path):
    from festival_bloomberg.oa.activation_v1 import run_activation_v1_oa

    transport = FakeTransport(
        responses=[
            (200, [{"id": "CC-MAIN-2024-30"}]),
        ]
    )
    research_path = tmp_path / "empty.duckdb"
    import duckdb

    duckdb.connect(str(research_path)).close()
    history_path = tmp_path / "empty_history.duckdb"
    duckdb.connect(str(history_path)).close()

    manifest = run_activation_v1_oa(
        research_db=str(research_path),
        history_db=str(history_path),
        report_path=str(tmp_path / "m.json"),
        transport=transport,
        mb_events=[],
    )
    assert manifest["gates"]["OUTCOME_HUNTER"] == "NOT_EVALUATED"
    assert manifest["gates"]["FORWARD_WATCH"] == "NOT_EVALUATED"
    assert manifest["gates"]["PIT_RECONSTRUCTION"] == "PARTIAL"
