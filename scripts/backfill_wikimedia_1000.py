"""ARTIST_SECURITY_1000_SCALE_V1 — P0: full Wikimedia daily-pageviews backfill.

Backfills the FULL valid series (2015-07-01 → latest complete observation day)
for every artist in the 1000-security universe. NOT truncated to 120 days.

Design:
- Universe comes from ``select_security_universe(conn, limit=1000)``.
- The existing ``collect_artist_daily_pageviews`` writes one observation row
  per (artist, day) with the full PIT contract (observation day, available_at,
  retrieved_at, raw evidence reference, rights/commercial state).
- Idempotent observation keys mean re-running resumes without duplicate rows
  (a killed run can be restarted; already-persisted days are skipped).
- ``--start`` defaults to the Wikimedia series start (2015-07-01).

Run (background):
    PYTHONPATH=python .venv/bin/python scripts/backfill_wikimedia_1000.py \
        --warehouse /tmp/artist_security_1000.duckdb --min-interval 0.25 \
        > /tmp/wiki_backfill.log 2>&1 &
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.acquisition.transport import UrllibTransport  # noqa: E402
from festival_bloomberg.attention.wikimedia_historical import collect_artist_daily_pageviews_batched  # noqa: E402
from festival_bloomberg.localenv import load_local_env  # noqa: E402
from festival_bloomberg.security.artist_security_master import select_security_universe  # noqa: E402

WIKIMEDIA_SERIES_START = "2015-07-01"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--universe-limit", type=int, default=1000)
    parser.add_argument("--start", default=WIKIMEDIA_SERIES_START)
    parser.add_argument("--end", default=None, help="YYYY-MM-DD; default = yesterday UTC")
    parser.add_argument("--min-interval", type=float, default=0.25)
    parser.add_argument("--chunk-days", type=int, default=400)
    parser.add_argument("--report", default="reports/artist_security_1000_wikimedia_backfill.json")
    args = parser.parse_args()

    load_local_env()
    conn = duckdb.connect(args.warehouse)
    try:
        universe = select_security_universe(conn, limit=args.universe_limit)
        names = [a.get("artist_name") or a["artist_key"] for a in universe]
        key_map = {a.get("artist_name") or a["artist_key"]: a["artist_key"] for a in universe}
        # ALSO map by normalized name (collector falls back to name:: keys
        # otherwise, which would split the tape).
        for a in universe:
            key_map[a.get("artist_name", "").strip().lower()] = a["artist_key"]
        print(f"universe: {len(universe)} artists; backfilling from {args.start}")
        sys.stdout.flush()

        summary = collect_artist_daily_pageviews_batched(
            conn,
            UrllibTransport(),
            names=names,
            start=args.start,
            end=args.end,
            chunk_days=args.chunk_days,
            min_interval_seconds=args.min_interval,
            artist_keys_by_name=key_map,
        )
        import json

        args.report and Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps({k: v for k, v in summary.items() if k != "per_artist"}, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps({k: v for k, v in summary.items() if k != "per_artist"}, indent=2, default=str))
        print(f"\nwrote {args.report}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
