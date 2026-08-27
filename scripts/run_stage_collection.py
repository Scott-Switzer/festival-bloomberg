"""Run all non-wiki collection stages against the staging warehouse.

The Wikimedia backfill holds the write lock on the main scale warehouse, so
the non-wiki stages (LB, identity, Spotify, YouTube, SetlistFM, event tape,
artist×market) run against the parallel staging warehouse first. The wiki
rows are merged in afterward, then materialization + feast + monitor run.

    PYTHONPATH=python .venv/bin/python scripts/run_stage_collection.py \
        --warehouse /tmp/artist_security_stage.duckdb
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.localenv import load_local_env  # noqa: E402


def _stage(label: str, fn, report: dict, key: str) -> None:
    print(f"\n=== {label} ===")
    sys.stdout.flush()
    t0 = time.time()
    try:
        result = fn()
        report[key] = result
        print(json.dumps(result, indent=2, default=str)[:1500])
    except Exception as exc:  # noqa: BLE001
        report[key] = {"status": "ERROR", "detail": str(exc)[:500]}
        print(f"ERROR in {label}: {exc}")
    print(f"({label} took {time.time() - t0:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/artist_security_stage.duckdb")
    parser.add_argument("--universe-limit", type=int, default=1000)
    parser.add_argument("--min-interval", type=float, default=0.3)
    parser.add_argument("--skip-lb", action="store_true")
    parser.add_argument("--skip-spotify", action="store_true")
    parser.add_argument("--skip-yt", action="store_true")
    parser.add_argument("--skip-setlist", action="store_true")
    args = parser.parse_args()

    load_local_env()
    conn = duckdb.connect(args.warehouse)
    report: dict = {"stages": {}, "started_at": time.time()}
    try:
        from festival_bloomberg.security.artist_security_master import select_security_universe

        universe = select_security_universe(conn, limit=args.universe_limit)
        print(f"universe: {len(universe)} artists")

        if not args.skip_lb:
            from festival_bloomberg.acquisition.transport import UrllibTransport
            from festival_bloomberg.attention.listenbrainz_bulk import collect_security_universe_listenbrainz

            def _lb():
                return collect_security_universe_listenbrainz(
                    conn, UrllibTransport(), universe=universe,
                    min_interval_seconds=args.min_interval,
                    include_range_history=True,
                )
            _stage("P1 ListenBrainz scale", _lb, report["stages"], "listenbrainz")

        from festival_bloomberg.identity.identity_master import run_identity_master

        def _idm():
            return run_identity_master(conn, universe_limit=args.universe_limit)
        _stage("P2 identity master", _idm, report["stages"], "identity_master")

        if not args.skip_spotify:
            from festival_bloomberg.acquisition.transport import UrllibTransport
            from festival_bloomberg.identity.spotify_identity import run_spotify_identity

            def _sp():
                return run_spotify_identity(
                    conn, UrllibTransport(), universe=universe,
                    client_id=os.environ.get("SPOTIFY_CLIENT_ID"),
                    client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET"),
                )
            _stage("P4 Spotify identity + catalog", _sp, report["stages"], "spotify")

        if not args.skip_yt:
            from festival_bloomberg.acquisition.transport import UrllibTransport
            from festival_bloomberg.attention.youtube_forward import collect_channel_snapshots

            def _yt():
                return collect_channel_snapshots(
                    conn, UrllibTransport(),
                    artists=universe,
                    api_key=os.environ.get("YOUTUBE_API_KEY"),
                )
            _stage("P3 YouTube forward tape", _yt, report["stages"], "youtube")

        if not args.skip_setlist:
            from festival_bloomberg.acquisition.transport import UrllibTransport
            from festival_bloomberg.security.live_ticket import run_live_ticket

            def _p5():
                return run_live_ticket(
                    conn, UrllibTransport(), universe=universe,
                    setlistfm_api_key=os.environ.get("SETLISTFM_API_KEY"),
                    min_interval_seconds=args.min_interval,
                )
            _stage("P5 live + ticket joins", _p5, report["stages"], "live_ticket")

        from festival_bloomberg.security.event_tape import ingest_provider_estate_events, measure_tape

        def _p9():
            ingest = ingest_provider_estate_events(conn)
            return {**ingest, "measurement": measure_tape(conn)}
        _stage("P9 EVENT_TAPE_2000", _p9, report["stages"], "event_tape")

        from festival_bloomberg.security.artist_market import build_artist_market_rows

        def _p10():
            return build_artist_market_rows(conn, universe=universe)
        _stage("P10 artist × market", _p10, report["stages"], "artist_market")

        report["status"] = "COMPLETE"
    finally:
        conn.close()
    Path("/tmp/stage_collection_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print("wrote /tmp/stage_collection_report.json")


if __name__ == "__main__":
    main()
