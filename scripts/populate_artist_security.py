"""OPEN_ARTIST_MARKET_DATA_V1 — bounded REAL population pass.

Builds a DuckDB warehouse from the R2 lake parquet, then runs the collectors
against live sources:

* ListenBrainz bulk totals + week/month ranges (key-free)
* Wikimedia historical daily pageviews (key-free) — bounded lookback by default
* YouTube channel snapshots (uses YOUTUBE_API_KEY from .env when present)
* Spotify catalog identity (uses SPOTIFY_CLIENT_ID/SECRET from .env when present)

then materializes the security master and reports honest coverage.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/populate_artist_security.py \
        --lake-dir /tmp/fi_lake \
        --warehouse /tmp/artist_security.duckdb \
        --universe-limit 200 \
        --wiki-lookback-days 120 \
        --min-interval 0.2

Bounded by design: no paid calls, YouTube/Spotify fail closed without keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.acquisition.transport import UrllibTransport  # noqa: E402
from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402
from festival_bloomberg.security.populate import run_population  # noqa: E402
from festival_bloomberg.localenv import load_local_env  # noqa: E402


def build_warehouse(conn, lake_dir: Path) -> None:
    """Load the lake parquet tables this pass needs into the warehouse."""
    apply_pending_migrations(conn)
    tables = {
        "core.artists": lake_dir / "artists.parquet",
        "core.entity_external_ids": lake_dir / "entity_external_ids.parquet",
        "metrics.artist_attention_observations": lake_dir / "artist_attention_observations.parquet",
        "core.event_performers": lake_dir / "event_performers.parquet",
    }
    for table, path in tables.items():
        if not path.exists():
            print(f"  (skip) {table}: {path.name} not present")
            continue
        schema, name = table.split(".")
        conn.execute(f"DELETE FROM {table}")
        conn.execute(f"INSERT INTO {table} SELECT * FROM read_parquet(?)", [str(path)])
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  loaded {table}: {n} rows")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake-dir", type=Path, default=Path("/tmp/fi_lake"))
    parser.add_argument("--warehouse", type=str, default="/tmp/artist_security.duckdb")
    parser.add_argument("--universe-limit", type=int, default=200)
    parser.add_argument("--wiki-lookback-days", type=int, default=120)
    parser.add_argument("--min-interval", type=float, default=0.2)
    parser.add_argument("--report", type=Path, default=Path("reports/artist_security_population_v1.json"))
    parser.add_argument(
        "--include-lb-ranges", action="store_true",
        help="also fetch per-artist ListenBrainz week/month/all_time ranges "
             "(~2-3s per range; OFF by default so bulk totals + wiki dominate)",
    )
    args = parser.parse_args()

    load_local_env()  # populates os.environ from repo .env (values never printed)
    youtube_key = os.environ.get("YOUTUBE_API_KEY")
    spotify_id = os.environ.get("SPOTIFY_CLIENT_ID")
    spotify_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")

    print(f"building warehouse at {args.warehouse}")
    conn = duckdb.connect(args.warehouse)
    try:
        build_warehouse(conn, args.lake_dir)
        print(f"selecting universe (limit {args.universe_limit})")
        report = run_population(
            conn,
            UrllibTransport(),
            universe_limit=args.universe_limit,
            wiki_lookback_days=args.wiki_lookback_days,
            youtube_api_key=youtube_key,
            spotify_client_id=spotify_id,
            spotify_client_secret=spotify_secret,
            min_interval_seconds=args.min_interval,
            include_lb_range_history=args.include_lb_ranges,
        )
        report["warehouse"] = args.warehouse
        report["lake_dir"] = str(args.lake_dir)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("\n=== POPULATION REPORT ===")
        print(json.dumps({
            "status": report["status"],
            "universe_size": report["universe_size"],
            "collectors": {
                k: {
                    "status": v.get("status"),
                    "rows_persisted": v.get("rows_persisted")
                    or v.get("daily_rows_persisted")
                    or v.get("artists_returned"),
                }
                for k, v in (report.get("collectors") or {}).items()
            },
            "materialization": report.get("materialization", {}).get("status"),
            "coverage": report.get("coverage"),
        }, indent=2, default=str))
        print(f"\nwrote {args.report}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
