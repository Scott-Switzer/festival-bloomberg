"""EXTERNAL_WORKER_LEASE_PROTOCOL_V1 + DIRECT_R2_DATA_PLANE for Hetzner workers.

Cloudflare Queues are not directly consumable by Hetzner machines, so the
Cloudflare control plane exposes a signed lease endpoint. Hetzner workers
are outbound-only (no public ingress) — they ask for work, execute, upload
evidence to R2, and callback. Cloudflare remains the governor; Hetzner is
only an execution lane.

Lease token reuses the existing HMAC conventions (FI_BATCH_HMAC_SECRET /
FI_LISTENER_HMAC_SECRET, SHA-256) so a single secret rotation covers both
batch containers and Hetzner workers. Nothing in this module logs a secret.

The R2 data plane uses scoped, request-level access: workers receive only
the bucket/key set enumerated in their leased task payload, never a full
bucket credential dump. Large inputs are streamed/partitioned — no worker
is expected to hold the entire 191GB ListenBrainz corpus locally.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from ..localenv import load_local_env

load_local_env()

LEASE_VERSION = "hetzner-lease-v1"
LEASE_TTL_SECONDS = 900  # 15 min — matches Cloudflare Queue visibility timeout families
MAX_LEASE_ATTEMPTS = 3


def _hmac_key() -> bytes:
    for name in ("FI_BATCH_HMAC_SECRET", "FI_LISTENER_HMAC_SECRET"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v.encode()
    return b""


def sign_lease(worker_id: str, job_id: str, lease_expiry: str, lease_id: str) -> str:
    """HMAC-SHA256 over worker:job:expiry:lease_id. Empty if no secret configured."""
    key = _hmac_key()
    if not key:
        return ""
    msg = f"{worker_id}:{job_id}:{lease_expiry}:{lease_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_lease(worker_id: str, job_id: str, lease_expiry: str, lease_id: str, token: str) -> bool:
    expected = sign_lease(worker_id, job_id, lease_expiry, lease_id)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, token)


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LeaseRequest:
    worker_id: str
    capabilities: tuple[str, ...]  # e.g. ("scraper", "playwright", "duckdb", "embed")
    git_sha: str | None = None
    ttl_seconds: int = LEASE_TTL_SECONDS


@dataclass(frozen=True)
class LeaseGrant:
    lease_id: str
    worker_id: str
    job_id: str
    job_type: str
    lease_expiry: str
    task_payload: dict[str, Any]
    token: str  # HMAC for callback auth
    # Scoped R2 access for this lease only — never a full credential.
    r2_inputs: tuple[dict[str, str], ...] = ()  # [{bucket, key}]
    r2_outputs: tuple[dict[str, str], ...] = ()  # [{bucket, key_prefix}]


@dataclass
class LeaseLedger:
    """In-memory lease state — durable truth is R2 control/leases/."""

    leases: dict[str, LeaseGrant] = field(default_factory=dict)

    def grant(self, request: LeaseRequest, job: dict[str, Any]) -> LeaseGrant:
        import uuid
        lease_id = f"lease_{uuid.uuid4().hex[:12]}"
        job_id = str(job.get("job_id") or job.get("id") or lease_id)
        job_type = str(job.get("job_type") or "hetzner_task")
        from datetime import datetime, timezone, timedelta
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=request.ttl_seconds)).isoformat()
        token = sign_lease(request.worker_id, job_id, expiry, lease_id)
        grant = LeaseGrant(
            lease_id=lease_id, worker_id=request.worker_id,
            job_id=job_id, job_type=job_type, lease_expiry=expiry,
            task_payload=job, token=token,
            r2_inputs=tuple(job.get("r2_inputs") or ()),
            r2_outputs=tuple(job.get("r2_outputs") or ()),
        )
        self.leases[lease_id] = grant
        return grant

    def verify_callback(self, lease_id: str, worker_id: str, token: str) -> bool:
        grant = self.leases.get(lease_id)
        if not grant or grant.worker_id != worker_id:
            return False
        return verify_lease(worker_id, grant.job_id, grant.lease_expiry, lease_id, token)


# ── Direct R2 data plane helper ─────────────────────────────────────

@dataclass(frozen=True)
class R2TransferSpec:
    bucket: str
    key: str
    local_path: str | None = None  # when None, streaming is expected
    expected_sha256: str | None = None
    content_type: str = "application/octet-stream"


def r2_transfer_summary(
    inputs: list[R2TransferSpec],
    outputs: list[R2TransferSpec],
    bytes_in: int = 0,
    bytes_out: int = 0,
) -> dict[str, Any]:
    """Structured summary for the procurement ledger — no secrets, just counts."""
    return {
        "inputs": len(inputs),
        "outputs": len(outputs),
        "bytes_in": bytes_in,
        "bytes_out": bytes_out,
        "streaming": any(s.local_path is None for s in inputs),
        "version": LEASE_VERSION,
    }
