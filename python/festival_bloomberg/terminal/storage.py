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

Snapshot lifecycle:
    BUILDING   → copy from canonical to temp file
    COPIED     → copy complete, workspace tables stripped
    VERIFIED   → all integrity checks passed (row counts, schema, critical tables)
    CURRENT    → atomically pointed at by CURRENT.json

A file that fails verification is NEVER promoted to VERIFIED and is cleaned up.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

SERVING_DIR = os.path.join(_PROJECT_ROOT, "data", "serving")
CURRENT_FILE = "CURRENT.json"
WORKSPACE_DEFAULT_DB = os.path.join(_PROJECT_ROOT, "data", "workspace", "terminal_workspace.duckdb")
_WORKSPACE_SCHEMA_SQL = os.path.join(_PROJECT_ROOT, "schema", "workspace_schema.sql")

PUBLISHER_VERSION = "terminal_serving_snapshot_v1"

# Snapshot status lifecycle.
SNAPSHOT_STATUS_BUILDING = "BUILDING"
SNAPSHOT_STATUS_COPIED = "COPIED"
SNAPSHOT_STATUS_VERIFIED = "VERIFIED"

# Tables that hold MUTABLE USER STATE (must be stripped from serving snapshots).
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
    ("planning", "show_economics_scenarios"),
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

# Explicit column mappings for workspace migration.  Each table maps to the
# columns that should be copied from canonical → workspace, preventing silent
# positional-cast drift if schemas evolve independently.
_MIGRATION_COLUMNS: dict[tuple[str, str], list[str]] = {
    ("core", "watchlists"): [
        "watchlist_key", "name", "description", "entity_type",
        "is_system", "created_at", "updated_at",
    ],
    ("core", "watchlist_items"): [
        "item_key", "watchlist_key", "entity_type", "entity_key",
        "entity_name", "notes", "tags", "added_at", "removed_at", "source_system",
    ],
    ("terminal", "saved_monitors"): [
        "monitor_key", "name", "entity_type", "watchlist_key",
        "filters", "visible_columns", "sort", "time_horizon",
        "created_at", "updated_at",
    ],
    ("planning", "festival_projects"): [
        "project_key", "name", "city", "market", "venue_site",
        "start_date", "end_date", "num_days", "num_stages",
        "talent_budget_usd", "genre_objectives", "target_audience",
        "min_billing_tier", "max_billing_tier", "notes",
        "scenario_class", "is_official", "created_at", "updated_at",
    ],
    ("planning", "festival_project_stages"): [
        "stage_key", "project_key", "stage_name", "capacity_claim",
        "capacity_evidence_class", "indoor_outdoor", "created_at",
    ],
    ("planning", "festival_candidate_artists"): [
        "candidate_key", "project_key", "artist_key", "artist_name",
        "musicbrainz_id", "inclusion_reasons", "availability_status",
        "availability_evidence", "scorecard_snapshot", "added_at", "updated_at",
    ],
    ("planning", "festival_shortlists"): [
        "shortlist_key", "project_key", "artist_key", "artist_name",
        "status", "candidate_day", "candidate_stage", "candidate_billing_tier",
        "notes", "evidence_snapshot", "created_at", "updated_at",
    ],
    ("planning", "festival_constraints"): [
        "constraint_key", "project_key", "constraint_type",
        "description", "payload", "source", "created_at",
    ],
    ("planning", "festival_scenarios"): [
        "scenario_key", "project_key", "name", "notes",
        "slots", "warnings", "summaries", "created_at", "updated_at",
    ],
    ("planning", "show_economics_scenarios"): [
        "scenario_key", "project_key", "name", "currency", "engine_version",
        "inputs", "derived_outputs", "created_at", "updated_at",
    ],
}


class ServingSnapshotError(RuntimeError):
    """A serving snapshot is missing, corrupt, or schema-incompatible."""


def publish_snapshot(
    canonical_path: str,
    snapshot_dir: str = SERVING_DIR,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Publish a NEW immutable serving snapshot from canonical.

    Lifecycle:
        1. COPY FROM DATABASE canonical → temp file   (status = BUILDING)
        2. Drop workspace tables from temp file       (status = COPIED)
        3. Verify row counts + schema                 (status = VERIFIED)
        4. Checkpoint + rename to immutable final path
        5. Atomically replace CURRENT.json pointer

    If verification fails, the temp file is deleted and no pointer is updated.
    """
    snapshot_id = snapshot_id or f"terminal_snapshot_{_now_stamp()}"
    final_path = os.path.join(snapshot_dir, f"{snapshot_id}.duckdb")
    if os.path.exists(final_path):
        raise FileExistsError(f"refusing to overwrite existing snapshot: {final_path}")
    os.makedirs(snapshot_dir, exist_ok=True)

    # Temp path: terminal_snapshot_<id>.<uuid>.tmp.duckdb
    import uuid
    tmp_path = os.path.join(snapshot_dir, f".{snapshot_id}.{uuid.uuid4().hex[:8]}.tmp.duckdb")

    canonical_meta = _read_canonical_meta(canonical_path)

    manifest: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "status": SNAPSHOT_STATUS_BUILDING,
        "published_at": _now_stamp(),
        "canonical_source_path": str(Path(canonical_path).resolve()),
        "canonical_schema_version": canonical_meta["schema_version"],
        "serving_schema_version": canonical_meta["schema_version"],
        "publisher_version": PUBLISHER_VERSION,
        "source_file_size_bytes": canonical_meta["file_size_bytes"],
        "table_count": canonical_meta["table_count"],
        "estimated_total_rows": canonical_meta["estimated_total_rows"],
        "critical_table_counts": canonical_meta["critical_counts"],
    }

    try:
        # ── Phase 1: COPY from canonical to temp file ──────────────────
        _attach_and_copy(canonical_path, tmp_path)
        manifest["status"] = SNAPSHOT_STATUS_COPIED
        logger.info("publish_snapshot: copied %s → %s", snapshot_id, tmp_path)

        # ── Phase 2: Strip workspace tables from serving artifact ──────
        _strip_workspace_tables(tmp_path)
        logger.info("publish_snapshot: stripped workspace tables from %s", snapshot_id)

        # ── Phase 3: Verify integrity ──────────────────────────────────
        _write_manifest_to_snapshot(tmp_path, manifest)
        manifest["status"] = SNAPSHOT_STATUS_VERIFIED
        _update_manifest_in_snapshot(tmp_path, manifest)
        _verify_snapshot(tmp_path, manifest)
        logger.info("publish_snapshot: verified %s", snapshot_id)

        # ── Phase 4: Checkpoint and rename to immutable final path ─────
        _checkpoint_and_close(tmp_path)
        os.rename(tmp_path, final_path)

        # ── Phase 5: Atomically publish the CURRENT pointer ────────────
        _publish_current_pointer(snapshot_dir, snapshot_id, final_path)

    except Exception:
        # On ANY failure: clean up temp file, do NOT touch final_path.
        _safe_remove(tmp_path)
        raise

    manifest["snapshot_path"] = final_path
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

    Validates:
        - manifest exists
        - status == VERIFIED
        - publisher/schema version compatible
        - critical tables exist
        - critical table actual counts match manifest recorded counts

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

    # Validate critical table row counts (not just existence).
    _validate_critical_row_counts(path, manifest)

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
    try:
        with open(_WORKSPACE_SCHEMA_SQL, encoding="utf-8") as fh:
            for statement in _split_statements(fh.read()):
                conn.execute(statement)
        # Ensure created_at is stable (INSERT OR IGNORE) — never overwrite on reopen.
        _ensure_workspace_meta(conn)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def migrate_workspace_state(canonical_path: str, workspace_conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """One-time, idempotent import of existing user state into workspace.

    Uses explicit column mappings per table to prevent silent positional-cast
    drift.  Canonical history is NOT deleted.  Fails with a clear error if
    canonical/workspace schemas are incompatible.
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
            qualified = f"{schema}.{table}"
            if (schema, table) not in canonical_tables:
                migrated[qualified] = 0
                continue

            columns = _MIGRATION_COLUMNS.get((schema, table))
            if columns is None:
                raise ServingSnapshotError(
                    f"WORKSPACE_MIGRATION_INCOMPATIBLE: no column mapping for {qualified}"
                )

            # Verify canonical actually has these columns.
            canonical_cols = {
                r[0] for r in workspace_conn.execute(
                    f"SELECT column_name FROM duckdb_columns() "
                    f"WHERE database_name = 'canonical' AND schema_name = ? AND table_name = ?",
                    [schema, table],
                ).fetchall()
            }
            missing_cols = [c for c in columns if c not in canonical_cols]
            if missing_cols:
                raise ServingSnapshotError(
                    f"WORKSPACE_MIGRATION_INCOMPATIBLE: {qualified} missing columns "
                    f"{missing_cols} in canonical"
                )

            col_list = ", ".join(f'"{c}"' for c in columns)
            workspace_conn.execute(
                f'INSERT OR IGNORE INTO "{schema}"."{table}" ({col_list}) '
                f'SELECT {col_list} FROM canonical."{schema}"."{table}"'
            )
            migrated[qualified] = int(
                workspace_conn.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
            )

        # Record migration metadata (created_at stays stable).
        workspace_conn.execute(
            "INSERT OR REPLACE INTO workspace_meta (key, value) VALUES ('last_migrated_at', ?)",
            [_now_stamp()],
        )
        workspace_conn.commit()
    finally:
        try:
            workspace_conn.execute("DETACH canonical")
        except Exception:
            pass
    return migrated


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _attach_and_copy(canonical_path: str, dest_path: str) -> None:
    """COPY FROM DATABASE canonical → dest with safe DETACH in finally."""
    conn = duckdb.connect()
    try:
        conn.execute(f"ATTACH '{canonical_path}' AS canonical (READ_ONLY)")
        conn.execute(f"ATTACH '{dest_path}' AS dest")
        conn.execute("COPY FROM DATABASE canonical TO dest")
        conn.execute("DETACH dest")
        conn.execute("DETACH canonical")
    finally:
        conn.close()


def _strip_workspace_tables(db_path: str) -> None:
    """Drop all workspace/user-state tables from a snapshot after copy.

    COPY FROM DATABASE copies everything from canonical, including deprecated
    user-state tables.  These must not exist in a serving snapshot.
    """
    conn = duckdb.connect(db_path)
    try:
        existing = {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT schema_name, table_name FROM duckdb_tables() "
                "WHERE NOT internal AND NOT temporary"
            ).fetchall()
        }
        for schema, table in WORKSPACE_TABLES:
            if (schema, table) in existing:
                conn.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}"')
                logger.info("stripped %s.%s from serving snapshot", schema, table)
        conn.commit()
    finally:
        conn.close()


def _write_manifest_to_snapshot(db_path: str, manifest: dict[str, Any]) -> None:
    """Write the manifest into the snapshot database for embedded validation."""
    conn = duckdb.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS terminal_snapshot_meta "
            "(snapshot_id VARCHAR PRIMARY KEY, published_at TIMESTAMP, manifest JSON)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO terminal_snapshot_meta (snapshot_id, published_at, manifest) "
            "VALUES (?, now(), ?)",
            [manifest["snapshot_id"], json.dumps(manifest)],
        )
        conn.commit()
    finally:
        conn.close()


def _update_manifest_in_snapshot(db_path: str, manifest: dict[str, Any]) -> None:
    """Update the manifest row (e.g. status transition to VERIFIED)."""
    conn = duckdb.connect(db_path)
    try:
        conn.execute(
            "UPDATE terminal_snapshot_meta SET manifest = ? WHERE snapshot_id = ?",
            [json.dumps(manifest), manifest["snapshot_id"]],
        )
        conn.commit()
    finally:
        conn.close()


def _checkpoint_and_close(db_path: str) -> None:
    """CHECKPOINT a database file and close it, ensuring data is flushed to disk."""
    conn = duckdb.connect(db_path)
    try:
        conn.execute("CHECKPOINT")
    finally:
        conn.close()


def _safe_remove(path: str) -> None:
    """Best-effort removal of a file, ignoring errors."""
    try:
        if os.path.exists(path):
            os.remove(path)
            # Also remove any WAL/SIDECAR files that DuckDB may have created.
            for suffix in (".wal", ".tmp"):
                sidecar = path + suffix
                if os.path.exists(sidecar):
                    os.remove(sidecar)
    except OSError as e:
        logger.warning("failed to clean up temp snapshot %s: %s", path, e)


def _publish_current_pointer(snapshot_dir: str, snapshot_id: str, final_path: str) -> None:
    """Atomically replace CURRENT.json to point at the new snapshot."""
    current_path = os.path.join(snapshot_dir, CURRENT_FILE)
    tmp_path = current_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"snapshot_id": snapshot_id, "snapshot_file": os.path.basename(final_path)},
            fh,
        )
    os.replace(tmp_path, current_path)


def _validate_critical_row_counts(snapshot_path: str, manifest: dict[str, Any]) -> None:
    """Verify that critical table row counts in the snapshot match the manifest."""
    expected_counts = manifest.get("critical_table_counts", {})
    if not expected_counts:
        return  # No counts recorded — skip (backwards compat).

    conn = duckdb.connect(snapshot_path, read_only=True)
    try:
        for schema, table in CRITICAL_TABLES:
            qualified = f"{schema}.{table}"
            expected = expected_counts.get(qualified)
            if expected is None:
                continue  # Not recorded in manifest (backwards compat).
            actual = int(
                conn.execute(
                    f'SELECT COUNT(*) FROM "{schema}"."{table}"'
                ).fetchone()[0]
            )
            if actual != expected:
                raise ServingSnapshotError(
                    f"SERVING_SNAPSHOT_ROW_COUNT_MISMATCH: {qualified} "
                    f"expected={expected} actual={actual}"
                )
    finally:
        conn.close()


def _ensure_workspace_meta(conn: duckdb.DuckDBPyConnection) -> None:
    """Ensure workspace_meta has stable created_at and schema_version.

    created_at is written with INSERT OR IGNORE so it never overwrites on
    repeated terminal starts.  schema_version is always set.  last_migrated_at
    is set by migrate_workspace_state().
    """
    conn.execute(
        "INSERT OR IGNORE INTO workspace_meta (key, value) "
        "VALUES ('created_at', CURRENT_TIMESTAMP::VARCHAR)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO workspace_meta (key, value) "
        "VALUES ('schema_version', 'terminal_workspace_v2')"
    )


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
        # DuckDB's estimated_size is an estimated row count, not exact.
        estimated_total_rows = int(
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
        "estimated_total_rows": estimated_total_rows,
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
    """Reopen read-only and confirm the manifest contract actually holds.

    Checks:
        - embedded manifest matches the manifest we wrote
        - all critical tables exist
        - critical table row counts match recorded counts
    """
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
        # Verify critical tables exist AND row counts match.
        expected_counts = manifest.get("critical_table_counts", {})
        for schema, table in CRITICAL_TABLES:
            qualified = f"{schema}.{table}"
            exists = conn.execute(
                "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
                [schema, table],
            ).fetchone()[0]
            if not exists:
                raise ServingSnapshotError(
                    f"SERVING_SNAPSHOT_CORRUPT: missing critical table {qualified}"
                )
            # Verify row counts.
            expected = expected_counts.get(qualified)
            if expected is not None:
                actual = int(
                    conn.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]
                )
                if actual != expected:
                    raise ServingSnapshotError(
                        f"SERVING_SNAPSHOT_ROW_COUNT_MISMATCH: {qualified} "
                        f"expected={expected} actual={actual}"
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
