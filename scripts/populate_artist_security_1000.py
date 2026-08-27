"""ARTIST_SECURITY_1000_SCALE_V1 — the full scale pass runner.

Orchestrates every milestone workstream against the 1000-security universe:

    P0  Wikimedia full backfill (2015-07-01 → latest) — key-free
    P1  ListenBrainz bulk + range history — key-free
    P2  Cross-provider identity master + scorecard
    P3  YouTube forward tape (fails closed; reports key provisioning status)
    P4  Spotify identity repair + catalog (candidates fail closed)
    P5  SetlistFM performance history + live statistics + future/ticket joins
    P6  Feast bounded adoption (real PIT retrieval validation)
    P7  Perspective internal artist monitor (real data export)
    P8  Voyager remains dormant (documented, not tuned)
    P9  EVENT_TAPE_2000 (provider estate bootstrap + real pair counts)
    P10 ARTIST × MARKET security objects (top US live markets)
    ... materialize the security master, then write the SUCCESS REPORT.

Bounded by design: paid/configured providers fail closed; every collector
reports honest coverage. Re-running is idempotent.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/populate_artist_security_1000.py \
        --warehouse /tmp/artist_security_1000.duckdb \
        --report reports/artist_security_1000_success.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.localenv import load_local_env  # noqa: E402
from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402


def _stage(label: str, fn, report: dict, key: str) -> None:
    print(f"\n=== {label} ===")
    sys.stdout.flush()
    t0 = time.time()
    try:
        result = fn()
        report[key] = result
        print(json.dumps({k: v for k, v in result.items() if k != "per_artist"}, indent=2, default=str)[:1200])
    except Exception as exc:  # noqa: BLE001
        report[key] = {"status": "ERROR", "detail": str(exc)[:500]}
        print(f"ERROR in {label}: {exc}")
    print(f"({label} took {time.time() - t0:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--report", default="reports/artist_security_1000_success.json")
    parser.add_argument("--universe-limit", type=int, default=1000)
    parser.add_argument("--wiki-start", default="2015-07-01", help="full backfill start")
    parser.add_argument("--wiki-end", default=None)
    parser.add_argument("--min-interval", type=float, default=0.22)
    parser.add_argument("--skip-wiki", action="store_true", help="wiki backfill already running separately")
    parser.add_argument("--skip-lb", action="store_true")
    parser.add_argument("--skip-yt", action="store_true")
    parser.add_argument("--skip-spotify", action="store_true")
    parser.add_argument("--skip-setlist", action="store_true")
    parser.add_argument("--no-materialize", action="store_true")
    parser.add_argument("--max-wiki-artists", type=int, default=None, help="bounded wiki pass (smoke)")
    args = parser.parse_args()

    load_local_env()
    conn = duckdb.connect(args.warehouse)
    report: dict = {
        "milestone": "ARTIST_SECURITY_1000_SCALE_V1",
        "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "universe_limit": args.universe_limit,
        "stages": {},
    }
    try:
        from festival_bloomberg.security.artist_security_master import select_security_universe

        universe = select_security_universe(conn, limit=args.universe_limit)
        report["universe_size"] = len(universe)
        print(f"universe: {len(universe)} artists")

        # ---- P1: ListenBrainz (bulk + ranges) ----
        if not args.skip_lb:
            from festival_bloomberg.attention.listenbrainz_bulk import collect_security_universe_listenbrainz
            from festival_bloomberg.acquisition.transport import UrllibTransport

            def _lb():
                return collect_security_universe_listenbrainz(
                    conn, UrllibTransport(), universe=universe,
                    min_interval_seconds=args.min_interval,
                    include_range_history=True,
                )
            _stage("P1 ListenBrainz scale", _lb, report["stages"], "listenbrainz")

        # ---- P0: Wikimedia full backfill ----
        if not args.skip_wiki:
            from festival_bloomberg.acquisition.transport import UrllibTransport
            from festival_bloomberg.attention.wikimedia_historical import collect_artist_daily_pageviews_batched

            names = [a.get("artist_name") or a["artist_key"] for a in universe]
            if args.max_wiki_artists:
                names = names[: args.max_wiki_artists]
            key_map = {a.get("artist_name") or a["artist_key"]: a["artist_key"] for a in universe}
            for a in universe:
                key_map.setdefault((a.get("artist_name") or "").strip().lower(), a["artist_key"])

            def _wiki():
                return collect_artist_daily_pageviews_batched(
                    conn, UrllibTransport(), names=names,
                    start=args.wiki_start, end=args.wiki_end,
                    min_interval_seconds=args.min_interval,
                    artist_keys_by_name=key_map,
                )
            _stage("P0 Wikimedia full backfill", _wiki, report["stages"], "wikimedia")

        # ---- P2: identity master ----
        from festival_bloomberg.identity.identity_master import run_identity_master

        def _idm():
            return run_identity_master(conn, universe_limit=args.universe_limit)
        _stage("P2 identity master", _idm, report["stages"], "identity_master")

        # ---- P4: Spotify identity repair + catalog ----
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

        # ---- P3: YouTube forward tape (fails closed) ----
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

        # ---- P5: SetlistFM performance history + live/ticket joins ----
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

        # ---- P9: EVENT_TAPE_2000 ----
        from festival_bloomberg.security.event_tape import ingest_provider_estate_events, measure_tape

        def _p9():
            ingest = ingest_provider_estate_events(conn)
            return {**ingest, "measurement": measure_tape(conn)}
        _stage("P9 EVENT_TAPE_2000", _p9, report["stages"], "event_tape")

        # ---- P10: ARTIST × MARKET ----
        from festival_bloomberg.security.artist_market import build_artist_market_rows

        def _p10():
            return build_artist_market_rows(conn, universe=universe)
        _stage("P10 artist × market", _p10, report["stages"], "artist_market")

        # ---- P6: Feast adoption ----
        from festival_bloomberg.security.feast_adoption import run_real_adoption

        def _p6():
            return run_real_adoption(conn, universe=universe)
        _stage("P6 Feast adoption", _p6, report["stages"], "feast_adoption")

        # ---- Materialize security master (factors/live/catalog/snapshots) ----
        if not args.no_materialize:
            from festival_bloomberg.security.artist_security_master import run_security_master

            def _mat():
                return run_security_master(conn, universe_limit=args.universe_limit)
            _stage("Materialize security master", _mat, report["stages"], "materialization")

        # ---- P7: Perspective monitor ----
        from festival_bloomberg.security.perspective_monitor import run_monitor

        def _p7():
            return run_monitor(
                conn,
                out_path="reports/artist_security_1000_monitor.json",
            )
        _stage("P7 Perspective monitor", _p7, report["stages"], "perspective_monitor")

        # ---- P8: Voyager status (dormant, documented) ----
        report["stages"]["voyager"] = {
            "status": "DORMANT_BY_DESIGN",
            "verdict": "INSUFFICIENT_DATA",
            "prior_overlap_lift": 0.0186,
            "note": "Not tuned in this milestone; re-run only after factor density materially improves.",
        }

        # ---- Success report ----
        from festival_bloomberg.security.scale_report import build_success_report

        report["success_report"] = build_success_report(conn, universe=universe, stages=report["stages"])
        report["status"] = "COMPLETE"
        report["finished_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    finally:
        conn.close()

    args.report = Path(args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {args.report}")


if __name__ == "__main__":
    main()
