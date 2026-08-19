"""Behavioral regressions for MUSIC_TERMINAL_PRODUCTIZATION_V1.

Covers the P0 audit fixes: temporal ListenBrainz observation keys, watchlist
mutations, identity-conflict visibility, alias->real-MBID resolution, TM
attraction-ID dedupe, DJ non-rejection, run-aware NEW_EVENT, consecutive
snapshot change alerts, and presale detection. All offline (in-memory/tmp
DuckDB, no network).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from festival_bloomberg.attention.listenbrainz import observation_key
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.identity.ticketmaster_resolution import (
    _mb_alias_candidates,
    classify_special,
    fetch_attraction_universe,
)
from festival_bloomberg.product.workflow import (
    _identity_conflicts,
    _presale_signature,
    add_watchlist_item,
    create_watchlist,
    generate_event_alerts,
    generate_new_event_alerts,
    list_watchlist_items,
    list_watchlists,
    remove_watchlist_item,
)
from festival_bloomberg.warehouse.repository import FestivalRepository

T1 = "2026-08-14T15:38:55+00:00"
T2 = "2026-08-14T16:57:29+00:00"
T3 = "2026-08-14T17:20:00+00:00"


def _seed_snapshot(conn, *, eid, retrieved_at, price=None, status="onsale",
                   presales=None, onsale_start=None, promoter=None):
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, event_status,
             onsale_start, presales, price_min, price_max, promoter, retrieved_at,
             knowledge_time, rights_status, commercial_use_status, ingested_at)
        VALUES (?, 'ticketmaster', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'research', 'research', now())
        """,
        [
            f"snap::{eid}::{retrieved_at}",
            eid,
            f"Event {eid}",
            status,
            onsale_start,
            presales,
            price,
            price,
            promoter,
            retrieved_at,
            retrieved_at,
        ],
    )


def _seed_attraction(conn, *, eid, attraction_id, attraction_name, retrieved_at=T1):
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, attractions, retrieved_at,
             knowledge_time, rights_status, commercial_use_status, ingested_at)
        VALUES (?, 'ticketmaster', ?, ?, ?, ?, 'research', 'research', now())
        """,
        [
            f"snap::{eid}",
            eid,
            json.dumps([{"ticketmaster_attraction_id": attraction_id,
                         "attraction_name": attraction_name}]),
            retrieved_at,
            retrieved_at,
        ],
    )


# ---------------------------------------------------------------------------
# ListenBrainz temporal observation keys
# ---------------------------------------------------------------------------
def test_listenbrainz_same_day_idempotent():
    k1 = observation_key(artist_key="mbid::x", mbid="x",
                         metric_kind="LISTENBRAINZ_LISTEN_COUNT", stats_range="all_time",
                         retrieved_at="2026-08-16T10:00:00+00:00")
    k2 = observation_key(artist_key="mbid::x", mbid="x",
                         metric_kind="LISTENBRAINZ_LISTEN_COUNT", stats_range="all_time",
                         retrieved_at="2026-08-16T11:59:00+00:00")
    assert k1 == k2


def test_listenbrainz_later_day_new_observation():
    k1 = observation_key(artist_key="mbid::x", mbid="x",
                         metric_kind="LISTENBRAINZ_LISTEN_COUNT", stats_range="all_time",
                         retrieved_at="2026-08-16T10:00:00+00:00")
    k2 = observation_key(artist_key="mbid::x", mbid="x",
                         metric_kind="LISTENBRAINZ_LISTEN_COUNT", stats_range="all_time",
                         retrieved_at="2026-08-23T10:00:00+00:00")
    assert k1 != k2


def test_listenbrainz_provider_updated_creates_new_observation():
    k1 = observation_key(artist_key="mbid::x", mbid="x",
                         metric_kind="LISTENBRAINZ_LISTEN_COUNT", stats_range="all_time",
                         retrieved_at="2026-08-16T10:00:00+00:00",
                         provider_last_updated="1000000")
    k2 = observation_key(artist_key="mbid::x", mbid="x",
                         metric_kind="LISTENBRAINZ_LISTEN_COUNT", stats_range="all_time",
                         retrieved_at="2026-08-16T10:00:00+00:00",
                         provider_last_updated="2000000")
    assert k1 != k2


# ---------------------------------------------------------------------------
# Special-attraction classification
# ---------------------------------------------------------------------------
def test_dj_khaled_not_rejected():
    assert classify_special("DJ Khaled") is None


def test_tribute_act_classified():
    assert classify_special("Tribute to Queen") == "TRIBUTE_ACT"


def test_presale_signature_diff():
    empty = _presale_signature(None)
    with_presale = _presale_signature(
        json.dumps([{"name": "Fan", "start": "2026-08-20", "end": "2026-08-21"}])
    )
    assert empty == frozenset()
    assert with_presale - empty == frozenset({("Fan", "2026-08-20", "2026-08-21")})


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------
def test_watchlist_create_add_remove(tmp_path):
    repo = FestivalRepository(str(tmp_path / "wl.duckdb"))
    try:
        EventRepository(repo.conn)  # apply pending migrations
        wl = create_watchlist(repo.conn, name="2027 Talent Targets", entity_type="ARTIST")
        assert wl["watchlist_key"]
        assert add_watchlist_item(repo.conn, watchlist_key_value=wl["watchlist_key"],
                                  entity_type="ARTIST", entity_key_value="mbid::billie",
                                  entity_name="Billie Eilish") == 1
        # idempotent re-add
        assert add_watchlist_item(repo.conn, watchlist_key_value=wl["watchlist_key"],
                                  entity_type="ARTIST", entity_key_value="mbid::billie",
                                  entity_name="Billie Eilish") == 0
        items = list_watchlist_items(repo.conn, wl["watchlist_key"])
        assert len(items) == 1 and items[0]["entity_name"] == "Billie Eilish"
        assert remove_watchlist_item(repo.conn, watchlist_key_value=wl["watchlist_key"],
                                     entity_type="ARTIST", entity_key_value="mbid::billie") >= 1
        assert list_watchlist_items(repo.conn, wl["watchlist_key"]) == []
        assert list_watchlists(repo.conn)[0]["name"] == "2027 Talent Targets"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Identity conflicts are visible (no silent exception suppression)
# ---------------------------------------------------------------------------
def test_identity_conflicts_visible(tmp_path):
    repo = FestivalRepository(str(tmp_path / "ic.duckdb"))
    try:
        EventRepository(repo.conn)
        repo.conn.execute(
            """
            INSERT INTO core.identity_conflicts
                (conflict_key, entity_type, entity_key, provider_a, provider_b,
                 value_a, value_b, issue, resolution_status)
            VALUES ('c1', 'ARTIST', 'mbid::billie', 'musicbrainz', 'spotify',
                    'url_a', 'url_b', 'MB url disagrees with acquired spotify ID', 'UNRESOLVED')
            """
        )
        conflicts = _identity_conflicts(repo.conn, 10)
        assert len(conflicts) == 1
        assert conflicts[0]["entity_key"] == "mbid::billie"
        assert conflicts[0]["provider_a"] == "musicbrainz"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Alias resolution returns the REAL MBID (not the internal artist_key)
# ---------------------------------------------------------------------------
def test_alias_resolution_returns_actual_mbid(tmp_path):
    repo = FestivalRepository(str(tmp_path / "alias.duckdb"))
    try:
        EventRepository(repo.conn)
        repo.conn.execute(
            """
            INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name)
            VALUES ('mbid::real-mbid', 'real-mbid', 'The Real Band', 'the real band')
            """
        )
        repo.conn.execute(
            """
            INSERT INTO core.artist_aliases
                (alias_key, artist_key, alias, normalized_alias, alias_type, source_system, ingested_at)
            VALUES ('ak1', 'mbid::real-mbid', 'Real Band', 'real band', 'ALIAS', 'test', now())
            """
        )
        rows = _mb_alias_candidates(repo.conn, "real band")
        assert rows, "alias candidate not found"
        artist_key, mbid, alias = rows[0]
        assert mbid == "real-mbid"  # NOT the internal artist_key
        assert artist_key == "mbid::real-mbid"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Ticketmaster attraction dedupe by provider ID
# ---------------------------------------------------------------------------
def test_same_name_different_tm_ids_stay_distinct(tmp_path):
    repo = FestivalRepository(str(tmp_path / "dedupe.duckdb"))
    try:
        EventRepository(repo.conn)
        _seed_attraction(repo.conn, eid="e1", attraction_id="tm-1", attraction_name="Same Name")
        _seed_attraction(repo.conn, eid="e2", attraction_id="tm-2", attraction_name="Same Name")
        universe = fetch_attraction_universe(repo.conn)
        assert len(universe) == 2, "same-name attractions with distinct provider IDs must not collapse"
        ids = sorted(a["attraction_id"] for a in universe)
        assert ids == ["tm-1", "tm-2"]
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Run-aware NEW_EVENT detection
# ---------------------------------------------------------------------------
def _count_alerts(conn, alert_type):
    return conn.execute(
        "SELECT COUNT(*) FROM core.alerts WHERE alert_type = ?", [alert_type]
    ).fetchone()[0]


def test_initial_snapshot_has_no_new_event_alert(tmp_path):
    repo = FestivalRepository(str(tmp_path / "ne1.duckdb"))
    try:
        EventRepository(repo.conn)
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T1, price=50.0)
        _seed_snapshot(repo.conn, eid="B", retrieved_at=T1, price=60.0)
        out = generate_new_event_alerts(repo.conn)
        assert out["status"] == "COMPLETE"
        assert "single snapshot" in out.get("note", "")
        assert _count_alerts(repo.conn, "NEW_EVENT") == 0
    finally:
        repo.close()


def test_later_run_new_event_creates_alert(tmp_path):
    repo = FestivalRepository(str(tmp_path / "ne2.duckdb"))
    try:
        EventRepository(repo.conn)
        # run 1: only A
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T1, price=50.0)
        # run 2: A again + new B
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T2, price=50.0)
        _seed_snapshot(repo.conn, eid="B", retrieved_at=T2, price=60.0)
        generate_new_event_alerts(repo.conn)
        assert _count_alerts(repo.conn, "NEW_EVENT") == 1
        row = repo.conn.execute(
            "SELECT entity_key FROM core.alerts WHERE alert_type='NEW_EVENT'"
        ).fetchone()
        assert row[0] == "tm::B"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Consecutive-snapshot change detection
# ---------------------------------------------------------------------------
def test_price_change_then_revert_creates_two_alerts(tmp_path):
    repo = FestivalRepository(str(tmp_path / "px.duckdb"))
    try:
        EventRepository(repo.conn)
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T1, price=50.0)
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T2, price=70.0)
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T3, price=50.0)
        generate_event_alerts(repo.conn)
        assert _count_alerts(repo.conn, "PRICE_RANGE_CHANGED") == 2
    finally:
        repo.close()


def test_status_change_then_revert(tmp_path):
    repo = FestivalRepository(str(tmp_path / "st.duckdb"))
    try:
        EventRepository(repo.conn)
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T1, status="onsale")
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T2, status="cancelled")
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T3, status="onsale")
        generate_event_alerts(repo.conn)
        assert _count_alerts(repo.conn, "EVENT_CANCELLED") == 1
        assert _count_alerts(repo.conn, "EVENT_STATUS_CHANGED") == 1
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Canonical artist display-name determinism (ARBITRARY() policy removal)
# ---------------------------------------------------------------------------
from festival_bloomberg.identity.artist_master import (  # noqa: E402
    collect_performer_mbids,
    backfill_canonical_names,
)
from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402


def _seed_performer(conn, *, event_mbid, artist_mbid, artist_name, role="main performer"):
    conn.execute(
        """
        INSERT OR IGNORE INTO core.event_performers
            (performer_key, event_mbid, artist_mbid, artist_name, performer_role,
             direction, source_system, knowledge_time, ingested_at)
        VALUES (?, ?, ?, ?, ?, 'forward', 'musicbrainz', now(), now())
        """,
        [f"pk::{event_mbid}::{artist_mbid}::{artist_name}",
         event_mbid, artist_mbid, artist_name, role],
    )


def test_canonical_name_uses_most_common_credit_deterministically(tmp_path):
    repo = FestivalRepository(str(tmp_path / "nm.duckdb"))
    apply_pending_migrations(repo.conn)
    try:
        # "Jay-Z" (5 relations) must beat "JAY-Z" (2) and "Jay Z" (1).
        _seed_performer(repo.conn, event_mbid="e1", artist_mbid="mbid-x", artist_name="Jay Z")
        for i in range(5):
            _seed_performer(repo.conn, event_mbid=f"e{i}", artist_mbid="mbid-x", artist_name="Jay-Z")
        for i in range(2):
            _seed_performer(repo.conn, event_mbid=f"f{i}", artist_mbid="mbid-x", artist_name="JAY-Z")
        rows = collect_performer_mbids(repo.conn)
        assert rows[0]["artist_name"] == "Jay-Z"
    finally:
        repo.close()


def test_canonical_name_deterministic_across_row_order(tmp_path):
    repo = FestivalRepository(str(tmp_path / "ord.duckdb"))
    apply_pending_migrations(repo.conn)
    try:
        # Same name set, different insertion order -> same preferred name.
        _seed_performer(repo.conn, event_mbid="a", artist_mbid="m1", artist_name="zeta")
        _seed_performer(repo.conn, event_mbid="b", artist_mbid="m1", artist_name="alpha")
        first = collect_performer_mbids(repo.conn)[0]["artist_name"]
        repo2 = FestivalRepository(str(tmp_path / "ord2.duckdb"))
        apply_pending_migrations(repo2.conn)
        try:
            _seed_performer(repo2.conn, event_mbid="b", artist_mbid="m1", artist_name="alpha")
            _seed_performer(repo2.conn, event_mbid="a", artist_mbid="m1", artist_name="zeta")
            second = collect_performer_mbids(repo2.conn)[0]["artist_name"]
            assert first == second
        finally:
            repo2.close()
    finally:
        repo.close()


def test_backfill_canonical_names_prefers_reference_name(tmp_path):
    repo = FestivalRepository(str(tmp_path / "bf.duckdb"))
    apply_pending_migrations(repo.conn)
    try:
        _seed_performer(repo.conn, event_mbid="e1", artist_mbid="mbid-r", artist_name="Stage Name")
        # Existing canonical row with an arbitrary/older name.
        repo.conn.execute(
            """
            INSERT INTO core.artists
                (artist_key, musicbrainz_id, name, normalized_name, sort_name, type,
                 source_system, evidence, extraction_method, resolution_status,
                 manually_reviewed, ingested_at, updated_at)
            VALUES ('mbid::mbid-r', 'mbid-r', 'Old Arbitrary Name', 'old arbitrary name',
                    'Old Arbitrary Name', 'UNKNOWN', 'musicbrainz', '{}',
                    'mbid_from_event_performers', 'REFERENCE', FALSE, now(), now())
            """
        )
        res = backfill_canonical_names(repo.conn)
        assert res["updated"] == 1
        name = repo.conn.execute(
            "SELECT name FROM core.artists WHERE artist_key = 'mbid::mbid-r'"
        ).fetchone()[0]
        assert name == "Stage Name"
    finally:
        repo.close()


def test_presale_discovered_and_unchanged_no_duplicate(tmp_path):
    repo = FestivalRepository(str(tmp_path / "pre.duckdb"))
    try:
        EventRepository(repo.conn)
        presale = json.dumps([{"name": "Fan", "start": "2026-08-20", "end": "2026-08-21"}])
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T1, presales=None)
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T2, presales=presale)
        generate_event_alerts(repo.conn)
        assert _count_alerts(repo.conn, "PRESALE_DISCOVERED") == 1
        # a third snapshot with the SAME presale must not duplicate
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T3, presales=presale)
        generate_event_alerts(repo.conn)
        assert _count_alerts(repo.conn, "PRESALE_DISCOVERED") == 1
    finally:
        repo.close()
