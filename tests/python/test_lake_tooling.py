"""Offline invariants for the R2 lake control-plane tooling."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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


def test_full_scan_checkpoint_geometry_fails_closed_for_legacy_and_mismatch():
    scan = _load_full_scan()
    legacy = {
        "source_dataset": scan.SOURCE_DATASET,
        "completed_batches": [[0, 6]],
        "completed_affinity_partitions": [],
        "batch_size_shards": scan.BATCH_SHARDS,
    }
    with pytest.raises(RuntimeError, match="listener_hash_partitions"):
        scan.validate_checkpoint(legacy, partitions=64)

    valid = {
        **legacy,
        "listener_hash_partitions": 64,
        "dump_version": scan.DUMP_VERSION,
    }
    scan.validate_checkpoint(valid, partitions=64)
    with pytest.raises(RuntimeError, match="uses 64"):
        scan.validate_checkpoint(valid, partitions=256)

    with pytest.raises(RuntimeError, match="incomplete"):
        scan.ensure_map_complete({"source_shard_count": 2, "completed_shards": [0]})
    scan.ensure_map_complete({"source_shard_count": 2, "completed_shards": [0, 1]})


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
