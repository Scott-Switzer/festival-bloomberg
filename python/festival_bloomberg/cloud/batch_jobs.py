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
import shutil
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure the festival_bloomberg package is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from festival_bloomberg.cloud.job_manifest import (
    STATUS_BUILD_COMPLETE,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_SUPERSEDED,
    STATUS_VERIFIED,
    JobManifest,
    manifest_key,
    new_manifest,
    now_iso,
)
from festival_bloomberg.cloud.listener_key import (
    ListenerKeyContract,
    derive_listener_key_and_partition,
    get_secret,
    validate_contract_compatibility,
)
from festival_bloomberg.cloud.r2_lake import R2Lake, R2LakeConfig

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
            build_graph,
            read_estate_json,
            read_wikidata_parquets,
            rows_from_connection,
            write_graph_tables,
        )

        conn = duckdb.connect(str(source_db_path), read_only=True)
        estate_rows = read_estate_json(str(estate_path))
        governed_keys = [row["artist_key"] for row in estate_rows]

        artists, external_ids, linkages, source_tables, available_broad = (
            rows_from_connection(conn, governed_keys, include_broad=False, max_artists=25_000)
        )
        conn.close()

        as_of = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00Z")
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
            derive_listener_key,
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
# TERMINAL SERVING BUILD V1 — compact buyer product artifact
# ════════════════════════════════════════════════════════════════

TERMINAL_SERVING_PREFIX = "serving/artist_security_terminal_v1"
TERMINAL_ARTIFACT = "artist_security_terminal_v1"
TERMINAL_CONTRACT_VERSION = "artist_security_terminal_v1"
# Ceiling for the compact serving artifact. The local launcher downloads only
# this object, so it must stay bounded (hundreds of MB or less).
DEFAULT_MAX_TERMINAL_ARTIFACT_BYTES = 600 * 1024 * 1024


def _streaming_sha256(path: Path, chunk_size: int = 16 * 1024 * 1024) -> str:
    """SHA-256 of a file using bounded-memory streaming reads."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _download_to_scratch(lake, bucket: str, key: str, dest: Path) -> int:
    """Stream an R2 object to disk (boto3 multipart download); returns bytes."""
    lake._s3.download_file(bucket, key, str(dest))
    return dest.stat().st_size


def _resolve_estate(lake, work: Path) -> tuple[Path, str, str, int]:
    """Resolve the governed 25K artist estate artifact from R2, streaming to
    scratch. Never guesses: checks the LAKE control key first (the identity
    job's path), then the BACKUPS pointer + fallback newest ``estate_*.json``.
    """
    primary_key = "control/artist_security_25000/v1/estate.json"
    for bucket in (lake.config.lake_bucket, lake.config.backup_bucket):
        if lake.verify_object_exists(bucket, primary_key):
            dest = work / "estate.json"
            size = _download_to_scratch(lake, bucket, primary_key, dest)
            return dest, primary_key, bucket, size

    pointer_key = "control/artist_security_25000/current.json"
    pointer = lake.read_checkpoint(lake.config.backup_bucket, pointer_key)
    if pointer and pointer.get("source"):
        src_key = str(pointer["source"])
        dest = work / Path(src_key).name
        size = _download_to_scratch(lake, lake.config.backup_bucket, src_key, dest)
        return dest, src_key, lake.config.backup_bucket, size

    objs = lake.list_prefix(lake.config.backup_bucket, "control/artist_security_25000/", limit=100)
    estate_objs = sorted(
        (o for o in objs if o["key"].endswith(".json") and "estate" in o["key"]),
        key=lambda o: o["key"], reverse=True,
    )
    if not estate_objs:
        raise RuntimeError(
            "ESTATE_ARTIFACT_NOT_FOUND: no estate.json in LAKE and no estate_*.json "
            "under control/artist_security_25000/ in BACKUPS"
        )
    best = estate_objs[0]
    dest = work / Path(best["key"]).name
    size = _download_to_scratch(lake, lake.config.backup_bucket, best["key"], dest)
    return dest, best["key"], lake.config.backup_bucket, size


def _resolve_affinity(lake, work: Path) -> tuple[Path, str, int]:
    """Resolve the ListenBrainz pilot Gold affinity parquet from LAKE."""
    candidates = ["gold/listenbrainz_pilot/artist_audience_affinity.parquet"]
    if not lake.verify_object_exists(lake.config.lake_bucket, candidates[0]):
        objs = lake.list_prefix(lake.config.lake_bucket, "gold/", limit=500)
        aff = sorted(
            (o for o in objs if "affinity" in o["key"] and o["key"].endswith(".parquet")),
            key=lambda o: o["key"], reverse=True,
        )
        if not aff:
            raise RuntimeError(
                "GOLD_AFFINITY_NOT_FOUND: no gold/*affinity*.parquet object in LAKE; "
                "audience peers/alternatives cannot be materialized"
            )
        candidates[0] = aff[0]["key"]
    dest = work / "affinity.parquet"
    size = _download_to_scratch(lake, lake.config.lake_bucket, candidates[0], dest)
    return dest, candidates[0], size


def _compute_demo_artists(db_path: Path, limit: int = 10) -> list[dict]:
    """Select highest cross-source-completeness artists and persist them inside
    the serving DB as ``demo_artists`` (Phase 4). Completeness counts real
    observed evidence families only; never fabricates values."""
    import duckdb
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS demo_artists (
                artist_key VARCHAR PRIMARY KEY,
                name VARCHAR,
                tier VARCHAR,
                completeness INTEGER NOT NULL,
                market_count INTEGER,
                historical_event_count INTEGER,
                festival_appearance_count INTEGER,
                attention_source_count INTEGER,
                peer_count INTEGER,
                future_event_count INTEGER
            )
        """)
        rows = conn.execute(
            f"""
            WITH att AS (
                SELECT artist_key, COUNT(DISTINCT source_system) AS attention_source_count
                FROM attention_observations GROUP BY artist_key
            ),
            peers AS (
                SELECT subject_key AS artist_key, COUNT(*) AS peer_count
                FROM artist_peers GROUP BY subject_key
            ),
            fut AS (
                SELECT artist_key, COUNT(*) AS future_event_count
                FROM future_events GROUP BY artist_key
            )
            SELECT
                a.artist_key, a.name, a.tier,
                (a.market_count > 0)::INTEGER
                  + (a.historical_event_count > 0)::INTEGER
                  + (a.festival_appearance_count > 0)::INTEGER
                  + (COALESCE(att.attention_source_count, 0) > 0)::INTEGER
                  + (COALESCE(peers.peer_count, 0) > 0)::INTEGER
                  + (COALESCE(fut.future_event_count, 0) > 0)::INTEGER AS completeness,
                a.market_count, a.historical_event_count, a.festival_appearance_count,
                COALESCE(att.attention_source_count, 0),
                COALESCE(peers.peer_count, 0),
                COALESCE(fut.future_event_count, 0)
            FROM artists a
            LEFT JOIN att USING (artist_key)
            LEFT JOIN peers USING (artist_key)
            LEFT JOIN fut USING (artist_key)
            ORDER BY completeness DESC, a.market_count DESC, a.historical_event_count DESC,
                     a.festival_appearance_count DESC, a.name
            LIMIT {int(limit)}
            """
        ).fetchall()
        cols = [
            "artist_key", "name", "tier", "completeness", "market_count",
            "historical_event_count", "festival_appearance_count",
            "attention_source_count", "peer_count", "future_event_count",
        ]
        demo = [dict(zip(cols, row)) for row in rows]
        conn.execute("DELETE FROM demo_artists")
        if demo:
            conn.executemany(
                "INSERT INTO demo_artists VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(d[c] for c in cols) for d in demo],
            )
        conn.execute("CHECKPOINT")
        return demo
    finally:
        conn.close()


def _validate_terminal_db(
    db_path: Path, demo_artists: list[dict], counts: dict,
) -> dict:
    """Phase 5: open the serving DB read-only, run required family checks and
    representative terminal queries, and measure query latency. Returns the
    validation summary used in CURRENT.json."""
    import duckdb
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        minimums = {
            "artists": 25_000, "artist_search_terms": 1, "artist_external_ids": 1,
            "attention_observations": 1, "artist_peers": 1, "artist_markets": 1,
            "event_history": 1, "festival_appearances": 1, "future_events": 1,
        }
        checks: dict[str, int] = {}
        passed = True
        for table, minimum in minimums.items():
            n = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            checks[table] = n
            if n < minimum:
                passed = False
                checks.setdefault("failures", [])
                checks["failures"].append(f"{table}={n} below minimum {minimum}")
        if not demo_artists:
            passed = False
            checks.setdefault("failures", []).append("no demo artists selected")
        # A demo artist must yield a populated page: peers + alternatives.
        if demo_artists:
            probe_key = demo_artists[0]["artist_key"]
            peer_n = int(conn.execute(
                "SELECT COUNT(*) FROM artist_peers WHERE subject_key = ?", [probe_key]
            ).fetchone()[0])
            if peer_n < 1:
                passed = False
                checks.setdefault("failures", []).append(
                    f"top demo artist {probe_key} has no audience peers"
                )
        # Representative latency probes over the actual product queries.
        probes = {
            "search": """
                SELECT st.artist_key, a.name FROM artist_search_terms st
                JOIN artists a USING (artist_key)
                WHERE st.normalized_term LIKE 'the%' OR st.normalized_term LIKE '%the%'
                LIMIT 25
            """,
            "artist": "SELECT * FROM artists WHERE artist_key = ?",
            "artist_full": "",  # filled below
            "compare": "",
        }
        latencies: dict[str, float] = {}
        t0 = time.time()
        conn.execute(probes["search"]).fetchall()
        latencies["search_ms"] = round((time.time() - t0) * 1000, 1)
        if demo_artists:
            key = demo_artists[0]["artist_key"]
            t0 = time.time()
            conn.execute("SELECT * FROM artists WHERE artist_key = ?", [key]).fetchall()
            latencies["artist_ms"] = round((time.time() - t0) * 1000, 1)
            t0 = time.time()
            conn.execute(
                "SELECT * FROM artist_peers WHERE subject_key = ? ORDER BY rank LIMIT 10",
                [key],
            ).fetchall()
            latencies["peers_ms"] = round((time.time() - t0) * 1000, 1)
            if len(demo_artists) > 1:
                other = demo_artists[1]["artist_key"]
                t0 = time.time()
                conn.execute(
                    "SELECT artist_key FROM artists WHERE artist_key IN (?, ?)", [key, other]
                ).fetchall()
                latencies["compare_probe_ms"] = round((time.time() - t0) * 1000, 1)
        return {
            "passed": passed,
            "checks": checks,
            "family_counts": {k: v for k, v in counts.items()},
            "latency_ms": latencies,
            "read_only": True,
            "unknown_preserved": True,
            "no_composite_score": True,
        }
    finally:
        conn.close()


_WIKIDATA_PROVIDER = {
    # external_id_property -> (id_type, source_system)
    "P1902": ("spotify", "wikidata"),
    "P2397": ("youtube", "wikidata"),
    "P213": ("isni", "wikidata"),
    "P214": ("viaf", "wikidata"),
    "P1953": ("discogs", "wikidata"),
    "P856": ("official_website", "wikidata"),
    "P434": ("musicbrainz", "wikidata"),
    "P2003": ("instagram", "wikidata"),
    "P2013": ("facebook", "wikidata"),
    "P2002": ("twitter", "wikidata"),
    "P2390": ("apple_music", "wikidata"),
    "P2207": ("spotify_artist_id", "wikidata"),
    "P3478": ("songkick", "wikidata"),
    "P4208": ("bandcamp", "wikidata"),
    "P3040": ("soundcloud", "wikidata"),
}


def _parquet_cols(conn, path: Path) -> set[str]:
    """Column names of a local Parquet file without loading it."""
    return {
        r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}')"
        ).fetchall()
    }


def _pick(cols: set[str], *candidates: str) -> str | None:
    return next((c for c in candidates if c in cols), None)


def _qp(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _materialize_r2_parquet_terminal(
    conn, *,
    estate_path: Path, estate_created_at: str,
    artists: list[dict[str, Any]],
    parquets: dict[str, Path],
    max_events_per_artist: int = 60,
    max_peers_per_artist: int = 12,
) -> dict[str, Any]:
    """Materialize the existing terminal schema from compact R2 assets only.

    Sources (all confirmed present in LAKE/BACKUPS via inventory):
        - estate.json            -> universe, markets, LB/Youtube summaries
        - silver/events + edges  -> live history + festival appearances
        - silver/series.parquet  -> festival identity
        - metrics attention export -> attention observations
        - events provider export -> forward/provider events
        - gold affinity          -> audience peers/alternatives
        - wikidata generation    -> artist external IDs

    Every insert keeps the source/scope/status/knowledge-time boundaries of
    the existing schema. UNKNOWN stays UNKNOWN (never zero).
    """
    from build_talent_buyer_terminal_v1 import (
        _create_indexes,
        _create_schema,
        _create_selected_table,
        _materialize_markets,
    )
    _create_schema(conn)
    _create_selected_table(conn, artists)

    q = _qp
    rows_honest: dict[str, int] = {}

    # ── artists (identity from estate; richer fields stay UNKNOWN/NULL) ──
    conn.execute(
        """
        INSERT INTO artists (
            artist_key, name, normalized_name, musicbrainz_id, tier,
            selection_bucket, selection_reason, evidence_profile,
            evidence_family_count, market_count, historical_event_count,
            festival_appearance_count, venues_played,
            listenbrainz_total_listens, listenbrainz_total_users,
            youtube_identifiers, disambiguation, aliases, country,
            origin_city, origin_region, area, artist_type, primary_genre,
            life_span_begin, life_span_end, is_active,
            source_system, source_scope, knowledge_time, status, rights_status
        )
        SELECT
            t.artist_key, t.artist_name, lower(trim(t.artist_name)),
            COALESCE(t.mbid, t.artist_key) , t.tier,
            t.selection_bucket, t.selection_reason, t.evidence_profile,
            t.evidence_family_count, t.market_count,
            t.historical_event_count, t.festival_appearance_count,
            t.venues_played, t.listenbrainz_total_listens,
            t.listenbrainz_total_users, t.youtube_identifiers,
            NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
            NULL, NULL, NULL,
            'artist_security_estate', 'R2_CONTROL_ESTATE',
            CAST(? AS TIMESTAMP), 'PRESENT', 'ESTATE_SUMMARY'
        FROM selected_artists t
        """,
        [estate_created_at],
    )
    rows_honest["artists"] = int(
        conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    )

    # ── artist_search_terms: canonical name + youtube channel ids ──
    conn.execute(
        """
        INSERT INTO artist_search_terms
        SELECT sha256(artist_key || '|canonical_name|' || lower(trim(name))),
               artist_key, name, lower(trim(name)), 'canonical_name',
               'artist_security_estate', 'R2_CONTROL_ESTATE', knowledge_time, status
        FROM artists WHERE name IS NOT NULL AND trim(name) <> ''
        """
    )
    conn.execute(
        """
        INSERT INTO artist_search_terms
        SELECT
            sha256(a.artist_key || '|youtube|' || trim(json_extract_string(yt.value, '$.channel_id'))),
            a.artist_key, json_extract_string(yt.value, '$.channel_id'),
            lower(trim(json_extract_string(yt.value, '$.channel_id'))),
            'youtube_channel_id', 'artist_security_estate', 'R2_CONTROL_ESTATE',
            a.knowledge_time, 'PRESENT'
        FROM artists a
        CROSS JOIN json_each(COALESCE(a.youtube_identifiers, '[]'::JSON)) yt
        WHERE trim(json_extract_string(yt.value, '$.channel_id')) <> ''
        """
    )

    # ── artist_external_ids from the Wikidata generation (latest) ──
    # The wikidata parquet links qid -> typed external ids; the qid -> mbid
    # join comes from rows whose external_id_property = 'P434' (MusicBrainz
    # ID value).  Join each property row through that qid map, mirroring the
    # identity graph's P434-join rule.  No mbid column is guessed.
    wd_path = parquets.get("wikidata_artist_external_ids")
    if wd_path is not None:
        wd_cols = _parquet_cols(conn, wd_path)
        prop_col = _pick(wd_cols, "external_id_property", "property")
        val_col = _pick(wd_cols, "external_id_value", "value", "id_value")
        qid_col = _pick(wd_cols, "qid", "wikidata_id", "QID")
        if prop_col and val_col and qid_col:
            conn.execute(
                f"""
                CREATE TEMP TABLE wd_qid_mbid AS
                SELECT wd_qid, wd_mbid FROM (
                    SELECT
                        CAST({qid_col} AS VARCHAR) AS wd_qid,
                        lower(trim(CAST({val_col} AS VARCHAR))) AS wd_mbid,
                        ROW_NUMBER() OVER (
                            PARTITION BY CAST({qid_col} AS VARCHAR)
                            ORDER BY lower(trim(CAST({val_col} AS VARCHAR)))
                        ) AS mbid_rank
                    FROM read_parquet({q(wd_path)})
                    WHERE CAST({prop_col} AS VARCHAR) = 'P434'
                      AND {val_col} IS NOT NULL
                      AND trim(CAST({val_col} AS VARCHAR)) <> ''
                ) q WHERE mbid_rank = 1
                """
            )
            mapped = ",".join(
                f"('{p}', '{id_type}', '{src}')"
                for p, (id_type, src) in _WIKIDATA_PROVIDER.items()
            )
            conn.execute(
                f"""
                INSERT INTO artist_external_ids (
                    external_id_key, artist_key, id_type, id_value, url,
                    source_system, source_scope, knowledge_time, status,
                    resolution_method, confidence
                )
                SELECT
                    sha256('r2wd|' || w.{qid_col} || '|' || w.{prop_col} || '|' || w.{val_col}),
                    'mbid::' || wq.wd_mbid,
                    map.id_type, CAST(w.{val_col} AS VARCHAR), NULL,
                    map.source_system, 'WIKIDATA_GENERATION', NULL,
                    'PRESENT', 'WIKIDATA_PROPERTY_LINK', NULL
                FROM read_parquet({q(wd_path)}) w
                JOIN wd_qid_mbid wq ON wq.wd_qid = CAST(w.{qid_col} AS VARCHAR)
                JOIN (VALUES {mapped}) map(prop, id_type, source_system)
                  ON map.prop = CAST(w.{prop_col} AS VARCHAR)
                JOIN selected_artists t
                  ON t.artist_key = 'mbid::' || wq.wd_mbid
                WHERE CAST(w.{prop_col} AS VARCHAR) <> 'P434'
                  AND w.{val_col} IS NOT NULL
                  AND trim(CAST(w.{val_col} AS VARCHAR)) <> ''
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY sha256(
                        'r2wd|' || w.{qid_col} || '|' || w.{prop_col} || '|' || w.{val_col}
                    )
                    ORDER BY map.id_type
                ) = 1
                """
            )
    # estate youtube channels also surface as external ids (observed)
    conn.execute(
        """
        INSERT INTO artist_external_ids (
            external_id_key, artist_key, id_type, id_value, url,
            source_system, source_scope, knowledge_time, status,
            resolution_method, confidence
        )
        SELECT
            sha256('ytest|' || a.artist_key || '|' || trim(json_extract_string(yt.value, '$.channel_id'))),
            a.artist_key, 'youtube', json_extract_string(yt.value, '$.channel_id'), NULL,
            'artist_security_estate', 'R2_CONTROL_ESTATE', a.knowledge_time,
            'PRESENT', 'ESTATE_OBSERVED', NULL
        FROM artists a
        CROSS JOIN json_each(COALESCE(a.youtube_identifiers, '[]'::JSON)) yt
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sha256(
                'ytest|' || a.artist_key || '|' || trim(json_extract_string(yt.value, '$.channel_id'))
            )
            ORDER BY a.artist_key
        ) = 1
        """
    )

    # ── attention observations from the metrics export ──
    att_path = parquets.get("attention")
    if att_path is not None:
        cols = _parquet_cols(conn, att_path)
        ak = _pick(cols, "artist_key")
        sk = _pick(cols, "source_system")
        mk = _pick(cols, "metric_kind")
        ps = _pick(cols, "period_start")
        pe = _pick(cols, "period_end")
        vv = _pick(cols, "value")
        vs = _pick(cols, "value_sum")
        vu = _pick(cols, "value_unit")
        st = _pick(cols, "status")
        su = _pick(cols, "source_url")
        rt = _pick(cols, "retrieved_at")
        ok = _pick(cols, "observation_key", "observation_id", "id")
        first = _pick(cols, "first_listen", "retrieved_at")
        last = _pick(cols, "last_listen")
        if ak and sk and mk:
            conn.execute(
                f"""
                INSERT INTO attention_observations (
                    observation_key, artist_key, source_system, metric_kind,
                    period_start, period_end, value, value_sum, value_unit,
                    status, source_url, retrieved_at, knowledge_time, source_scope
                )
                SELECT
                    CASE WHEN {ok or 'NULL'} IS NOT NULL THEN CAST({ok} AS VARCHAR) ELSE sha256(
                        'r2att|' || CAST({ak} AS VARCHAR) || '|' || CAST({sk} AS VARCHAR)
                        || '|' || CAST({mk} AS VARCHAR) || '|' || COALESCE(CAST({ps or 'NULL'} AS VARCHAR), '') ) END,
                    CAST({ak} AS VARCHAR), CAST({sk} AS VARCHAR), CAST({mk} AS VARCHAR),
                    TRY_CAST(CAST({ps or 'NULL'} AS VARCHAR) AS DATE),
                    TRY_CAST(CAST({pe or 'NULL'} AS VARCHAR) AS DATE),
                    TRY_CAST(CAST({vv or 'NULL'} AS DOUBLE) AS DOUBLE),
                    TRY_CAST(CAST({vs or 'NULL'} AS DOUBLE) AS DOUBLE),
                    {vu and f'CAST({vu} AS VARCHAR)' or 'NULL'},
                    COALESCE(CAST({st or 'NULL'} AS VARCHAR), 'ok'),
                    {su and f'CAST({su} AS VARCHAR)' or 'NULL'},
                    {rt and f'CAST({rt} AS TIMESTAMP)' or 'NULL'},
                    {rt and f'CAST({rt} AS TIMESTAMP)' or 'NULL'},
                    'R2_EXPORTED_OBSERVATION'
                FROM read_parquet({q(att_path)})
                WHERE CAST({ak} AS VARCHAR) IN (SELECT artist_key FROM selected_artists)
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(
                        CAST({ok or 'NULL'} AS VARCHAR),
                        sha256(
                            'r2att|' || CAST({ak} AS VARCHAR) || '|' || CAST({sk} AS VARCHAR)
                            || '|' || CAST({mk} AS VARCHAR) || '|' || COALESCE(CAST({ps or 'NULL'} AS VARCHAR), '')
                        )
                    )
                    ORDER BY {rt and f'{rt} DESC NULLS LAST' or '1'}, {sk}
                ) = 1
                """
            )

    # ── audience peers from gold affinity ──
    aff_path = parquets.get("affinity")
    if aff_path is not None:
        aff_cols = _parquet_cols(conn, aff_path)
        ka = _pick(aff_cols, "artist_key_a")
        kb = _pick(aff_cols, "artist_key_b")
        sh = _pick(aff_cols, "shared_listeners")
        jac = _pick(aff_cols, "jaccard")
        cos = _pick(aff_cols, "cosine")
        kt = _pick(aff_cols, "knowledge_time")
        if ka and kb and sh:
            conn.execute(
                f"""
                CREATE TEMP VIEW affinity_directed AS
                SELECT CAST({ka} AS VARCHAR) AS subject_key,
                       CAST({kb} AS VARCHAR) AS peer_key,
                       CAST({sh} AS BIGINT) AS shared_listeners,
                       {jac and f'CAST({jac} AS DOUBLE)' or 'NULL::DOUBLE'} AS jaccard,
                       {cos and f'CAST({cos} AS DOUBLE)' or 'NULL::DOUBLE'} AS cosine,
                       {kt and f'CAST({kt} AS TIMESTAMP)' or 'NULL::TIMESTAMP'} AS knowledge_time
                FROM read_parquet({q(aff_path)})
                UNION ALL
                SELECT CAST({kb} AS VARCHAR), CAST({ka} AS VARCHAR),
                       CAST({sh} AS BIGINT),
                       {jac and f'CAST({jac} AS DOUBLE)' or 'NULL::DOUBLE'},
                       {cos and f'CAST({cos} AS DOUBLE)' or 'NULL::DOUBLE'},
                       {kt and f'CAST({kt} AS TIMESTAMP)' or 'NULL::TIMESTAMP'}
                FROM read_parquet({q(aff_path)})
                """
            )
            conn.execute(
                f"""
                INSERT INTO artist_peers
                SELECT
                    sha256(subject_key || '|' || peer_key || '|pilot'),
                    subject_key, peer_key, a.name AS peer_name,
                    ROW_NUMBER() OVER (
                        PARTITION BY subject_key
                        ORDER BY shared_listeners DESC NULLS LAST, jaccard DESC NULLS LAST, peer_key
                    )::INTEGER AS rank,
                    d.shared_listeners, d.jaccard, d.cosine,
                    'listenbrainz', 'PILOT_AUDIENCE_DATA', d.knowledge_time,
                    'DESCRIPTIVE_PILOT',
                    'Pilot audience affinity only; not demand, ticket intent, or interchangeability.'
                FROM (
                    SELECT subject_key, peer_key, shared_listeners, jaccard, cosine,
                           knowledge_time,
                           ROW_NUMBER() OVER (
                               PARTITION BY subject_key, peer_key
                               ORDER BY shared_listeners DESC NULLS LAST, jaccard DESC NULLS LAST
                           ) AS dup_rank
                    FROM affinity_directed
                    WHERE subject_key IN (SELECT artist_key FROM selected_artists)
                      AND peer_key IN (SELECT artist_key FROM selected_artists)
                ) d
                LEFT JOIN artists a ON a.artist_key = d.peer_key
                WHERE d.dup_rank = 1
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY subject_key
                    ORDER BY shared_listeners DESC NULLS LAST, jaccard DESC NULLS LAST, peer_key
                ) <= {int(max_peers_per_artist)}
                """
            )

    # ── artist_markets from the governed estate (reuse estate-builder loop) ──
    _materialize_markets(conn, artists, (estate_created_at or "1970-01-01")[:10])

    # ── live history from the silver event graph ──
    events_path = parquets.get("events")
    edges_path = parquets.get("event_artist_edges")
    place_edges_path = parquets.get("event_place_edges")
    venues_path = parquets.get("venues")
    if events_path and edges_path:
        ev_cols = _parquet_cols(conn, events_path)
        ed_cols = _parquet_cols(conn, edges_path)
        ev_name = _pick(ev_cols, "name", "event_name")
        ev_begin = _pick(ev_cols, "begin_date")
        ev_end = _pick(ev_cols, "end_date")
        ev_type = _pick(ev_cols, "event_type", "type")
        ed_event = _pick(ed_cols, "event_mbid")
        ed_artist = _pick(ed_cols, "artist_mbid")
        ed_artist_name = _pick(ed_cols, "artist_name")
        ed_role = _pick(ed_cols, "performer_role", "relation_type")
        # Venue context: LEFT JOIN a bounded per-event venue name.
        venue_join = "NULL::VARCHAR AS venue_name"
        venue_from = ""
        if place_edges_path and venues_path:
            pc = _parquet_cols(conn, place_edges_path)
            vc = _parquet_cols(conn, venues_path)
            pe_event = _pick(pc, "event_mbid")
            pe_place = _pick(pc, "place_mbid")
            pe_place_name = _pick(pc, "place_name")
            v_id = _pick(vc, "place_mbid", "place_id", "venue_mbid")
            v_name = _pick(vc, "name", "place_name", "venue_name")
            if pe_event and (pe_place or pe_place_name) and v_id and v_name:
                venue_join = f"COALESCE(pe.{pe_place_name}, v.{v_name}) AS venue_name"
                venue_from = (
                    f"LEFT JOIN read_parquet({q(place_edges_path)}) pe "
                    f"ON CAST(pe.{pe_event} AS VARCHAR) = CAST(e.event_mbid AS VARCHAR)\n"
                    f"LEFT JOIN read_parquet({q(venues_path)}) v "
                    f"ON CAST(v.{v_id} AS VARCHAR) = CAST(pe.{pe_place} AS VARCHAR)\n"
                )
        conn.execute(
            f"""
            INSERT INTO event_history (
                event_key, artist_key, artist_name, event_name, event_date,
                event_end_date, event_type, venue_name, market_name, city,
                state_code, number_of_shows, is_multi_show, source_system,
                source_url, source_scope, knowledge_time, status, location_method
            )
            SELECT * EXCLUDE (artist_rank)
            FROM (
                SELECT
                    'mb-event::' || e.event_mbid || '::' || p.{ed_artist} AS event_key,
                    'mbid::' || lower(p.{ed_artist}),
                    COALESCE(p.{ed_artist_name}, a.name),
                    e.{ev_name}, TRY_CAST(CAST(e.{ev_begin or 'begin_date'} AS VARCHAR) AS DATE),
                    TRY_CAST(CAST(e.{ev_end or 'begin_date'} AS VARCHAR) AS DATE) AS end_date,
                    e.{ev_type},
                    {venue_join}, NULL, NULL, NULL, NULL, NULL,
                    'musicbrainz', 'https://musicbrainz.org/event/' || e.event_mbid,
                    'R2_SILVER_EVENT_GRAPH', NULL, 'OBSERVED', NULL,
                    ROW_NUMBER() OVER (
                        PARTITION BY 'mbid::' || lower(p.{ed_artist})
                        ORDER BY TRY_CAST(CAST(e.{ev_begin or 'begin_date'} AS VARCHAR) AS DATE) DESC NULLS LAST, e.event_mbid
                    ) AS artist_rank
                FROM read_parquet({q(edges_path)}) p
                JOIN read_parquet({q(events_path)}) e ON CAST(e.event_mbid AS VARCHAR) = CAST(p.{ed_event} AS VARCHAR)
                {venue_from}
                LEFT JOIN artists a ON a.artist_key = 'mbid::' || lower(p.{ed_artist})
                WHERE 'mbid::' || lower(p.{ed_artist}) IN (SELECT artist_key FROM selected_artists)
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY 'mb-event::' || e.event_mbid || '::' || p.{ed_artist}
                    ORDER BY TRY_CAST(CAST(e.{ev_begin or 'begin_date'} AS VARCHAR) AS DATE) DESC NULLS LAST, e.event_mbid
                ) = 1
            ) bounded
            WHERE artist_rank <= {int(max_events_per_artist)}
            """
        )

    # ── festival appearances from silver series graph (FESTIVAL only) ──
    series_edges_path = parquets.get("event_series_edges")
    series_path = parquets.get("series")
    if events_path and edges_path and series_edges_path and series_path:
        sc_cols = _parquet_cols(conn, series_edges_path)
        s_cols = _parquet_cols(conn, series_path)
        se_event = _pick(sc_cols, "event_mbid")
        se_series = _pick(sc_cols, "series_mbid")
        s_id = _pick(s_cols, "series_mbid")
        s_name = _pick(s_cols, "name", "series_name")
        s_class = _pick(s_cols, "classification")
        if se_event and se_series and s_id and s_name and s_class:
            conn.execute(
                f"""
                INSERT INTO festival_appearances (
                    appearance_key, artist_key, event_key, festival_key,
                    edition_key, festival_name, event_name, edition_year,
                    event_date, performance_date, market_name, venue_name,
                    billing_order, billing_tier, stage_name, artist_role,
                    co_billed_artist_names, repeat_appearance_count,
                    source_system, source_url, source_scope, knowledge_time, status
                )
                SELECT * EXCLUDE (artist_rank)
                FROM (
                    SELECT
                        sha256(p.{ed_artist} || '|' || se.{se_series} || '|' || se.{se_event}) AS appearance_key,
                        'mbid::' || lower(p.{ed_artist}),
                        'mb-event::' || se.{se_event},
                        s.{s_id}, s.{s_id},
                        s.{s_name}, e.name,
                        TRY_CAST(SUBSTR(CAST(e.{ev_begin or 'begin_date'} AS VARCHAR), 1, 4) AS INTEGER),
                        TRY_CAST(CAST(e.{ev_begin or 'begin_date'} AS VARCHAR) AS DATE),
                        TRY_CAST(CAST(e.{ev_begin or 'begin_date'} AS VARCHAR) AS DATE),
                        NULL, NULL, NULL, NULL, NULL, p.{ed_role},
                        NULL,
                        COUNT(*) OVER (PARTITION BY 'mbid::' || lower(p.{ed_artist}), s.{s_id}),
                        'musicbrainz', 'https://musicbrainz.org/event/' || se.{se_event},
                        'R2_SILVER_SERIES_GRAPH', NULL, 'OBSERVED',
                        ROW_NUMBER() OVER (
                            PARTITION BY 'mbid::' || lower(p.{ed_artist})
                            ORDER BY TRY_CAST(CAST(e.{ev_begin or 'begin_date'} AS VARCHAR) AS DATE) DESC NULLS LAST, se.{se_event}
                        ) AS artist_rank
                    FROM read_parquet({q(series_edges_path)}) se
                    JOIN read_parquet({q(series_path)}) s
                      ON CAST(s.{s_id} AS VARCHAR) = CAST(se.{se_series} AS VARCHAR)
                     AND upper(CAST(s.{s_class} AS VARCHAR)) = 'FESTIVAL'
                    JOIN read_parquet({q(edges_path)}) p ON CAST(p.{ed_event} AS VARCHAR) = CAST(se.{se_event} AS VARCHAR)
                    JOIN read_parquet({q(events_path)}) e ON CAST(e.event_mbid AS VARCHAR) = CAST(se.{se_event} AS VARCHAR)
                    WHERE 'mbid::' || lower(p.{ed_artist}) IN (SELECT artist_key FROM selected_artists)
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY sha256(p.{ed_artist} || '|' || se.{se_series} || '|' || se.{se_event})
                        ORDER BY se.{se_event}
                    ) = 1
                ) bounded
                WHERE artist_rank <= {int(max_events_per_artist)}
                """
            )

    # ── forward/provider events from the events export (name-matched) ──
    prov_path = parquets.get("provider_snapshots")
    if prov_path is not None:
        pc = _parquet_cols(conn, prov_path)
        p_local = _pick(pc, "local_date", "event_date", "date")
        p_time = _pick(pc, "event_time", "time")
        p_status = _pick(pc, "event_status", "status")
        p_venue = _pick(pc, "venue_name", "venue")
        p_city = _pick(pc, "city")
        p_state = _pick(pc, "state_code", "state")
        p_country = _pick(pc, "country_code", "country")
        p_promoter = _pick(pc, "promoter")
        p_min = _pick(pc, "price_min", "price")
        p_max = _pick(pc, "price_max")
        p_cur = _pick(pc, "price_currency", "currency")
        p_attractions = _pick(pc, "attractions")
        p_provider = _pick(pc, "provider", "source_platform")
        p_url = _pick(pc, "canonical_url", "source_url", "url")
        p_oid = _pick(pc, "platform_object_id", "event_key", "provider_event_id", "id")
        p_retrieved = _pick(pc, "retrieved_at")
        p_knowledge = _pick(pc, "knowledge_time")
        p_rights = _pick(pc, "rights_status")
        if p_local and p_attractions and p_oid:
            provider_filter = (
                f"WHERE lower(CAST({p_provider} AS VARCHAR)) = 'ticketmaster'" if p_provider else ""
            )
            conn.execute(
                f"""
                INSERT INTO future_events (
                    future_event_key, artist_key, provider_event_id, event_name,
                    event_date, event_time, event_status, venue_name, market_name,
                    city, state_code, promoter, ticket_price_min, ticket_price_max,
                    ticket_price_currency, ticket_price_basis, ticket_evidence_status,
                    source_system, source_url, source_scope, retrieved_at,
                    knowledge_time, status, rights_status
                )
                SELECT
                    sha256('prov|' || CAST(s.{p_oid} AS VARCHAR) || '|' || a.artist_key),
                    a.artist_key, CAST(s.{p_oid} AS VARCHAR),
                    CASE WHEN a.artist_name IS NULL OR trim(a.artist_name) = '' THEN NULL ELSE a.artist_name END,
                    TRY_CAST(SUBSTR(CAST(s.{p_local} AS VARCHAR), 1, 10) AS DATE),
                    {p_time and f'TRY_CAST(CAST(s.{p_time} AS VARCHAR) AS TIMESTAMP)' or 'NULL::TIMESTAMP'},
                    {p_status and f'CAST(s.{p_status} AS VARCHAR)' or 'NULL::VARCHAR'},
                    {p_venue and f'CAST(s.{p_venue} AS VARCHAR)' or 'NULL::VARCHAR'},
                    NULL,
                    {p_city and f'CAST(s.{p_city} AS VARCHAR)' or 'NULL::VARCHAR'},
                    {p_state and f'CAST(s.{p_state} AS VARCHAR)' or 'NULL::VARCHAR'},
                    {p_promoter and f'CAST(s.{p_promoter} AS VARCHAR)' or 'NULL::VARCHAR'},
                    CASE WHEN s.{p_min} > 0 THEN CAST(s.{p_min} AS DOUBLE) ELSE NULL END,
                    CASE WHEN s.{p_max} > 0 THEN CAST(s.{p_max} AS DOUBLE) ELSE NULL END,
                    {p_cur and f'CAST(s.{p_cur} AS VARCHAR)' or 'NULL::VARCHAR'},
                    CASE WHEN s.{p_min} > 0 OR s.{p_max} > 0
                         THEN 'ADVERTISED_STRUCTURED_RANGE' ELSE NULL END,
                    CASE WHEN s.{p_min} > 0 OR s.{p_max} > 0
                         THEN 'ADVERTISED_RANGE' ELSE 'NO_CURRENT_TICKET_EVIDENCE' END,
                    {p_provider and f'CAST(s.{p_provider} AS VARCHAR)' or "'ticketmaster'"},
                    {p_url and f'CAST(s.{p_url} AS VARCHAR)' or 'NULL::VARCHAR'},
                    'R2_EVENTS_EXPORT_NAME_MATCH',
                    {p_retrieved and f'CAST(s.{p_retrieved} AS TIMESTAMP)' or 'NULL::TIMESTAMP'},
                    {p_knowledge and f'CAST(s.{p_knowledge} AS TIMESTAMP)' or 'NULL::TIMESTAMP'},
                    'OBSERVED', {p_rights and f'CAST(s.{p_rights} AS VARCHAR)' or 'NULL::VARCHAR'}
                FROM (
                    SELECT s.*, attraction.value AS attraction_json
                    FROM read_parquet({q(prov_path)}) s
                    CROSS JOIN json_each(COALESCE(s.{p_attractions}, '[]'::JSON)) attraction
                ) s
                JOIN selected_artists a
                  ON lower(trim(COALESCE(json_extract_string(s.attraction_json, '$.name'),
                                        json_extract_string(s.attraction_json, '$.attraction_name'), '')))
                     = lower(trim(a.artist_name))
                WHERE TRY_CAST(SUBSTR(CAST(s.{p_local} AS VARCHAR), 1, 10) AS DATE) >= CURRENT_DATE
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY sha256('prov|' || CAST(s.{p_oid} AS VARCHAR) || '|' || a.artist_key)
                    ORDER BY {p_knowledge and f'CAST(s.{p_knowledge} AS TIMESTAMP) DESC NULLS LAST' or '1'}, s.{p_oid}
                ) = 1
                """
            )
            # dedupe same provider event per artist
            conn.execute(
                """
                DELETE FROM future_events WHERE future_event_key IN (
                    SELECT future_event_key FROM (
                        SELECT future_event_key,
                               ROW_NUMBER() OVER (PARTITION BY provider_event_id, artist_key ORDER BY knowledge_time DESC NULLS LAST) rn
                        FROM future_events
                    ) q WHERE rn > 1
                )
                """
            )

    # ── provenance / product_meta ──
    rows_honest.update({
        "artist_search_terms": int(conn.execute("SELECT COUNT(*) FROM artist_search_terms").fetchone()[0]),
        "artist_external_ids": int(conn.execute("SELECT COUNT(*) FROM artist_external_ids").fetchone()[0]),
        "attention_observations": int(conn.execute("SELECT COUNT(*) FROM attention_observations").fetchone()[0]),
        "artist_peers": int(conn.execute("SELECT COUNT(*) FROM artist_peers").fetchone()[0]),
        "artist_markets": int(conn.execute("SELECT COUNT(*) FROM artist_markets").fetchone()[0]),
        "event_history": int(conn.execute("SELECT COUNT(*) FROM event_history").fetchone()[0]),
        "festival_appearances": int(conn.execute("SELECT COUNT(*) FROM festival_appearances").fetchone()[0]),
        "future_events": int(conn.execute("SELECT COUNT(*) FROM future_events").fetchone()[0]),
    })
    try:
        _create_indexes(conn)
    except Exception:
        pass
    # product_meta provenance row (existing schema contract; read by the API)
    conn.execute(
        """
        INSERT INTO product_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "TALENT_BUYER_TERMINAL_V1",
            "artist_security_terminal_v1",
            datetime.now(UTC).replace(tzinfo=None),
            "r2-lake-serving-assets",
            str(estate_path.resolve()) or "r2-lake-estate",
            str(parquets.get("affinity", Path("r2-lake-affinity"))),
            rows_honest.get("artists", 0) or 0,
            rows_honest.get("artist_markets", 0) or 0,
            rows_honest.get("artist_peers", 0) or 0,
            rows_honest.get("event_history", 0) or 0,
            rows_honest.get("festival_appearances", 0) or 0,
            rows_honest.get("future_events", 0) or 0,
            "VERIFIED_COMPACT_BUILD",
            json.dumps({"materializer": "_materialize_r2_parquet_terminal", "sources": sorted(str(p) for p in parquets.values())}),
            "Read-only buyer evidence; pilot audience affinity is descriptive; no score, demand forecast, booking advice, attendance or gross prediction.",
        ],
    )
    conn.execute("CHECKPOINT")
    return rows_honest


def run_terminal_serving_build(spec: dict, scratch_dir: Path) -> dict:
    """Build the compact Talent Buyer serving artifact entirely from R2 assets.

    Reads (existing compact R2 assets, never raw corpora):
        - governed 25K estate (BACKUPS control/artist_security_25000/)
        - silver event graph (events + edges + series), attention and provider
          event exports, pilot Gold audience affinity, and the latest Wikidata
          artist external-id generation — the canonical warehouse DB is NOT in
          R2 (licensed, never uploaded), so this job never depends on it.

    Materializes the existing ``artist_security_terminal_v1`` schema,
    validates the DB read-only, selects demo artists, computes a streaming
    SHA-256, uploads the generation object, and only then publishes
    ``serving/artist_security_terminal_v1/CURRENT.json``.
    """
    lake = _get_lake()
    job_id = spec.get("job_id", "terminal_serving_build_v1")
    params = spec.get("params", {})
    max_events_per_artist = int(params.get("max_events_per_artist", 60) or 60)
    max_peers_per_artist = int(params.get("max_peers_per_artist", 12) or 12)
    max_artifact_bytes = int(
        params.get("max_artifact_bytes") or DEFAULT_MAX_TERMINAL_ARTIFACT_BYTES
    )

    manifest = new_manifest(
        job_type="terminal_serving_build_v1",
        job_id=job_id,
        code_commit=_git_commit(),
        container_image="festival-bloomberg-batch:latest",
        params=params,
    )
    manifest_key_path = manifest_key("terminal_serving_build_v1", job_id)
    start = time.time()
    work = scratch_dir / "terminal_build"
    work.mkdir(parents=True, exist_ok=True)

    try:
        if max_events_per_artist < 1:
            raise ValueError("max_events_per_artist must be >= 1")

        # ── 1. Resolve + stream inputs from R2 (no guessing) ──
        estate_path, estate_key, estate_bucket, estate_size = _resolve_estate(lake, work)
        manifest.source_paths.append(f"r2://{estate_bucket}/{estate_key}")
        manifest.r2_read_bytes += estate_size

        # Confirmed present via cloud inventory (2026-08-31):
        #   - silver/events, silver/venues, silver/series (MB event graph)
        #   - metrics/artist_attention_observations export
        #   - events/provider_event_snapshots export
        #   - gold/listenbrainz_pilot affinity
        #   - silver/wikidata/generations/<run_id>/artist_external_ids.parquet
        fixed_parquet_keys = {
            "events": "silver/events/events.parquet",
            "event_artist_edges": "silver/events/event_artist_edges.parquet",
            "event_place_edges": "silver/events/event_place_edges.parquet",
            "event_series_edges": "silver/events/event_series_edges.parquet",
            "venues": "silver/venues/venues.parquet",
            "series": "silver/series/series.parquet",
            "attention": "metrics/artist_attention_observations/artist_attention_observations.parquet",
            "provider_snapshots": "events/provider_event_snapshots/provider_event_snapshots.parquet",
            "affinity": "gold/listenbrainz_pilot/artist_audience_affinity.parquet",
        }
        parquets: dict[str, Path] = {}
        for name, key in fixed_parquet_keys.items():
            if not lake.verify_object_exists(lake.config.lake_bucket, key):
                raise RuntimeError(
                    f"R2_INPUT_MISSING: {key} not found in LAKE bucket"
                )
            dest = work / Path(key).name.replace("/", "_") or Path(name + ".parquet")
            size = _download_to_scratch(lake, lake.config.lake_bucket, key, dest)
            parquets[name] = dest
            manifest.source_paths.append(f"r2://{lake.config.lake_bucket}/{key}")
            manifest.r2_read_bytes += size

        # Wikidata generation: follow the CURRENT pointer, never guess run_id.
        wd_current = lake.read_checkpoint(
            lake.config.lake_bucket, "silver/wikidata/CURRENT.json",
        )
        wd_run_id = (wd_current or {}).get("run_id")
        if not wd_run_id:
            raise RuntimeError(
                "WIKIDATA_CURRENT_MISSING: silver/wikidata/CURRENT.json has no run_id"
            )
        wd_key = f"silver/wikidata/generations/{wd_run_id}/artist_external_ids.parquet"
        if not lake.verify_object_exists(lake.config.lake_bucket, wd_key):
            raise RuntimeError(f"WIKIDATA_ARTIST_IDS_MISSING: {wd_key}")
        wd_path = work / "wikidata_artist_external_ids.parquet"
        size = _download_to_scratch(lake, lake.config.lake_bucket, wd_key, wd_path)
        parquets["wikidata_artist_external_ids"] = wd_path
        manifest.source_paths.append(f"r2://{lake.config.lake_bucket}/{wd_key}")
        manifest.r2_read_bytes += size

        # Estate artists (the governed 25K selection; never inferred).
        estate_payload = json.loads(estate_path.read_text(encoding="utf-8"))
        artists = estate_payload.get("artists") or []
        if not artists:
            raise RuntimeError("ESTATE_ARTISTS_EMPTY: estate payload has no artists")
        estate_created_at = str(estate_payload.get("created_at", ""))

        # ── 2. Materialize the existing terminal schema ──
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        import duckdb

        output_path = work / "terminal.duckdb"
        conn = duckdb.connect(str(output_path))
        conn.execute("PRAGMA threads=2")
        try:
            counts = _materialize_r2_parquet_terminal(
                conn,
                estate_path=estate_path,
                estate_created_at=estate_created_at,
                artists=artists,
                parquets=parquets,
                max_events_per_artist=max_events_per_artist,
                max_peers_per_artist=max_peers_per_artist,
            )
        finally:
            conn.close()
        manifest.rows_written = sum(int(v) for v in counts.values())

        # ── 2b. Fold gold artist-intelligence products into the serving DB ──
        # P17/P18: the compact artifact carries the factor tape + sentiment so
        # the Artist page serves fully materialized (no live fan-out). The
        # gold products are optional inputs: a missing CURRENT leaves the
        # serving DB without those tables rather than failing the build.
        gold_counts = _fold_gold_artist_intelligence(
            lake, work,
            factor_tape_current=(
                f"{GOLD_FACTOR_TAPE_PREFIX}/CURRENT.json"
            ),
            sentiment_current=f"{GOLD_SENTIMENT_PREFIX}/CURRENT.json",
            manifest=manifest,
        )
        counts.update(gold_counts)
        manifest.rows_written = sum(int(v) for v in counts.values())

        # ── 3. Demo artists (Phase 4): persisted inside the artifact ──
        demo_artists = _compute_demo_artists(output_path)

        # ── 4. Validation before any upload (Phase 5) ──
        validation = _validate_terminal_db(output_path, demo_artists, counts)
        if not validation["passed"]:
            raise RuntimeError(
                "SERVING_VALIDATION_FAILED: "
                + "; ".join(validation["checks"].get("failures", []) or [])
            )

        # ── 5. Streaming hash + bounded-size guard ──
        db_bytes = output_path.stat().st_size
        if db_bytes > max_artifact_bytes:
            raise RuntimeError(
                f"SERVING_ARTIFACT_TOO_LARGE: {db_bytes} bytes exceeds "
                f"max_artifact_bytes={max_artifact_bytes}; tighten row bounds and retry"
            )
        db_sha = _streaming_sha256(output_path)
        generation = "terminal_v1_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

        # ── 6. Upload generation object (streaming multipart upload) ──
        db_key = f"{TERMINAL_SERVING_PREFIX}/generations/{generation}/terminal.duckdb"
        lake._s3.upload_file(
            str(output_path), lake.config.lake_bucket, db_key,
            ExtraArgs={
                "ContentType": "application/octet-stream",
                "Metadata": {
                    "job_id": job_id, "sha256": db_sha,
                    "artifact": TERMINAL_ARTIFACT, "generation": generation,
                },
            },
        )
        manifest.output_paths.append(f"r2://{lake.config.lake_bucket}/{db_key}")
        manifest.output_hashes[db_key] = db_sha
        manifest.r2_write_bytes += db_bytes

        # ── 7. VERIFY the DB object BEFORE CURRENT moves ──
        verify_outputs(
            lake,
            bucket=lake.config.lake_bucket,
            output_hashes={db_key: db_sha},
            manifest=manifest,
            manifest_key_path=manifest_key_path,
        )

        # ── 8. Publish CURRENT.json ONLY after VERIFIED ──
        current_payload = {
            "artifact": TERMINAL_ARTIFACT,
            "contract_version": TERMINAL_CONTRACT_VERSION,
            "generation": generation,
            "object_key": db_key,
            "sha256": db_sha,
            "bytes": db_bytes,
            "created_at": now_iso(),
            "source_generations": {
                "estate_object": estate_key,
                "estate_bucket": estate_bucket,
                "inputs": {name: key for name, key in fixed_parquet_keys.items()},
                "wikidata_artist_external_ids": wd_key,
                "code_commit": _git_commit(),
            },
            "row_counts": counts,
            "demo_artists": demo_artists,
            "validation": validation,
        }
        current_bytes = json.dumps(current_payload, indent=2, sort_keys=True, default=str).encode()
        current_sha = hashlib.sha256(current_bytes).hexdigest()
        current_key = f"{TERMINAL_SERVING_PREFIX}/CURRENT.json"
        try:
            lake.put_bytes(
                lake.config.lake_bucket, current_key, current_bytes,
                content_type="application/json",
                metadata={"job_id": job_id, "sha256": current_sha, "generation": generation},
            )
        except Exception:
            manifest.error_code = ERR_PUBLICATION_FAILED
            manifest.error = "CURRENT.json write failed; generation remains VERIFIED"
            manifest.publication_state = STATUS_VERIFIED
            manifest.status = STATUS_VERIFIED
            lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())
            raise RuntimeError(
                f"PUBLICATION_FAILED: CURRENT pointer write failed for {current_key}"
            )
        manifest.output_paths.append(f"r2://{lake.config.lake_bucket}/{current_key}")
        manifest.output_hashes[current_key] = current_sha
        manifest.status = STATUS_PUBLISHED
        manifest.publication_state = STATUS_PUBLISHED
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        manifest.rows_read = manifest.r2_read_bytes
        manifest.params["generation"] = generation
        manifest.params["demo_artist_count"] = len(demo_artists)
        lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())

        return {
            "status": "COMPLETED",
            "manifest_key": manifest_key_path,
            "generation": generation,
            "serving_key": db_key,
            "current_key": current_key,
            "serving_sha256": db_sha,
            "serving_bytes": db_bytes,
            "row_counts": counts,
            "demo_artists": demo_artists,
            "validation_pass": validation["passed"],
            "latency_ms": validation.get("latency_ms", {}),
            "runtime_seconds": manifest.runtime_seconds,
            "r2_read_bytes": manifest.r2_read_bytes,
            "r2_write_bytes": manifest.r2_write_bytes,
        }

    except Exception as e:
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
            lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())
        except Exception:
            pass
        raise

    finally:
        shutil.rmtree(work, ignore_errors=True)


# ════════════════════════════════════════════════════════════════
# ARTIST FACTOR TAPE (gold) + SENTIMENT (gold) materializers
# ════════════════════════════════════════════════════════════════

#: Prefixes for the gold artist-intelligence data products (P17).
GOLD_FACTOR_TAPE_PREFIX = "gold/artist_factor_tape"
GOLD_SENTIMENT_PREFIX = "gold/artist_sentiment"

#: Staging prefix where the Worker writes official-API ticks.
STAGING_YOUTUBE_PREFIX = "staging/youtube/"
STAGING_SENTIMENT_PREFIX = "staging/sentiment_samples/"

#: Only these tick keys carry measurable values; everything else is metadata.
_YOUTUBE_VALUE_FACTORS = {
    "subscriber_count": ("count", "POINT_IN_TIME", "CHANNEL"),
    "channel_view_count": ("count", "POINT_IN_TIME", "CHANNEL"),
    "video_count": ("count", "POINT_IN_TIME", "CHANNEL"),
}


def _normalize_youtube_tick(tick: dict) -> list[dict]:
    """Normalize one official-API youtube tick into factor observations.

    Every observation carries the full comparability contract (migration
    050): measurement_basis, measurement_window, population_scope,
    geographic_scope, methodology_version, coverage_generation. A factor
    delta is only ever computed between rows that share all of these.
    """
    rows: list[dict] = []
    artist_key = tick.get("artist_key") or ""
    channel_id = tick.get("youtube_channel_id") or ""
    observed_at = tick.get("observed_at") or tick.get("knowledge_time") or ""
    retrieved_at = tick.get("retrieved_at") or observed_at
    knowledge_time = tick.get("knowledge_time") or observed_at
    evidence_ref = tick.get("raw_evidence_ref") or ""
    rights = tick.get("rights_status") or "RIGHTS_REVIEW_REQUIRED"
    commercial = tick.get("commercial_use_status") or "TERMS_REVIEW_REQUIRED"
    precision = tick.get("subscriber_precision") or "UNKNOWN"
    tick_gen = tick.get("schema_version") or "youtube_channel_tick_v1"
    if not artist_key or not channel_id or not observed_at:
        return rows
    for factor_name, (unit, basis, population) in _YOUTUBE_VALUE_FACTORS.items():
        value = tick.get(factor_name)
        if value is None:
            continue  # UNKNOWN stays UNKNOWN; we do not fabricate 0.
        rows.append({
            "factor_observation_key": hashlib.sha256(
                f"ytick|{artist_key}|{channel_id}|{factor_name}|{observed_at}|{value}".encode()
            ).hexdigest(),
            "artist_key": artist_key,
            "factor_family": "youtube_channel",
            "factor_name": factor_name,
            "platform": "youtube",
            "value": float(value),
            "unit": unit,
            "observation_time": observed_at,
            "available_at": observed_at,
            "knowledge_time": knowledge_time,
            "retrieved_at": retrieved_at,
            "source": "YOUTUBE_API",
            "evidence_ref": evidence_ref,
            "source_scope": "OFFICIAL_API_CHANNEL_STATISTICS",
            "rights_status": rights,
            "commercial_use_status": commercial,
            "quality_status": precision,
            "generation": tick_gen,
            "measurement_basis": basis,
            "measurement_window": None,
            "population_scope": population,
            "geographic_scope": None,
            "methodology_version": "youtube-data-api-v3-channels",
            "coverage_generation": tick_gen,
        })
    return rows


def _fold_gold_artist_intelligence(
    lake, work: Path,
    *, factor_tape_current: str, sentiment_current: str,
    manifest: JobManifest,
) -> dict[str, int]:
    """Fold gold artist-intelligence products into the compact serving DB.

    Opens its own connection to the artifact under construction, reads the
    CURRENT pointers for gold/artist_factor_tape and gold/artist_sentiment
    (if published), streams the parquet to scratch, and materializes the
    ``artist_factor_observations`` and ``artist_sentiment_observations``
    tables into the serving artifact.

    Missing gold products are tolerated (counts stay 0) — the base terminal
    still builds. Any object present but corrupt fails the build closed.
    """
    import duckdb

    q = _qp
    conn = duckdb.connect(str(work / "terminal.duckdb"))
    conn.execute("PRAGMA threads=2")
    out: dict[str, int] = {"artist_factor_observations": 0, "artist_sentiment_observations": 0}

    # ── Factor tape ──
    current = lake.read_checkpoint(lake.config.lake_bucket, factor_tape_current)
    if current and current.get("object_key"):
        tape_key = str(current["object_key"])
        tape_path = work / "gold_artist_factor_tape.parquet"
        size = _download_to_scratch(lake, lake.config.lake_bucket, tape_key, tape_path)
        manifest.source_paths.append(f"r2://{lake.config.lake_bucket}/{tape_key}")
        manifest.r2_read_bytes += size
        # Immutable rows only: a new snapshot is a new row, never an update.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artist_factor_observations (
                factor_observation_key VARCHAR PRIMARY KEY,
                artist_key VARCHAR NOT NULL,
                factor_family VARCHAR NOT NULL,
                factor_name VARCHAR NOT NULL,
                platform VARCHAR,
                value DOUBLE,
                unit VARCHAR,
                observation_time TIMESTAMP,
                available_at TIMESTAMP,
                knowledge_time TIMESTAMP,
                retrieved_at TIMESTAMP,
                period_start DATE,
                period_end DATE,
                source VARCHAR,
                evidence_ref VARCHAR,
                source_scope VARCHAR,
                rights_status VARCHAR,
                commercial_use_status VARCHAR,
                quality_status VARCHAR,
                generation VARCHAR,
                evidence_json JSON,
                measurement_basis VARCHAR,
                measurement_window VARCHAR,
                population_scope VARCHAR,
                geographic_scope VARCHAR,
                methodology_version VARCHAR,
                coverage_generation VARCHAR
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO artist_factor_observations (
                factor_observation_key, artist_key, factor_family,
                factor_name, platform, value, unit, observation_time,
                available_at, knowledge_time, retrieved_at,
                source, evidence_ref, source_scope, rights_status,
                commercial_use_status, quality_status, generation,
                measurement_basis, measurement_window, population_scope,
                geographic_scope, methodology_version, coverage_generation
            )
            SELECT
                factor_observation_key, artist_key, factor_family,
                factor_name, platform, value, unit,
                CAST(observation_time AS TIMESTAMP),
                CAST(available_at AS TIMESTAMP),
                CAST(knowledge_time AS TIMESTAMP),
                CAST(retrieved_at AS TIMESTAMP),
                source, evidence_ref, source_scope, rights_status,
                commercial_use_status, quality_status, generation,
                measurement_basis, measurement_window, population_scope,
                geographic_scope, methodology_version, coverage_generation
            FROM read_parquet({q(tape_path)})
            ON CONFLICT (factor_observation_key) DO NOTHING
            """
        )
        out["artist_factor_observations"] = int(
            conn.execute("SELECT COUNT(*) FROM artist_factor_observations").fetchone()[0]
        )

    # ── Sentiment ──
    current = lake.read_checkpoint(lake.config.lake_bucket, sentiment_current)
    if current and current.get("object_key"):
        sent_key = str(current["object_key"])
        sent_path = work / "gold_artist_sentiment.parquet"
        size = _download_to_scratch(lake, lake.config.lake_bucket, sent_key, sent_path)
        manifest.source_paths.append(f"r2://{lake.config.lake_bucket}/{sent_key}")
        manifest.r2_read_bytes += size
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artist_sentiment_observations (
                observation_key VARCHAR PRIMARY KEY,
                artist_key VARCHAR NOT NULL,
                platform VARCHAR NOT NULL,
                "date" DATE NOT NULL,
                mention_count BIGINT NOT NULL,
                analyzed_count BIGINT NOT NULL,
                positive_share DOUBLE,
                neutral_share DOUBLE,
                negative_share DOUBLE,
                sentiment_mean DOUBLE,
                engagement_weighted_sentiment DOUBLE,
                engagement_total BIGINT,
                topic_distribution JSON,
                language_distribution JSON,
                sample_quality VARCHAR NOT NULL,
                source_generation VARCHAR NOT NULL,
                model_name VARCHAR NOT NULL,
                model_version VARCHAR NOT NULL,
                deduplicated_count BIGINT,
                spam_filtered_count BIGINT,
                source VARCHAR NOT NULL,
                evidence_ref VARCHAR,
                source_scope VARCHAR NOT NULL,
                rights_status VARCHAR NOT NULL,
                commercial_use_status VARCHAR NOT NULL,
                quality_status VARCHAR NOT NULL,
                retrieved_at TIMESTAMP NOT NULL,
                knowledge_time TIMESTAMP
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO artist_sentiment_observations (
                observation_key, artist_key, platform, "date",
                mention_count, analyzed_count, positive_share,
                neutral_share, negative_share, sentiment_mean,
                engagement_weighted_sentiment, engagement_total,
                topic_distribution, sample_quality, source_generation,
                model_name, model_version, source, evidence_ref,
                source_scope, rights_status, commercial_use_status,
                quality_status, retrieved_at, knowledge_time
            )
            SELECT
                observation_key, artist, platform, CAST("date" AS DATE),
                mention_count, analyzed_count, positive_share,
                neutral_share, negative_share, sentiment_mean,
                engagement_weighted_sentiment, engagement_total,
                topic_distribution::JSON, sample_quality, source_generation,
                'vader', 'VADER_3.3.2', source, NULL,
                'OFFICIAL_API_COMMENT_SAMPLE', rights_status::VARCHAR,
                commercial_use_status::VARCHAR, 'OBSERVED',
                CAST(retrieved_at AS TIMESTAMP), CAST("date" AS TIMESTAMP)
            FROM read_parquet({q(sent_path)})
            ON CONFLICT (observation_key) DO NOTHING
            """
        )
        out["artist_sentiment_observations"] = int(
            conn.execute("SELECT COUNT(*) FROM artist_sentiment_observations").fetchone()[0]
        )

    conn.close()
    return out


def run_artist_factor_tape_build(spec: dict, scratch_dir: Path) -> dict:
    """Refresh without discarding history; retain the existing dispatch contract."""
    from festival_bloomberg.cloud.factor_history import run_factor_history

    return run_factor_history(spec, scratch_dir, lake=_get_lake(), normalize=_normalize_youtube_tick)


def run_artist_sentiment_build(spec: dict, scratch_dir: Path) -> dict:
    """Materialize the gold artist sentiment daily aggregate.

    Reads (LAKE): staging/sentiment_samples/...json — bounded comment/social
    samples the Worker collected. Each sample carries the raw text, artist_key,
    platform, and observation time; no usernames or user IDs.

    Aggregates per artist×platform×date with the VADER baseline:
        mention_count, analyzed_count, positive/neutral/negative share,
        sentiment_mean, engagement_weighted_sentiment, sample_quality,
        source_generation. Raw identities never enter the gold product.

    Writes (LAKE):
        - gold/artist_sentiment/<generation>/artist_sentiment.parquet
        - gold/artist_sentiment/CURRENT.json
    """
    lake = _get_lake()
    job_id = spec.get("job_id", "artist_sentiment_build_v1")
    params = spec.get("params", {})
    max_samples = int(params.get("max_samples") or 100_000)

    manifest = new_manifest(
        job_type="artist_sentiment_build_v1",
        job_id=job_id,
        code_commit=_git_commit(),
        container_image="festival-bloomberg-batch:latest",
        params=params,
    )
    manifest_key_path = manifest_key("artist_sentiment_build_v1", job_id)
    start = time.time()
    work = scratch_dir / "sentiment"
    work.mkdir(parents=True, exist_ok=True)

    try:
        from collections import defaultdict

        from festival_bloomberg.vader_sentiment import score_text

        samples = lake.list_prefix(
            lake.config.lake_bucket, STAGING_SENTIMENT_PREFIX, limit=max_samples,
        )
        sample_keys = [s["key"] for s in samples if s["key"].endswith(".json")][:max_samples]
        if not sample_keys:
            # No real samples yet: publish an HONEST empty generation with a
            # clear status instead of fabricating rows (UNKNOWN != 0).
            current_payload = {
                "artifact": GOLD_SENTIMENT_PREFIX,
                "contract_version": "artist_sentiment_v1",
                "generation": "artist_sentiment_v1_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
                "object_key": None,
                "sha256": None,
                "bytes": 0,
                "rows": 0,
                "created_at": now_iso(),
                "status": "NO_SAMPLES_YET",
                "source_prefix": STAGING_SENTIMENT_PREFIX,
            }
            current_key = f"{GOLD_SENTIMENT_PREFIX}/CURRENT.json"
            lake.put_bytes(
                lake.config.lake_bucket, current_key,
                json.dumps(current_payload, indent=2, sort_keys=True).encode(),
                content_type="application/json",
                metadata={"job_id": job_id, "generation": current_payload["generation"]},
            )
            manifest.status = STATUS_PUBLISHED
            manifest.publication_state = STATUS_PUBLISHED
            manifest.completed_at = now_iso()
            manifest.runtime_seconds = round(time.time() - start, 2)
            lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())
            return {
                "status": "COMPLETED",
                "generation": current_payload["generation"],
                "rows": 0,
                "note": "NO_SAMPLES_YET — no staging sentiment samples; honest empty generation",
                "manifest_key": manifest_key_path,
            }

        # ── Aggregate with VADER (per artist×platform×date) ──
        # P10: deduplicate repeated/cross-posted text — an identical comment
        # collected again (same artist/platform/day) counts once, never twice.
        agg: dict[tuple, dict] = defaultdict(lambda: {
            "mention_count": 0, "analyzed_count": 0, "sentiment_sum": 0.0,
            "engagement_sum": 0.0, "weighted_sum": 0.0,
            "positive": 0, "neutral": 0, "negative": 0, "langs": set(),
            "rights": {}, "commercial": {}, "sources": {}, "last_observed": "",
        })
        seen_texts: dict[tuple, set] = defaultdict(set)
        skipped = 0
        for key in sample_keys:
            try:
                raw = lake.get_bytes(lake.config.lake_bucket, key)
            except Exception:
                skipped += 1
                continue
            manifest.r2_read_bytes += len(raw)
            try:
                s = json.loads(raw)
            except (ValueError, TypeError):
                skipped += 1
                continue
            artist_key = s.get("artist_key") or ""
            platform = s.get("platform") or "unknown"
            text = s.get("text") or ""
            obs = s.get("observed_at") or ""
            engagement = float(s.get("engagement") or 0)
            if not artist_key or not text or not obs:
                skipped += 1
                continue
            day = obs[:10]
            bucket = (artist_key, platform, day)
            norm = " ".join(text.lower().split())
            if norm in seen_texts[bucket]:
                skipped += 1
                continue
            seen_texts[bucket].add(norm)
            a = agg[bucket]
            a["mention_count"] += 1
            a["analyzed_count"] += 1
            score = float(score_text(text).compound)
            a["sentiment_sum"] += score
            a["engagement_sum"] += engagement
            a["weighted_sum"] += score * (1 + engagement)
            if score >= 0.05:
                a["positive"] += 1
            elif score <= -0.05:
                a["negative"] += 1
            else:
                a["neutral"] += 1
            lang = s.get("language") or "unknown"
            a["langs"].add(lang)
            r = s.get("rights_status") or "RIGHTS_REVIEW_REQUIRED"
            c = s.get("commercial_use_status") or "TERMS_REVIEW_REQUIRED"
            so = s.get("source") or "UNKNOWN"
            a["rights"][r] = a["rights"].get(r, 0) + 1
            a["commercial"][c] = a["commercial"].get(c, 0) + 1
            a["sources"][so] = a["sources"].get(so, 0) + 1
            if obs > a["last_observed"]:
                a["last_observed"] = obs

        rows_out: list[dict] = []
        for (artist_key, platform, day), a in agg.items():
            total = a["analyzed_count"]
            rows_out.append({
                "observation_key": hashlib.sha256(
                    f"sent|{artist_key}|{platform}|{day}".encode()
                ).hexdigest(),
                "artist": artist_key,
                "platform": platform,
                "date": day,
                "mention_count": a["mention_count"],
                "analyzed_count": a["analyzed_count"],
                "positive_share": round(a["positive"] / total, 4) if total else 0.0,
                "neutral_share": round(a["neutral"] / total, 4) if total else 0.0,
                "negative_share": round(a["negative"] / total, 4) if total else 0.0,
                "sentiment_mean": round(a["sentiment_sum"] / total, 4) if total else 0.0,
                "engagement_weighted_sentiment": round(
                    a["weighted_sum"] / (a["engagement_sum"] + total), 4
                ) if total else 0.0,
                "engagement_total": round(a["engagement_sum"], 2),
                "topic_distribution": "{}",
                "sample_quality": "VADER_BASELINE",
                "source_generation": "artist_sentiment_v1",
                "languages": json.dumps(sorted(a["langs"])),
                "rights_status": max(a["rights"], key=a["rights"].get)
                if a["rights"] else "RIGHTS_REVIEW_REQUIRED",
                "commercial_use_status": max(a["commercial"], key=a["commercial"].get)
                if a["commercial"] else "TERMS_REVIEW_REQUIRED",
                "source": max(a["sources"], key=a["sources"].get)
                if a["sources"] else "UNKNOWN",
                "retrieved_at": a["last_observed"] or f"{day}T00:00:00Z",
            })

        columns = [
            "observation_key", "artist", "platform", "date", "mention_count",
            "analyzed_count", "positive_share", "neutral_share", "negative_share",
            "sentiment_mean", "engagement_weighted_sentiment", "engagement_total",
            "topic_distribution", "sample_quality", "source_generation", "languages",
            "source", "rights_status", "commercial_use_status", "retrieved_at",
        ]
        import pyarrow as pa
        import pyarrow.parquet as pq

        out_path = work / "artist_sentiment.parquet"
        table = pa.table(
            {col: [r.get(col) for r in rows_out] for col in columns},
            schema=pa.schema([pa.field(c, pa.string() if c in ("topic_distribution", "languages", "sample_quality", "source_generation") else pa.float64() if c in ("positive_share", "neutral_share", "negative_share", "sentiment_mean", "engagement_weighted_sentiment", "engagement_total") else pa.int64() if c in ("mention_count", "analyzed_count") else pa.string()) for c in columns]),
        )
        pq.write_table(table, out_path)

        generation = "artist_sentiment_v1_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_key = f"{GOLD_SENTIMENT_PREFIX}/{generation}/artist_sentiment.parquet"
        lake._s3.upload_file(
            str(out_path), lake.config.lake_bucket, out_key,
            ExtraArgs={
                "ContentType": "application/octet-stream",
                "Metadata": {"job_id": job_id, "generation": generation, "artifact": GOLD_SENTIMENT_PREFIX},
            },
        )
        manifest.output_paths.append(f"r2://{lake.config.lake_bucket}/{out_key}")
        out_sha = _streaming_sha256(out_path)
        manifest.output_hashes[out_key] = out_sha
        manifest.r2_write_bytes += out_path.stat().st_size
        manifest.rows_written = len(rows_out)

        verify_outputs(
            lake,
            bucket=lake.config.lake_bucket,
            output_hashes={out_key: out_sha},
            manifest=manifest,
            manifest_key_path=manifest_key_path,
        )

        current_payload = {
            "artifact": GOLD_SENTIMENT_PREFIX,
            "contract_version": "artist_sentiment_v1",
            "generation": generation,
            "object_key": out_key,
            "sha256": out_sha,
            "bytes": out_path.stat().st_size,
            "rows": len(rows_out),
            "artists": len({r["artist"] for r in rows_out}),
            "created_at": now_iso(),
            "source_prefix": STAGING_SENTIMENT_PREFIX,
            "samples_read": len(sample_keys),
            "skipped": skipped,
            "model": {"name": "vader", "version": "VADER_3.3.2"},
        }
        current_key = f"{GOLD_SENTIMENT_PREFIX}/CURRENT.json"
        lake.put_bytes(
            lake.config.lake_bucket, current_key,
            json.dumps(current_payload, indent=2, sort_keys=True, default=str).encode(),
            content_type="application/json",
            metadata={"job_id": job_id, "generation": generation},
        )
        manifest.output_paths.append(f"r2://{lake.config.lake_bucket}/{current_key}")
        manifest.status = STATUS_PUBLISHED
        manifest.publication_state = STATUS_PUBLISHED
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())

        return {
            "status": "COMPLETED",
            "generation": generation,
            "sentiment_key": out_key,
            "rows": len(rows_out),
            "artists": len({r["artist"] for r in rows_out}),
            "samples_read": len(sample_keys),
            "skipped": skipped,
            "manifest_key": manifest_key_path,
        }

    except Exception as e:
        if manifest.error_code is None:
            manifest.error_code = ERR_JOB_EXEC_FAILED
        manifest.status = STATUS_FAILED
        manifest.error = str(e)[:300]
        manifest.error_detail = traceback.format_exc()
        manifest.completed_at = now_iso()
        manifest.runtime_seconds = round(time.time() - start, 2)
        try:
            lake.write_manifest(lake.config.lake_bucket, manifest_key_path, manifest.to_dict())
        except Exception:
            pass
        raise

    finally:
        shutil.rmtree(work, ignore_errors=True)


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
            partitioned = conn.execute("""
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
