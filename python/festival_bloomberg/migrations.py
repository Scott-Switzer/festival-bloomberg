"""Versioned DuckDB migrations for the Python warehouse client."""

from __future__ import annotations

import duckdb

from .schema_paths import load_migration_files, load_schema_sql, split_sql_statements


def _applied_versions(connection: duckdb.DuckDBPyConnection) -> set[int]:
    rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return {int(row[0]) for row in rows}


def apply_pending_migrations(connection: duckdb.DuckDBPyConnection) -> int:
    """Apply base schema and pending migrations; return count applied this call."""
    for statement in split_sql_statements(load_schema_sql()):
        connection.execute(statement)

    applied = _applied_versions(connection)
    applied_now = 0

    for version, name, path in load_migration_files():
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            connection.execute("BEGIN TRANSACTION")
            for statement in split_sql_statements(sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                [version, name],
            )
            connection.execute("COMMIT")
            applied.add(version)
            applied_now += 1
        except Exception:
            connection.execute("ROLLBACK")
            raise

    return applied_now
