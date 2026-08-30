"""Canonical dataset registry for the R2 data lake.

Layer contract:
    RAW      festival-intelligence-raw/bulk/<source>/dump=<version>/
    BRONZE   festival-intelligence-lake/bronze/<dataset>/
    SILVER   festival-intelligence-lake/silver/<dataset>/
    GOLD     festival-intelligence-lake/gold/<dataset>/
    SERVING  festival-intelligence-lake/serving/<version>/

Every dataset (raw corpus or derived product) registers exactly one entry in
control/data_catalog/current.json. Registration is honest by construction:
row counts and byte counts come from what was actually written/verified.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

CATALOG_PATH = Path("control/data_catalog/current.json")
LAYERS = ("RAW", "BRONZE", "SILVER", "GOLD", "SERVING")
ACCESS_CLASSIFICATIONS = ("PUBLIC", "INTERNAL", "RESTRICTED", "QUARANTINED")


def build_commit() -> str:
    """Short SHA of the current HEAD, or 'unknown' when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def load_catalog() -> dict[str, Any]:
    if CATALOG_PATH.exists():
        try:
            return json.loads(CATALOG_PATH.read_text())
        except Exception as exc:
            raise RuntimeError(f"catalog is not valid JSON: {CATALOG_PATH}") from exc
    return {"catalog_version": 1, "updated_at": None, "datasets": {}}


def save_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    catalog["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    catalog["build_commit"] = build_commit()
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The catalog is the control-plane index for immutable R2 objects.  Do not
    # leave a truncated JSON file behind if the process is interrupted while
    # refreshing it.
    payload = json.dumps(catalog, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".current.", suffix=".json", dir=CATALOG_PATH.parent)
    try:
        # mkstemp intentionally creates mode 0600. The catalog contains no
        # credentials and is shared with read-only local product processes,
        # so retain the repository's normal world-readable data-file mode.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, CATALOG_PATH)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return catalog


def _prepare_dataset_entry(
    *,
    dataset_id: str,
    dataset_version: str,
    layer: str,
    source: str,
    source_version: str,
    r2_bucket: str,
    r2_prefix: str,
    fmt: str,
    schema_version: str,
    row_count: int | None = None,
    byte_count: int | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
    source_checksum: str | None = None,
    artifact_checksum: str | None = None,
    verification_status: str,
    license: str,
    rights_status: str,
    commercial_use_status: str,
    serving_eligible: bool = False,
    access_classification: str = "INTERNAL",
    upstream_dataset_ids: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Validate one registration request and build its stored entry."""
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {LAYERS}, got {layer!r}")
    if not dataset_id.strip():
        raise ValueError("dataset_id must not be empty")
    if not dataset_version.strip():
        raise ValueError("dataset_version must not be empty")
    if row_count is not None and row_count < 0:
        raise ValueError("row_count must be non-negative or None")
    if byte_count is not None and byte_count < 0:
        raise ValueError("byte_count must be non-negative or None")
    if type(serving_eligible) is not bool:
        raise ValueError("serving_eligible must be a boolean")
    if access_classification not in ACCESS_CLASSIFICATIONS:
        raise ValueError(
            f"access_classification must be one of {ACCESS_CLASSIFICATIONS}"
        )
    if serving_eligible and access_classification != "PUBLIC":
        raise ValueError("only PUBLIC datasets can be serving eligible")
    entry: dict[str, Any] = {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "layer": layer,
        "source": source,
        "source_version": source_version,
        "r2_bucket": r2_bucket,
        "r2_prefix": r2_prefix,
        "format": fmt,
        "schema_version": schema_version,
        "row_count": row_count,
        "byte_count": byte_count,
        "date_min": date_min,
        "date_max": date_max,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_checksum": source_checksum,
        "artifact_checksum": artifact_checksum,
        "verification_status": verification_status,
        "license": license,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "serving_eligible": serving_eligible,
        "access_classification": access_classification,
        "upstream_dataset_ids": upstream_dataset_ids or [],
        "build_commit": build_commit(),
    }
    if notes:
        entry["notes"] = notes
    return entry


def register_dataset_batch(
    registrations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and atomically register a complete dataset generation.

    Every request is validated before the catalog is changed. The catalog is
    then replaced once, so readers cannot observe a subset of the generation.
    """
    entries = [_prepare_dataset_entry(**registration) for registration in registrations]
    if len({entry["dataset_id"] for entry in entries}) != len(entries):
        raise ValueError("dataset_id values in one registration batch must be unique")
    catalog = load_catalog()
    datasets = catalog.setdefault("datasets", {})
    for entry in entries:
        datasets[entry["dataset_id"]] = entry
    save_catalog(catalog)
    return entries


def register_dataset(
    *,
    dataset_id: str,
    dataset_version: str,
    layer: str,
    source: str,
    source_version: str,
    r2_bucket: str,
    r2_prefix: str,
    fmt: str,
    schema_version: str,
    row_count: int | None = None,
    byte_count: int | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
    source_checksum: str | None = None,
    artifact_checksum: str | None = None,
    verification_status: str,
    license: str,
    rights_status: str,
    commercial_use_status: str,
    serving_eligible: bool = False,
    access_classification: str = "INTERNAL",
    upstream_dataset_ids: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Register (or refresh) one dataset entry. Returns the stored entry."""
    return register_dataset_batch([{
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "layer": layer,
        "source": source,
        "source_version": source_version,
        "r2_bucket": r2_bucket,
        "r2_prefix": r2_prefix,
        "fmt": fmt,
        "schema_version": schema_version,
        "row_count": row_count,
        "byte_count": byte_count,
        "date_min": date_min,
        "date_max": date_max,
        "source_checksum": source_checksum,
        "artifact_checksum": artifact_checksum,
        "verification_status": verification_status,
        "license": license,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "serving_eligible": serving_eligible,
        "access_classification": access_classification,
        "upstream_dataset_ids": upstream_dataset_ids,
        "notes": notes,
    }])[0]


def dataset_for_serving(dataset_id: str) -> dict[str, Any]:
    """Return one catalog entry only when the authoritative policy allows it."""
    entry = (load_catalog().get("datasets") or {}).get(dataset_id)
    if entry is None:
        raise KeyError(dataset_id)
    if not entry.get("serving_eligible") or entry.get("access_classification") != "PUBLIC":
        raise PermissionError(f"dataset {dataset_id!r} is not eligible for serving")
    return entry


def catalog_summary() -> dict[str, Any]:
    catalog = load_catalog()
    datasets = catalog.get("datasets", {})
    by_layer: dict[str, int] = {}
    bytes_by_layer: dict[str, int] = {}
    for entry in datasets.values():
        layer = entry.get("layer", "?")
        by_layer[layer] = by_layer.get(layer, 0) + 1
        bytes_by_layer[layer] = bytes_by_layer.get(layer, 0) + (entry.get("byte_count") or 0)
    return {
        "dataset_count": len(datasets),
        "datasets_by_layer": by_layer,
        "bytes_by_layer": bytes_by_layer,
        "updated_at": catalog.get("updated_at"),
    }
