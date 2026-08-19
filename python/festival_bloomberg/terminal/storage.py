"""Terminal storage roles: canonical / serving snapshot / workspace.

Three storage roles, three files — the terminal must never hold the canonical
research warehouse open:

    CANONICAL   authoritative evidence + acquisition (one writer)
    SERVING     read-only published snapshot the terminal serves (readers)
    WORKSPACE   mutable analyst state (watchlists, monitors, planning projects)

DuckDB native files do not allow a separate writer while readers have the file
open, so the terminal reads the SERVING snapshot (a separate file) and writes
only the WORKSPACE file. Canonical ingestion can therefore own the canonical
file whenever it needs to.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Default paths (project-root relative). These match the documented roles.
SERVING_DEFAULT_DB = os.path.join(_PROJECT_ROOT, "data", "serving", "terminal_snapshot.duckdb")
WORKSPACE_DEFAULT_DB = os.path.join(_PROJECT_ROOT, "data", "workspace", "terminal_workspace.duckdb")

# Tables that hold MUTABLE USER STATE (moved out of canonical storage).
# Evidence observations, alerts, and read models are NOT in this list.
WORKSPACE_TABLES: tuple[tuple[str, str], ...] = (
    ("core", "watchlists"),
    ("core", "watchlist_items"),
    ("terminal", "saved_monitors"),
    ("planning", "festival_projects"),
    ("planning", "festival_project_stages"),
    ("planning", "festival_candidate_artists"),
    ("planning", "festival_shortlists"),
    ("planning", "festival_constraints"),
    ("planning", "festival_scenarios"),
)


def open_serving_snapshot(db_path: str = SERVING_DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    """Open the published serving snapshot read-only.

    Fails closed with a clear message if no snapshot exists yet (rather than
    silently opening canonical or applying migrations).
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"SERVING_SNAPSHOT_OUTDATED: no serving snapshot at {db_path}. "
            "Run `festival terminal publish-snapshot` first."
        )
    return duckdb.connect(db_path, read_only=True)


def create_workspace_db(db_path: str = WORKSPACE_DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the mutable workspace DB, schema applied.

    Applies the full migration schema so the workspace tables (watchlists,
    monitors, planning) exist. Canonical evidence tables exist here too but are
    intentionally empty and never read by the terminal.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = duckdb.connect(db_path)
    from ..migrations import apply_pending_migrations

    apply_pending_migrations(conn)
    return conn


def publish_snapshot(
    canonical_path: str,
    snapshot_path: str = SERVING_DEFAULT_DB,
    *,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Build a consistent serving snapshot from canonical storage.

    Copies every base table via DuckDB cross-database ``CREATE TABLE AS`` (no
    raw file copy of a potentially-open DB). Returns metadata incl. a compact
    checksum summary. The caller is responsible for ensuring canonical is not
    being actively written during the copy (DuckDB single-writer model).
    """
    snapshot_id = snapshot_id or f"snap_{_now_stamp()}"
    parent = os.path.dirname(snapshot_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    dst = duckdb.connect(snapshot_path)
    try:
        dst.execute(f"ATTACH '{canonical_path}' AS canonical (READ_ONLY)")
        tables = dst.execute(
            """
            SELECT schema_name, table_name
            FROM duckdb_tables()
            WHERE database_name = 'canonical' AND NOT internal AND NOT temporary
            ORDER BY schema_name, table_name
            """
        ).fetchall()
        views = dst.execute(
            """
            SELECT schema_name, view_name
            FROM duckdb_views()
            WHERE database_name = 'canonical' AND NOT internal AND NOT temporary
            ORDER BY schema_name, view_name
            """
        ).fetchall()

        counts: dict[str, int] = {}
        # Base tables: copy structure + data.
        for schema, table in tables:
            dst.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            dst.execute(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" AS '
                f'SELECT * FROM canonical."{schema}"."{table}"'
            )
            counts[f"{schema}.{table}"] = int(
                dst.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
            )

        # Views: materialize their result (view dependencies resolve against
        # the attached canonical DB, which stays attached for the whole copy).
        for schema, view in views:
            dst.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            dst.execute(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{view}" AS '
                f'SELECT * FROM canonical."{schema}"."{view}"'
            )
            counts[f"{schema}.{view}"] = int(
                dst.execute(f'SELECT COUNT(*) FROM "{schema}"."{view}"').fetchone()[0]
            )

        total_rows = sum(counts.values())
        dst.execute("DETACH canonical")

        manifest = {
            "snapshot_id": snapshot_id,
            "canonical_source_path": canonical_path,
            "published_at": _now_stamp(),
            "software_version": "terminal_serving_snapshot_v1",
            "table_count": len(tables) + len(views),
            "total_rows": total_rows,
            "table_row_counts": counts,
        }
        dst.execute(
            "CREATE TABLE IF NOT EXISTS terminal_snapshot_meta "
            "(snapshot_id VARCHAR PRIMARY KEY, published_at TIMESTAMP, manifest JSON)"
        )
        dst.execute(
            "INSERT OR REPLACE INTO terminal_snapshot_meta (snapshot_id, published_at, manifest) "
            "VALUES (?, now(), ?)",
            [snapshot_id, json.dumps(manifest)],
        )
        dst.execute("CHECKPOINT")
        dst.commit()
        return manifest
    finally:
        dst.close()


def migrate_workspace_state(canonical_path: str, workspace_conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """One-time, idempotent import of existing user state into workspace.

    Reads old workspace state from canonical (watchlists, monitors, planning)
    and copies rows into the workspace DB. Canonical history is NOT deleted.
    """
    migrated: dict[str, int] = {}
    workspace_conn.execute(f"ATTACH '{canonical_path}' AS canonical (READ_ONLY)")
    try:
        canonical_tables = {
            (r[0], r[1])
            for r in workspace_conn.execute(
                "SELECT schema_name, table_name FROM duckdb_tables() "
                "WHERE database_name = 'canonical' AND NOT internal AND NOT temporary"
            ).fetchall()
        }
        for schema, table in WORKSPACE_TABLES:
            if (schema, table) not in canonical_tables:
                migrated[f"{schema}.{table}"] = 0
                continue
            workspace_conn.execute(
                f'INSERT OR IGNORE INTO "{schema}"."{table}" '
                f'SELECT * FROM canonical."{schema}"."{table}"'
            )
            migrated[f"{schema}.{table}"] = int(
                workspace_conn.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
            )
        workspace_conn.commit()
    finally:
        workspace_conn.execute("DETACH canonical")
    return migrated


def _now_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
