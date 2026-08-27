"""Final orchestration for ARTIST_SECURITY_1000_SCALE_V1.

Runs after both background collectors complete:
  1. wiki backfill  (writes raw wiki rows into the main warehouse)
  2. stage pass     (writes LB/identity/Spotify/YT/SetlistFM/tape/market into
                    the staging warehouse)

Steps:
  1. Merge the staging warehouse into the main warehouse
     (scripts/merge_stage_into_main.py) — idempotent, by natural key.
  2. Run the final populate pass against the MAIN warehouse with all network
     collection stages SKIPPED (their rows were merged). This runs the
     remaining local stages: identity master refresh (idempotent), feast
     adoption (P6), materialization, Perspective monitor (P7), Voyager status
     (P8), and the SUCCESS REPORT.

    PYTHONPATH=python .venv/bin/python scripts/finalize_artist_security_1000.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="/tmp/artist_security_stage.duckdb")
    parser.add_argument("--main", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--report", default="reports/artist_security_1000_success.json")
    parser.add_argument("--min-interval", type=float, default=0.22)
    args = parser.parse_args()

    env_py = ROOT / ".venv" / "bin" / "python"
    py = str(env_py if env_py.exists() else sys.executable)

    print("=== 1/2 merge stage -> main ===")
    merge = subprocess.run(
        [
            py, str(ROOT / "scripts" / "merge_stage_into_main.py"),
            "--stage", args.stage, "--main", args.main,
        ],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "python")},
    )
    if merge.returncode != 0:
        print("MERGE FAILED", flush=True)
        sys.exit(merge.returncode)

    print("=== 2/2 final populate pass (local stages only) ===")
    populate = subprocess.run(
        [
            py, str(ROOT / "scripts" / "populate_artist_security_1000.py"),
            "--warehouse", args.main,
            "--report", str(args.report),
            "--skip-wiki", "--skip-lb", "--skip-spotify", "--skip-yt", "--skip-setlist",
            "--min-interval", str(args.min_interval),
        ],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "python")},
    )
    if populate.returncode != 0:
        print("POPULATE FAILED", flush=True)
        sys.exit(populate.returncode)

    print(f"\nDONE — report at {args.report}")


if __name__ == "__main__":
    main()
