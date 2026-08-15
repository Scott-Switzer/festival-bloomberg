"""Offline regressions for PRE_EVENT_CUTOFF_ACQUISITION_V1.

Covers the decision-time taxonomy, booking/offer bound semantics, interval
evidence, day-granularity safety, warm-start-by-cutoff, and the live OA driver
end-to-end through persisted (offline) rows. No network; no fabricated dates.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from festival_bloomberg.flywheel.cutoffs import (
    CUTOFF_ANNOUNCEMENT,
    CUTOFF_BOOKING_OR_OFFER,
    CUTOFF_EVENT_DATE,
    CUTOFF_GENERAL_ONSALE,
    CUTOFF_RESULT_PUBLICATION,
    CONSERVATIVE_BOUND_PIT,
    KIND_ANNOUNCEMENT_UPPER_BOUND,
    KIND_FIRST_SEEN_UPPER_BOUND,
    KIND_OBSERVED_BOOKING_DATE,
    STRICT_PIT,
    build_cutoff_evidence_row,
    decision_time_coverage,
    derive_event_date_cutoff,
    derive_forward_announcement_and_booking_bounds,
    derive_result_publication_cutoff,
    effective_cutoff_timestamp,
    prior_outcome_distribution,
    validate_cutoff_type,
)
from festival_bloomberg.flywheel.pit import (
    ARCHIVE_CAPTURE_UPPER_BOUND,
    OBSERVED_DAY,
    build_archive_upper_bound_evidence,
    build_pit_evidence_row,
    event_key_from_engagement,
)
from festival_bloomberg.flywheel.repository import FlywheelRepository
from festival_bloomberg.research.boxscore import (
    HEADCOUNT_REPORTED_ATTENDANCE,
    BoxofficeEngagement,
)
from festival_bloomberg.research.repository import ResearchRepository


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
def test_cutoff_types_are_distinct_and_closed():
    # announcement != booking != onsale: the decision points are never collapsed.
    assert CUTOFF_ANNOUNCEMENT != CUTOFF_BOOKING_OR_OFFER
    assert CUTOFF_GENERAL_ONSALE != CUTOFF_ANNOUNCEMENT
    assert CUTOFF_BOOKING_OR_OFFER != CUTOFF_GENERAL_ONSALE
    assert validate_cutoff_type(CUTOFF_BOOKING_OR_OFFER) == CUTOFF_BOOKING_OR_OFFER
    with pytest.raises(ValueError):
        validate_cutoff_type("SHOWTIME")


def test_announcement_is_never_a_booking_date():
    # A public announcement upper bound is a BOUND, never an exact booking date.
    fw = derive_forward_announcement_and_booking_bounds(
        {
            "watch_event_id": "watch_1",
            "provider_event_id": "e1",
            "provider": "musicbrainz",
            "first_seen_at": "2026-08-15T10:00:00",
            "source_url": "https://x",
        }
    )
    announcement, booking = fw
    assert announcement["cutoff_type"] == CUTOFF_ANNOUNCEMENT
    assert announcement["cutoff_kind"] == KIND_FIRST_SEEN_UPPER_BOUND
    assert booking["cutoff_type"] == CUTOFF_BOOKING_OR_OFFER
    assert booking["cutoff_kind"] == KIND_ANNOUNCEMENT_UPPER_BOUND
    assert booking["cutoff_kind"] != KIND_OBSERVED_BOOKING_DATE  # not a fake exact date
    assert booking["cutoff_timestamp"] is None  # never an exact booking instant
    assert booking["upper_bound"] == "2026-08-15T10:00:00"
    assert booking["bound_semantics"] == "booking_no_later_than_announcement"


def test_archive_first_seen_is_upper_bound_not_publication():
    fw = derive_forward_announcement_and_booking_bounds(
        {
            "watch_event_id": "watch_2",
            "provider_event_id": "e2",
            "provider": "musicbrainz",
            "first_seen_at": "2026-08-15T10:00:00",
        }
    )
    row = fw[0]
    assert row["evidence_class"] == ARCHIVE_CAPTURE_UPPER_BOUND
    assert row["archive_capture_time"] == "2026-08-15T10:00:00"
    assert row["cutoff_timestamp"] is None  # availability BY T, not publication AT T
    assert row["upper_bound"] == "2026-08-15T10:00:00"


def test_booking_interval_never_becomes_midpoint():
    # Interval evidence (lower + upper) is preserved as-is; STRICT has no exact
    # instant to use, and nothing fabricates a midpoint.
    row = build_cutoff_evidence_row(
        canonical_event_id="boxoffice_a_b_2024-01-01",
        cutoff_type=CUTOFF_BOOKING_OR_OFFER,
        cutoff_kind=KIND_ANNOUNCEMENT_UPPER_BOUND,
        evidence_class=ARCHIVE_CAPTURE_UPPER_BOUND,
        granularity="EXACT",
        lower_bound="2024-01-01T00:00:00",
        upper_bound="2024-02-01T00:00:00",
        bound_semantics="booking_no_later_than_announcement",
    )
    assert row["lower_bound"] == "2024-01-01T00:00:00"
    assert row["upper_bound"] == "2024-02-01T00:00:00"
    assert row["cutoff_timestamp"] is None  # no midpoint invented
    assert effective_cutoff_timestamp(row, mode=STRICT_PIT) is None
    # Conservative mode prefers the (optimistic) upper bound; never a midpoint.
    assert effective_cutoff_timestamp(row, mode=CONSERVATIVE_BOUND_PIT) == datetime(
        2024, 2, 1
    )


def test_day_granularity_result_publication_is_end_of_day():
    # OBSERVED_DAY publication -> availability at END of documented day: a
    # same-day result can never inform a cutoff earlier that same day.
    pit = build_pit_evidence_row(
        canonical_event_id="boxoffice_a_b_2024-01-01",
        evidence_class=OBSERVED_DAY,
        source_publication_time="2024-01-10T23:59:59.999999",
        source_url="https://news.pollstar.com/x",
        source_provider="pollstar",
        source_document_id="src_x",
    )
    cutoff = derive_result_publication_cutoff(pit)
    assert cutoff["cutoff_type"] == CUTOFF_RESULT_PUBLICATION
    assert cutoff["cutoff_timestamp"].startswith("2024-01-10T23:59:59")
    # A cutoff earlier on the SAME day must not see this result.
    t = effective_cutoff_timestamp(cutoff, mode=STRICT_PIT)
    assert t is not None and t >= datetime(2024, 1, 10, 23, 59, 59)
    # Archive-bound result publication is a BOUND, never an exact instant.
    arch = build_archive_upper_bound_evidence(
        canonical_event_id="boxoffice_a_b_2024-01-01",
        capture_time="2024-01-11T08:00:00Z",
        source_url="https://x",
        source_provider="commoncrawl",
        source_document_id="CC-1",
    )
    arch_cutoff = derive_result_publication_cutoff(arch)
    assert arch_cutoff["cutoff_timestamp"] is None
    assert arch_cutoff["upper_bound"].startswith("2024-01-11")
    assert effective_cutoff_timestamp(arch_cutoff, mode=STRICT_PIT) is None
    assert effective_cutoff_timestamp(arch_cutoff, mode=CONSERVATIVE_BOUND_PIT) is not None


def test_event_date_cutoff_is_day_granularity_start_of_day():
    eng = {
        "engagement_id": "e1", "artist": "A", "venue": "B", "market": "M",
        "start_date": date(2024, 5, 10), "reporting_source": "pollstar",
        "source_url": "https://x", "rights_status": "RESEARCH_ONLY",
        "commercial_use_status": "RESEARCH_ONLY",
    }
    row = derive_event_date_cutoff(eng)
    assert row["cutoff_type"] == CUTOFF_EVENT_DATE
    assert row["cutoff_timestamp"].startswith("2024-05-10T00:00:00")
    assert derive_event_date_cutoff({**eng, "start_date": None}) is None


def test_first_seen_immutable():
    # Re-deriving bounds from the same persisted first_seen never changes it.
    fw = {"watch_event_id": "watch_3", "first_seen_at": "2026-08-15T10:00:00"}
    r1 = derive_forward_announcement_and_booking_bounds(fw)[0]["upper_bound"]
    r2 = derive_forward_announcement_and_booking_bounds(fw)[0]["upper_bound"]
    assert r1 == r2 == "2026-08-15T10:00:00"


# ---------------------------------------------------------------------------
# Warm-start by cutoff
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


def _insert_pit(conn, *, artist, venue, start_date, pub):
    row = build_pit_evidence_row(
        canonical_event_id=event_key_from_engagement(
            {"artist": artist, "venue": venue, "start_date": start_date}
        ),
        evidence_class=OBSERVED_DAY,
        source_publication_time=f"{pub}T23:59:59.999999",
        source_url="https://news.pollstar.com/x",
        source_provider="pollstar",
        source_document_id=f"src_{artist}_{start_date}",
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


def _seed_warm_start_db(conn):
    research = ResearchRepository(conn)
    flywheel = FlywheelRepository(conn)
    for i, d in enumerate(("2024-01-10", "2024-03-10", "2024-05-10", "2024-07-10")):
        research.insert_engagement(_engagement(
            f"e{i+1}", artist="Same Artist", venue="Arena A", start_date=d,
        ))
    # e1, e2, e3 results published before e4's event date (knowable priors).
    _insert_pit(conn, artist="Same Artist", venue="Arena A", start_date="2024-01-10", pub="2024-01-05")
    _insert_pit(conn, artist="Same Artist", venue="Arena A", start_date="2024-03-10", pub="2024-03-05")
    _insert_pit(conn, artist="Same Artist", venue="Arena A", start_date="2024-05-10", pub="2024-05-05")
    # e4's own result published after its event date.
    _insert_pit(conn, artist="Same Artist", venue="Arena A", start_date="2024-07-10", pub="2024-07-15")
    conn.commit()
    return research, flywheel


def test_warm_start_differs_by_cutoff_and_unknown_is_not_zero(tmp_path):
    import duckdb

    conn = duckdb.connect(str(tmp_path / "ws.duckdb"))
    research, flywheel = _seed_warm_start_db(conn)

    # Derive EVENT_DATE cutoffs for the single-show universe.
    for eng in research.query_engagements():
        if eng.get("is_multi_show"):
            continue
        row = derive_event_date_cutoff(eng)
        if row:
            flywheel.insert_pre_event_cutoff(row)

    event_date = prior_outcome_distribution(
        conn, cutoff_type=CUTOFF_EVENT_DATE, dimension="artist", mode=STRICT_PIT, min_prior=3
    )
    # e4 has 3 same-artist priors published before its event date.
    assert event_date["targets_with_known_cutoff"] == 4
    assert event_date["targets_with_3_plus_priors"] == 1

    # BOOKING / ANNOUNCEMENT / ONSALE have NO historical cutoff evidence: they
    # are UNKNOWN (reported separately), never silently zeroed.
    for cutoff_type in (CUTOFF_BOOKING_OR_OFFER, CUTOFF_ANNOUNCEMENT, CUTOFF_GENERAL_ONSALE):
        dist = prior_outcome_distribution(
            conn, cutoff_type=cutoff_type, dimension="artist", mode=STRICT_PIT, min_prior=3
        )
        assert dist["targets_with_known_cutoff"] == 0
        assert dist["targets_with_unknown_cutoff"] == 4
        assert dist["targets_with_3_plus_priors"] == 0
    conn.close()


def test_prior_unavailable_until_publication(tmp_path):
    import duckdb

    conn = duckdb.connect(str(tmp_path / "pub.duckdb"))
    research, flywheel = _seed_warm_start_db(conn)
    for eng in research.query_engagements():
        if not eng.get("is_multi_show"):
            row = derive_event_date_cutoff(eng)
            if row:
                flywheel.insert_pre_event_cutoff(row)

    # A prior published AFTER the target event date must NOT count: move e3's
    # publication to after e4's event (it is then not knowable at e4's date).
    conn.execute(
        "UPDATE flywheel.pit_reconstruction_evidence SET source_publication_time = '2024-08-01T23:59:59.999999' "
        "WHERE canonical_event_id = ?",
        [event_key_from_engagement({"artist": "Same Artist", "venue": "Arena A", "start_date": "2024-05-10"})],
    )
    conn.commit()
    dist = prior_outcome_distribution(
        conn, cutoff_type=CUTOFF_EVENT_DATE, dimension="artist", mode=STRICT_PIT, min_prior=3
    )
    # Only e1 and e2 remain knowable priors for e4 -> e4 no longer reaches 3.
    assert dist["targets_with_3_plus_priors"] == 0
    # e3 (priors e1,e2) and e4 (priors e1,e2) both have exactly 2 knowable
    # priors; e3's own result is no longer knowable before e4's date.
    assert dist["prior_distribution"]["2"] == 2
    conn.close()


def test_multishow_never_a_prior(tmp_path):
    import duckdb

    conn = duckdb.connect(str(tmp_path / "multi.duckdb"))
    research = ResearchRepository(conn)
    flywheel = FlywheelRepository(conn)
    research.insert_engagement(_engagement(
        "e1", artist="Same Artist", venue="Arena A", start_date="2024-01-10",
    ))
    research.insert_engagement(_engagement(
        "e2", artist="Same Artist", venue="Arena A", start_date="2024-03-10",
    ))
    # A multi-show aggregate with the same artist must never become a prior.
    research.insert_engagement(_engagement(
        "m1", artist="Same Artist", venue="Stadium", start_date="2024-02-10",
        is_multi_show=True, number_of_shows=3,
    ))
    _insert_pit(conn, artist="Same Artist", venue="Arena A", start_date="2024-01-10", pub="2024-01-05")
    _insert_pit(conn, artist="Same Artist", venue="Stadium", start_date="2024-02-10", pub="2024-02-05")
    conn.commit()
    for eng in research.query_engagements():
        if not eng.get("is_multi_show"):
            row = derive_event_date_cutoff(eng)
            if row:
                flywheel.insert_pre_event_cutoff(row)
    dist = prior_outcome_distribution(
        conn, cutoff_type=CUTOFF_EVENT_DATE, dimension="artist", mode=STRICT_PIT, min_prior=1
    )
    # e2 has exactly ONE prior (e1): the multi-show m1 never counts.
    assert dist["prior_distribution"]["1"] == 1
    assert dist["prior_distribution"]["2"] == 0
    conn.close()


# ---------------------------------------------------------------------------
# Live OA driver (hermetic, persisted rows only)
# ---------------------------------------------------------------------------
def test_pre_event_cutoff_oa_end_to_end(tmp_path):
    import duckdb

    from festival_bloomberg.oa.pre_event_cutoffs import run_pre_event_cutoff_oa

    research_path = tmp_path / "corpus.duckdb"
    conn = duckdb.connect(str(research_path))
    research = ResearchRepository(conn)
    flywheel = FlywheelRepository(conn)
    research.insert_engagement(_engagement("eng_1"))
    research.insert_engagement(_engagement(
        "eng_2", artist="Ariana Grande", start_date="2024-03-10",
    ))
    _insert_pit(conn, artist="Zach Bryan", venue="United Center", start_date="2024-03-05", pub="2024-03-07")
    _insert_pit(conn, artist="Ariana Grande", venue="United Center", start_date="2024-03-10", pub="2024-03-12")
    # Enroll a future forward event (first-seen bound source).
    from festival_bloomberg.flywheel.forward_discovery import build_forward_event_row

    flywheel.register_forward_event(build_forward_event_row(
        provider="musicbrainz", provider_event_id="mb-1", artist_name="Future Artist",
        venue_name="Future Arena", market=None, event_date=date(2099, 1, 1),
        first_seen_at=datetime(2026, 8, 15, 10, 0, 0),
    ))
    conn.close()

    manifest = run_pre_event_cutoff_oa(
        research_db=str(research_path),
        report_path=str(tmp_path / "pec_manifest.json"),
    )

    assert manifest["software_version"] == "pre_event_cutoff_acquisition_v1"
    cutoffs = manifest["cutoffs_inserted"]
    assert cutoffs["event_date_derived"] == 2
    assert cutoffs["result_publication_derived"] == 2
    assert cutoffs["forward_announcement_bound"] == 1
    assert cutoffs["forward_booking_bound"] == 1
    # NEW decision-useful cutoffs are only the forward pre-event bounds; the
    # event date and result publication re-express facts the corpus already
    # knew (bookkeeping, not new acquisition).
    assert cutoffs["new_decision_useful_cutoffs"] == 2

    ws = manifest["warm_start_by_cutoff"]
    assert ws[CUTOFF_EVENT_DATE]["artist"]["strict"]["targets_with_known_cutoff"] == 2
    # Historical pre-event cutoffs remain UNKNOWN (not zeroed).
    for ct in (CUTOFF_BOOKING_OR_OFFER, CUTOFF_ANNOUNCEMENT, CUTOFF_GENERAL_ONSALE):
        assert ws[ct]["artist"]["strict"]["targets_with_known_cutoff"] == 0
        assert ws[ct]["artist"]["strict"]["targets_with_unknown_cutoff"] == 2

    coverage = manifest["decision_time_coverage"]
    assert coverage["EVENTS_WITH_EVENT_DATE"] == 2
    assert coverage["EVENTS_WITH_RESULT_PUBLICATION"] == 2
    assert coverage["FORWARD_EVENTS_WITH_ANNOUNCEMENT_BOUND"] == 1
    assert coverage["FORWARD_EVENTS_WITH_BOOKING_BOUND"] == 1

    matrix = manifest["historical_cutoff_matrix_summary"]
    assert matrix[CUTOFF_EVENT_DATE]["EXACT"] == 2
    assert matrix[CUTOFF_ANNOUNCEMENT]["UNKNOWN"] == 2
    assert matrix[CUTOFF_BOOKING_OR_OFFER]["UNKNOWN"] == 2

    readiness = manifest["comparable_engine_readiness"]
    assert readiness["EVENT_DATE"]["usable_target_events"] == 2
    assert readiness["BOOKING_OR_OFFER"]["usable_target_events"] == 0
    assert readiness["BOOKING_OR_OFFER"]["status"] == "NOT_READY"

    # The derivation run is honest: pure warehouse derivation, no HTTP.
    conn = duckdb.connect(str(research_path), read_only=True)
    try:
        run = conn.execute(
            "SELECT http_requests, request_count_status, new_cutoffs, requests "
            "FROM flywheel.provider_acquisition_runs WHERE provider = 'pre_event_cutoff_derivation'"
        ).fetchone()
        assert run[0] is None  # no HTTP performed
        assert run[1] == "UNKNOWN"
        assert run[2] == 2  # only the genuinely-new forward bounds
        assert run[3] is None
        cutoff_rows = conn.execute(
            "SELECT COUNT(*) FROM flywheel.pre_event_cutoff_evidence"
        ).fetchone()[0]
        assert cutoff_rows == 2 + 2 + 1 + 1  # event_date + result + ann + booking
    finally:
        conn.close()

    assert (tmp_path / "pec_manifest.json").is_file()
