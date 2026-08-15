"""Schema path resolution and migration runner tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.schema_paths import load_schema_sql, resolve_schema_root


def test_resolve_schema_root_finds_repo_schema():
    root = resolve_schema_root()
    assert (root / "duckdb.sql").is_file()
    assert (root / "migrations" / "002_published_at_point_in_time_v2.sql").is_file()
    assert "published_at" in load_schema_sql()


def test_apply_pending_migrations_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "migrations.duckdb"
    import duckdb

    connection = duckdb.connect(str(db_path))
    try:
        assert apply_pending_migrations(connection) == 27
        assert apply_pending_migrations(connection) == 0
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'metrics'
                  AND table_name IN (
                    'artist_attention_observations',
                    'edition_analytical_metrics'
                  )
                """
            ).fetchall()
        }
        assert tables == {
            "artist_attention_observations",
            "edition_analytical_metrics",
        }
        canonical_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                  AND table_name IN (
                    'canonical_entities',
                    'canonical_entity_aliases',
                    'canonical_entity_source_ids',
                    'canonical_entity_provenance',
                    'entity_resolution_reviews'
                  )
                """
            ).fetchall()
        }
        assert canonical_tables == {
            "canonical_entities",
            "canonical_entity_aliases",
            "canonical_entity_source_ids",
            "canonical_entity_provenance",
            "entity_resolution_reviews",
        }
    finally:
        connection.close()


def test_schema_loads_outside_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Packaged / copied schema trees work when cwd is not the repository root."""
    import shutil

    schema_root = tmp_path / "pkg" / "schema"
    shutil.copytree(resolve_schema_root(), schema_root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    from festival_bloomberg import schema_paths

    monkeypatch.setattr(
        schema_paths,
        "_schema_roots",
        lambda: [schema_root],
    )
    assert "published_at" in schema_paths.load_schema_sql()
