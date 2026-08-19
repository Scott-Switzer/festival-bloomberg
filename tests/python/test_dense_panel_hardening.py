"""Pre-acquisition hardening regressions for DENSE_PRE_EVENT_DATA_PANEL_V1.

Covers event-deduped competition, tri-state PIT knowability, research vs
commercial admission, ListenBrainz rights reconciliation, attention
missingness/completeness, and target-population coverage denominators.
"""

from __future__ import annotations

from datetime import date

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
        v = venue_coverage_by_target(repo.conn)
        assert v["GLOBAL_CANONICAL_VENUES"]["total"] == 1
        assert v["GLOBAL_CANONICAL_VENUES"]["capacity_pct"] == 1.0
        assert v["BASELINE_ALL_TARGETS"]["matched"] >= 1   # "The Corner Hotel" is in the corpus
        assert v["BASELINE_ALL_TARGETS"]["match_pct"] > 0.0

        a = artist_coverage_by_target(repo.conn)
        assert a["GLOBAL_CANONICAL_ARTISTS"]["total"] == 1
        assert a["BASELINE_ARTISTS"]["matched"] >= 1       # "Deep Sea Arcade" is in the corpus

        e = event_coverage_by_target(repo.conn)
        assert e["BASELINE_EVENTS"] == 657
        assert e["TIME_HOLD_EVENTS"] > 0
    finally:
        repo.close()
