"""Merge the staging warehouse's non-wiki collection into the main warehouse.

The Wikimedia backfill holds the write lock on the main scale warehouse, so the
other stages (LB, identity, Spotify, YouTube, SetlistFM, event tape, artist ×
market) ran against a parallel staging warehouse. Once the backfill completes
and releases the lock, this script copies those rows into the main warehouse.

Every target table has a PRIMARY KEY / natural key, so the merge is an
INSERT OR REPLACE / ON CONFLICT upsert — idempotent, re-runnable, and it
preserves rows already present in the main warehouse (e.g. wiki rows).

    PYTHONPATH=python .venv/bin/python scripts/merge_stage_into_main.py \
        --stage /tmp/artist_security_stage.duckdb \
        --main /tmp/artist_security_1000.duckdb
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402

# (table, pk_column) — merge by INSERT OR REPLACE on the natural key.
TABLES: list[tuple[str, str]] = [
    ("identity.artist_provider_linkages", "linkage_key"),
    ("identity.spotify_artist_resolutions", "resolution_key"),
    ("raw.musicbrainz_event", "mbid"),
    ("core.series_events", "series_event_key"),
    ("metrics.artist_attention_observations", "observation_key"),
    ("metrics.artist_factor_observations", "factor_observation_key"),
    ("metrics.artist_performance_observations", "performance_key"),
    ("metrics.artist_live_statistics", "stat_key"),
    ("acquisition.event_tape_scale", "event_key"),
    ("asm.artist_market_security_v1", "row_key"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="/tmp/artist_security_stage.duckdb")
    parser.add_argument("--main", default="/tmp/artist_security_1000.duckdb")
    args = parser.parse_args()

    dst = duckdb.connect(args.main)
    # Ensure the main warehouse carries the latest schema (044 tables) before
    # merging into them; safe now that the wiki backfill has released the lock.
    apply_pending_migrations(dst)
    # Attach the staging warehouse so `stage.{table}` resolves inside SQL.
    from pathlib import Path as _P

    dst.execute(
        f"ATTACH '{_P(args.stage).as_posix()}' AS stage (READ_ONLY)"
    )
    report: dict = {"merged": {}, "status": "COMPLETE"}
    try:
        for table, pk in TABLES:
            try:
                n_src = dst.execute(f"SELECT COUNT(*) FROM stage.{table}").fetchone()[0]
            except Exception:  # table absent from the stage warehouse
                report["merged"][table] = {"status": "SKIPPED", "detail": "absent from stage"}
                print(f"{table}: absent from stage (skipped)")
                continue
            if int(n_src) == 0:
                report["merged"][table] = {"status": "SKIPPED", "detail": "empty in stage"}
                print(f"{table}: empty in stage (skipped)")
                continue
            cols = [
                c[0]
                for c in dst.execute(f"DESCRIBE {table}").fetchall()
            ]
            col_sql = ", ".join(cols)
            # Use INSERT OR REPLACE (DuckDB) = upsert by PK.
            dst.execute(f"BEGIN TRANSACTION")
            dst.execute(
                f"INSERT OR REPLACE INTO {table} ({col_sql}) "
                f"SELECT {col_sql} FROM stage.{table}"
            )
            n_dst = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            dst.execute("COMMIT")
            report["merged"][table] = {"stage_rows": int(n_src), "main_rows_after": int(n_dst)}
            print(f"{table}: stage={n_src} main_after={n_dst}")
    finally:
        try:
            dst.execute("DETACH stage")
        except Exception:
            pass
        dst.close()
    Path("/tmp/stage_merge_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print("wrote /tmp/stage_merge_report.json")


if __name__ == "__main__":
    main()
