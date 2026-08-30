"""Offline invariants for the R2 lake control-plane tooling."""

from __future__ import annotations

import importlib.util
import io
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from festival_bloomberg.lake import catalog


def _load_full_scan():
    path = Path(__file__).parents[2] / "scripts" / "lb_full_scan.py"
    spec = importlib.util.spec_from_file_location("lb_full_scan_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_refresh_is_atomic_and_rejects_invalid_counts(tmp_path, monkeypatch):
    target = tmp_path / "control" / "data_catalog" / "current.json"
    monkeypatch.setattr(catalog, "CATALOG_PATH", target)
    monkeypatch.setattr(catalog, "build_commit", lambda: "test")

    saved = catalog.save_catalog({"catalog_version": 1, "datasets": {}})
    assert target.exists()
    assert target.stat().st_mode & 0o777 == 0o644
    assert saved["build_commit"] == "test"
    assert catalog.load_catalog()["catalog_version"] == 1

    with pytest.raises(ValueError, match="byte_count"):
        catalog.register_dataset(
            dataset_id="silver.bad",
            dataset_version="v1",
            layer="SILVER",
            source="test",
            source_version="v1",
            r2_bucket="lake",
            r2_prefix="x",
            fmt="parquet",
            schema_version="v1",
            byte_count=-1,
            verification_status="TEST",
            license="CC0-1.0",
            rights_status="TEST",
            commercial_use_status="TEST",
        )


def test_catalog_invalid_json_fails_closed(tmp_path, monkeypatch):
    target = tmp_path / "current.json"
    target.write_text("{")
    monkeypatch.setattr(catalog, "CATALOG_PATH", target)
    with pytest.raises(RuntimeError, match="not valid JSON"):
        catalog.load_catalog()


def test_catalog_generation_batch_validates_before_atomic_replace(tmp_path, monkeypatch):
    target = tmp_path / "current.json"
    monkeypatch.setattr(catalog, "CATALOG_PATH", target)
    monkeypatch.setattr(catalog, "build_commit", lambda: "test")
    catalog.save_catalog({
        "catalog_version": 1,
        "datasets": {"existing": {"dataset_id": "existing"}},
    })

    def registration(dataset_id: str, *, byte_count: int = 1):
        return {
            "dataset_id": dataset_id,
            "dataset_version": "v1",
            "layer": "SILVER",
            "source": "test",
            "source_version": "v1",
            "r2_bucket": "lake",
            "r2_prefix": dataset_id,
            "fmt": "parquet",
            "schema_version": "v1",
            "byte_count": byte_count,
            "verification_status": "TEST",
            "license": "CC0-1.0",
            "rights_status": "TEST",
            "commercial_use_status": "TEST",
        }

    before = target.read_bytes()
    with pytest.raises(ValueError, match="byte_count"):
        catalog.register_dataset_batch([
            registration("silver.good"),
            registration("silver.bad", byte_count=-1),
        ])
    assert target.read_bytes() == before

    entries = catalog.register_dataset_batch([
        registration("silver.one"), registration("silver.two")
    ])
    assert [entry["dataset_id"] for entry in entries] == ["silver.one", "silver.two"]
    assert set(catalog.load_catalog()["datasets"]) == {
        "existing", "silver.one", "silver.two"
    }
    assert catalog.load_catalog()["datasets"]["silver.one"]["serving_eligible"] is False
    with pytest.raises(PermissionError, match="not eligible"):
        catalog.dataset_for_serving("silver.one")

    public = registration("serving.public")
    public.update({"layer": "SERVING", "serving_eligible": True,
                   "access_classification": "PUBLIC"})
    catalog.register_dataset_batch([public])
    assert catalog.dataset_for_serving("serving.public")["dataset_id"] == "serving.public"
    bad = registration("serving.bad")
    bad.update({"serving_eligible": True, "access_classification": "RESTRICTED"})
    with pytest.raises(ValueError, match="only PUBLIC"):
        catalog.register_dataset_batch([bad])


def test_full_scan_checkpoint_geometry_fails_closed_for_legacy_and_mismatch():
    scan = _load_full_scan()
    legacy = {
        "source_dataset": scan.SOURCE_DATASET,
        "completed_batches": [[0, 6]],
        "completed_affinity_partitions": [],
        "batch_size_shards": scan.BATCH_SHARDS,
    }
    with pytest.raises(RuntimeError, match="pipeline_version"):
        scan.validate_checkpoint(legacy, partitions=64)

    valid = {
        **legacy,
        "pipeline_version": scan.PIPELINE_VERSION,
        "listener_hash_partitions": 64,
        "dump_version": scan.DUMP_VERSION,
        "source_key": scan.RAW_KEY,
        "source_bytes": scan.TOTAL_SOURCE_BYTES,
        "map_target_shards": 2,
        "run_namespace": scan.scan_namespace(64, 2),
        "source_shard_count": 2,
        "completed_batches": [],
    }
    scan.validate_checkpoint(valid, partitions=64)
    with pytest.raises(RuntimeError, match="uses 64"):
        scan.validate_checkpoint(valid, partitions=256)

    incomplete = {**valid, "completed_shards": [0]}
    with pytest.raises(RuntimeError, match="membership is invalid"):
        scan.ensure_map_complete(incomplete)
    wrong_members = {**valid, "completed_shards": [0, 2]}
    with pytest.raises(RuntimeError, match="extra=1"):
        scan.ensure_map_complete(wrong_members)
    scan.ensure_map_complete({**valid, "completed_shards": [0, 1]})


def test_full_scan_namespace_isolated_by_target_and_partitions():
    scan = _load_full_scan()
    assert scan.scan_namespace(256, 76) != scan.scan_namespace(256, 1526)
    assert scan.scan_namespace(64, 76) != scan.scan_namespace(256, 76)
    assert scan.scan_namespace(
        256, 76, tar_index_sha256="a" * 64, artist_universe_sha256="b" * 64
    ) != scan.scan_namespace(
        256, 76, tar_index_sha256="a" * 64, artist_universe_sha256="c" * 64
    )


def test_full_scan_resource_guards_fail_closed(monkeypatch):
    scan = _load_full_scan()
    monkeypatch.setattr(
        scan.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=scan.MIN_FREE_DISK_BYTES - 1),
    )
    with pytest.raises(RuntimeError, match="insufficient free disk"):
        scan.require_free_disk()

    monkeypatch.setattr(
        scan.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="python scripts/build_wikidata_music_graph.py --parallel-decompress\n"
        ),
    )
    with pytest.raises(RuntimeError, match="build_wikidata_music_graph.py"):
        scan.require_no_competing_heavy_job()


def test_full_scan_exclusive_lock_rejects_a_second_pipeline(tmp_path, monkeypatch):
    scan = _load_full_scan()
    monkeypatch.setattr(scan, "RUN_LOCK", tmp_path / "run.lock")
    with scan.exclusive_run_lock("map") as owner:
        assert owner["command"] == "map"
        with pytest.raises(RuntimeError, match="another ListenBrainz"):
            with scan.exclusive_run_lock("reduce-pairs"):
                pass


def test_full_scan_binds_exact_source_identity():
    scan = _load_full_scan()

    class FakeS3:
        def __init__(self, size, etag):
            self.size = size
            self.etag = etag

        def head_object(self, **_kwargs):
            return {"ContentLength": self.size, "ETag": f'"{self.etag}"'}

    ckpt = {"source_etag": None}
    scan.bind_source_object(FakeS3(scan.TOTAL_SOURCE_BYTES, "etag-1"), ckpt)
    assert ckpt["source_etag"] == "etag-1"
    with pytest.raises(RuntimeError, match="size changed"):
        scan.bind_source_object(FakeS3(scan.TOTAL_SOURCE_BYTES - 1, "etag-1"), ckpt)
    with pytest.raises(RuntimeError, match="ETag changed"):
        scan.bind_source_object(FakeS3(scan.TOTAL_SOURCE_BYTES, "etag-2"), ckpt)
    with pytest.raises(RuntimeError, match="no ETag"):
        scan.bind_source_object(FakeS3(scan.TOTAL_SOURCE_BYTES, ""), {"source_etag": None})


def test_full_scan_binds_index_and_universe_digests(tmp_path):
    scan = _load_full_scan()
    source = tmp_path / "input.json"
    source.write_text('{"version": 1}\n')
    ckpt = {}
    digest = scan.bind_local_input_digest(ckpt, "input_sha256", source)
    assert ckpt["input_sha256"] == digest
    source.write_text('{"version": 2}\n')
    with pytest.raises(RuntimeError, match="changed since"):
        scan.bind_local_input_digest(ckpt, "input_sha256", source)


def test_full_scan_tar_index_geometry_is_validated():
    scan = _load_full_scan()
    members = [
        {"name": "dump/0.parquet", "offset": 512, "size": 100},
        {"name": "dump/1.parquet", "offset": 1024, "size": 100},
    ]
    assert [m["name"] for m in scan.validate_tar_index(members)] == [
        "dump/0.parquet", "dump/1.parquet"
    ]
    with pytest.raises(RuntimeError, match="overlap"):
        scan.validate_tar_index([
            {"name": "dump/0.parquet", "offset": 512, "size": 700},
            {"name": "dump/1.parquet", "offset": 1024, "size": 100},
        ])
    with pytest.raises(RuntimeError, match="contiguous"):
        scan.validate_tar_index([
            {"name": "dump/0.parquet", "offset": 512, "size": 100},
            {"name": "dump/2.parquet", "offset": 1024, "size": 100},
        ])


def test_full_scan_committed_manifests_are_reducer_authority():
    scan = _load_full_scan()
    ad_key = f"silver/listenbrainz/_partial/{scan.scan_namespace(2, 2)}/artist_day/batch_0.parquet"
    la_key = (
        f"{scan.PRIVATE_PARTIAL_ROOT}/{scan.scan_namespace(2, 2)}/"
        "listener_artist/part=0/batch_0.parquet"
    )
    artifacts = [
        {"key": ad_key, "bytes": 3, "sha256": "a" * 64},
        {"bucket": scan.PRIVATE_BUCKET, "key": la_key, "bytes": 4,
         "sha256": "b" * 64},
    ]
    ckpt = {
        "source_dataset": scan.SOURCE_DATASET,
        "pipeline_version": scan.PIPELINE_VERSION,
        "dump_version": scan.DUMP_VERSION,
        "source_key": scan.RAW_KEY,
        "source_bytes": scan.TOTAL_SOURCE_BYTES,
        "listener_hash_partitions": 2,
        "batch_size_shards": scan.BATCH_SHARDS,
        "run_namespace": scan.scan_namespace(2, 2),
        "source_shard_count": 2,
        "map_target_shards": 2,
        "completed_shards": [0, 1],
        "completed_batches": [[0, 1]],
        "completed_affinity_partitions": [],
        "duckdb_version": scan.duckdb.__version__,
        "listener_partition_algorithm": "DUCKDB_HASH_V1",
        "batch_artifacts": {"0": artifacts},
        "batch_partition_coverage": {"0": {"0": True, "1": False}},
    }

    class FakeS3:
        def head_object(self, **kwargs):
            artifact = next(a for a in artifacts if a["key"] == kwargs["Key"])
            return {
                "ContentLength": artifact["bytes"],
                "Metadata": {"sha256": artifact["sha256"]},
            }

    assert scan.committed_map_artifacts(FakeS3(), ckpt, "artist_day") == [artifacts[0]]
    assert scan.committed_map_artifacts(FakeS3(), ckpt, "listener_artist") == [artifacts[1]]
    assert scan.committed_listener_artifacts(FakeS3(), ckpt) == [artifacts[1]]
    bad_coverage = {**ckpt, "batch_partition_coverage": {"0": {"0": False, "1": False}}}
    with pytest.raises(RuntimeError, match="disagree with coverage"):
        scan.committed_listener_artifacts(FakeS3(), bad_coverage)
    with pytest.raises(RuntimeError, match="batch artifact manifests"):
        scan.committed_map_artifacts(FakeS3(), {**ckpt, "batch_artifacts": {}}, "artist_day")


def test_full_scan_download_verifies_manifest_sha256(tmp_path):
    scan = _load_full_scan()
    payload = b"verified artifact"
    digest = hashlib.sha256(payload).hexdigest()
    artifact = {"key": "x.parquet", "bytes": len(payload), "sha256": digest}

    class FakeS3:
        def get_object(self, **_kwargs):
            return {
                "Body": io.BytesIO(payload),
                "ContentLength": len(payload),
                "Metadata": {"sha256": digest},
            }

    local = tmp_path / "x.parquet"
    assert scan.download(FakeS3(), artifact["key"], local, artifact) == len(payload)
    assert local.read_bytes() == payload
    with pytest.raises(RuntimeError, match="hash metadata"):
        scan.download(
            FakeS3(), artifact["key"], local,
            {**artifact, "sha256": "0" * 64},
        )

    private_artifact = {
        **artifact,
        "bucket": scan.PRIVATE_BUCKET,
        "key": f"{scan.PRIVATE_PARTIAL_ROOT}/run/part=0/batch_0.parquet",
    }
    with pytest.raises(PermissionError, match="reducer-only"):
        scan.download(FakeS3(), private_artifact["key"], local, private_artifact)
    assert scan.download(
        FakeS3(), private_artifact["key"], local, private_artifact,
        private_access=scan.PRIVATE_REDUCER_ACCESS,
    ) == len(payload)


def test_full_scan_checkpoint_remote_failure_is_explicit(tmp_path, monkeypatch):
    scan = _load_full_scan()
    checkpoint = tmp_path / "checkpoint" / "current.json"
    monkeypatch.setattr(scan, "CHECKPOINT", checkpoint)

    class FailingS3:
        def put_object(self, **_kwargs):
            raise OSError("remote unavailable")

    with pytest.raises(RuntimeError, match="remote resume state is stale"):
        scan.save_checkpoint(FailingS3(), {"pipeline": "test"})
    assert json.loads(checkpoint.read_text())["pipeline"] == "test"
    assert not list(checkpoint.parent.glob(".checkpoint.*.json"))


def test_full_scan_resolves_every_distinct_canonical_artist_in_credit():
    scan = _load_full_scan()
    universe = {
        "m1": {"key": "artist-b", "tier": "CORE"},
        "m2": {"key": "artist-a", "tier": "CORE"},
        "m3": {"key": "artist-a", "tier": "CORE"},
    }
    assert [row["key"] for row in scan.resolve_credit(["m1", "m2", "m3"], universe)] == [
        "artist-a",
        "artist-b",
    ]
    assert scan.resolve_credit([], universe) == []


def test_full_scan_affinity_sums_fragments_before_global_top_k():
    scan = _load_full_scan()
    con = duckdb.connect()
    con.execute("CREATE TABLE la(listener_key VARCHAR, artist_key VARCHAR, listen_count BIGINT)")
    con.executemany(
        "INSERT INTO la VALUES (?, ?, ?)",
        [
            ("u", "A", 4),
            ("u", "B", 3),
            ("u", "A", 4),
            ("u", "C", 5),
        ],
    )
    scan.materialize_affinity_partition(con, top_k=2)
    assert con.execute(
        "SELECT artist_key, listen_count FROM bounded ORDER BY artist_key"
    ).fetchall() == [("A", 8), ("C", 5)]
    assert con.execute("""
        SELECT a.artist_key, b.artist_key, COUNT(*)
        FROM bounded a JOIN bounded b
          ON a.listener_key = b.listener_key AND a.artist_key < b.artist_key
        GROUP BY 1, 2
    """).fetchall() == [("A", "C", 1)]


def test_full_scan_pair_support_is_applied_after_global_union():
    scan = _load_full_scan()
    con = duckdb.connect()
    con.execute("CREATE TABLE pair_input(a VARCHAR, b VARCHAR, sh BIGINT)")
    con.executemany("INSERT INTO pair_input VALUES ('A', 'B', ?)", [(1,), (1,), (1,)])
    con.execute("CREATE TABLE node_input(a VARCHAR, listeners BIGINT)")
    con.executemany("INSERT INTO node_input VALUES (?, ?)", [("A", 1), ("A", 1), ("A", 1), ("B", 1), ("B", 1), ("B", 1)])
    con.execute("CREATE TABLE population_input(listener_count BIGINT)")
    con.executemany("INSERT INTO population_input VALUES (?)", [(1,), (1,), (1,)])
    scan.materialize_global_affinity(con, minimum_shared=3)
    assert con.execute("SELECT * FROM pairs").fetchall() == [("A", "B", 3)]
    assert con.execute("SELECT * FROM nodes ORDER BY artist_key").fetchall() == [
        ("A", 3),
        ("B", 3),
    ]
    assert con.execute("SELECT listener_count FROM population").fetchone() == (3,)


def test_full_scan_artist_day_merges_additive_counts_without_false_uniques():
    scan = _load_full_scan()
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE ad(
          artist_key VARCHAR,
          obs_day DATE,
          listen_count BIGINT,
          unique_listeners BIGINT,
          unique_recordings BIGINT
        )
    """)
    con.execute("""
        INSERT INTO ad VALUES
          ('A', DATE '2026-01-01', 4, 3, 2),
          ('A', DATE '2026-01-01', 5, 3, 3)
    """)
    scan.materialize_artist_day_global(con)
    assert con.execute("""
        SELECT listen_count, unique_listeners, unique_recordings,
               batch_unique_listener_sum, batch_unique_recording_sum,
               distinct_count_status
        FROM ad_global
    """).fetchone() == (9, None, None, 6, 5, "UNKNOWN_ACROSS_BATCHES")


def test_full_scan_data_shards_excludes_tar_metadata():
    scan = _load_full_scan()
    members = [
        {"name": "root/SCHEMA_SEQUENCE", "offset": 0, "size": 1},
        {"name": "root/0.parquet", "offset": 0, "size": 1},
        {"name": "root/3.parquet", "offset": 0, "size": 1},
        {"name": "root/nope.parquet", "offset": 0, "size": 1},
    ]
    assert [m["name"] for m in scan.data_shards(members)] == [
        "root/0.parquet",
        "root/3.parquet",
    ]
