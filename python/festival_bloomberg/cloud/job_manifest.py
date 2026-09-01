"""
Job Manifest Contract — defines the manifest schema and statuses for cloud
batch data-processing jobs.

Every heavy job gets a manifest written to R2 at:
    control/jobs/<job_type>/<job_id>/manifest.json

Status flow:
    PLANNED → RUNNING → BUILD_COMPLETE → VERIFIED → PUBLISHED
                    ↘ FAILED

VERIFIED requires verifying every output object: exists, size reconciles,
SHA-256 matches recorded digest.  Only after VERIFIED may the CURRENT
pointer move and status become PUBLISHED.

A manifest is the authoritative record of what ran, against what inputs,
what it produced, and whether those outputs are published.

A manifest is the authoritative record of what ran, against what inputs,
what it produced, and whether those outputs are published.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

STATUS_PLANNED = "PLANNED"
STATUS_RUNNING = "RUNNING"
STATUS_BUILD_COMPLETE = "BUILD_COMPLETE"
STATUS_VERIFIED = "VERIFIED"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_FAILED = "FAILED"
STATUS_SUPERSEDED = "SUPERSEDED_NONCANONICAL"

VALID_STATUSES = frozenset({
    STATUS_PLANNED, STATUS_RUNNING, STATUS_BUILD_COMPLETE,
    STATUS_VERIFIED, STATUS_PUBLISHED, STATUS_FAILED, STATUS_SUPERSEDED,
})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_key(job_type: str, job_id: str) -> str:
    """Deterministic R2 key for a job manifest."""
    return f"control/jobs/{job_type}/{job_id}/manifest.json"


@dataclass
class JobManifest:
    """Canonical manifest for a batch data-processing job."""
    schema_version: int = 1
    job_id: str = ""
    job_type: str = ""
    code_commit: str = ""
    container_image: str = ""
    source_generation: str = ""
    source_paths: list[str] = field(default_factory=list)
    status: str = STATUS_PLANNED
    started_at: str = ""
    completed_at: str = ""
    completed_batches: int = 0
    failed_batches: int = 0
    total_batches: int = 0
    bytes_read: int = 0
    rows_read: int = 0
    rows_written: int = 0
    output_paths: list[str] = field(default_factory=list)
    output_hashes: dict[str, str] = field(default_factory=dict)
    runtime_seconds: float = 0.0
    peak_rss_bytes: int = 0
    r2_read_bytes: int = 0
    r2_write_bytes: int = 0
    scratch_peak_bytes: int = 0
    error: str | None = None
    error_detail: str | None = None
    # V1B: fixed machine-readable error code (never raw exception text).
    error_code: str | None = None
    publication_state: str = "UNPUBLISHED"
    params: dict[str, Any] = field(default_factory=dict)
    # Verification metadata — filled during the VERIFIED transition.
    verified_at: str | None = None
    verified_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str)

    def checksum(self) -> str:
        """Deterministic checksum of the manifest content."""
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def new_manifest(
    job_type: str, job_id: str, *,
    source_generation: str = "",
    code_commit: str = "",
    container_image: str = "",
    source_paths: list[str] | None = None,
    total_batches: int = 0,
    params: dict | None = None,
) -> JobManifest:
    """Create a new job manifest."""
    return JobManifest(
        job_id=job_id,
        job_type=job_type,
        code_commit=code_commit,
        container_image=container_image,
        source_generation=source_generation,
        source_paths=source_paths or [],
        total_batches=total_batches,
        started_at=now_iso(),
        status=STATUS_RUNNING,
        params=params or {},
    )
