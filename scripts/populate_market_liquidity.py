"""MARKET_LIQUIDITY_TAPE_V1 — the full market-liquidity pass runner.

Orchestrates every workstream against the real event tape + security universe,
starting from legitimate official structured APIs (Ticketmaster Discovery first).

    P0  Ticketmaster structured tape — Discovery GET_EVENT enrich (status /
        onsale / standard price range / promoter / url) + Inventory Status auth
        probe (honest NOT_AUTHORIZED/ENDPOINT_UNREACHABLE — never scraped).
    P1/P2  SeatGeek + StubHub official API auth probes (fail closed; no key => NOT_AUTHORIZED).
    P3  Marketplace identity graph — TM exact IDs into acquisition.event_identifiers.
    P5  Bootstrap cohort — 500-1000 future events defensibly linked to the universe.
    P6  Longitudinal depth metrics (PIT event-marketplace-days, pair distribution).
    P7  Forward artist tape — wiki latest daily + LB current + YouTube (BLOCKED_INVALID_KEY).
    P8  Product join — market liquidity into asm.artist_market_security_v1.
    P9  Perspective monitor — add market-liquidity columns to the real 1000-security monitor.
    P10 Rights/cost scorecard (acquisition.source_auth_status).
    P11 Other-marketplace probe (Vivid/TickPick/Gametime/AXS) — deferral, no paid provider.

Bounded: providers without keys fail closed honestly; costs tracked; all metrics real.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/populate_market_liquidity.py \
        --warehouse /tmp/artist_security_1000.duckdb \
        --report reports/market_liquidity_success.json
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
    print(f"\n=== {label} ===", flush=True)
    t0 = time.time()
    try:
        result = fn()
        report["stages"][key] = result
        print(json.dumps(result, indent=2, default=str)[:1600], flush=True)
    except Exception as exc:  # noqa: BLE001
        report["stages"][key] = {"status": "ERROR", "detail": str(exc)[:500]}
        print(f"ERROR in {label}: {exc}", flush=True)
    print(f"({label} took {time.time() - t0:.1f}s)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--report", default="reports/market_liquidity_success.json")
    parser.add_argument("--universe-limit", type=int, default=1000)
    parser.add_argument("--max-cohort", type=int, default=1000)
    parser.add_argument("--min-interval", type=float, default=0.35)
    parser.add_argument("--skip-network", action="store_true",
                        help="skip TM attraction search + price observation collection")
    parser.add_argument("--max-tm-events", type=int, default=None,
                        help="bounded TM price enrich (smoke)")
    args = parser.parse_args()

    load_local_env()
    conn = duckdb.connect(args.warehouse)
    report: dict = {
        "milestone": "MARKET_LIQUIDITY_TAPE_V1",
        "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "universe_limit": args.universe_limit,
        "stages": {},
    }
    try:
        applied = apply_pending_migrations(conn)
        report["migrations_applied"] = applied

        from festival_bloomberg.security.artist_security_master import select_security_universe
        universe = select_security_universe(conn, limit=args.universe_limit)
        report["universe_size"] = len(universe)
        print(f"universe: {len(universe)} artists", flush=True)

        # ---- P0/P1/P2/P10: source auth probes + scorecard ----
        from festival_bloomberg.acquisition.transport import UrllibTransport
        from festival_bloomberg.security.market_liquidity import (
            probe_inventory_status_auth, probe_seatgeek_auth, probe_stubhub_auth,
        )
        from festival_bloomberg.security.marketplace_probe import probe_other_marketplaces

        tm_key = os.environ.get("TICKETMASTER_API_KEY")
        env_keys = {k: os.environ.get(k) for k in (
            "TICKETMASTER_API_KEY", "SEATGEEK_CLIENT_ID", "SEATGEEK_API_KEY",
            "SEATGEEK_KEY", "STUBHUB_CLIENT_ID", "STUBHUB_CLIENT_SECRET",
            "STUBHUB_APP_SNIFFER", "VIVIDSEATS_API_KEY", "TICKPICK_API_KEY",
            "GAMETIME_API_KEY", "AXS_API_KEY",
        )}
        transport = UrllibTransport()

        def _auth():
            return {
                "ticketmaster_discovery": {"auth_state": "AUTHORIZED" if tm_key else "NOT_AUTHORIZED",
                                           "credential_state": "CONFIGURED" if tm_key else "ABSENT"},
                "ticketmaster_inventory": probe_inventory_status_auth(conn, transport, tm_key),
                "seatgeek": probe_seatgeek_auth(conn, env_keys),
                "stubhub": probe_stubhub_auth(conn, env_keys),
                "other_marketplaces": probe_other_marketplaces(conn, env_keys),
            }
        _stage("P0/P1/P2/P10/P11 source auth", _auth, report, "source_auth")

        # ---- P0: TM attraction linker + cohort ----
        from festival_bloomberg.security.market_liquidity import (
            resolve_tm_attractions, build_bootstrap_cohort,
            collect_tm_price_observations,
        )

        cohort = []
        if not args.skip_network:
            _stage("P0 attraction linker", lambda: resolve_tm_attractions(
                conn, transport, universe=universe, api_key=tm_key,
                min_interval_seconds=args.min_interval,
            ), report, "attraction_linker")
            cohort_result = build_bootstrap_cohort(conn, max_events=args.max_cohort)
            report["stages"]["cohort"] = cohort_result
            cohort = cohort_result.get("cohort") or []
            print(f"cohort size: {len(cohort)}", flush=True)
            if cohort and tm_key:
                _stage("P0 TM price observations", lambda: collect_tm_price_observations(
                    conn, transport, cohort=cohort, api_key=tm_key,
                    min_interval_seconds=args.min_interval, max_events=args.max_tm_events,
                ), report, "tm_price_observations")
        else:
            report["stages"]["cohort"] = {"status": "SKIPPED_NETWORK", "cohort": [], "n": 0}

        # ---- P3: marketplace identity graph (TM exact) ----
        from festival_bloomberg.security.market_liquidity import upsert_event_identifier_tm
        graph_count = 0
        for ev in cohort:
            upsert_event_identifier_tm(conn, ev)
            graph_count += 1
        report["stages"]["identity_graph_tm"] = {"events_mapped": graph_count}

        # ---- P6: longitudinal depth metrics ----
        from festival_bloomberg.security.market_liquidity import measure_longitudinal_depth
        _stage("P6 longitudinal depth", lambda: measure_longitudinal_depth(conn), report, "longitudinal_depth")

        # ---- P7: forward artist tape ----
        from festival_bloomberg.security.forward_tape import run_forward_tape
        _stage("P7 forward artist tape", lambda: run_forward_tape(
            conn, universe=universe, api_key=os.environ.get("YOUTUBE_API_KEY"),
        ), report, "forward_tape")

        # ---- P8: product join into artist × market security ----
        from festival_bloomberg.security.market_liquidity_join import join_market_liquidity_into_security
        _stage("P8 product join", lambda: join_market_liquidity_into_security(conn), report, "product_join")

        # ---- P9: Perspective monitor (with market-liquidity columns) ----
        from festival_bloomberg.security.perspective_monitor import run_monitor
        _stage("P9 perspective monitor", lambda: run_monitor(
            conn, out_path="reports/artist_security_1000_monitor.json",
        ), report, "perspective_monitor")

        # ---- Success report ----
        from festival_bloomberg.security.market_liquidity_report import build_market_liquidity_report
        report["success_report"] = build_market_liquidity_report(conn, stages=report["stages"])
        report["status"] = "COMPLETE"
        report["finished_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    finally:
        conn.close()

    args.report = Path(args.report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {args.report}", flush=True)


if __name__ == "__main__":
    main()