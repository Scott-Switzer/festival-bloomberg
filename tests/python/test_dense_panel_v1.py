"""Tests for DENSE_PRE_EVENT_DATA_PANEL_V1 foundations.

Feature registry admission (coverage semantics, purity, rights derivation),
PIT historical attention windows (including the listened_at/available_at
leakage rule), competition raw counts (PIT-safe), and dense-panel coverage.
"""

from __future__ import annotations

from datetime import date, timedelta

from festival_bloomberg.attention.historical_pit import daily_series, pit_features
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.intelligence.coverage_voi import dense_panel_coverage
from festival_bloomberg.planning.competition import (
    _knowable_before,
    competition_for_event,
    market_competition_profile,
)
from festival_bloomberg.research.feature_registry import (
    REGISTRY,
    FeatureSpec,
    STATUS_ADMITTED,
    STATUS_CANDIDATE,
    STATUS_NOT_MEASURED,
    STATUS_REJECTED_COVERAGE,
    STATUS_REJECTED_LEAKAGE,
    STATUS_REJECTED_RIGHTS,
    admit,
    most_restrictive,
    registry_snapshot,
    resolve_commercial_status,
)
from festival_bloomberg.warehouse.repository import FestivalRepository


def _fresh(tmp_path, name: str) -> FestivalRepository:
    repo = FestivalRepository(str(tmp_path / name))
    EventRepository(repo.conn)
    return repo


def _feat(name: str, min_cov: float, *, sources: tuple[str, ...] = ("internal",)) -> FeatureSpec:
    return FeatureSpec(
        name=name, semantic_definition="x", entity_type="EVENT", value_type="numeric",
        event_time_meaning="x", knowledge_time_rule="x", source="x", sources=sources,
        derivation_version="v1", minimum_coverage=min_cov, pit_admissible=True,
    )


# ---------------------------------------------------------------------------
# Feature registry admission
# ---------------------------------------------------------------------------
def test_registry_all_entries_declared_with_pit_rules():
    assert len(REGISTRY) >= 8
    for spec in REGISTRY:
        assert spec.semantic_definition
        assert spec.knowledge_time_rule
        assert spec.event_time_meaning
        assert spec.pit_admissible  # no entry may be admitted without PIT
        # every feature must declare canonical source IDs for rights derivation
        assert spec.sources, f"{spec.name} must declare canonical sources"


def test_registry_not_measured_without_coverage():
    snap = registry_snapshot()
    by_name = {s["name"]: s for s in snap}
    # without measured coverage, entries are NOT_MEASURED (never ADMITTED)
    assert by_name["venue_capacity_band"]["status"] == STATUS_NOT_MEASURED
    assert by_name["venue_capacity_band"]["coverage_state"] == "NOT_MEASURED"


def test_registry_admission_coverage_gates():
    # measured zero is a real failure, not "unknown"
    snap = registry_snapshot(measured={"venue_capacity_band": 0.0})
    by_name = {s["name"]: s for s in snap}
    assert by_name["venue_capacity_band"]["status"] == STATUS_REJECTED_COVERAGE
    assert by_name["venue_capacity_band"]["coverage_state"] == "MEASURED_ZERO"

    # below the minimum (0.6) rejects
    snap2 = registry_snapshot(measured={"venue_capacity_band": 0.5999})
    idx = [s["name"] for s in snap2].index("venue_capacity_band")
    assert snap2[idx]["status"] == STATUS_REJECTED_COVERAGE

    # at/above the minimum admits
    snap3 = registry_snapshot(measured={"venue_capacity_band": 0.6})
    idx = [s["name"] for s in snap3].index("venue_capacity_band")
    assert snap3[idx]["status"] == STATUS_ADMITTED


def test_none_is_not_zero_and_thresholds():
    # None != 0
    assert admit(_feat("f", 0.4), measured_coverage=None).status == STATUS_NOT_MEASURED
    assert admit(_feat("f", 0.4), measured_coverage=0.0).status == STATUS_REJECTED_COVERAGE
    assert admit(_feat("f", 0.4), measured_coverage=0.399).status == STATUS_REJECTED_COVERAGE
    assert admit(_feat("f", 0.4), measured_coverage=0.4).status == STATUS_ADMITTED


def test_admit_is_pure():
    f = _feat("f", 0.4)
    result = admit(f, measured_coverage=0.5)
    assert result is not f
    assert result.status == STATUS_ADMITTED
    assert result.current_coverage == 0.5
    # the original is untouched
    assert f.status == STATUS_CANDIDATE
    assert f.current_coverage is None


def test_registry_snapshot_does_not_mutate():
    before = [(s.name, s.status, s.current_coverage) for s in REGISTRY]
    registry_snapshot(measured={"venue_capacity_band": 0.9})
    after = [(s.name, s.status, s.current_coverage) for s in REGISTRY]
    assert before == after


def test_leakage_field_rejected():
    bad = FeatureSpec(
        name="price_proxy", semantic_definition="x", entity_type="EVENT",
        value_type="numeric", event_time_meaning="x", knowledge_time_rule="x",
        source="x", derivation_version="x", minimum_coverage=0.5, pit_admissible=True,
        leakage_fields=("price_min",),
    )
    result = admit(bad)
    assert result.status == STATUS_REJECTED_LEAKAGE
    assert bad.status == STATUS_CANDIDATE  # original untouched


# ---------------------------------------------------------------------------
# Rights derivation from canonical source policy
# ---------------------------------------------------------------------------
def test_rights_derived_from_canonical_policy():
    # wikidata (APPROVED) + osm (APPROVED_WITH_CONDITIONS) -> conditional
    assert resolve_commercial_status(("wikidata", "openstreetmap")) == "APPROVED_WITH_CONDITIONS"
    # ticketmaster (agreement) + musicbrainz (legal review) -> agreement required,
    # NOT the old hand-written "OPEN_ATTRIBUTION_REQUIRED"
    assert resolve_commercial_status(("ticketmaster_api", "musicbrainz")) == "COMMERCIAL_AGREEMENT_REQUIRED"
    # listenbrainz is absent from every canonical registry -> fail closed
    assert resolve_commercial_status(("listenbrainz",)) == "UNKNOWN"


def test_composite_rights_never_more_permissive():
    assert most_restrictive("APPROVED", "RESEARCH_ONLY") == "RESEARCH_ONLY"
    assert most_restrictive("APPROVED", "PROHIBITED") == "PROHIBITED"
    # a composite equals the most restrictive of its components
    combined = resolve_commercial_status(("wikidata", "musicbrainz"))
    components = most_restrictive(
        resolve_commercial_status(("wikidata",)),
        resolve_commercial_status(("musicbrainz",)),
    )
    assert combined == components


def test_listenbrainz_feature_rejected_rights():
    snap = registry_snapshot(measured={"artist_attention_listenbrainz_30d_at_cutoff": 0.5})
    by_name = {s["name"]: s for s in snap}
    assert by_name["artist_attention_listenbrainz_30d_at_cutoff"]["status"] == STATUS_REJECTED_RIGHTS


# ---------------------------------------------------------------------------
# PIT historical attention
# ---------------------------------------------------------------------------
def _obs(days_ago: int, value: float, *, available_days_ago: int | None = None):
    today = date.today()
    return {
        "day": (today - timedelta(days=days_ago)).isoformat(),
        "value": value,
        "available": (today - timedelta(days=available_days_ago)).isoformat() if available_days_ago is not None else None,
    }


def test_pit_windows_respect_cutoff():
    today = date.today()
    cutoff = today.isoformat()
    rows = [_obs(1, 10), _obs(5, 20), _obs(20, 30), _obs(40, 40), _obs(100, 50)]
    daily = daily_series(rows, value_fn=lambda r: r["value"], day_fn=lambda r: r["day"], cutoff=cutoff)
    f = pit_features(daily, cutoff=cutoff)
    assert f["status"] == "OK"
    assert f["7d"] == 30.0      # days 1,5
    assert f["30d"] == 60.0     # days 1,5,20
    assert f["90d"] == 100.0    # days 1,5,20,40
    assert f["365d"] == 150.0   # all
    assert f["days_observed"] == 5


def test_pit_excludes_cutoff_day_and_future():
    today = date.today()
    cutoff = today.isoformat()
    rows = [
        _obs(0, 999),            # same day as cutoff -> excluded
        _obs(-1, 999),           # future -> excluded
        _obs(1, 5),              # eligible
    ]
    daily = daily_series(rows, value_fn=lambda r: r["value"], day_fn=lambda r: r["day"], cutoff=cutoff)
    assert sum(daily.values()) == 5.0


def test_listenbrainz_available_at_leakage_rule():
    """listened_at < cutoff AND available_at (inserted_at) < cutoff — late imports never leak."""
    today = date.today()
    cutoff = today.isoformat()
    rows = [
        _obs(1, 10, available_days_ago=1),     # listened + available before cutoff
        _obs(2, 20, available_days_ago=0),     # listened before cutoff but available today -> EXCLUDED
        _obs(3, 30, available_days_ago=5),
    ]
    with_avail = daily_series(
        rows, value_fn=lambda r: r["value"], day_fn=lambda r: r["day"],
        available_fn=lambda r: r["available"], cutoff=cutoff)
    without_avail = daily_series(
        rows, value_fn=lambda r: r["value"], day_fn=lambda r: r["day"], cutoff=cutoff)
    assert sum(with_avail.values()) == 40.0     # rows 1,3 only
    assert sum(without_avail.values()) == 60.0  # all three when not enforced


def test_pit_growth_and_trend():
    today = date.today()
    cutoff = today.isoformat()
    # rising series: recent 30d (days_ago 1..30) high, prior 30d low
    rows = []
    for i in range(1, 61):
        v = 100 if i <= 30 else 10
        rows.append(_obs(i, v))
    daily = daily_series(rows, value_fn=lambda r: r["value"], day_fn=lambda r: r["day"], cutoff=cutoff)
    f = pit_features(daily, cutoff=cutoff)
    assert f["growth_30d"] > 0
    assert f["trend_90d"] is None or f["trend_90d"] >= 0


# ---------------------------------------------------------------------------
# Competition (raw counts, PIT-safe)
# ---------------------------------------------------------------------------
def _seed_tm_event(conn, *, eid, city, local_date, knowledge_time):
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, event_status,
             city, local_date, retrieved_at, knowledge_time, rights_status,
             commercial_use_status, ingested_at)
        VALUES (?, 'ticketmaster', ?, 'Event ' || ?, 'onsale', ?, ?, ?, ?,
                'research', 'research', ?)
        """,
        [f"snap::{eid}", eid, eid, city, local_date, knowledge_time, knowledge_time, knowledge_time],
    )


def test_competition_pit_safety(tmp_path):
    repo = _fresh(tmp_path, "pitcomp.duckdb")
    try:
        # target event (must be self-excluded even though it matches market+date)
        _seed_tm_event(repo.conn, eid="e0", city="Chicago", local_date="2027-08-01",
                       knowledge_time="2027-06-01 00:00:00")
        # pre-cutoff known competitor, same day
        _seed_tm_event(repo.conn, eid="e1", city="Chicago", local_date="2027-08-01",
                       knowledge_time="2027-06-01 00:00:00")
        # known only AFTER the cutoff -> not PIT-known
        _seed_tm_event(repo.conn, eid="e2", city="Chicago", local_date="2027-08-01",
                       knowledge_time="2027-08-15 00:00:00")
        # pre-cutoff known, +4 days
        _seed_tm_event(repo.conn, eid="e3", city="Chicago", local_date="2027-08-05",
                       knowledge_time="2027-07-01 00:00:00")
        # different market -> excluded
        _seed_tm_event(repo.conn, eid="e4", city="New York", local_date="2027-08-01",
                       knowledge_time="2027-06-01 00:00:00")

        c = competition_for_event(
            repo.conn, target_event_id="e0", event_date="2027-08-01",
            market="Chicago", research_cutoff="2027-08-01")
        assert c["status"] == "OBSERVED"
        # same day: e1 known, e2 not-known-at-cutoff (self e0 excluded)
        assert c["windows"]["pm0"]["known"] == 1
        assert c["windows"]["pm0"]["unknown_knowability"] == 1
        assert c["windows"]["pm0"]["coverage"] == 0.5
        # +-7 / +-14: e1 + e3 known, e2 unknown
        assert c["windows"]["pm7"]["known"] == 2
        assert c["windows"]["pm7"]["unknown_knowability"] == 1
        assert c["windows"]["pm14"]["known"] == 2
        assert c["windows"]["pm14"]["unknown_knowability"] == 1
    finally:
        repo.close()


def test_competition_non_pit_without_cutoff(tmp_path):
    repo = _fresh(tmp_path, "nonpit.duckdb")
    try:
        _seed_tm_event(repo.conn, eid="e0", city="Chicago", local_date="2027-08-01",
                       knowledge_time="2027-06-01 00:00:00")
        _seed_tm_event(repo.conn, eid="e1", city="Chicago", local_date="2027-08-01",
                       knowledge_time="2027-08-15 00:00:00")
        c = competition_for_event(
            repo.conn, target_event_id="e0", event_date="2027-08-01", market="Chicago")
        assert c["status"] == "NON_PIT"
        # without a cutoff every observed event counts as "known" (current view)
        assert c["windows"]["pm0"]["known"] == 1
        assert c["windows"]["pm0"]["unknown_knowability"] == 0
    finally:
        repo.close()


def test_knowable_before_semantics():
    d = date(2024, 1, 1)
    assert _knowable_before(None, d) is None          # unknown knowability
    assert _knowable_before("2023-12-31", d) is True  # strictly before
    assert _knowable_before("2024-01-01", d) is False  # at cutoff -> not before
    assert _knowable_before("2024-01-02", d) is False  # after


def test_market_competition_profile_busiest_date(tmp_path):
    repo = _fresh(tmp_path, "mcp.duckdb")
    try:
        _seed_tm_event(repo.conn, eid="a", city="Austin", local_date="2027-03-02",
                       knowledge_time="2026-01-01 00:00:00")
        _seed_tm_event(repo.conn, eid="b", city="Austin", local_date="2027-03-01",
                       knowledge_time="2026-01-01 00:00:00")
        _seed_tm_event(repo.conn, eid="c", city="Austin", local_date="2027-03-01",
                       knowledge_time="2026-01-01 00:00:00")
        p = market_competition_profile(repo.conn, market="Austin")
        assert p["event_count"] == 3
        assert p["max_events_same_day"] == 2
        # busiest_date is the argmax count (2027-03-01), not the latest date (2027-03-02)
        assert p["busiest_date"] == "2027-03-01"
        p2 = market_competition_profile(repo.conn, market="Nowhere")
        assert p2["status"] == "UNKNOWN"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Dense panel coverage probe (distinct-venue, never >100%)
# ---------------------------------------------------------------------------
def _seed_claim(conn, *, cid, venue, value):
    conn.execute(
        """
        INSERT INTO economics.venue_capacity_claims
            (claim_id, canonical_venue_id, capacity_value, capacity_kind,
             provider, source, retrieved_at, knowledge_time, claim_status)
        VALUES (?, ?, ?, 'MAXIMUM_VENUE_CAPACITY', 'wikidata', 'wikidata',
                now(), now(), 'ACTIVE')
        """,
        [cid, venue, value],
    )


def test_dense_panel_coverage_probe(tmp_path):
    repo = _fresh(tmp_path, "dpc.duckdb")
    try:
        repo.conn.execute(
            "INSERT INTO core.venues (venue_key, name, normalized_name, capacity, latitude) "
            "VALUES ('v1', 'V1', 'v1', 5000, 41.0), ('v2', 'V2', 'v2', NULL, NULL)"
        )
        cov = dense_panel_coverage(repo.conn)
        assert cov["venue_capacity_band"] == 0.5
        assert cov["venue_coordinates"] == 0.5
        # not-yet-built families report None (NOT measured), never fabricated 0
        assert cov["artist_attention_wikimedia_30d_at_cutoff"] is None
        assert cov["market_population_vintage"] is None
    finally:
        repo.close()


def test_venue_capacity_coverage_one_venue_many_claims(tmp_path):
    repo = _fresh(tmp_path, "vcap.duckdb")
    try:
        repo.conn.execute(
            "INSERT INTO core.venues (venue_key, name, normalized_name, capacity) "
            "VALUES ('v1', 'V1', 'v1', 20000), ('v2', 'V2', 'v2', NULL)"
        )
        # one venue with core capacity AND five (conflicting) claims -> ONE covered venue
        for i in range(5):
            _seed_claim(repo.conn, cid=f"c{i}", venue="v1", value=15000 + i)
        cov = dense_panel_coverage(repo.conn)
        assert cov["venue_capacity_band"] == 0.5  # 1 of 2, never 6/2
    finally:
        repo.close()


def test_venue_capacity_core_plus_claim_still_one_venue(tmp_path):
    repo = _fresh(tmp_path, "vcap2.duckdb")
    try:
        repo.conn.execute(
            "INSERT INTO core.venues (venue_key, name, normalized_name, capacity) "
            "VALUES ('v1', 'V1', 'v1', 1000)"
        )
        _seed_claim(repo.conn, cid="c0", venue="v1", value=900)
        cov = dense_panel_coverage(repo.conn)
        assert cov["venue_capacity_band"] == 1.0  # core capacity + claim = one venue
    finally:
        repo.close()


def test_venue_capacity_conflicts_preserved(tmp_path):
    repo = _fresh(tmp_path, "vconf.duckdb")
    try:
        repo.conn.execute(
            "INSERT INTO core.venues (venue_key, name, normalized_name) VALUES ('v1', 'V1', 'v1')"
        )
        _seed_claim(repo.conn, cid="c1", venue="v1", value=18000)
        _seed_claim(repo.conn, cid="c2", venue="v1", value=22000)
        n = repo.conn.execute(
            "SELECT COUNT(DISTINCT capacity_value) FROM economics.venue_capacity_claims "
            "WHERE canonical_venue_id = 'v1'").fetchone()[0]
        # conflicting claims are preserved, not collapsed
        assert n == 2
        # and coverage counts the venue once
        assert dense_panel_coverage(repo.conn)["venue_capacity_band"] == 1.0
    finally:
        repo.close()
