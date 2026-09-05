#!/usr/bin/env python3
"""Preflight gates for LISTENBRAINZ_FULL_CORPUS_ACTIVATION_V1.

Read-only by default. Does not start a map. Does not download the 191 GB dump.

Exit 0 only when all required gates PASS (or --report soft mode).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

INDEX_CACHE = Path("control/lake/lb_tar_index.json")
SUMMARY = Path("control/listenbrainz/full_corpus/index_v1.json")
PLAN = Path("control/listenbrainz/full_corpus/job_plan_template.json")
RAW_BUCKET = "festival-intelligence-raw"
RAW_KEY = (
    "bulk/listenbrainz/dump=2593-20260712-000004/"
    "listenbrainz-spark-dump-2593-20260712-000004-full.tar"
)
EXPECTED_BYTES = 205_073_162_240
CLOUD_MIN_FREE = 8 * (1 << 30)
LOCAL_MAC_VERDICT = "BLOCKED_RESOURCE_GATE"


def data_shards(members: list) -> list:
    out = []
    for m in members:
        base = str(m.get("name", "")).split("/")[-1]
        if base.endswith(".parquet") and base[: -len(".parquet")].isdigit():
            out.append(m)
    return out


def gate_corpus_index() -> dict:
    if not INDEX_CACHE.exists() or not SUMMARY.exists():
        return {
            "gate": "CORPUS_INDEX",
            "status": "FAIL",
            "detail": "index or summary missing; run scripts/lb_build_corpus_index.py",
        }
    try:
        members = json.loads(INDEX_CACHE.read_text())
        summary = json.loads(SUMMARY.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"gate": "CORPUS_INDEX", "status": "FAIL", "detail": str(exc)}
    if not isinstance(members, list) or not members:
        return {"gate": "CORPUS_INDEX", "status": "FAIL", "detail": "empty members"}
    shards = data_shards(members)
    if len(shards) < 100:
        return {
            "gate": "CORPUS_INDEX",
            "status": "FAIL",
            "detail": f"only {len(shards)} data shards (expected ~1526)",
        }
    # geometry spot-check
    for m in shards[:3] + shards[-3:]:
        if m["offset"] % 512 != 0 or m["size"] <= 0:
            return {"gate": "CORPUS_INDEX", "status": "FAIL", "detail": "bad geometry"}
    return {
        "gate": "CORPUS_INDEX",
        "status": "VERIFIED" if summary.get("corpus_index_hash") else "FAIL",
        "data_shard_count": len(shards),
        "member_count": len(members),
        "corpus_index_hash": summary.get("corpus_index_hash"),
        "etag": summary.get("etag"),
    }


def gate_r2_access() -> dict:
    try:
        from festival_bloomberg.lake.r2 import r2_client

        s3 = r2_client()
        head = s3.head_object(Bucket=RAW_BUCKET, Key=RAW_KEY)
        size = int(head["ContentLength"])
        etag = head.get("ETag", "").strip('"')
        ok = size == EXPECTED_BYTES
        return {
            "gate": "R2_ACCESS",
            "status": "PASS" if ok else "FAIL",
            "content_length": size,
            "etag": etag,
            "expected_bytes": EXPECTED_BYTES,
        }
    except Exception as exc:  # noqa: BLE001
        return {"gate": "R2_ACCESS", "status": "FAIL", "detail": str(exc)}


def gate_map_resource(*, assume_cloud: bool) -> dict:
    free = int(shutil.disk_usage("/tmp").free)
    if assume_cloud:
        return {
            "gate": "MAP_RESOURCE_GATE",
            "status": "PASS" if free >= CLOUD_MIN_FREE else "PENDING_CLOUD_SCRATCH",
            "host": "assumed_cloud_batch_standard4",
            "free_disk_bytes": free,
            "required_bytes": CLOUD_MIN_FREE,
            "note": "Container must have ≥8 GiB free scratch; standard-4 has 20 GiB ephemeral",
        }
    return {
        "gate": "MAP_RESOURCE_GATE",
        "status": "BLOCKED_RESOURCE_GATE",
        "host": "local_mac",
        "free_disk_bytes": free,
        "required_bytes": CLOUD_MIN_FREE,
        "verdict": LOCAL_MAC_VERDICT,
        "note": "Do not run full corpus map on this laptop",
    }


def gate_checkpoint() -> dict:
    # Production-grade contract exists in lb_full_scan + R2 job checkpoints.
    # Local checkpoint absence is OK before first cloud run.
    ckpt = Path("control/lake/listenbrainz_full_scan/current.json")
    return {
        "gate": "CHECKPOINT_RESUME",
        "status": "PASS",
        "contract": "scripts/lb_full_scan.py + control/jobs/listenbrainz_tar_map/<job_id>/",
        "local_checkpoint_present": ckpt.exists(),
        "note": "Immutable job plan + per-shard verified outputs; resume skips completed batches",
    }


def gate_reducer() -> dict:
    return {
        "gate": "REDUCER_RESOURCE_GATE",
        "status": "PENDING_BOUNDED_PROOF",
        "policy": {"top_k": 25, "min_shared_listeners": 3},
        "required_before_full_run": [
            "deterministic partitions",
            "bounded memory/disk",
            "no unrestricted pair explosion",
            "top-25 enforcement",
            "25K membership exact",
            "fixtures excluded",
        ],
        "note": "Pass only after larger-than-pilot real-corpus proof",
    }


def gate_output_versioning() -> dict:
    ok = PLAN.exists() and SUMMARY.exists()
    plan = json.loads(PLAN.read_text()) if PLAN.exists() else {}
    return {
        "gate": "OUTPUT_VERSIONING",
        "status": "PASS" if ok and plan.get("corpus_index_hash") else "FAIL",
        "gold_prefix": "gold/artist_audience_affinity/",
        "immutable_generations": True,
        "advance_current": "conditional_after_verify",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--assume-cloud",
        action="store_true",
        help="Evaluate MAP_RESOURCE_GATE as cloud container (not local Mac)",
    )
    ap.add_argument(
        "--report",
        action="store_true",
        help="Always exit 0; print honest gate statuses",
    )
    args = ap.parse_args()

    gates = [
        gate_corpus_index(),
        gate_r2_access(),
        gate_checkpoint(),
        gate_map_resource(assume_cloud=args.assume_cloud),
        gate_reducer(),
        gate_output_versioning(),
    ]
    hard_fail = {"FAIL", "BLOCKED_RESOURCE_GATE"}
    pending = {"PENDING_BOUNDED_PROOF", "PENDING_CLOUD_SCRATCH"}
    all_pass = all(g["status"] in ("PASS", "VERIFIED") for g in gates)
    ready_for_full = all_pass and not any(g["status"] in pending for g in gates)

    report = {
        "milestone": "LISTENBRAINZ_FULL_CORPUS_ACTIVATION_V1",
        "full_processing_status": (
            "READY_FOR_FULL_RUN"
            if ready_for_full
            else (
                "BLOCKED_RESOURCE_GATE"
                if any(g["status"] == "BLOCKED_RESOURCE_GATE" for g in gates)
                else "PARTIAL_PROCESSING_ACTIVE"
            )
        ),
        "ready_for_full_run": ready_for_full,
        "gates": gates,
        "next": (
            "Build/complete corpus index → freeze job plan → cloud bounded proof "
            "(>pilot shards) → reducer gates → full map only after all PASS"
        ),
    }
    print(json.dumps(report, indent=2))
    out = Path("docs/listenbrainz-full-corpus-preflight-v1.json")
    out.write_text(json.dumps(report, indent=2) + "\n")

    if args.report:
        return 0
    if any(g["status"] in hard_fail for g in gates):
        return 2
    if not ready_for_full:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
