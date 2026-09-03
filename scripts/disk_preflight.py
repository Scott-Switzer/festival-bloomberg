#!/usr/bin/env python3
"""Fail-fast disk preflight for local DuckDB, tests, and profiling jobs.

The project deliberately keeps large canonical data outside normal worktrees.
This command only inspects filesystem availability and optional paths; it does
not create a virtualenv, download dependencies, open DuckDB, or delete files.

Examples:
    PYTHONPATH=python python scripts/disk_preflight.py
    python scripts/disk_preflight.py --path /tmp --minimum-free-gib 2
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

DEFAULT_MINIMUM_FREE_GIB = 2.0
DEFAULT_RESERVED_ARTIFACT_GIB = 0.5


def _usage(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_gib": round(usage.free / (1024**3), 3),
    }


def preflight(
    *,
    paths: list[str | os.PathLike[str]] | None = None,
    minimum_free_gib: float = DEFAULT_MINIMUM_FREE_GIB,
    reserved_artifact_gib: float = DEFAULT_RESERVED_ARTIFACT_GIB,
) -> dict[str, Any]:
    """Return a machine-readable disk decision without changing files."""
    if minimum_free_gib < 0 or reserved_artifact_gib < 0:
        raise ValueError("disk thresholds must be non-negative")
    requested = paths or [os.getcwd(), "/tmp"]
    checked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in requested:
        path = Path(raw).expanduser().resolve()
        # disk_usage accepts an existing ancestor; resolve the nearest one so
        # a prospective output directory does not need to exist yet.
        candidate = path
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        checked.append(_usage(candidate))
    threshold = minimum_free_gib + reserved_artifact_gib
    failures = [item for item in checked if item["free_gib"] < threshold]
    return {
        "status": "PASS" if not failures else "INSUFFICIENT_DISK",
        "minimum_free_gib": minimum_free_gib,
        "reserved_artifact_gib": reserved_artifact_gib,
        "required_free_gib": threshold,
        "checked": checked,
        "message": (
            "enough free space for bounded local work"
            if not failures
            else "free space is below the requested test/artifact reserve; stop before opening DuckDB"
        ),
        "artifacts_policy": {
            "canonical_warehouse": "external/non-worktree",
            "virtualenvs": "reuse a shared environment; do not create one per worktree",
            "profiling": "write bounded reports to an explicitly supplied output directory",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path", action="append", dest="paths", help="Filesystem path to inspect (repeatable)."
    )
    parser.add_argument("--minimum-free-gib", type=float, default=DEFAULT_MINIMUM_FREE_GIB)
    parser.add_argument(
        "--reserved-artifact-gib", type=float, default=DEFAULT_RESERVED_ARTIFACT_GIB
    )
    args = parser.parse_args()
    result = preflight(
        paths=args.paths,
        minimum_free_gib=args.minimum_free_gib,
        reserved_artifact_gib=args.reserved_artifact_gib,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
