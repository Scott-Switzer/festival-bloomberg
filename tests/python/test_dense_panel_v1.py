"""Tests for DENSE_PRE_EVENT_DATA_PANEL_V1 foundations.

Feature registry admission, PIT historical attention windows (including the
listened_at/inserted_at leakage rule), and competition raw counts.
"""

from __future__ import annotations

from datetime import date, timedelta

from festival_bloomberg.attention.historical_pit import daily_series, pit_features
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.intelligence.coverage_voi import dense_panel_coverage
from festival_bloomberg.planning.competition import (
    competition_for_event,
    market_competition_profile,
)
from festival_bloomberg.research.feature_registry import (
    REGISTRY,
    STATUS_ADMITTED,
    STATUS_REJECTED_COVERAGE,
    STATUS_REJECTED_LEAKAGE,
    registry_snapshot,
)
from festival_bloomberg.warehouse.repository import FestivalRepository


def _fresh(tmp_path, name: str) -> FestivalRepository:
    repo = FestivalRepository(str(tmp_path / name))
    EventRepository(repo.conn)
    return repo


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


def test_registry_snapshot_admits_with_coverage():
    snap = registry_snapshot()
    by_name = {s["name"]: s for s in snap}
    # without measured coverage, entries pass admission (coverage plan only)
    assert by_name["venue_capacity_band"]["status"] == STATUS_ADMITTED
    # with measured coverage below the minimum -> REJECTED_COVERAGE
    snap2 = registry_snapshot(measured={"venue_capacity_band": 0.1})
    assert snap2[[s["name"] for s in snap2].index("venue_capacity_band")]["status"] == STATUS_REJECTED_COVERAGE


def test_leakage_field_rejected():
    from festival_bloomberg.research.feature_registry import FeatureSpec, admit
    bad = FeatureSpec(
        name="price_proxy", semantic_definition="x", entity_type="EVENT",
        value_type="numeric", event_time_meaning="x", knowledge_time_rule="x",
        source="x", rights_status="OPEN_COMMERCIAL_OK", commercial_use_status="OK",
        derivation_version="x", minimum_coverage=0.5, pit_admissible=True,
        leakage_fields=("price_min",),
    )
    admit(bad)
    assert bad.status == STATUS_REJECTED_LEAKAGE


# ---------------------------------------------------------------------------
# PIT historical attention
# ---------------------------------------------------------------------------
def _obs(days_ago: int, value: float, *, inserted_days_ago: int | None = None):
    today = date.today()
    return {
        "day": (today - timedelta(days=days_ago)).isoformat(),
        "value": value,
        "inserted": (today - timedelta(days=inserted_days_ago)).isoformat() if inserted_days_ago is not None else None,
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


def test_listenbrainz_inserted_at_leakage_rule():
    """listened_at < cutoff AND inserted_at < cutoff — late imports never leak."""
    today = date.today()
    cutoff = today.isoformat()
    rows = [
        _obs(1, 10, inserted_days_ago=1),     # listened + inserted before cutoff
        _obs(2, 20, inserted_days_ago=0),     # listened before cutoff but inserted today -> EXCLUDED
        _obs(3, 30, inserted_days_ago=5),
    ]
    with_insert = daily_series(
        rows, value_fn=lambda r: r["value"], day_fn=lambda r: r["day"],
        inserted_fn=lambda r: r["inserted"], cutoff=cutoff)
    without_insert = daily_series(
        rows, value_fn=lambda r: r["value"], day_fn=lambda r: r["day"], cutoff=cutoff)
    assert sum(with_insert.values()) == 40.0   # rows 1,3 only
    assert sum(without_insert.values()) == 60.0  # all three when not enforced


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
# Competition (raw counts)
# ---------------------------------------------------------------------------
def _seed_tm_event(conn, *, eid, city, local_date):
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, event_status,
             city, local_date, retrieved_at, knowledge_time, rights_status,
             commercial_use_status, ingested_at)
        VALUES (?, 'ticketmaster', ?, 'Event ' || ?, 'onsale', ?, ?, now(), now(),
                'research', 'research', now())
        """,
        [f"snap::{eid}", eid, eid, city, local_date],
    )


def test_competition_window_counts(tmp_path):
    repo = _fresh(tmp_path, "comp.duckdb")
    try:
        _seed_tm_event(repo.conn, eid="e1", city="Chicago", local_date="2027-08-01")
        _seed_tm_event(repo.conn, eid="e2", city="Chicago", local_date="2027-08-01")
        _seed_tm_event(repo.conn, eid="e3", city="Chicago", local_date="2027-08-04")
        _seed_tm_event(repo.conn, eid="e4", city="Chicago", local_date="2027-07-30")
        _seed_tm_event(repo.conn, eid="e5", city="New York", local_date="2027-08-01")
        c = competition_for_event(repo.conn, event_date="2027-08-01", market="Chicago")
        assert c["status"] == "OBSERVED"
        assert c["windows"]["pm0"] == 2      # same-day Chicago: e1,e2
        assert c["windows"]["pm7"] == 4      # +-7: e1,e2,e3,e4 (e5 different market)
        # unknown inputs -> UNKNOWN, never zero
        u = competition_for_event(repo.conn, event_date=None, market=None)
        assert u["status"] == "UNKNOWN"
    finally:
        repo.close()


def test_market_competition_profile(tmp_path):
    repo = _fresh(tmp_path, "mcp.duckdb")
    try:
        _seed_tm_event(repo.conn, eid="a", city="Austin", local_date="2027-03-01")
        _seed_tm_event(repo.conn, eid="b", city="Austin", local_date="2027-03-01")
        _seed_tm_event(repo.conn, eid="c", city="Austin", local_date="2027-03-02")
        p = market_competition_profile(repo.conn, market="Austin")
        assert p["event_count"] == 3
        assert p["max_events_same_day"] == 2
        p2 = market_competition_profile(repo.conn, market="Nowhere")
        assert p2["status"] == "UNKNOWN"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Dense panel coverage probe
# ---------------------------------------------------------------------------
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
        # not-yet-built families report 0 (honest, never fabricated)
        assert cov["artist_attention_wikimedia_30d_at_cutoff"] == 0.0
        assert cov["market_population_vintage"] == 0.0
    finally:
        repo.close()
