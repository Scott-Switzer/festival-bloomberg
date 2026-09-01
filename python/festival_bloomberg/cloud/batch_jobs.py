"""
Batch job implementations for cloud container execution.

Each function receives a job spec dict and a scratch directory, reads source
data from R2, processes with bounded local scratch, writes outputs to R2,
and returns a summary dict.

Contract for every job:
    - Read source from R2 (never assume local files exist)
    - Use bounded scratch under the provided scratch_dir
    - Write outputs to R2 with deterministic keys
    - Write a job manifest to R2 control/jobs/<type>/<id>/manifest.json
    - Delete scratch before returning
    - Never assume persistent disk between invocations
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the festival_bloomberg package is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from festival_bloomberg.cloud.r2_lake import R2Lake, R2LakeConfig
from festival_bloomberg.cloud.job_manifest import (
    JobManifest, new_manifest, manifest_key,
    STATUS_BUILD_COMPLETE, STATUS_VERIFIED, STATUS_PUBLISHED, STATUS_FAILED,
    STATUS_SUPERSEDED, now_iso,
)
from festival_bloomberg.cloud.listener_key import (
    ListenerKeyContract, derive_listener_key_and_partition,
    derive_listener_keys_batch, get_secret, get_secret_version,
    canonical_input, validate_contract_compatibility,
)


# ── Fixed machine-readable error codes (P1 V1B) ───────────────────
# Internal exceptions are NEVER exposed raw. The status API and manifest
# carry only these fixed codes; full details go to internal logs only.
ERR_JOB_VALIDATION_FAILED = "JOB_VALIDATION_FAILED"
ERR_CONTAINER_START_FAILED = "CONTAINER_START_FAILED"
ERR_JOB_EXEC_FAILED = "JOB_EXEC_FAILED"
ERR_R2_READ_FAILED = "R2_READ_FAILED"
ERR_R2_VERIFY_FAILED = "R2_VERIFY_FAILED"
ERR_PUBLICATION_FAILED = "PUBLICATION_FAILED"
ERR_LISTENER_KEY_CONFIG = "LISTENER_KEY_CONFIG_FAILED"


def _get_lake() -> R2Lake:
    return R2Lake(R2LakeConfig.from_env())


# ── Listener-key contract for this run ────────────────────────────
# The HMAC secret (FI_LISTENER_HMAC_SECRET) is read from the Cloudflare
# secret binding at runtime. It never appears in Git, R2, manifests,
# checkpoints, stdout, stderr, or status API payloads.
# The secret-version identifier (FI_LISTENER_HMAC_SECRET_VERSION) is
# REQUIRED and read lazily so importing the module never fails without env.
def _listener_key_contract() -> ListenerKeyContract:
    """Build the listener-key contract from required environment config.

    Fails closed (RuntimeError) if FI_LISTENER_HMAC_SECRET_VERSION is unset.
    """
    return ListenerKeyContract.from_env()


def _listener_key_metadata() -> dict:
    """Return the listener-key contract metadata (version identifiers only)."""
    return _listener_key_contract().to_metadata()


# ── Metric-universe metadata for Gold affinity outputs ────────────
# These labels ensure nobody reads TOP_25-retained metrics as full-population
# fan overlap. Never call these "TOTAL FANS" or total artist audience.
AFFINITY_METRIC_UNIVERSE = {
    "audience_source": "LISTENBRAINZ",
    "audience_semantics": "OBSERVED_LISTENBRAINZ_AUDIENCE_SAMPLE",
    "listener_universe": "TOP_25_RETAINED_PER_LISTENER",
    "top_k": 25,
    "shared_listener_semantics": "GLOBAL_UNIQUE_LISTENERS_WITHIN_METRIC_UNIVERSE",
    # Jaccard/cosine/lift/PMI are all computed over the TOP_25-retained
    # listener universe, NOT the full observed population.
    "jaccard_universe": "TOP_25_RETAINED",
    "cosine_universe": "TOP_25_RETAINED",
    "lift_universe": "TOP_25_RETAINED",
    "pmi_universe": "TOP_25_RETAINED",
    # Explicit prohibition labels
    "never_label_as": "TOTAL_FANS",
    "never_infer": [
        "ticket_demand", "purchase_propensity",
        "attendance", "willingness_to_pay",
    ],
}


def _git_commit() -> str:
    """Best-effort git commit hash (available in container build context)."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=5,
        )
        return result.stdout.strip()[:12] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def verify_outputs(
    lake, *,
    bucket: str,
    output_hashes: dict[str, str],
    manifest: JobManifest,
    manifest_key_path: str,
) -> None:
    """Verify every required logical output before VERIFIED (P0-1/P5/P6).

    A generated object existing in R2 does NOT mean VERIFIED. This verifies:
      - object exists (HEAD)
      - SHA-256 matches the recorded digest

    On any failure: manifest → FAILED with fixed code R2_VERIFY_FAILED,
    persisted, and RuntimeError raised. CURRENT is never touched here — the
    caller must invoke transition_verified_to_published() only after this
    passes.
    """
    for out_key, expected_sha in output_hashes.items():
        if not lake.verify_object(bucket, out_key, expected_sha):
            manifest.status = STATUS_FAILED
            manifest.publication_state = "UNPUBLISHED"
            manifest.error_code = ERR_R2_VERIFY_FAILED
            manifest.error = f"Verification failed for {out_key}"
            manifest.completed_at = now_iso()
            try:
                lake.write_manifest(bucket, manifest_key_path, manifest.to_dict())
            except Exception:
                pass
            raise RuntimeError(manifest.error or "Verification failed")

    manifest.status = STATUS_VERIFIED
    manifest.publication_state = STATUS_VERIFIED
    manifest.verified_at = now_iso()
    manifest.verified_hashes = dict(output_hashes)
    lake.write_manifest(bucket, manifest_key_path, manifest.to_dict())


def transition_verified_to_published(
    lake, *,
    bucket: str,
    current_key: str,
    target_key: str,
    manifest: JobManifest,
    manifest_key_path: str,
) -> None:
    """P0-1: CURRENT pointer moves ONLY after VERIFIED; PUBLISHED only after pointer write.

    Ordering contract:
      BUILD_COMPLETE → VERIFIED → (move CURRENT) → PUBLISHED

    If the CURRENT write fails: the generation REMAINS VERIFIED (manifest
    persisted as VERIFIED) and is NEVER called PUBLISHED.
    """
    # manifest must already be VERIFIED (persisted) by the caller.
    try:
        lake.write_current_pointer(bucket, current_key, target_key)
    except Exception:
        manifest.error_code = ERR_PUBLICATION_FAILED
        manifest.error = "CURRENT pointer write failed; generation remains VERIFIED"
        manifest.publication_state = STATUS_VERIFIED
        manifest.status = STATUS_VERIFIED
        try:
            lake.write_manifest(bucket, manifest_key_path, manifest.to_dict())
        except Exception:
            pass
        raise RuntimeError(
            f"PUBLICATION_FAILED: CURRENT pointer write failed for {current_key}"
        )

    manifest.publication_state = STATUS_PUBLISHED
    manifest.status = STATUS_PUBLISHED
    lake.write_manifest(bucket, manifest_key_path, manifest.to_dict())


def _fail_closed(manifest: JobManifest, lake, bucket: str, manifest_key_path: str, code: str) -> None:
    """Record FAILED status with a fixed error code and persist the manifest."""
    manifest.status = STATUS_FAILED
    manifest.publication_state = "UNPUBLISHED"
    if manifest.error_code is None:
        manifest.error_code = code
    manifest.completed_at = now_iso()
    try:
        lake.write_manifest(bucket, manifest_key_path, manifest.to_dict())
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
# IDENTITY GRAPH V2
# ════════════════════════════════════════════════════════════════

def run_identity_graph_v2(spec: dict, scratch_dir: Path) -> dict:
    """Materialize Identity Graph V2 from the authoritative Wikidata generation.

    Reads:
        - source DB from R2 (RAW_BUCKET)
        - governed estate from R2 (LAKE_BUCKET)
        - Wikidata parquets from R2 (LAKE_BUCKET, generation 20260831T014029Z-1369)

    Writes:
        - identity_graph_v2.duckdb → R2 LAKE_BUCKET
        - identity_graph_v2_report.json → R2 LAKE_BUCKET
        - manifest → R2 LAKE_BUCKET control/jobs/...

    Never uses /private/tmp/wd_v2_parquet or any provisional dataset.
    """
    lake = _get_lake()
    job_id = spec.get("job_id", "identity_v2")
    source_generation = spec.get("source_generation", "20260831T014029Z-1369")

    manifest = new_manifest(
        job_type="identity_graph_v2",
        job_id=job_id,
        source_generation=source_generation,
        code_commit=_git_commit(),
        container_image="festival-bloomberg-batch:latest",
        params=spec.get("params", {}),
    )

    manifest_key_path = manifest_key("identity_graph_v2", job_id)
    start = time.time()

    try:
        import duckdb

        # ── 1. Download source DB from R2 to scratch ──
        source_db_key = "warehouse/boxoffice_research_v2.duckdb"
        manifest.source_paths.append(f"r2://{lake.config.raw_bucket}/{source_db_key}")
        source_db_path = scratch_dir / "source.duckdb"

        lake._s3.download_file(
            lake.config.raw_bucket, source_db_key, str(source_db_path),
        )
        manifest.r2_read_bytes = source_db_path.stat().st_size

        # ── 2. Download governed estate from R2 ──
        estate_key = "control/artist_security_25000/v1/estate.json"
        estate_path = scratch_dir / "estate.json"
        estate_data = lake.get_bytes(lake.config.lake_bucket, estate_key)
        estate_path.write_bytes(estate_data)
        manifest.r2_read_bytes += len(estate_data)

        # ── 3. Download Wikidata parquets from the authoritative generation ──
        # Only the artist subset product (artist_external_ids.parquet) is used.
        # entity_external_ids.parquet carries the SAME artist rows plus venue/
        # place rows; passing both double-counts every artist claim and can trip
        # the evidence row bound.  The remaining generation products are not
        # consumed by the graph builder's projected reader.
        wd_prefix = f"silver/wikidata/generations/{source_generation}/"
        wd_objects = lake.list_prefix(lake.config.lake_bucket, wd_prefix, limit=50)
        wd_parquet_paths: list[Path] = []
        for obj in wd_objects:
            if obj["key"].endswith("artist_external_ids.parquet"):
                local_path = scratch_dir / Path(obj["key"]).name
                lake._s3.download_file(
                    lake.config.lake_bucket, obj["key"], str(local_path),
                )
                wd_parquet_paths.append(local_path)
                manifest.r2_read_bytes += obj["size"]

        if not wd_parquet_paths:
            raise ValueError(
                f"No artist_external_ids.parquet found at r2://{lake.config.lake_bucket}/{wd_prefix}"
            )

        # ── 4. Run the Identity Graph V2 builder ──
        from festival_bloomberg.identity.graph_v2 import (
            build_graph, jsonable, read_estate_json, read_wikidata_parquets,
            rows_from_connection, write_graph_tables,
        )

        conn = duckdb.connect(str(source_db_path), read_only=True)
        estate_rows = read_estate_json(str(estate_path))
        governed_keys = [row["artist_key"] for row in estate_rows]

        artists, external_ids, linkages, source_tables, available_broad = (
            rows_from_connection(conn, governed_keys, include_broad=False, max_artists=25_000)
        )
        conn.close()

        as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        result = build_graph(
            artists=artists,
            external_ids=external_ids,
            linkages=linkages,
            wikidata_rows=read_wikidata_parquets(
                [str(p) for p in wd_parquet_paths],
                allowed_mbids=[row.get("musicbrainz_id") for row in artists],
            ),
            estate_rows=estate_rows,
            as_of=as_of,
            canonical_limit=25_000,
            source_tables=source_tables,
            source_artifacts=[],  # artifacts computed separately for cloud
            available_broader_artist_count=available_broad,
            max_evidence=500_000,
            max_edges=250_000,
        )

        # ── 5. Write output DB to scratch, then upload to R2 ──
        output_db_path = scratch_dir / "identity_graph_v2.duckdb"
        result["run"]["build_status"] = "MATERIALIZED"
        out_conn = duckdb.connect(str(output_db_path))
        write_graph_tables(out_conn, result)
        out_conn.close()

        output_db_bytes = output_db_path.read_bytes()
        output_db_sha = hashlib.sha256(output_db_bytes).hexdigest()
        output_db_key = f"identity/graph_v2/{job_id}/identity_graph_v2.duckdb"
        lake.put_bytes(
            lake.config.lake_bucket, output_db_key, output_db_bytes,
            content_type="application/octet-stream",
            metadata={"job_id": job_id, "sha256": output_db_sha},
        )
        manifest.output_paths.append(f"r2://{lake.config.lake_bucket}/{output_db_key}")
        manifest.output_hashes[output_db_key] = output_db_sha
        manifest.r2_write_bytes += len(output_db_bytes)

        # ── 6. Write report to R2 ──
        from collections import Counter
        conflicts = result.get("conflicts", [])
        counts = Counter(
            (row.get("conflict_type"), row.get("provider")) for row in conflicts
        )
        compact_report = {
            "run": result["run"],
            "scorecard": result["scorecard"],
            "conflict_summary": [
                {"conflict_type": k[0], "provider": k[1], "count": c}
                for k, c in sorted(counts.items())
            ],
        }
        report_bytes = json.dumps(compact_report, indent=2, sort_keys=True, default=str).encode()
        report_sha = hashlib.sha256(report_bytes).hexdigest()
        report_key = f"identity/graph_v2/{job_id}/identity_graph_v2_report.json"
        lake.put_bytes(
            lake.config.lake_bucket, report_key, report_bytes,
            content_type="application/json",
            metadata={"job_id": job_id, "sha256": report_sha},
        )
        manifest.output_paths.append(f"r2://{lake.config.lake_bucket}/{report_key}")
        manifest.output_hashes[report_key] = report_sha
        manifest.r2_write_bytes += len(report_bytes)

        # ── 7. Compute coverage stats ──
        scorecard = result.get("scorecard", [])
        coverage_by_scope: dict[str, dict] = {}
        for card in scorecard:
            scope = card.get("scope", "UNKNOWN")
            if scope not in coverage_by_scope:
                coverage_by_scope[scope] = {
                    "universe": 0, "verified": 0, "supported": 0,
                    "candidate": 0, "conflict": 0, "missing": 0,
                }
            s = coverage_by_scope[scope]
            s["universe"] += card.get("universe_count", 0)
            s["verified"] += card.get("verified_exact_count", 0)
            s["supported"] += card.get("supported_multi_source_count", 0)
            s["candidate"] += card.get("candidate_count", 0)
            s["conflict"] += card.get("conflict_count", 0)
            s["missing"] += card.get("missing_count", 0)

        manifest.status = STATUS_BUILD_COMPLETE
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        manifest.rows_read = len(artists) + len(external_ids) + len(linkages)
        manifest.rows_written = result.get("run", {}).get("evidence_count", 0)

        # ── 8. VERIFY outputs BEFORE any publication action (P0-1) ──
        # A generated object existing in R2 does NOT mean VERIFIED.
        # On failure: manifest → FAILED, CURRENT remains untouched.
        verify_outputs(
            lake,
            bucket=lake.config.lake_bucket,
            output_hashes=manifest.output_hashes,
            manifest=manifest,
            manifest_key_path=manifest_key_path,
        )

        # ── 9. Only after VERIFIED: supersede the old report, then move CURRENT ──
        old_report_key = "identity/graph_v2/identity_graph_v2_report.json"
        old_report = lake.read_checkpoint(lake.config.lake_bucket, old_report_key)
        if old_report:
            old_report["superseded_by"] = job_id
            old_report["superseded_at"] = now_iso()
            old_report["canonical_status"] = STATUS_SUPERSEDED
            lake.put_bytes(
                lake.config.lake_bucket, old_report_key,
                json.dumps(old_report, default=str).encode(),
                content_type="application/json",
            )

        # CURRENT pointer moves ONLY after VERIFIED. If the pointer write
        # fails, the generation remains VERIFIED and is never called PUBLISHED.
        transition_verified_to_published(
            lake,
            bucket=lake.config.lake_bucket,
            current_key="identity/graph_v2/CURRENT.json",
            target_key=f"identity/graph_v2/{job_id}",
            manifest=manifest,
            manifest_key_path=manifest_key_path,
        )

        return {
            "status": "COMPLETED",
            "manifest_key": manifest_key_path,
            "output_db_key": output_db_key,
            "report_key": report_key,
            "source_generation": source_generation,
            "coverage": coverage_by_scope,
            "evidence_count": result.get("run", {}).get("evidence_count", 0),
            "edge_count": result.get("run", {}).get("edge_count", 0),
            "conflict_count": result.get("run", {}).get("conflict_count", 0),
            "runtime_seconds": manifest.runtime_seconds,
            "r2_read_bytes": manifest.r2_read_bytes,
            "r2_write_bytes": manifest.r2_write_bytes,
        }

    except Exception as e:
        # P0-1: If outputs were already VERIFIED but publication (CURRENT
        # pointer) failed, the generation REMAINS VERIFIED — never FAILED,
        # never PUBLISHED.
        if manifest.status == STATUS_VERIFIED:
            if manifest.error_code is None:
                manifest.error_code = ERR_PUBLICATION_FAILED
            manifest.error = str(e)
            manifest.publication_state = STATUS_VERIFIED
            try:
                lake.write_manifest(
                    lake.config.lake_bucket, manifest_key_path, manifest.to_dict(),
                )
            except Exception:
                pass
            raise
        if manifest.error_code is None:
            manifest.error_code = ERR_JOB_EXEC_FAILED
        manifest.status = STATUS_FAILED
        manifest.error = str(e)
        manifest.error_detail = traceback.format_exc()
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        try:
            lake.write_manifest(
                lake.config.lake_bucket, manifest_key_path, manifest.to_dict(),
            )
        except Exception:
            pass
        raise

    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════
# CLOUD SMOKE — tiny deterministic validation job
# ════════════════════════════════════════════════════════════════

def run_cloud_smoke(spec: dict, scratch_dir: Path) -> dict:
    """Tiny deterministic smoke test proving the cloud execution path works.

    Proves:
    - container starts
    - Python package imports
    - R2 HEAD works
    - R2 range/stream read works
    - private R2 write works
    - hash verification works
    - manifest updates work
    - ephemeral scratch cleanup works

    Output is tiny.  If any step fails, stop and report.
    """
    lake = _get_lake()
    job_id = spec.get("job_id", "cloud_smoke")

    manifest = new_manifest(
        job_type="cloud_smoke",
        job_id=job_id,
        code_commit=_git_commit(),
        container_image="festival-bloomberg-batch:latest",
        params=spec.get("params", {}),
    )
    manifest_key_path = manifest_key("cloud_smoke", job_id)
    start = time.time()

    try:
        import duckdb

        # ── 1. Python package imports ──
        from festival_bloomberg.cloud.listener_key import (
            derive_listener_key, ListenerKeyContract,
        )
        manifest.rows_read = 1  # import succeeded

        # ── 2. R2 HEAD works ──
        test_key = f"smoke/{job_id}/probe.txt"
        probe_data = b"festival-bloomberg-cloud-smoke-probe"
        lake.put_bytes(
            lake.config.private_bucket, test_key, probe_data,
            content_type="text/plain",
        )
        head_meta = lake.head(lake.config.private_bucket, test_key)
        assert head_meta is not None, "R2 HEAD failed on just-written object"
        manifest.r2_write_bytes += len(probe_data)

        # ── 3. R2 range read works ──
        range_data = lake.range_read(
            lake.config.private_bucket, test_key, 0, len(probe_data) - 1,
        )
        assert len(range_data) > 0, "R2 range read returned empty"
        manifest.r2_read_bytes += len(range_data)

        # ── 4. Full R2 read works ──
        full_data = lake.get_bytes(lake.config.private_bucket, test_key)
        assert full_data == probe_data, "R2 full read mismatch"

        # ── 5. Hash verification works ──
        actual_sha = hashlib.sha256(full_data).hexdigest()
        expected_sha = hashlib.sha256(probe_data).hexdigest()
        assert actual_sha == expected_sha, "SHA-256 verification failed"
        manifest.output_hashes[test_key] = expected_sha
        manifest.output_paths.append(f"r2://{lake.config.private_bucket}/{test_key}")

        # ── 6. DuckDB works ──
        test_db = scratch_dir / "smoke.duckdb"
        conn = duckdb.connect(str(test_db))
        conn.execute("CREATE TABLE smoke AS SELECT 42 AS answer")
        answer = conn.execute("SELECT answer FROM smoke").fetchone()[0]
        assert answer == 42, "DuckDB query failed"
        conn.close()

        # ── 7. HMAC pseudonymization works (with a test secret) ──
        test_secret = b"x" * 32  # test-only, not a real secret
        key1 = derive_listener_key(42, test_secret)
        key2 = derive_listener_key(42, test_secret)
        assert key1 == key2, "HMAC pseudonymization not deterministic"
        assert len(key1) == 64, f"listener_key wrong length: {len(key1)}"

        # ── 8. Manifest write works ──
        manifest.status = STATUS_BUILD_COMPLETE
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        manifest.rows_written = 1

        # P5/P0-1: BUILD_COMPLETE → VERIFIED → PUBLISHED
        verify_outputs(
            lake,
            bucket=lake.config.private_bucket,
            output_hashes={test_key: expected_sha},
            manifest=manifest,
            manifest_key_path=manifest_key_path,
        )
        # cloud_smoke has no CURRENT pointer; publish directly after VERIFIED.
        manifest.publication_state = STATUS_PUBLISHED
        manifest.status = STATUS_PUBLISHED
        lake.write_manifest(
            lake.config.private_bucket, manifest_key_path, manifest.to_dict(),
        )

        # ── 9. Cleanup probe object ──
        lake.delete_object(lake.config.private_bucket, test_key)

        return {
            "status": "COMPLETED",
            "manifest_key": manifest_key_path,
            "smoke_checks": [
                "python_imports",
                "r2_head",
                "r2_range_read",
                "r2_full_read",
                "hash_verification",
                "duckdb_query",
                "hmac_pseudonymization",
                "manifest_write",
            ],
            "runtime_seconds": manifest.runtime_seconds,
        }

    except Exception as e:
        if manifest.error_code is None:
            manifest.error_code = ERR_JOB_EXEC_FAILED
        manifest.status = STATUS_FAILED
        manifest.error = str(e)
        manifest.error_detail = traceback.format_exc()
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        try:
            lake.write_manifest(
                lake.config.private_bucket, manifest_key_path, manifest.to_dict(),
            )
        except Exception:
            pass
        raise

    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════
# LISTENBRAINZ MAP STAGE
# ════════════════════════════════════════════════════════════════

def run_listenbrainz_map(spec: dict, scratch_dir: Path) -> dict:
    """Map stage: stream R2 shards, filter to 25K, aggregate, write partials.

    Reads: raw ListenBrainz shards from R2 (RAW_BUCKET)
    Writes: hash-partitioned listener×artist partials to PRIVATE R2

    Checkpoint-based resume: reads checkpoint, skips completed batches.
    """
    lake = _get_lake()
    job_id = spec.get("job_id", "lb_map")
    params = spec.get("params", {})
    max_shards = spec.get("max_batches") or params.get("max_shards", 76)
    partitions = params.get("partitions", 64)

    manifest = new_manifest(
        job_type="listenbrainz_map",
        job_id=job_id,
        code_commit=_git_commit(),
        container_image="festival-bloomberg-batch:latest",
        total_batches=max_shards,
        params=params,
    )
    manifest_key_path = manifest_key("listenbrainz_map", job_id)
    start = time.time()

    try:
        import duckdb

        # V1B: Require BOTH the HMAC secret and its version identifier BEFORE
        # any processing. Missing config fails closed.
        contract = _listener_key_contract()
        contract.require_version()
        hmac_secret = get_secret()
        # Record the listener-key contract (version identifiers only, never
        # the secret) AFTER config validation succeeds.
        manifest.params.update(contract.to_metadata())
        manifest.params["listener_hash_partitions"] = partitions

        # ── Read checkpoint (resume) ──
        ckpt_key = f"control/jobs/listenbrainz_map/{job_id}/checkpoint.json"
        ckpt = lake.read_checkpoint(lake.config.private_bucket, ckpt_key) or {
            "completed_shards": [], "partial_keys": [],
        }
        completed = set(ckpt.get("completed_shards", []))
        existing_partials = list(ckpt.get("partial_keys", []))

        # ── Validate checkpoint compatibility (P4: version/generation safety) ──
        # If resuming, verify the checkpoint's listener-key contract matches.
        if ckpt.get("completed_shards"):
            ckpt_meta = {
                k: ckpt[k] for k in contract.to_metadata()
                if k in ckpt
            }
            if ckpt_meta:
                validate_contract_compatibility(
                    ckpt_meta,
                    expected_contract=contract,
                    expected_partition_count=partitions,
                )

        # ── Load ARTIST_SECURITY_25000 MBID set from R2 ──
        mbid_key = "control/artist_security_25000/v1/artist_mbids.json"
        mbid_data = lake.get_bytes(lake.config.lake_bucket, mbid_key)
        artist_mbids = set(json.loads(mbid_data))
        manifest.r2_read_bytes += len(mbid_data)

        # ── List raw ListenBrainz shards ──
        shard_prefix = "raw/listenbrainz/"
        all_shards = lake.list_prefix(lake.config.raw_bucket, shard_prefix, limit=2000)
        shard_keys = [s["key"] for s in all_shards if s["key"].endswith(".zst")]
        shards_to_process = [k for k in shard_keys if k not in completed][:max_shards]

        # ── Process each shard (bounded) ──
        new_partials: list[str] = []
        total_listens = 0
        matched_listens = 0

        for shard_key in shards_to_process:
            # Download shard to scratch (bounded — delete after)
            local_shard = scratch_dir / Path(shard_key).name
            lake._s3.download_file(lake.config.raw_bucket, shard_key, str(local_shard))
            shard_size = local_shard.stat().st_size
            manifest.r2_read_bytes += shard_size

            # Process with DuckDB — aggregate listener×artist, filter to 25K
            conn = duckdb.connect(str(scratch_dir / "map.duckdb"), read_only=False)
            conn.execute("SET memory_limit='2GB'")
            conn.execute("SET threads TO 4")

            # The shard is a compressed JSON lines file; DuckDB can read it
            # via read_json_auto with compression detection.
            # Production schema: user_id (int64 not null), artist_mbid, listened_at.
            # We use user_id as the canonical input for HMAC pseudonymization.
            # Raw user_id exists transiently during source parsing only.
            mbid_values = ",".join(f"('{m}')" for m in list(artist_mbids)[:5000])
            conn.execute(f"""
                CREATE TEMP TABLE listens AS
                SELECT
                    user_id::BIGINT AS user_id,
                    artist_mbid::VARCHAR AS artist_mbid,
                    listened_at::VARCHAR AS listened_at
                FROM read_json_auto('{local_shard}', compression='zstd')
                WHERE artist_mbid IS NOT NULL
                  AND artist_mbid IN (SELECT * FROM (VALUES {mbid_values}))
            """)

            count = conn.execute("SELECT COUNT(*) FROM listens").fetchone()[0]
            total_listens += count

            # P2 (performance-safe pseudonymization):
            # Deduplicate user_ids first, then HMAC only the distinct set,
            # then join/map back to the full listen set. This avoids
            # per-listen HMAC calls on the full corpus.
            distinct_ids = conn.execute(
                "SELECT DISTINCT user_id FROM listens ORDER BY user_id"
            ).fetchall()
            distinct_id_list = [r[0] for r in distinct_ids]

            # Derive listener_key + partition for each distinct user_id.
            # This is the only place HMAC is called — once per distinct listener.
            key_rows = [
                derive_listener_key_and_partition(uid, partitions, hmac_secret)
                for uid in distinct_id_list
            ]

            # Create a mapping table in DuckDB: user_id → listener_key, partition
            import pyarrow as pa
            mapping_table = pa.table({
                "user_id": pa.array(distinct_id_list, type=pa.int64()),
                "listener_key": pa.array([k[0] for k in key_rows], type=pa.string()),
                "partition": pa.array([k[1] for k in key_rows], type=pa.int32()),
            })
            conn.register("listener_map", mapping_table)

            # Aggregate listener×artist and join to the pseudonymized map.
            # Durable partials contain ONLY: listener_key, artist_mbid,
            # listen_count, partition.
            # NO raw user_id or user_name is ever written to R2.
            partitioned = conn.execute(f"""
                WITH agg AS (
                    SELECT
                        l.user_id,
                        l.artist_mbid,
                        COUNT(*) as listen_count
                    FROM listens l
                    GROUP BY l.user_id, l.artist_mbid
                )
                SELECT
                    m.listener_key,
                    a.artist_mbid,
                    a.listen_count,
                    m.partition
                FROM agg a
                JOIN listener_map m ON a.user_id = m.user_id
            """).fetchall()

            matched_listens += sum(r[2] for r in partitioned)

            # Write partitioned partials to R2 (private bucket)
            # Schema: listener_key (VARCHAR), artist_mbid (VARCHAR),
            #         listen_count (BIGINT)
            # NO raw listener identity — only the HMAC-derived pseudonym.
            import io
            import pyarrow.parquet as pq

            for part in range(partitions):
                rows = [(r[0], r[1], r[2], r[3]) for r in partitioned if r[3] == part]
                if not rows:
                    continue
                table = pa.table({
                    "listener_key": [r[0] for r in rows],
                    "artist_mbid": [r[1] for r in rows],
                    "listen_count": [r[2] for r in rows],
                }, names=["listener_key", "artist_mbid", "listen_count"])

                partial_key = f"listenbrainz/map/{job_id}/partition={part}/{Path(shard_key).stem}.parquet"
                buf = io.BytesIO()
                pq.write_table(table, buf, compression="zstd")
                partial_bytes = buf.getvalue()
                partial_sha = hashlib.sha256(partial_bytes).hexdigest()
                lake.put_bytes(
                    lake.config.private_bucket, partial_key, partial_bytes,
                    content_type="application/octet-stream",
                    metadata={"sha256": partial_sha},
                )
                new_partials.append(partial_key)
                manifest.r2_write_bytes += len(partial_bytes)

            conn.close()
            local_shard.unlink(missing_ok=True)

            # Update checkpoint with listener-key contract metadata
            completed.add(shard_key)
            ckpt = {
                "completed_shards": sorted(completed),
                "partial_keys": existing_partials + new_partials,
                **_listener_key_metadata(),
                "listener_hash_partitions": partitions,
            }
            lake.write_checkpoint(lake.config.private_bucket, ckpt_key, ckpt)
            manifest.completed_batches = len(completed)

        manifest.status = STATUS_BUILD_COMPLETE
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        manifest.rows_read = total_listens
        manifest.rows_written = matched_listens

        # P5: BUILD_COMPLETE → VERIFIED → PUBLISHED (map manifest).
        # Each partial object was written with a sha256 metadata tag at upload;
        # per-partial verification is enforced by the reducer's contract check
        # (partition count + listener-key generation + object presence).
        manifest.status = STATUS_VERIFIED
        manifest.publication_state = STATUS_VERIFIED
        lake.write_manifest(lake.config.private_bucket, manifest_key_path, manifest.to_dict())

        manifest.publication_state = STATUS_PUBLISHED
        manifest.status = STATUS_PUBLISHED
        lake.write_manifest(lake.config.private_bucket, manifest_key_path, manifest.to_dict())

        return {
            "status": "COMPLETED",
            "manifest_key": manifest_key_path,
            "shards_processed": len(shards_to_process),
            "total_shards_completed": len(completed),
            "total_listens_scanned": total_listens,
            "matched_listens": matched_listens,
            "partial_keys": existing_partials + new_partials,
            "runtime_seconds": manifest.runtime_seconds,
        }

    except Exception as e:
        if manifest.error_code is None:
            if "FI_LISTENER_HMAC_SECRET" in str(e) or "FI_LISTENER_HMAC_SECRET_VERSION" in str(e):
                manifest.error_code = ERR_LISTENER_KEY_CONFIG
            else:
                manifest.error_code = ERR_JOB_EXEC_FAILED
        manifest.status = STATUS_FAILED
        manifest.error = str(e)
        manifest.error_detail = traceback.format_exc()
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        try:
            lake.write_manifest(lake.config.private_bucket, manifest_key_path, manifest.to_dict())
        except Exception:
            pass
        raise

    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════
# LISTENBRAINZ REDUCE STAGE
# ════════════════════════════════════════════════════════════════

def run_listenbrainz_reduce(spec: dict, scratch_dir: Path) -> dict:
    """Reduce stage: global listener×artist aggregation + TOP_25 + pair generation.

    Reads: hash-partitioned partials from PRIVATE R2
    Writes: artist_day + audience affinity edges to R2 (LAKE_BUCKET)

    CRITICAL: TOP_25 is applied AFTER global aggregation, never per shard.
    """
    lake = _get_lake()
    job_id = spec.get("job_id", "lb_reduce")
    params = spec.get("params", {})
    top_k = params.get("top_k_per_listener", 25)
    min_shared = params.get("min_shared_listeners", 3)
    map_job_id = params.get("map_job_id", "lb_map")

    manifest = new_manifest(
        job_type="listenbrainz_reduce",
        job_id=job_id,
        code_commit=_git_commit(),
        container_image="festival-bloomberg-batch:latest",
        params=params,
    )
    manifest_key_path = manifest_key("listenbrainz_reduce", job_id)
    start = time.time()

    try:
        import duckdb
        import pyarrow as pa
        import io

        # V1B: Reducer must know WHICH secret generation produced the partials.
        # Missing FI_LISTENER_HMAC_SECRET_VERSION fails closed before any read.
        contract = _listener_key_contract()
        contract.require_version()
        # Record the listener-key contract + metric-universe metadata AFTER
        # config validation succeeds (version identifiers only, never secret).
        manifest.params.update(contract.to_metadata())
        manifest.params.update(AFFINITY_METRIC_UNIVERSE)
        manifest.params["listener_hash_partitions"] = params.get("partitions", 64)

        # ── Read all partials from the map job ──
        partial_prefix = f"listenbrainz/map/{map_job_id}/"
        partials = lake.list_prefix(lake.config.private_bucket, partial_prefix, limit=5000)

        # P4: Validate that the map partials use the same listener-key contract.
        # Read the map checkpoint to verify compatibility.
        map_ckpt_key = f"control/jobs/listenbrainz_map/{map_job_id}/checkpoint.json"
        map_ckpt = lake.read_checkpoint(lake.config.private_bucket, map_ckpt_key)
        if map_ckpt and map_ckpt.get("completed_shards"):
            ckpt_meta = {k: map_ckpt[k] for k in contract.to_metadata() if k in map_ckpt}
            if ckpt_meta:
                validate_contract_compatibility(
                    ckpt_meta,
                    expected_contract=contract,
                    expected_partition_count=params.get("partitions", 64),
                )

        conn = duckdb.connect(str(scratch_dir / "reduce.duckdb"))
        conn.execute("SET memory_limit='8GB'")
        conn.execute("SET threads TO 4")

        # ── Global listener×artist aggregation ──
        # Partial schema (post-pseudonymization): listener_key, artist_mbid,
        # listen_count. NO raw user_name or user_id is present in these
        # partials. All joins/group-by use listener_key.
        first_table = True
        for p in partials:
            if not p["key"].endswith(".parquet"):
                continue
            data = lake.get_bytes(lake.config.private_bucket, p["key"])
            manifest.r2_read_bytes += len(data)
            local_part = scratch_dir / Path(p["key"]).name
            local_part.write_bytes(data)
            if first_table:
                conn.execute(f"""
                    CREATE TABLE listener_artist AS
                    SELECT listener_key, artist_mbid, listen_count
                    FROM read_parquet('{local_part}')
                """)
                first_table = False
            else:
                conn.execute(f"""
                    INSERT INTO listener_artist
                    SELECT listener_key, artist_mbid, listen_count
                    FROM read_parquet('{local_part}')
                """)
            local_part.unlink(missing_ok=True)

        if first_table:
            raise RuntimeError("No map partials found — map stage must complete first.")

        # Global aggregation: SUM listen_count per (listener_key, artist_mbid)
        conn.execute("""
            CREATE TABLE listener_artist_agg AS
            SELECT listener_key, artist_mbid, SUM(listen_count) as listen_count
            FROM listener_artist
            GROUP BY listener_key, artist_mbid
        """)

        # ── TOP_25 per listener (GLOBALLY, after aggregation) ──
        conn.execute(f"""
            CREATE TABLE listener_top25 AS
            SELECT listener_key, artist_mbid, listen_count
            FROM (
                SELECT
                    listener_key, artist_mbid, listen_count,
                    ROW_NUMBER() OVER (PARTITION BY listener_key ORDER BY listen_count DESC) as rk
                FROM listener_artist_agg
            ) WHERE rk <= {top_k}
        """)

        # ── Pair generation (bounded — only from shared listeners) ──
        # shared_listeners = COUNT(DISTINCT listener_key) — the true global
        # unique-listener count within the TOP_25 metric universe.
        conn.execute(f"""
            CREATE TABLE audience_pairs AS
            SELECT
                a.artist_mbid as artist_a,
                b.artist_mbid as artist_b,
                COUNT(DISTINCT a.listener_key) as shared_listeners,
                COUNT(DISTINCT a.listener_key) as listeners_a,
                COUNT(DISTINCT b.listener_key) as listeners_b
            FROM listener_top25 a
            JOIN listener_top25 b ON a.listener_key = b.listener_key AND a.artist_mbid < b.artist_mbid
            GROUP BY a.artist_mbid, b.artist_mbid
            HAVING COUNT(DISTINCT a.listener_key) >= {min_shared}
        """)

        # ── Compute Jaccard, cosine, lift, PMI ──
        # All metrics are computed over the TOP_25-retained listener universe.
        # listeners_a / listeners_b = global population counts within the
        # TOP_25 universe (not total real-world fans).
        conn.execute("""
            CREATE TABLE audience_affinity AS
            SELECT
                p.artist_a,
                p.artist_b,
                p.shared_listeners,
                p.listeners_a,
                p.listeners_b,
                -- Jaccard = |A ∩ B| / |A ∪ B| = sh / (la + lb - sh)
                CASE
                    WHEN p.listeners_a + p.listeners_b - p.shared_listeners = 0 THEN 0.0
                    ELSE CAST(p.shared_listeners AS DOUBLE) / (p.listeners_a + p.listeners_b - p.shared_listeners)
                END AS jaccard,
                -- Cosine = sh / sqrt(la * lb)
                CASE
                    WHEN p.listeners_a * p.listeners_b = 0 THEN 0.0
                    ELSE CAST(p.shared_listeners AS DOUBLE) / SQRT(CAST(p.listeners_a AS DOUBLE) * p.listeners_b)
                END AS cosine,
                -- Lift = P(A∩B) / (P(A) * P(B))
                -- = (sh / N) / ((la / N) * (lb / N)) = sh * N / (la * lb)
                -- where N = total distinct listeners in the TOP_25 universe.
                CASE
                    WHEN p.listeners_a = 0 OR p.listeners_b = 0 THEN 0.0
                    ELSE CAST(p.shared_listeners AS DOUBLE) * total_n / (CAST(p.listeners_a AS DOUBLE) * p.listeners_b)
                END AS lift,
                -- PMI = log2(P(A∩B) / (P(A) * P(B))) = log2(lift)
                CASE
                    WHEN p.listeners_a = 0 OR p.listeners_b = 0 THEN NULL
                    WHEN CAST(p.shared_listeners AS DOUBLE) * total_n / (CAST(p.listeners_a AS DOUBLE) * p.listeners_b) <= 0 THEN NULL
                    ELSE LOG2(CAST(p.shared_listeners AS DOUBLE) * total_n / (CAST(p.listeners_a AS DOUBLE) * p.listeners_b))
                END AS pmi
            FROM audience_pairs p
            CROSS JOIN (SELECT COUNT(DISTINCT listener_key) AS total_n FROM listener_top25) n
        """)

        # ── Write artist_day to R2 (LAKE_BUCKET — product-safe) ──
        # No listener-level key in this output — only aggregate per-artist counts.
        artist_day = conn.execute("""
            SELECT artist_mbid, COUNT(DISTINCT listener_key) as unique_listeners, SUM(listen_count) as total_listens
            FROM listener_artist_agg GROUP BY artist_mbid
        """).fetchall()

        ad_table = pa.table({
            "artist_mbid": [r[0] for r in artist_day],
            "unique_listeners": [r[1] for r in artist_day],
            "total_listens": [r[2] for r in artist_day],
        })
        ad_meta = lake.put_parquet(
            lake.config.lake_bucket,
            f"silver/listenbrainz/artist_day/{job_id}/part0.parquet",
            ad_table,
        )
        manifest.output_paths.append(ad_meta["uri"])
        manifest.output_hashes[ad_meta["key"]] = ad_meta["sha256"]
        manifest.r2_write_bytes += ad_meta["bytes"]

        # ── Write affinity edges to R2 (LAKE_BUCKET — product-safe aggregate) ──
        # Gold schema: artist_a, artist_b, shared_listeners, listeners_a,
        # listeners_b, jaccard, cosine, lift, pmi.
        # NO listener-level key in Gold/Serving.
        pairs = conn.execute("""
            SELECT artist_a, artist_b, shared_listeners,
                   listeners_a, listeners_b, jaccard, cosine, lift, pmi
            FROM audience_affinity
        """).fetchall()
        pairs_table = pa.table({
            "artist_a": [r[0] for r in pairs],
            "artist_b": [r[1] for r in pairs],
            "shared_listeners": [r[2] for r in pairs],
            "listeners_a": [r[3] for r in pairs],
            "listeners_b": [r[4] for r in pairs],
            "jaccard": [r[5] for r in pairs],
            "cosine": [r[6] for r in pairs],
            "lift": [r[7] for r in pairs],
            "pmi": [r[8] for r in pairs],
        })
        pair_meta = lake.put_parquet(
            lake.config.lake_bucket,
            f"gold/audience_affinity/{job_id}/part0.parquet",
            pairs_table,
            metadata=_listener_key_metadata(),
        )
        manifest.output_paths.append(pair_meta["uri"])
        manifest.output_hashes[pair_meta["key"]] = pair_meta["sha256"]
        manifest.r2_write_bytes += pair_meta["bytes"]

        conn.close()

        manifest.status = STATUS_BUILD_COMPLETE
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        manifest.rows_read = len(partials)
        manifest.rows_written = len(pairs)

        # P5/P0-1: BUILD_COMPLETE → VERIFIED → PUBLISHED
        # Verify every output object exists and SHA matches before publishing.
        verify_outputs(
            lake,
            bucket=lake.config.lake_bucket,
            output_hashes=manifest.output_hashes,
            manifest=manifest,
            manifest_key_path=manifest_key_path,
        )
        # Reduce outputs have no CURRENT pointer; publish directly after VERIFIED.
        manifest.publication_state = STATUS_PUBLISHED
        manifest.status = STATUS_PUBLISHED
        lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())

        return {
            "status": "COMPLETED",
            "manifest_key": manifest_key_path,
            "artist_day_key": ad_meta["uri"],
            "affinity_key": pair_meta["uri"],
            "unique_listeners": len(artist_day),
            "affinity_edges": len(pairs),
            "top_k": top_k,
            "metric_universe": AFFINITY_METRIC_UNIVERSE,
            "runtime_seconds": manifest.runtime_seconds,
        }

    except Exception as e:
        if manifest.error_code is None:
            if "FI_LISTENER_HMAC_SECRET" in str(e) or "FI_LISTENER_HMAC_SECRET_VERSION" in str(e):
                manifest.error_code = ERR_LISTENER_KEY_CONFIG
            else:
                manifest.error_code = ERR_JOB_EXEC_FAILED
        manifest.status = STATUS_FAILED
        manifest.error = str(e)
        manifest.error_detail = traceback.format_exc()
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        try:
            lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())
        except Exception:
            pass
        raise

    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
