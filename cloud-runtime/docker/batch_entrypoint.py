#!/usr/bin/env python3
"""
Batch entrypoint for Festival Bloomberg cloud data-processing jobs.

Runs inside a Cloudflare Container (standard-4: 4 vCPU / 12 GiB / 20 GB disk).
Receives a job spec via the FI_BATCH_JOB environment variable (JSON).

Job types:
    identity_graph_v2   — materialize Identity Graph V2 from R2 source
    listenbrainz_map    — map stage of the LB production scan
    listenbrainz_reduce — reduce stage (global listener aggregation + TOP_25)

Contract:
    READ source from R2 (bounded local scratch)
    PROCESS with DuckDB/PyArrow
    WRITE partials + checkpoint to R2
    VERIFY outputs
    UPDATE checkpoint manifest in R2
    DELETE local scratch
    EXIT

On restart, reads the R2 checkpoint and skips completed batches.
No unique data may remain on ephemeral container disk after exit.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent  # /app in the batch container
sys.path.insert(0, str(PROJECT_ROOT / "python"))
sys.path.insert(0, str(PROJECT_ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# V1B P1: Fixed machine-readable error codes. Raw exception text is never
# placed in the status API / manifest summary — only these codes plus
# bounded safe metadata.
ERR_JOB_VALIDATION_FAILED = "JOB_VALIDATION_FAILED"
ERR_JOB_EXEC_FAILED = "JOB_EXEC_FAILED"
ERR_NO_JOB_SPEC = "NO_JOB_SPEC"


# P7: Explicit job-type allowlist. A request cannot supply an arbitrary
# command/shell/entrypoint — only these job types are dispatchable.
ALLOWED_JOB_TYPES = frozenset({
    "identity_graph_v2",
    "listenbrainz_map",
    "listenbrainz_reduce",
    "cloud_smoke",
})

# Bounded numeric parameter limits.
MAX_MAX_BATCHES = 2000
MAX_PARTITIONS = 1024
MIN_PARTITIONS = 1
MAX_TOP_K = 1000
MAX_SOURCE_GENERATION_LEN = 128
MAX_JOB_ID_LEN = 128

# Characters allowed in job_id and source_generation (alphanumeric, dash, underscore).
_SAFE_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789-_"
)


def _validate_id(value: str, field: str) -> str:
    """Validate that an identifier is a safe bounded string.

    Rejects path traversal, shell-like payloads, and oversized values.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    if len(value) > MAX_JOB_ID_LEN:
        raise ValueError(f"{field} too long: max {MAX_JOB_ID_LEN} chars")
    if not all(c in _SAFE_ID_CHARS for c in value):
        raise ValueError(
            f"{field} contains invalid characters; "
            "only alphanumeric, dash, underscore allowed"
        )
    return value


def _validate_generation(value: str) -> str:
    """Validate source_generation format."""
    if not isinstance(value, str):
        raise ValueError("source_generation must be a string")
    if len(value) > MAX_SOURCE_GENERATION_LEN:
        raise ValueError(f"source_generation too long: max {MAX_SOURCE_GENERATION_LEN} chars")
    if not all(c in _SAFE_ID_CHARS for c in value):
        raise ValueError("source_generation contains invalid characters")
    return value


def _validate_params(params: dict) -> dict:
    """Validate bounded numeric parameters in the job spec."""
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    validated = {}
    for k, v in params.items():
        if k in ("partitions", "listener_hash_partitions"):
            if not isinstance(v, int) or v < MIN_PARTITIONS or v > MAX_PARTITIONS:
                raise ValueError(f"{k} must be int in [{MIN_PARTITIONS}, {MAX_PARTITIONS}]")
            validated[k] = v
        elif k in ("max_shards", "max_batches"):
            if not isinstance(v, int) or v < 1 or v > MAX_MAX_BATCHES:
                raise ValueError(f"{k} must be int in [1, {MAX_MAX_BATCHES}]")
            validated[k] = v
        elif k in ("top_k_per_listener", "top_k"):
            if not isinstance(v, int) or v < 1 or v > MAX_TOP_K:
                raise ValueError(f"{k} must be int in [1, {MAX_TOP_K}]")
            validated[k] = v
        elif k in ("min_shared_listeners", "min_shared"):
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"{k} must be non-negative int")
            validated[k] = v
        else:
            # Pass through non-numeric params as-is (strings, etc.)
            validated[k] = v
    return validated


def validate_spec(spec: dict) -> dict:
    """Validate the full job spec before dispatch.

    Rejects:
    - unknown job_type
    - path traversal in job_id/source_generation
    - shell-like payloads
    - arbitrary command/executable/entrypoint
    - unbounded numeric parameters
    - oversized values
    """
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")

    job_type = spec.get("job_type", "")
    if job_type not in ALLOWED_JOB_TYPES:
        raise ValueError(
            f"Unknown or disallowed job_type: '{job_type}'. "
            f"Allowed: {sorted(ALLOWED_JOB_TYPES)}"
        )

    job_id = _validate_id(spec.get("job_id", f"batch_{int(time.time())}"), "job_id")
    source_generation = _validate_generation(spec.get("source_generation", ""))
    params = _validate_params(spec.get("params", {}))
    max_batches = spec.get("max_batches")
    if max_batches is not None:
        if not isinstance(max_batches, int) or max_batches < 1 or max_batches > MAX_MAX_BATCHES:
            raise ValueError(f"max_batches must be int in [1, {MAX_MAX_BATCHES}]")

    # Reject any attempt to supply arbitrary command/executable/shell/env.
    for forbidden_key in ("command", "exec", "executable", "shell", "entrypoint", "cmd"):
        if forbidden_key in spec:
            raise ValueError(
                f"Spec contains forbidden key '{forbidden_key}' — "
                "arbitrary command execution is not allowed."
            )

    # V1B P0-3: FI_BATCH_JOB must contain ONLY the sanitized logical job
    # spec. Secrets/R2 credentials are provided by the DO container env,
    # never through the job spec. A spec carrying env_vars is rejected.
    if "env_vars" in spec:
        raise ValueError(
            "Spec contains 'env_vars' — secrets must never travel inside "
            "FI_BATCH_JOB; the container controller supplies them via its "
            "own environment bindings."
        )

    # Sanitized spec: only safe logical control fields. Everything else
    # (env_vars, secrets, unknown keys) is dropped.
    return {
        "job_type": job_type,
        "job_id": job_id,
        "source_generation": source_generation,
        "params": params,
        "max_batches": max_batches,
    }


def sanitize_job_spec(raw_spec: dict) -> dict:
    """V1B P0-3: Return ONLY the sanitized logical job spec.

    Drops env_vars, secrets, and any unknown key. Never spreads raw_spec.
    Raises ValueError if the raw spec carries forbidden content.
    """
    return validate_spec(raw_spec)


def main() -> int:
    job_json = os.environ.get("FI_BATCH_JOB")
    if not job_json:
        print(json.dumps({
            "status": "FAILED", "error_code": ERR_NO_JOB_SPEC,
            "error": "No FI_BATCH_JOB provided",
        }))
        return 1

    try:
        raw_spec = json.loads(job_json)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "status": "FAILED", "error_code": ERR_JOB_VALIDATION_FAILED,
            "error": f"Invalid job JSON: {e}",
        }))
        return 1

    # P7 + V1B P0-3: Validate AND sanitize the spec before dispatch. The
    # sanitized spec is the ONLY thing used downstream — never raw_spec.
    try:
        spec = sanitize_job_spec(raw_spec)
    except (ValueError, TypeError) as e:
        print(json.dumps({
            "status": "FAILED", "error_code": ERR_JOB_VALIDATION_FAILED,
            "error": f"Spec validation failed: {e}",
        }))
        return 1

    job_type = spec["job_type"]
    job_id = spec["job_id"]
    scratch_dir = Path(os.environ.get("FI_SCRATCH_DIR", "/tmp/festival-bloomberg"))
    scratch_dir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    result: dict = {
        "job_id": job_id,
        "job_type": job_type,
        "status": "RUNNING",
        "started_at": now_iso(),
        "scratch_dir": str(scratch_dir),
        "container": {
            "vcpu": 4,
            "memory_mib": 12288,
            "disk_gb": 20,
        },
    }

    try:
        if job_type == "identity_graph_v2":
            from festival_bloomberg.cloud.batch_jobs import run_identity_graph_v2
            outcome = run_identity_graph_v2(spec, scratch_dir)
            result.update(outcome)
        elif job_type == "listenbrainz_map":
            from festival_bloomberg.cloud.batch_jobs import run_listenbrainz_map
            outcome = run_listenbrainz_map(spec, scratch_dir)
            result.update(outcome)
        elif job_type == "listenbrainz_reduce":
            from festival_bloomberg.cloud.batch_jobs import run_listenbrainz_reduce
            outcome = run_listenbrainz_reduce(spec, scratch_dir)
            result.update(outcome)
        elif job_type == "cloud_smoke":
            from festival_bloomberg.cloud.batch_jobs import run_cloud_smoke
            outcome = run_cloud_smoke(spec, scratch_dir)
            result.update(outcome)
        else:
            # Should be unreachable because validate_spec() already rejects
            # unknown job types, but fail closed here too.
            raise ValueError(f"Unknown job_type: {job_type}")

        result["status"] = "COMPLETED"

    except Exception as e:
        result["status"] = "FAILED"
        result["error_code"] = ERR_JOB_EXEC_FAILED
        result["error"] = str(e)[:300]
        result["traceback"] = traceback.format_exc()

    finally:
        # Clean scratch — no unique data may remain on ephemeral disk.
        try:
            import shutil
            shutil.rmtree(scratch_dir, ignore_errors=True)
        except Exception:
            pass

    result["completed_at"] = now_iso()
    result["duration_seconds"] = round(time.time() - start, 2)

    # Print the final JSON summary on the last line (DO parses this).
    print(json.dumps(result, default=str))
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
