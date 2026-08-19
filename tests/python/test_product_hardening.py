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

import duckdb

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
                   presales=None, onsale_start=None, promoter=None,
                   acquisition_run_id=None):
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, event_status,
             onsale_start, presales, price_min, price_max, promoter, retrieved_at,
             knowledge_time, rights_status, commercial_use_status, ingested_at,
             acquisition_run_id)
        VALUES (?, 'ticketmaster', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'research', 'research', now(), ?)
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
            acquisition_run_id,
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
    assert classify_special("Rumours of Fleetwood Mac") == "TRIBUTE_ACT"
    assert classify_special("Rage UK – A Tribute to Rage Against the Machine") == "TRIBUTE_ACT"


def test_cover_band_classified():
    assert classify_special("Lana Del Rey Karaoke Band") == "COVER_BAND"


def test_weak_signals_do_not_reject_real_artists():
    # Collaboration billing and orchestra/symphony are WEAK features: real
    # artists legitimately named "Dead & Company" or an orchestra must not
    # be rejected by name heuristics alone.
    assert classify_special("Dead & Company") is None  # real band name, untouched
    assert "SPECIAL_EVENT" not in __import__(
        "festival_bloomberg.identity.ticketmaster_resolution",
        fromlist=["REJECT_SPECIALS"],
    ).REJECT_SPECIALS
    assert "COLLABORATION_BILLING" not in __import__(
        "festival_bloomberg.identity.ticketmaster_resolution",
        fromlist=["REJECT_SPECIALS"],
    ).REJECT_SPECIALS


def test_dj_khaled_still_plain_artist():
    assert classify_special("DJ Khaled") is None


# ---------------------------------------------------------------------------
# External-ID collision ledger + YouTube channel extraction
# ---------------------------------------------------------------------------
from festival_bloomberg.oa.music_terminal_productization import (  # noqa: E402
    _extract_youtube_channel_id,
    audit_external_id_collisions,
)


def test_youtube_extraction_rejects_path_fragments():
    assert _extract_youtube_channel_id("https://www.youtube.com/channel/UCewJbjHlk3iXNanury7vw9g") == "UCewJbjHlk3iXNanury7vw9g"
    assert _extract_youtube_channel_id("https://www.youtube.com/@billieeilish") == "@billieeilish"
    assert _extract_youtube_channel_id("https://www.youtube.com/featured") is None
    assert _extract_youtube_channel_id("https://www.youtube.com/videos") is None
    assert _extract_youtube_channel_id("https://www.youtube.com/") is None


def test_external_id_collisions_persist_as_conflicts(tmp_path):
    repo = FestivalRepository(str(tmp_path / "col.duckdb"))
    try:
        EventRepository(repo.conn)
        for key in ("mbid::a", "mbid::b"):
            repo.conn.execute(
                """
                INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name)
                VALUES (?, ?, 'Artist', 'artist')
                """,
                [key, key.removeprefix("mbid::")],
            )
        for key in ("mbid::a", "mbid::b"):
            repo.conn.execute(
                """
                INSERT INTO core.entity_external_ids
                    (external_id_key, entity_type, entity_key, id_type, id_value,
                     url, is_primary, confidence, source_system, namespace,
                     resolution_status, resolution_method, first_seen_at, last_seen_at,
                     knowledge_time, ingested_at)
                VALUES (?, 'artist', ?, 'wikidata', 'Q12345', 'https://www.wikidata.org/wiki/Q12345',
                        FALSE, 1.0, 'musicbrainz', 'wikidata', 'CROWD_CURATED_REFERENCE',
                        'mb_url_relationship', now(), now(), now(), now())
                """,
                [f"ek::{key}", key],
            )
        res = audit_external_id_collisions(repo.conn)
        assert res["conflicts_persisted"] == 1
        rows = repo.conn.execute(
            "SELECT entity_key, issue FROM core.identity_conflicts"
        ).fetchall()
        assert len(rows) == 1
        assert "Q12345" in rows[0][1]
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Artist search index (materialized terms, exact-first, FTS candidate layer)
# ---------------------------------------------------------------------------
from festival_bloomberg.intelligence.readmodels import search_entities  # noqa: E402
from festival_bloomberg.oa.music_terminal_productization import build_artist_search_index  # noqa: E402


def _seed_reference_artist(conn, *, mbid, name, sort_name=None, aliases=None):
    conn.execute(
        """
        INSERT INTO reference.musicbrainz_artists
            (mbid, name, normalized_name, sort_name, aliases, knowledge_time, ingested_at)
        VALUES (?, ?, ?, ?, ?, now(), now())
        """,
        [mbid, name, name.lower(), sort_name or name,
         json.dumps(aliases or []), ],
    )


def test_search_index_exact_first_and_fallback(tmp_path):
    repo = FestivalRepository(str(tmp_path / "si.duckdb"))
    try:
        EventRepository(repo.conn)
        _seed_reference_artist(repo.conn, mbid="m1", name="Billie Eilish",
                               aliases=[{"name": "Billie Eilish Pirate Baird O'Connell"}])
        _seed_reference_artist(repo.conn, mbid="m2", name="Bad Bunny")
        _seed_reference_artist(repo.conn, mbid="m3", name="Fred again..")
        repo.conn.execute(
            """
            INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name)
            VALUES ('mbid::m1', 'm1', 'Billie Eilish', 'billie eilish')
            """
        )
        res = build_artist_search_index(repo.conn)
        assert res["terms_after"] >= 3
        # exact canonical surfaces first
        hits = search_entities(repo.conn, "Billie Eilish", limit=5)
        assert hits and hits[0]["entity_id"] == "mbid::m1"
        # alias match resolves to the same artist
        hits2 = search_entities(repo.conn, "Pirate Baird", limit=5)
        assert any(h["entity_id"] == "mbid::m1" for h in hits2)
        # no-match returns empty, not an error
        assert search_entities(repo.conn, "zzzz not an artist") == []
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Pipeline phase ledger (resumability)
# ---------------------------------------------------------------------------
def test_pipeline_phase_ledger_records_runs(tmp_path):
    from festival_bloomberg.oa.music_terminal_productization import run_music_terminal_productization_oa
    db = tmp_path / "ledger.duckdb"
    out = run_music_terminal_productization_oa(
        db_path=str(db), report_path=str(tmp_path / "r1.json"),
        phases=("artist_master_bootstrap",),
    )
    conn = duckdb.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT milestone, phase, status FROM audit.pipeline_phase_runs"
        ).fetchall()
        assert rows, "phase ledger should record the bootstrap phase"
        assert rows[0][0] == "music_terminal_productization_v1"
        assert rows[0][1] == "artist_master_bootstrap"
        assert rows[0][2] == "COMPLETE"
    finally:
        conn.close()


def test_tribute_act_not_merged_into_real_band(tmp_path):
    """A tribute act that shares its name with a real band must be rejected,
    never merged into that band (false-merge is the worst failure mode)."""
    from festival_bloomberg.identity.ticketmaster_resolution import resolve_attraction
    repo = FestivalRepository(str(tmp_path / "trib.duckdb"))
    try:
        EventRepository(repo.conn)
        # Real band "Fleetwood Mac" exists in the canonical master.
        repo.conn.execute(
            """
            INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name)
            VALUES ('mbid::fleetwood', 'bd13909f-1c29-4c27-a874-d4aaf27c5b1a',
                    'Fleetwood Mac', 'fleetwood mac')
            """
        )
        # "Rumours of Fleetwood Mac" is a tribute act: must NOT map to the band.
        result = resolve_attraction(
            repo.conn, attraction_name="Rumours of Fleetwood Mac",
            knowledge_time="2026-08-18T00:00:00+00:00")
        assert result["resolution_status"] == "REJECTED_NON_ARTIST"
        assert result.get("special_classification") == "TRIBUTE_ACT"
        assert result.get("artist_key") is None
        # And the real band still resolves to itself.
        real = resolve_attraction(
            repo.conn, attraction_name="Fleetwood Mac",
            knowledge_time="2026-08-18T00:00:00+00:00")
        assert real["resolution_status"] == "MATCHED_ARTIST"
        assert real.get("artist_key") == "mbid::fleetwood"
    finally:
        repo.close()


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


# ---------------------------------------------------------------------------
# Explicit acquisition runs + alert related-entity graph + personalized TODAY
# ---------------------------------------------------------------------------
from festival_bloomberg.product.workflow import (  # noqa: E402
    build_today,
    complete_acquisition_run,
    start_acquisition_run,
)


def _seed_resolution(conn, *, attraction_id, artist_key, artist_mbid="mbid-1"):
    conn.execute(
        """
        INSERT INTO identity.ticketmaster_artist_resolutions
            (resolution_key, attraction_id, attraction_name, normalized_name,
             artist_key, artist_mbid, matched_name, resolution_status, match_method,
             knowledge_time)
        VALUES (?, ?, 'Test Artist', 'test artist', ?, ?, 'Test Artist',
                'MATCHED_ARTIST', 'MB_EXACT_NAME', now())
        """,
        [f"rk::{attraction_id}", attraction_id, artist_key, artist_mbid],
    )


def test_new_event_with_explicit_runs_creates_alert(tmp_path):
    repo = FestivalRepository(str(tmp_path / "runs.duckdb"))
    try:
        EventRepository(repo.conn)
        r1 = start_acquisition_run(repo.conn, provider="ticketmaster", operation="refresh")
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T1, price=50.0,
                       acquisition_run_id=r1)
        complete_acquisition_run(repo.conn, run_id=r1, status="COMPLETE", record_count=1)
        r2 = start_acquisition_run(repo.conn, provider="ticketmaster", operation="refresh")
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T2, price=50.0,
                       acquisition_run_id=r2)
        _seed_snapshot(repo.conn, eid="B", retrieved_at=T2, price=60.0,
                       acquisition_run_id=r2)
        complete_acquisition_run(repo.conn, run_id=r2, status="COMPLETE", record_count=2)
        out = generate_new_event_alerts(repo.conn)
        assert out["status"] == "COMPLETE"
        assert _count_alerts(repo.conn, "NEW_EVENT") == 1
        row = repo.conn.execute(
            "SELECT entity_key FROM core.alerts WHERE alert_type='NEW_EVENT'"
        ).fetchone()
        assert row[0] == "tm::B"
    finally:
        repo.close()


def test_alert_related_entities_attach_resolved_artist(tmp_path):
    repo = FestivalRepository(str(tmp_path / "rel.duckdb"))
    try:
        EventRepository(repo.conn)
        _seed_attraction(repo.conn, eid="e1", attraction_id="tm-1", attraction_name="Billie Eilish")
        _seed_resolution(repo.conn, attraction_id="tm-1", artist_key="mbid::billie")
        r1 = start_acquisition_run(repo.conn, provider="ticketmaster", operation="refresh")
        _seed_snapshot(repo.conn, eid="old", retrieved_at=T1, price=50.0,
                       acquisition_run_id=r1)
        complete_acquisition_run(repo.conn, run_id=r1, status="COMPLETE")
        r2 = start_acquisition_run(repo.conn, provider="ticketmaster", operation="refresh")
        _seed_snapshot(repo.conn, eid="old", retrieved_at=T2, price=50.0,
                       acquisition_run_id=r2)
        _seed_snapshot(repo.conn, eid="e1", retrieved_at=T2, price=60.0,
                       acquisition_run_id=r2)
        complete_acquisition_run(repo.conn, run_id=r2, status="COMPLETE")
        generate_new_event_alerts(repo.conn)
        edges = repo.conn.execute(
            """
            SELECT entity_type, entity_key, relationship FROM core.alert_related_entities
            """
        ).fetchall()
        artist_edges = [e for e in edges if e[0] == "ARTIST"]
        assert artist_edges, "alert should link to the resolved artist"
        assert artist_edges[0][1] == "mbid::billie"
        assert artist_edges[0][2] == "EVENT_PRIMARY_ARTIST"
    finally:
        repo.close()


def test_today_personalized_for_watched_artist(tmp_path):
    repo = FestivalRepository(str(tmp_path / "today.duckdb"))
    try:
        EventRepository(repo.conn)
        _seed_attraction(repo.conn, eid="e1", attraction_id="tm-1", attraction_name="Billie Eilish")
        _seed_resolution(repo.conn, attraction_id="tm-1", artist_key="mbid::billie")
        wl = create_watchlist(repo.conn, name="Talent", entity_type="ARTIST")
        add_watchlist_item(repo.conn, watchlist_key_value=wl["watchlist_key"],
                           entity_type="ARTIST", entity_key_value="mbid::billie",
                           entity_name="Billie Eilish")
        r1 = start_acquisition_run(repo.conn, provider="ticketmaster", operation="refresh")
        _seed_snapshot(repo.conn, eid="old", retrieved_at=T1, price=50.0,
                       acquisition_run_id=r1)
        complete_acquisition_run(repo.conn, run_id=r1, status="COMPLETE")
        r2 = start_acquisition_run(repo.conn, provider="ticketmaster", operation="refresh")
        _seed_snapshot(repo.conn, eid="old", retrieved_at=T2, price=50.0,
                       acquisition_run_id=r2)
        _seed_snapshot(repo.conn, eid="e1", retrieved_at=T2, price=60.0,
                       acquisition_run_id=r2)
        complete_acquisition_run(repo.conn, run_id=r2, status="COMPLETE")
        generate_new_event_alerts(repo.conn)
        today = build_today(repo.conn, limit=10)
        wl_new = today["sections"]["watchlist"]["new_events"]
        assert any(x["entity_key"] == "tm::e1" for x in wl_new), \
            "watched artist must surface the related new-event alert"
        # contract: flat first_seen_at present
        assert wl_new[0]["first_seen_at"]
    finally:
        repo.close()


def test_today_ticketing_contract_flat_fields(tmp_path):
    repo = FestivalRepository(str(tmp_path / "tc.duckdb"))
    try:
        EventRepository(repo.conn)
        presale = json.dumps([{"name": "Fan", "start": "2026-08-20", "end": "2026-08-21"}])
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T1, price=50.0)
        _seed_snapshot(repo.conn, eid="A", retrieved_at=T2, price=70.0, presales=presale)
        generate_event_alerts(repo.conn)
        today = build_today(repo.conn, limit=10)
        tick = today["sections"]["ticketing"]
        for r in tick["new_presales"]:
            assert r["event_name"] and r["presale_start"] == "2026-08-20"
            assert "detail" not in r  # contract: flat, never nested detail
        for r in tick["status_changes"]:
            assert r["event_name"]
    finally:
        repo.close()
