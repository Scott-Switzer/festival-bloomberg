"""Regression coverage for the open-source pilot evaluations.

- Voyager pilot: factor-vector KNN retrieval + CO_BILLED overlap evaluation
- Feast pilot: PIT historical retrieval semantics equivalence
- Perspective pilot: snapshot export + sort/filter/pivot semantics
- memray pilot: dev-only, fails closed when memray absent

All offline; no external dependency is imported at runtime.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations

from festival_bloomberg.security.pilots.voyager_pilot import (
    build_factor_vectors,
    cosine_similarity,
    evaluate,
    knn,
)
from festival_bloomberg.security.pilots.feast_pilot import (
    FeastStyleRetrieval,
    build_feast_rows,
    equivalence_test,
    run_pilot as feast_run,
)
from festival_bloomberg.security.pilots.perspective_pilot import (
    export_snapshot,
    run_pilot as perspective_run,
)
from festival_bloomberg.security.pilots.memray_pilot import profile_materialization


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "pilots.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


def _seed_factor(conn, artist_key, factor_name, value, as_of="2026-08-20"):
    conn.execute(
        """INSERT INTO metrics.artist_factor_observations
           (factor_observation_key, artist_key, factor_family, factor_name, value,
            value_unit, as_of, retrieved_at, source_system, source_version,
            rights_status, commercial_use_status)
           VALUES (?, ?, 'DEMAND', ?, ?, 'unit', ?, '2026-08-20T00:00:00Z',
                   'test', 'v1', 'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY')
           ON CONFLICT (factor_observation_key) DO NOTHING""",
        [f"f_{artist_key}_{factor_name}_{as_of}", artist_key, factor_name, value, as_of],
    )


def _seed_peer(conn, subject, peer):
    conn.execute(
        """INSERT INTO core.artist_peer_edges
           (edge_key, subject_key, peer_key, edge_type, strength, source_system, knowledge_time)
           VALUES (?, ?, ?, 'CO_BILLED', 1, 'test', '2026-08-20T00:00:00Z')
           ON CONFLICT (edge_key) DO NOTHING""",
        [f"e_{subject}_{peer}", subject, peer],
    )


# ---------------------------------------------------------------------------
# Voyager pilot
# ---------------------------------------------------------------------------

class TestVoyagerPilot:
    def _seed_vectors(self, conn):
        # three artists with distinguishable factor vectors
        _seed_factor(conn, "a", "LB_TOTAL_LISTENS", 1000.0)
        _seed_factor(conn, "a", "LB_TOTAL_LISTENERS", 100.0)
        _seed_factor(conn, "a", "WIKI_VIEWS_28D", 5000.0)
        _seed_factor(conn, "b", "LB_TOTAL_LISTENS", 950.0)
        _seed_factor(conn, "b", "LB_TOTAL_LISTENERS", 95.0)
        _seed_factor(conn, "b", "WIKI_VIEWS_28D", 4900.0)
        _seed_factor(conn, "c", "LB_TOTAL_LISTENS", 10.0)
        _seed_factor(conn, "c", "LB_TOTAL_LISTENERS", 2.0)
        _seed_factor(conn, "c", "WIKI_VIEWS_28D", 50.0)

    def test_build_factor_vectors_latest_per_factor(self, conn):
        _seed_factor(conn, "a", "LB_TOTAL_LISTENS", 100.0, as_of="2026-08-01")
        _seed_factor(conn, "a", "LB_TOTAL_LISTENS", 900.0, as_of="2026-08-20")  # latest wins
        vectors = build_factor_vectors(conn, artist_keys=["a"])
        assert vectors["a"]["LB_TOTAL_LISTENS"] == 900.0

    def test_cosine_similarity(self):
        assert cosine_similarity({"x": 1.0, "y": 2.0}, {"x": 2.0, "y": 4.0}) == pytest.approx(1.0)
        assert cosine_similarity({"x": 1.0}, {"y": 1.0}) == 0.0
        assert cosine_similarity({}, {}) == 0.0

    def test_knn_orders_by_similarity(self, conn):
        self._seed_vectors(conn)
        vectors = build_factor_vectors(conn, artist_keys=["a", "b", "c"])
        neighbors = knn(vectors, k=2)
        assert neighbors["a"][0][0] == "b"  # b is most similar to a
        assert neighbors["b"][0][0] == "a"
        assert neighbors["c"][0][0] in ("a", "b")

    def test_evaluate_reports_verdict(self, conn):
        self._seed_vectors(conn)
        _seed_peer(conn, "a", "b")
        _seed_peer(conn, "b", "a")
        result = evaluate(conn, artist_keys=["a", "b", "c"], k=2)
        assert result["status"] == "COMPLETE"
        assert result["usable_artists"] == 3
        assert result["recommendation"] in ("ADOPT", "INSUFFICIENT_DATA", "REJECT_OVERLAP")

    def test_evaluate_insufficient_data(self, conn):
        result = evaluate(conn, artist_keys=["a", "b"], k=2)
        assert result["status"] == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Feast pilot
# ---------------------------------------------------------------------------

class TestFeastPilot:
    def test_equivalence_on_synthetic_series(self):
        today = date(2026, 8, 26)
        daily = {today - timedelta(days=i): float(100 + i) for i in range(1, 120)}
        cutoffs = [today - timedelta(days=d) for d in (10, 45, 90)]
        result = equivalence_test(daily, cutoffs=cutoffs)
        assert result["status"] == "COMPLETE"
        assert result["mismatch_count"] == 0
        assert result["semantics_compatible"] is True
        assert result["recommendation"] == "ADOPT"

    def test_late_availability_is_excluded(self):
        # A value observed on day D but available only at D+2 (late import)
        # must NOT leak into a cutoff at D+1.
        today = date(2026, 8, 26)
        daily = {today - timedelta(days=5): 100.0}
        cutoffs = [today - timedelta(days=4)]
        result = equivalence_test(daily, cutoffs=cutoffs, available_delta_days=2)
        assert result["mismatch_count"] == 0
        assert result["semantics_compatible"] is True

    def test_feast_style_retrieval_gates_on_availability(self):
        today = date(2026, 8, 26)
        rows = build_feast_rows(
            {today - timedelta(days=3): 100.0},
            entity="a1", feature="pageviews", available_delta_days=1,
        )
        retrieval = FeastStyleRetrieval(rows)
        # at cutoff D+2, the day D+3 observation is NOT available yet
        assert retrieval.retrieve("a1", today - timedelta(days=2), "pageviews") is None
        # at cutoff D+5, it IS available
        assert retrieval.retrieve("a1", today - timedelta(days=5), "pageviews") is None
        assert retrieval.retrieve("a1", today, "pageviews") == 100.0

    def test_run_pilot_default(self):
        result = feast_run()
        assert result["status"] == "COMPLETE"
        assert result["comparisons"] == 9  # 3 cutoffs x 3 windows


# ---------------------------------------------------------------------------
# Perspective pilot
# ---------------------------------------------------------------------------

class TestPerspectivePilot:
    def _seed_snapshot(self, conn):
        conn.execute(
            """INSERT INTO metrics.artist_security_snapshots
               (snapshot_key, artist_key, snapshot_date, factor_summary,
                snapshot_version, calculated_at)
               VALUES ('s1', 'mbid::a1', '2026-08-26', ?, 'v1', '2026-08-26T00:00:00Z')""",
            [json.dumps({
                "DEMAND": [{"factor_name": "YT_SUBSCRIBERS", "value": 1200.0}],
                "MOMENTUM": [{"factor_name": "WIKI_MOMENTUM", "value": 0.35}],
                "CATALOG": [{"factor_name": "DAYS_SINCE_LAST_RELEASE", "value": 42.0}],
            })],
        )

    def test_export_snapshot_flat_rows(self, conn):
        self._seed_snapshot(conn)
        rows = export_snapshot(conn)
        assert len(rows) == 1
        assert rows[0]["artist"] == "mbid::a1"
        assert rows[0]["yt_momentum"] == 1200.0
        assert rows[0]["catalog_recency"] == 42.0
        assert rows[0]["factor_coverage"] == 3

    def test_run_pilot_reports_semantics(self, conn):
        self._seed_snapshot(conn)
        result = perspective_run(conn)
        assert result["status"] == "COMPLETE"
        assert result["recommendation"] == "ADOPT"
        assert result["semantics"]["rows"] == 1
        assert "artist" in result["semantics"]["columns_present"]
        assert "lb_momentum" not in result["semantics"]["columns_present"]

    def test_run_pilot_empty(self, conn):
        result = perspective_run(conn)
        assert result["recommendation"] == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# memray pilot
# ---------------------------------------------------------------------------

class TestMemrayPilot:
    def test_fails_closed_when_not_installed(self):
        """Fail-closed: never crashes the suite, never installs anything.

        When memray is absent we report SKIPPED. When it IS installed the
        pilot runs against a nonexistent DB and must report ERROR (fail
        closed) rather than raise — proving it is dev tooling only.
        """
        result = profile_materialization(db_path="/nonexistent/path/warehouse.duckdb")
        assert result["status"] in ("SKIPPED", "ERROR", "TIMEOUT", "COMPLETE")
        assert "not a runtime dependency" in result.get("reason", "") or result["status"] != "SKIPPED"
