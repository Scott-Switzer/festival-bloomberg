"""PILOT 4 — Bloomberg Memray (Apache-2.0) dev-only profiler wrapper.

Memray profiles native+Python memory allocations. This wrapper runs the
largest artist-factor materialization (``run_security_master``) under memray
and reports peak memory + the top allocation sites.

It is DEV TOOLING ONLY: memray is never a runtime dependency. If memray is not
installed the wrapper reports SKIPPED (fail-closed) rather than failing or
installing anything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MATERIALIZATION_ENTRY = "festival_bloomberg.security.artist_security_master"


def _memray_available() -> bool:
    try:
        import memray  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def profile_materialization(
    *,
    db_path: str | None = None,
    universe_limit: int = 1000,
    output_dir: str | None = None,
    python: str | None = None,
) -> dict[str, Any]:
    """Run the materialization under memray and summarize the profile.

    ``db_path`` is an existing DuckDB warehouse (with migrations applied). The
    profile is written to ``output_dir / memray_materialization.bin``; a JSON
    summary is returned (never the raw binary in the report).
    """
    if not _memray_available():
        return {
            "status": "SKIPPED",
            "reason": "memray not installed — dev-only profiler, not a runtime dependency",
        }
    out_dir = Path(output_dir or "/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    import time

    profile_path = out_dir / f"memray_materialization_{int(time.time())}.bin"
    script = (
        f"from festival_bloomberg.security.artist_security_master import run_security_master; "
        f"import duckdb; c = duckdb.connect({db_path!r}); "
        f"r = run_security_master(c, universe_limit={universe_limit}); print(r)"
    )
    cmd = [
        python or sys.executable,
        "-m", "memray", "run",
        "--output", str(profile_path),
        "-c", script,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "reason": "materialization exceeded 30m"}
    if result.returncode != 0:
        return {
            "status": "ERROR",
            "reason": result.stderr[-500:],
            "profile_path": str(profile_path),
        }
    # Parse the memray stats JSON for the headline numbers.
    stats_path = out_dir / "memray_stats.json"
    stats_cmd = [
        python or sys.executable,
        "-m", "memray", "stats",
        "--json", str(profile_path),
    ]
    try:
        stats = subprocess.run(stats_cmd, capture_output=True, text=True, timeout=300)
        stats_payload = json.loads(stats.stdout) if stats.stdout else {}
    except (ValueError, subprocess.TimeoutExpired):
        stats_payload = {}
    return {
        "status": "COMPLETE",
        "profile_path": str(profile_path),
        "materialization_output": result.stdout[-300:],
        "stats": {
            "peak_memory_bytes": stats_payload.get("peak_memory_bytes"),
            "total_allocations": stats_payload.get("total_allocations"),
            "top_allocation_sites": (stats_payload.get("top_locations") or [])[:5],
        },
    }


def run_pilot(**kwargs: Any) -> dict[str, Any]:
    return profile_materialization(**kwargs)
