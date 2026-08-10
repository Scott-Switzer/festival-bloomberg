"""
Warehouse path resolution for festival-bloomberg.

CI failures in the mis-targeted festival-intelligence attempts commonly stemmed
from looking up `festival_intelligence.duckdb` / `FESTIVAL_INTELLIGENCE_DUCKDB_PATH`
instead of the bloomberg warehouse path. This module is the single source of
truth for local DuckDB file location.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_WAREHOUSE_PATH = "data/warehouse/festival_bloomberg.duckdb"
WAREHOUSE_ENV_VAR = "FESTIVAL_BLOOMBERG_DUCKDB_PATH"

# Legacy / wrong-repo env vars seen in failed intelligence CI runs.
_LEGACY_ENV_VARS = (
    "FESTIVAL_INTELLIGENCE_DUCKDB_PATH",
    "FESTIVAL_INTEL_DUCKDB_PATH",
)

_LEGACY_BASENAMES = {
    "festival_intelligence.duckdb",
    "festival-intelligence.duckdb",
}


def _repo_root() -> Path:
    """Locate repository root (directory containing package.json)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "package.json").is_file():
            return parent
    # Fallback: python/festival_bloomberg -> python -> repo
    return here.parents[2]


def resolve_warehouse_path(
    explicit: str | os.PathLike[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    create_parent: bool = False,
) -> Path:
    """
    Resolve the DuckDB warehouse path.

    Priority:
      1. explicit argument
      2. FESTIVAL_BLOOMBERG_DUCKDB_PATH
      3. legacy intelligence env vars (remapped basename -> festival_bloomberg.duckdb)
      4. DEFAULT_WAREHOUSE_PATH under the repo root when relative

    Relative paths are resolved against the repository root (not CWD) so CI
    jobs that start in subdirectories still find the same warehouse file.
    """
    environ = env if env is not None else os.environ
    raw: str | None = None
    remapped_from_legacy = False

    if explicit is not None:
        raw = str(explicit)
    elif environ.get(WAREHOUSE_ENV_VAR):
        raw = environ[WAREHOUSE_ENV_VAR]
    else:
        for key in _LEGACY_ENV_VARS:
            if environ.get(key):
                raw = environ[key]
                remapped_from_legacy = True
                break

    if not raw:
        raw = DEFAULT_WAREHOUSE_PATH

    path = Path(raw).expanduser()
    if remapped_from_legacy or path.name.lower() in _LEGACY_BASENAMES:
        # Keep the directory, force the bloomberg filename.
        path = path.with_name(Path(DEFAULT_WAREHOUSE_PATH).name)

    if not path.is_absolute():
        path = (_repo_root() / path).resolve()
    else:
        path = path.resolve()

    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    return path
