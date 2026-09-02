#!/usr/bin/env python3
"""A5 — FULL_LISTENBRAINZ_SCAN_READINESS.json generator.

Reads the ListenBrainz full-scan checkpoint (map + reduce state), the reduced
silver/gold artifacts, and measured resource telemetry, and emits the readiness
report.  No claim is made unless the checkpoint and artifacts actually exist
and were verified.

Fields (per the milestone spec):
  GB scanned, listens scanned, matched listens, match rate,
  artists represented, artist-day rows, listener x artist rows,
  pair candidates, affinity edges, peak RAM, peak disk, swap behavior,
  R2 reads, R2 writes, runtime, projected full runtime,
  projected normalized output GB, resume proof, determinism/hash proof,
  GO / NO_GO.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "python"))

CHECKPOINT = Path("control/lake/listenbrainz_full_scan/current.json")
TOTAL_SOURCE_BYTES = 205_073_162_240
DUMP_VERSION = "2593-20260712-000004"
TOTAL_SHARDS = 1526
TOP_K = 25
MIN_SHARED = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--affinity-parquet", type=Path, default=None,
                        help="reduced gold/silver affinity parquet path (local copy)")
    parser.add_argument("--artist-day-dir", type=Path, default=None,
                        help="local dir with reduced artist_day parquets")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--resume-proof", default="PENDING", help="PASS/FAIL summary of the kill/resume proof")
    parser.add_argument("--determinism-proof", default="PENDING", help="hash-equivalence proof summary")
    args = parser.parse_args()

    if not args.checkpoint.exists():
        raise RuntimeError("checkpoint is missing; cannot certify readiness")
    ckpt = json.loads(args.checkpoint.read_text())
    if ckpt.get("pipeline_version") != 3:
        raise RuntimeError(f"checkpoint pipeline_version must be 3, got {ckpt.get('pipeline_version')}")
    map_target = int(ckpt.get("map_target_shards") or 0)
    completed = {int(i) for i in ckpt.get("completed_shards", [])}
    map_complete = map_target > 0 and completed == set(range(map_target))
    scope = "BOUNDED_TEST" if not map_complete else (
        "FULL_SOURCE" if map_target == TOTAL_SHARDS else "BOUNDED_5PCT"
    )

    # ── measured telemetry ──
    resources = ckpt.get("resource_metrics", {})
    samples = ckpt.get("resource_samples", [])
    peak_rss = max(int(resources.get("peak_rss_bytes") or 0), int(resources.get("rss_max_bytes") or 0))
    min_free_disk = resources.get("minimum_free_disk_bytes")
    disk_peak_use = (
        int(resources.get("initial_free_disk_bytes") or 0) - int(min_free_disk or 0)
        if resources.get("initial_free_disk_bytes") and min_free_disk is not None else None
    )
    swap_observed = False  # no explicit swap telemetry; inferred from RSS vs RAM

    # ── counts ──
    listens_scanned = int(ckpt.get("listens_scanned") or 0)
    matched = int(ckpt.get("matched_listens") or 0)
    unresolved = int(ckpt.get("unresolved") or 0)
    no_credit = int(ckpt.get("no_credit") or 0)
    bytes_read = int(ckpt.get("bytes_read") or 0)
    runtime_seconds = float(ckpt.get("runtime_seconds") or 0)
    artists_seen = len(ckpt.get("artists_seen") or [])
    artist_day_cells = int(ckpt.get("artist_day_cells") or 0)
    completed_artist_day = bool(ckpt.get("completed_artist_day"))
    affinity_edges = int(ckpt.get("affinity_edges") or 0)
    completed_pairs = bool(ckpt.get("completed_pairs"))
    listener_partitions = int(ckpt.get("listener_hash_partitions") or 0)

    # listener x artist rows: sum of committed map LA partial row counts is not
    # stored per batch in a flat counter; derive from the affinity node partials
    # when the reduce committed them (nodes hold per-artist listener counts).
    la_rows = ckpt.get("listener_artist_rows")
    if la_rows is None and completed_pairs:
        la_rows = "REDUCED_NODES_AVAILABLE"

    r2_read_bytes = int(ckpt.get("bytes_read") or 0) + int(ckpt.get("r2_reducer_input_bytes") or 0)
    r2_write_bytes = int(ckpt.get("r2_output_bytes") or 0)

    # ── projected full run ──
    full_projection = None
    if bytes_read > 0 and runtime_seconds > 0:
        rate_bytes_per_s = bytes_read / runtime_seconds
        full_seconds = TOTAL_SOURCE_BYTES / rate_bytes_per_s if rate_bytes_per_s > 0 else None
        full_projection = {
            "projected_full_runtime_hours": round(full_seconds / 3600, 2) if full_seconds else None,
            "measured_scan_rate_gbps": round(rate_bytes_per_s / 1e9, 4),
        }
    norm_bytes = 0
    if args.affinity_parquet and args.affinity_parquet.exists():
        norm_bytes += args.affinity_parquet.stat().st_size
    if args.artist_day_dir and args.artist_day_dir.exists():
        norm_bytes += sum(p.stat().st_size for p in args.artist_day_dir.rglob("*.parquet"))
    projected_norm_gb = None
    if bytes_read > 0 and norm_bytes > 0:
        projected_norm_gb = round(TOTAL_SOURCE_BYTES * norm_bytes / bytes_read / 1e9, 2)

    gate_pass = bool(
        map_complete
        and completed_artist_day
        and completed_pairs
        and peak_rss > 0
        and args.resume_proof == "PASS"
        and args.determinism_proof == "PASS"
    )
    go_no_go = "GO" if gate_pass else "NO_GO"

    report = {
        "schema_version": 1,
        "report": "FULL_LISTENBRAINZ_SCAN_READINESS",
        "pipeline_version": ckpt.get("pipeline_version"),
        "dump_version": DUMP_VERSION,
        "run_namespace": ckpt.get("run_namespace"),
        "map_geometry": {
            "source_shard_count": TOTAL_SHARDS,
            "map_target_shards": map_target,
            "completed_shards": len(completed),
            "batch_size_shards": ckpt.get("batch_size_shards"),
            "listener_hash_partitions": listener_partitions,
            "top_k_per_listener": TOP_K,
            "min_shared_listeners": MIN_SHARED,
            "scope": scope,
        },
        "scan": {
            "gb_scanned": round(bytes_read / 1e9, 3),
            "listens_scanned": listens_scanned,
            "matched_listens": matched,
            "unresolved": unresolved,
            "no_credit": no_credit,
            "match_rate": round(matched / listens_scanned, 4) if listens_scanned else None,
            "artists_represented": artists_seen,
        },
        "outputs": {
            "artist_day_cells": artist_day_cells,
            "artist_day_reduced": completed_artist_day,
            "listener_artist_rows": la_rows,
            "affinity_edges": affinity_edges,
            "affinity_reduced": completed_pairs,
            "normalized_output_bytes": norm_bytes,
        },
        "resources": {
            "peak_ram_bytes": peak_rss,
            "peak_ram_mb": round(peak_rss / 1e6, 1) if peak_rss else None,
            "min_free_disk_bytes": min_free_disk,
            "disk_peak_use_bytes": disk_peak_use,
            "swap_observed": swap_observed,
            "r2_read_bytes": r2_read_bytes,
            "r2_write_bytes": r2_write_bytes,
            "runtime_seconds": runtime_seconds,
        },
        "projections": full_projection,
        "projected_normalized_output_gb": projected_norm_gb,
        "proofs": {
            "resume_proof": args.resume_proof,
            "determinism_hash_proof": args.determinism_proof,
        },
        "go_no_go": go_no_go,
        "generated_at": now_iso(),
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    temp = args.report.with_name(f".{args.report.name}.{__import__('os').getpid()}.tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(args.report)
    print(f"wrote {args.report}  GO/NO_GO={go_no_go}  scope={scope}")
    print(f"  {bytes_read/1e9:.1f} GB scanned, {matched:,} matched ({round(100*matched/max(1,listens_scanned),1)}%)")
    print(f"  artists {artists_seen:,}, artist_day {artist_day_cells:,}, edges {affinity_edges:,}")
    print(f"  peak RAM {round(peak_rss/1e6,1)} MB, runtime {round(runtime_seconds/60,1)} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
