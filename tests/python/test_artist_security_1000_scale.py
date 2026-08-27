"""Regression coverage for ARTIST_SECURITY_1000_SCALE_V1.

Covers (all offline via scripted FakeTransport):
- Migration 044 tables exist
- Wikimedia batched backfill (per-day rows, PIT fields, idempotent)
- Identity master (lake + MBID-derived linkages, scorecard, no name-only verify)
- Spotify identity repair (lake name-join candidates, ambiguity fails closed)
- Live/ticket joins (setlistfm history → live stats → future/ticket factors)
- Feast adoption (real-history PIT validation verdicts)
- Perspective monitor (real-data export + semantics)
- EVENT_TAPE_2000 (provider estate bootstrap + measurement)
- Artist × market security objects (P10)
- YouTube invalid-key provisioning status (fails closed)
- Success report builder
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations

from tests.python.conftest import FakeTransport

from festival_bloomberg.attention.wikimedia_historical import (
    collect_artist_daily_pageviews_batched,
    daily_observation_key,
)
from festival_bloomberg.attention.youtube_forward import collect_channel_snapshots
from festival_bloomberg.identity.identity_master import (
    run_identity_master,
    resolve_from_lake,
    resolve_mbid_derived,
)
from festival_bloomberg.identity.spotify_identity import (
    collect_catalog_for_universe,
    lake_spotify_candidates,
    run_spotify_identity,
    search_spotify_candidates,
)
from festival_bloomberg.security.artist_market import build_artist_market_rows
from festival_bloomberg.security.artist_security_master import select_security_universe
from festival_bloomberg.security.event_tape import (
    ingest_provider_estate_events,
    measure_tape,
)
from festival_bloomberg.security.feast_adoption import (
    load_real_factor_series,
    run_real_adoption,
)
from festival_bloomberg.security.live_ticket import (
    collect_setlistfm_history,
    derive_live_statistics,
    join_future_events_and_tickets,
    market_key_for,
    run_live_ticket,
)
from festival_bloomberg.security.perspective_monitor import (
    export_monitor_rows,
    run_monitor,
)
from festival_bloomberg.security.scale_report import build_success_report


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "scale.duckdb"))
    c.execute("SET memory_limit='2GB'")
    apply_pending_migrations(c)
    yield c
    c.close()


def _seed_artist(conn, artist_key, name, mbid, *, channel_id=None, spotify_id=None):
    conn.execute(
        """INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name, sort_name)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT (artist_key) DO NOTHING""",
        [artist_key, mbid, name, name.lower(), name],
    )
    for id_type, id_value in (("musicbrainz", mbid), ("youtube", channel_id), ("spotify", spotify_id)):
        if not id_value:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO core.entity_external_ids
               (external_id_key, entity_type, entity_key, id_type, id_value, url,
                is_primary, confidence, source_system)
               VALUES (?, 'artist', ?, ?, ?, ?, FALSE, 1.0, 'test')""",
            [f"eid_{artist_key}_{id_type}", artist_key, id_type, id_value,
             f"https://example.com/{id_value}"],
        )


def _seed_lb_total(conn, artist_key, listens=1000):
    conn.execute(
        """INSERT INTO metrics.artist_attention_observations
           (observation_key, artist_key, source_system, metric_kind, value,
            value_unit, status, source_url, retrieved_at, metric_version)
           VALUES (?, ?, 'listenbrainz', 'LISTENBRAINZ_TOTAL_LISTEN_COUNT', ?, 'count',
                   'ok', 'https://src', '2026-08-20T00:00:00Z', 'v1')""",
        [f"obs_{artist_key}_total", artist_key, listens],
    )


def _seed_wiki_daily(conn, artist_key, days=40, spike_day=None, spike=1000.0, base=100.0):
    today = date.today()
    d = today - timedelta(days=days)
    while d < today:
        views = spike if spike_day and d == spike_day else base
        conn.execute(
            """INSERT INTO metrics.artist_attention_observations
               (observation_key, artist_key, source_system, metric_kind, value,
                value_unit, status, source_url, retrieved_at, period_start, period_end,
                metric_version)
               VALUES (?, ?, 'wikimedia', 'pageviews', ?, 'pageviews', 'ok',
                       'https://src', '2026-08-20T00:00:00Z', ?, ?,
                       'wikimedia_pageviews_daily_v1')""",
            [daily_observation_key(artist_key=artist_key, day=d.isoformat()),
             artist_key, views, d.isoformat(), d.isoformat()],
        )
        d += timedelta(days=1)


def _seed_event(conn, event_mbid, begin_date, event_type="Concert"):
    conn.execute(
        """INSERT OR IGNORE INTO raw.musicbrainz_event (mbid, name, event_type, begin_date, ingested_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        [event_mbid, f"event {event_mbid}", event_type, begin_date],
    )
    conn.execute(
        """INSERT INTO core.event_performers
           (performer_key, event_mbid, artist_mbid, artist_name, performer_role, source_system, ingested_at)
           VALUES (?, ?, ?, ?, 'main performer', 'musicbrainz', CURRENT_TIMESTAMP)
           ON CONFLICT (performer_key) DO NOTHING""",
        [f"perf_{event_mbid}", event_mbid, event_mbid, "artist"],
    )


def _seed_tm_estate(conn, event_id, artist_name, city, state, local_date, venue="Venue A"):
    conn.execute(
        """INSERT INTO events.provider_event_snapshots
           (snapshot_key, provider, platform_object_id, event_name, artist_name,
            venue_name, city, state_code, country_code, local_date, event_status,
            canonical_url, retrieved_at, knowledge_time, rights_status,
            commercial_use_status, software_version, ingested_at)
           VALUES (?, 'ticketmaster', ?, ?, ?, ?, ?, ?, 'US', ?, 'onsale', ?,
                   '2026-08-20T00:00:00Z', '2026-08-20T00:00:00Z',
                   'RESEARCH_ONLY', 'PROTOTYPE_ONLY', 'test', CURRENT_TIMESTAMP)""",
        [f"snap_{event_id}", event_id, f"{artist_name} live", artist_name,
         venue, city, state, local_date, f"https://tm.com/event/{event_id}"],
    )


def _wiki_item(day: date, views: int) -> dict:
    return {
        "project": "en.wikipedia", "article": "Artist", "granularity": "daily",
        "timestamp": day.strftime("%Y%m%d") + "00",
        "access": "all-access", "agent": "user", "views": views,
    }


# ---------------------------------------------------------------------------
# Migration 044
# ---------------------------------------------------------------------------

class TestMigration044:
    def test_scale_tables_exist(self, conn):
        for table in (
            "identity.artist_provider_linkages",
            "identity.identity_coverage_scorecard",
            "asm.artist_market_security_v1",
            "acquisition.event_tape_scale",
            "metrics.artist_performance_observations",
        ):
            schema, name = table.split(".")
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                [schema, name],
            ).fetchone()
            assert row, f"{table} missing"
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='identity' AND table_name='artist_provider_linkages'"
        ).fetchall()}
        for required in ("artist_key", "provider", "provider_id", "link_method",
                         "confidence", "evidence_ref", "first_seen_at",
                         "last_verified_at", "rights_status", "commercial_use_status"):
            assert required in cols


# ---------------------------------------------------------------------------
# P0: Wikimedia batched backfill
# ---------------------------------------------------------------------------

class TestWikimediaBatched:
    def test_batched_daily_rows_pit_fields(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        day1, day2 = date(2026, 8, 1), date(2026, 8, 2)
        t = FakeTransport([(200, {"items": [_wiki_item(day1, 100), _wiki_item(day2, 150)]})])
        s = collect_artist_daily_pageviews_batched(
            conn, t, names=["Alpha Artist"], start="2026-08-01", end="2026-08-02",
            min_interval_seconds=0,
            artist_keys_by_name={"Alpha Artist": "mbid::a1"},
        )
        assert s["status"] == "COMPLETE"
        assert s["daily_rows_persisted"] == 2
        assert s["names_ok"] == 1
        rows = conn.execute(
            "SELECT artist_key, period_start, period_end, value, metric_version "
            "FROM metrics.artist_attention_observations WHERE source_system='wikimedia' ORDER BY period_start"
        ).fetchall()
        assert [(r[0], r[1].isoformat(), r[2].isoformat(), r[3]) for r in rows] == [
            ("mbid::a1", "2026-08-01", "2026-08-01", 100),
            ("mbid::a1", "2026-08-02", "2026-08-02", 150),
        ]
        assert all(r[4] == "wikimedia_pageviews_daily_v1" for r in rows)

    def test_batched_idempotent_rerun(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        day = date(2026, 8, 1)
        t1 = FakeTransport([(200, {"items": [_wiki_item(day, 100)]})])
        s1 = collect_artist_daily_pageviews_batched(
            conn, t1, names=["Alpha Artist"], start="2026-08-01", end="2026-08-01",
            min_interval_seconds=0, artist_keys_by_name={"Alpha Artist": "mbid::a1"},
        )
        t2 = FakeTransport([(200, {"items": [_wiki_item(day, 100)]})])
        s2 = collect_artist_daily_pageviews_batched(
            conn, t2, names=["Alpha Artist"], start="2026-08-01", end="2026-08-01",
            min_interval_seconds=0, artist_keys_by_name={"Alpha Artist": "mbid::a1"},
        )
        assert s1["daily_rows_persisted"] == 1
        assert s2["daily_rows_persisted"] == 0
        assert s2["daily_rows_skipped_existing"] == 1

    def test_batched_pre_series_never_written(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        pre, day = date(2015, 6, 30), date(2026, 8, 1)
        t = FakeTransport([(200, {"items": [_wiki_item(pre, 999), _wiki_item(day, 50)]})])
        s = collect_artist_daily_pageviews_batched(
            conn, t, names=["Alpha Artist"], start="2026-08-01", end="2026-08-01",
            min_interval_seconds=0, artist_keys_by_name={"Alpha Artist": "mbid::a1"},
        )
        assert s["daily_rows_persisted"] == 1
        vals = [r[0] for r in conn.execute(
            "SELECT value FROM metrics.artist_attention_observations WHERE source_system='wikimedia'"
        ).fetchall()]
        assert vals == [50]
    def test_wiki_acceleration_factor_derived(self, conn):
        """WIKI_ACCELERATION (second-order momentum) is derived from real history."""
        from festival_bloomberg.security.artist_security_master import derive_demand_and_momentum_factors
        from festival_bloomberg.security.artist_security_master import select_security_universe

        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        # 100 days: flat 100/day for first 84, then accelerating to 200/day
        today = date.today()
        day = today - timedelta(days=100)
        while day < today:
            views = 200.0 if day >= today - timedelta(days=28) else 100.0
            conn.execute(
                """INSERT INTO metrics.artist_attention_observations
                   (observation_key, artist_key, source_system, metric_kind, value,
                    value_unit, status, source_url, retrieved_at, period_start, period_end,
                    metric_version)
                   VALUES (?, 'mbid::a1', 'wikimedia', 'pageviews', ?, 'pageviews', 'ok',
                           'https://src', '2026-08-20T00:00:00Z', ?, ?,
                           'wikimedia_pageviews_daily_v1')""",
                [daily_observation_key(artist_key="mbid::a1", day=day.isoformat()),
                 views, day.isoformat(), day.isoformat()],
            )
            day += timedelta(days=1)
        rows, _ = derive_demand_and_momentum_factors(
            conn, universe=select_security_universe(conn, limit=10), as_of=today,
        )
        accel = [r for r in rows if r["factor_name"] == "WIKI_ACCELERATION"]
        assert accel, "WIKI_ACCELERATION should be derived when 84+ days of history exist"
        assert accel[0]["factor_family"] == "MOMENTUM"
        # 28d window = 5600, prior 28d = 2800 → momentum_now = 1.0;
        # prior window = 2800, prior-prior = 2800 → momentum_prior = 0.0;
        # acceleration = 1.0 - 0.0 = 1.0
        assert accel[0]["value"] == pytest.approx(1.0, abs=0.01)
        assert str(accel[0]["period_start"]) == (today - timedelta(days=84)).isoformat()


# ---------------------------------------------------------------------------
# P2: Identity master
# ---------------------------------------------------------------------------

class TestIdentityMaster:
    def test_mbid_derived_linkages(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        linkages = resolve_mbid_derived(select_security_universe(conn, limit=10))
        providers = {l["provider"] for l in linkages}
        assert {"MUSICBRAINZ", "LISTENBRAINZ"} <= providers
        assert all(l["resolution_status"] == "VERIFIED" for l in linkages)
        assert all(l["confidence"] == 1.0 for l in linkages)

    def test_lake_linkages_verified_with_evidence(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc", spotify_id="sp1")
        universe = select_security_universe(conn, limit=10)
        linkages = resolve_from_lake(conn, universe=universe)
        providers = {l["provider"]: l for l in linkages}
        assert providers["YOUTUBE"]["provider_id"] == "UCabc"
        assert providers["SPOTIFY"]["provider_id"] == "sp1"
        assert providers["YOUTUBE"]["resolution_status"] == "VERIFIED"
        assert providers["YOUTUBE"]["link_method"] == "LAKE_EXTERNAL_ID"
        assert providers["YOUTUBE"]["evidence_ref"].startswith("https://")

    def test_run_identity_master_scorecard(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc")
        result = run_identity_master(conn, universe_limit=10)
        assert result["status"] == "COMPLETE"
        assert result["linkages"]["inserted"] >= 3  # mbid + lb + youtube
        sc = result["scorecard"]
        assert sc["status"] == "COMPLETE"
        assert sc["universe_size"] >= 1
        assert "YOUTUBE" in sc["providers"]
        assert sc["providers"]["YOUTUBE"]["verified_count"] == 1
        # scorecard persisted
        n = conn.execute(
            "SELECT COUNT(*) FROM identity.identity_coverage_scorecard"
        ).fetchone()[0]
        assert n > 0


# ---------------------------------------------------------------------------
# P4: Spotify identity repair
# ---------------------------------------------------------------------------

class TestSpotifyIdentity:
    def test_lake_name_join_is_candidate_not_verified(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        # lake spotify id keyed by legacy name:: key (the pilot's weakness)
        conn.execute(
            """INSERT INTO core.entity_external_ids
               (external_id_key, entity_type, entity_key, id_type, id_value, url,
                is_primary, confidence, source_system)
               VALUES ('eid_lake_sp', 'artist', 'name::alpha artist', 'spotify', 'sp1',
                       'https://open.spotify.com/artist/sp1', FALSE, 1.0, 'spotify')"""
        )
        linkages, summary = lake_spotify_candidates(conn, universe=select_security_universe(conn, limit=10))
        spot = [l for l in linkages if l["provider"] == "SPOTIFY"]
        assert len(spot) == 1
        assert spot[0]["resolution_status"] == "CANDIDATE"  # never silently verified
        assert spot[0]["link_method"] == "LAKE_NAME_JOIN"
        assert spot[0]["confidence"] < 1.0
        assert summary["candidates"] == 1

    def test_lake_name_join_ambiguous_fails_closed(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        for i, sid in enumerate(("sp1", "sp2")):
            conn.execute(
                """INSERT INTO core.entity_external_ids
                   (external_id_key, entity_type, entity_key, id_type, id_value, url,
                    is_primary, confidence, source_system)
                   VALUES (?, 'artist', 'name::alpha artist', 'spotify', ?, ?, FALSE, 1.0, 'spotify')""",
                [f"eid_lake_sp{i}", sid, f"https://open.spotify.com/artist/{sid}"],
            )
        linkages, summary = lake_spotify_candidates(conn, universe=select_security_universe(conn, limit=10))
        spot = [l for l in linkages if l["provider"] == "SPOTIFY"]
        assert all(l["resolution_status"] == "AMBIGUOUS" for l in spot)
        assert summary["ambiguous"] == 1

    def test_search_candidates_fail_closed_without_creds(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        result = search_spotify_candidates(
            conn, FakeTransport(), universe=select_security_universe(conn, limit=10),
            client_id=None, client_secret=None,
        )
        assert result["status"] == "NOT_CONFIGURED"

    def test_search_exact_becomes_candidate(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        t = FakeTransport([
            # token exchange
            (200, {"access_token": "tok", "expires_in": 3600}),
            # search
            (200, {"artists": {"items": [
                {"id": "sp1", "name": "Alpha Artist", "type": "artist", "uri": "spotify:artist:sp1",
                 "external_urls": {"spotify": "https://open.spotify.com/artist/sp1"}}
            ], "total": 1}}),
        ])
        result = search_spotify_candidates(
            conn, t, universe=select_security_universe(conn, limit=10),
            client_id="cid", client_secret="csec",
        )
        assert result["status"] == "COMPLETE"
        assert result["artists_searched"] == 1
        assert result["linkages_persisted"] >= 1
        row = conn.execute(
            "SELECT resolution_status, link_method FROM identity.artist_provider_linkages "
            "WHERE provider='SPOTIFY'"
        ).fetchone()
        assert row[0] == "CANDIDATE"
        assert row[1] == "PROVIDER_SEARCH_CANDIDATE"

    def test_catalog_collection_no_creds_fails_closed(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        result = collect_catalog_for_universe(
            conn, FakeTransport(), universe=select_security_universe(conn, limit=10),
            client_id=None, client_secret=None,
        )
        assert result["status"] == "NOT_CONFIGURED"

    def test_run_spotify_identity_full(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        result = run_spotify_identity(
            conn, FakeTransport(), universe=select_security_universe(conn, limit=10),
            client_id=None, client_secret=None,
        )
        assert result["status"] == "COMPLETE"
        assert result["search_candidates"]["status"] == "NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# P5: Live + ticket joins
# ---------------------------------------------------------------------------

class TestLiveTicket:
    def _universe(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        return select_security_universe(conn, limit=10)

    def _setlist_payload(self, artist, event_date, venue, city, state, setlist_id):
        return {
            "type": "setlists", "itemsPerPage": 20, "page": 1, "total": 1,
            "setlist": [{
                "id": setlist_id, "versionId": f"v{setlist_id}", "eventDate": event_date,
                "lastUpdated": "2026-08-20T00:00:00.000+0000",
                "artist": {"mbid": "a1", "name": artist},
                "venue": {"id": "v1", "name": venue, "city": {
                    "id": "c1", "name": city, "stateCode": state,
                    "country": {"code": "US", "name": "United States"},
                    "coords": {"lat": 41.8, "long": -87.6}}},
                "url": f"https://www.setlist.fm/setlist/{setlist_id}.html",
            }],
        }

    def test_collect_setlistfm_history(self, conn):
        universe = self._universe(conn)
        t = FakeTransport([(200, self._setlist_payload(
            "Alpha Artist", "10-08-2026", "Madison Square Garden", "New York", "NY", "abc123"))])
        s = collect_setlistfm_history(
            conn, t, universe=universe, api_key="key", min_interval_seconds=0,
        )
        assert s["status"] == "COMPLETE"
        assert s["artists_with_shows"] == 1
        assert s["rows_persisted"] == 1
        row = conn.execute(
            "SELECT show_date, market_key, venue_name, event_type, source_system "
            "FROM metrics.artist_performance_observations"
        ).fetchone()
        assert row[0].isoformat() == "2026-08-10"
        assert row[1] == "new-york-ny"
        assert row[2] == "Madison Square Garden"

    def test_setlist_no_key_fails_closed(self, conn):
        s = collect_setlistfm_history(conn, FakeTransport(), universe=self._universe(conn), api_key=None)
        assert s["status"] == "NOT_CONFIGURED"

    def test_market_key_for(self):
        assert market_key_for("New York", "NY") == "new-york-ny"
        assert market_key_for("Las Vegas", "NV") == "las-vegas-nv"
        assert market_key_for(None, "NY") is None

    def test_derive_live_statistics(self, conn):
        universe = self._universe(conn)
        t = FakeTransport([(200, self._setlist_payload(
            "Alpha Artist", "01-01-2026", "Venue X", "Chicago", "IL", "s1"))])
        collect_setlistfm_history(conn, t, universe=universe, api_key="key", min_interval_seconds=0)
        # seed an older show via MB events
        _seed_event(conn, "evt_old", "2025-01-01")
        conn.execute(
            """INSERT INTO metrics.artist_performance_observations
               (performance_key, artist_key, show_date, venue_name, market_key, event_type,
                source_system, retrieved_at, rights_status, commercial_use_status)
               VALUES ('perf_old', 'mbid::a1', '2025-01-01', 'Old Hall', 'chicago-il',
                       'CONCERT', 'musicbrainz', '2026-08-20T00:00:00Z',
                       'CC0_CORE', 'PROTOTYPE_ONLY')"""
        )
        result = derive_live_statistics(conn, universe=universe)
        assert result["status"] == "COMPLETE"
        assert result["rows_written"] == 1
        row = conn.execute(
            "SELECT shows_365d, markets_365d, unique_venues_365d, days_since_last_show "
            "FROM metrics.artist_live_statistics"
        ).fetchone()
        assert row[0] == 1  # 2026-01-01 within 365d of today(2026-08-26)
        assert row[1] == 1
        assert row[2] == 1

    def test_future_events_and_tickets_join(self, conn):
        universe = self._universe(conn)
        _seed_tm_estate(conn, "evt101", "Alpha Artist", "New York", "NY", "2026-11-01")
        result = join_future_events_and_tickets(conn, universe=universe)
        assert result["status"] == "COMPLETE"
        assert result["artists_with_future_events"] == 1
        rows = conn.execute(
            "SELECT factor_name, value FROM metrics.artist_market_factor_observations "
            "WHERE artist_key='mbid::a1' ORDER BY factor_name"
        ).fetchall()
        names = {r[0]: r[1] for r in rows}
        assert "UPCOMING_MARKET_EVENTS" in names
        g = conn.execute(
            "SELECT value FROM metrics.artist_factor_observations "
            "WHERE artist_key='mbid::a1' AND factor_name='FUTURE_EVENTS'"
        ).fetchone()
        assert g and g[0] == 1


# ---------------------------------------------------------------------------
# P6: Feast adoption
# ---------------------------------------------------------------------------

class TestFeastAdoption:
    def test_load_real_factor_series(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        conn.execute(
            """INSERT INTO metrics.artist_factor_observations
               (factor_observation_key, artist_key, factor_family, factor_name, value,
                value_unit, as_of, available_at, retrieved_at, source_system, source_version,
                rights_status, commercial_use_status)
               VALUES ('f1', 'mbid::a1', 'MOMENTUM', 'WIKI_MOMENTUM', 0.25, 'relative',
                       '2026-08-20', '2026-08-21T00:00:00Z', '2026-08-22T00:00:00Z',
                       'test', 'v1', 'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')"""
        )
        daily = load_real_factor_series(conn, artist_key="mbid::a1", factor_name="WIKI_MOMENTUM")
        assert daily == {date(2026, 8, 20): 0.25}

    def test_real_adoption_approves_on_history(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        _seed_wiki_daily(conn, "mbid::a1", days=40)
        universe = select_security_universe(conn, limit=10)
        result = run_real_adoption(conn, universe=universe)
        assert result["status"] == "COMPLETE"
        assert result["verdict"] == "APPROVED_DEPENDENCY_BOUNDED"
        assert result["total_mismatches"] == 0
        assert result["artists_with_real_history"] == 1

    def test_real_adoption_no_data(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        universe = select_security_universe(conn, limit=10)
        result = run_real_adoption(conn, universe=universe)
        assert result["artists_with_real_history"] == 0
        assert result["verdict"] == "APPROVED_DEPENDENCY_BOUNDED"  # no divergence on no data


# ---------------------------------------------------------------------------
# P7: Perspective monitor
# ---------------------------------------------------------------------------

class TestPerspectiveMonitor:
    def _seed_full(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc")
        _seed_lb_total(conn, "mbid::a1")
        _seed_wiki_daily(conn, "mbid::a1", days=40)
        conn.execute(
            """INSERT INTO metrics.artist_factor_observations
               (factor_observation_key, artist_key, factor_family, factor_name, value,
                value_unit, as_of, retrieved_at, source_system, source_version,
                rights_status, commercial_use_status)
               VALUES
               ('f_wiki', 'mbid::a1', 'MOMENTUM', 'WIKI_MOMENTUM', 0.35, 'relative',
                '2026-08-20', '2026-08-20T00:00:00Z', 'test', 'v1',
                'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY'),
               ('f_yt', 'mbid::a1', 'DEMAND', 'YT_SUBSCRIBERS', 1200.0, 'subscribers',
                '2026-08-20', '2026-08-20T00:00:00Z', 'test', 'v1',
                'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')"""
        )
        conn.execute(
            """INSERT INTO metrics.artist_live_statistics
               (stat_key, artist_key, as_of, shows_30d, shows_90d, shows_365d,
                markets_365d, unique_venues_365d, festival_appearances_365d,
                days_since_last_show, source_system, source_version, retrieved_at,
                rights_status, commercial_use_status)
               VALUES ('ls1', 'mbid::a1', '2026-08-26', 2, 5, 20, 3, 8, 1, 12,
                       'test', 'v1', '2026-08-26T00:00:00Z',
                       'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')"""
        )
        conn.execute(
            """INSERT INTO identity.artist_provider_linkages
               (linkage_key, artist_key, provider, provider_id, link_method,
                resolution_status, first_seen_at, rights_status, commercial_use_status)
               VALUES
               ('lk1', 'mbid::a1', 'MUSICBRAINZ', 'a1', 'MBID_DERIVED', 'VERIFIED',
                '2026-08-20T00:00:00Z', 'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY'),
               ('lk2', 'mbid::a1', 'YOUTUBE', 'UCabc', 'LAKE_EXTERNAL_ID', 'VERIFIED',
                '2026-08-20T00:00:00Z', 'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')"""
        )

    def test_export_monitor_real_rows(self, conn):
        self._seed_full(conn)
        rows = export_monitor_rows(conn, artist_keys=["mbid::a1"])
        assert len(rows) == 1
        r = rows[0]
        assert r["artist"] == "mbid::a1"
        assert r["wiki_momentum"] == 0.35
        assert r["yt_momentum"] == 1200.0
        assert r["shows_365d"] == 20
        assert r["festival_appearances"] == 1
        assert r["data_confidence"] is not None

    def test_run_monitor_reports_semantics(self, conn):
        self._seed_full(conn)
        result = run_monitor(conn, artist_keys=["mbid::a1"])
        assert result["status"] == "COMPLETE"
        assert result["monitor_ready"] is True
        assert result["semantics"]["rows"] == 1
        assert "wiki_momentum" in result["semantics"]["columns_present"]


# ---------------------------------------------------------------------------
# P9: EVENT_TAPE_2000
# ---------------------------------------------------------------------------

class TestEventTape:
    def test_ingest_provider_estate(self, conn):
        _seed_tm_estate(conn, "evt1", "Alpha Artist", "Chicago", "IL", "2026-11-01")
        _seed_tm_estate(conn, "evt2", "Beta Band", "Las Vegas", "NV", "2026-12-01")
        result = ingest_provider_estate_events(conn)
        assert result["status"] == "COMPLETE"
        assert result["distinct_events"] == 2
        assert result["events_with_market"] == 2
        row = conn.execute(
            "SELECT event_key, market_key, marketplace_count FROM acquisition.event_tape_scale "
            "WHERE event_key='event::tm:evt1'"
        ).fetchone()
        assert row[1] == "chicago-il"
        assert row[2] == 1  # provider bootstrap, honest count

    def test_ingest_is_idempotent(self, conn):
        _seed_tm_estate(conn, "evt1", "Alpha Artist", "Chicago", "IL", "2026-11-01")
        r1 = ingest_provider_estate_events(conn)
        r2 = ingest_provider_estate_events(conn)
        assert r2["rows_written"] == 0

    def test_measure_tape(self, conn):
        m = measure_tape(conn)
        assert m["status"] == "COMPLETE"
        assert m["events_in_tape"] == 0
        _seed_tm_estate(conn, "evt1", "Alpha Artist", "Chicago", "IL", "2026-11-01")
        ingest_provider_estate_events(conn)
        m2 = measure_tape(conn)
        assert m2["events_in_tape"] == 1


# ---------------------------------------------------------------------------
# P10: Artist × market
# ---------------------------------------------------------------------------

class TestArtistMarket:
    def test_build_artist_market_rows(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        universe = select_security_universe(conn, limit=10)
        # historical show in chicago
        conn.execute(
            """INSERT INTO metrics.artist_performance_observations
               (performance_key, artist_key, show_date, venue_name, market_key, event_type,
                source_system, retrieved_at, rights_status, commercial_use_status)
               VALUES ('perf_ch', 'mbid::a1', '2026-01-15', 'United Center', 'chicago-il',
                       'CONCERT', 'setlistfm', '2026-08-20T00:00:00Z',
                       'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY'),
                      ('perf_ch2', 'mbid::a1', '2026-08-01', 'Soldier Field', 'chicago-il',
                       'CONCERT', 'setlistfm', '2026-08-20T00:00:00Z',
                       'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')"""
        )
        # upcoming chicago event in the estate
        _seed_tm_estate(conn, "evt_ch", "Alpha Artist", "Chicago", "IL", "2026-12-01", venue="United Center")
        result = build_artist_market_rows(conn, universe=universe)
        assert result["status"] == "COMPLETE"
        assert result["rows_written"] >= 1
        row = conn.execute(
            "SELECT market_key, historical_shows, days_since_last_market_show, "
            "market_venues_played, upcoming_market_events "
            "FROM asm.artist_market_security_v1 WHERE artist_key='mbid::a1'"
        ).fetchall()
        assert any(r[0] == "chicago-il" for r in row)
        chicago = [r for r in row if r[0] == "chicago-il"][0]
        assert chicago[1] == 2  # historical shows
        assert chicago[2] is not None  # days since last
        assert chicago[3] == 2  # venues played
        assert chicago[4] == 1  # upcoming


# ---------------------------------------------------------------------------
# P3: YouTube invalid key provisioning status
# ---------------------------------------------------------------------------

class TestYouTubeInvalidKey:
    def test_invalid_key_fails_closed_with_provisioning_status(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123")
        t = FakeTransport([
            (400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                             "errors": [{"message": "API key not valid", "reason": "badRequest"}]}}),
        ])
        s = collect_channel_snapshots(
            conn, t,
            artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1", "channel_id": "UCabc123"}],
            api_key="dead-key",
        )
        assert s["status"] == "NOT_CONFIGURED"
        assert s["key_provisioning_status"] == "INVALID_KEY"
        assert s["rows_persisted"] == 0
        assert "INVALID" in s["detail"]


# ---------------------------------------------------------------------------
# Success report
# ---------------------------------------------------------------------------

class TestSuccessReport:
    def test_build_success_report_real_counts(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc")
        _seed_lb_total(conn, "mbid::a1")
        _seed_wiki_daily(conn, "mbid::a1", days=40)
        conn.execute(
            """INSERT INTO metrics.artist_factor_observations
               (factor_observation_key, artist_key, factor_family, factor_name, value,
                value_unit, as_of, retrieved_at, source_system, source_version,
                rights_status, commercial_use_status)
               VALUES ('f1', 'mbid::a1', 'DEMAND', 'LB_TOTAL_LISTENS', 1000.0, 'listens',
                       '2026-08-20', '2026-08-20T00:00:00Z', 'test', 'v1',
                       'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')"""
        )
        universe = select_security_universe(conn, limit=10)
        run_identity_master(conn, universe_limit=10)
        report = build_success_report(conn, universe=universe)
        assert report["artist_security_1000"]["universe_size"] == 1
        assert report["factor_estate"]["wikimedia_daily_rows"] == 40
        assert report["factor_estate"]["listenbrainz_usable_artists"] == 1
        assert "MUSICBRAINZ" in report["identity_coverage_by_provider"]
        assert report["factor_estate"]["artists_with_5plus_factors"] >= 0
        assert report["pit_validation"]["verdict"] == "NOT_RUN"
