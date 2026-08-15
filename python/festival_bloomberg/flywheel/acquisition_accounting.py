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

UNITS ARE NEVER MIXED. A run row carries TWO explicit units:

    HTTP level (one row per provider interaction):
        http_requests / http_successful_responses / http_rate_limited /
        http_failures

    TASK level (one row per hunt task attempt):
        tasks_attempted / tasks_claim_found / tasks_not_found plus the
        migration-018 failure counters (rate_limited, http_failed,
        parser_failed, rights_blocked, auth_failed, other_failure) which are
        ALL keyed by hunt-attempt status.

Task counts are never used as HTTP response counts and vice versa. A request
count is NEVER estimated from returned row counts: when it cannot be measured
``http_requests`` is NULL and ``request_count_status`` = 'UNKNOWN'.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

ACCOUNTING_OBJECTIVE_VERSION = "data_acquisition_activation_v1"

#: TASK-level failure counters on the run row, keyed by hunt attempt status.
FAILURE_COUNTERS = (
    "rights_blocked",
    "rate_limited",
    "parser_failed",
    "http_failed",
    "auth_failed",
    "other_failure",
)

REQUEST_COUNT_MEASURED = "MEASURED"
REQUEST_COUNT_UNKNOWN = "UNKNOWN"


def build_acquisition_run_row(
    *,
    provider: str,
    pipeline: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    http_requests: int | None = None,
    http_successful_responses: int = 0,
    http_rate_limited: int = 0,
    http_failures: int = 0,
    request_count_status: str | None = None,
    tasks_attempted: int = 0,
    tasks_claim_found: int = 0,
    tasks_not_found: int = 0,
    records_parsed: int = 0,
    new_claims: int = 0,
    new_unique_events_improved: int = 0,
    new_cutoffs: int = 0,
    new_warm_start_events: int = 0,
    new_forward_observations: int = 0,
    new_ticket_pace_events: int = 0,
    duplicates: int = 0,
    conflicts: int = 0,
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
    """Build a per-provider acquisition run row (pure).

    ``http_requests=None`` means the request count was NOT measured (e.g. an
    offline fixture); it is stored as NULL with ``request_count_status`` =
    'UNKNOWN' — never estimated from returned row counts. The migration-018
    ``requests`` / ``successful_responses`` columns mirror the HTTP-level
    counters; ``not_found`` mirrors the task-level ``tasks_not_found``.
    """
    started = started_at or utc_now()
    finished = finished_at or utc_now()
    run_id = f"acq_{provider}_{started.strftime('%Y%m%dT%H%M%S%f')}"
    if request_count_status is None:
        request_count_status = (
            REQUEST_COUNT_MEASURED if http_requests is not None else REQUEST_COUNT_UNKNOWN
        )
    return {
        "run_id": run_id,
        "provider": provider,
        "pipeline": pipeline,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        # HTTP level (and 018 mirror columns).
        "http_requests": http_requests,
        "requests": http_requests,
        "http_successful_responses": http_successful_responses,
        "successful_responses": http_successful_responses,
        "http_rate_limited": http_rate_limited,
        "http_failures": http_failures,
        "request_count_status": request_count_status,
        # TASK level.
        "tasks_attempted": tasks_attempted,
        "tasks_claim_found": tasks_claim_found,
        "tasks_not_found": tasks_not_found,
        "not_found": tasks_not_found,
        "rights_blocked": rights_blocked,
        "rate_limited": rate_limited,
        "parser_failed": parser_failed,
        "http_failed": http_failed,
        "auth_failed": auth_failed,
        "other_failure": other_failure,
        # Evidence counters.
        "records_parsed": records_parsed,
        "new_claims": new_claims,
        "new_unique_events_improved": new_unique_events_improved,
        "new_cutoffs": new_cutoffs,
        "new_warm_start_events": new_warm_start_events,
        "new_forward_observations": new_forward_observations,
        "new_ticket_pace_events": new_ticket_pace_events,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "latency_ms_total": latency_ms_total,
        "quota_consumed": quota_consumed,
        "monetary_cost_usd": monetary_cost_usd,
        "detail": detail,
    }


def derive_metrics(run: dict[str, Any], *, as_of: datetime | None = None) -> dict[str, Any]:
    """Derive yield metrics from one run row (pure, never invented).

    Every rate declares its denominator explicitly. HTTP-denominator rates
    divide by ``http_requests``; task-denominator rates divide by
    ``tasks_attempted``. Numerators are NEVER cross-unit: task counts never
    enter an HTTP-denominator numerator and vice versa. When the denominator
    is zero/unmeasured, the rate is None (never fabricated).
    """
    now = as_of or utc_now()
    http_requests = run.get("http_requests")
    http_requests = http_requests if isinstance(http_requests, int) else None
    tasks_attempted = run.get("tasks_attempted") or 0
    http_scale = 1000.0 / http_requests if http_requests else 0.0
    task_scale = 1000.0 / tasks_attempted if tasks_attempted else 0.0

    def _per_1000_http(counter: str) -> float | None:
        if not http_requests:
            return None
        return round((run.get(counter) or 0) * http_scale, 4)

    def _per_1000_tasks(counter: str) -> float | None:
        if not tasks_attempted:
            return None
        return round((run.get(counter) or 0) * task_scale, 4)

    def _cost_per(counter: str) -> float | None:
        count = run.get(counter) or 0
        if count <= 0:
            return None
        return round((run.get("monetary_cost_usd") or 0.0) / count, 6)

    usable = (run.get("new_unique_events_improved") or 0) + (
        run.get("new_cutoffs") or 0
    )
    http_successes = run.get("http_successful_responses") or 0
    return {
        "metric_id": f"acqm_{run['run_id']}",
        "run_id": run["run_id"],
        "provider": run["provider"],
        # HTTP-denominator rates (legacy names kept; numerators are HTTP-level).
        "successes_per_1000_requests": _per_1000_http("http_successful_responses"),
        "new_claims_per_1000_requests": _per_1000_http("new_claims"),
        "new_cutoffs_per_1000_requests": _per_1000_http("new_cutoffs"),
        "new_usable_events_per_1000_requests": (
            round(usable * http_scale, 4) if http_requests else None
        ),
        "new_warm_starts_per_1000_requests": _per_1000_http("new_warm_start_events"),
        # New explicit-denominator metrics (migration 019).
        "http_success_rate": (
            round(http_successes / http_requests, 6) if http_requests else None
        ),
        "claims_per_1000_http_requests": _per_1000_http("new_claims"),
        "claims_per_1000_tasks_attempted": _per_1000_tasks("new_claims"),
        "new_events_per_1000_http_requests": _per_1000_http("new_unique_events_improved"),
        "cost_per_new_claim": _cost_per("new_claims"),
        "cost_per_new_usable_event": _cost_per("new_unique_events_improved"),
        "cost_per_new_warm_start": _cost_per("new_warm_start_events"),
        "knowledge_time": now.isoformat(),
    }
