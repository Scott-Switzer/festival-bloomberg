from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_wikidata_generation.py"
SPEC = importlib.util.spec_from_file_location("verify_wikidata_generation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _Body:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeS3:
    def __init__(self, objects: dict[str, bytes], metadata: dict[str, dict[str, str]] | None = None):
        self.objects = objects
        self.metadata = metadata or {}
        self.head_sizes: dict[str, int] = {}
        self.gets: list[str] = []
        self.bodies: list[_Body] = []

    def head_object(self, *, Bucket: str, Key: str):
        if Bucket != MODULE.LAKE_BUCKET or Key not in self.objects:
            raise KeyError(Key)
        data = self.objects[Key]
        return {
            "ContentLength": self.head_sizes.get(Key, len(data)),
            "Metadata": self.metadata.get(Key, {"sha256": hashlib.sha256(data).hexdigest()}),
        }

    def get_object(self, *, Bucket: str, Key: str):
        if Bucket != MODULE.LAKE_BUCKET or Key not in self.objects:
            raise KeyError(Key)
        self.gets.append(Key)
        body = _Body(self.objects[Key])
        self.bodies.append(body)
        return {"Body": body}

    def put_object(self, **_kwargs):
        raise AssertionError("verifier must not write R2")

    def delete_object(self, **_kwargs):
        raise AssertionError("verifier must not delete R2")

    def upload_fileobj(self, *args, **kwargs):
        raise AssertionError("verifier must not upload to R2")


def _json(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _fixture(*, artifact_status: str = "ARTIFACTS_VERIFIED") -> tuple[_FakeS3, str, list[str]]:
    run_id = "fixture-run"
    prefix = f"silver/wikidata/generations/{run_id}/"
    knowledge_time = "2026-08-30T00:00:00Z"
    objects: dict[str, bytes] = {}
    artifact_entries = []
    artifact_keys = []
    for name, filename in MODULE.EXPECTED_FILES.items():
        schema = MODULE.OUTPUT_SCHEMAS[name]
        arrays = []
        for field in schema:
            if pa.types.is_floating(field.type):
                arrays.append(pa.array([0.0], type=field.type))
            elif field.name == "source_system":
                arrays.append(pa.array(["wikidata"], type=field.type))
            elif field.name in {"knowledge_time", "ingested_at"}:
                arrays.append(pa.array([knowledge_time], type=field.type))
            else:
                arrays.append(pa.array([field.name], type=field.type))
        table = pa.Table.from_arrays(arrays, schema=schema)
        sink = io.BytesIO()
        pq.write_table(table, sink, compression="zstd")
        artifact_data = sink.getvalue()
        artifact_sha = hashlib.sha256(artifact_data).hexdigest()
        artifact_key = f"{prefix}{filename}"
        artifact_keys.append(artifact_key)
        objects[artifact_key] = artifact_data
        artifact_entries.append({
            "name": name,
            "dataset_id": MODULE.EXPECTED_DATASET_IDS[name],
            "r2_key": artifact_key,
            "row_count": 1,
            "byte_count": len(artifact_data),
            "sha256": artifact_sha,
            "schema": str(schema),
        })
    source = {
        "bucket": MODULE.RAW_BUCKET,
        "key": MODULE.RAW_KEY,
        "bytes": MODULE.RAW_BYTES,
        "etag": MODULE.RAW_ETAG,
    }
    artifacts_manifest = {
        "schema_version": "wikidata-silver-artifacts-v1",
        "run_id": run_id,
        "dump_version": "latest-truthy-20260828",
        "source": source,
        "knowledge_time": knowledge_time,
        "status": artifact_status,
        "artifacts": artifact_entries,
    }
    artifacts_data = _json(artifacts_manifest)
    artifacts_key = f"{prefix}artifacts.json"
    artifacts_sha = hashlib.sha256(artifacts_data).hexdigest()
    manifest = {
        "schema_version": "wikidata-silver-generation-v1",
        "run_id": run_id,
        "dump_version": "latest-truthy-20260828",
        "source": source,
        "knowledge_time": knowledge_time,
        "status": "PUBLISHED",
        "artifacts_manifest_key": artifacts_key,
        "artifacts_manifest_sha256": artifacts_sha,
        "artifact_count": len(artifact_entries),
    }
    manifest_data = _json(manifest)
    manifest_key = f"{prefix}manifest.json"
    manifest_sha = hashlib.sha256(manifest_data).hexdigest()
    current = {
        "schema_version": "wikidata-silver-current-v1",
        "run_id": run_id,
        "dump_version": "latest-truthy-20260828",
        "manifest_key": manifest_key,
        "manifest_sha256": manifest_sha,
        "published_at": "2026-08-30T00:00:00Z",
        "publication_authority": True,
    }
    current_data = _json(current)
    current_key = MODULE.CURRENT_KEY
    objects[current_key] = current_data
    objects[manifest_key] = manifest_data
    objects[artifacts_key] = artifacts_data
    return _FakeS3(objects), current_key, artifact_keys


def _relink_manifests(fake: _FakeS3) -> None:
    """Refresh fixture links after mutating the artifacts manifest."""
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    fake.objects[artifacts_key] = _json(json.loads(fake.objects[artifacts_key]))
    manifest_key = next(k for k in fake.objects if k.endswith("manifest.json"))
    manifest = json.loads(fake.objects[manifest_key])
    manifest["artifacts_manifest_sha256"] = hashlib.sha256(
        fake.objects[artifacts_key]
    ).hexdigest()
    fake.objects[manifest_key] = _json(manifest)
    current = json.loads(fake.objects[MODULE.CURRENT_KEY])
    current["manifest_sha256"] = hashlib.sha256(fake.objects[manifest_key]).hexdigest()
    fake.objects[MODULE.CURRENT_KEY] = _json(current)


def _parquet_bytes(name: str, overrides: dict[str, str] | None = None) -> bytes:
    schema = MODULE.OUTPUT_SCHEMAS[name]
    overrides = overrides or {}
    arrays = []
    for field in schema:
        if pa.types.is_floating(field.type):
            value = 0.0
        elif field.name == "source_system":
            value = "wikidata"
        elif field.name in {"knowledge_time", "ingested_at"}:
            value = "2026-08-30T00:00:00Z"
        else:
            value = field.name
        arrays.append(pa.array([overrides.get(field.name, value)], type=field.type))
    table = pa.Table.from_arrays(arrays, schema=schema)
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue()


def test_success_is_head_only_by_default_and_reports_verified_totals():
    fake, _, artifact_keys = _fixture()
    report = MODULE.verify_generation(fake)
    assert report["status"] == "PASS_METADATA_ONLY"
    assert report["verification_strength"] == "METADATA_ONLY"
    assert report["mode"] == "HEAD_ONLY"
    assert report["artifact_count"] == len(MODULE.EXPECTED_FILES)
    assert report["total_rows"] == len(MODULE.EXPECTED_FILES)
    assert report["total_bytes"] == sum(len(fake.objects[k]) for k in artifact_keys)
    assert not set(artifact_keys) & set(fake.gets)
    assert all(body.closed for body in fake.bodies)


def test_json_exact_content_length_boundary_is_accepted():
    fake, _, _ = _fixture()
    max_json = max(
        len(data) for key, data in fake.objects.items() if key.endswith(".json")
    )
    report = MODULE.verify_generation(fake, max_json_bytes=max_json)
    assert report["status"] == "PASS_METADATA_ONLY"


def test_json_trailing_byte_is_rejected_with_stale_head():
    fake, _, _ = _fixture()
    key = MODULE.CURRENT_KEY
    original = fake.objects[key]
    fake.objects[key] = original + b"x"
    fake.head_sizes[key] = len(original)
    fake.metadata[key] = {"sha256": hashlib.sha256(original).hexdigest()}
    with pytest.raises(MODULE.VerificationError, match="trailing data"):
        MODULE.verify_generation(fake)
    assert fake.bodies and fake.bodies[0].closed


@pytest.mark.parametrize("published_at", [None, "", "not-a-timestamp", "2026-99-99T00:00:00Z"])
def test_current_published_at_must_be_valid_utc(published_at):
    fake, _, _ = _fixture()
    current = json.loads(fake.objects[MODULE.CURRENT_KEY])
    if published_at is None:
        current.pop("published_at")
    else:
        current["published_at"] = published_at
    fake.objects[MODULE.CURRENT_KEY] = _json(current)
    with pytest.raises(MODULE.VerificationError, match="CURRENT published_at"):
        MODULE.verify_generation(fake)


def test_current_published_at_must_match_generation_knowledge_time():
    fake, _, _ = _fixture()
    current = json.loads(fake.objects[MODULE.CURRENT_KEY])
    current["published_at"] = "2026-08-30T00:00:01Z"
    fake.objects[MODULE.CURRENT_KEY] = _json(current)
    with pytest.raises(MODULE.VerificationError, match="CURRENT published_at mismatch"):
        MODULE.verify_generation(fake)


def test_deep_success_verifies_parquet_and_uses_bounded_temp_file():
    fake, _, artifact_keys = _fixture()
    report = MODULE.verify_generation(fake, deep=True, max_temp_bytes=1 << 20)
    assert report["status"] == "PASS"
    assert report["verification_strength"] == "DEEP"
    assert set(artifact_keys) <= set(fake.gets)
    assert all(body.closed for body in fake.bodies)


def test_deep_artifact_trailing_byte_is_rejected_with_stale_head():
    fake, _, artifact_keys = _fixture()
    key = artifact_keys[0]
    original = fake.objects[key]
    fake.objects[key] = original + b"x"
    fake.head_sizes[key] = len(original)
    fake.metadata[key] = {"sha256": hashlib.sha256(original).hexdigest()}
    with pytest.raises(MODULE.VerificationError, match="trailing data"):
        MODULE.verify_generation(fake, deep=True, max_temp_bytes=len(original) + 1)
    assert all(body.closed for body in fake.bodies)


def test_deep_decoded_memory_cap_is_enforced_from_metadata():
    fake, _, _ = _fixture()
    with pytest.raises(MODULE.VerificationError, match="decoded memory cap"):
        MODULE.verify_generation(fake, deep=True, max_decoded_batch_bytes=1)


def test_decoded_memory_bound_must_be_positive():
    fake, _, _ = _fixture()
    with pytest.raises(MODULE.VerificationError, match="decoded batch memory bound"):
        MODULE.verify_generation(fake, max_decoded_batch_bytes=0)


def test_missing_current_fails_without_legacy_fallback():
    with pytest.raises(MODULE.VerificationError, match="unavailable"):
        MODULE.verify_generation(_FakeS3({}))


def test_oversized_json_is_rejected_before_body_read():
    fake, _, _ = _fixture()
    with pytest.raises(MODULE.VerificationError, match="exceeds configured bound"):
        MODULE.verify_generation(fake, max_json_bytes=1)
    assert fake.gets == []


class _GetFailureS3(_FakeS3):
    def get_object(self, **_kwargs):
        raise RuntimeError("provider secret should not escape")


class _ReadFailureBody(_Body):
    def read(self, size: int = -1) -> bytes:
        raise RuntimeError("provider secret should not escape")


class _CloseFailureBody(_Body):
    def close(self) -> None:
        raise RuntimeError("provider secret should not escape")


class _ReadFailureS3(_FakeS3):
    def get_object(self, *, Bucket: str, Key: str):
        if Bucket != MODULE.LAKE_BUCKET or Key not in self.objects:
            raise KeyError(Key)
        body = _ReadFailureBody(self.objects[Key])
        self.bodies.append(body)
        return {"Body": body}


class _CloseFailureS3(_FakeS3):
    def get_object(self, *, Bucket: str, Key: str):
        if Bucket != MODULE.LAKE_BUCKET or Key not in self.objects:
            raise KeyError(Key)
        body = _CloseFailureBody(self.objects[Key])
        self.bodies.append(body)
        return {"Body": body}


def test_provider_get_read_and_close_exceptions_are_safe_verification_errors():
    fake, _, _ = _fixture()
    with pytest.raises(MODULE.VerificationError, match="R2 object read failed") as get_error:
        MODULE.verify_generation(_GetFailureS3(fake.objects))
    assert "secret" not in str(get_error.value)
    with pytest.raises(MODULE.VerificationError, match="R2 JSON read failed") as read_error:
        MODULE.verify_generation(_ReadFailureS3(fake.objects))
    assert "secret" not in str(read_error.value)
    with pytest.raises(MODULE.VerificationError, match="R2 JSON body close failed") as close_error:
        MODULE.verify_generation(_CloseFailureS3(fake.objects))
    assert "secret" not in str(close_error.value)


def test_missing_artifact_fails_closed():
    fake, _, _ = _fixture()
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    payload["artifacts"].pop()
    manifest_key = next(k for k in fake.objects if k.endswith("manifest.json"))
    manifest = json.loads(fake.objects[manifest_key])
    manifest["artifact_count"] = len(payload["artifacts"])
    fake.objects[manifest_key] = _json(manifest)
    _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="name set mismatch"):
        MODULE.verify_generation(fake)


def test_wrong_source_identity_fails_closed():
    fake, _, _ = _fixture()
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    payload["source"]["bucket"] = "wrong-bucket"
    _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="source identity mismatch"):
        MODULE.verify_generation(fake)


@pytest.mark.parametrize("knowledge_time", ["", "not-a-timestamp", "2026-99-99T00:00:00Z"])
def test_empty_or_invalid_artifacts_knowledge_time_fails_closed(knowledge_time: str):
    fake, _, _ = _fixture()
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    payload["knowledge_time"] = knowledge_time
    _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="knowledge_time"):
        MODULE.verify_generation(fake)


def test_unexpected_artifact_fails_closed():
    fake, _, _ = _fixture()
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    payload["artifacts"][0]["name"] = "unexpected"
    _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="name set mismatch"):
        MODULE.verify_generation(fake)


def test_wrong_dataset_id_fails_closed():
    fake, _, _ = _fixture()
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    payload["artifacts"][0]["dataset_id"] = "silver.not_wikidata"
    _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="dataset_id mismatch"):
        MODULE.verify_generation(fake)


def test_dump_version_mismatch_fails_closed():
    fake, _, _ = _fixture()
    current = json.loads(fake.objects[MODULE.CURRENT_KEY])
    current["dump_version"] = "different-dump"
    fake.objects[MODULE.CURRENT_KEY] = _json(current)
    with pytest.raises(MODULE.VerificationError, match="approved generator version"):
        MODULE.verify_generation(fake)


def test_dump_version_must_be_the_approved_generator_version():
    fake, _, _ = _fixture()
    current = json.loads(fake.objects[MODULE.CURRENT_KEY])
    current["dump_version"] = "other-version"
    fake.objects[MODULE.CURRENT_KEY] = _json(current)
    manifest_key = next(k for k in fake.objects if k.endswith("manifest.json"))
    manifest = json.loads(fake.objects[manifest_key])
    manifest["dump_version"] = "other-version"
    fake.objects[manifest_key] = _json(manifest)
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    artifacts = json.loads(fake.objects[artifacts_key])
    artifacts["dump_version"] = "other-version"
    fake.objects[artifacts_key] = _json(artifacts)
    _relink_manifests(fake)
    current = json.loads(fake.objects[MODULE.CURRENT_KEY])
    current["dump_version"] = "other-version"
    fake.objects[MODULE.CURRENT_KEY] = _json(current)
    with pytest.raises(MODULE.VerificationError, match="approved generator version"):
        MODULE.verify_generation(fake)


def test_artifacts_manifest_dump_version_mismatch_fails_closed():
    fake, _, _ = _fixture()
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    payload["dump_version"] = "different-dump"
    _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="artifacts manifest dump_version mismatch"):
        MODULE.verify_generation(fake)


def test_generation_manifest_knowledge_time_is_required_and_valid():
    fake, _, _ = _fixture()
    manifest_key = next(k for k in fake.objects if k.endswith("manifest.json"))
    payload = json.loads(fake.objects[manifest_key])
    payload.pop("knowledge_time")
    _replace_generation_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="generation manifest knowledge_time"):
        MODULE.verify_generation(fake)


def test_generation_and_artifacts_knowledge_times_must_match():
    fake, _, _ = _fixture()
    manifest_key = next(k for k in fake.objects if k.endswith("manifest.json"))
    payload = json.loads(fake.objects[manifest_key])
    payload["knowledge_time"] = "2026-08-30T00:00:01Z"
    _replace_generation_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="manifest knowledge_time mismatch"):
        MODULE.verify_generation(fake)


def test_deep_schema_field_mismatch_fails_closed():
    fake, _, artifact_keys = _fixture()
    artifact_key = artifact_keys[0]
    wrong_schema = pa.schema([
        pa.field("qid", pa.string()),
        pa.field("classification", pa.string()),
        pa.field("source_system", pa.string()),
        pa.field("knowledge_time", pa.string()),
    ])
    table = pa.Table.from_arrays(
        [pa.array([field.name], type=field.type) for field in wrong_schema],
        schema=wrong_schema,
    )
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    _replace_artifact(fake, "music_entities", sink.getvalue())
    with pytest.raises(MODULE.VerificationError, match="schema mismatch"):
        MODULE.verify_generation(fake, deep=True, max_temp_bytes=1 << 20)


def test_deep_manifest_schema_field_mismatch_fails_closed():
    fake, _, _ = _fixture()
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    payload["artifacts"][0]["schema"] = "wrong schema"
    _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError, match="manifest schema mismatch"):
        MODULE.verify_generation(fake, deep=True, max_temp_bytes=1 << 20)


def test_deep_wrong_source_system_row_fails_closed():
    fake, _, _ = _fixture()
    _replace_artifact(fake, "music_entities", _parquet_bytes(
        "music_entities", {"source_system": "other-source"}
    ))
    with pytest.raises(MODULE.VerificationError, match="source_system provenance"):
        MODULE.verify_generation(fake, deep=True, max_temp_bytes=1 << 20)


def test_deep_mismatched_row_timestamp_fails_closed():
    fake, _, _ = _fixture()
    _replace_artifact(fake, "music_entities", _parquet_bytes(
        "music_entities", {"ingested_at": "2026-08-30T00:00:01Z"}
    ))
    with pytest.raises(MODULE.VerificationError, match="ingested_at provenance"):
        MODULE.verify_generation(fake, deep=True, max_temp_bytes=1 << 20)


def test_deep_corrupt_parquet_pages_fail_closed():
    fake, _, _ = _fixture()
    _replace_artifact(fake, "music_entities", b"not a parquet file")
    with pytest.raises(MODULE.VerificationError, match="not valid Parquet"):
        MODULE.verify_generation(fake, deep=True, max_temp_bytes=1 << 20)


@pytest.mark.parametrize("mismatch", ["hash", "size", "status"])
def test_hash_size_and_status_mismatches_fail(mismatch: str):
    fake, _, artifact_keys = _fixture()
    artifact_key = artifact_keys[0]
    if mismatch == "hash":
        fake.metadata[artifact_key] = {"sha256": "0" * 64}
    elif mismatch == "size":
        fake.objects[artifact_key] += b"x"
    else:
        artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
        payload = json.loads(fake.objects[artifacts_key])
        payload["status"] = "PUBLISHED"
        _replace_artifacts_manifest(fake, payload)
    with pytest.raises(MODULE.VerificationError):
        MODULE.verify_generation(fake)


def _replace_artifacts_manifest(fake: _FakeS3, payload: dict) -> None:
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    fake.objects[artifacts_key] = _json(payload)
    _relink_manifests(fake)


def _replace_generation_manifest(fake: _FakeS3, payload: dict) -> None:
    manifest_key = next(k for k in fake.objects if k.endswith("manifest.json"))
    fake.objects[manifest_key] = _json(payload)
    current = json.loads(fake.objects[MODULE.CURRENT_KEY])
    current["manifest_sha256"] = hashlib.sha256(fake.objects[manifest_key]).hexdigest()
    fake.objects[MODULE.CURRENT_KEY] = _json(current)


def _replace_artifact(fake: _FakeS3, name: str, data: bytes) -> None:
    key = next(
        k for k in fake.objects
        if k.endswith(MODULE.EXPECTED_FILES[name])
    )
    fake.objects[key] = data
    artifacts_key = next(k for k in fake.objects if k.endswith("artifacts.json"))
    payload = json.loads(fake.objects[artifacts_key])
    artifact = next(item for item in payload["artifacts"] if item["name"] == name)
    artifact["byte_count"] = len(data)
    artifact["sha256"] = hashlib.sha256(data).hexdigest()
    _replace_artifacts_manifest(fake, payload)
