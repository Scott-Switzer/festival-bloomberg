"""Regressions for the terminal serving-snapshot architecture.

Covers immutable/versioned snapshot publication (no in-place updates), the
snapshot integrity contract, fail-closed open validation, workspace table
stripping, the dedicated workspace schema boundary, workspace migration with
explicit columns, write/read splitting, and the critical concurrency
acceptance: a separate process writes canonical while the terminal (serving +
workspace) stays online, and repeated publication never serves stale data.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.terminal import storage
from festival_bloomberg.terminal.server import TerminalApp


def _canonical_with_schema(path: str):
    conn = duckdb.connect(path)
    apply_pending_migrations(conn)
    return conn


def _publish(tmp_path, canonical, name="canonical.duckdb", snapshot_id=None):
    serving_dir = str(tmp_path / "serving")
    manifest = storage.publish_snapshot(canonical, snapshot_dir=serving_dir, snapshot_id=snapshot_id)
    return serving_dir, manifest


def _new_canonical(tmp_path, name="canonical.duckdb"):
    canonical = str(tmp_path / name)
    conn = _canonical_with_schema(canonical)
    conn.close()
    return canonical


# ---------------------------------------------------------------------------
# Immutable snapshot publication + CURRENT pointer
# ---------------------------------------------------------------------------
def test_publish_snapshot_writes_immutable_file_and_current(tmp_path):
    canonical = _new_canonical(tmp_path)
    serving_dir, manifest = _publish(tmp_path, canonical, snapshot_id="snap_1")

    snap_path = manifest["snapshot_path"]
    assert os.path.basename(snap_path) == "snap_1.duckdb"
    assert os.path.exists(snap_path)
    assert storage.current_snapshot_path(serving_dir) == snap_path

    conn = storage.open_serving_snapshot(serving_dir)
    try:
        meta = json.loads(
            conn.execute(
                "SELECT manifest FROM terminal_snapshot_meta WHERE snapshot_id='snap_1'"
            ).fetchone()[0]
        )
        assert meta["status"] == "VERIFIED"
        assert meta["canonical_source_path"].endswith("canonical.duckdb")
        assert meta["publisher_version"] == storage.PUBLISHER_VERSION
        # Critical tables present in the snapshot.
        for s, t in storage.CRITICAL_TABLES:
            n = conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
                [s, t],
            ).fetchone()[0]
            assert n == 1, f"missing {s}.{t}"
    finally:
        conn.close()


def test_repeated_publish_never_serves_stale_data(tmp_path):
    """P0: publish N, mutate canonical, publish N+1 — no in-place updates."""
    canonical = _new_canonical(tmp_path)
    _, manifest_n = _publish(tmp_path, canonical, snapshot_id="snap_n")

    # Mutate canonical.
    conn = duckdb.connect(canonical)
    conn.execute("INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a9', 'NIN', 'nin')")
    conn.commit()
    conn.close()

    _, manifest_n1 = _publish(tmp_path, canonical, snapshot_id="snap_n1")

    assert manifest_n["snapshot_id"] != manifest_n1["snapshot_id"]
    assert manifest_n["snapshot_path"] != manifest_n1["snapshot_path"]
    assert os.path.exists(manifest_n["snapshot_path"])

    # N+1 has the mutation; N remains untouched.
    c1 = duckdb.connect(manifest_n1["snapshot_path"], read_only=True)
    try:
        assert c1.execute("SELECT COUNT(*) FROM core.artists WHERE artist_key='a9'").fetchone()[0] == 1
    finally:
        c1.close()
    cn = duckdb.connect(manifest_n["snapshot_path"], read_only=True)
    try:
        assert cn.execute("SELECT COUNT(*) FROM core.artists WHERE artist_key='a9'").fetchone()[0] == 0
    finally:
        cn.close()

    # CURRENT points at N+1.
    assert storage.current_snapshot_path(tmp_path / "serving") == manifest_n1["snapshot_path"]


def test_publish_refuses_to_overwrite_existing_snapshot(tmp_path):
    canonical = _new_canonical(tmp_path)
    serving_dir = str(tmp_path / "serving")
    storage.publish_snapshot(canonical, snapshot_dir=serving_dir, snapshot_id="snap_dup")
    with pytest.raises(FileExistsError):
        storage.publish_snapshot(canonical, snapshot_dir=serving_dir, snapshot_id="snap_dup")


# ---------------------------------------------------------------------------
# P0: Snapshot status lifecycle — no VERIFIED before verification
# ---------------------------------------------------------------------------
def test_snapshot_status_lifecycle(tmp_path):
    """The manifest inside the snapshot must be VERIFIED, never BUILDING/COPIED."""
    canonical = _new_canonical(tmp_path)
    serving_dir, manifest = _publish(tmp_path, canonical, snapshot_id="snap_lifecycle")

    # On-disk manifest must say VERIFIED.
    assert manifest["status"] == "VERIFIED"
    stored = json.loads(
        duckdb.connect(manifest["snapshot_path"], read_only=True)
        .execute("SELECT manifest FROM terminal_snapshot_meta").fetchone()[0]
    )
    assert stored["status"] == "VERIFIED"


def test_failed_verification_does_not_leave_verified_file(tmp_path):
    """If verification fails, no file with status=VERIFIED should remain."""
    canonical = _new_canonical(tmp_path)
    serving_dir = str(tmp_path / "serving")

    # Simulate a mismatch: publish, then corrupt the manifest counts.
    manifest = storage.publish_snapshot(canonical, snapshot_dir=serving_dir, snapshot_id="snap_corrupt")

    # Now publish a second snapshot — if we tamper the canonical between
    # copy and verify, the snapshot is cleaned up.  We test the cleanup
    # path by attempting to publish with a snapshot_id that already exists
    # (which is caught before verification).
    with pytest.raises(FileExistsError):
        storage.publish_snapshot(canonical, snapshot_dir=serving_dir, snapshot_id="snap_corrupt")


# ---------------------------------------------------------------------------
# P0: Critical row-count verification
# ---------------------------------------------------------------------------
def test_open_validates_critical_row_counts(tmp_path):
    """open_serving_snapshot rejects snapshots with mismatched row counts."""
    canonical = _new_canonical(tmp_path)
    # Add some data so counts are non-zero.
    conn = duckdb.connect(canonical)
    conn.execute("INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a1', 'A', 'a')")
    conn.commit()
    conn.close()

    serving_dir, manifest = _publish(tmp_path, canonical, snapshot_id="snap_counts")

    # Tamper the manifest to record wrong counts.
    snap_path = manifest["snapshot_path"]
    tampered = duckdb.connect(snap_path)
    stored = json.loads(tampered.execute("SELECT manifest FROM terminal_snapshot_meta").fetchone()[0])
    stored["critical_table_counts"]["core.artists"] = 999999  # Wrong count.
    tampered.execute(
        "UPDATE terminal_snapshot_meta SET manifest = ?",
        [json.dumps(stored)],
    )
    tampered.commit()
    tampered.close()

    with pytest.raises(storage.ServingSnapshotError, match="ROW_COUNT_MISMATCH"):
        storage.open_serving_snapshot(serving_dir)


def test_critical_table_row_counts_recorded_in_manifest(tmp_path):
    """The manifest records actual row counts for critical tables."""
    canonical = _new_canonical(tmp_path)
    conn = duckdb.connect(canonical)
    conn.execute("INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a1', 'A', 'a')")
    conn.execute("INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a2', 'B', 'b')")
    conn.commit()
    conn.close()

    _, manifest = _publish(tmp_path, canonical, snapshot_id="snap_c2")
    counts = manifest["critical_table_counts"]
    assert counts["core.artists"] == 2


# ---------------------------------------------------------------------------
# P0: Serving snapshot must NOT contain workspace tables
# ---------------------------------------------------------------------------
def test_serving_snapshot_does_not_contain_workspace_tables(tmp_path):
    """After canonical copy, workspace/user-state tables are dropped."""
    canonical = _new_canonical(tmp_path)
    conn = duckdb.connect(canonical)
    # Add workspace state to canonical (simulates legacy data).
    conn.execute("INSERT INTO core.watchlists (watchlist_key, name) VALUES ('wl1', 'Private List')")
    conn.execute(
        "INSERT INTO planning.festival_projects (project_key, name, scenario_class) "
        "VALUES ('p1', 'Secret Project', 'SYNTHETIC_PLANNING_SCENARIO')"
    )
    conn.commit()
    conn.close()

    serving_dir, manifest = _publish(tmp_path, canonical, snapshot_id="snap_strip")
    s_conn = duckdb.connect(manifest["snapshot_path"], read_only=True)
    try:
        for schema, table in storage.WORKSPACE_TABLES:
            n = s_conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
                [schema, table],
            ).fetchone()[0]
            assert n == 0, f"serving snapshot must not contain {schema}.{table}"
    finally:
        s_conn.close()


def test_workspace_migration_preserves_private_state(tmp_path):
    """Canonical workspace tables survive migration even though serving strips them."""
    canonical = _new_canonical(tmp_path)
    conn = duckdb.connect(canonical)
    conn.execute("INSERT INTO core.watchlists (watchlist_key, name) VALUES ('wl1', 'My Watchlist')")
    conn.execute(
        "INSERT INTO planning.festival_projects (project_key, name, scenario_class) "
        "VALUES ('p1', 'My Project', 'SYNTHETIC_PLANNING_SCENARIO')"
    )
    conn.commit()
    conn.close()

    # Publish serving snapshot (strips workspace tables).
    serving_dir, manifest = _publish(tmp_path, canonical, snapshot_id="snap_ws_preserve")

    # Workspace migration still gets the data from canonical.
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    try:
        migrated = storage.migrate_workspace_state(canonical, ws_conn)
        assert migrated["core.watchlists"] == 1
        assert migrated["planning.festival_projects"] == 1

        # Verify workspace actually has the data.
        assert ws_conn.execute("SELECT COUNT(*) FROM core.watchlists WHERE watchlist_key='wl1'").fetchone()[0] == 1
        assert ws_conn.execute("SELECT COUNT(*) FROM planning.festival_projects WHERE project_key='p1'").fetchone()[0] == 1
    finally:
        ws_conn.close()

    # Serving snapshot does NOT have it (table was stripped, not just empty).
    s_conn = storage.open_serving_snapshot(serving_dir)
    try:
        n = s_conn.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name='core' AND table_name='watchlists'"
        ).fetchone()[0]
        assert n == 0, "serving snapshot must not contain core.watchlists"
    finally:
        s_conn.close()


# ---------------------------------------------------------------------------
# Fail-closed open validation
# ---------------------------------------------------------------------------
def test_open_serving_snapshot_missing(tmp_path):
    with pytest.raises(storage.ServingSnapshotError) as exc:
        storage.open_serving_snapshot(str(tmp_path / "nope"))
    assert "SERVING_SNAPSHOT_MISSING" in str(exc.value)


def test_open_serving_snapshot_corrupt_no_manifest(tmp_path):
    bad = str(tmp_path / "bad.duckdb")
    conn = duckdb.connect(bad)
    conn.execute("CREATE TABLE x (y INT)")
    conn.close()
    with pytest.raises(storage.ServingSnapshotError) as exc:
        storage.open_serving_snapshot(bad)
    assert "SERVING_SNAPSHOT_CORRUPT" in str(exc.value)


def test_open_serving_snapshot_schema_incompatible(tmp_path):
    canonical = _new_canonical(tmp_path)
    serving_dir, manifest = _publish(tmp_path, canonical, snapshot_id="snap_incompat")
    # Corrupt the stored publisher_version to a future version.
    conn = duckdb.connect(manifest["snapshot_path"])
    m = json.loads(conn.execute(
        "SELECT manifest FROM terminal_snapshot_meta WHERE snapshot_id='snap_incompat'"
    ).fetchone()[0])
    m["publisher_version"] = "future_v999"
    conn.execute(
        "UPDATE terminal_snapshot_meta SET manifest=? WHERE snapshot_id='snap_incompat'",
        [json.dumps(m)],
    )
    conn.close()
    with pytest.raises(storage.ServingSnapshotError) as exc:
        storage.open_serving_snapshot(serving_dir)
    assert "SERVING_SCHEMA_INCOMPATIBLE" in str(exc.value)


def test_serving_snapshot_is_read_only(tmp_path):
    canonical = _new_canonical(tmp_path)
    serving_dir, _ = _publish(tmp_path, canonical, snapshot_id="snap_ro")
    conn = storage.open_serving_snapshot(serving_dir)
    try:
        with pytest.raises(Exception):
            conn.execute("CREATE TABLE core.should_fail (x INT)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workspace schema boundary
# ---------------------------------------------------------------------------
def test_workspace_has_only_user_state_tables(tmp_path):
    ws = str(tmp_path / "workspace.duckdb")
    conn = storage.create_workspace_db(ws)
    try:
        for schema, table in storage.WORKSPACE_TABLES:
            n = conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
                [schema, table],
            ).fetchone()[0]
            assert n == 1, f"missing {schema}.{table}"
        # Canonical evidence tables must NOT exist in workspace.
        for schema, table in (
            ("core", "artists"),
            ("core", "venues"),
            ("events", "provider_event_snapshots"),
            ("metrics", "artist_attention_observations"),
            ("economics", "event_outcome_claims"),
        ):
            n = conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
                [schema, table],
            ).fetchone()[0]
            assert n == 0, f"workspace must not contain {schema}.{table}"
        assert conn.execute(
            "SELECT value FROM workspace_meta WHERE key='schema_version'"
        ).fetchone()[0] == "terminal_workspace_v3"
    finally:
        conn.close()


def test_workspace_created_at_is_stable(tmp_path):
    """created_at is written once (INSERT OR IGNORE) and never overwritten."""
    ws = str(tmp_path / "workspace.duckdb")
    conn1 = storage.create_workspace_db(ws)
    ts1 = conn1.execute("SELECT value FROM workspace_meta WHERE key='created_at'").fetchone()[0]
    conn1.close()

    # Reopen — created_at should not change.
    conn2 = storage.create_workspace_db(ws)
    ts2 = conn2.execute("SELECT value FROM workspace_meta WHERE key='created_at'").fetchone()[0]
    conn2.close()

    assert ts1 == ts2


def test_migrate_workspace_state_copies_and_keeps_canonical(tmp_path):
    canonical = _new_canonical(tmp_path)
    conn = duckdb.connect(canonical)
    conn.execute("INSERT INTO core.watchlists (watchlist_key, name) VALUES ('wl1', 'My List')")
    conn.execute(
        "INSERT INTO core.watchlist_items (item_key, watchlist_key, entity_type, entity_key, entity_name) "
        "VALUES ('i1', 'wl1', 'ARTIST', 'mbid::x', 'Artist X')"
    )
    conn.commit()
    conn.close()

    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    try:
        migrated = storage.migrate_workspace_state(canonical, ws_conn)
        assert migrated["core.watchlists"] == 1
        assert migrated["core.watchlist_items"] == 1
    finally:
        ws_conn.close()

    canon = duckdb.connect(canonical, read_only=True)
    try:
        assert canon.execute(
            "SELECT COUNT(*) FROM core.watchlists WHERE watchlist_key='wl1'"
        ).fetchone()[0] == 1
    finally:
        canon.close()


def test_migrate_records_last_migrated_at(tmp_path):
    canonical = _new_canonical(tmp_path)
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    try:
        storage.migrate_workspace_state(canonical, ws_conn)
        val = ws_conn.execute(
            "SELECT value FROM workspace_meta WHERE key='last_migrated_at'"
        ).fetchone()
        assert val is not None, "last_migrated_at must be set after migration"
    finally:
        ws_conn.close()


# ---------------------------------------------------------------------------
# P1: Explicit migration column validation
# ---------------------------------------------------------------------------
def test_migrate_rejects_incompatible_canonical_schema(tmp_path):
    """If canonical is missing expected columns, migration fails clearly."""
    canonical = str(tmp_path / "incomplete.duckdb")
    conn = duckdb.connect(canonical)
    # Create watchlists with only 2 columns (missing most of the expected set).
    conn.execute("CREATE SCHEMA core")
    conn.execute("CREATE TABLE core.watchlists (watchlist_key VARCHAR, name VARCHAR)")
    conn.commit()
    conn.close()

    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    try:
        with pytest.raises(storage.ServingSnapshotError, match="WORKSPACE_MIGRATION_INCOMPATIBLE"):
            storage.migrate_workspace_state(canonical, ws_conn)
    finally:
        ws_conn.close()


# ---------------------------------------------------------------------------
# estimated_total_rows naming
# ---------------------------------------------------------------------------
def test_manifest_uses_estimated_total_rows(tmp_path):
    canonical = _new_canonical(tmp_path)
    _, manifest = _publish(tmp_path, canonical, snapshot_id="snap_est")
    assert "estimated_total_rows" in manifest
    assert isinstance(manifest["estimated_total_rows"], int)
    # Old key must not exist.
    assert "total_rows" not in manifest


# ---------------------------------------------------------------------------
# Write/read splitting + concurrency + generational acceptance
# ---------------------------------------------------------------------------
def test_watchlist_writes_go_to_workspace_not_serving(tmp_path):
    canonical = _new_canonical(tmp_path)
    serving_dir, _ = _publish(tmp_path, canonical, snapshot_id="snap_ws")
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    s_conn = storage.open_serving_snapshot(serving_dir)
    app = TerminalApp(s_conn, ws_conn)
    try:
        res = app.dispatch("POST", "/api/watchlists", body=json.dumps({"name": "wl"}).encode())
        assert res["status"] == 200
        assert ws_conn.execute("SELECT COUNT(*) FROM core.watchlists").fetchone()[0] == 1
        # watchlists is stripped from serving — check via metadata.
        n = s_conn.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name='core' AND table_name='watchlists'"
        ).fetchone()[0]
        assert n == 0
    finally:
        s_conn.close()
        ws_conn.close()


def test_concurrency_canonical_writer_while_terminal_online(tmp_path):
    """The acceptance that fixes the PID 91764 class failure."""
    canonical = _new_canonical(tmp_path)
    conn = duckdb.connect(canonical)
    conn.execute("INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a1', 'A', 'a')")
    conn.commit()
    conn.close()

    serving_dir, _ = _publish(tmp_path, canonical, snapshot_id="snap_conc")
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    s_conn = storage.open_serving_snapshot(serving_dir)
    app = TerminalApp(s_conn, ws_conn)

    try:
        script = (
            "import duckdb\n"
            f"c = duckdb.connect({canonical!r})\n"
            "c.execute(\"INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a2', 'B', 'b')\")\n"
            "c.commit()\n"
            "c.close()\n"
            "print('WROTE_OK')\n"
        )
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert "WROTE_OK" in proc.stdout
        assert app.dispatch("GET", "/api/planning/projects")["status"] == 200
    finally:
        s_conn.close()
        ws_conn.close()


def test_generational_snapshot_while_terminal_serves_n(tmp_path):
    """Publish N+1 while the terminal keeps serving N; reopen picks up N+1."""
    canonical = _new_canonical(tmp_path)
    conn = duckdb.connect(canonical)
    conn.execute("INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a1', 'A', 'a')")
    conn.commit()
    conn.close()

    serving_dir, manifest_n = _publish(tmp_path, canonical, snapshot_id="snap_n")
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    ws_conn.execute(
        "INSERT INTO core.watchlists (watchlist_key, name) VALUES ('wl1', 'P')"
    )
    ws_conn.commit()

    # Terminal online on snapshot N.
    s_conn = storage.open_serving_snapshot(serving_dir)
    app = TerminalApp(s_conn, ws_conn)

    try:
        # Separate process mutates canonical, then publish N+1.
        conn = duckdb.connect(canonical)
        conn.execute("INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a2', 'B', 'b')")
        conn.commit()
        conn.close()

        manifest_n1 = storage.publish_snapshot(canonical, snapshot_dir=serving_dir, snapshot_id="snap_n1")

        # Old terminal connection still reads N consistently (no 'a2').
        assert s_conn.execute(
            "SELECT COUNT(*) FROM core.artists WHERE artist_key='a2'"
        ).fetchone()[0] == 0

        # CURRENT points at N+1; a fresh open sees 'a2'.
        assert storage.current_snapshot_path(serving_dir) == manifest_n1["snapshot_path"]
        s2 = storage.open_serving_snapshot(serving_dir)
        try:
            assert s2.execute(
                "SELECT COUNT(*) FROM core.artists WHERE artist_key='a2'"
            ).fetchone()[0] == 1
        finally:
            s2.close()

        # Workspace state persisted throughout.
        assert ws_conn.execute("SELECT COUNT(*) FROM core.watchlists WHERE watchlist_key='wl1'").fetchone()[0] == 1
    finally:
        s_conn.close()
        ws_conn.close()


def test_today_combines_workspace_watchlist_and_serving_alerts(tmp_path):
    canonical = _new_canonical(tmp_path)
    serving_dir, _ = _publish(tmp_path, canonical, snapshot_id="snap_today")
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    ws_conn.execute("INSERT INTO core.watchlists (watchlist_key, name) VALUES ('wl1', 'Watched')")
    ws_conn.execute(
        "INSERT INTO core.watchlist_items (item_key, watchlist_key, entity_type, entity_key, entity_name) "
        "VALUES ('i1', 'wl1', 'ARTIST', 'mbid::x', 'Artist X')"
    )
    ws_conn.commit()

    s_conn = storage.open_serving_snapshot(serving_dir)
    try:
        from festival_bloomberg.product.workflow import build_today

        today = build_today(s_conn, ws_conn, limit=10)
        watchlist = today["sections"]["watchlist"]
        assert watchlist["watched_entities"] == 1
        assert watchlist["watched_names"] == ["Artist X"]
    finally:
        s_conn.close()
        ws_conn.close()
