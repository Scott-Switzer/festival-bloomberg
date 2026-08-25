"""MARKET_COMPETITIVE_CALENDAR_V1 regression tests.

Covers: event dedup, target self-exclusion, same-day / +-3 / +-7 / +-14
windows, exact date delta, segment/genre/subgenre/family preservation,
haversine distance + 5/10/25/50-mile buckets, missing-coordinates UNKNOWN,
earliest-knowledge-time, PIT tri-state (post-cutoff can never be known-before-
cutoff, unknown stays unknown), recursive deep-page splitting with explicit
partition states (no silent truncation), classification changes staying
observable (append-only), music-only vs full-calendar comparison, the buyer
workspace route contract, and the absence of any opaque competition score.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.planning.competitive_calendar import (
    calendar_for_proposed_show,
    competitive_calendar,
    distance_bucket,
    haversine_miles,
)
from festival_bloomberg.planning.competition import (
    BUCKET_KNOWN,
    BUCKET_POST,
    BUCKET_UNKNOWN,
)

T0 = "2026-08-01T00:00:00"


def _db(tmp_path) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect(str(tmp_path / "cal.duckdb"))
    apply_pending_migrations(db)
    return db


def _snap(
    db,
    event_id: str,
    date: str,
    *,
    segment: str = "Music",
    segment_id: str | None = None,
    genre: str | None = None,
    genre_id: str | None = None,
    subgenre: str | None = None,
    subgenre_id: str | None = None,
    family: str | None = None,
    city: str = "Chicago",
    state: str = "IL",
    lat: float | None = None,
    lon: float | None = None,
    venue_id: str | None = None,
    venue_name: str | None = None,
    kt: str = T0,
    retrieved: str | None = None,
    event_name: str | None = None,
) -> None:
    retrieved = retrieved or kt
    db.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, venue_id,
             venue_name, city, state_code, country_code, latitude, longitude,
             local_date, segment, segment_id, genre, genre_id, subgenre,
             subgenre_id, family, retrieved_at, knowledge_time, content_hash,
             rights_status, commercial_use_status, ingested_at)
        VALUES (?, 'ticketmaster', ?, ?, ?, ?, ?, 'US', ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, 'h', 'RESEARCH_ONLY', 'PROTOTYPE_ONLY', CURRENT_TIMESTAMP)
        """,
        [
            f"{event_id}|{retrieved}", event_id, event_name or f"Event {event_id}",
            venue_id, venue_name, city, state, lat, lon, date,
            segment, segment_id, genre, genre_id, subgenre, subgenre_id, family,
            retrieved, kt,
        ],
    )


def _calendar(db, *, date="2026-09-10", city="Chicago", state="IL", **kw):
    return competitive_calendar(
        db, city=city, state_code=state, target_date=date, **kw
    )


# --- geography ------------------------------------------------------------


def test_haversine_miles_and_buckets():
    assert haversine_miles(0.0, 0.0, 0.0, 0.0) == 0.0
    chicago = (41.8781, -87.6298)
    nyc = (40.7128, -74.0060)
    assert 700 < haversine_miles(*chicago, *nyc) < 800
    assert haversine_miles(None, -87.6, 41.8, -87.6) is None
    assert distance_bucket(None) == "UNKNOWN"
    assert distance_bucket(0.0) == "within_5"
    assert distance_bucket(6.0) == "within_10"
    assert distance_bucket(12.0) == "within_25"
    assert distance_bucket(30.0) == "within_50"
    assert distance_bucket(80.0) == "beyond_50"


def test_coordinates_missing_is_unknown(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "e_competitor", "2026-09-10", venue_name="NoCoords Hall")
        cal = _calendar(db, target_lat=41.8781, target_lon=-87.6298)
        assert cal["status"] == "OBSERVED"
        row = cal["rows"][0]
        assert row["distance_miles"] is None
        assert row["distance_bucket"] == "UNKNOWN"
        assert cal["distance"]["UNKNOWN"] == 1
    finally:
        db.close()


def test_distance_buckets_in_calendar(tmp_path):
    db = _db(tmp_path)
    try:
        # Competitors south of the target (1 deg lat ~= 69 mi):
        # e_near ~2.6 mi, e_far ~8 mi away.
        _snap(db, "e_far", "2026-09-10", lat=41.76, lon=-87.6)
        _snap(db, "e_near", "2026-09-10", lat=41.84, lon=-87.62)
        cal = _calendar(db, target_lat=41.8781, target_lon=-87.6298, target_venue_id="v1")
        buckets = {r["event_id"]: r["distance_bucket"] for r in cal["rows"]}
        assert buckets["e_near"] == "within_5"
        assert buckets["e_far"] == "within_10"
    finally:
        db.close()


# --- windows / dedup / exclusion ------------------------------------------


def test_same_day_and_pm_windows_with_exact_delta(tmp_path):
    db = _db(tmp_path)
    try:
        for delta, eid in ((0, "e0"), (3, "e3"), (7, "e7"), (14, "e14"), (15, "e15")):
            d = "2026-09-10" if delta == 0 else (
                f"2026-09-{10 + delta}" if 10 + delta <= 30 else "2026-09-24")
            # 10+7=17, 10+14=24 -> all within September; 10+15=25 valid too.
            _snap(db, eid, f"2026-09-{10 + delta:02d}")
        cal = _calendar(db)
        by_id = {r["event_id"]: r for r in cal["rows"]}
        assert by_id["e0"]["windows"] == ["pm0", "pm3", "pm7", "pm14"]
        assert by_id["e3"]["windows"] == ["pm3", "pm7", "pm14"]
        assert by_id["e7"]["windows"] == ["pm7", "pm14"]
        assert by_id["e14"]["windows"] == ["pm14"]
        assert "e15" not in by_id
        assert by_id["e3"]["date_delta_days"] == 3
        assert cal["windows"]["pm0"]["total"] == 1
        assert cal["windows"]["pm3"]["total"] == 2
        assert cal["windows"]["pm14"]["total"] == 4
    finally:
        db.close()


def test_target_event_self_exclusion(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "target", "2026-09-10", venue_id="v1")
        _snap(db, "competitor", "2026-09-10", venue_id="v2")
        cal = _calendar(db, target_event_id="target", target_venue_id="v1")
        ids = {r["event_id"] for r in cal["rows"]}
        assert ids == {"competitor"}
        assert cal["windows"]["pm0"]["total"] == 1
    finally:
        db.close()


def test_event_dedup_across_snapshots(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "same_event", "2026-09-10", kt="2026-08-01T00:00:00")
        _snap(db, "same_event", "2026-09-10", kt="2026-08-05T00:00:00")
        _snap(db, "same_event", "2026-09-10", kt="2026-08-09T00:00:00")
        cal = _calendar(db)
        assert len(cal["rows"]) == 1
        assert cal["rows"][0]["earliest_knowledge_time"].startswith("2026-08-01")
        assert cal["rows"][0]["snapshot_count"] == 3
    finally:
        db.close()


def test_classification_changes_observable_not_rewritten(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "e1", "2026-09-10", segment="Music", kt="2026-08-01T00:00:00")
        _snap(db, "e1", "2026-09-10", segment="Arts & Theatre", kt="2026-08-05T00:00:00")
        # Append-only: both snapshots persist; the event-level row uses the
        # earliest snapshot's classification.
        n = db.execute(
            "SELECT count(*) FROM events.provider_event_snapshots WHERE platform_object_id='e1'"
        ).fetchone()[0]
        assert n == 2
        cal = _calendar(db)
        assert cal["rows"][0]["segment"] == "Music"
    finally:
        db.close()


# --- taxonomy --------------------------------------------------------------


def test_segment_genre_subgenre_family_preserved(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "e1", "2026-09-10", segment="Sports", segment_id="KZSeg",
              genre="Basketball", genre_id="KZGen", subgenre="NBA",
              subgenre_id="KZSub", family="false", venue_name="Arena")
        row = _calendar(db)["rows"][0]
        assert row["segment"] == "Sports"
        assert row["segment_id"] == "KZSeg"
        assert row["genre"] == "Basketball"
        assert row["genre_id"] == "KZGen"
        assert row["subgenre"] == "NBA"
        assert row["subgenre_id"] == "KZSub"
        assert row["family"] == "false"
    finally:
        db.close()


def test_no_opaque_competition_score(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "e1", "2026-09-10", segment="Sports")
        _snap(db, "e2", "2026-09-10", segment="Music")
        cal = _calendar(db)
        serialized = json.dumps(cal)
        assert "score" not in serialized.lower()
        assert cal["status"] == "OBSERVED"
    finally:
        db.close()


# --- PIT semantics ---------------------------------------------------------


def test_post_cutoff_event_never_known_before_cutoff(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "known", "2026-09-10", kt="2026-07-01T00:00:00")
        _snap(db, "post", "2026-09-10", kt="2026-08-20T00:00:00")  # after cutoff
        cal = _calendar(db, research_cutoff="2026-08-10")
        known_ids = {r["event_id"] for r in cal["known_at_cutoff"]}
        post_ids = {r["event_id"] for r in cal["observed_after_cutoff"]}
        assert known_ids == {"known"}
        assert post_ids == {"post"}
        # Same-day counts never mix the two.
        assert cal["windows"]["pm0"][BUCKET_KNOWN] == {"Music": 1}
        assert cal["windows"]["pm0"][BUCKET_POST] == {"Music": 1}
        assert cal["pit_mode"] == "PIT"
    finally:
        db.close()


def test_unknown_knowledge_time_stays_unknown():
    # knowledge_time is NOT NULL on persisted snapshots (retrieval time always
    # exists), but a missing/unknown knowability must never be treated as zero
    # or as known — it stays in the UNKNOWN bucket.
    from festival_bloomberg.planning.competition import _classify

    assert _classify(None, __import__("datetime").date(2026, 8, 10)) == BUCKET_UNKNOWN
    assert _classify("2026-08-01T00:00:00", __import__("datetime").date(2026, 8, 10)) == BUCKET_KNOWN
    assert _classify("2026-08-20T00:00:00", __import__("datetime").date(2026, 8, 10)) == BUCKET_POST


def test_non_pit_mode_without_cutoff(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "e1", "2026-09-10", kt="2026-08-20T00:00:00")
        cal = _calendar(db)  # no research_cutoff
        assert cal["pit_mode"] == "NON_PIT"
        assert len(cal["known_at_cutoff"]) == 1
        assert cal["status"] == "OBSERVED"
    finally:
        db.close()


def test_proposed_show_calendar_no_exclusion(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "existing", "2026-09-10", venue_id="other")
        cal = calendar_for_proposed_show(db, city="Chicago", state_code="IL",
                                         date="2026-09-10")
        assert len(cal["rows"]) == 1
        assert cal["rows"][0]["event_id"] == "existing"
        assert cal["status"] == "OBSERVED"
    finally:
        db.close()


# --- music-only vs full calendar -------------------------------------------


def test_full_calendar_adds_non_music_context(tmp_path):
    db = _db(tmp_path)
    try:
        _snap(db, "target", "2026-09-10", segment="Music", venue_id="tv")
        _snap(db, "music_comp", "2026-09-10", segment="Music", venue_id="m1")
        _snap(db, "sports_comp", "2026-09-10", segment="Sports", venue_id="s1")
        _snap(db, "family_comp", "2026-09-13", segment="Family", venue_id="f1")
        cal = _calendar(db, target_event_id="target")
        w0 = cal["windows"]["pm0"]
        # Music-only context: same-day music exists.
        assert w0[BUCKET_KNOWN].get("Music", 0) == 1
        # Full-calendar context: non-music same-day exists too.
        assert w0[BUCKET_KNOWN].get("Sports", 0) == 1
        # +-3 window picks up the family event.
        assert cal["windows"]["pm3"][BUCKET_KNOWN].get("Family", 0) == 1
        # The same target in a music-only universe (sports/family relabeled
        # as Music) has no non-music context.
        db.execute("UPDATE events.provider_event_snapshots SET segment='Music' "
                   "WHERE platform_object_id IN ('sports_comp','family_comp')")
        cal2 = _calendar(db, target_event_id="target")
        w0_2 = cal2["windows"]["pm0"]
        assert set(w0_2[BUCKET_KNOWN].keys()) == {"Music"}
    finally:
        db.close()


# --- recursive split / partition manifest -----------------------------------


def _fake_record(event_id: str, date: str = "2026-09-10") -> dict:
    return {
        "platform_object_id": event_id,
        "event_name": f"Event {event_id}",
        "attractions": [],
        "ticketmaster_venue_id": None,
        "venue_name": None,
        "city": "Chicago",
        "state_code": "IL",
        "country_code": "US",
        "latitude": None,
        "longitude": None,
        "local_date": date,
        "local_time": None,
        "event_time": None,
        "timezone": None,
        "event_status": "onsale",
        "onsale_start": None,
        "onsale_end": None,
        "presales": [],
        "price_min": None,
        "price_max": None,
        "price_currency": None,
        "price_type": None,
        "promoter": None,
        "classifications": {"segment": "Music"},
        "event_type": None,
        "canonical_url": None,
        "content_hash": f"h_{event_id}",
    }


class _FakeSweepProvider:
    """Mimics TicketmasterProvider.acquire for partition-state tests.

    The first call (the wide window) reports a total above the deep-paging
    ceiling and is truncated; the two narrow children are complete.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.configured_result = True

    def configured(self) -> bool:
        return self.configured_result

    def acquire(self, request):
        from festival_bloomberg.acquisition.contracts import (
            AcquisitionResult,
            AcquisitionStatus,
            utc_now,
        )

        self.calls += 1
        started = utc_now()
        if self.calls == 1:
            # Parent: reported total exceeds ceiling, served only 1000.
            records = tuple(_fake_record(f"p{i}") for i in range(50))
            meta = {
                "pages_fetched": 1,
                "items_fetched": 50,
                "reported_total": 2000,
                "complete": False,
                "truncated": True,
                "coverage_status": "TRUNCATED_BY_CAP",
            }
        else:
            records = tuple(_fake_record(f"child{self.calls}_{i}") for i in range(2))
            meta = {
                "pages_fetched": 1,
                "items_fetched": 2,
                "reported_total": 2,
                "complete": True,
                "truncated": False,
                "coverage_status": "COMPLETE",
            }
        return AcquisitionResult(
            request_id=request.request_id,
            provider="ticketmaster",
            provider_endpoint="fake",
            status=AcquisitionStatus.SUCCESS,
            started_at=started,
            completed_at=started,
            record_count=len(records),
            provider_metadata={"pagination": meta, "provider_version": "t", "parser_version": "p"},
            records=records,
        )


def test_recursive_split_no_silent_truncation(tmp_path):
    from datetime import timedelta

    from festival_bloomberg.oa.data_fabric import _sweep_window
    from festival_bloomberg.planning.competitive_calendar import _d  # noqa: F401

    db = _db(tmp_path)
    try:
        summary = {
            "status": "RUNNING", "partitions": 0, "requests": 0,
            "rate_limited": 0, "provider_errors": 0, "partitions_split": 0,
            "partitions_complete": 0, "partitions_truncated": 0,
            "events_persisted": 0, "distinct_events": 0,
        }
        start = __import__("datetime").datetime(2026, 9, 1)
        end = start + timedelta(days=30)
        _sweep_window(
            db, _FakeSweepProvider(), "Chicago", "IL", start, end,
            depth=0, parent_id=None, summary=summary,
            run_retrieved="2026-08-01T00:00:00", classification_name="Sports",
        )
        rows = db.execute(
            "SELECT status, parent_partition_id, depth, classification_name "
            "FROM terminal.acquisition_partitions ORDER BY depth"
        ).fetchall()
        statuses = sorted(r[0] for r in rows)
        assert statuses == ["COMPLETE", "COMPLETE", "SPLIT"]
        parent = db.execute(
            "SELECT partition_id FROM terminal.acquisition_partitions WHERE status='SPLIT'"
        ).fetchone()[0]
        children = db.execute(
            "SELECT count(*) FROM terminal.acquisition_partitions "
            "WHERE parent_partition_id = ? AND depth = 1", [parent]
        ).fetchone()[0]
        assert children == 2
        assert all(r[3] == "Sports" for r in rows)
    finally:
        db.close()


# --- buyer workspace route contract -----------------------------------------


def test_workspace_competitive_calendar_route(tmp_path):
    from festival_bloomberg.planning.repository import create_project
    from festival_bloomberg.terminal.server import TerminalApp
    from festival_bloomberg.terminal.storage import create_workspace_db

    serving = _db(tmp_path)
    workspace = create_workspace_db(str(tmp_path / "workspace.duckdb"))
    _snap(serving, "comp", "2026-09-10", segment="Sports", venue_name="Arena")
    project = create_project(
        workspace, name="Test Fest", city="Chicago", market="Chicago, IL",
        start_date="2026-09-10",
    )
    app = TerminalApp(serving, workspace)
    try:
        res = app.dispatch(
            "GET", f"/api/planning/projects/{project['project_key']}/competitive-calendar",
            query="date=2026-09-10",
        )
        assert res["status"] == 200
        cal = json.loads(res["body"])
        assert cal["status"] == "OBSERVED"
        assert set(cal.keys()) >= {"windows", "distance", "rows", "known_at_cutoff",
                                   "observed_after_cutoff", "unknown_knowledge_time", "pit_mode"}
        assert any(r["event_id"] == "comp" for r in cal["rows"])
        assert cal["pit_mode"] == "NON_PIT"
    finally:
        serving.close()
        workspace.close()
