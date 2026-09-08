#!/usr/bin/env python3
"""Launch parallel ListenBrainz affinity reduce workers, then seal/pairs/gold/serve.

Affinity does not depend on artist-day. Default: 16 workers × 16 parts.

  PYTHONPATH=python .venv/bin/python scripts/lb_launch_parallel_affinity.py --trigger
  PYTHONPATH=python .venv/bin/python scripts/lb_launch_parallel_affinity.py --seal-and-finish
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

WORKER = os.environ.get(
    "FI_BATCH_WORKER_URL",
    "https://fi-acquisition-runtime.scswitzer.workers.dev",
)
MAP_JOB = "lb_full_map_1526"
PARTITIONS = 256
WORKERS = 16  # 256/16 = 16 parts each


def load_admin_token() -> str:
    tok = os.environ.get("ADMIN_TOKEN", "").strip().strip('"').strip("'")
    if tok:
        return tok
    for line in Path(".env").read_text().splitlines():
        if line.startswith("ADMIN_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("ADMIN_TOKEN missing")


def http_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "festival-bloomberg-lb-affinity/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:500]
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


def plan(workers: int = WORKERS) -> list[dict]:
    # Approximate even slices over 256 partition slots; empty parts are skipped
    # inside reduce-affinity, so workers with sparse ranges finish early.
    chunk = (PARTITIONS + workers - 1) // workers
    out = []
    for i in range(workers):
        offset = i * chunk
        if offset >= PARTITIONS:
            break
        count = min(chunk, PARTITIONS - offset)
        out.append(
            {
                "job_id": f"lb_aff_w{i:02d}_{offset}_{offset + count}",
                "part_offset": offset,
                "max_parts": count,
            }
        )
    return out


def trigger(token: str, workers: int) -> None:
    for i, w in enumerate(plan(workers)):
        http_json("POST", f"{WORKER}/ops/container/restart?job_id={w['job_id']}", token)
        time.sleep(0.4)
        body = {
            "job_type": "listenbrainz_tar_reduce",
            "job_id": w["job_id"],
            "params": {
                "phase": "affinity",
                "map_job_id": MAP_JOB,
                "partitions": PARTITIONS,
                "part_offset": w["part_offset"],
                "max_parts": w["max_parts"],
            },
        }
        r = http_json("POST", f"{WORKER}/batch/trigger", token, body)
        print(
            f"[{i+1}/{workers}] {w['job_id']} offset={w['part_offset']} "
            f"n={w['max_parts']} -> {r.get('status')}",
            flush=True,
        )
        time.sleep(3.0)


def seal_and_finish(token: str) -> None:
    steps = [
        ("lb_aff_seal_v1", {"phase": "affinity_seal", "map_job_id": MAP_JOB, "partitions": PARTITIONS}),
        ("lb_aff_pairs_v1", {"phase": "pairs", "map_job_id": MAP_JOB, "partitions": PARTITIONS}),
        ("lb_aff_gold_v1", {"phase": "gold", "map_job_id": MAP_JOB, "partitions": PARTITIONS}),
    ]
    for job_id, params in steps:
        http_json("POST", f"{WORKER}/ops/container/restart?job_id={job_id}", token)
        time.sleep(0.4)
        r = http_json(
            "POST",
            f"{WORKER}/batch/trigger",
            token,
            {"job_type": "listenbrainz_tar_reduce", "job_id": job_id, "params": params},
        )
        print(f"triggered {job_id} phase={params['phase']} -> {r.get('status')}", flush=True)
        # Wait for completion before next dependent step.
        for _ in range(180):
            time.sleep(60)
            st = http_json("GET", f"{WORKER}/batch/status?job_id={job_id}", token)
            status = (st.get("status") or {}).get("status")
            print(f"  {job_id}={status}", flush=True)
            if status in ("COMPLETED", "PUBLISHED"):
                break
            if status == "FAILED":
                raise SystemExit(f"{job_id} FAILED")
        else:
            raise SystemExit(f"{job_id} timeout")

    sid = f"terminal_serving_lb_full_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    http_json("POST", f"{WORKER}/ops/container/restart?job_id={sid}", token)
    time.sleep(0.4)
    r = http_json(
        "POST",
        f"{WORKER}/batch/trigger",
        token,
        {"job_type": "terminal_serving_build_v1", "job_id": sid, "params": {}},
    )
    print(f"SERVING_TRIGGERED {sid} -> {r.get('status')}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--trigger", action="store_true")
    ap.add_argument("--seal-and-finish", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()
    p = plan(args.workers)
    print(json.dumps({"workers": len(p), "plan": p}, indent=2))
    if args.dry_run and not args.trigger and not args.seal_and_finish:
        return 0
    token = load_admin_token()
    if args.trigger:
        trigger(token, args.workers)
    if args.seal_and_finish:
        seal_and_finish(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
