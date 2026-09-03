#!/usr/bin/env python3
"""Record p50/p95 evidence for the terminal read path on a real serving DB.

Runs the same endpoint set twice against one compact serving snapshot:

- ``before``  — global serialization emulation (single compatibility lock
  around every request, matching the pre-fast-path single-connection world).
- ``after``   — the shipped fast path: thread-local DuckDB connections with
  only narrow workspace-mutation locking.

The request set is built from real artists/markets in the snapshot (demo
artists when present) so status codes are meaningful. Only dispatcher latency
is measured — no HTTP layer, no external providers, no artifact rebuild.

Example:
    python scripts/benchmark_terminal_evidence.py \
        --serving-db serving/artist_security_terminal_v1/terminal_demo_ai_v1.duckdb \
        --output control/artifacts/artist_intelligence_terminal_v1/perf_fastpath.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
from benchmark_terminal import BenchmarkRequest, benchmark_app


def _demo_keys(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute("SELECT artist_key FROM demo_artists").fetchall()
    if rows:
        return [row[0] for row in rows]
    return [
        row[0]
        for row in conn.execute("SELECT artist_key FROM artists LIMIT 5").fetchall()
    ]


def _market_key(conn: duckdb.DuckDBPyConnection) -> str:
    row = conn.execute(
        "SELECT market_key FROM artist_markets GROUP BY market_key "
        "ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else "chicago-il"


def build_requests(conn: duckdb.DuckDBPyConnection) -> tuple[BenchmarkRequest, ...]:
    demo = _demo_keys(conn)
    a = demo[0]
    b = demo[1] if len(demo) > 1 else demo[0]
    market = _market_key(conn)
    name = conn.execute(
        "SELECT name FROM artists WHERE artist_key = ?", [a]
    ).fetchone()
    search = str((name or [a])[0]).split(" (")[0]
    underwrite_body = json.dumps(
        {
            "artist_key": a,
            "market_key": market,
            "guarantee": {"type": "FIXED", "amount": 100000},
            "capacity": 4000,
            "ticket_price": 45,
        }
    ).encode()
    return (
        BenchmarkRequest("search", "GET", "/api/search", f"q={search}&limit=10"),
        BenchmarkRequest("artist", "GET", f"/api/artist-security/{a}"),
        BenchmarkRequest("market", "GET", f"/api/market/{market}"),
        BenchmarkRequest(
            "compare", "GET", "/api/artist-security/compare", f"a={a}&b={b}"
        ),
        BenchmarkRequest("underwrite", "POST", "/api/underwrite", body=underwrite_body),
        BenchmarkRequest("portfolio", "GET", "/api/portfolio"),
        BenchmarkRequest("pace", "GET", "/api/pace"),
        BenchmarkRequest("monitor", "GET", "/api/monitor"),
    )


def make_app(db_path: Path, *, serialized: bool):
    from festival_bloomberg.terminal.artist_security import open_product_db
    from festival_bloomberg.terminal.mvp_server import MvpTerminalApp, open_workspace

    conn = open_product_db(str(db_path))
    workspace = open_workspace(":memory:")
    app = MvpTerminalApp(
        conn,
        workspace,
        db_path=db_path,
        current_json_path=db_path.parent / "CURRENT.json",
    )
    if serialized:
        # Emulate the pre-fast-path single-connection world: every request
        # (reads included) funnels through one process-wide lock.
        app._compatibility_mode = True  # type: ignore[attr-defined]
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples-per-worker", type=int, default=5)
    args = parser.parse_args()
    if not args.serving_db.is_file():
        raise SystemExit(f"serving DB not found: {args.serving_db}")

    probe = duckdb.connect(str(args.serving_db), read_only=True)
    try:
        requests = build_requests(probe)
    finally:
        probe.close()

    before = make_app(args.serving_db, serialized=True)
    after = make_app(args.serving_db, serialized=False)
    try:
        result = {
            "benchmark": "terminal_read_path_before_after_v1",
            "serving_db": str(args.serving_db),
            "requests": [r.name for r in requests],
            "concurrency": [1, 8, 20, 32],
            "samples_per_worker": args.samples_per_worker,
            "before_serialized": benchmark_app(
                before, requests=requests, samples_per_worker=args.samples_per_worker
            ),
            "after_fast_path": benchmark_app(
                after, requests=requests, samples_per_worker=args.samples_per_worker
            ),
            "external_provider_calls": 0,
            "artifact_rebuilt": False,
        }
    finally:
        before.close()
        after.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
