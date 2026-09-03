#!/usr/bin/env python3
"""Bounded terminal read-path benchmark.

The benchmark exercises the dispatcher with deterministic, fixture-sized
requests. It does not call external providers, rebuild the warehouse, or
write benchmark artifacts unless ``--output`` is supplied. A real compact
serving DB can be supplied with ``--artist-security-db`` and the benchmark
will open it read-only; otherwise the benchmark reports the endpoint contract
against a caller-provided app factory in tests.

The CLI is intentionally conservative: without an existing compact artifact it
exits with a clear SKIPPED result instead of manufacturing product latency
claims. Tests use ``benchmark_app`` directly with a tiny in-memory fixture.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

ENDPOINTS = (
    "search",
    "artist",
    "market",
    "compare",
    "underwrite",
    "portfolio",
    "pace",
    "monitor",
)
CONCURRENCIES = (1, 8, 20, 32)


@dataclass(frozen=True)
class BenchmarkRequest:
    name: str
    method: str
    path: str
    query: str = ""
    body: bytes = b""


def percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        value = ordered[lower]
    else:
        value = ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
    return round(value, 3)


def default_requests() -> tuple[BenchmarkRequest, ...]:
    """Use read-only endpoint shapes; writes are excluded deliberately."""
    return (
        BenchmarkRequest("search", "GET", "/api/search", "q=artist&limit=10"),
        BenchmarkRequest("artist", "GET", "/api/artist-security/mbid::artist"),
        BenchmarkRequest("market", "GET", "/api/markets/chicago"),
        BenchmarkRequest(
            "compare", "GET", "/api/artist-security/compare", "a=mbid::artist&b=mbid::peer"
        ),
        BenchmarkRequest("underwrite", "POST", "/api/underwrite", body=b"{}"),
        BenchmarkRequest("portfolio", "GET", "/api/portfolio"),
        BenchmarkRequest("pace", "GET", "/api/pace"),
        BenchmarkRequest("monitor", "GET", "/api/monitor"),
    )


def _invoke(app: Any, request: BenchmarkRequest, _: int) -> tuple[float, int]:
    started = time.perf_counter()
    response = app.dispatch(request.method, request.path, request.query, request.body)
    return (time.perf_counter() - started) * 1000.0, int(response.get("status", 0))


def benchmark_app(
    app: Any,
    *,
    requests: Iterable[BenchmarkRequest] | None = None,
    concurrency_levels: Iterable[int] = CONCURRENCIES,
    samples_per_worker: int = 4,
) -> dict[str, Any]:
    """Measure dispatcher latency and status at bounded concurrency levels."""
    if samples_per_worker < 1:
        raise ValueError("samples_per_worker must be positive")
    request_list = tuple(requests or default_requests())
    if not request_list:
        raise ValueError("at least one benchmark request is required")
    levels = tuple(int(level) for level in concurrency_levels)
    if not levels:
        raise ValueError("at least one concurrency level is required")
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for workers in levels:
        if workers < 1 or workers > 128:
            raise ValueError("concurrency must be in [1, 128]")
        level_results: dict[str, dict[str, Any]] = {}
        for request in request_list:
            timings: list[float] = []
            statuses: list[int] = []
            total = workers * samples_per_worker
            invoke = partial(_invoke, app, request)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for elapsed, status in executor.map(invoke, range(total)):
                    timings.append(elapsed)
                    statuses.append(status)
            level_results[request.name] = {
                "requests": len(timings),
                "p50_ms": percentile(timings, 0.50),
                "p95_ms": percentile(timings, 0.95),
                "min_ms": round(min(timings), 3),
                "max_ms": round(max(timings), 3),
                "status_counts": {
                    str(status): statuses.count(status) for status in sorted(set(statuses))
                },
            }
        results[str(workers)] = level_results
    return {
        "benchmark": "terminal_read_path_v1",
        "endpoints": [request.name for request in request_list],
        "concurrency": list(levels),
        "samples_per_worker": samples_per_worker,
        "results": results,
        "external_provider_calls": 0,
        "artifact_rebuilt": False,
    }


def benchmark_before_after(
    before_app: Any,
    after_app: Any,
    *,
    requests: Iterable[BenchmarkRequest] | None = None,
    concurrency_levels: Iterable[int] = CONCURRENCIES,
    samples_per_worker: int = 4,
) -> dict[str, Any]:
    """Persist comparable p50/p95 measurements for the old and new dispatchers."""
    request_list = tuple(requests or default_requests())
    levels = tuple(int(level) for level in concurrency_levels)
    return {
        "benchmark": "terminal_read_path_before_after_v1",
        "endpoints": [request.name for request in request_list],
        "concurrency": list(levels),
        "samples_per_worker": samples_per_worker,
        "before": benchmark_app(
            before_app,
            requests=request_list,
            concurrency_levels=levels,
            samples_per_worker=samples_per_worker,
        ),
        "after": benchmark_app(
            after_app,
            requests=request_list,
            concurrency_levels=levels,
            samples_per_worker=samples_per_worker,
        ),
        "external_provider_calls": 0,
        "artifact_rebuilt": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artist-security-db", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--samples-per-worker", type=int, default=4)
    args = parser.parse_args()
    if args.artist_security_db is None or not args.artist_security_db.is_file():
        result = {
            "benchmark": "terminal_read_path_v1",
            "status": "SKIPPED",
            "reason": "an existing compact artist-security DB is required; no artifact was rebuilt",
            "external_provider_calls": 0,
            "artifact_rebuilt": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    from festival_bloomberg.terminal.artist_security import open_product_db
    from festival_bloomberg.terminal.mvp_server import MvpTerminalApp, open_workspace

    conn = open_product_db(str(args.artist_security_db))
    workspace = open_workspace(":memory:")
    app = MvpTerminalApp(
        conn,
        workspace,
        db_path=args.artist_security_db,
        current_json_path=args.artist_security_db.parent / "CURRENT.json",
    )
    try:
        result = benchmark_app(app, samples_per_worker=args.samples_per_worker)
    finally:
        app.close()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
