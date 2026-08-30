"""Regression coverage for ARTIST_SECURITY_MASTER_V1.

The artist as a tradable security: factor families (demand, momentum, live,
catalog, network) computed as EVIDENCE-BACKED observations — never an opaque
artist score. Covers:

- migration 043 schema (security master + factor tables)
- deterministic security universe selection (non-predictive)
- demand/momentum factor derivation from the attention tape
- live statistics from the event/performance graph
- catalog statistics from core.releases
- CO_BILLED peer edges (comparable universe)
- security snapshot display object
- PIT semantics: as_of != retrieved_at; UNKNOWN stays NULL (never zero)
"""

from __future__ import annotations

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.security.artist_security_master import (
    select_security_universe,
    derive_demand_and_momentum_factors,
    derive_live_statistics,
    derive_catalog_statistics,
    derive_peer_edges,
    build_security_snapshots,
    run_security_master,
    factor_observation_key,
)


@pytest.fixture()
def conn(tmp_path):
    # Avoid naming the DuckDB catalog exactly like the `security` schema;
    # newer DuckDB binders correctly require qualification when they collide.
    c = duckdb.connect(str(tmp_path / "artist_security_test.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


def _seed_artists(conn, rows):
    for r in rows:
        conn.execute(
            """INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name, sort_name)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT (artist_key) DO NOTHING""",
            [r["artist_key"], r.get("mbid"), r["name"], r["name"].lower(), r["name"]],
        )
        if r.get("mbid"):
            conn.execute(
                """INSERT OR IGNORE INTO core.entity_external_ids
                   (external_id_key, entity_type, entity_key, id_type, id_value, url,
                    is_primary, confidence, source_system, namespace, resolution_status)
                   VALUES (?, 'artist', ?, 'musicbrainz', ?, ?, FALSE, 1.0,
                           'musicbrainz', 'musicbrainz', 'CROWD_CURATED_REFERENCE')""",
                [f"eid_{r['artist_key']}", r["artist_key"], r["mbid"],
                 f"https://musicbrainz.org/artist/{r['mbid']}"],
            )


def _seed_attention(conn, artist_key, *, source, metric_kind, value, retrieved_at="2026-08-20T00:00:00Z",
                    granularity=None, period_start=None, period_end=None):
    conn.execute(
        """INSERT INTO metrics.artist_attention_observations
           (observation_key, artist_key, source_system, metric_kind, value, value_unit,
            status, source_url, retrieved_at, granularity, period_start, period_end, metric_version)
           VALUES (?, ?, ?, ?, ?, 'count', 'ok', 'https://src', ?, ?, ?, ?, 'v1')""",
        [f"obs_{artist_key}_{metric_kind}_{granularity or ''}", artist_key, source, metric_kind,
         value, retrieved_at, granularity, period_start, period_end],
    )


def _seed_event(conn, event_mbid, begin_date, event_type="Concert"):
    conn.execute(
        """INSERT OR IGNORE INTO raw.musicbrainz_event (mbid, name, event_type, begin_date, ingested_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        [event_mbid, f"event {event_mbid}", event_type, begin_date],
    )


def _seed_performer(conn, event_mbid, artist_mbid, artist_name, role="main performer"):
    conn.execute(
        """INSERT INTO core.event_performers
           (performer_key, event_mbid, artist_mbid, artist_name, performer_role, source_system, ingested_at)
           VALUES (?, ?, ?, ?, ?, 'musicbrainz', CURRENT_TIMESTAMP)
           ON CONFLICT (performer_key) DO NOTHING""",
        [f"perf_{event_mbid}_{artist_mbid}", event_mbid, artist_mbid, artist_name, role],
    )


def _seed_release(conn, artist_key, release_date):
    import json
    rg_key = f"rg_{artist_key}"
    conn.execute(
        """INSERT INTO core.release_groups
           (release_group_key, musicbrainz_id, name, primary_type, artist_keys, source_system, ingested_at)
           VALUES (?, ?, 'release group', 'Album', ?, 'musicbrainz', CURRENT_TIMESTAMP)
           ON CONFLICT (release_group_key) DO NOTHING""",
        [rg_key, f"mb_rg_{artist_key}", json.dumps({"artist_keys": [artist_key]})],
    )
    conn.execute(
        """INSERT INTO core.releases
           (release_key, musicbrainz_id, release_group_key, name, release_date, source_system, ingested_at)
           VALUES (?, ?, ?, 'release', ?, 'musicbrainz', CURRENT_TIMESTAMP)
           ON CONFLICT (release_key) DO NOTHING""",
        [f"rel_{artist_key}_{release_date}", f"mb_rel_{artist_key}_{release_date}", rg_key, release_date],
    )


class TestSchema:
    def test_migration_creates_security_master_tables(self, conn):
        tables = {
            (row[0], row[1])
            for row in conn.execute(
                """SELECT table_schema, table_name FROM information_schema.tables
                   WHERE table_name IN (
                     'artist_security_master', 'artist_factor_observations',
                     'artist_market_factor_observations', 'artist_peer_edges',
                     'artist_collaboration_edges', 'artist_live_statistics',
                     'artist_catalog_statistics', 'artist_security_snapshots')"""
            ).fetchall()
        }
        assert ("asm", "artist_security_master") in tables
        assert ("metrics", "artist_factor_observations") in tables
        assert ("metrics", "artist_market_factor_observations") in tables
        assert ("core", "artist_peer_edges") in tables
        assert ("core", "artist_collaboration_edges") in tables
        assert ("metrics", "artist_live_statistics") in tables
        assert ("metrics", "artist_catalog_statistics") in tables
        assert ("metrics", "artist_security_snapshots") in tables

    def test_factor_observation_requires_rights_and_pit_fields(self, conn):
        cols = {
            row[0] for row in conn.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema='metrics' AND table_name='artist_factor_observations'"""
            ).fetchall()
        }
        for required in (
            "factor_observation_key", "artist_key", "factor_family", "factor_name",
            "value", "value_unit", "as_of", "retrieved_at",
            "rights_status", "commercial_use_status",
        ):
            assert required in cols


class TestUniverseSelection:
    def test_selects_decision_relevant_artists_deterministically(self, conn):
        _seed_artists(conn, [
            {"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha Artist"},
            {"artist_key": "mbid::b2", "mbid": "b2", "name": "Beta Band"},
            {"artist_key": "mbid::c3", "mbid": "c3", "name": "Gamma Trio"},
        ])
        _seed_attention(conn, "mbid::a1", source="listenbrainz", metric_kind="LISTENBRAINZ_TOTAL_LISTEN_COUNT", value=1000)
        _seed_attention(conn, "mbid::b2", source="listenbrainz", metric_kind="LISTENBRAINZ_TOTAL_LISTEN_COUNT", value=500)
        _seed_event(conn, "evt1", "2026-06-01")
        _seed_performer(conn, "evt1", "b2", "Beta Band")

        u1 = select_security_universe(conn, limit=10)
        u2 = select_security_universe(conn, limit=10)
        assert [a["artist_key"] for a in u1] == [a["artist_key"] for a in u2]  # deterministic
        # Beta has event performance (ticket-market presence) → ranks first.
        assert u1[0]["artist_key"] == "mbid::b2"
        reasons = {a["selection_reason"] for a in u1}
        assert reasons == {"security_universe_v1_non_predictive"}


class TestDemandMomentumFactors:
    def test_derives_demand_level_factors_from_listenbrainz(self, conn):
        _seed_artists(conn, [{"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"}])
        _seed_attention(conn, "mbid::a1", source="listenbrainz", metric_kind="LISTENBRAINZ_TOTAL_LISTEN_COUNT", value=5000)
        _seed_attention(conn, "mbid::a1", source="listenbrainz", metric_kind="LISTENBRAINZ_TOTAL_USER_COUNT", value=900)
        rows, summary = derive_demand_and_momentum_factors(
            conn, universe=select_security_universe(conn, limit=10),
        )
        names = {r["factor_name"] for r in rows}
        assert "LB_TOTAL_LISTENS" in names
        assert "LB_TOTAL_LISTENERS" in names
        assert summary["demand_rows"] >= 2

    def test_momentum_velocity_from_week_and_month_ranges(self, conn):
        _seed_artists(conn, [{"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"}])
        _seed_attention(conn, "mbid::a1", source="listenbrainz", metric_kind="LISTENBRAINZ_LISTEN_COUNT",
                        value=100, granularity="week")
        _seed_attention(conn, "mbid::a1", source="listenbrainz", metric_kind="LISTENBRAINZ_LISTEN_COUNT",
                        value=400, granularity="month")
        rows, _ = derive_demand_and_momentum_factors(conn, universe=select_security_universe(conn, limit=10))
        velocity = [r for r in rows if r["factor_name"] == "LB_LISTEN_VELOCITY"]
        assert velocity and velocity[0]["value"] == pytest.approx(0.25)
        assert velocity[0]["factor_family"] == "MOMENTUM"

    def test_unknown_stays_null_never_zero(self, conn):
        _seed_artists(conn, [{"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"}])
        # No attention rows at all → no DEMAND factors for this artist.
        rows, _ = derive_demand_and_momentum_factors(conn, universe=select_security_universe(conn, limit=10))
        assert all(r["artist_key"] != "mbid::a1" or r["value"] is not None for r in rows)
        # An artist with zero attention produces no fabricated demand row.
        assert not any(r["factor_name"] == "LB_TOTAL_LISTENS" and r["value"] == 0 for r in rows)


class TestLiveStatistics:
    def test_show_counts_and_days_since_last_show(self, conn):
        _seed_artists(conn, [{"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"}])
        _seed_event(conn, "evt1", "2026-08-01")
        _seed_event(conn, "evt2", "2026-06-01")
        _seed_event(conn, "evt3", "2025-12-01")
        for evt in ("evt1", "evt2", "evt3"):
            _seed_performer(conn, evt, "a1", "Alpha")
        rows, summary = derive_live_statistics(
            conn, universe=select_security_universe(conn, limit=10), as_of=__import__("datetime").date(2026, 8, 26),
        )
        assert summary["rows_written"] == 1
        live = rows[0]
        assert live["shows_30d"] == 1
        assert live["shows_90d"] == 2
        assert live["shows_365d"] == 3
        assert live["days_since_last_show"] == 25

    def test_festival_appearances_are_separate(self, conn):
        _seed_artists(conn, [{"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"}])
        _seed_event(conn, "fest1", "2026-07-10", event_type="Festival")
        _seed_performer(conn, "fest1", "a1", "Alpha")
        rows, _ = derive_live_statistics(conn, universe=select_security_universe(conn, limit=10),
                                         as_of=__import__("datetime").date(2026, 8, 26))
        assert rows[0]["festival_appearances_365d"] == 1


class TestCatalogStatistics:
    def test_release_recency_and_depth(self, conn):
        _seed_artists(conn, [{"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"}])
        _seed_release(conn, "mbid::a1", "2026-02-01")
        _seed_release(conn, "mbid::a1", "2025-02-01")
        rows, summary = derive_catalog_statistics(
            conn, universe=select_security_universe(conn, limit=10), as_of=__import__("datetime").date(2026, 8, 26),
        )
        assert summary["rows_written"] == 1
        cat = rows[0]
        assert cat["releases_12m"] == 1
        assert cat["catalog_depth"] == 2
        assert cat["days_since_last_release"] == pytest.approx(207, abs=2)


class TestPeerEdges:
    def test_co_billed_peers_from_shared_events(self, conn):
        _seed_artists(conn, [
            {"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"},
            {"artist_key": "mbid::b2", "mbid": "b2", "name": "Beta"},
        ])
        _seed_event(conn, "evt1", "2026-06-01")
        _seed_event(conn, "evt2", "2026-07-01")
        for evt in ("evt1", "evt2"):
            _seed_performer(conn, evt, "a1", "Alpha")
            _seed_performer(conn, evt, "b2", "Beta")
        rows, summary = derive_peer_edges(conn, universe=select_security_universe(conn, limit=10))
        assert summary["rows_written"] == 1  # one undirected edge
        assert rows[0]["edge_type"] == "CO_BILLED"
        assert rows[0]["strength"] == 2


class TestSnapshots:
    def test_snapshot_builds_factor_summary_display_object(self, conn):
        _seed_artists(conn, [{"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"}])
        _seed_attention(conn, "mbid::a1", source="listenbrainz", metric_kind="LISTENBRAINZ_TOTAL_LISTEN_COUNT", value=5000)
        factors, _ = derive_demand_and_momentum_factors(conn, universe=select_security_universe(conn, limit=10))
        for f in factors:
            conn.execute(
                """INSERT INTO metrics.artist_factor_observations
                   (factor_observation_key, artist_key, factor_family, factor_name, value,
                    value_unit, as_of, retrieved_at, source_system, source_version,
                    rights_status, commercial_use_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')
                   ON CONFLICT (factor_observation_key) DO NOTHING""",
                [f["factor_observation_key"], f["artist_key"], f["factor_family"], f["factor_name"],
                 f["value"], f["value_unit"], f["as_of"], f["retrieved_at"], f["source_system"],
                 f["source_version"]],
            )
        rows, summary = build_security_snapshots(conn, universe=select_security_universe(conn, limit=10))
        assert summary["rows_written"] == 1
        import json
        family_map = json.loads(rows[0]["factor_summary"])
        assert "DEMAND" in family_map
        assert family_map["DEMAND"][0]["factor_name"] == "LB_TOTAL_LISTENS"


class TestEndToEnd:
    def test_run_security_master_full_pass(self, conn):
        _seed_artists(conn, [
            {"artist_key": "mbid::a1", "mbid": "a1", "name": "Alpha"},
            {"artist_key": "mbid::b2", "mbid": "b2", "name": "Beta"},
        ])
        _seed_attention(conn, "mbid::a1", source="listenbrainz", metric_kind="LISTENBRAINZ_TOTAL_LISTEN_COUNT", value=8000)
        _seed_attention(conn, "mbid::b2", source="listenbrainz", metric_kind="LISTENBRAINZ_TOTAL_LISTEN_COUNT", value=3000)
        _seed_event(conn, "evt1", "2026-06-01")
        _seed_performer(conn, "evt1", "a1", "Alpha")
        _seed_performer(conn, "evt1", "b2", "Beta")
        _seed_release(conn, "mbid::a1", "2026-01-15")

        result = run_security_master(conn, universe_limit=10)
        assert result["status"] == "COMPLETE"
        assert result["universe_size"] == 2
        assert result["factor_observations"]["written"] >= 1
        assert result["live_statistics"]["written"] >= 1
        assert result["catalog_statistics"]["written"] >= 1
        assert result["peer_edges"]["written"] == 1
        assert result["security_snapshots"]["written"] == 2

        # Re-running is idempotent (no duplicate rows).
        result2 = run_security_master(conn, universe_limit=10)
        assert result2["factor_observations"]["written"] == 0
        assert result2["security_snapshots"]["written"] == 0

        master_rows = conn.execute("SELECT COUNT(*) FROM asm.artist_security_master").fetchone()[0]
        assert master_rows == 2

    def test_factor_observation_key_is_deterministic(self):
        k1 = factor_observation_key(artist_key="mbid::a1", factor_name="LB_TOTAL_LISTENS",
                                    as_of="2026-08-26", source_system="listenbrainz")
        k2 = factor_observation_key(artist_key="mbid::a1", factor_name="LB_TOTAL_LISTENS",
                                    as_of="2026-08-26", source_system="listenbrainz")
        k3 = factor_observation_key(artist_key="mbid::a1", factor_name="LB_TOTAL_LISTENS",
                                    as_of="2026-08-27", source_system="listenbrainz")
        assert k1 == k2
        assert k1 != k3
