"""Bounded, replayable refresh of the existing factor-tape Parquet contract.

Source listing is bounded and repeated, not a high-water timestamp that loses
backdated arrivals. A frozen job plan plus verified chunks survives process loss.
The parent remains immutable; conditional CURRENT publication prevents lost updates.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import resource
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .job_manifest import new_manifest, now_iso

PREFIX = "gold/artist_factor_tape"
CURRENT = f"{PREFIX}/CURRENT.json"
SOURCE = "staging/youtube/"
VERSION = "factor_history_v2.1"
COLUMNS = (
    "factor_observation_key artist_key factor_family factor_name platform value unit "
    "observation_time available_at knowledge_time retrieved_at source evidence_ref "
    "source_scope rights_status commercial_use_status quality_status generation "
    "measurement_basis measurement_window population_scope geographic_scope "
    "methodology_version coverage_generation"
).split()
SCHEMA = pa.schema([(c, pa.float64() if c == "value" else pa.string()) for c in COLUMNS])


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _bound(params, name, default, maximum):
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"INVALID_BOUND:{name}")
    return value


def _download(lake, bucket, key, sha, path, max_bytes):
    head = lake.head(bucket, key)
    if not head or head["ContentLength"] > max_bytes:
        raise RuntimeError("INPUT_MISSING_OR_OVERSIZED")
    lake._s3.download_file(bucket, key, str(path))
    if path.stat().st_size > max_bytes or _file_sha(path) != sha:
        raise RuntimeError("INPUT_HASH_MISMATCH")
    return path.stat().st_size


def _validate_tick(tick):
    if not isinstance(tick, dict):
        raise RuntimeError("INVALID_TICK")
    if any(tick.get(k) for k in ("sandbox", "is_sandbox", "fixture", "is_fixture", "synthetic", "is_test")):
        raise RuntimeError("NONPRODUCTION_TICK")
    if tick.get("schema_version") != "youtube_channel_tick_v1" or tick.get("source") != "YOUTUBE_API":
        raise RuntimeError("UNSUPPORTED_TICK_CONTRACT")
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", str(tick.get("youtube_channel_id", ""))):
        raise RuntimeError("INVALID_CHANNEL_ID")
    if not tick.get("artist_key") or not tick.get("raw_evidence_ref"):
        raise RuntimeError("MISSING_IDENTITY_OR_LINEAGE")
    # Current collection timestamps may not be invented from the event date.
    from datetime import datetime
    for name in ("observed_at", "retrieved_at", "knowledge_time"):
        value = datetime.fromisoformat(str(tick.get(name, "")).replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise RuntimeError("UNZONED_TIMESTAMP")
    for name in ("subscriber_count", "channel_view_count", "video_count"):
        value = tick.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise RuntimeError("INVALID_FACTOR_VALUE")


def run_factor_history(spec, scratch_dir: Path, *, lake, normalize):
    from .batch_jobs import _git_commit, verify_outputs

    params = spec.get("params", {})
    max_ticks = _bound(params, "max_ticks", 25_000, 100_000)
    max_artists = _bound(params, "max_artists", 25_000, 25_000)
    inventory_limit = _bound(params, "max_inventory", 100_000, 100_000)
    concurrency = _bound(params, "read_concurrency", 16, 32)
    batch_size = _bound(params, "batch_size", 256, 512)
    job_id = spec.get("job_id", "artist_factor_tape_build_v1")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", job_id):
        raise ValueError("INVALID_JOB_ID")
    bucket = lake.config.lake_bucket
    control = f"control/jobs/artist_factor_tape_build_v1/{job_id}"
    manifest_path = f"{control}/manifest.json"
    manifest = new_manifest("artist_factor_tape_build_v1", job_id, code_commit=_git_commit(), params=params)
    started = time.monotonic()
    work = Path(scratch_dir) / "factor_tape"
    work.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        if shutil.disk_usage(work).free < 2 * 1024**3:
            raise RuntimeError("SCRATCH_FREE_SPACE_BELOW_2_GIB")
        plan, _ = lake.read_versioned_json(bucket, f"{control}/plan.json")
        if plan is None:
            parent, parent_etag = lake.read_versioned_json(bucket, CURRENT)
            ledger = {}
            if parent and parent.get("ledger_key"):
                manifest.r2_read_bytes += _download(lake, bucket, parent["ledger_key"], parent["ledger_sha256"], work / "ledger.json", 64 * 1024**2)
                ledger = json.loads((work / "ledger.json").read_bytes())
            inventory = lake.list_prefix(bucket, SOURCE, limit=inventory_limit + 1)
            if len(inventory) > inventory_limit:
                raise RuntimeError("INVENTORY_LIMIT_EXCEEDED: no partial inventory publication")
            inventory = sorted((o for o in inventory if o["key"].endswith(".json")), key=lambda o: o["key"])
            for obj in inventory:
                if obj["size"] > 64 * 1024 or not obj.get("etag"):
                    raise RuntimeError("SOURCE_SIZE_OR_VERSION_INVALID")
                if obj["key"] in ledger and ledger[obj["key"]]["etag"] != obj["etag"]:
                    raise RuntimeError("APPEND_ONLY_SOURCE_MUTATED")
            pending = [o for o in inventory if o["key"] not in ledger]
            plan = {"version": VERSION, "params": params, "parent": parent, "parent_etag": parent_etag,
                    "selected": [{k: o[k] for k in ("key", "size", "etag")} for o in pending[:max_ticks]],
                    "inventory_count": len(inventory), "pending_count": len(pending),
                    "planned_at": now_iso(), "code_commit": _git_commit()}
            lake.put_json_if_version(bucket, f"{control}/plan.json", plan, None)
        if plan["version"] != VERSION or plan["params"] != params or plan["code_commit"] != _git_commit():
            raise RuntimeError("RESUME_CONTRACT_MISMATCH: use a new job_id")
        completed, _ = lake.read_versioned_json(bucket, f"{control}/result.json")
        if completed:
            return completed
        parent = plan["parent"]
        ledger = {}
        parent_rows = 0
        conn = duckdb.connect(str(work / "merge.duckdb"))
        conn.execute("SET memory_limit='256MB'")
        conn.execute("SET threads=2")
        conn.execute("SET max_temp_directory_size='1GB'")
        conn.execute("CREATE TABLE tape (" + ",".join(f'{c} {"DOUBLE" if c == "value" else "VARCHAR"}' for c in COLUMNS) + ")")
        if parent:
            manifest.source_generation = parent["generation"]
            manifest.source_paths.append(parent["object_key"])
            manifest.r2_read_bytes += _download(lake, bucket, parent["object_key"], parent["sha256"], work / "parent.parquet", 512 * 1024**2)
            conn.execute("INSERT INTO tape SELECT " + ",".join(COLUMNS) + " FROM read_parquet(?)", [str(work / "parent.parquet")])
            parent_rows = conn.execute("SELECT count(*) FROM tape").fetchone()[0]
            if parent_rows != parent["factor_rows"]:
                raise RuntimeError("PARENT_ROW_COUNT_MISMATCH")
            if parent.get("ledger_key"):
                manifest.r2_read_bytes += _download(lake, bucket, parent["ledger_key"], parent["ledger_sha256"], work / "ledger.json", 64 * 1024**2)
                ledger = json.loads((work / "ledger.json").read_bytes())
        selected = plan["selected"]
        manifest.total_batches = (len(selected) + batch_size - 1) // batch_size
        manifest.source_paths.append(f"{control}/plan.json")
        reused = 0
        def read_one(obj):
            raw = lake.get_bytes_if_match(bucket, obj["key"], obj["etag"])
            if len(raw) != obj["size"]:
                raise RuntimeError("SOURCE_SIZE_CHANGED")
            tick = json.loads(raw)
            _validate_tick(tick)
            return normalize(tick), {"etag": obj["etag"], "sha256": _sha(raw)}, len(raw)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for offset in range(0, len(selected), batch_size):
                chunk_inputs = selected[offset:offset + batch_size]
                digest = _sha(_json({"version": VERSION, "inputs": chunk_inputs}))
                chunk_key = f"{control}/chunks/{digest}.parquet"
                checkpoint_key = f"{control}/chunks/{digest}.json"
                checkpoint, _ = lake.read_versioned_json(bucket, checkpoint_key)
                chunk_path = work / "chunk.parquet"
                if checkpoint:
                    manifest.r2_read_bytes += _download(lake, bucket, chunk_key, checkpoint["sha256"], chunk_path, 4 * 1024**2)
                    reused += 1
                else:
                    rows = []
                    inputs = {}
                    # map preserves frozen source order; at most one bounded chunk of futures.
                    for obj, (normalized, lineage, size) in zip(chunk_inputs, pool.map(read_one, chunk_inputs)):
                        rows.extend(normalized)
                        inputs[obj["key"]] = lineage
                        manifest.r2_read_bytes += size
                    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), chunk_path, compression="zstd")
                    checkpoint = {"sha256": _file_sha(chunk_path), "inputs": inputs}
                    lake.put_bytes(bucket, chunk_key, chunk_path.read_bytes())
                    manifest.r2_write_bytes += chunk_path.stat().st_size
                    if not lake.verify_object(bucket, chunk_key, checkpoint["sha256"]):
                        raise RuntimeError("CHUNK_VERIFY_FAILED")
                    lake.put_json_if_version(bucket, checkpoint_key, checkpoint, None)
                conn.execute("INSERT INTO tape SELECT * FROM read_parquet(?)", [str(chunk_path)])
                ledger.update(checkpoint["inputs"])
                manifest.completed_batches += 1
                manifest.rows_read = min(offset + batch_size, len(selected))
                manifest.scratch_peak_bytes = max(manifest.scratch_peak_bytes, sum(p.stat().st_size for p in work.rglob('*') if p.is_file()))
                lake.write_manifest(bucket, manifest_path, manifest.to_dict())
                chunk_path.unlink()
        # Never silently reconcile two different payloads sharing an observation key.
        conn.execute("CREATE TABLE unique_rows AS SELECT DISTINCT * FROM tape")
        if conn.execute("SELECT 1 FROM unique_rows GROUP BY factor_observation_key HAVING count(*) > 1 LIMIT 1").fetchone():
            raise RuntimeError("OBSERVATION_KEY_CONFLICT")
        count, artists, first, last = conn.execute("SELECT count(*), count(DISTINCT artist_key), min(observation_time), max(observation_time) FROM unique_rows").fetchone()
        if not count:
            raise RuntimeError("FACTOR_TAPE_EMPTY")
        if artists > max_artists:
            raise RuntimeError("ARTIST_LIMIT_EXCEEDED: history retained; no publication")
        if count < parent_rows:
            raise RuntimeError("PARENT_HISTORY_REGRESSION")
        tape_path = work / "artist_factor_tape.parquet"
        conn.execute("COPY (SELECT * FROM unique_rows ORDER BY factor_observation_key) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(tape_path)])
        conn.close()
        conn = None
        if tape_path.stat().st_size > 512 * 1024**2:
            raise RuntimeError("OUTPUT_SIZE_LIMIT_EXCEEDED")
        tape_sha = _file_sha(tape_path)
        ledger_bytes = _json(ledger)
        ledger_sha = _sha(ledger_bytes)
        generation = "artist_factor_tape_v1_" + _sha(_json({"tape": tape_sha, "ledger": ledger_sha, "plan": plan}))[:24]
        tape_key = f"{PREFIX}/{generation}/artist_factor_tape.parquet"
        ledger_key = f"{PREFIX}/{generation}/inputs.json"
        lake._s3.upload_file(str(tape_path), bucket, tape_key)
        lake.put_bytes(bucket, ledger_key, ledger_bytes, content_type="application/json")
        manifest.r2_write_bytes += tape_path.stat().st_size + len(ledger_bytes)
        manifest.rows_written = count
        manifest.output_hashes = {tape_key: tape_sha, ledger_key: ledger_sha}
        manifest.output_paths = [f"r2://{bucket}/{key}" for key in manifest.output_hashes]
        manifest.scratch_peak_bytes = max(manifest.scratch_peak_bytes, sum(p.stat().st_size for p in work.rglob('*') if p.is_file()))
        manifest.peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        manifest.runtime_seconds = round(time.monotonic() - started, 3)
        manifest.status = "BUILD_COMPLETE"
        lake.write_manifest(bucket, manifest_path, manifest.to_dict())
        verify_outputs(lake, bucket=bucket, output_hashes=manifest.output_hashes, manifest=manifest, manifest_key_path=manifest_path)
        payload = {"artifact": PREFIX, "contract_version": "artist_factor_tape_v1", "refresh_version": VERSION,
                   "generation": generation, "object_key": tape_key, "sha256": tape_sha, "bytes": tape_path.stat().st_size,
                   "ledger_key": ledger_key, "ledger_sha256": ledger_sha, "created_at": now_iso(),
                   "source_prefix": SOURCE, "tick_rows_read": len(selected), "factor_rows": count, "artists": artists,
                   "skipped": 0, "parent_generation": parent and parent["generation"], "parent_rows": parent_rows,
                   "added_rows": count - parent_rows, "inventory_count": plan["inventory_count"],
                   "pending_ticks": plan["pending_count"] - len(selected), "coverage": "BOUNDED_INVENTORY_AS_OF_PLAN",
                   "time_min": first, "time_max": last, "code_commit": _git_commit(), "plan_key": f"{control}/plan.json"}
        live, _ = lake.read_versioned_json(bucket, CURRENT)
        # Retry after an uncertain successful publish must not restore an older generation.
        if not live or live.get("generation") != generation:
            lake.put_json_if_version(bucket, CURRENT, payload, plan["parent_etag"])
        manifest.status = manifest.publication_state = "PUBLISHED"
        manifest.completed_at = now_iso()
        lake.write_manifest(bucket, manifest_path, manifest.to_dict())
        result = {"status": "COMPLETED", **payload, "current_key": CURRENT, "tape_key": tape_key,
                  "manifest_key": manifest_path, "reused_chunks": reused, "runtime_seconds": manifest.runtime_seconds,
                  "peak_rss_bytes": manifest.peak_rss_bytes, "scratch_peak_bytes": manifest.scratch_peak_bytes,
                  "r2_read_bytes": manifest.r2_read_bytes, "r2_write_bytes": manifest.r2_write_bytes}
        lake.put_bytes(bucket, f"{control}/result.json", _json(result), content_type="application/json")
        return result
    except Exception:
        # Verification success survives publication failure; never claim PUBLISHED on CAS failure.
        if manifest.publication_state != "VERIFIED" and manifest.publication_state != "PUBLISHED":
            manifest.status = "FAILED"
        manifest.error_code = "PUBLICATION_FAILED" if manifest.publication_state == "VERIFIED" else "FACTOR_HISTORY_FAILED"
        manifest.error = manifest.error_code
        manifest.runtime_seconds = round(time.monotonic() - started, 3)
        try:
            lake.write_manifest(bucket, manifest_path, manifest.to_dict())
        except Exception:
            pass
        raise
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(work, ignore_errors=True)
