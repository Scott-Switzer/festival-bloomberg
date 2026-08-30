#!/usr/bin/env python3
"""Verify the currently published Wikidata Silver generation.

The R2 ``CURRENT.json`` object is the publication authority.  The catalog and
the older fixed-key objects are deliberately not consulted: a missing
``CURRENT.json`` is a failed verification, not a fallback to legacy output.

Default mode performs only HEAD and JSON reads.  ``--deep`` downloads one
Parquet artifact at a time to a bounded temporary file and verifies its bytes,
Deep decoding is limited to 1,024-row batches and a configurable decoded
memory cap.  The verifier never writes R2 or the local catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from festival_bloomberg.lake.r2 import r2_client


LAKE_BUCKET = "festival-intelligence-lake"
RAW_BUCKET = "festival-intelligence-raw"
RAW_KEY = "bulk/wikidata/dump=latest-truthy/latest-truthy.nt.bz2"
RAW_BYTES = 43_329_477_419
RAW_ETAG = "7240fc164e418c27eac9e3ade4ad71b2-646"
DUMP_VERSION = "latest-truthy-20260828"
CURRENT_KEY = "silver/wikidata/CURRENT.json"
DEFAULT_MAX_JSON_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TEMP_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_DECODED_BATCH_BYTES = 128 * 1024 * 1024
DEEP_BATCH_SIZE = 1_024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)
EXPECTED_FILES = {
    "music_entities": "music_entities.parquet",
    "entity_types": "entity_types.parquet",
    "entity_ids": "entity_external_ids.parquet",
    "artist_ids": "artist_external_ids.parquet",
    "venue_ids": "venue_external_ids.parquet",
    "place_ids": "place_external_ids.parquet",
    "coordinates": "entity_coordinates.parquet",
    "locations": "entity_locations.parquet",
    "websites": "entity_websites.parquet",
    "inceptions": "entity_inception.parquet",
    "genres": "genres.parquet",
    "relationships": "relationships.parquet",
}
EXPECTED_DATASET_IDS = {
    name: f"silver.wikidata_{name}" for name in EXPECTED_FILES
}
EXPECTED_DATASET_IDS.update({
    "entity_ids": "silver.wikidata_entity_external_ids",
    "artist_ids": "silver.wikidata_artist_external_ids",
    "venue_ids": "silver.wikidata_venue_external_ids",
    "place_ids": "silver.wikidata_place_external_ids",
    "coordinates": "silver.wikidata_entity_coordinates",
    "locations": "silver.wikidata_entity_locations",
    "websites": "silver.wikidata_entity_websites",
    "inceptions": "silver.wikidata_entity_inception",
})

_COMMON_FIELDS = [
    pa.field("source_system", pa.string()),
    pa.field("knowledge_time", pa.string()),
    pa.field("ingested_at", pa.string()),
]
OUTPUT_SCHEMAS = {
    "music_entities": pa.schema([
        pa.field("qid", pa.string()), pa.field("classification", pa.string()),
    ] + _COMMON_FIELDS),
    "entity_types": pa.schema([
        pa.field("qid", pa.string()), pa.field("type_qid", pa.string()),
    ] + _COMMON_FIELDS),
    "entity_ids": pa.schema([
        pa.field("qid", pa.string()), pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "artist_ids": pa.schema([
        pa.field("qid", pa.string()), pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "venue_ids": pa.schema([
        pa.field("qid", pa.string()), pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "place_ids": pa.schema([
        pa.field("qid", pa.string()), pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "coordinates": pa.schema([
        pa.field("qid", pa.string()), pa.field("longitude", pa.float64()),
        pa.field("latitude", pa.float64()),
    ] + _COMMON_FIELDS),
    "locations": pa.schema([
        pa.field("qid", pa.string()), pa.field("location_property", pa.string()),
        pa.field("location_qid", pa.string()),
    ] + _COMMON_FIELDS),
    "websites": pa.schema([
        pa.field("qid", pa.string()), pa.field("url", pa.string()),
    ] + _COMMON_FIELDS),
    "inceptions": pa.schema([
        pa.field("qid", pa.string()), pa.field("inception", pa.string()),
    ] + _COMMON_FIELDS),
    "genres": pa.schema([
        pa.field("qid", pa.string()), pa.field("genre_qid", pa.string()),
    ] + _COMMON_FIELDS),
    "relationships": pa.schema([
        pa.field("subject_qid", pa.string()),
        pa.field("relationship_property", pa.string()),
        pa.field("object_qid", pa.string()),
    ] + _COMMON_FIELDS),
}


class VerificationError(RuntimeError):
    """A safe, user-facing verification failure."""


def _safe_int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"{field} is not an integer") from exc
    if result < 0:
        raise VerificationError(f"{field} is negative")
    return result


def _metadata(head: dict[str, Any]) -> dict[str, str]:
    value = head.get("Metadata", {})
    return value if isinstance(value, dict) else {}


def _head(s3: Any, key: str) -> dict[str, Any]:
    try:
        value = s3.head_object(Bucket=LAKE_BUCKET, Key=key)
    except Exception as exc:  # noqa: BLE001 - hide provider details/credentials
        raise VerificationError(f"required R2 object is unavailable: {key}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"invalid HEAD response for {key}")
    return value


def _get_object(s3: Any, key: str) -> dict[str, Any]:
    try:
        response = s3.get_object(Bucket=LAKE_BUCKET, Key=key)
    except Exception as exc:  # noqa: BLE001 - hide provider details/credentials
        raise VerificationError(f"R2 object read failed: {key}") from exc
    if not isinstance(response, dict):
        raise VerificationError(f"invalid R2 read response: {key}")
    return response


def _read_body(
    response: dict[str, Any],
    key: str,
    expected_size: int,
    max_bytes: int,
) -> bytes:
    body = response.get("Body")
    if body is None or not hasattr(body, "read"):
        raise VerificationError(f"R2 object has no readable body: {key}")
    close = getattr(body, "close", None)
    if not callable(close):
        raise VerificationError(f"R2 object body is not closable: {key}")
    if expected_size > max_bytes:
        raise VerificationError(f"R2 object exceeds configured bound: {key}")
    data = bytearray()
    try:
        target = expected_size + 1  # one probe byte rejects stale HEAD/prefixes
        while len(data) < target:
            request_size = min(1024 * 1024, target - len(data))
            chunk = body.read(request_size)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise VerificationError(f"R2 object body is not bytes: {key}")
            if len(chunk) > request_size:
                raise VerificationError(f"R2 JSON body exceeds configured bound: {key}")
            data.extend(chunk)
        if len(data) > expected_size:
            raise VerificationError(f"R2 JSON body has trailing data: {key}")
    except VerificationError:
        raise
    except Exception as exc:  # noqa: BLE001 - hide provider details/credentials
        raise VerificationError(f"R2 JSON read failed: {key}") from exc
    finally:
        try:
            close()
        except Exception as exc:  # noqa: BLE001 - hide provider details/credentials
            raise VerificationError(f"R2 JSON body close failed: {key}") from exc
    return bytes(data)


def _read_verified_json(
    s3: Any,
    key: str,
    expected_sha256: str | None = None,
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES,
) -> dict[str, Any]:
    head = _head(s3, key)
    metadata_sha = _metadata(head).get("sha256")
    if not metadata_sha or not SHA256_RE.fullmatch(metadata_sha):
        raise VerificationError(f"missing or invalid SHA-256 metadata: {key}")
    head_size = _safe_int(head.get("ContentLength", -1), f"{key} ContentLength")
    if head_size > max_json_bytes:
        raise VerificationError(f"JSON object exceeds configured bound: {key}")
    response = _get_object(s3, key)
    data = _read_body(response, key, head_size, max_json_bytes)
    if head_size != len(data):
        raise VerificationError(f"JSON size mismatch: {key}")
    content_sha = hashlib.sha256(data).hexdigest()
    if content_sha != metadata_sha:
        raise VerificationError(f"JSON content SHA-256 mismatch: {key}")
    if expected_sha256 is not None and content_sha != expected_sha256:
        raise VerificationError(f"linked JSON SHA-256 mismatch: {key}")
    try:
        value = json.loads(data)
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"invalid JSON: {key}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"JSON root is not an object: {key}")
    return value


def _source(source: Any, label: str) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise VerificationError(f"{label} source is missing")
    if (
        source.get("bucket") != RAW_BUCKET
        or source.get("key") != RAW_KEY
        or _safe_int(source.get("bytes", -1), f"{label} source bytes") != RAW_BYTES
        or str(source.get("etag", "")).strip('"') != RAW_ETAG
    ):
        raise VerificationError(f"{label} source identity mismatch")
    return {
        "bucket": RAW_BUCKET,
        "key": RAW_KEY,
        "bytes": RAW_BYTES,
        "etag": RAW_ETAG,
    }


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise VerificationError(f"{field} must be a non-empty string")
    return value


def _require_rfc3339_utc(value: Any, field: str) -> str:
    value = _require_string(value, field)
    if not RFC3339_UTC_RE.fullmatch(value):
        raise VerificationError(f"{field} must be RFC3339 UTC")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise VerificationError(f"{field} must be RFC3339 UTC") from exc
    return value


def _artifact_head(s3: Any, artifact: dict[str, Any], run_id: str) -> tuple[int, str]:
    name = _require_string(artifact.get("name"), "artifact name")
    key = _require_string(artifact.get("r2_key"), f"artifact {name} key")
    prefix = f"silver/wikidata/generations/{run_id}/"
    if name not in EXPECTED_FILES or key != f"{prefix}{EXPECTED_FILES[name]}":
        raise VerificationError(f"artifact {name} is outside its generation")
    bucket = artifact.get("r2_bucket", LAKE_BUCKET)
    if bucket != LAKE_BUCKET:
        raise VerificationError(f"artifact {name} bucket mismatch")
    expected_size = _safe_int(artifact.get("byte_count", -1), f"artifact {name} byte_count")
    expected_sha = _require_string(artifact.get("sha256"), f"artifact {name} sha256")
    if not SHA256_RE.fullmatch(expected_sha):
        raise VerificationError(f"artifact {name} sha256 is invalid")
    head = _head(s3, key)
    actual_size = _safe_int(head.get("ContentLength", -1), f"artifact {name} ContentLength")
    if actual_size != expected_size:
        raise VerificationError(f"artifact {name} size mismatch")
    actual_sha = _metadata(head).get("sha256")
    if actual_sha != expected_sha:
        raise VerificationError(f"artifact {name} SHA-256 metadata mismatch")
    return actual_size, expected_sha


def _deep_verify_artifact(
    s3: Any,
    artifact: dict[str, Any],
    run_id: str,
    max_temp_bytes: int,
    knowledge_time: str,
    max_decoded_batch_bytes: int,
) -> None:
    name = _require_string(artifact.get("name"), "artifact name")
    key = _require_string(artifact.get("r2_key"), f"artifact {name} key")
    expected_size, expected_sha = _artifact_head(s3, artifact, run_id)
    if expected_size > max_temp_bytes:
        raise VerificationError(f"artifact {name} exceeds deep temp-file bound")
    response = _get_object(s3, key)
    body = response.get("Body")
    if body is None or not hasattr(body, "read"):
        raise VerificationError(f"artifact {name} has no readable body")
    close = getattr(body, "close", None)
    if not callable(close):
        raise VerificationError(f"artifact {name} body is not closable")
    count = 0
    digest = hashlib.sha256()
    # At most DEEP_BATCH_SIZE rows are decoded at once; the Parquet file itself
    # is bounded independently by max_temp_bytes and is deleted on exit.
    with tempfile.TemporaryDirectory(prefix="wikidata_verify_") as temp_dir:
        path = Path(temp_dir) / "artifact.parquet"
        try:
            with path.open("wb") as handle:
                while True:
                    if count >= expected_size + 1:
                        break
                    try:
                        remaining = expected_size + 1 - count
                        chunk = body.read(min(8 * 1024 * 1024, remaining))
                    except Exception as exc:  # noqa: BLE001 - hide provider details
                        raise VerificationError(f"artifact {name} body read failed") from exc
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise VerificationError(f"artifact {name} body is not bytes")
                    count += len(chunk)
                    if count > max_temp_bytes:
                        raise VerificationError(f"artifact {name} exceeds deep temp-file bound")
                    if count > expected_size:
                        raise VerificationError(f"artifact {name} has trailing data")
                    digest.update(chunk)
                    handle.write(chunk)
        finally:
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - hide provider details
                raise VerificationError(f"artifact {name} body close failed") from exc
        if count != expected_size or digest.hexdigest() != expected_sha:
            raise VerificationError(f"artifact {name} content verification failed")
        try:
            parquet = pq.ParquetFile(path)
            actual_schema = parquet.schema_arrow
            expected_schema = OUTPUT_SCHEMAS.get(name)
            if expected_schema is None:
                raise VerificationError(f"artifact {name} has no known schema")
            if artifact.get("schema") != str(expected_schema):
                raise VerificationError(f"artifact {name} manifest schema mismatch")
            if actual_schema != expected_schema:
                raise VerificationError(f"artifact {name} schema mismatch")
            metadata = parquet.metadata
            if metadata is None:
                raise VerificationError(f"artifact {name} has no Parquet metadata")
            for row_group_index in range(metadata.num_row_groups):
                row_group = metadata.row_group(row_group_index)
                row_group_bytes = 0
                for column_index in range(row_group.num_columns):
                    column = row_group.column(column_index)
                    column_bytes = column.total_uncompressed_size
                    if column_bytes is None or column_bytes < 0:
                        raise VerificationError(
                            f"artifact {name} has invalid uncompressed-size metadata"
                        )
                    if column_bytes > max_decoded_batch_bytes:
                        raise VerificationError(
                            f"artifact {name} column exceeds decoded memory cap"
                        )
                    row_group_bytes += column_bytes
                if row_group_bytes > max_decoded_batch_bytes:
                    raise VerificationError(
                        f"artifact {name} row group exceeds decoded memory cap"
                    )
            actual_rows = 0
            batches = parquet.iter_batches(batch_size=DEEP_BATCH_SIZE)
            while True:
                allocation_before = pa.total_allocated_bytes()
                try:
                    batch = next(batches)
                except StopIteration:
                    break
                allocation_delta = pa.total_allocated_bytes() - allocation_before
                if batch.nbytes > max_decoded_batch_bytes:
                    raise VerificationError(
                        f"artifact {name} decoded batch exceeds memory cap"
                    )
                if allocation_delta > max_decoded_batch_bytes:
                    raise VerificationError(
                        f"artifact {name} Arrow allocation exceeds memory cap"
                    )
                batch.validate(full=True)
                for field_name, expected in (
                    ("source_system", "wikidata"),
                    ("knowledge_time", knowledge_time),
                    ("ingested_at", knowledge_time),
                ):
                    values = batch.column(field_name)
                    matches = pc.equal(values, pa.scalar(expected, type=pa.string()))
                    matches = pc.fill_null(matches, False)
                    if pc.all(matches).as_py() is not True:
                        raise VerificationError(
                            f"artifact {name} has invalid {field_name} provenance"
                        )
                actual_rows += batch.num_rows
            expected_rows = _safe_int(artifact.get("row_count", -1), f"artifact {name} row_count")
            if actual_rows != expected_rows:
                raise VerificationError(f"artifact {name} row count mismatch")
        except VerificationError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize Parquet failures
            raise VerificationError(f"artifact {name} is not valid Parquet") from exc


def verify_generation(
    s3: Any,
    *,
    deep: bool = False,
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES,
    max_temp_bytes: int = DEFAULT_MAX_TEMP_BYTES,
    max_decoded_batch_bytes: int = DEFAULT_MAX_DECODED_BATCH_BYTES,
) -> dict[str, Any]:
    """Verify one immutable generation and return a credential-free report."""
    if max_json_bytes <= 0:
        raise VerificationError("JSON object bound must be positive")
    if max_temp_bytes <= 0:
        raise VerificationError("deep temp-file bound must be positive")
    if max_decoded_batch_bytes <= 0:
        raise VerificationError("decoded batch memory bound must be positive")
    current = _read_verified_json(s3, CURRENT_KEY, max_json_bytes=max_json_bytes)
    if current.get("schema_version") != "wikidata-silver-current-v1":
        raise VerificationError("CURRENT schema mismatch")
    if current.get("publication_authority") is not True:
        raise VerificationError("CURRENT is not marked as publication authority")
    run_id = _require_string(current.get("run_id"), "CURRENT run_id")
    if not RUN_ID_RE.fullmatch(run_id):
        raise VerificationError("CURRENT run_id is invalid")
    dump_version = _require_string(current.get("dump_version"), "CURRENT dump_version")
    if dump_version != DUMP_VERSION:
        raise VerificationError("CURRENT dump_version is not the approved generator version")
    published_at = _require_rfc3339_utc(
        current.get("published_at"),
        "CURRENT published_at",
    )
    manifest_key = _require_string(current.get("manifest_key"), "CURRENT manifest_key")
    manifest_sha = _require_string(current.get("manifest_sha256"), "CURRENT manifest_sha256")
    generation_prefix = f"silver/wikidata/generations/{run_id}/"
    if manifest_key != f"{generation_prefix}manifest.json" or not SHA256_RE.fullmatch(manifest_sha):
        raise VerificationError("CURRENT manifest linkage is invalid")
    manifest = _read_verified_json(s3, manifest_key, manifest_sha, max_json_bytes)
    if manifest.get("schema_version") != "wikidata-silver-generation-v1":
        raise VerificationError("generation manifest schema mismatch")
    if manifest.get("status") != "PUBLISHED":
        raise VerificationError("generation manifest is not PUBLISHED")
    if manifest.get("run_id") != run_id:
        raise VerificationError("generation manifest run_id mismatch")
    if manifest.get("dump_version") != dump_version:
        raise VerificationError("generation manifest dump_version mismatch")
    generation_knowledge_time = _require_rfc3339_utc(
        manifest.get("knowledge_time"),
        "generation manifest knowledge_time",
    )
    source = _source(manifest.get("source"), "generation manifest")
    artifacts_key = _require_string(
        manifest.get("artifacts_manifest_key"), "generation artifacts manifest key"
    )
    artifacts_sha = _require_string(
        manifest.get("artifacts_manifest_sha256"), "generation artifacts manifest SHA-256"
    )
    if artifacts_key != f"{generation_prefix}artifacts.json" or not SHA256_RE.fullmatch(artifacts_sha):
        raise VerificationError("generation artifacts linkage is invalid")
    artifacts_manifest = _read_verified_json(
        s3, artifacts_key, artifacts_sha, max_json_bytes
    )
    if artifacts_manifest.get("schema_version") != "wikidata-silver-artifacts-v1":
        raise VerificationError("artifacts manifest schema mismatch")
    if artifacts_manifest.get("status") != "ARTIFACTS_VERIFIED":
        raise VerificationError("artifacts manifest is not ARTIFACTS_VERIFIED")
    if artifacts_manifest.get("run_id") != run_id:
        raise VerificationError("artifacts manifest run_id mismatch")
    if artifacts_manifest.get("dump_version") != dump_version:
        raise VerificationError("artifacts manifest dump_version mismatch")
    if _source(artifacts_manifest.get("source"), "artifacts manifest") != source:
        raise VerificationError("manifest source identities differ")
    knowledge_time = _require_rfc3339_utc(
        artifacts_manifest.get("knowledge_time"),
        "artifacts manifest knowledge_time",
    )
    if generation_knowledge_time != knowledge_time:
        raise VerificationError("manifest knowledge_time mismatch")
    if published_at != generation_knowledge_time:
        raise VerificationError("CURRENT published_at mismatch")
    artifacts = artifacts_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise VerificationError("artifacts manifest artifacts is not a list")
    declared_count = _safe_int(manifest.get("artifact_count", -1), "manifest artifact_count")
    if declared_count != len(artifacts):
        raise VerificationError("artifact count mismatch")
    names_in_manifest = {
        _require_string(artifact.get("name"), "artifact name")
        for artifact in artifacts
        if isinstance(artifact, dict)
    }
    if names_in_manifest != set(EXPECTED_FILES):
        missing = sorted(set(EXPECTED_FILES) - names_in_manifest)
        unexpected = sorted(names_in_manifest - set(EXPECTED_FILES))
        raise VerificationError(
            f"artifact name set mismatch (missing={missing}, unexpected={unexpected})"
        )
    names: set[str] = set()
    dataset_ids: set[str] = set()
    total_rows = 0
    total_bytes = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise VerificationError("artifact entry is not an object")
        name = _require_string(artifact.get("name"), "artifact name")
        if name in names:
            raise VerificationError(f"duplicate artifact name: {name}")
        names.add(name)
        dataset_id = _require_string(artifact.get("dataset_id"), f"artifact {name} dataset_id")
        if dataset_id != EXPECTED_DATASET_IDS[name]:
            raise VerificationError(f"artifact {name} dataset_id mismatch")
        if dataset_id in dataset_ids:
            raise VerificationError(f"duplicate artifact dataset_id: {dataset_id}")
        dataset_ids.add(dataset_id)
        size, _ = _artifact_head(s3, artifact, run_id)
        total_bytes += size
        total_rows += _safe_int(artifact.get("row_count", -1), f"artifact {name} row_count")
        if deep:
            _deep_verify_artifact(
                s3,
                artifact,
                run_id,
                max_temp_bytes,
                knowledge_time,
                max_decoded_batch_bytes,
            )
    return {
        "status": "PASS" if deep else "PASS_METADATA_ONLY",
        "mode": "DEEP" if deep else "HEAD_ONLY",
        "verification_strength": "DEEP" if deep else "METADATA_ONLY",
        "deep_batch_size": DEEP_BATCH_SIZE,
        "max_decoded_batch_bytes": max_decoded_batch_bytes,
        "current_key": CURRENT_KEY,
        "run_id": run_id,
        "source": source,
        "artifact_count": len(artifacts),
        "artifact_names": sorted(names),
        "rows": total_rows,
        "bytes": total_bytes,
        "total_rows": total_rows,
        "total_bytes": total_bytes,
        "manifest_status": manifest["status"],
        "artifacts_manifest_status": artifacts_manifest["status"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deep", action="store_true", help="download and verify Parquet artifacts")
    parser.add_argument(
        "--max-temp-bytes", type=int,
        default=int(os.environ.get("WD_VERIFY_MAX_TEMP_BYTES", str(DEFAULT_MAX_TEMP_BYTES))),
        help="maximum one-artifact temporary file size in --deep mode",
    )
    parser.add_argument(
        "--max-json-bytes", type=int,
        default=int(os.environ.get("WD_VERIFY_MAX_JSON_BYTES", str(DEFAULT_MAX_JSON_BYTES))),
        help="maximum size of each JSON control object",
    )
    parser.add_argument(
        "--max-decoded-batch-bytes", type=int,
        default=int(os.environ.get(
            "WD_VERIFY_MAX_DECODED_BATCH_BYTES",
            str(DEFAULT_MAX_DECODED_BATCH_BYTES),
        )),
        help="maximum decoded bytes for one Parquet row group/batch",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_generation(
            r2_client(), deep=args.deep, max_json_bytes=args.max_json_bytes,
            max_temp_bytes=args.max_temp_bytes,
            max_decoded_batch_bytes=args.max_decoded_batch_bytes,
        )
    except VerificationError as exc:
        report = {
            "status": "FAIL",
            "mode": "DEEP" if args.deep else "HEAD_ONLY",
            "current_key": CURRENT_KEY,
            "error": str(exc),
        }
        print(json.dumps(report, sort_keys=True))
        return 1
    except Exception:
        # Never print provider exceptions: they may contain request details or
        # credentials.  The verifier's contract is one safe JSON report.
        report = {
            "status": "FAIL",
            "mode": "DEEP" if args.deep else "HEAD_ONLY",
            "current_key": CURRENT_KEY,
            "error": "verification failed",
        }
        print(json.dumps(report, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
