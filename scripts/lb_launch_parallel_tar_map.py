#!/usr/bin/env python3
"""Launch parallel ListenBrainz tar-map workers and optionally seal checkpoints.

Default geometry (proof-sized slices):
  1526 shards / 76 per worker ≈ 21 workers (~1h wall-clock if they stay healthy)

Usage:
  # Print the wave plan only
  PYTHONPATH=python .venv/bin/python scripts/lb_launch_parallel_tar_map.py --dry-run

  # Trigger the wave (requires ADMIN_TOKEN)
  PYTHONPATH=python .venv/bin/python scripts/lb_launch_parallel_tar_map.py --trigger

  # Merge worker checkpoints into a sealed full-map checkpoint
  PYTHONPATH=python .venv/bin/python scripts/lb_launch_parallel_tar_map.py --seal
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

TOTAL_SHARDS = 1526
CHUNK = 76  # proven healthy ~1h slice
PARTITIONS = 256
WORKER = os.environ.get(
    "FI_BATCH_WORKER_URL",
    "https://fi-acquisition-runtime.scswitzer.workers.dev",
)
WAVE = "lb_par_1526"


def load_admin_token() -> str:
    tok = os.environ.get("ADMIN_TOKEN", "").strip()
    if tok:
        return tok
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ADMIN_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("ADMIN_TOKEN missing")


def wave_plan(total: int = TOTAL_SHARDS, chunk: int = CHUNK) -> list[dict]:
    plan = []
    start = 0
    idx = 0
    while start < total:
        end = min(start + chunk, total)
        count = end - start
        # Align end down only when not the final slice; final may be short.
        if end < total and count % 4 != 0:
            end -= count % 4
            count = end - start
        job_id = f"{WAVE}_w{idx:02d}_{start}_{end}"
        plan.append(
            {
                "job_id": job_id,
                "shard_start": start,
                "max_shards": count,
                "map_target_shards": total,
                "partitions": PARTITIONS,
            }
        )
        start = end
        idx += 1
    return plan


def http_json(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:500]
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


def trigger_wave(plan: list[dict], *, token: str, stagger_s: float = 1.5) -> None:
    for i, worker in enumerate(plan):
        # Restart DO so a fresh container picks up the current image.
        http_json(
            "POST",
            f"{WORKER}/ops/container/restart?job_id={worker['job_id']}",
            token,
        )
        time.sleep(0.3)
        body = {
            "job_type": "listenbrainz_tar_map",
            "job_id": worker["job_id"],
            "max_batches": worker["max_shards"],
            "params": {
                "partitions": worker["partitions"],
                "max_shards": worker["max_shards"],
                "shard_start": worker["shard_start"],
                "map_target_shards": worker["map_target_shards"],
            },
        }
        result = http_json("POST", f"{WORKER}/batch/trigger", token, body)
        print(
            f"[{i+1}/{len(plan)}] triggered {worker['job_id']} "
            f"slice=[{worker['shard_start']},{worker['shard_start']+worker['max_shards']}) "
            f"status={result.get('status')}",
            flush=True,
        )
        if i + 1 < len(plan):
            time.sleep(stagger_s)


def seal_checkpoints(plan: list[dict]) -> dict:
    from festival_bloomberg.localenv import load_local_env
    from festival_bloomberg.lake.r2 import r2_client

    load_local_env()
    s3 = r2_client()
    lake = "festival-intelligence-lake"
    sealed: dict = {
        "pipeline": "listenbrainz_full_scan",
        "pipeline_version": 3,
        "map_target_shards": TOTAL_SHARDS,
        "listener_hash_partitions": PARTITIONS,
        "completed_batches": [],
        "batch_artifacts": {},
        "checkpoint_authority": "CLOUD_JOB_R2",
        "cloud_job_id": f"{WAVE}_sealed",
        "seal_workers": [],
    }
    for worker in plan:
        key = f"control/jobs/listenbrainz_tar_map/{worker['job_id']}/checkpoint.json"
        try:
            ckpt = json.loads(s3.get_object(Bucket=lake, Key=key)["Body"].read())
        except Exception as exc:  # noqa: BLE001
            sealed.setdefault("missing_workers", []).append(
                {"job_id": worker["job_id"], "error": type(exc).__name__}
            )
            continue
        sealed["run_namespace"] = ckpt.get("run_namespace") or sealed.get("run_namespace")
        sealed["tar_index_sha256"] = ckpt.get("tar_index_sha256")
        sealed["artist_universe_sha256"] = ckpt.get("artist_universe_sha256")
        sealed["source_shard_count"] = ckpt.get("source_shard_count", TOTAL_SHARDS)
        sealed["dump_version"] = ckpt.get("dump_version")
        sealed["source_key"] = ckpt.get("source_key")
        sealed["source_bytes"] = ckpt.get("source_bytes")
        sealed["source_etag"] = ckpt.get("source_etag")
        for rng in ckpt.get("completed_batches") or []:
            sealed["completed_batches"].append(rng)
        arts = ckpt.get("batch_artifacts") or {}
        sealed["batch_artifacts"].update(arts)
        sealed["seal_workers"].append(
            {
                "job_id": worker["job_id"],
                "completed_batches": len(ckpt.get("completed_batches") or []),
                "listens_scanned": ckpt.get("listens_scanned"),
                "matched_listens": ckpt.get("matched_listens"),
                "updated_at": ckpt.get("updated_at"),
            }
        )

    # Sum slice counters (each worker tracks only its shard range).
    listens = 0
    matched = 0
    for w in sealed["seal_workers"]:
        listens += int(w.get("listens_scanned") or 0)
        matched += int(w.get("matched_listens") or 0)
    sealed["listens_scanned"] = listens
    sealed["matched_listens"] = matched
    sealed["completed_batches"] = sorted(
        sealed["completed_batches"], key=lambda r: (r[0], r[1])
    )
    # Deduplicate identical ranges
    dedup = []
    seen = set()
    for rng in sealed["completed_batches"]:
        key = (int(rng[0]), int(rng[1]))
        if key in seen:
            continue
        seen.add(key)
        dedup.append([key[0], key[1]])
    sealed["completed_batches"] = dedup

    covered = set()
    for a, b in sealed["completed_batches"]:
        covered.update(range(a, b + 1))
    sealed["covered_shard_count"] = len(covered)
    sealed["complete"] = len(covered) >= TOTAL_SHARDS
    sealed["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    out_key = f"control/jobs/listenbrainz_tar_map/{WAVE}_sealed/checkpoint.json"
    payload = (json.dumps(sealed, indent=2) + "\n").encode()
    s3.put_object(Bucket=lake, Key=out_key, Body=payload, ContentType="application/json")
    # Also publish as the canonical full-map checkpoint when complete.
    if sealed["complete"]:
        canon = "control/jobs/listenbrainz_tar_map/lb_full_map_1526/checkpoint.json"
        s3.put_object(Bucket=lake, Key=canon, Body=payload, ContentType="application/json")
        sealed["canonical_checkpoint"] = canon
    sealed["sealed_checkpoint"] = out_key
    return sealed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--trigger", action="store_true")
    ap.add_argument("--seal", action="store_true")
    ap.add_argument("--chunk", type=int, default=CHUNK)
    args = ap.parse_args()
    plan = wave_plan(chunk=args.chunk)
    print(json.dumps({"workers": len(plan), "chunk": args.chunk, "plan": plan}, indent=2))
    if args.dry_run and not args.trigger and not args.seal:
        return 0
    if args.trigger:
        token = load_admin_token()
        trigger_wave(plan, token=token)
    if args.seal:
        sealed = seal_checkpoints(plan)
        print(json.dumps({
            "complete": sealed.get("complete"),
            "covered_shard_count": sealed.get("covered_shard_count"),
            "workers_ok": len(sealed.get("seal_workers") or []),
            "missing": sealed.get("missing_workers"),
            "listens_scanned": sealed.get("listens_scanned"),
            "matched_listens": sealed.get("matched_listens"),
            "sealed_checkpoint": sealed.get("sealed_checkpoint"),
            "canonical_checkpoint": sealed.get("canonical_checkpoint"),
        }, indent=2))
        return 0 if sealed.get("complete") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
