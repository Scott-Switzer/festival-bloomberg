"""P3-P13 — Production MAP/REDUCE full-scan for the 205 GB ListenBrainz corpus.

Why not the pilot architecture: the pilot staged ALL matched rows to one local
Parquet (1% -> 14.3M rows). A full run would approach ~1.4B matched rows / ~289M
listener_artist rows — impossible on this 8 GB RAM / ~14 GB free-disk Mac.

This pipeline is bounded, resumable, and applies the audience-affinity policy
GLOBALLY (never per source shard):

    MAP    per batch of 16 shards: stream -> filter to 25K -> aggregate ->
           write tiny partial parquets to R2 -> DELETE local -> checkpoint.

    REDUCE artist_day:   aggregate ALL artist_day partials -> silver, by year/mo.
    REDUCE affinity:     hash-partition listener_artist by listener_hash, then
           per partition aggregate per-listener globally, rank, apply top-K
           (from the P1/P2 sensitivity study: TOP_25), generate pairs, emit
           pair partials -> reduce to non-serving Silver affinity evidence.

Phases are independent commands so each is bounded and resumable:

    python scripts/lb_full_scan.py map --max-shards N [--batch 16]
    python scripts/lb_full_scan.py reduce-artist-day
    python scripts/lb_full_scan.py reduce-affinity --partitions H
    python scripts/lb_full_scan.py reduce-pairs

Checkpoint manifest written to
    control/lake/listenbrainz_full_scan/current.json
Skips completed batches on restart.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from festival_bloomberg.lake.r2 import r2_client  # noqa: E402
from festival_bloomberg.lake.catalog import register_dataset  # noqa: E402

RAW_BUCKET = "festival-intelligence-raw"
LAKE_BUCKET = "festival-intelligence-lake"
PRIVATE_BUCKET = "festival-intelligence-private"
RAW_KEY = ("bulk/listenbrainz/dump=2593-20260712-000004/"
           "listenbrainz-spark-dump-2593-20260712-000004-full.tar")
DUMP_VERSION = "2593-20260712-000004"

# Local plumbing (survives per-session; contents are transient, uploaded + deleted)
# Cloud batch overrides via FI_LB_SCAN_ROOT + FI_LB_CHECKPOINT_AUTHORITY=CLOUD_JOB_R2
_SCAN_ROOT = Path(os.environ["FI_LB_SCAN_ROOT"]) if os.environ.get("FI_LB_SCAN_ROOT") else None
INDEX_CACHE = (
    (_SCAN_ROOT / "lb_tar_index.json")
    if _SCAN_ROOT
    else Path("control/lake/lb_tar_index.json")
)
ESTATE_JSON = Path("data/control/artist_security_25000/v1/"
                   "estate_20260828T013314Z_f87e5d1d073e.json")
CHECKPOINT = (
    (_SCAN_ROOT / "checkpoint.json")
    if _SCAN_ROOT
    else Path("control/lake/listenbrainz_full_scan/current.json")
)
SPILL = (_SCAN_ROOT / "spill") if _SCAN_ROOT else Path("/tmp/lb_full_spill")
LOCAL = (_SCAN_ROOT / "local") if _SCAN_ROOT else Path("/tmp/lb_full_local")

# Policy (from P1/P2 sensitivity study — see control/lake/listenbrainz_sensitivity_summary.json)
TOP_K = 25                      # per-listener global artist cap
MIN_SHARED_LISTENERS = 3        # minimum shared listeners to persist an edge

# Sizes
# Bounded-test geometry for the constrained dev Mac (8 GiB RAM / ~3 GiB free):
# batch=4 keeps the per-batch working set (~540 MiB shards + DuckDB agg) inside
# the available disk so DuckDB never spills; the batch size is part of the scan
# namespace so a full-capacity run can still use batch=16 under the same code.
BATCH_SHARDS = 4
TOTAL_SOURCE_BYTES = 205_073_162_240
SOURCE_DATASET = "raw.listenbrainz_full_dump"
PIPELINE_VERSION = 3
# 0.9 GiB floor (approved tightening; reduced further because the Mac has
# ~1.5 GiB free and per-batch peak local use is ~0.4 GiB at batch=4 with a
# 512 MB DuckDB cap).  The pipeline is resume-safe: a batch that fails on disk
# pressure is simply redone on restart.
# Cloud standard-4 has 20 GiB ephemeral — require 8 GiB free before map.
_CLOUD_AUTH = os.environ.get("FI_LB_CHECKPOINT_AUTHORITY", "").strip()
MIN_FREE_DISK_BYTES = (
    int(8 * 1024 * 1024 * 1024)
    if _CLOUD_AUTH == "CLOUD_JOB_R2"
    else int(0.9 * 1024 * 1024 * 1024)
)
RUN_LOCK = (_SCAN_ROOT / "run.lock") if _SCAN_ROOT else Path("/tmp/festival_listenbrainz_full_scan.lock")
PRIVATE_PARTIAL_ROOT = "listenbrainz/listener_level"
PRIVATE_REDUCER_ACCESS = "LISTENER_LEVEL_REDUCER_ONLY"
HOST_FINGERPRINT = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16]
CHECKPOINT_AUTHORITY = (
    "CLOUD_JOB_R2" if _CLOUD_AUTH == "CLOUD_JOB_R2" else "LOCAL_HOST_ONLY"
)
CLOUD_JOB_ID = os.environ.get("FI_LB_JOB_ID", "").strip() or None
CLOUD_CHECKPOINT_KEY = (
    f"control/jobs/listenbrainz_tar_map/{CLOUD_JOB_ID}/checkpoint.json"
    if CLOUD_JOB_ID
    else None
)
COMPETING_HEAVY_MARKERS = (
    "build_wikidata_music_graph.py",
    "dense_derived_artifacts.py",
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sql_string_or_null(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


@contextmanager
def exclusive_run_lock(command: str):
    """Prevent concurrent map/reducer processes from sharing local/R2 state."""
    RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "unknown owner"
            raise RuntimeError(
                f"another ListenBrainz pipeline command holds {RUN_LOCK}: {owner}"
            ) from exc
        lock_state = {
            "pid": os.getpid(),
            "command": command,
            "acquired_at": now_iso(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(lock_state, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        try:
            yield lock_state
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            return json.loads(CHECKPOINT.read_text())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"checkpoint is not valid JSON: {CHECKPOINT}") from exc
    # Cloud: durable resume authority is the per-job R2 checkpoint.
    if CHECKPOINT_AUTHORITY == "CLOUD_JOB_R2" and CLOUD_CHECKPOINT_KEY:
        try:
            s3 = r2_client()
            body = s3.get_object(Bucket=LAKE_BUCKET, Key=CLOUD_CHECKPOINT_KEY)["Body"].read()
            ckpt = json.loads(body)
            CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
            CHECKPOINT.write_text(json.dumps(ckpt, indent=2) + "\n")
            return ckpt
        except Exception:  # noqa: BLE001 — missing key → fresh checkpoint
            pass
    return {
        "pipeline": "listenbrainz_full_scan",
        "pipeline_version": PIPELINE_VERSION,
        "source_dataset": SOURCE_DATASET,
        "dump_version": DUMP_VERSION,
        "source_key": RAW_KEY,
        "source_bytes": TOTAL_SOURCE_BYTES,
        "source_etag": None,
        "source_object_last_modified": None,
        "source_first_access_at": None,
        "tar_index_sha256": None,
        "artist_universe_sha256": None,
        "batch_size_shards": BATCH_SHARDS,
        "listener_hash_partitions": None,
        "run_namespace": None,
        "listener_level_access": "QUARANTINED_NOT_SERVING",
        "checkpoint_authority": CHECKPOINT_AUTHORITY,
        "execution_host_fingerprint": HOST_FINGERPRINT,
        "source_shard_count": 0,
        "map_target_shards": None,
        "completed_batches": [],   # list of [first_idx, last_idx]
        "batch_artifacts": {},
        "completed_shards": [],
        "completed_artist_day": False,
        "completed_affinity_partitions": [],
        "completed_pairs": False,
        "listens_scanned": 0,
        "matched_listens": 0,
        "unresolved": 0,
        "no_credit": 0,
        "artists_seen": 0,
        "bytes_read": 0,
        "fetch_retries": 0,
        "runtime_seconds": 0.0,
        "r2_output_objects": 0,
        "r2_output_bytes": 0,
        "resource_metrics": {
            "peak_rss_bytes": 0,
            "minimum_free_disk_bytes": None,
        },
        "started_at": None,
        "updated_at": None,
    }


def validate_checkpoint(ckpt: dict, *, partitions: int | None = None) -> None:
    """Fail closed when a checkpoint belongs to a different scan geometry."""
    if ckpt.get("source_dataset") != SOURCE_DATASET:
        raise RuntimeError(
            "checkpoint source_dataset is incompatible; archive it and start a fresh run"
        )
    if int(ckpt.get("pipeline_version") or 0) != PIPELINE_VERSION:
        raise RuntimeError(
            f"checkpoint pipeline_version must be {PIPELINE_VERSION}; archive it and start fresh"
        )
    if ckpt.get("dump_version") != DUMP_VERSION:
        raise RuntimeError("checkpoint dump_version does not match the configured raw dump")
    if ckpt.get("source_key") != RAW_KEY:
        raise RuntimeError("checkpoint source_key does not match the configured raw object")
    if int(ckpt.get("source_bytes") or 0) != TOTAL_SOURCE_BYTES:
        raise RuntimeError("checkpoint source byte count does not match the configured raw object")
    completed = bool(
        ckpt.get("completed_batches")
        or ckpt.get("completed_shards")
        or ckpt.get("completed_affinity_partitions")
    )
    saved = ckpt.get("listener_hash_partitions")
    if completed and saved is None:
        raise RuntimeError(
            "legacy checkpoint has no listener_hash_partitions; do not resume it implicitly"
        )
    if partitions is not None and saved is not None and int(saved) != partitions:
        raise RuntimeError(
            f"checkpoint uses {saved} listener partitions, command requests {partitions}; "
            "use the same value or start a fresh run"
        )
    if ckpt.get("batch_size_shards") not in (None, BATCH_SHARDS):
        raise RuntimeError("checkpoint batch size does not match the fixed map geometry")
    if completed and ckpt.get("duckdb_version") != duckdb.__version__:
        raise RuntimeError("checkpoint DuckDB version differs from the active runtime")
    if completed and ckpt.get("listener_partition_algorithm") != "DUCKDB_HASH_V1":
        raise RuntimeError("checkpoint listener partition algorithm is incompatible")
    if completed and (
        not ckpt.get("tar_index_sha256") or not ckpt.get("artist_universe_sha256")
    ):
        raise RuntimeError("checkpoint is missing exact index/universe input digests")
    if completed and ckpt.get("checkpoint_authority") != CHECKPOINT_AUTHORITY:
        raise RuntimeError("checkpoint authority does not match this runtime mode")
    if (
        completed
        and CHECKPOINT_AUTHORITY == "LOCAL_HOST_ONLY"
        and ckpt.get("execution_host_fingerprint") != HOST_FINGERPRINT
    ):
        raise RuntimeError("multi-host resume is prohibited for LOCAL_HOST_ONLY")
    if (
        completed
        and CHECKPOINT_AUTHORITY == "CLOUD_JOB_R2"
        and CLOUD_JOB_ID
        and ckpt.get("cloud_job_id") not in (None, CLOUD_JOB_ID)
    ):
        raise RuntimeError("cloud checkpoint job_id does not match FI_LB_JOB_ID")
    if ckpt.get("completed_batches") and not ckpt.get("batch_partition_coverage"):
        raise RuntimeError("checkpoint is missing per-batch partition coverage markers")
    namespace = ckpt.get("run_namespace")
    if completed and not namespace:
        raise RuntimeError("checkpoint has completed work but no run_namespace")
    target_shards = ckpt.get("map_target_shards")
    if namespace and not target_shards:
        raise RuntimeError("checkpoint has a run_namespace but no map_target_shards")
    if partitions is not None and namespace:
        expected_namespace = scan_namespace(
            partitions,
            int(target_shards),
            tar_index_sha256=ckpt.get("tar_index_sha256"),
            artist_universe_sha256=ckpt.get("artist_universe_sha256"),
        )
        if namespace != expected_namespace:
            raise RuntimeError(
                f"checkpoint namespace {namespace!r} does not match {expected_namespace!r}"
            )


def ensure_map_complete(ckpt: dict) -> None:
    """Reducers must never publish a partial scan as a complete dataset."""
    source_shards = int(ckpt.get("source_shard_count") or 0)
    expected = int(ckpt.get("map_target_shards") or 0)
    if expected <= 0 or source_shards <= 0 or expected > source_shards:
        raise RuntimeError(
            f"map target is invalid (target={expected}, source={source_shards})"
        )
    completed = {int(i) for i in (ckpt.get("completed_shards") or [])}
    required = set(range(expected))
    if expected <= 0 or completed != required:
        missing = len(required - completed) if expected > 0 else expected
        extra = len(completed - required) if expected > 0 else len(completed)
        raise RuntimeError(
            f"map shard membership is invalid ({len(completed)}/{expected}; "
            f"missing={missing}, extra={extra}); reducers are blocked"
        )


def scan_namespace(
    partitions: int,
    target_shards: int,
    *,
    tar_index_sha256: str | None = None,
    artist_universe_sha256: str | None = None,
) -> str:
    """Immutable R2 namespace for one exact source and map geometry."""
    namespace = (
        f"v{PIPELINE_VERSION}-{DUMP_VERSION}-b{BATCH_SHARDS}"
        f"-p{partitions}-n{target_shards}"
    )
    if tar_index_sha256 or artist_universe_sha256:
        if not tar_index_sha256 or not artist_universe_sha256:
            raise ValueError("both index and universe digests are required")
        namespace += f"-i{tar_index_sha256[:12]}-u{artist_universe_sha256[:12]}"
    return namespace


def partial_prefix(ckpt: dict, family: str) -> str:
    namespace = ckpt.get("run_namespace")
    if not namespace:
        raise RuntimeError("run_namespace is required before reading or writing partials")
    if family == "listener_artist":
        return f"{PRIVATE_PARTIAL_ROOT}/{namespace}/{family}"
    return f"silver/listenbrainz/_partial/{namespace}/{family}"


def completion_scope(ckpt: dict) -> str:
    target = int(ckpt.get("map_target_shards") or 0)
    source = int(ckpt.get("source_shard_count") or 0)
    return "FULL_SOURCE" if target > 0 and target == source else "BOUNDED_TEST"


def artist_day_output_prefix(ckpt: dict) -> str:
    if completion_scope(ckpt) == "FULL_SOURCE":
        return (
            f"silver/listenbrainz/artist_day/dump={DUMP_VERSION}/"
            f"run={ckpt['run_namespace']}"
        )
    return f"silver/listenbrainz/_validation/{ckpt['run_namespace']}/artist_day"


def affinity_output_key(ckpt: dict) -> str:
    if completion_scope(ckpt) == "FULL_SOURCE":
        return (
            "silver/listenbrainz/audience_affinity_evidence/"
            f"dump={DUMP_VERSION}/run={ckpt['run_namespace']}/"
            "all_time/part0.parquet"
        )
    return (
        f"silver/listenbrainz/_validation/{ckpt['run_namespace']}"
        "/audience_affinity/all_time/part0.parquet"
    )


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return int(ordered[index])


def _current_rss_bytes() -> int:
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            check=True,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        ru_maxrss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return ru_maxrss if sys.platform == "darwin" else ru_maxrss * 1024


def record_resource_snapshot(
    ckpt: dict,
    *,
    phase: str,
    local_root: Path = LOCAL,
) -> None:
    """Record bounded process/disk telemetry without scanning the filesystem."""
    ru_maxrss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    peak_rss_bytes = ru_maxrss if sys.platform == "darwin" else ru_maxrss * 1024
    rss_bytes = _current_rss_bytes()
    metrics = ckpt.setdefault("resource_metrics", {})
    metrics["peak_rss_bytes"] = max(
        int(metrics.get("peak_rss_bytes") or 0), peak_rss_bytes, rss_bytes
    )
    probe = local_root if local_root.exists() else local_root.parent
    free_bytes = int(shutil.disk_usage(probe).free)
    if metrics.get("initial_free_disk_bytes") is None:
        metrics["initial_free_disk_bytes"] = free_bytes
    previous = metrics.get("minimum_free_disk_bytes")
    metrics["minimum_free_disk_bytes"] = (
        free_bytes if previous is None else min(int(previous), free_bytes)
    )
    metrics["peak_free_disk_loss_bytes"] = max(
        0,
        int(metrics["initial_free_disk_bytes"]) - int(metrics["minimum_free_disk_bytes"]),
    )
    samples = ckpt.setdefault("resource_samples", [])
    samples.append({
        "at": now_iso(),
        "phase": phase,
        "rss_bytes": rss_bytes,
        "free_disk_bytes": free_bytes,
    })
    if len(samples) > 2048:
        raise RuntimeError("resource sample bound exceeded")
    rss_values = [int(sample["rss_bytes"]) for sample in samples]
    metrics["rss_sample_count"] = len(rss_values)
    metrics["rss_p50_bytes"] = _percentile(rss_values, 0.50)
    metrics["rss_p95_bytes"] = _percentile(rss_values, 0.95)
    metrics["rss_max_bytes"] = max(rss_values)


def require_free_disk(*, path: Path = LOCAL.parent) -> None:
    free_bytes = int(shutil.disk_usage(path).free)
    if free_bytes < MIN_FREE_DISK_BYTES:
        raise RuntimeError(
            f"insufficient free disk: {free_bytes} bytes available, "
            f"{MIN_FREE_DISK_BYTES} required"
        )


def require_capacity_for_artifacts(
    artifacts: list[dict], *, path: Path = LOCAL.parent
) -> None:
    """Reserve input bytes plus the normal 8 GiB working-space floor."""
    input_bytes = sum(int(artifact["bytes"]) for artifact in artifacts)
    free_bytes = int(shutil.disk_usage(path).free)
    required = input_bytes + MIN_FREE_DISK_BYTES
    if free_bytes < required:
        raise RuntimeError(
            f"reducer inputs require {input_bytes} bytes plus the "
            f"{MIN_FREE_DISK_BYTES}-byte safety reserve; {free_bytes} available"
        )


def competing_heavy_jobs() -> list[str]:
    """Detect known local bulk jobs that must not share a Mac host.

    Cloud batch containers (CLOUD_JOB_R2) are single-tenant and often lack
    `ps`/procps; host contention checks do not apply there.
    """
    if CHECKPOINT_AUTHORITY == "CLOUD_JOB_R2":
        return []
    try:
        result = subprocess.run(
            ["ps", "-axo", "command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "unable to inspect process table for competing heavy jobs "
            "(ps unavailable); refuse to run on this host"
        ) from exc
    conflicts = [
        line.strip()
        for line in result.stdout.splitlines()
        if any(marker in line for marker in COMPETING_HEAVY_MARKERS)
    ]
    return sorted({
        marker for marker in COMPETING_HEAVY_MARKERS
        if any(marker in line for line in conflicts)
    })


def require_no_competing_heavy_job() -> None:
    """Fail closed when another known local bulk job is active."""
    markers = competing_heavy_jobs()
    if markers:
        raise RuntimeError(
            "competing heavy job is active; ListenBrainz is blocked: " + ", ".join(markers)
        )


def configure_duckdb(con) -> None:
    """Apply the same bounded resource contract to every pipeline phase."""
    SPILL.mkdir(parents=True, exist_ok=True)
    # 512 MiB cap keeps the aggregate inside RAM on the constrained Mac (the
    # batch=4 m-table is ~270-400 MB) and avoids OS swap pressure that eats
    # the disk floor and kills the process mid-batch.
    con.execute("PRAGMA memory_limit='512MB'")
    con.execute(f"SET temp_directory='{SPILL}'")
    con.execute("SET threads=2")


def cleanup_local_transients() -> None:
    """Remove only this pipeline's rebuildable, lock-protected scratch state."""
    allowed_prefixes = ("/tmp/lb_full_",)
    if CHECKPOINT_AUTHORITY == "CLOUD_JOB_R2" and _SCAN_ROOT is not None:
        # Cloud batch scratch lives under FI_SCRATCH_DIR / FI_LB_SCAN_ROOT.
        allowed_prefixes = (
            "/tmp/lb_full_",
            "/tmp/festival-bloomberg/",
            str(_SCAN_ROOT.resolve()) + os.sep,
        )
    for path in (LOCAL, SPILL):
        resolved = str(path.resolve())
        if not resolved.startswith(allowed_prefixes):
            raise RuntimeError(f"refusing unsafe transient cleanup target: {path}")
        shutil.rmtree(path, ignore_errors=True)


def save_checkpoint(s3, ckpt: dict) -> None:
    """Atomically save checkpoint, then refresh R2 (backup or cloud authority).

    LOCAL_HOST_ONLY: local file is authoritative; R2 host_backups/ is DR only.
    CLOUD_JOB_R2: R2 control/jobs/listenbrainz_tar_map/<job_id>/checkpoint.json
    is the durable resume authority (container ephemeral disk is not).
    """
    ckpt["updated_at"] = now_iso()
    ckpt["checkpoint_authority"] = CHECKPOINT_AUTHORITY
    ckpt["execution_host_fingerprint"] = HOST_FINGERPRINT
    if CLOUD_JOB_ID:
        ckpt["cloud_job_id"] = CLOUD_JOB_ID
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ckpt, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".checkpoint.", suffix=".json", dir=CHECKPOINT.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, CHECKPOINT)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    try:
        if CHECKPOINT_AUTHORITY == "CLOUD_JOB_R2" and CLOUD_CHECKPOINT_KEY:
            s3.put_object(
                Bucket=LAKE_BUCKET,
                Key=CLOUD_CHECKPOINT_KEY,
                Body=payload.encode(),
                ContentType="application/json",
            )
        else:
            s3.put_object(
                Bucket=LAKE_BUCKET,
                Key=(
                    "control/listenbrainz_full_scan/host_backups/"
                    f"{HOST_FINGERPRINT}.json"
                ),
                Body=payload.encode(),
            )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("checkpoint R2 copy failed; remote resume state is stale") from exc


def bind_source_object(s3, ckpt: dict) -> None:
    """Bind a run to the exact R2 object identity before reading any shard."""
    head = s3.head_object(Bucket=RAW_BUCKET, Key=RAW_KEY)
    byte_count = int(head["ContentLength"])
    etag = str(head.get("ETag") or "").strip('"')
    if byte_count != TOTAL_SOURCE_BYTES:
        raise RuntimeError(
            f"raw source size changed: expected {TOTAL_SOURCE_BYTES}, observed {byte_count}"
        )
    saved_etag = ckpt.get("source_etag")
    if saved_etag and saved_etag != etag:
        raise RuntimeError("raw source ETag changed since the checkpoint was created")
    if not etag:
        raise RuntimeError("raw source has no ETag; exact source identity is unavailable")
    last_modified_raw = head.get("LastModified")
    last_modified = (
        last_modified_raw.isoformat()
        if hasattr(last_modified_raw, "isoformat")
        else (str(last_modified_raw) if last_modified_raw else None)
    )
    saved_last_modified = ckpt.get("source_object_last_modified")
    if saved_last_modified and last_modified != saved_last_modified:
        raise RuntimeError("raw source LastModified changed since checkpoint creation")
    ckpt["source_etag"] = etag
    ckpt["source_object_last_modified"] = last_modified


def require_private_storage(s3) -> None:
    """Require the unbound private R2 bucket before any listener-level work."""
    try:
        s3.head_bucket(Bucket=PRIVATE_BUCKET)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"private listener bucket {PRIVATE_BUCKET!r} is unavailable"
        ) from exc


def build_tar_index(s3) -> list[dict]:
    """Reuse cached index or walk tar headers via 512-byte range GETs (resumable)."""
    if INDEX_CACHE.exists():
        return json.loads(INDEX_CACHE.read_text())
    raise RuntimeError("tar index missing; run scripts/lb_pilot.py or lb_format_inventory first")


def data_shards(members: list[dict]) -> list[dict]:
    return [m for m in members
            if (m["name"].split("/")[-1].endswith(".parquet")
                and m["name"].split("/")[-1][:-8].isdigit())]


def validate_tar_index(members: list[dict]) -> list[dict]:
    """Validate cached tar range geometry and return stable shard-id order."""
    if not isinstance(members, list) or not members:
        raise RuntimeError("tar index must be a non-empty list")
    if any(
        not isinstance(member, dict)
        or not isinstance(member.get("name"), str)
        or type(member.get("offset")) is not int
        or type(member.get("size")) is not int
        for member in members
    ):
        raise RuntimeError("tar index member has invalid name/offset/size")
    shards = data_shards(members)
    if not shards:
        raise RuntimeError("tar index contains no numbered parquet shards")
    seen_names: set[str] = set()
    seen_ids: set[int] = set()
    ranges = []
    for member in shards:
        name = member.get("name")
        offset = member.get("offset")
        size = member.get("size")
        shard_id = int(name.split("/")[-1][:-8])
        if name in seen_names or shard_id in seen_ids:
            raise RuntimeError("tar index contains duplicate shard identity")
        if offset < 0 or size <= 0 or offset % 512 != 0:
            raise RuntimeError("tar index contains invalid tar range geometry")
        if offset + size > TOTAL_SOURCE_BYTES:
            raise RuntimeError("tar index shard range exceeds the source object")
        seen_names.add(name)
        seen_ids.add(shard_id)
        ranges.append((offset, offset + size, name))
    expected_ids = set(range(len(shards)))
    if seen_ids != expected_ids:
        raise RuntimeError("tar index numbered shard sequence is not contiguous")
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if previous[1] > current[0]:
            raise RuntimeError("tar index shard ranges overlap")
    return sorted(
        shards, key=lambda member: int(member["name"].split("/")[-1][:-8])
    )


def load_universe() -> dict[str, dict]:
    """mbid -> {key, tier} for the 25K universe (from estate JSON)."""
    data = json.loads(ESTATE_JSON.read_text())
    out = {}
    for a in data.get("artists", []):
        mbid = a.get("mbid")
        if mbid:
            out[mbid] = {"key": a.get("key"), "tier": a.get("tier")}
    return out


def resolve_credit(credit, universe: dict[str, dict]) -> list[dict]:
    """Return every distinct canonical artist represented in one credit."""
    hits = {}
    for mbid in credit or []:
        hit = universe.get(mbid)
        if hit and hit.get("key"):
            hits[hit["key"]] = hit
    return [hits[key] for key in sorted(hits)]


def fetch_shard(s3, m: dict, local: Path) -> int:
    """Stream one tar-member range to disk; return the retry count."""
    end = m["offset"] + m["size"] - 1
    attempts = 0
    while True:
        try:
            resp = s3.get_object(Bucket=RAW_BUCKET, Key=RAW_KEY,
                                 Range=f"bytes={m['offset']}-{end}")
            local.unlink(missing_ok=True)
            with local.open("wb") as handle:
                shutil.copyfileobj(resp["Body"], handle, length=8 * 1024 * 1024)
            if local.stat().st_size != int(m["size"]):
                raise RuntimeError("source shard range size mismatch")
            return attempts
        except Exception as e:  # noqa: BLE001
            local.unlink(missing_ok=True)
            attempts += 1
            if attempts > 8:
                raise
            print(f"  fetch retry {attempts} ({e.__class__.__name__})", flush=True)
            time.sleep(min(60, 5 * attempts))


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bind_local_input_digest(ckpt: dict, field: str, path: Path) -> str:
    """Bind resumable work to one exact local control-plane input."""
    digest = _sha256_path(path)
    saved = ckpt.get(field)
    if saved and saved != digest:
        raise RuntimeError(f"{field} changed since the checkpoint was created")
    ckpt[field] = digest
    return digest


def upload(s3, local: Path, key: str) -> dict:
    """Stream a local artifact to R2, verify its size, then remove the local copy."""
    bucket = PRIVATE_BUCKET if key.startswith(PRIVATE_PARTIAL_ROOT + "/") else LAKE_BUCKET
    size = local.stat().st_size
    digest = _sha256_path(local)
    with local.open("rb") as handle:
        s3.upload_fileobj(
            handle,
            bucket,
            key,
            ExtraArgs={"Metadata": {"sha256": digest}},
        )
    head = s3.head_object(Bucket=bucket, Key=key)
    sz = head["ContentLength"]
    if sz != size:
        raise RuntimeError(f"upload size mismatch for {key}")
    remote_digest = (head.get("Metadata") or {}).get("sha256")
    if remote_digest != digest:
        raise RuntimeError(f"upload SHA-256 metadata mismatch for {key}")
    local.unlink()
    return {"bucket": bucket, "key": key, "bytes": size, "sha256": digest}


def download(
    s3,
    key: str,
    local: Path,
    expected_artifact: dict | None = None,
    *,
    private_access: str | None = None,
) -> int:
    """Stream an R2 object and verify it against its committed manifest."""
    if key.startswith(PRIVATE_PARTIAL_ROOT + "/") and private_access != PRIVATE_REDUCER_ACCESS:
        raise PermissionError("listener-level artifact requires reducer-only access")
    if expected_artifact is not None and expected_artifact.get("key") != key:
        raise RuntimeError(f"artifact manifest key mismatch for {key}")
    bucket = (
        expected_artifact.get("bucket", LAKE_BUCKET)
        if expected_artifact is not None else LAKE_BUCKET
    )
    if key.startswith(PRIVATE_PARTIAL_ROOT + "/") and bucket != PRIVATE_BUCKET:
        raise PermissionError("listener-level artifact must reside in the private bucket")
    response = s3.get_object(Bucket=bucket, Key=key)
    expected = int(response.get("ContentLength") or 0)
    if expected_artifact is not None:
        if expected != int(expected_artifact["bytes"]):
            raise RuntimeError(f"artifact manifest size mismatch for {key}")
        remote_digest = (response.get("Metadata") or {}).get("sha256")
        if remote_digest != expected_artifact["sha256"]:
            raise RuntimeError(f"artifact manifest hash metadata mismatch for {key}")
    local.unlink(missing_ok=True)
    try:
        with local.open("wb") as handle:
            shutil.copyfileobj(response["Body"], handle, length=8 * 1024 * 1024)
    except Exception:
        local.unlink(missing_ok=True)
        raise
    observed = local.stat().st_size
    if expected and observed != expected:
        local.unlink(missing_ok=True)
        raise RuntimeError(f"download size mismatch for {key}")
    if expected_artifact is not None:
        digest = _sha256_path(local)
        if digest != expected_artifact["sha256"]:
            local.unlink(missing_ok=True)
            raise RuntimeError(f"download SHA-256 mismatch for {key}")
    return observed


def record_upload(ckpt: dict, artifact: dict) -> dict:
    ckpt["r2_output_objects"] = int(ckpt.get("r2_output_objects") or 0) + 1
    ckpt["r2_output_bytes"] = (
        int(ckpt.get("r2_output_bytes") or 0) + int(artifact["bytes"])
    )
    return artifact


def verify_artifact_manifest(s3, artifacts: list[dict]) -> None:
    """Verify object metadata; private HEAD access never reads listener content."""
    for artifact in artifacts:
        bucket = artifact.get("bucket", LAKE_BUCKET)
        if artifact["key"].startswith(PRIVATE_PARTIAL_ROOT + "/"):
            if bucket != PRIVATE_BUCKET:
                raise PermissionError("listener artifact manifest names a non-private bucket")
        head = s3.head_object(Bucket=bucket, Key=artifact["key"])
        if int(head["ContentLength"]) != int(artifact["bytes"]):
            raise RuntimeError(f"artifact size changed for {artifact['key']}")
        digest = (head.get("Metadata") or {}).get("sha256")
        if digest != artifact["sha256"]:
            raise RuntimeError(f"artifact hash metadata changed for {artifact['key']}")


def artifact_manifest_sha256(artifacts: list[dict]) -> str:
    payload = json.dumps(
        sorted(artifacts, key=lambda artifact: artifact["key"]),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def committed_map_artifacts(s3, ckpt: dict, family: str) -> list[dict]:
    """Return only exact, verified artifacts from committed map batches."""
    ensure_map_complete(ckpt)
    target = int(ckpt["map_target_shards"])
    expected_ranges = [
        [start, min(start + BATCH_SHARDS, target) - 1]
        for start in range(0, target, BATCH_SHARDS)
    ]
    observed_ranges = ckpt.get("completed_batches") or []
    if observed_ranges != expected_ranges:
        raise RuntimeError(
            "completed batch ranges do not match the exact map geometry"
        )
    manifests = ckpt.get("batch_artifacts") or {}
    expected_starts = {str(start) for start in range(0, target, BATCH_SHARDS)}
    if set(manifests) != expected_starts:
        raise RuntimeError("batch artifact manifests do not match the map geometry")
    prefix = partial_prefix(ckpt, family) + "/"
    selected = []
    for start in range(0, target, BATCH_SHARDS):
        artifacts = manifests[str(start)]
        if not artifacts:
            raise RuntimeError(f"committed batch {start} has no artifacts")
        verify_artifact_manifest(s3, artifacts)
        family_artifacts = [a for a in artifacts if a["key"].startswith(prefix)]
        if family == "artist_day" and len(family_artifacts) != 1:
            raise RuntimeError(
                f"committed batch {start} must have exactly one artist-day artifact"
            )
        selected.extend(family_artifacts)
    keys = [artifact["key"] for artifact in selected]
    if len(keys) != len(set(keys)):
        raise RuntimeError(f"duplicate committed {family} artifact key")
    return sorted(selected, key=lambda artifact: artifact["key"])


def committed_listener_artifacts(s3, ckpt: dict) -> list[dict]:
    """Verify explicit present/empty coverage for every batch and partition."""
    artifacts = committed_map_artifacts(s3, ckpt, "listener_artist")
    target = int(ckpt["map_target_shards"])
    partitions = int(ckpt.get("listener_hash_partitions") or 0)
    if partitions <= 0:
        raise RuntimeError("listener partition count is invalid")
    expected_starts = {str(start) for start in range(0, target, BATCH_SHARDS)}
    coverage = ckpt.get("batch_partition_coverage") or {}
    if set(coverage) != expected_starts:
        raise RuntimeError("batch partition coverage does not match map geometry")
    actual: dict[str, set[str]] = {start: set() for start in expected_starts}
    for artifact in artifacts:
        key = artifact["key"]
        part = key.split("/part=", 1)[1].split("/", 1)[0]
        batch = key.rsplit("/batch_", 1)[1].removesuffix(".parquet")
        if batch not in actual or part in actual[batch]:
            raise RuntimeError("listener artifact batch/partition identity is invalid")
        actual[batch].add(part)
    expected_part_keys = {str(part) for part in range(partitions)}
    for start in expected_starts:
        marker = coverage[start]
        if not isinstance(marker, dict) or set(marker) != expected_part_keys:
            raise RuntimeError(f"batch {start} partition coverage is incomplete")
        if any(type(value) is not bool for value in marker.values()):
            raise RuntimeError(f"batch {start} partition coverage is malformed")
        present = {part for part, value in marker.items() if value}
        if present != actual[start]:
            raise RuntimeError(
                f"batch {start} partition artifacts disagree with coverage markers"
            )
    return artifacts


def record_reducer_download(ckpt: dict, byte_count: int) -> None:
    ckpt["r2_reducer_input_bytes"] = (
        int(ckpt.get("r2_reducer_input_bytes") or 0) + int(byte_count)
    )


def _r2_list(s3, prefix: str):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=LAKE_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def materialize_artist_day_global(con) -> None:
    """Merge additive artist/day partials without inventing global uniques.

    A listener or recording can occur in more than one source batch. Summing
    batch-level distinct counts would therefore overstate global distincts.
    Preserve those sums as diagnostics and leave the global unique fields NULL
    until an exact mergeable distinct-state implementation exists.
    """
    con.execute("DROP TABLE IF EXISTS ad_global")
    con.execute("""
        CREATE TEMP TABLE ad_global AS
        SELECT artist_key, obs_day,
               SUM(listen_count)::BIGINT AS listen_count,
               NULL::BIGINT AS unique_listeners,
               NULL::BIGINT AS unique_recordings,
               SUM(unique_listeners)::BIGINT AS batch_unique_listener_sum,
               SUM(unique_recordings)::BIGINT AS batch_unique_recording_sum,
               'UNKNOWN_ACROSS_BATCHES'::VARCHAR AS distinct_count_status
        FROM ad
        GROUP BY 1, 2
    """)


def materialize_affinity_partition(con, *, top_k: int = TOP_K) -> None:
    """Globally aggregate listener/artist fragments, then rank one artist set."""
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    con.execute("DROP TABLE IF EXISTS la_global")
    con.execute("DROP TABLE IF EXISTS bounded")
    con.execute("""
        CREATE TEMP TABLE la_global AS
        SELECT listener_key, artist_key, SUM(listen_count)::BIGINT AS listen_count
        FROM la
        GROUP BY 1, 2
    """)
    con.execute(f"""
        CREATE TEMP TABLE bounded AS
        SELECT listener_key, artist_key, listen_count
        FROM (
            SELECT listener_key, artist_key, listen_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY listener_key
                       ORDER BY listen_count DESC, artist_key
                   ) AS rn
            FROM la_global
        )
        WHERE rn <= {top_k}
    """)


def materialize_global_affinity(con, *, minimum_shared: int = MIN_SHARED_LISTENERS) -> None:
    """Global cross-partition union with metric-universe metadata.

    Runs after every per-listener partition's top-K + pair materialization is
    committed, so shared_listeners is the true global count per pair, never a
    per-shard top-K artifact.

    P11: Metric-universe metadata.
    All metrics (Jaccard, cosine, lift, PMI) are computed over the
    TOP_25_RETAINED_PER_LISTENER universe, NOT the full observed population.
    Never label these as TOTAL_FANS or total artist audience.
    Never infer ticket_demand, purchase_propensity, attendance, or
    willingness_to_pay.

    Support tiers (LOW/MEDIUM/HIGH) are NOT yet materialized — they will be
    derived from the actual corpus distribution after the global reduce
    produces an observed support distribution. Do not invent arbitrary
    thresholds before seeing the real distribution.

    GATE REGISTER — do not claim A3/A5 completion here.
    gen = 0014df7
    pipeline_version = 3
    stage = MAP_COMPLETE_NEEDED
    reduce = NOT_STARTED_LOCAL
    cloud_batch = NOT_EXECUTED
    """
    if minimum_shared <= 0:
        raise ValueError("minimum_shared must be positive")
    con.execute("DROP TABLE IF EXISTS pairs")
    con.execute("DROP TABLE IF EXISTS nodes")
    con.execute("DROP TABLE IF EXISTS population")
    con.execute(f"""
        CREATE TEMP TABLE pairs AS
        SELECT a AS artist_key_a, b AS artist_key_b,
               SUM(sh)::BIGINT AS shared_listeners
        FROM pair_input
        GROUP BY 1, 2
        HAVING SUM(sh) >= {minimum_shared}
    """)
    con.execute("""
        CREATE TEMP TABLE nodes AS
        SELECT a AS artist_key, SUM(listeners)::BIGINT AS listeners
        FROM node_input
        GROUP BY 1
    """)
    con.execute("""
        CREATE TEMP TABLE population AS
        SELECT SUM(listener_count)::BIGINT AS listener_count
        FROM population_input
    """)


# P11: Metric-universe metadata for Gold affinity outputs.
# These labels ensure nobody reads TOP_25-retained metrics as full-population
# fan overlap. Never call these "TOTAL FANS" or total artist audience.
AFFINITY_METRIC_UNIVERSE = {
    "audience_source": "LISTENBRAINZ",
    "audience_semantics": "OBSERVED_LISTENBRAINZ_AUDIENCE_SAMPLE",
    "listener_universe": "TOP_25_RETAINED_PER_LISTENER",
    "top_k": TOP_K,
    "shared_listener_semantics": "GLOBAL_UNIQUE_LISTENERS_WITHIN_METRIC_UNIVERSE",
    "jaccard_universe": "TOP_25_RETAINED",
    "cosine_universe": "TOP_25_RETAINED",
    "lift_universe": "TOP_25_RETAINED",
    "pmi_universe": "TOP_25_RETAINED",
    "never_label_as": "TOTAL_FANS",
    "never_infer": [
        "ticket_demand", "purchase_propensity",
        "attendance", "willingness_to_pay",
    ],
    "support_tiers": "DEFERRED — derive from corpus distribution after global reduce",
}


def cmd_map(args) -> None:
    require_free_disk()
    require_no_competing_heavy_job()
    s3 = r2_client()
    require_private_storage(s3)
    ckpt = load_checkpoint()
    if args.partitions <= 0:
        raise ValueError("--partitions must be positive")
    if args.max_shards <= 0:
        raise ValueError("--max-shards must be positive")
    validate_checkpoint(ckpt, partitions=args.partitions)
    bind_source_object(s3, ckpt)
    ckpt["pipeline_version"] = PIPELINE_VERSION
    ckpt["listener_hash_partitions"] = args.partitions
    ckpt["batch_size_shards"] = BATCH_SHARDS
    ckpt["dump_version"] = DUMP_VERSION
    ckpt["source_key"] = RAW_KEY
    ckpt["source_bytes"] = TOTAL_SOURCE_BYTES
    members = build_tar_index(s3)
    bind_local_input_digest(ckpt, "tar_index_sha256", INDEX_CACHE)
    bind_local_input_digest(ckpt, "artist_universe_sha256", ESTATE_JSON)
    ckpt["duckdb_version"] = duckdb.__version__
    ckpt["listener_partition_algorithm"] = "DUCKDB_HASH_V1"
    ckpt["listener_level_access"] = "QUARANTINED_NOT_SERVING"
    ckpt["active_run_lock"] = args.run_lock
    shards = validate_tar_index(members)
    total_shards = len(shards)
    if ckpt.get("source_shard_count") not in (0, total_shards):
        raise RuntimeError("checkpoint source shard count does not match the tar index")
    ckpt["source_shard_count"] = total_shards
    ckpt["started_at"] = ckpt.get("started_at") or now_iso()

    shard_start = int(getattr(args, "shard_start", 0) or 0)
    if shard_start < 0:
        raise ValueError("--shard-start must be >= 0")
    if shard_start % BATCH_SHARDS != 0:
        raise ValueError(
            f"--shard-start must be aligned to BATCH_SHARDS={BATCH_SHARDS}"
        )
    slice_count = int(args.max_shards)
    if slice_count <= 0:
        raise ValueError("--max-shards must be positive")
    # Parallel workers share one full-corpus namespace via --map-target-shards.
    map_target = int(getattr(args, "map_target_shards", None) or slice_count)
    map_target = min(map_target, total_shards)
    shard_end = min(shard_start + slice_count, map_target, total_shards)
    if shard_end <= shard_start:
        raise ValueError(
            f"empty shard slice: start={shard_start} end={shard_end} target={map_target}"
        )

    prior_target = ckpt.get("map_target_shards")
    has_committed_map = bool(ckpt.get("completed_batches") or ckpt.get("completed_shards"))
    if has_committed_map and int(prior_target or 0) != map_target:
        raise RuntimeError(
            f"checkpoint target is {prior_target}, command requests {map_target}; "
            "use a fresh checkpoint and namespace for a different target"
        )
    ckpt["map_target_shards"] = map_target
    ckpt["shard_slice_start"] = shard_start
    ckpt["shard_slice_end"] = shard_end
    ckpt["run_namespace"] = scan_namespace(
        args.partitions,
        map_target,
        tar_index_sha256=ckpt["tar_index_sha256"],
        artist_universe_sha256=ckpt["artist_universe_sha256"],
    )

    SPILL.mkdir(parents=True, exist_ok=True)
    LOCAL.mkdir(parents=True, exist_ok=True)

    # DuckDB handles the aggregation for a batch; spill keeps RAM bounded.
    con = duckdb.connect()
    configure_duckdb(con)

    universe = load_universe()
    print(f"universe MBIDs: {len(universe):,}   total shards: {total_shards:,}")

    # Completed batches are the ONLY resume authority. A batch is atomic: it is
    # marked complete together with its counters only after ALL its partials are
    # uploaded. A crash mid-batch leaves the batch uncommitted -> redone on resume.
    done_batches = set()
    for rng in ckpt.get("completed_batches", []):
        for i in range(rng[0], rng[1] + 1):
            done_batches.add(i)
    pending_shards = set(range(shard_start, shard_end)) - done_batches
    reductions_exist = bool(
        ckpt.get("completed_artist_day")
        or ckpt.get("completed_affinity_partitions")
        or ckpt.get("completed_pairs")
    )
    if pending_shards and reductions_exist:
        raise RuntimeError(
            "map cannot extend after a reducer committed outputs; use a fresh checkpoint "
            "and run_namespace so reductions cannot silently omit new batches"
        )
    artists_seen: set[str] = set(ckpt.get("artists_seen", []) or [])

    # Commit counters + shard set ONLY at batch boundaries (not per shard), so a
    # mid-batch crash cannot leave the checkpoint inconsistent with R2 partials.
    t_start = time.time()
    print(
        f"map slice [{shard_start}..{shard_end}) of target {map_target} "
        f"({len(pending_shards)} pending / {shard_end - shard_start} in slice)",
        flush=True,
    )
    idx = shard_start
    while idx < shard_end:
        batch_last = min(idx + BATCH_SHARDS, shard_end)
        if all(i in done_batches for i in range(idx, batch_last)):
            artifacts = (ckpt.get("batch_artifacts") or {}).get(str(idx))
            if not artifacts:
                raise RuntimeError(
                    f"completed batch {idx} has no artifact manifest; resume is unsafe"
                )
            verify_artifact_manifest(s3, artifacts)
            print(f"[resume] batch [{idx}..{batch_last - 1}] already complete — skipped")
            idx = batch_last
            continue
        print(f"\n=== batch [{idx}..{batch_last - 1}] ===", flush=True)
        bt = time.time()
        # staged counters for THIS batch (apply to ckpt only on commit)
        b_bytes = 0
        b_matched = 0
        b_unres = 0
        b_nocr = 0
        b_retries = 0
        b_artifacts = []
        b_source_access_at = now_iso()
        # build matched-row DuckDB table for this batch
        con.execute("DROP TABLE IF EXISTS m")
        con.execute("CREATE TABLE m (artist_key VARCHAR, listener_key BIGINT, "
                    "listened_at TIMESTAMP, recording_mbid VARCHAR)")
        for i in range(idx, batch_last):
            m = shards[i]
            raw_path = LOCAL / f"source_shard_{i}.parquet"
            b_retries += fetch_shard(s3, m, raw_path)
            b_bytes += m["size"]
            pf = pq.ParquetFile(raw_path)
            for b in pf.iter_batches(batch_size=75_000):
                ambs = b.column("artist_credit_mbids").to_pylist()
                uids = b.column("user_id").to_pylist()
                la = b.column("listened_at").to_pylist()
                rm = b.column("recording_mbid").to_pylist()
                k, u, l, r = [], [], [], []
                for j in range(b.num_rows):
                    credit = ambs[j]
                    if not credit:
                        b_nocr += 1
                        continue
                    hits = resolve_credit(credit, universe)
                    if not hits:
                        b_unres += 1
                        continue
                    b_matched += 1
                    for hit in hits:
                        k.append(hit["key"])
                        u.append(int(uids[j]))
                        l.append(la[j])
                        r.append(rm[j])
                if k:
                    t = pa.table({"artist_key": k, "listener_key": u,
                                  "listened_at": l, "recording_mbid": r})
                    artists_seen.update(k)
                    con.register("t_ins", t)
                    con.execute("INSERT INTO m SELECT * FROM t_ins")
                    con.unregister("t_ins")
            raw_path.unlink(missing_ok=True)
            require_free_disk()
            print(f"    shard {i}: {m['name'].split('/')[-1]} {m['size']//1048576} MiB "
                  f"this-batch retained={b_matched:,}", flush=True)

        # ---- partial: artist_day ----
        ad_local = LOCAL / f"artist_day_batch_{idx}.parquet"
        ad_local.unlink(missing_ok=True)
        con.execute(f"""
            COPY (
                SELECT artist_key, CAST(listened_at AS DATE) AS obs_day,
                       COUNT(*) AS listen_count,
                       COUNT(DISTINCT listener_key) AS unique_listeners,
                       COUNT(DISTINCT recording_mbid) AS unique_recordings
                FROM m GROUP BY 1, 2
                ORDER BY 1, 2
            ) TO '{ad_local}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        b_artifacts.append(record_upload(
            ckpt,
            upload(s3, ad_local, f"{partial_prefix(ckpt, 'artist_day')}/batch_{idx}.parquet"),
        ))

        # ---- partial: listener_artist, hash-partitioned by listener ----
        parts_dir = LOCAL / "la_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        for f in parts_dir.glob("*.parquet"):
            f.unlink()
        con.execute(f"""
            COPY (
                SELECT abs(hash(CAST(listener_key AS VARCHAR))) % {args.partitions} AS part,
                       CAST(listener_key AS VARCHAR) AS listener_key, artist_key,
                       COUNT(*) AS listen_count
                FROM m GROUP BY 1, 2, 3
            ) TO '{parts_dir}/part.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        df_parts = con.execute(
            f"SELECT DISTINCT part FROM read_parquet('{parts_dir}/part.parquet') "
            "ORDER BY part").fetchall()
        part_keys = [r[0] for r in df_parts]
        present_parts = set(part_keys)
        batch_partition_coverage = {
            str(part): part in present_parts for part in range(args.partitions)
        }
        for p in part_keys:
            p_local = parts_dir / f"p{p}.parquet"
            con.execute(f"""
                COPY (
                  SELECT CAST(listener_key AS VARCHAR) AS listener_key, artist_key,
                         listen_count FROM read_parquet('{parts_dir}/part.parquet')
                  WHERE part = {p}
                  ORDER BY 1, 2
                ) TO '{p_local}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            b_artifacts.append(record_upload(
                ckpt,
                upload(
                    s3,
                    p_local,
                    f"{partial_prefix(ckpt, 'listener_artist')}/part={p}/batch_{idx}.parquet",
                ),
            ))
        (parts_dir / "part.parquet").unlink(missing_ok=True)
        con.execute("DROP TABLE m")

        if args.interrupt_after_batch == idx:
            raise RuntimeError(
                f"intentional interruption after uploading batch {idx} and before checkpoint commit"
            )

        # ---- commit batch (counters + shards + list all at once) ----
        ckpt["bytes_read"] += b_bytes
        ckpt["matched_listens"] += b_matched
        ckpt["unresolved"] += b_unres
        ckpt["no_credit"] += b_nocr
        ckpt["fetch_retries"] = int(ckpt.get("fetch_retries") or 0) + b_retries
        ckpt["source_first_access_at"] = (
            ckpt.get("source_first_access_at") or b_source_access_at
        )
        ckpt["source_last_access_at"] = now_iso()
        ckpt["listens_scanned"] += (b_matched + b_unres + b_nocr)
        ckpt["completed_shards"] = sorted(
            set(ckpt.get("completed_shards", [])) | set(range(idx, batch_last)))
        ckpt["completed_batches"].append([idx, batch_last - 1])
        ckpt.setdefault("batch_artifacts", {})[str(idx)] = sorted(
            b_artifacts, key=lambda item: item["key"]
        )
        ckpt.setdefault("batch_partition_coverage", {})[str(idx)] = (
            batch_partition_coverage
        )
        ckpt["artists_seen"] = sorted(artists_seen)
        record_resource_snapshot(ckpt, phase="map_batch_commit")
        save_checkpoint(s3, ckpt)
        for i in range(idx, batch_last):
            done_batches.add(i)
        bt_s = time.time() - bt
        mbps = b_bytes / 1e6 / max(0.001, bt_s)
        print(f"  batch done in {bt_s:.0f}s @ {mbps:.1f} MB/s reads; "
              f"matched total {ckpt['matched_listens']:,}; "
              f"{len(ckpt['completed_batches'])} batches", flush=True)
        idx = batch_last

    con.close()
    ckpt["runtime_seconds"] += time.time() - t_start
    record_resource_snapshot(ckpt, phase="map_complete")
    save_checkpoint(s3, ckpt)
    n_batches = len(ckpt["completed_batches"])
    proj_h = (TOTAL_SOURCE_BYTES / max(1, ckpt["bytes_read"])) * ckpt["runtime_seconds"] / 3600
    print(f"\n=== MAP done: {n_batches} batches, {len(ckpt['completed_shards'])} shards ===")
    print(f"bytes_read {ckpt['bytes_read']/1e9:.1f} GB  matched {ckpt['matched_listens']:,} "
          f"unresolved {ckpt['unresolved']:,} no_credit {ckpt['no_credit']:,}")
    print(f"projected full-scan time at this rate: {proj_h:.2f} h")


def cmd_reduce_artist_day(args) -> None:
    require_free_disk()
    require_no_competing_heavy_job()
    s3 = r2_client()
    require_private_storage(s3)
    ckpt = load_checkpoint()
    validate_checkpoint(ckpt)
    ensure_map_complete(ckpt)
    ckpt["active_run_lock"] = args.run_lock
    if ckpt.get("completed_artist_day"):
        artifacts = ckpt.get("artist_day_artifacts") or []
        if not artifacts:
            raise RuntimeError("completed artist-day reduction has no output manifest")
        verify_artifact_manifest(s3, artifacts)
        print("artist_day already reduced.")
        return
    LOCAL.mkdir(parents=True, exist_ok=True)
    input_artifacts = committed_map_artifacts(s3, ckpt, "artist_day")
    print(f"artist_day partials: {len(input_artifacts)}")
    if not input_artifacts:
        raise RuntimeError("map is complete but no artist_day partials were found")
    require_capacity_for_artifacts(input_artifacts)
    local_files = []
    for i, artifact in enumerate(input_artifacts):
        d = LOCAL / f"ad_{i}.parquet"
        record_reducer_download(
            ckpt, download(s3, artifact["key"], d, artifact)
        )
        local_files.append(str(d))
    require_free_disk()
    record_resource_snapshot(ckpt, phase="artist_day_inputs_local")
    con = duckdb.connect()
    configure_duckdb(con)
    con.execute("CREATE TABLE ad AS "
                "SELECT * FROM read_parquet([{}])".format(", ".join(f"'{p}'" for p in local_files)))
    materialize_artist_day_global(con)
    require_free_disk()
    # partition by year/month
    out_root = Path("/tmp/lb_full_local_ad")
    out_root.mkdir(exist_ok=True)
    output_prefix = artist_day_output_prefix(ckpt)
    output_artifacts = []
    periods = con.execute(
        "SELECT strftime(obs_day, '%Y/%m') AS ym FROM ad_global GROUP BY 1 ORDER BY 1").fetchall()
    for (ym,) in periods:
        yyyy, mm = ym.split("/")
        o = out_root / f"year={yyyy}/month={mm}/part0.parquet"
        o.parent.mkdir(parents=True, exist_ok=True)
        o.unlink(missing_ok=True)
        con.execute(f"""
            COPY (
              SELECT artist_key, obs_day, listen_count,
                     unique_listeners, unique_recordings,
                     batch_unique_listener_sum, batch_unique_recording_sum,
                     distinct_count_status
              FROM ad_global WHERE strftime(obs_day, '%Y/%m') = '{ym}'
              ORDER BY artist_key, obs_day
            ) TO '{o}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        output_artifacts.append(record_upload(
            ckpt,
            upload(s3, o, f"{output_prefix}/year={yyyy}/month={mm}/part.parquet"),
        ))
    total = con.execute("SELECT COUNT(*) FROM ad_global").fetchone()[0]
    con.close()
    for f in local_files:
        Path(f).unlink(missing_ok=True)
    scope = completion_scope(ckpt)
    register_dataset(
        dataset_id=(
            "silver.listenbrainz_artist_day"
            if scope == "FULL_SOURCE"
            else "silver.listenbrainz_artist_day_validation"
        ),
        dataset_version=(DUMP_VERSION if scope == "FULL_SOURCE" else ckpt["run_namespace"]),
        layer="SILVER",
        source="listenbrainz",
        source_version=DUMP_VERSION,
        r2_bucket=LAKE_BUCKET,
        r2_prefix=output_prefix,
        fmt="parquet",
        schema_version="silver-listenbrainz-artist-day-v2",
        row_count=total,
        byte_count=sum(int(artifact["bytes"]) for artifact in output_artifacts),
        source_checksum=ckpt.get("source_etag"),
        artifact_checksum=artifact_manifest_sha256(output_artifacts),
        verification_status=(
            "BUILD_COMPLETE" if scope == "FULL_SOURCE" else "BOUNDED_TEST_COMPLETE"
        ),
        license="CC0-1.0",
        rights_status="OPEN_DATA_AGGREGATED",
        commercial_use_status="ALLOWED",
        serving_eligible=False,
        access_classification="INTERNAL",
        upstream_dataset_ids=[SOURCE_DATASET],
        notes="Global unique listener/recording counts remain unknown across batches.",
    )
    ckpt["completed_artist_day"] = True
    ckpt["artist_day_cells"] = total
    ckpt["artist_day_distinct_count_status"] = "UNKNOWN_ACROSS_BATCHES"
    ckpt["artist_day_artifacts"] = sorted(output_artifacts, key=lambda item: item["key"])
    ckpt["map_completion_scope"] = scope
    record_resource_snapshot(ckpt, phase="artist_day_complete")
    save_checkpoint(s3, ckpt)
    print(f"artist_day reduced: {total:,} cells")


def cmd_reduce_affinity(args) -> None:
    """Global per-listener aggregrate within each listener partition + top-K + pairs."""
    require_free_disk()
    require_no_competing_heavy_job()
    s3 = r2_client()
    require_private_storage(s3)
    ckpt = load_checkpoint()
    if args.partitions <= 0:
        raise ValueError("--partitions must be positive")
    validate_checkpoint(ckpt, partitions=args.partitions)
    ensure_map_complete(ckpt)
    ckpt["active_run_lock"] = args.run_lock
    LOCAL.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    configure_duckdb(con)
    # The committed batch manifests, not an R2 prefix listing, are the input
    # authority. Globally empty hash partitions are valid and need no object.
    listener_artifacts = committed_listener_artifacts(s3, ckpt)
    listener_prefix = partial_prefix(ckpt, "listener_artist")
    artifacts_by_part: dict[str, list[dict]] = {}
    for artifact in listener_artifacts:
        relative = artifact["key"][len(listener_prefix) + 1:]
        segments = relative.split("/")
        if len(segments) != 2 or not segments[0].startswith("part="):
            raise RuntimeError(f"invalid listener artifact key {artifact['key']}")
        part = segments[0].split("=", 1)[1]
        batch_name = segments[1]
        batch_value = batch_name.removeprefix("batch_").removesuffix(".parquet")
        if (
            not part.isdigit()
            or not batch_name.endswith(".parquet")
            or not batch_value.isdigit()
            or int(batch_value) % BATCH_SHARDS != 0
        ):
            raise RuntimeError(f"invalid listener artifact key {artifact['key']}")
        if int(part) >= args.partitions:
            raise RuntimeError(f"listener partition out of range in {artifact['key']}")
        artifacts_by_part.setdefault(part, []).append(artifact)
    parts = sorted(artifacts_by_part, key=int)
    print(f"listener partitions seen: {len(parts)}")
    if not parts:
        raise RuntimeError("map produced no listener-level affinity inputs")
    ckpt["affinity_expected_partitions"] = parts
    done_parts = set(ckpt.get("completed_affinity_partitions", []))
    for part in parts:
        if part in done_parts:
            artifacts = (ckpt.get("affinity_partition_artifacts") or {}).get(part)
            if not artifacts:
                raise RuntimeError(
                    f"completed affinity partition {part} has no artifact manifest"
                )
            verify_artifact_manifest(s3, artifacts)
            continue
        input_artifacts = sorted(
            artifacts_by_part[part], key=lambda artifact: artifact["key"]
        )
        require_capacity_for_artifacts(input_artifacts)
        print(f"  partition {part}: {len(input_artifacts)} batches", flush=True)
        local_files = []
        for i, artifact in enumerate(input_artifacts):
            d = LOCAL / f"la_{part}_{i}.parquet"
            record_reducer_download(
                ckpt,
                download(
                    s3,
                    artifact["key"],
                    d,
                    artifact,
                    private_access=PRIVATE_REDUCER_ACCESS,
                ),
            )
            local_files.append(str(d))
        require_free_disk()
        con.execute("DROP TABLE IF EXISTS la")
        con.execute("CREATE TABLE la AS "
                    "SELECT * FROM read_parquet([{}])".format(", ".join(f"'{p}'" for p in local_files)))
        n_rows = con.execute("SELECT COUNT(*) FROM la").fetchone()[0]
        # Global per-listener SUM happens before rank. Support remains unfiltered
        # until the final cross-partition reduction.
        materialize_affinity_partition(con)
        require_free_disk()
        pair_local = LOCAL / f"pairs_{part}.parquet"
        nodelocal = LOCAL / f"nodes_{part}.parquet"
        population_local = LOCAL / f"population_{part}.parquet"
        pair_local.unlink(missing_ok=True)
        nodelocal.unlink(missing_ok=True)
        population_local.unlink(missing_ok=True)
        con.execute(f"""
            COPY (
              WITH pairs AS (
                SELECT a.artist_key AS a, b.artist_key AS b, COUNT(*) AS sh
                FROM bounded a JOIN bounded b
                     ON a.listener_key = b.listener_key AND a.artist_key < b.artist_key
                GROUP BY 1, 2
              )
              SELECT a, b, sh FROM pairs
              ORDER BY a, b
            ) TO '{pair_local}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        con.execute(f"""
            COPY (
              SELECT artist_key AS a, COUNT(DISTINCT listener_key) AS listeners
              FROM bounded GROUP BY 1
              ORDER BY 1
            ) TO '{nodelocal}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        con.execute(f"""
            COPY (
              SELECT COUNT(DISTINCT listener_key)::BIGINT AS listener_count
              FROM bounded
            ) TO '{population_local}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        part_artifacts = []
        part_artifacts.append(record_upload(
            ckpt,
            upload(s3, pair_local, f"{partial_prefix(ckpt, 'affinity_pairs')}/part={part}.parquet"),
        ))
        part_artifacts.append(record_upload(
            ckpt,
            upload(s3, nodelocal, f"{partial_prefix(ckpt, 'artist_listeners')}/part={part}.parquet"),
        ))
        part_artifacts.append(record_upload(
            ckpt,
            upload(
                s3,
                population_local,
                f"{partial_prefix(ckpt, 'affinity_population')}/part={part}.parquet",
            ),
        ))
        # free
        con.execute("DROP TABLE la")
        for f in local_files:
            Path(f).unlink(missing_ok=True)
        done_parts.add(part)
        ckpt["completed_affinity_partitions"] = sorted(done_parts)
        ckpt.setdefault("affinity_partition_artifacts", {})[part] = sorted(
            part_artifacts, key=lambda item: item["key"]
        )
        record_resource_snapshot(ckpt, phase="affinity_partition_commit")
        save_checkpoint(s3, ckpt)
        print(f"  partition {part}: {n_rows:,} LA rows done", flush=True)
    con.close()


def cmd_reduce_pairs(args) -> None:
    """Union affinity pair partials, join global artist listener totals, compute metrics."""
    require_free_disk()
    require_no_competing_heavy_job()
    s3 = r2_client()
    require_private_storage(s3)
    ckpt = load_checkpoint()
    validate_checkpoint(ckpt)
    ensure_map_complete(ckpt)
    ckpt["active_run_lock"] = args.run_lock
    if ckpt.get("completed_pairs"):
        artifact = ckpt.get("affinity_output_artifact")
        if not artifact:
            raise RuntimeError("completed affinity reduction has no output manifest")
        verify_artifact_manifest(s3, [artifact])
        print("affinity pairs already reduced.")
        return
    partitions = int(ckpt.get("listener_hash_partitions") or 0)
    listener_artifacts = committed_listener_artifacts(s3, ckpt)
    expected_parts = {
        artifact["key"].split("/part=", 1)[1].split("/", 1)[0]
        for artifact in listener_artifacts
    }
    completed_parts = {str(i) for i in ckpt.get("completed_affinity_partitions", [])}
    if partitions <= 0 or not expected_parts or completed_parts != expected_parts:
        raise RuntimeError(
            "affinity partitions are incomplete; reduce-pairs cannot publish a partial graph"
        )
    LOCAL.mkdir(parents=True, exist_ok=True)
    SPILL.mkdir(parents=True, exist_ok=True)
    # Gather only outputs named by committed per-partition manifests.
    partition_manifests = ckpt.get("affinity_partition_artifacts") or {}
    if set(partition_manifests) != expected_parts:
        raise RuntimeError("affinity partition manifests do not match expected partitions")
    all_artifacts = []
    for part in sorted(expected_parts, key=int):
        artifacts = partition_manifests[part]
        if len(artifacts) != 3:
            raise RuntimeError(f"affinity partition {part} must have three artifacts")
        verify_artifact_manifest(s3, artifacts)
        all_artifacts.extend(artifacts)
    pair_prefix = partial_prefix(ckpt, "affinity_pairs") + "/"
    node_prefix = partial_prefix(ckpt, "artist_listeners") + "/"
    population_prefix = partial_prefix(ckpt, "affinity_population") + "/"
    pair_artifacts = sorted(
        (a for a in all_artifacts if a["key"].startswith(pair_prefix)),
        key=lambda artifact: artifact["key"],
    )
    node_artifacts = sorted(
        (a for a in all_artifacts if a["key"].startswith(node_prefix)),
        key=lambda artifact: artifact["key"],
    )
    population_artifacts = sorted(
        (a for a in all_artifacts if a["key"].startswith(population_prefix)),
        key=lambda artifact: artifact["key"],
    )
    print(
        f"pair partials: {len(pair_artifacts)}   "
        f"node partials: {len(node_artifacts)}   "
        f"population partials: {len(population_artifacts)}"
    )
    if not pair_artifacts or not node_artifacts or not population_artifacts:
        raise RuntimeError("affinity reduction inputs are incomplete")
    if not all(
        len(artifacts) == len(expected_parts)
        for artifacts in (pair_artifacts, node_artifacts, population_artifacts)
    ):
        raise RuntimeError("affinity reduction input object counts do not match the partition geometry")
    require_capacity_for_artifacts(
        pair_artifacts + node_artifacts + population_artifacts
    )
    con = duckdb.connect()
    configure_duckdb(con)

    pair_locals, node_locals, population_locals = [], [], []
    for i, artifact in enumerate(pair_artifacts):
        d = LOCAL / f"pr_{i}.parquet"
        record_reducer_download(
            ckpt, download(s3, artifact["key"], d, artifact)
        )
        pair_locals.append(str(d))
    for i, artifact in enumerate(node_artifacts):
        d = LOCAL / f"nd_{i}.parquet"
        record_reducer_download(
            ckpt, download(s3, artifact["key"], d, artifact)
        )
        node_locals.append(str(d))
    for i, artifact in enumerate(population_artifacts):
        d = LOCAL / f"pop_{i}.parquet"
        record_reducer_download(
            ckpt, download(s3, artifact["key"], d, artifact)
        )
        population_locals.append(str(d))
    require_free_disk()
    record_resource_snapshot(ckpt, phase="affinity_pair_inputs_local")

    con.execute(
        "CREATE TEMP VIEW pair_input AS SELECT * FROM read_parquet([{}])".format(
            ",".join(repr(p) for p in pair_locals)
        )
    )
    con.execute(
        "CREATE TEMP VIEW node_input AS SELECT * FROM read_parquet([{}])".format(
            ",".join(repr(p) for p in node_locals)
        )
    )
    con.execute(
        "CREATE TEMP VIEW population_input AS SELECT * FROM read_parquet([{}])".format(
            ",".join(repr(p) for p in population_locals)
        )
    )
    materialize_global_affinity(con)
    require_free_disk()

    out = Path("/tmp/lb_affinity_evidence.parquet")
    out.unlink(missing_ok=True)
    scope = completion_scope(ckpt)
    evidence_status = (
        "SILVER_EXPLORATORY" if scope == "FULL_SOURCE" else "SILVER_BOUNDED_TEST"
    )
    knowledge_time_sql = sql_string_or_null(ckpt.get("source_object_last_modified"))
    retrieved_at_sql = sql_string_or_null(ckpt.get("source_first_access_at"))
    con.execute(f"""
        COPY (
            SELECT p.artist_key_a, p.artist_key_b, p.shared_listeners,
                   n1.listeners AS listeners_a, n2.listeners AS listeners_b,
                   population.listener_count AS population_listeners,
                   ROUND(p.shared_listeners::DOUBLE /
                         (n1.listeners + n2.listeners - p.shared_listeners), 5) AS jaccard,
                   ROUND(p.shared_listeners::DOUBLE /
                         SQRT(n1.listeners * n2.listeners), 5) AS cosine,
                   ROUND(
                       p.shared_listeners::DOUBLE * population.listener_count /
                       NULLIF(n1.listeners * n2.listeners, 0), 5
                   ) AS lift,
                   ROUND(LN(
                       p.shared_listeners::DOUBLE * population.listener_count /
                       NULLIF(n1.listeners * n2.listeners, 0)
                   ), 5) AS pmi,
                   {knowledge_time_sql}::TIMESTAMP AS knowledge_time,
                   'RAW_R2_OBJECT_LAST_MODIFIED' AS knowledge_time_basis,
                   NULL::TIMESTAMP AS source_publication_time,
                   {retrieved_at_sql}::TIMESTAMP AS retrieved_at,
                   'all_time' AS period,
                   '{DUMP_VERSION}' AS source_version,
                   'listenbrainz' AS source,
                   'ARTIST_SECURITY_25000' AS artist_universe,
                   'LISTENER_TOP_25' AS metric_universe,
                   {TOP_K}::INTEGER AS top_k,
                   {MIN_SHARED_LISTENERS}::INTEGER AS minimum_shared_listeners,
                   '{evidence_status}' AS evidence_status,
                   '{scope}' AS source_coverage_scope,
                   {int(ckpt['map_target_shards'])}::INTEGER AS source_shards_scanned,
                   {int(ckpt['source_shard_count'])}::INTEGER AS source_shards_total,
                   'CC0-1.0' AS license,
                   'OPEN_DATA_WITH_LISTENER_LEVEL_QUARANTINE' AS rights_status,
                   'ALLOWED' AS commercial_use_status,
                   'AGGREGATED_NO_LISTENER_IDENTIFIERS' AS privacy_classification,
                   'NOT_EVALUATED' AS significance_status,
                   'NOT_EVALUATED' AS multiple_testing_status,
                   FALSE AS serving_eligible
            FROM pairs p
            JOIN nodes n1 ON n1.artist_key = p.artist_key_a
            JOIN nodes n2 ON n2.artist_key = p.artist_key_b
            CROSS JOIN population
            ORDER BY p.artist_key_a, p.artist_key_b
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_edges = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out}')").fetchone()[0]
    output_key = affinity_output_key(ckpt)
    output_artifact = record_upload(ckpt, upload(s3, out, output_key))
    for f in pair_locals + node_locals + population_locals:
        Path(f).unlink(missing_ok=True)
    register_dataset(
        dataset_id=(
            "silver.listenbrainz_audience_affinity_evidence"
            if scope == "FULL_SOURCE"
            else "silver.listenbrainz_audience_affinity_validation"
        ),
        dataset_version=(DUMP_VERSION if scope == "FULL_SOURCE" else ckpt["run_namespace"]),
        layer="SILVER",
        source="listenbrainz",
        source_version=DUMP_VERSION,
        r2_bucket=LAKE_BUCKET,
        r2_prefix=output_key,
        fmt="parquet",
        schema_version="silver-listenbrainz-affinity-v2",
        row_count=n_edges,
        byte_count=int(output_artifact["bytes"]),
        source_checksum=ckpt.get("source_etag"),
        artifact_checksum=output_artifact["sha256"],
        verification_status=(
            "BUILD_COMPLETE" if scope == "FULL_SOURCE" else "BOUNDED_TEST_COMPLETE"
        ),
        license="CC0-1.0",
        rights_status="OPEN_DATA_WITH_LISTENER_LEVEL_QUARANTINE",
        commercial_use_status="ALLOWED",
        serving_eligible=False,
        access_classification="INTERNAL",
        upstream_dataset_ids=[SOURCE_DATASET],
        notes=(
            "Non-serving exploratory affinity evidence; significance and "
            "multiple-testing controls are not evaluated."
        ),
    )
    ckpt["completed_pairs"] = True
    ckpt["affinity_edges"] = n_edges
    ckpt["affinity_evidence_status"] = evidence_status
    ckpt["affinity_metric_universe"] = "LISTENER_TOP_25"
    ckpt["affinity_output_key"] = output_key
    ckpt["affinity_output_artifact"] = output_artifact
    ckpt["map_completion_scope"] = scope
    record_resource_snapshot(ckpt, phase="affinity_pairs_complete")
    save_checkpoint(s3, ckpt)
    con.close()
    print(f"Silver affinity evidence edges: {n_edges:,}")


def cmd_status(args) -> None:
    """Read-only readiness report; never contacts R2 or starts a scan."""
    ckpt = load_checkpoint()
    index_ok = INDEX_CACHE.exists()
    shard_count = None
    if index_ok:
        try:
            shard_count = len(validate_tar_index(json.loads(INDEX_CACHE.read_text())))
        except (OSError, TypeError, ValueError, KeyError, RuntimeError):
            index_ok = False
    try:
        validate_checkpoint(ckpt, partitions=args.partitions)
        checkpoint_ok = True
        checkpoint_error = None
    except RuntimeError as exc:
        checkpoint_ok = False
        checkpoint_error = str(exc)
    completed = set()
    for rng in ckpt.get("completed_batches", []) or []:
        if isinstance(rng, list) and len(rng) == 2:
            completed.update(range(int(rng[0]), int(rng[1]) + 1))
    target = args.target_shards
    free_disk_bytes = int(shutil.disk_usage(LOCAL.parent).free)
    heavy_jobs = competing_heavy_jobs()
    resource_ready = free_disk_bytes >= MIN_FREE_DISK_BYTES and not heavy_jobs
    print(json.dumps({
        "pipeline": "listenbrainz_full_scan",
        "pipeline_version": PIPELINE_VERSION,
        "read_only": True,
        "index_present": index_ok,
        "indexed_data_shards": shard_count,
        "checkpoint_valid_for_partitions": checkpoint_ok,
        "checkpoint_error": checkpoint_error,
        "completed_shards": len(completed),
        "checkpoint_map_target_shards": ckpt.get("map_target_shards"),
        "checkpoint_run_namespace": ckpt.get("run_namespace"),
        "target_shards": target,
        "free_disk_bytes": free_disk_bytes,
        "minimum_free_disk_bytes": MIN_FREE_DISK_BYTES,
        "competing_heavy_jobs": heavy_jobs,
        "bounded_map_command": (
            f"PYTHONPATH=python .venv/bin/python scripts/lb_full_scan.py map "
            f"--max-shards {target} --partitions {args.partitions}"
        ),
        "ready_for_bounded_map": bool(
            index_ok and shard_count and shard_count >= target and checkpoint_ok and resource_ready
        ),
    }, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("map")
    pm.add_argument("--max-shards", type=int, default=1526,
                    help="shard count for this worker slice (from --shard-start)")
    pm.add_argument(
        "--shard-start",
        type=int,
        default=0,
        help="inclusive shard index for this worker (must be BATCH_SHARDS-aligned)",
    )
    pm.add_argument(
        "--map-target-shards",
        type=int,
        default=None,
        help=(
            "full-corpus target used for run_namespace / completion scope; "
            "parallel workers must share the same value (e.g. 1526)"
        ),
    )
    pm.add_argument("--partitions", type=int, default=256,
                    help="listener hash partitions for affinity (must be stable across run)")
    pm.add_argument(
        "--interrupt-after-batch",
        type=int,
        default=None,
        help="test-only failure injection after this batch start index uploads, before commit",
    )
    pm.set_defaults(fn=cmd_map)

    ps = sub.add_parser("reduce-artist-day")
    ps.set_defaults(fn=cmd_reduce_artist_day)

    pa = sub.add_parser("reduce-affinity")
    pa.add_argument("--partitions", type=int, default=256)
    pa.set_defaults(fn=cmd_reduce_affinity)

    pp = sub.add_parser("reduce-pairs")
    pp.set_defaults(fn=cmd_reduce_pairs)

    ps = sub.add_parser("status", help="read-only checkpoint/index readiness report")
    ps.add_argument("--target-shards", type=int, default=76)
    ps.add_argument("--partitions", type=int, default=256)
    ps.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    if args.cmd == "status":
        args.fn(args)
    else:
        with exclusive_run_lock(args.cmd) as lock_state:
            args.run_lock = lock_state
            try:
                args.fn(args)
            finally:
                cleanup_local_transients()


if __name__ == "__main__":
    main()
