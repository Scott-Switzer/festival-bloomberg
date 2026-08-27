"""Regression coverage for OPEN_ARTIST_MARKET_DATA_V1 collectors.

Covers:
- Wikimedia historical DAILY pageviews backfill (per-day rows, PIT fields)
- YouTube forward tape (channel snapshots, no-key fails closed)
- Spotify conservative catalog (identity only, API mode recorded)
- ListenBrainz bulk orchestration (totals + range history)
- security.populate: universe → collectors → materialization → coverage
- WIKI window/zscore/shock factor derivation + YT snapshot factors

All offline via scripted FakeTransport.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations

from tests.python.conftest import FakeTransport

from festival_bloomberg.attention.wikimedia_historical import (
    collect_artist_daily_pageviews,
    collect_artist_daily_pageviews_bounded,
    daily_observation_key,
    split_windows,
)
from festival_bloomberg.attention.youtube_forward import (
    collect_channel_snapshots,
    resolve_channel_id,
)
from festival_bloomberg.attention.spotify_catalog import (
    collect_artist_catalog,
    infer_api_mode,
)
from festival_bloomberg.attention.listenbrainz_bulk import (
    collect_security_universe_listenbrainz,
)
from festival_bloomberg.security.artist_security_master import (
    derive_youtube_snapshot_factors,
    run_security_master,
    select_security_universe,
)
from festival_bloomberg.security.populate import (
    compute_coverage,
    load_identity_map,
    resolve_identity,
    run_population,
)


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "market.duckdb"))
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


def _seed_lb_total(conn, artist_key, listens=1000, listeners=100):
    for metric_kind, value in (
        ("LISTENBRAINZ_TOTAL_LISTEN_COUNT", listens),
        ("LISTENBRAINZ_TOTAL_USER_COUNT", listeners),
    ):
        conn.execute(
            """INSERT INTO metrics.artist_attention_observations
               (observation_key, artist_key, source_system, metric_kind, value,
                value_unit, status, source_url, retrieved_at, metric_version)
               VALUES (?, ?, 'listenbrainz', ?, ?, 'count', 'ok', 'https://src',
                       '2026-08-20T00:00:00Z', 'v1')""",
            [f"obs_{artist_key}_{metric_kind}", artist_key, metric_kind, value],
        )


def _seed_event(conn, event_mbid, begin_date):
    conn.execute(
        """INSERT OR IGNORE INTO raw.musicbrainz_event (mbid, name, event_type, begin_date, ingested_at)
           VALUES (?, ?, 'Concert', ?, CURRENT_TIMESTAMP)""",
        [event_mbid, f"event {event_mbid}", begin_date],
    )
    conn.execute(
        """INSERT INTO core.event_performers
           (performer_key, event_mbid, artist_mbid, artist_name, performer_role, source_system, ingested_at)
           VALUES (?, ?, ?, ?, 'main performer', 'musicbrainz', CURRENT_TIMESTAMP)
           ON CONFLICT (performer_key) DO NOTHING""",
        [f"perf_{event_mbid}_{event_mbid}", event_mbid, event_mbid, "artist"],
    )


def _wiki_item(day: date, views: int) -> dict:
    return {
        "project": "en.wikipedia",
        "article": "Artist",
        "granularity": "daily",
        "timestamp": day.strftime("%Y%m%d") + "00",
        "access": "all-access",
        "agent": "user",
        "views": views,
    }


# ---------------------------------------------------------------------------
# Wikimedia historical daily backfill
# ---------------------------------------------------------------------------

class TestWikimediaHistorical:
    def test_split_windows_covers_range(self):
        start = date(2026, 1, 1)
        end = date(2026, 1, 3)
        windows = split_windows(start, end, chunk_days=2)
        assert windows == [(date(2026, 1, 1), date(2026, 1, 2)), (date(2026, 1, 3), date(2026, 1, 3))]

    def test_daily_rows_are_per_day_with_pit_fields(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        day1, day2 = date(2026, 8, 1), date(2026, 8, 2)
        transport = FakeTransport([
            (200, {"items": [_wiki_item(day1, 100), _wiki_item(day2, 150)]}),
        ])
        summary = collect_artist_daily_pageviews(
            conn, transport, names=["Alpha Artist"],
            start="2026-08-01", end="2026-08-02", min_interval_seconds=0,
        )
        assert summary["status"] == "COMPLETE"
        assert summary["names_ok"] == 1
        assert summary["daily_rows_persisted"] == 2
        rows = conn.execute(
            "SELECT period_start, period_end, value, metric_version, status "
            "FROM metrics.artist_attention_observations WHERE source_system='wikimedia' ORDER BY period_start"
        ).fetchall()
        assert [(r[0].isoformat(), r[1].isoformat(), r[2], r[4]) for r in rows] == [
            ("2026-08-01", "2026-08-01", 100, "ok"),
            ("2026-08-02", "2026-08-02", 150, "ok"),
        ]
        assert all(r[3] == "wikimedia_pageviews_daily_v1" for r in rows)

    def test_daily_rows_are_idempotent(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        day = date(2026, 8, 1)
        transport = FakeTransport([
            (200, {"items": [_wiki_item(day, 100)]}),
            (200, {"items": [_wiki_item(day, 100)]}),
        ])
        s1 = collect_artist_daily_pageviews(conn, transport, names=["Alpha Artist"],
                                            start="2026-08-01", end="2026-08-01", min_interval_seconds=0)
        s2 = collect_artist_daily_pageviews(conn, transport, names=["Alpha Artist"],
                                            start="2026-08-01", end="2026-08-01", min_interval_seconds=0)
        assert s1["daily_rows_persisted"] == 1
        assert s2["daily_rows_persisted"] == 0  # idempotent
        assert conn.execute(
            "SELECT COUNT(*) FROM metrics.artist_attention_observations WHERE source_system='wikimedia'"
        ).fetchone()[0] == 1

    def test_pre_series_days_are_never_written(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        pre = date(2015, 6, 30)  # before series start
        day = date(2026, 8, 1)
        transport = FakeTransport([(200, {"items": [_wiki_item(pre, 999), _wiki_item(day, 50)]})])
        summary = collect_artist_daily_pageviews(conn, transport, names=["Alpha Artist"],
                                                 start="2026-08-01", end="2026-08-01", min_interval_seconds=0)
        # the pre-series item is returned but must be filtered out
        assert summary["daily_rows_persisted"] == 1
        values = [r[0] for r in conn.execute(
            "SELECT value FROM metrics.artist_attention_observations WHERE source_system='wikimedia'"
        ).fetchall()]
        assert values == [50]

    def test_bounded_backfill_window(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        today = date.today()
        transport = FakeTransport([(200, {"items": [_wiki_item(today - timedelta(days=5), 10)]})])
        summary = collect_artist_daily_pageviews_bounded(
            conn, transport, names=["Alpha Artist"], lookback_days=30, min_interval_seconds=0,
        )
        assert summary["daily_rows_persisted"] == 1

    def test_daily_observation_key_stable(self):
        k1 = daily_observation_key(artist_key="mbid::a1", day="2026-08-01")
        k2 = daily_observation_key(artist_key="mbid::a1", day="2026-08-01")
        k3 = daily_observation_key(artist_key="mbid::a1", day="2026-08-02")
        assert k1 == k2 and k1 != k3


# ---------------------------------------------------------------------------
# YouTube forward tape
# ---------------------------------------------------------------------------

class TestYouTubeForward:
    def test_resolve_channel_id_variants(self):
        assert resolve_channel_id("UCabc123") == "UCabc123"
        assert resolve_channel_id("https://www.youtube.com/channel/UCabc123") == "UCabc123"
        assert resolve_channel_id("https://www.youtube.com/user/danielmyer") == "danielmyer"
        assert resolve_channel_id("channel/UCabc123") == "UCabc123"
        assert resolve_channel_id(None) is None

    def test_no_key_fails_closed(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        summary = collect_channel_snapshots(
            conn, FakeTransport(), artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1"}],
            api_key=None,
        )
        assert summary["status"] == "NOT_CONFIGURED"
        assert summary["rows_persisted"] == 0

    def test_snapshot_persists_metrics(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123")
        transport = FakeTransport([
            # channels.list
            (200, {"items": [{"statistics": {"subscriberCount": "1200", "viewCount": "50000", "videoCount": "30"}}]}),
            # search.list recent video
            (200, {"items": [{"id": {"videoId": "vid1"}}]}),
            # videos.list
            (200, {"items": [{"statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "2"}}]}),
        ])
        summary = collect_channel_snapshots(
            conn, transport,
            artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1", "channel_id": "UCabc123"}],
            api_key="test-key", snapshot_date="2026-08-26",
        )
        assert summary["status"] == "COMPLETE"
        assert summary["artists_resolved"] == 1
        assert summary["rows_persisted"] == 6  # 3 channel + 3 video metrics
        kinds = {r[0] for r in conn.execute(
            "SELECT metric_kind FROM metrics.artist_attention_observations WHERE source_system='youtube'"
        ).fetchall()}
        assert kinds == {"YT_SUBSCRIBERS", "YT_CHANNEL_VIEWS", "YT_VIDEO_COUNT",
                         "YT_RECENT_VIDEO_VIEWS", "YT_RECENT_VIDEO_LIKES", "YT_RECENT_VIDEO_COMMENTS"}

    def test_quota_exceeded_stops_batch(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123")
        transport = FakeTransport([
            (403, {"error": {"errors": [{"reason": "quotaExceeded"}]}}),
        ])
        summary = collect_channel_snapshots(
            conn, transport,
            artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1", "channel_id": "UCabc123"}],
            api_key="test-key",
        )
        assert summary["status"] == "RATE_LIMITED_STOPPED"

    def test_yt_factor_derivation_uses_latest_snapshot(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123")
        # two days of snapshots — factor derivation must expose the latest
        for day in ("2026-08-25", "2026-08-26"):
            transport = FakeTransport([
                (200, {"items": [{"statistics": {"subscriberCount": "1200", "viewCount": "50000", "videoCount": "30"}}]}),
            ])
            collect_channel_snapshots(
                conn, transport,
                artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1", "channel_id": "UCabc123"}],
                api_key="test-key", snapshot_date=day, include_recent_video=False,
            )
        rows, summary = derive_youtube_snapshot_factors(
            conn, universe=select_security_universe(conn, limit=10),
        )
        yt = [r for r in rows if r["factor_name"] == "YT_SUBSCRIBERS"]
        assert len(yt) == 1  # one factor row, latest snapshot only
        assert yt[0]["value"] == 1200.0


# ---------------------------------------------------------------------------
# Spotify conservative catalog
# ---------------------------------------------------------------------------

class TestSpotifyCatalog:
    def test_no_credentials_fails_closed(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", spotify_id="sp1")
        summary = collect_artist_catalog(
            conn, FakeTransport(),
            artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1"}],
            spotify_id_by_key={"mbid::a1": "sp1"},
            client_id=None, client_secret=None,
        )
        assert summary["status"] == "NOT_CONFIGURED"
        assert summary["rows_persisted"] == 0

    def test_catalog_identity_persisted_with_api_mode(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", spotify_id="sp1")
        transport = FakeTransport([
            (200, {"access_token": "tok123", "expires_in": 3600}),
            (200, {"id": "sp1", "name": "Alpha Artist", "uri": "spotify:artist:sp1",
                   "external_urls": {"spotify": "https://open.spotify.com/artist/sp1"}}),
        ])
        summary = collect_artist_catalog(
            conn, transport,
            artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1"}],
            spotify_id_by_key={"mbid::a1": "sp1"},
            client_id="cid", client_secret="csec",
        )
        assert summary["status"] == "COMPLETE"
        assert summary["artists_resolved"] == 1
        row = conn.execute(
            "SELECT metric_kind, status, provenance_json FROM metrics.artist_attention_observations "
            "WHERE source_system='spotify'"
        ).fetchone()
        assert row[0] == "SPOTIFY_CATALOG_IDENTITY"
        assert row[1] == "ok"
        prov = json.loads(row[2])
        assert prov["api_mode"] == "UNKNOWN"
        assert "id" in prov["fields_present"]
        assert "popularity" not in prov["fields_present"]

    def test_infer_api_mode(self):
        assert infer_api_mode(None, 401) == "DEVELOPMENT_OR_SCOPE_LIMITED"
        assert infer_api_mode({"error": {"message": "extended quota required"}}, 403) == "EXTENDED_QUOTA"
        assert infer_api_mode({"error": {"message": "development mode restriction"}}, 403) == "DEVELOPMENT"
        assert infer_api_mode({"id": "x"}, 200) == "UNKNOWN"


# ---------------------------------------------------------------------------
# ListenBrainz bulk orchestration
# ---------------------------------------------------------------------------

class TestListenBrainzBulk:
    def test_bulk_popularity_and_ranges(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        _seed_artist(conn, "mbid::b2", "Beta Band", "b2")
        _seed_lb_total(conn, "mbid::a1")
        def _lb_payload(name, listens, rng, frm, to):
            return (200, {"payload": {"artist_name": name, "total_listen_count": listens, "listeners": [],
                       "range": rng, "from_ts": frm, "to_ts": to, "last_updated": to}})

        transport = FakeTransport([
            # collect_artist_popularity: one POST returning a JSON LIST of rows
            (200, [
                {"artist_mbid": "a1", "total_listen_count": 1000, "total_user_count": 100},
                {"artist_mbid": "b2", "total_listen_count": 500, "total_user_count": 50},
            ]),
            # collect_priority_range_history: week + month + all_time for a1
            _lb_payload("Alpha Artist", 100, "week", 1754000000, 1754600000),
            _lb_payload("Alpha Artist", 400, "month", 1752000000, 1754600000),
            _lb_payload("Alpha Artist", 900, "all_time", 1600000000, 1754600000),
            # week + month + all_time for b2
            _lb_payload("Beta Band", 50, "week", 1754000000, 1754600000),
            _lb_payload("Beta Band", 200, "month", 1752000000, 1754600000),
            _lb_payload("Beta Band", 450, "all_time", 1600000000, 1754600000),
        ])
        universe = select_security_universe(conn, limit=10)
        summary = collect_security_universe_listenbrainz(
            conn, transport, universe=universe, min_interval_seconds=0,
        )
        assert summary["status"] == "COMPLETE"
        assert summary["bulk_popularity"]["artists_returned"] == 2
        assert summary["rows_persisted_total"] > 0


# ---------------------------------------------------------------------------
# security.populate orchestrator
# ---------------------------------------------------------------------------

class TestPopulateOrchestrator:
    def _seed_universe(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123", spotify_id="sp1")
        _seed_artist(conn, "mbid::b2", "Beta Band", "b2")
        _seed_lb_total(conn, "mbid::a1")
        _seed_lb_total(conn, "mbid::b2")
        _seed_event(conn, "evt1", "2026-06-01")

    def test_load_identity_map(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123", spotify_id="sp1")
        m = load_identity_map(conn)
        assert m["mbid::a1"]["channel_id"] == "UCabc123"
        assert m["mbid::a1"]["spotify_id"] == "sp1"

    def test_resolve_identity(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123")
        universe = select_security_universe(conn, limit=10)
        identity_map = load_identity_map(conn)
        artists, channel_by_key, spotify_by_key = resolve_identity(universe, identity_map)
        assert channel_by_key["mbid::a1"] == "UCabc123"
        assert "mbid::b2" not in channel_by_key

    def test_run_population_full_pass(self, conn):
        self._seed_universe(conn)
        today = date.today()
        wiki_day = today - timedelta(days=2)
        def _lb_payload(name, listens, rng, frm, to):
            return (200, {"payload": {"artist_name": name, "total_listen_count": listens, "listeners": [],
                       "range": rng, "from_ts": frm, "to_ts": to, "last_updated": to}})

        transport = FakeTransport([
            # LB bulk popularity POST (JSON list)
            (200, [
                {"artist_mbid": "a1", "total_listen_count": 1000, "total_user_count": 100},
                {"artist_mbid": "b2", "total_listen_count": 500, "total_user_count": 50},
            ]),
            # LB range history: a1
            _lb_payload("Alpha Artist", 100, "week", 1754000000, 1754600000),
            _lb_payload("Alpha Artist", 400, "month", 1752000000, 1754600000),
            _lb_payload("Alpha Artist", 900, "all_time", 1600000000, 1754600000),
            # LB range history: b2
            _lb_payload("Beta Band", 50, "week", 1754000000, 1754600000),
            _lb_payload("Beta Band", 200, "month", 1752000000, 1754600000),
            _lb_payload("Beta Band", 450, "all_time", 1600000000, 1754600000),
            # wiki: a1 full history
            (200, {"items": [_wiki_item(wiki_day, 100)]}),
            # wiki: b2 full history
            (200, {"items": [_wiki_item(wiki_day, 50)]}),
        ])
        report = run_population(
            conn, transport, universe_limit=10,
            wiki_start=(today - timedelta(days=5)).isoformat(),
            youtube_api_key=None,  # fail closed
            spotify_client_id=None, spotify_client_secret=None,  # fail closed
            min_interval_seconds=0,
        )
        assert report["status"] == "COMPLETE"
        assert report["universe_size"] == 2
        assert report["collectors"]["listenbrainz"]["status"] == "COMPLETE"
        assert report["collectors"]["wikimedia"]["status"] == "COMPLETE"
        assert report["collectors"]["youtube"]["status"] == "NOT_CONFIGURED"
        assert report["collectors"]["spotify"]["status"] == "NOT_CONFIGURED"
        assert report["materialization"]["status"] == "COMPLETE"
        coverage = report["coverage"]
        assert coverage["universe_size"] == 2
        assert coverage["musicbrainz_backed_pct"] == 100.0
        assert coverage["wikimedia_usable_artists"] == 2

    def test_compute_coverage_counts(self, conn):
        self._seed_universe(conn)
        coverage = compute_coverage(conn, universe_limit=10)
        assert coverage["status"] == "OK"
        assert coverage["listenbrainz_usable_artists"] == 2
        assert coverage["wikimedia_usable_artists"] == 0


# ---------------------------------------------------------------------------
# WIKI window factor derivation
# ---------------------------------------------------------------------------

class TestWikiFactorDerivation:
    def test_wiki_windows_zscore_shock(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        # seed 40 days of daily pageviews: flat 100/day, then a spike at the end
        today = date.today()
        day = today - timedelta(days=40)
        while day < today:
            views = 1000 if day == today - timedelta(days=1) else 100
            conn.execute(
                """INSERT INTO metrics.artist_attention_observations
                   (observation_key, artist_key, source_system, metric_kind, value,
                    value_unit, status, source_url, retrieved_at, period_start, period_end,
                    metric_version)
                   VALUES (?, 'mbid::a1', 'wikimedia', 'pageviews', ?, 'pageviews', 'ok',
                           'https://src', '2026-08-20T00:00:00Z', ?, ?, 'wikimedia_pageviews_daily_v1')""",
                [f"wiki_{day.isoformat()}", views, day.isoformat(), day.isoformat()],
            )
            day += timedelta(days=1)
        universe = select_security_universe(conn, limit=10)
        from festival_bloomberg.security.artist_security_master import derive_demand_and_momentum_factors

        rows, summary = derive_demand_and_momentum_factors(conn, universe=universe, as_of=today)
        names = {r["factor_name"]: r for r in rows}
        assert "WIKI_VIEWS_1D" in names and names["WIKI_VIEWS_1D"]["value"] == 1000.0
        assert "WIKI_VIEWS_7D" in names
        assert "WIKI_VIEWS_28D" in names
        assert "WIKI_VIEWS_90D" in names
        assert "WIKI_ZSCORE" in names and names["WIKI_ZSCORE"]["value"] > 3.0  # spike vs flat
        assert "WIKI_ATTENTION_SHOCK" in names and names["WIKI_ATTENTION_SHOCK"]["value"] > 5.0

    def test_no_wiki_data_no_fabricated_rows(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1")
        from festival_bloomberg.security.artist_security_master import derive_demand_and_momentum_factors

        rows, _ = derive_demand_and_momentum_factors(
            conn, universe=select_security_universe(conn, limit=10),
        )
        assert not any(r["factor_name"].startswith("WIKI_") for r in rows)


class TestSecurityMasterWithYouTube:
    def test_run_security_master_includes_youtube_factors(self, conn):
        _seed_artist(conn, "mbid::a1", "Alpha Artist", "a1", channel_id="UCabc123")
        _seed_lb_total(conn, "mbid::a1")
        transport = FakeTransport([
            (200, {"items": [{"statistics": {"subscriberCount": "1200", "viewCount": "50000", "videoCount": "30"}}]}),
        ])
        collect_channel_snapshots(
            conn, transport,
            artists=[{"artist_name": "Alpha Artist", "artist_key": "mbid::a1", "channel_id": "UCabc123"}],
            api_key="test-key", include_recent_video=False,
        )
        result = run_security_master(conn, universe_limit=10)
        assert result["status"] == "COMPLETE"
        yt_factors = conn.execute(
            "SELECT factor_name FROM metrics.artist_factor_observations WHERE artist_key='mbid::a1' AND factor_name LIKE 'YT_%'"
        ).fetchall()
        assert {"YT_SUBSCRIBERS"} <= {r[0] for r in yt_factors}
