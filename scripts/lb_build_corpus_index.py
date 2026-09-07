#!/usr/bin/env python3
"""Build a durable ListenBrainz full-corpus tar index WITHOUT downloading shards.

Header-only R2 Range GETs. Writes the member list in the format already
consumed by scripts/lb_full_scan.py and scripts/lb_pilot.py:

  control/lake/lb_tar_index.json   → [{name, offset, size}, ...]

Plus a hashed envelope (no member list in git):

  control/listenbrainz/full_corpus/index_v1.json
  control/listenbrainz/full_corpus/job_plan_template.json

Optional --upload puts the full index + plan on festival-intelligence-lake.

Usage:
  PYTHONUNBUFFERED=1 PYTHONPATH=python .venv/bin/python scripts/lb_build_corpus_index.py
  PYTHONUNBUFFERED=1 PYTHONPATH=python .venv/bin/python scripts/lb_build_corpus_index.py --upload
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from festival_bloomberg.lake.r2 import r2_client  # noqa: E402

RAW_BUCKET = "festival-intelligence-raw"
LAKE_BUCKET = "festival-intelligence-lake"
RAW_KEY = (
    "bulk/listenbrainz/dump=2593-20260712-000004/"
    "listenbrainz-spark-dump-2593-20260712-000004-full.tar"
)
DUMP_VERSION = "2593-20260712-000004"
LOCAL_INDEX = Path("control/lake/lb_tar_index.json")
SUMMARY_PATH = Path("control/listenbrainz/full_corpus/index_v1.json")
PLAN_PATH = Path("control/listenbrainz/full_corpus/job_plan_template.json")
R2_INDEX_KEY = "control/listenbrainz/full_corpus/index_v1.json"
R2_PLAN_KEY = "control/listenbrainz/full_corpus/job_plan_template.json"
R2_MEMBERS_KEY = "control/listenbrainz/full_corpus/tar_members_v1.json"


def data_shards(members: list[dict]) -> list[dict]:
    out = []
    for m in members:
        base = m["name"].split("/")[-1]
        if base.endswith(".parquet") and base[: -len(".parquet")].isdigit():
            out.append(m)
    return out


def walk_tar_headers(s3, total_size: int) -> list[dict]:
    """Resumable header walk; skip payloads by offset arithmetic."""
    members: list[dict] = []
    off = 0
    if LOCAL_INDEX.exists():
        try:
            cached = json.loads(LOCAL_INDEX.read_text())
            if isinstance(cached, list) and cached:
                members = cached
                last = members[-1]
                off = last["offset"] + ((last["size"] + 511) // 512) * 512
                print(
                    f"resuming from offset {off:,} ({len(members)} members cached)",
                    flush=True,
                )
        except Exception:  # noqa: BLE001
            members, off = [], 0

    t0 = time.time()
    last_flush = time.time()
    while off + 512 <= total_size:
        try:
            resp = s3.get_object(
                Bucket=RAW_BUCKET, Key=RAW_KEY, Range=f"bytes={off}-{off + 511}"
            )
            header = resp["Body"].read(512)
        except Exception as exc:  # noqa: BLE001
            print(f"EOF/error at offset {off:,}: {exc.__class__.__name__}", flush=True)
            break
        if len(header) < 512:
            break
        if header == b"\0" * 512:
            # end-of-archive marker (two zero blocks)
            if off + 1024 <= total_size:
                try:
                    nxt = s3.get_object(
                        Bucket=RAW_BUCKET,
                        Key=RAW_KEY,
                        Range=f"bytes={off + 512}-{off + 1023}",
                    )["Body"].read(512)
                    if nxt == b"\0" * 512:
                        break
                except Exception:  # noqa: BLE001
                    break
            off += 512
            continue
        name = header[0:100].split(b"\0")[0].decode("utf-8", "replace")
        size_field = header[124:136].split(b"\0")[0]
        try:
            size = int(size_field or b"0", 8)
        except ValueError as exc:
            raise RuntimeError(f"TAR_HEADER_PARSE_FAILED offset={off}") from exc
        data_start = off + 512
        members.append({"name": name, "offset": data_start, "size": size})
        off = data_start + ((size + 511) // 512) * 512
        if time.time() - last_flush > 5:
            LOCAL_INDEX.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_INDEX.write_text(json.dumps(members, indent=0) + "\n")
            shards = len(data_shards(members))
            print(
                f"  {len(members)} members / {shards} data shards "
                f"@ {off / (1 << 30):.2f} GiB ({time.time() - t0:.0f}s)",
                flush=True,
            )
            last_flush = time.time()
    LOCAL_INDEX.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_INDEX.write_text(json.dumps(members, indent=0) + "\n")
    print(
        f"index complete: {len(members)} members → {LOCAL_INDEX} "
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )
    return members


def corpus_index_hash(members: list[dict]) -> str:
    material = json.dumps(
        [{"n": m["name"], "o": m["offset"], "s": m["size"]} for m in members],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(material).hexdigest()


def write_artifacts(members: list[dict], *, etag: str, total: int, built_at: str) -> dict:
    shards = data_shards(members)
    cih = corpus_index_hash(members)
    summary = {
        "schema_version": "listenbrainz_full_corpus_index_v1",
        "dump_version": DUMP_VERSION,
        "r2_bucket": RAW_BUCKET,
        "r2_key": RAW_KEY,
        "etag": etag,
        "total_bytes": total,
        "member_count": len(members),
        "data_shard_count": len(shards),
        "corpus_index_hash": cih,
        "built_at": built_at,
        "method": "ustar_header_range_walk",
        "local_members_path": str(LOCAL_INDEX),
        "members_omitted_from_git": True,
        "compat": "scripts/lb_full_scan.py INDEX_CACHE format [{name,offset,size}]",
        "first_data_shard": shards[0] if shards else None,
        "last_data_shard": shards[-1] if shards else None,
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n")

    plan = {
        "schema_version": "listenbrainz_full_corpus_job_plan_v1",
        "job_id_template": "lb_full_{dump}_{index12}_{universe12}_{policy}_{commit8}",
        "dump_version": DUMP_VERSION,
        "corpus_index_hash": cih,
        "corpus_index_key": R2_INDEX_KEY,
        "tar_members_key": R2_MEMBERS_KEY,
        "algorithm_policy": {
            "top_k_artists_per_listener": 25,
            "min_shared_listeners": 3,
            "label": "LISTENBRAINZ CONSUMPTION AFFINITY",
            "not": [
                "ticket_demand",
                "local_demand",
                "fan_crossover_probability",
                "sales_affinity",
            ],
        },
        "resource_limits": {
            "duckdb_memory_limit": "512MB",
            "batch_shards": 4,
            "min_free_disk_bytes": 8 * (1 << 30),
            "max_rss_bytes": 6 * (1 << 30),
            "compute": "cloudflare_batch_container_standard4_NOT_local_mac",
            "container_ephemeral_disk_gb": 20,
        },
        "output_prefixes": {
            "private_partials": "listenbrainz/listener_level/",
            "silver_artist_day": "silver/listenbrainz/artist_day/",
            "gold_affinity": "gold/artist_audience_affinity/",
        },
        "preflight_gates": [
            "CORPUS_INDEX=VERIFIED",
            "CHECKPOINT_RESUME=PASS",
            "MAP_RESOURCE_GATE=PASS",
            "REDUCER_RESOURCE_GATE=PASS",
            "R2_ACCESS=PASS",
            "OUTPUT_VERSIONING=PASS",
        ],
        "processor": "scripts/lb_full_scan.py",
        "cloud_job_type": "listenbrainz_tar_map",
        "created_at": built_at,
        "note": (
            "Retries of the same job_id must reuse this plan. "
            "New inputs/policies require a new job_id. "
            "Do NOT run the full 191 GB map on the local MacBook. "
            "Cloud listenbrainz_map (raw/listenbrainz/*.zst) is a different "
            "layout and must NOT be used for this tar dump."
        ),
    }
    PLAN_PATH.write_text(json.dumps(plan, indent=2) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true")
    ap.add_argument(
        "--summary-only",
        action="store_true",
        help="Rebuild summary/plan from existing local members cache",
    )
    args = ap.parse_args()

    s3 = r2_client()
    print("HEAD corpus…", flush=True)
    head = s3.head_object(Bucket=RAW_BUCKET, Key=RAW_KEY)
    total = int(head["ContentLength"])
    etag = head.get("ETag", "").strip('"')
    print(f"corpus bytes={total} etag={etag}", flush=True)

    if args.summary_only:
        if not LOCAL_INDEX.exists():
            raise SystemExit(f"missing {LOCAL_INDEX}")
        members = json.loads(LOCAL_INDEX.read_text())
        if not isinstance(members, list):
            raise SystemExit("local index must be a member list [{name,offset,size}]")
    else:
        print("walking tar headers (range GETs only)…", flush=True)
        members = walk_tar_headers(s3, total)

    built_at = datetime.now(UTC).isoformat()
    summary = write_artifacts(members, etag=etag, total=total, built_at=built_at)
    print(
        json.dumps(
            {
                "corpus_index_hash": summary["corpus_index_hash"],
                "data_shard_count": summary["data_shard_count"],
                "member_count": summary["member_count"],
                "total_bytes": summary["total_bytes"],
                "ready_for_local_map": False,
                "reason": "local disk insufficient; use cloud batch listenbrainz_tar_map",
            },
            indent=2,
        ),
        flush=True,
    )

    if args.upload:
        print("uploading to lake…", flush=True)
        # Full members list (processing contract)
        s3.put_object(
            Bucket=LAKE_BUCKET,
            Key=R2_MEMBERS_KEY,
            Body=(json.dumps(members) + "\n").encode(),
            ContentType="application/json",
        )
        # Summary envelope
        s3.put_object(
            Bucket=LAKE_BUCKET,
            Key=R2_INDEX_KEY,
            Body=SUMMARY_PATH.read_bytes(),
            ContentType="application/json",
        )
        s3.put_object(
            Bucket=LAKE_BUCKET,
            Key=R2_PLAN_KEY,
            Body=PLAN_PATH.read_bytes(),
            ContentType="application/json",
        )
        print(f"uploaded s3://{LAKE_BUCKET}/{R2_MEMBERS_KEY}", flush=True)
        print(f"uploaded s3://{LAKE_BUCKET}/{R2_INDEX_KEY}", flush=True)
        print(f"uploaded s3://{LAKE_BUCKET}/{R2_PLAN_KEY}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
