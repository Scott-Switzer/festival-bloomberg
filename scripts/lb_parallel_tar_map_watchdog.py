#!/usr/bin/env python3
"""Keep every incomplete ListenBrainz parallel tar-map worker online.

Never restarts a worker that is either:
  - writing checkpoints within LIVE_SEC, or
  - DO status RUNNING with started_at within RECENT_START_SEC
    (protects the first batch before any checkpoint lands).

Usage:
  PYTHONPATH=python .venv/bin/python scripts/lb_parallel_tar_map_watchdog.py
  PYTHONPATH=python .venv/bin/python scripts/lb_parallel_tar_map_watchdog.py --once
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lb_launch_parallel_tar_map import (  # noqa: E402
    WAVE,
    WORKER,
    http_json,
    load_admin_token,
    wave_plan,
)

LIVE_SEC = 600  # 10 min checkpoint freshness
RECENT_START_SEC = 900  # 15 min — do not kill first-batch workers
TARGET_LIVE = 18
INTERVAL_SEC = 120
LAKE = "festival-intelligence-lake"


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def classify(plan, s3, token: str) -> tuple[list, list, list, set[int]]:
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

        need_shards = (
            set(range(w["shard_start"], w["shard_start"] + w["max_shards"])) - done
        )
        if not need_shards:
            complete.append(w)
            continue

        do_status = None
        started_at = None
        err = None
        try:
            d = http_json("GET", f"{WORKER}/batch/status?job_id={jid}", token)
            st = d.get("status") or {}
            do_status = st.get("status")
            started_at = _parse_iso(st.get("started_at"))
            err = st.get("last_safe_error_code")
        except Exception:
            pass

        recently_started = (
            do_status == "RUNNING"
            and started_at is not None
            and (now - started_at) < RECENT_START_SEC
        )
        ckpt_fresh = age is not None and age < LIVE_SEC

        if ckpt_fresh or recently_started:
            live.append(w)
            continue

        # FAILED / stale RUNNING / no DO — safe to requeue
        need.append({**w, "_do_status": do_status, "_err": err, "_age": age})
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
            print(
                f"  queued {jid} -> {r.get('status')} "
                f"(was {w.get('_do_status')}/{w.get('_err')} age={w.get('_age')})",
                flush=True,
            )
            launched += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  fail {jid}: {exc}", flush=True)
        time.sleep(4.0)
    return launched


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=INTERVAL_SEC)
    ap.add_argument("--target-live", type=int, default=TARGET_LIVE)
    args = ap.parse_args()
    target_live = args.target_live

    from festival_bloomberg.localenv import load_local_env
    from festival_bloomberg.lake.r2 import r2_client

    load_local_env()
    token = load_admin_token()
    s3 = r2_client()
    print(
        f"watchdog start wave={WAVE} target_live={target_live} "
        f"live_sec={LIVE_SEC} recent_start_sec={RECENT_START_SEC} "
        f"interval={args.interval}s",
        flush=True,
    )

    def round_once() -> bool:
        plan = wave_plan()
        live, need, complete, covered = classify(plan, s3, token)
        print(
            f"{time.strftime('%H:%M:%S')} covered={len(covered)}/1526 "
            f"live={len(live)} need={len(need)} complete_slices={len(complete)}",
            flush=True,
        )
        if len(covered) >= 1526:
            print("MAP_COMPLETE", flush=True)
            return True
        slots = max(0, target_live - len(live))
        if slots and need:
            print(
                f"  refilling {min(slots, len(need))} (target_live={target_live})",
                flush=True,
            )
            refill(need, token=token, slots=slots)
        elif not need:
            print("  all incomplete slices protected/live — holding", flush=True)
        else:
            print(f"  at capacity live={len(live)} — holding", flush=True)
        return False

    if args.once:
        round_once()
        return 0
    rounds = max(1, int(6 * 3600 / args.interval))
    for _ in range(rounds):
        try:
            if round_once():
                return 0
        except Exception as exc:  # noqa: BLE001
            print(f"round error: {exc}", flush=True)
        time.sleep(args.interval)
    print("watchdog exit (time budget)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
