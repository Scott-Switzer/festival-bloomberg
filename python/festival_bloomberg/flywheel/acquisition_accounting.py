"""TRACK E — acquisition economics.

Per-provider acquisition accounting + DERIVED yield metrics. The point is to
answer "where should the next acquisition dollar/request/minute go?" — not to
invent a composite provider score. Every metric row is derived from a
persisted run row; nothing is hand-entered. Definitions are explicit:

    new_claim                     source assertion not previously present
    new_unique_event_improved     event gained >= 1 previously unavailable
                                  decision-useful field
    new_cutoff                    previously unknown decision cutoff now
                                  supported by qualifying evidence
    new_warm_start_event          target event newly eligible for the chosen
                                  PIT warm-start criterion
    new_ticket_pace_event         event newly reaches >= 2 temporally distinct
                                  ticket observations
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

ACCOUNTING_OBJECTIVE_VERSION = "data_acquisition_activation_v1"

#: Failure counters on the run row, keyed by hunt attempt status.
FAILURE_COUNTERS = (
    "rights_blocked",
    "rate_limited",
    "parser_failed",
    "http_failed",
    "auth_failed",
    "other_failure",
)


def build_acquisition_run_row(
    *,
    provider: str,
    pipeline: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    requests: int = 0,
    successful_responses: int = 0,
    records_parsed: int = 0,
    new_claims: int = 0,
    new_unique_events_improved: int = 0,
    new_cutoffs: int = 0,
    new_warm_start_events: int = 0,
    new_forward_observations: int = 0,
    new_ticket_pace_events: int = 0,
    duplicates: int = 0,
    conflicts: int = 0,
    not_found: int = 0,
    rights_blocked: int = 0,
    rate_limited: int = 0,
    parser_failed: int = 0,
    http_failed: int = 0,
    auth_failed: int = 0,
    other_failure: int = 0,
    latency_ms_total: int = 0,
    quota_consumed: int = 0,
    monetary_cost_usd: float = 0.0,
    detail: str | None = None,
) -> dict[str, Any]:
    started = started_at or utc_now()
    finished = finished_at or utc_now()
    run_id = f"acq_{provider}_{started.strftime('%Y%m%dT%H%M%S%f')}"
    return {
        "run_id": run_id,
        "provider": provider,
        "pipeline": pipeline,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "requests": requests,
        "successful_responses": successful_responses,
        "records_parsed": records_parsed,
        "new_claims": new_claims,
        "new_unique_events_improved": new_unique_events_improved,
        "new_cutoffs": new_cutoffs,
        "new_warm_start_events": new_warm_start_events,
        "new_forward_observations": new_forward_observations,
        "new_ticket_pace_events": new_ticket_pace_events,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "not_found": not_found,
        "rights_blocked": rights_blocked,
        "rate_limited": rate_limited,
        "parser_failed": parser_failed,
        "http_failed": http_failed,
        "auth_failed": auth_failed,
        "other_failure": other_failure,
        "latency_ms_total": latency_ms_total,
        "quota_consumed": quota_consumed,
        "monetary_cost_usd": monetary_cost_usd,
        "detail": detail,
    }


def derive_metrics(run: dict[str, Any], *, as_of: datetime | None = None) -> dict[str, Any]:
    """Derive yield metrics from one run row (pure, never invented).

    All rates are per 1,000 requests; cost rates divide by the corresponding
    new-evidence counter (None when the denominator is zero).
    """
    now = as_of or utc_now()
    requests = run.get("requests") or 0
    scale = 1000.0 / requests if requests > 0 else 0.0

    def _per_1000(counter: str) -> float | None:
        return round((run.get(counter) or 0) * scale, 4) if requests > 0 else None

    def _cost_per(counter: str) -> float | None:
        count = run.get(counter) or 0
        if count <= 0:
            return None
        return round((run.get("monetary_cost_usd") or 0.0) / count, 6)

    usable = (run.get("new_unique_events_improved") or 0) + (
        run.get("new_cutoffs") or 0
    )
    return {
        "metric_id": f"acqm_{run['run_id']}",
        "run_id": run["run_id"],
        "provider": run["provider"],
        "successes_per_1000_requests": _per_1000("successful_responses"),
        "new_claims_per_1000_requests": _per_1000("new_claims"),
        "new_cutoffs_per_1000_requests": _per_1000("new_cutoffs"),
        "new_usable_events_per_1000_requests": (
            round(usable * scale, 4) if requests > 0 else None
        ),
        "new_warm_starts_per_1000_requests": _per_1000("new_warm_start_events"),
        "cost_per_new_claim": _cost_per("new_claims"),
        "cost_per_new_usable_event": _cost_per("new_unique_events_improved"),
        "cost_per_new_warm_start": _cost_per("new_warm_start_events"),
        "knowledge_time": now.isoformat(),
    }
