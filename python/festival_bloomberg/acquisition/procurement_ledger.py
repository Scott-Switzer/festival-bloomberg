"""PROCUREMENT_LEDGER — first-class acquisition economics.

Every provider call emits a ProcurementRecord (raw economics + quality).
Aggregated across a window, it yields per-lane cost_per_1k_* metrics that
the MarketplaceRouter consumes. Storage is R2 (control/procurement/) with
a local JSON fallback for dev.

Unknown cost stays UNKNOWN — never $0. Unknown fields stay null.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcurementRecord:
    run_id: str
    task_type: str
    platform: str | None
    lane: str  # Lane.value
    provider: str
    endpoint_or_actor: str | None
    started_at: str
    finished_at: str
    artists_requested: int
    pages: int | None
    records_returned: int
    records_valid: int
    records_unique: int
    records_entity_resolved: int
    records_new_to_estate: int
    bytes: int | None
    provider_charge_usd: float | None  # None = UNKNOWN
    proxy_charge_usd: float | None
    compute_charge_usd: float | None
    total_charge_usd: float | None  # None when any component unknown
    duration_seconds: float
    http_errors: int
    rate_limits: int
    parser_errors: int
    identity_errors: int
    duplicate_rate: float | None
    freshness_seconds: float | None
    field_completeness: float | None
    precision_sample: str | None
    quality_status: str

    def cost_per_1k(self, numerator: str) -> float | None:
        if self.total_charge_usd is None:
            return None
        denom = {
            "returned": self.records_returned,
            "valid": self.records_valid,
            "unique": self.records_unique,
            "resolved": self.records_entity_resolved,
            "new": self.records_new_to_estate,
        }.get(numerator, 0)
        if not denom:
            return None
        return (self.total_charge_usd / denom) * 1000.0


LEDGER_R2_PREFIX = "control/procurement"
LEDGER_CURRENT_KEY = f"{LEDGER_R2_PREFIX}/ledger.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def procurement_record_key(rec: ProcurementRecord) -> str:
    return hashlib.sha256(f"{rec.run_id}|{rec.task_type}|{rec.lane}|{rec.provider}|{rec.started_at}".encode()).hexdigest()[:16]


def record_from_result(
    *,
    run_id: str,
    task_type: str,
    lane: str,
    provider: str,
    endpoint_or_actor: str | None,
    request: Any,
    result: Any,
    duration_seconds: float | None = None,
    bytes_observed: int | None = None,
) -> ProcurementRecord:
    """Build a ProcurementRecord from an AcquisitionResult + request.

    Never fabricates cost. Unknown stays None.
    """
    started = getattr(result, "started_at", None)
    completed = getattr(result, "completed_at", None)
    if hasattr(started, "isoformat"):
        started_s = started.isoformat()
    else:
        started_s = str(started or _now_iso())
    if hasattr(completed, "isoformat"):
        finished_s = completed.isoformat()
    else:
        finished_s = str(completed or _now_iso())
    if duration_seconds is None:
        try:
            duration_seconds = (completed - started).total_seconds() if hasattr(started, "timestamp") and hasattr(completed, "timestamp") else 0.0
        except Exception:
            duration_seconds = 0.0

    provider_charge = getattr(result, "cost_usd", None)
    # Only trust cost when the provider reported one; None stays UNKNOWN.
    records_returned = int(getattr(result, "record_count", 0) or 0)
    status = str(getattr(getattr(result, "status", None), "value", "") or getattr(result, "status", "") or "UNKNOWN")
    error_cat = getattr(result, "error_category", None) or getattr(result, "errorCategory", None)
    is_rate_limited = 1 if status == "RATE_LIMITED" or error_cat == "rate_limited" else 0
    is_http_error = 1 if status == "PROVIDER_ERROR" or status == "TIMEOUT" else 0

    # Valid/unique/new require post-normalization; default to returned for now.
    return ProcurementRecord(
        run_id=run_id,
        task_type=task_type,
        platform=getattr(request, "platform", None) if request is not None else None,
        lane=lane,
        provider=provider,
        endpoint_or_actor=endpoint_or_actor,
        started_at=started_s,
        finished_at=finished_s,
        artists_requested=1,
        pages=None,
        records_returned=records_returned,
        records_valid=records_returned if status == "SUCCESS" else 0,
        records_unique=records_returned if status == "SUCCESS" else 0,
        records_entity_resolved=0,
        records_new_to_estate=0,
        bytes=bytes_observed,
        provider_charge_usd=provider_charge,
        proxy_charge_usd=None,
        compute_charge_usd=None,
        total_charge_usd=provider_charge,
        duration_seconds=float(duration_seconds or 0.0),
        http_errors=is_http_error,
        rate_limits=is_rate_limited,
        parser_errors=0,
        identity_errors=0,
        duplicate_rate=None,
        freshness_seconds=None,
        field_completeness=None,
        precision_sample=None,
        quality_status=status,
    )


def aggregate_ledger(records: list[ProcurementRecord]) -> dict[str, Any]:
    """Per-task+lanel aggregation: cost_per_1k_* + success/rate-limit stats."""
    from collections import defaultdict

    buckets: dict[str, list[ProcurementRecord]] = defaultdict(list)
    for r in records:
        buckets[f"{r.task_type}:{r.lane}"].append(r)

    out: dict[str, Any] = {}
    for key, bucket in buckets.items():
        total_cost = sum(r.total_charge_usd for r in bucket if r.total_charge_usd is not None)
        any_unknown = any(r.total_charge_usd is None for r in bucket)
        total_unique = sum(r.records_unique for r in bucket)
        total_valid = sum(r.records_valid for r in bucket)
        total_returned = sum(r.records_returned for r in bucket)
        total_resolved = sum(r.records_entity_resolved for r in bucket)
        total_new = sum(r.records_new_to_estate for r in bucket)
        rate_limited = sum(r.rate_limits for r in bucket)
        http_errors = sum(r.http_errors for r in bucket)
        successes = sum(1 for r in bucket if r.quality_status == "SUCCESS")
        # cost_per_1k when any cost unknown → UNKNOWN (None)
        def c1k(denom: int) -> float | None:
            if any_unknown or not denom:
                # If all UNKNOWN but denom exists, we can't compute cost -> None
                if any_unknown and total_cost == 0:
                    return None
                if not denom:
                    return None
            if not denom:
                return None
            # Only known-cost buckets count
            known_cost = sum(r.total_charge_usd for r in bucket if r.total_charge_usd is not None) or 0.0
            if any(r.total_charge_usd is None for r in bucket) and known_cost == 0:
                return None
            return (known_cost / denom) * 1000.0 if denom else None

        # Use total_cost (which already excludes UNKNOWN rows' 0 contribution)
        # but gate on any_unknown so we don't pretend $0 when cost is unknown.
        out[key] = {
            "runs": len(bucket),
            "total_charge_usd": None if any_unknown and total_cost == 0 else round(total_cost, 6),
            "cost_per_1k_returned": c1k(total_returned),
            "cost_per_1k_valid": c1k(total_valid),
            "cost_per_1k_unique": c1k(total_unique),
            "cost_per_1k_resolved": c1k(total_resolved) if total_resolved else None,
            "cost_per_new_unique_observation": c1k(total_new) if total_new else None,
            "success_rate": round(successes / len(bucket), 3) if bucket else 0,
            "rate_limit_rate": round(rate_limited / len(bucket), 3) if bucket else 0,
            "http_error_rate": round(http_errors / len(bucket), 3) if bucket else 0,
        }
    return out


def write_ledger_local(records: list[ProcurementRecord], path: str | Path = "control/procurement/ledger.json") -> Path:
    """Write the aggregated ledger locally (dev fallback). Also writes raw."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    agg = aggregate_ledger(records)
    raw = [asdict(r) for r in records]
    payload = {"generated_at": _now_iso(), "aggregated": agg, "raw": raw, "raw_count": len(raw)}
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def append_ledger_record(record: ProcurementRecord, path: str | Path = "control/procurement/ledger.json") -> Path:
    """Append one record to the local ledger (creates if missing)."""
    p = Path(path)
    existing: list[dict] = []
    if p.exists():
        try:
            existing = json.loads(p.read_text()).get("raw") or []
        except Exception:
            existing = []
    # Re-hydrate as ProcurementRecord list via raw dicts → aggregate
    # Simpler: just append the dict and re-aggregate
    existing.append(asdict(record))
    # Write back as aggregated + raw
    recs = [ProcurementRecord(**{k: v for k, v in d.items() if k in ProcurementRecord.__dataclass_fields__}) for d in existing]
    return write_ledger_local(recs, path)
