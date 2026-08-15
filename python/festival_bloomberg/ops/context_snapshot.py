"""Context snapshot for coding/research agents.

Run ``python -m festival_bloomberg.ops.context_snapshot`` before planning any
change. It reports, WITHOUT secret values:

- git: repo, branch, SHA, dirty/clean
- database: path, applied migrations, schema count, table count
- entity counts across the warehouse
- provider credential PRESENCE (configured? — never the value)
- research state (accepted verdicts / PIT readiness markers)
- product state (terminal endpoints / ASK model status)

This is the anti-amnesia entrypoint referenced by AGENTS.md.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..config import provider_credential_status
from ..localenv import load_local_env

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = "data/warehouse/boxoffice_research_v2.duckdb"


def _git() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
        except Exception:
            return ""

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    sha = run("rev-parse", "HEAD")
    dirty = run("status", "--porcelain")
    return {
        "repo": "Scott-Switzer/festival-bloomberg",
        "branch": branch,
        "sha": sha,
        "dirty": bool(dirty),
        "changed_files": len(dirty.splitlines()) if dirty else 0,
    }


def _database(db_path: str) -> dict[str, Any]:
    import duckdb
    info: dict[str, Any] = {"path": db_path}
    try:
        conn = duckdb.connect(db_path, read_only=True)
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}"
        return info
    try:
        info["migrations_applied"] = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
        info["tables"] = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','pg_catalog')"
        ).fetchone()[0]
    except Exception:  # noqa: BLE001
        pass
    finally:
        conn.close()
    return info


def _counts(db_path: str) -> dict[str, Any]:
    import duckdb
    try:
        conn = duckdb.connect(db_path, read_only=True)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, Any] = {}
    queries = {
        "artists": "SELECT COUNT(DISTINCT artist) FROM research.canonical_boxoffice_engagements",
        "historical_engagements": "SELECT COUNT(*) FROM research.canonical_boxoffice_engagements",
        "venues": "SELECT COUNT(DISTINCT venue) FROM research.canonical_boxoffice_engagements",
        "markets": "SELECT COUNT(DISTINCT city) FROM research.canonical_boxoffice_engagements",
        "forward_events": "SELECT COUNT(*) FROM flywheel.forward_watch_events",
        "outcome_claims": "SELECT COUNT(*) FROM economics.event_outcome_claims",
        "festivals": "SELECT COUNT(*) FROM core.festivals",
        "festival_editions": "SELECT COUNT(*) FROM core.festival_editions",
        "festival_lineup_slots": "SELECT COUNT(*) FROM core.lineup_slots",
        "festival_billing_observations": "SELECT COUNT(*) FROM core.festival_billing_observations",
        "activity_tape": "SELECT COUNT(*) FROM terminal.activity_tape",
    }
    for key, sql in queries.items():
        try:
            out[key] = int(conn.execute(sql).fetchone()[0])
        except Exception:  # noqa: BLE001
            out[key] = None
    conn.close()
    return out


def build_snapshot(db_path: str = DEFAULT_DB) -> dict[str, Any]:
    load_local_env()
    creds = provider_credential_status()
    return {
        "git": _git(),
        "database": _database(db_path),
        "entities": _counts(db_path),
        "providers": {
            k: {"configured": v["nonempty_any"]} for k, v in creds.items()
        },
        "research": {
            "baseline_verdict": "COMPS_SIGNAL_ONLY",
            "strict_pit_warm_start": "0 (result availability != pre-event knowability)",
            "pre_event_cutoffs": "ANNOUNCEMENT/ONSALE/BOOKING ~0 in public corpus",
        },
        "product": {
            "terminal_endpoints": ["/api/search", "/api/tape", "/api/sources",
                                   "/api/festivals", "/api/artists/{id}",
                                   "/api/events/{id}", "/api/venues/{id}",
                                   "/api/markets/{id}", "/api/ask"],
            "ask_provider": "NVIDIA NIM (fail-closed) + deterministic fallback",
        },
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Festival Intelligence context snapshot")
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()
    print(json.dumps(build_snapshot(args.db), indent=2, default=str))


if __name__ == "__main__":
    main()
