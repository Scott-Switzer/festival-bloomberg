"""Regressions for the terminal serving-snapshot architecture.

The terminal must never hold the canonical research warehouse open. It serves a
read-only published snapshot and writes mutable analyst state to a separate
workspace DB. These tests cover snapshot publishing, workspace migration, the
fail-closed serving open, workspace/read-model splitting, and the critical
concurrency acceptance: a separate process can write canonical while the
terminal (serving + workspace) stays online.
"""

from __future__ import annotations

import json
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


def _publish(tmp_path, name="canonical.duckdb"):
    canonical = str(tmp_path / name)
    conn = _canonical_with_schema(canonical)
    conn.close()
    serving = str(tmp_path / "serving.duckdb")
    storage.publish_snapshot(canonical, serving)
    return canonical, serving


# ---------------------------------------------------------------------------
# Snapshot publishing + serving open
# ---------------------------------------------------------------------------
def test_publish_snapshot_copies_tables_and_meta(tmp_path):
    canonical, serving = _publish(tmp_path)
    conn = storage.open_serving_snapshot(serving)
    try:
        # Full schema present (core.artists exists even if empty).
        n = conn.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name='core' AND table_name='artists'"
        ).fetchone()[0]
        assert n == 1
        # Snapshot metadata was written.
        meta = conn.execute("SELECT manifest FROM terminal_snapshot_meta LIMIT 1").fetchone()
        assert meta is not None
        manifest = json.loads(meta[0])
        assert manifest["canonical_source_path"] == canonical
        assert manifest["table_count"] >= 1
    finally:
        conn.close()


def test_open_serving_snapshot_fails_closed_when_missing(tmp_path):
    missing = str(tmp_path / "nope.duckdb")
    with pytest.raises(FileNotFoundError) as exc:
        storage.open_serving_snapshot(missing)
    assert "SERVING_SNAPSHOT_OUTDATED" in str(exc.value)


def test_serving_snapshot_is_read_only(tmp_path):
    _, serving = _publish(tmp_path)
    conn = storage.open_serving_snapshot(serving)
    try:
        with pytest.raises(Exception):
            conn.execute("CREATE TABLE core.should_fail (x INT)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workspace DB + migration
# ---------------------------------------------------------------------------
def test_create_workspace_db_has_user_state_tables(tmp_path):
    ws = str(tmp_path / "workspace.duckdb")
    conn = storage.create_workspace_db(ws)
    try:
        for schema, table in storage.WORKSPACE_TABLES:
            n = conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
                [schema, table],
            ).fetchone()[0]
            assert n == 1, f"missing {schema}.{table}"
    finally:
        conn.close()


def test_migrate_workspace_state_copies_and_keeps_canonical(tmp_path):
    canonical = str(tmp_path / "canonical.duckdb")
    conn = _canonical_with_schema(canonical)
    conn.execute(
        "INSERT INTO core.watchlists (watchlist_key, name) VALUES ('wl1', 'My List')"
    )
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
        assert ws_conn.execute(
            "SELECT name FROM core.watchlists WHERE watchlist_key='wl1'"
        ).fetchone()[0] == "My List"
    finally:
        ws_conn.close()

    # Canonical history is NOT deleted.
    canon_conn = duckdb.connect(canonical, read_only=True)
    try:
        assert canon_conn.execute(
            "SELECT COUNT(*) FROM core.watchlists WHERE watchlist_key='wl1'"
        ).fetchone()[0] == 1
    finally:
        canon_conn.close()


# ---------------------------------------------------------------------------
# Write/read splitting + concurrency acceptance
# ---------------------------------------------------------------------------
def test_watchlist_writes_go_to_workspace_not_serving(tmp_path):
    canonical, serving = _publish(tmp_path)
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    s_conn = storage.open_serving_snapshot(serving)
    app = TerminalApp(s_conn, ws_conn)
    try:
        res = app.dispatch("POST", "/api/watchlists", body=json.dumps({"name": "wl"}).encode())
        assert res["status"] == 200
        # In workspace...
        assert ws_conn.execute("SELECT COUNT(*) FROM core.watchlists").fetchone()[0] == 1
        # ...not in the serving snapshot.
        assert s_conn.execute("SELECT COUNT(*) FROM core.watchlists").fetchone()[0] == 0
    finally:
        s_conn.close()
        ws_conn.close()


def test_concurrency_canonical_writer_while_terminal_online(tmp_path):
    """The acceptance that fixes the PID 91764 class failure.

    Terminal online (serving snapshot + workspace open) while a SEPARATE
    process opens canonical read-write and inserts — must succeed.
    """
    canonical = str(tmp_path / "canonical.duckdb")
    conn = _canonical_with_schema(canonical)
    conn.execute(
        "INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a1', 'A', 'a')"
    )
    conn.commit()
    conn.close()

    serving = str(tmp_path / "serving.duckdb")
    storage.publish_snapshot(canonical, serving)
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    s_conn = storage.open_serving_snapshot(serving)
    app = TerminalApp(s_conn, ws_conn)

    try:
        # Separate process writes canonical while terminal connections are open.
        script = (
            "import duckdb\n"
            f"c = duckdb.connect({canonical!r})\n"
            "c.execute(\"INSERT INTO core.artists (artist_key, name, normalized_name) VALUES ('a2', 'B', 'b')\")\n"
            "c.commit()\n"
            "c.close()\n"
            "print('WROTE_OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, proc.stderr
        assert "WROTE_OK" in proc.stdout

        # Terminal still serves (its own snapshot is unaffected mid-flight).
        assert app.dispatch("GET", "/api/planning/projects")["status"] == 200
    finally:
        s_conn.close()
        ws_conn.close()

    # The new row landed in canonical.
    check = duckdb.connect(canonical, read_only=True)
    try:
        names = {r[0] for r in check.execute("SELECT name FROM core.artists").fetchall()}
        assert names == {"A", "B"}
    finally:
        check.close()


def test_today_combines_workspace_watchlist_and_serving_alerts(tmp_path):
    canonical = str(tmp_path / "canonical.duckdb")
    conn = _canonical_with_schema(canonical)
    conn.commit()
    conn.close()

    serving = str(tmp_path / "serving.duckdb")
    storage.publish_snapshot(canonical, serving)
    ws = str(tmp_path / "workspace.duckdb")
    ws_conn = storage.create_workspace_db(ws)
    ws_conn.execute(
        "INSERT INTO core.watchlists (watchlist_key, name) VALUES ('wl1', 'Watched')"
    )
    ws_conn.execute(
        "INSERT INTO core.watchlist_items (item_key, watchlist_key, entity_type, entity_key, entity_name) "
        "VALUES ('i1', 'wl1', 'ARTIST', 'mbid::x', 'Artist X')"
    )
    ws_conn.commit()

    s_conn = storage.open_serving_snapshot(serving)
    try:
        from festival_bloomberg.product.workflow import build_today

        today = build_today(s_conn, ws_conn, limit=10)
        watchlist = today["sections"]["watchlist"]
        assert watchlist["watched_entities"] == 1
        assert watchlist["watched_names"] == ["Artist X"]
    finally:
        s_conn.close()
        ws_conn.close()
