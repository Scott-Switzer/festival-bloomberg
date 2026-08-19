"""Pre-acquisition hardening regressions for DENSE_PRE_EVENT_DATA_PANEL_V1.

Covers event-deduped competition, tri-state PIT knowability, research vs
commercial admission, ListenBrainz rights reconciliation, attention
missingness/completeness, and target-population coverage denominators.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from festival_bloomberg.attention.historical_pit import calendar_window
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.intelligence.coverage_targets import (
    artist_coverage_by_target,
    event_coverage_by_target,
    load_baseline_targets,
    venue_coverage_by_target,
)
from festival_bloomberg.planning.competition import (
    _classify,
    competition_for_event,
    market_competition_profile,
)
from festival_bloomberg.research.feature_registry import (
    COMMERCIAL_AGREEMENT_REQUIRED,
    COMMERCIAL_OK,
    COMMERCIAL_OK_WITH_CONDITIONS,
    FeatureSpec,
    STATUS_ADMITTED,
    admit,
    commercially_usable,
    resolve_commercial_status,
)
from festival_bloomberg.warehouse.repository import FestivalRepository


def _fresh(tmp_path, name: str) -> FestivalRepository:
    repo = FestivalRepository(str(tmp_path / name))
    EventRepository(repo.conn)
    return repo


def _seed(conn, *, eid, city, local_date, knowledge_time, snap):
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, event_status,
             city, local_date, retrieved_at, knowledge_time, rights_status,
             commercial_use_status, ingested_at)
        VALUES (?, 'ticketmaster', ?, 'Event ' || ?, 'onsale', ?, ?, ?, ?,
                'research', 'research', ?)
        """,
        [snap, eid, eid, city, local_date, knowledge_time, knowledge_time, knowledge_time],
    )


def _feat(name: str, min_cov: float, *, sources: tuple[str, ...] = ("internal",)) -> FeatureSpec:
    return FeatureSpec(
        name=name, semantic_definition="x", entity_type="EVENT", value_type="numeric",
        event_time_meaning="x", knowledge_time_rule="x", source="x", sources=sources,
        derivation_version="v1", minimum_coverage=min_cov, pit_admissible=True,
    )


# ---------------------------------------------------------------------------
# PART 1 — competition event dedup (snapshot table)
# ---------------------------------------------------------------------------
def test_competition_event_dedup_multiple_snapshots(tmp_path):
    repo = _fresh(tmp_path, "dedup.duckdb")
    try:
        _seed(repo.conn, eid="e0", city="Chicago", local_date="2027-08-01",
              knowledge_time="2027-06-01 00:00:00", snap="s0")
        # one competitor with THREE snapshots -> exactly one event
        _seed(repo.conn, eid="e1", city="Chicago", local_date="2027-08-01",
              knowledge_time="2027-06-15 00:00:00", snap="s1")
        _seed(repo.conn, eid="e1", city="Chicago", local_date="2027-08-01",
              knowledge_time="2027-07-01 00:00:00", snap="s2")
        _seed(repo.conn, eid="e1", city="Chicago", local_date="2027-08-01",
              knowledge_time="2027-06-01 00:00:00", snap="s3")
        c = competition_for_event(
            repo.conn, target_event_id="e0", event_date="2027-08-01",
            market="Chicago", research_cutoff="2027-08-01")
        # earliest knowledge_time (June 1) < cutoff -> known_before_cutoff, counted ONCE
        assert c["windows"]["pm0"]["known_before_cutoff"] == 1
        assert c["windows"]["pm0"]["observed_post_cutoff"] == 0
        assert c["windows"]["pm0"]["unknown_knowledge_time"] == 0
    finally:
        repo.close()


def test_market_profile_counts_distinct_events(tmp_path):
    repo = _fresh(tmp_path, "mktdedup.duckdb")
    try:
        _seed(repo.conn, eid="e1", city="Chicago", local_date="2027-03-01",
              knowledge_time="2026-01-01 00:00:00", snap="s1")
        _seed(repo.conn, eid="e1", city="Chicago", local_date="2027-03-01",
              knowledge_time="2026-01-02 00:00:00", snap="s2")
        _seed(repo.conn, eid="e2", city="Chicago", local_date="2027-03-01",
              knowledge_time="2026-01-01 00:00:00", snap="s3")
        p = market_competition_profile(repo.conn, market="Chicago")
        assert p["event_count"] == 2          # distinct events, not 3 snapshots
        assert p["max_events_same_day"] == 2
        assert p["busiest_date"] == "2027-03-01"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# PART 2 — tri-state PIT vs missingness
# ---------------------------------------------------------------------------
def test_classify_tri_state():
    d = date(2024, 1, 1)
    assert _classify("2023-12-31", d) == "known_before_cutoff"
    assert _classify("2024-01-01", d) == "observed_post_cutoff"
    assert _classify("2024-06-01", d) == "observed_post_cutoff"
    assert _classify(None, d) == "unknown_knowledge_time"
    assert _classify("garbage", d) == "unknown_knowledge_time"


def test_competition_tri_state_counts(tmp_path):
    repo = _fresh(tmp_path, "tristate.duckdb")
    try:
        _seed(repo.conn, eid="e0", city="Austin", local_date="2027-08-01",
              knowledge_time="2027-06-01 00:00:00", snap="s0")
        # known before cutoff
        _seed(repo.conn, eid="e1", city="Austin", local_date="2027-08-01",
              knowledge_time="2027-06-01 00:00:00", snap="s1")
        # observed only after cutoff
        _seed(repo.conn, eid="e2", city="Austin", local_date="2027-08-01",
              knowledge_time="2027-09-01 00:00:00", snap="s2")
        c = competition_for_event(
            repo.conn, target_event_id="e0", event_date="2027-08-01",
            market="Austin", research_cutoff="2027-08-01")
        assert c["windows"]["pm0"]["known_before_cutoff"] == 1
        assert c["windows"]["pm0"]["observed_post_cutoff"] == 1
        assert c["windows"]["pm0"]["unknown_knowledge_time"] == 0
        # knowledge_time_coverage = (known + post) / total = 2/2
        assert c["windows"]["pm0"]["knowledge_time_coverage"] == 1.0
        assert c["windows"]["pm0"]["unknown_rate"] == 0.0
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# PART 3 — research vs commercial admission
# ---------------------------------------------------------------------------
def test_research_commercial_admission_split():
    # Ticketmaster-derived: research-ADMITTED but commercial agreement required
    f = _feat("tm_comp", 0.4, sources=("ticketmaster_api",))
    r = admit(f, measured_coverage=0.5)
    assert r.research_status == STATUS_ADMITTED
    assert r.commercial_status == COMMERCIAL_AGREEMENT_REQUIRED
    assert commercially_usable(r.commercial_status) is False

    # Wikidata-only: commercially usable as-is
    g = _feat("wd", 0.4, sources=("wikidata",))
    r2 = admit(g, measured_coverage=0.5)
    assert r2.research_status == STATUS_ADMITTED
    assert r2.commercial_status == COMMERCIAL_OK
    assert commercially_usable(r2.commercial_status) is True


def test_commercial_status_snapshot_fields():
    from festival_bloomberg.research.feature_registry import registry_snapshot
    snap = registry_snapshot(measured={"event_competition_same_day_market": 0.5})
    by = {s["name"]: s for s in snap}
    s = by["event_competition_same_day_market"]
    assert s["research_status"] == STATUS_ADMITTED
    assert s["commercial_status"] == COMMERCIAL_AGREEMENT_REQUIRED
    assert s["commercial_product_allowed"] is False


# ---------------------------------------------------------------------------
# PART 4 — ListenBrainz rights reconciliation
# ---------------------------------------------------------------------------
def test_listenbrainz_registered_canonically():
    assert resolve_commercial_status(("listenbrainz",)) == "APPROVED_WITH_CONDITIONS"
    assert resolve_commercial_status(("listenbrainz",)) != "UNKNOWN"


def test_listenbrainz_feature_commercially_conditional():
    from festival_bloomberg.research.feature_registry import registry_snapshot
    snap = registry_snapshot(measured={"artist_attention_listenbrainz_30d_at_cutoff": 0.5})
    by = {s["name"]: s for s in snap}
    s = by["artist_attention_listenbrainz_30d_at_cutoff"]
    assert s["status"] == STATUS_ADMITTED
    assert s["commercial_status"] == COMMERCIAL_OK_WITH_CONDITIONS


# ---------------------------------------------------------------------------
# PART 5 — attention missingness / completeness
# ---------------------------------------------------------------------------
def test_calendar_window_incomplete_does_not_fabricate_zero():
    daily = {date(2027, 1, 1): 10.0, date(2027, 1, 3): 20.0}
    w = calendar_window(daily, start="2027-01-01", end="2027-01-05", complete=False)
    assert w["expected_days"] == 5
    assert w["observed_days"] == 2
    assert w["missing_days"] == 3
    assert w["completeness_pct"] == 0.4
    # incomplete: only observed days, no fabricated zeros
    assert set(w["series"].keys()) == {"2027-01-01", "2027-01-03"}


def test_calendar_window_complete_fills_true_zero():
    daily = {date(2027, 1, 1): 10.0, date(2027, 1, 3): 20.0}
    w = calendar_window(daily, start="2027-01-01", end="2027-01-05", complete=True)
    assert w["expected_days"] == 5
    assert w["observed_days"] == 2
    assert w["missing_days"] == 3
    assert len(w["series"]) == 5
    assert w["series"]["2027-01-02"] == 0.0   # true zero, not missing
    assert w["true_zero_days"] == 3


def test_calendar_window_unavailable_days():
    daily = {date(2027, 1, 3): 20.0}
    w = calendar_window(
        daily, start="2027-01-01", end="2027-01-05", complete=False,
        unavailable={"2027-01-01", "2027-01-02"},
    )
    assert w["expected_days"] == 3      # 5 minus 2 unavailable
    assert w["unavailable_days"] == 2
    assert w["observed_days"] == 1
    assert w["missing_days"] == 2


# ---------------------------------------------------------------------------
# PART 6 — target-population coverage
# ---------------------------------------------------------------------------
def _write_corpus(path: Path, rows: list[dict]) -> str:
    path.write_text(json.dumps({"corpus_version": "test", "rows": rows}))
    return str(path)


def _synthetic_corpus(tmp_path) -> str:
    rows = [
        {"engagement_id": "e1", "artist": "Deep Sea Arcade", "venue": "The Corner Hotel",
         "folds": {"TIME": "TRAIN"}},
        {"engagement_id": "e2", "artist": "Someone Else", "venue": "Other Venue",
         "folds": {"TIME": "TEST"}},
        {"engagement_id": "e3", "artist": "Deep Sea Arcade", "venue": "Other Venue",
         "folds": {"TIME": "TEST"}},
    ]
    return _write_corpus(tmp_path / "corpus.json", rows)


def test_load_baseline_targets_synthetic(tmp_path):
    t = load_baseline_targets(_synthetic_corpus(tmp_path))
    assert t["events_all"] == 3
    assert t["events_time"] == 2
    assert "deep sea arcade" in t["artists_all"]
    assert "the corner hotel" in t["venues_all"]
    assert "someone else" in t["artists_time"]
    assert "other venue" in t["venues_time"]


@pytest.mark.skipif(
    not Path("reports/baseline_research_v1/corpus_v1_manifest.json").exists(),
    reason="frozen corpus artifact is local-only (reports/ is gitignored)",
)
def test_load_baseline_targets_real_corpus():
    t = load_baseline_targets()
    assert t["events_all"] == 657
    assert 0 < t["events_time"] < t["events_all"]
    assert t["venues_all"] and t["artists_all"]
    assert t["venues_time"] <= t["venues_all"]
    assert t["artists_time"] <= t["artists_all"]


def test_venue_and_artist_coverage_by_target(tmp_path):
    repo = _fresh(tmp_path, "cov.duckdb")
    try:
        repo.conn.execute(
            "INSERT INTO core.venues (venue_key, name, normalized_name, capacity, latitude) "
            "VALUES ('v1', 'The Corner Hotel', 'the corner hotel', 800, -37.8)"
        )
        repo.conn.execute(
            "INSERT INTO core.artists (artist_key, name, normalized_name) "
            "VALUES ('a1', 'Deep Sea Arcade', 'deep sea arcade')"
        )
        cp = _synthetic_corpus(tmp_path)
        v = venue_coverage_by_target(repo.conn, corpus_path=cp)
        assert v["GLOBAL_CANONICAL_VENUES"]["total"] == 1
        assert v["GLOBAL_CANONICAL_VENUES"]["capacity_pct"] == 1.0
        assert v["BASELINE_ALL_TARGETS"]["total"] == 2     # corner hotel + other venue
        assert v["BASELINE_ALL_TARGETS"]["matched"] == 1   # only corner hotel seeded

        a = artist_coverage_by_target(repo.conn, corpus_path=cp)
        assert a["GLOBAL_CANONICAL_ARTISTS"]["total"] == 1
        assert a["BASELINE_ARTISTS"]["total"] == 2
        assert a["BASELINE_ARTISTS"]["matched"] == 1       # only deep sea arcade seeded

        e = event_coverage_by_target(repo.conn, corpus_path=cp)
        assert e["BASELINE_EVENTS"] == 3
        assert e["TIME_HOLD_EVENTS"] == 2
    finally:
        repo.close()


def test_venue_capacity_coverage_is_intersection_not_global(tmp_path):
    repo = _fresh(tmp_path, "intersect.duckdb")
    try:
        # 5 global venues, all with capacity (but NOT in the TIME target set)
        for i in range(5):
            repo.conn.execute(
                "INSERT INTO core.venues (venue_key, name, normalized_name, capacity) "
                "VALUES (?, ?, ?, 1000)",
                [f"g{i}", f"Global Venue {i}", f"global venue {i}"],
            )
        # 2 TIME venues; only 1 matches a seeded capacity venue
        rows = [
            {"engagement_id": "e1", "artist": "A", "venue": "Global Venue 0",
             "folds": {"TIME": "TEST"}},
            {"engagement_id": "e2", "artist": "B", "venue": "Unseeded Time Venue",
             "folds": {"TIME": "TEST"}},
        ]
        cp = _write_corpus(tmp_path / "c.json", rows)
        v = venue_coverage_by_target(repo.conn, corpus_path=cp)
        time = v["BASELINE_TIME_TARGETS"]
        assert time["total"] == 2
        # only "Global Venue 0" is capacity-covered among TIME targets
        assert time["capacity_count"] == 1
        assert time["capacity_pct"] == 0.5      # 1/2, NOT 5/2 (old bug)
        # a global covered venue outside the target set must NOT affect TIME coverage
        assert time["coords_count"] == 0
        assert time["coords_pct"] == 0.0
        # every coverage fraction and numerator is within bounds
        for key, row in v.items():
            assert 0.0 <= row["capacity_pct"] <= 1.0, (key, row["capacity_pct"])
            assert 0.0 <= row["coords_pct"] <= 1.0, (key, row["coords_pct"])
            assert 0.0 <= row["canonical_match_pct"] <= 1.0, (key, row["canonical_match_pct"])
            assert row["capacity_count"] <= row["total"]
            assert row["coords_count"] <= row["total"]
            assert row["canonical_match_count"] <= row["total"]
    finally:
        repo.close()
