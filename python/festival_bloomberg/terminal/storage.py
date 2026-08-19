"""Terminal storage roles: canonical / immutable serving snapshot / workspace.

Three storage roles, three files — the terminal must never hold the canonical
research warehouse open:

    CANONICAL   authoritative evidence + acquisition (one writer)
    SERVING     immutable, versioned, read-only published snapshots
    WORKSPACE   mutable analyst state (watchlists, monitors, planning projects)

Serving snapshots are IMMUTABLE and versioned:

    data/serving/terminal_snapshot_<id>.duckdb   (never rewritten)
    data/serving/CURRENT.json                    (atomically-replaced pointer)

The terminal opens whatever immutable file CURRENT.json names. Publishing a new
snapshot never mutates the file the terminal currently has open, so the running
terminal keeps reading snapshot N while canonical produces snapshot N+1.
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

SERVING_DIR = os.path.join(_PROJECT_ROOT, "data", "serving")
CURRENT_FILE = "CURRENT.json"
WORKSPACE_DEFAULT_DB = os.path.join(_PROJECT_ROOT, "data", "workspace", "terminal_workspace.duckdb")
_WORKSPACE_SCHEMA_SQL = os.path.join(_PROJECT_ROOT, "schema", "workspace_schema.sql")

PUBLISHER_VERSION = "terminal_serving_snapshot_v1"
SNAPSHOT_STATUS_VERIFIED = "VERIFIED"

# Tables that hold MUTABLE USER STATE (moved out of canonical storage).
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

# Critical tables whose presence/row-counts are part of the integrity contract.
CRITICAL_TABLES: tuple[tuple[str, str], ...] = (
    ("core", "artists"),
    ("core", "venues"),
    ("core", "festivals"),
    ("core", "festival_editions"),
    ("core", "lineup_slots"),
    ("events", "provider_event_snapshots"),
    ("economics", "event_outcome_claims"),
    ("metrics", "artist_attention_observations"),
)


class ServingSnapshotError(RuntimeError):
    """A serving snapshot is missing, corrupt, or schema-incompatible."""


def publish_snapshot(
    canonical_path: str,
    snapshot_dir: str = SERVING_DIR,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Publish a NEW immutable serving snapshot from canonical.

    Snapshots are never updated in place: each publication writes a fresh,
    nonexistent ``<snapshot_id>.duckdb`` via DuckDB ``COPY FROM DATABASE``,
    verifies it, then atomically replaces the CURRENT pointer. A previously
    published snapshot (possibly still open by the terminal) is left untouched.
    """
    snapshot_id = snapshot_id or f"terminal_snapshot_{_now_stamp()}"
    snapshot_path = os.path.join(snapshot_dir, f"{snapshot_id}.duckdb")
    if os.path.exists(snapshot_path):
        raise FileExistsError(f"refusing to overwrite existing snapshot: {snapshot_path}")
    os.makedirs(snapshot_dir, exist_ok=True)

    canonical_meta = _read_canonical_meta(canonical_path)

    # Copy schema + constraints + indexes + views + data in one statement.
    orc = duckdb.connect()
    try:
        orc.execute(f"ATTACH '{canonical_path}' AS canonical (READ_ONLY)")
        orc.execute(f"ATTACH '{snapshot_path}' AS dest")
        orc.execute("COPY FROM DATABASE canonical TO dest")
        orc.execute("DETACH dest")
        orc.execute("DETACH canonical")
    finally:
        orc.close()

    manifest = {
        "snapshot_id": snapshot_id,
        "status": SNAPSHOT_STATUS_VERIFIED,
        "published_at": _now_stamp(),
        "canonical_source_path": str(Path(canonical_path).resolve()),
        "canonical_schema_version": canonical_meta["schema_version"],
        "serving_schema_version": canonical_meta["schema_version"],
        "publisher_version": PUBLISHER_VERSION,
        "source_file_size_bytes": canonical_meta["file_size_bytes"],
        "table_count": canonical_meta["table_count"],
        "total_rows": canonical_meta["total_rows"],
        "critical_table_counts": canonical_meta["critical_counts"],
    }

    # Write the manifest into the snapshot itself (read during validation).
    conn = duckdb.connect(snapshot_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS terminal_snapshot_meta "
            "(snapshot_id VARCHAR PRIMARY KEY, published_at TIMESTAMP, manifest JSON)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO terminal_snapshot_meta (snapshot_id, published_at, manifest) "
            "VALUES (?, now(), ?)",
            [snapshot_id, json.dumps(manifest)],
        )
        conn.execute("CHECKPOINT")
        conn.commit()
    finally:
        conn.close()

    # Verify independently (reopen read-only) before publishing the pointer.
    _verify_snapshot(snapshot_path, manifest)

    # Atomically publish the CURRENT pointer.
    current_path = os.path.join(snapshot_dir, CURRENT_FILE)
    tmp_path = current_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"snapshot_id": snapshot_id, "snapshot_file": os.path.basename(snapshot_path)}, fh)
    os.replace(tmp_path, current_path)

    manifest["snapshot_path"] = snapshot_path
    manifest["current_updated"] = True
    return manifest


def current_snapshot_path(snapshot_dir: str = SERVING_DIR) -> str | None:
    """Return the immutable snapshot path CURRENT points at (or None)."""
    current_path = os.path.join(snapshot_dir, CURRENT_FILE)
    if not os.path.exists(current_path):
        return None
    with open(current_path, encoding="utf-8") as fh:
        data = json.load(fh)
    name = data.get("snapshot_file") or f"{data.get('snapshot_id', '')}.duckdb"
    path = os.path.join(snapshot_dir, name)
    return path if os.path.exists(path) else None


def open_serving_snapshot(snapshot_path_or_dir: str = SERVING_DIR) -> duckdb.DuckDBPyConnection:
    """Resolve CURRENT, validate, and open the serving snapshot read-only.

    Fails closed with a distinct error class for each failure mode; it never
    silently opens canonical or applies migrations.
    """
    if os.path.isdir(snapshot_path_or_dir) or not snapshot_path_or_dir.endswith(".duckdb"):
        snapshot_dir = snapshot_path_or_dir
        path = current_snapshot_path(snapshot_dir)
        if path is None:
            raise ServingSnapshotError(
                f"SERVING_SNAPSHOT_MISSING: no verified snapshot in {snapshot_dir}"
            )
    else:
        path = snapshot_path_or_dir

    if not os.path.exists(path):
        raise ServingSnapshotError(f"SERVING_SNAPSHOT_MISSING: {path} does not exist")

    manifest = _read_snapshot_manifest(path)
    if manifest is None:
        raise ServingSnapshotError(
            f"SERVING_SNAPSHOT_CORRUPT: {path} has no terminal_snapshot_meta manifest"
        )
    if manifest.get("status") != SNAPSHOT_STATUS_VERIFIED:
        raise ServingSnapshotError(
            f"SERVING_SNAPSHOT_CORRUPT: {path} manifest status={manifest.get('status')!r}"
        )
    _assert_schema_compatible(manifest)
    return duckdb.connect(path, read_only=True)


def create_workspace_db(db_path: str = WORKSPACE_DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the mutable workspace DB.

    Applies ONLY the dedicated workspace schema (watchlists, monitors, planning
    + workspace_meta) — never the canonical migration stack. Canonical evidence
    schemas therefore cannot accidentally exist in the workspace.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = duckdb.connect(db_path)
    with open(_WORKSPACE_SCHEMA_SQL, encoding="utf-8") as fh:
        for statement in _split_statements(fh.read()):
            conn.execute(statement)
    conn.commit()
    return conn


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


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _read_canonical_meta(canonical_path: str) -> dict[str, Any]:
    conn = duckdb.connect(canonical_path, read_only=True)
    try:
        schema_version = None
        try:
            schema_version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        except Exception:
            schema_version = None

        table_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE NOT internal AND NOT temporary"
            ).fetchone()[0]
        )
        critical_counts: dict[str, int] = {}
        for schema, table in CRITICAL_TABLES:
            try:
                critical_counts[f"{schema}.{table}"] = int(
                    conn.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
                )
            except Exception:
                critical_counts[f"{schema}.{table}"] = 0
        total_rows = int(
            conn.execute(
                "SELECT SUM(estimated_size) FROM duckdb_tables() WHERE NOT internal AND NOT temporary"
            ).fetchone()[0]
            or 0
        )
    finally:
        conn.close()

    return {
        "schema_version": schema_version,
        "table_count": table_count,
        "critical_counts": critical_counts,
        "total_rows": total_rows,
        "file_size_bytes": int(os.path.getsize(canonical_path)),
    }


def _read_snapshot_manifest(snapshot_path: str) -> dict[str, Any] | None:
    conn = duckdb.connect(snapshot_path, read_only=True)
    try:
        try:
            raw = conn.execute(
                "SELECT manifest FROM terminal_snapshot_meta ORDER BY published_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            return None
        if raw is None:
            return None
        manifest = json.loads(raw[0]) if isinstance(raw[0], str) else raw[0]
        return manifest
    finally:
        conn.close()


def _assert_schema_compatible(manifest: dict[str, Any]) -> None:
    """Fail closed if the serving schema is incompatible with the publisher."""
    if manifest.get("publisher_version") != PUBLISHER_VERSION:
        raise ServingSnapshotError(
            "SERVING_SCHEMA_INCOMPATIBLE: snapshot publisher_version="
            f"{manifest.get('publisher_version')!r} vs expected {PUBLISHER_VERSION!r}"
        )
    missing = [
        f"{s}.{t}" for s, t in CRITICAL_TABLES
        if manifest.get("critical_table_counts", {}).get(f"{s}.{t}") is None
    ]
    if missing:
        raise ServingSnapshotError(
            f"SERVING_SCHEMA_INCOMPATIBLE: snapshot missing critical tables {missing}"
        )


def _verify_snapshot(snapshot_path: str, manifest: dict[str, Any]) -> None:
    """Reopen read-only and confirm the manifest contract actually holds."""
    conn = duckdb.connect(snapshot_path, read_only=True)
    try:
        stored = json.loads(
            conn.execute(
                "SELECT manifest FROM terminal_snapshot_meta WHERE snapshot_id = ?",
                [manifest["snapshot_id"]],
            ).fetchone()[0]
        )
        for key in ("snapshot_id", "status", "canonical_schema_version", "publisher_version"):
            if stored.get(key) != manifest.get(key):
                raise ServingSnapshotError(
                    f"SERVING_SNAPSHOT_CORRUPT: manifest mismatch on {key}"
                )
        for schema, table in CRITICAL_TABLES:
            exists = conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
                [schema, table],
            ).fetchone()[0]
            if not exists:
                raise ServingSnapshotError(
                    f"SERVING_SNAPSHOT_CORRUPT: missing critical table {schema}.{table}"
                )
    finally:
        conn.close()


def _split_statements(sql: str) -> list[str]:
    """Split a SQL file on ';' boundaries, dropping blanks/comments-only."""
    statements: list[str] = []
    current: list[str] = []
    for raw in sql.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        current.append(raw)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        statements.append("\n".join(current))
    return [s for s in statements if s.strip()]


def _now_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
