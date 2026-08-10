"""Tests for warehouse path resolution (bloomberg vs legacy intelligence names)."""

from __future__ import annotations

from pathlib import Path

from festival_bloomberg.paths import (
    DEFAULT_WAREHOUSE_PATH,
    WAREHOUSE_ENV_VAR,
    resolve_warehouse_path,
)


def test_default_path_is_bloomberg_under_repo_root():
    path = resolve_warehouse_path(env={})
    assert path.name == "festival_bloomberg.duckdb"
    assert path.as_posix().endswith(DEFAULT_WAREHOUSE_PATH)
    assert path.is_absolute()


def test_explicit_path_wins(tmp_path: Path):
    target = tmp_path / "custom.duckdb"
    path = resolve_warehouse_path(target, env={})
    assert path == target.resolve()


def test_bloomberg_env_var(tmp_path: Path):
    target = tmp_path / "from-env.duckdb"
    path = resolve_warehouse_path(
        env={WAREHOUSE_ENV_VAR: str(target)},
    )
    assert path == target.resolve()


def test_legacy_intelligence_env_remaps_basename(tmp_path: Path):
    legacy = tmp_path / "warehouse" / "festival_intelligence.duckdb"
    path = resolve_warehouse_path(
        env={"FESTIVAL_INTELLIGENCE_DUCKDB_PATH": str(legacy)},
    )
    assert path.name == "festival_bloomberg.duckdb"
    assert path.parent == legacy.parent.resolve()


def test_legacy_basename_in_explicit_path_remapped(tmp_path: Path):
    legacy = tmp_path / "festival_intelligence.duckdb"
    path = resolve_warehouse_path(legacy, env={})
    assert path.name == "festival_bloomberg.duckdb"


def test_create_parent(tmp_path: Path):
    target = tmp_path / "nested" / "a" / "warehouse.duckdb"
    path = resolve_warehouse_path(target, env={}, create_parent=True)
    assert path.parent.is_dir()
