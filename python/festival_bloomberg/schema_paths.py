"""Resolve packaged or checkout-local DuckDB schema files."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

MIGRATION_FILE_PATTERN = re.compile(r"^(\d+)_(.+)\.sql$")


def _schema_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        package_root = Path(str(resources.files("festival_bloomberg")))
        candidate = package_root / "schema"
        if (candidate / "duckdb.sql").is_file():
            roots.append(candidate)
    except (TypeError, FileNotFoundError, ModuleNotFoundError):
        pass

    module_dir = Path(__file__).resolve().parent
    for parent in [module_dir, *module_dir.parents]:
        candidate = parent / "schema"
        if (candidate / "duckdb.sql").is_file():
            roots.append(candidate)

    cwd_candidate = Path.cwd() / "schema"
    if (cwd_candidate / "duckdb.sql").is_file():
        roots.append(cwd_candidate)

    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def resolve_schema_root() -> Path:
    roots = _schema_roots()
    if not roots:
        raise FileNotFoundError("Canonical DuckDB schema not found (schema/duckdb.sql)")
    return roots[0]


def load_schema_sql() -> str:
    return (resolve_schema_root() / "duckdb.sql").read_text(encoding="utf-8")


def split_sql_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def load_migration_files() -> list[tuple[int, str, Path]]:
    migration_dir = resolve_schema_root() / "migrations"
    if not migration_dir.is_dir():
        raise FileNotFoundError(f"DuckDB migrations directory not found: {migration_dir}")

    migrations: list[tuple[int, str, Path]] = []
    for path in sorted(migration_dir.glob("*.sql")):
        match = MIGRATION_FILE_PATTERN.match(path.name)
        if not match:
            raise ValueError(f"Invalid migration filename: {path.name}")
        migrations.append((int(match.group(1)), match.group(2), path))

    for index in range(1, len(migrations)):
        if migrations[index][0] <= migrations[index - 1][0]:
            raise ValueError("Migration versions must be strictly increasing")
    return migrations
