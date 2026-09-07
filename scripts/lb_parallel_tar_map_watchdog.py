#!/usr/bin/env python3
"""Keep every incomplete ListenBrainz parallel tar-map worker online.

Never restarts a worker whose checkpoint updated within LIVE_SEC.
Every INTERVAL_SEC, requeues stale/missing/failed slices up to TARGET_LIVE
concurrent workers (under Cloudflare max_instances).

Usage:
  PYTHONPATH=python .venv/bin/python scripts/lb_parallel_tar_map_watchdog.py
  PYTHONPATH=python .venv/bin/python scripts/lb_parallel_tar_map_watchdog.py --once
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lb_launch_parallel_tar_map import (  # noqa: E402
    WAVE,
    http_json,
    load_admin_token,
    wave_plan,
)

LIVE_SEC = 480  # 8 min — batch boundary ~2–4 min; never touch fresher
STALE_RESTART_SEC = 480
TARGET_LIVE = 18  # under max_instances=20; leave headroom for cron/other
INTERVAL_SEC = 120
WORKER = "https://fi-acquisition-runtime.scswitzer.workers.dev"
LAKE = "festival-intelligence-lake"


def classify(plan, s3) -> tuple[list, list, list, set[int]]:
    from festival_bloomberg.lake.r2 import r2_client  # noqa: F401 — s3 passed in

    now = time.time()
    live: list = []
    need: list = []
    complete: list = []
    covered: set[int] = set()
    for w in plan:
        jid = w["job_id"]
        age = None
        done: set[int] = set()
        try:
            meta = s3.head_object(
                Bucket=LAKE,
                Key=f"control/jobs/listenbrainz_tar_map/{jid}/checkpoint.json",
            )
            age = now - meta["LastModified"].timestamp()
            ckpt = json.loads(
                s3.get_object(
                    Bucket=LAKE,
                    Key=f"control/jobs/listenbrainz_tar_map/{jid}/checkpoint.json",
                )["Body"].read()
            )
            for a, b in ckpt.get("completed_batches") or []:
                done.update(range(a, b + 1))
                covered.update(range(a, b + 1))
        except Exception:
            pass
        need_shards = set(range(w["shard_start"], w["shard_start"] + w["max_shards"])) - done
        if not need_shards:
            complete.append(w)
            continue
        # Also treat DO FAILED as need even if somehow fresh (shouldn't happen)
        if age is not None and age < LIVE_SEC:
            live.append(w)
        else:
            need.append(w)
    return live, need, complete, covered


def refill(need: list, *, token: str, slots: int) -> int:
    launched = 0
    for w in need[:slots]:
        jid = w["job_id"]
        try:
            http_json("POST", f"{WORKER}/ops/container/restart?job_id={jid}", token)
            time.sleep(0.4)
            body = {
                "job_type": "listenbrainz_tar_map",
                "job_id": jid,
                "max_batches": w["max_shards"],
                "params": {
                    "partitions": w["partitions"],
                    "max_shards": w["max_shards"],
                    "shard_start": w["shard_start"],
                    "map_target_shards": w["map_target_shards"],
                },
            }
            r = http_json("POST", f"{WORKER}/batch/trigger", token, body)
            print(f"  queued {jid} -> {r.get('status')}", flush=True)
            launched += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  fail {jid}: {exc}", flush=True)
        time.sleep(3.0)
    return launched


def round_once(*, token: str, s3) -> bool:
    """Return True when all shards covered."""
    plan = wave_plan()
    live, need, complete, covered = classify(plan, s3)
    print(
        f"{time.strftime('%H:%M:%S')} covered={len(covered)}/1526 "
        f"live={len(live)} need={len(need)} complete_slices={len(complete)}",
        flush=True,
    )
    if len(covered) >= 1526:
        print("MAP_COMPLETE", flush=True)
        return True
    slots = max(0, TARGET_LIVE - len(live))
    if slots and need:
        print(f"  refilling {min(slots, len(need))} (target_live={TARGET_LIVE})", flush=True)
        refill(need, token=token, slots=slots)
    elif not need:
        print("  all incomplete slices look live — holding", flush=True)
    else:
        print(f"  at capacity live={len(live)} — holding", flush=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=INTERVAL_SEC)
    ap.add_argument("--target-live", type=int, default=TARGET_LIVE)
    args = ap.parse_args()
    global TARGET_LIVE
    TARGET_LIVE = args.target_live

    from festival_bloomberg.localenv import load_local_env
    from festival_bloomberg.lake.r2 import r2_client

    load_local_env()
    token = load_admin_token()
    s3 = r2_client()
    print(
        f"watchdog start wave={WAVE} target_live={TARGET_LIVE} "
        f"live_sec={LIVE_SEC} interval={args.interval}s",
        flush=True,
    )
    if args.once:
        round_once(token=token, s3=s3)
        return 0
    # ~4h max
    rounds = max(1, int(4 * 3600 / args.interval))
    for i in range(rounds):
        try:
            if round_once(token=token, s3=s3):
                return 0
        except Exception as exc:  # noqa: BLE001
            print(f"round error: {exc}", flush=True)
        time.sleep(args.interval)
    print("watchdog exit (time budget)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
