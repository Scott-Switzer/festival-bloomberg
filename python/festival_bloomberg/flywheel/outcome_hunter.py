"""OUTCOME_HUNTER — claims-based outcome acquisition.

For every known historical performance, the hunter searches permitted sources
for explicit evidence of the memo's target fields:

    attendance | paid_tickets | gross | sellout | capacity | ticket_price
    | promoter | tour | announcement | onsale | show_count

It produces CLAIMS, never silently resolved single values: conflicting
observations coexist in ``economics.event_outcome_claims`` and reconciliation
happens later. Attribute evidence (promoter, tour, announcement, onsale,
show_count) is recorded on hunt tasks / decision cutoffs — it is not forced
into the outcome taxonomy, which stays controlled and fail-closed.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..economics.outcome_claims import (
    EXPLICIT_NOT_SOLD_OUT_ASSERTION,
    EXPLICIT_SOLD_OUT_ASSERTION,
    EVENT_USABLE_CAPACITY,
    OBSERVED_PUBLIC,
    PAID_TICKETS,
    PRIMARY_FACE_VALUE_MAX,
    PRIMARY_FACE_VALUE_MIN,
    REPORTED_ATTENDANCE,
    TICKET_GROSS,
    VENUE_CAPACITY,
    OutcomeClaim,
)

#: The memo's target fields, in order.
HUNT_TARGET_FIELDS: tuple[str, ...] = (
    "attendance",
    "paid_tickets",
    "gross",
    "sellout",
    "capacity",
    "ticket_price",
    "promoter",
    "tour",
    "announcement",
    "onsale",
    "show_count",
)

#: Fields whose findings become outcome claims in the controlled taxonomy.
OUTCOME_FIELDS = frozenset(
    {"attendance", "paid_tickets", "gross", "sellout", "capacity", "ticket_price"}
)

#: Fields that carry attribute evidence only (no numeric outcome claim).
ATTRIBUTE_FIELDS = frozenset({"promoter", "tour", "announcement", "onsale", "show_count"})

TASK_PENDING = "PENDING"
TASK_SEARCHING = "SEARCHING"
TASK_CLAIM_FOUND = "CLAIM_FOUND"
TASK_NOT_FOUND = "NOT_FOUND"
TASK_BLOCKED = "BLOCKED"
TASK_STATUSES = frozenset(
    {TASK_PENDING, TASK_SEARCHING, TASK_CLAIM_FOUND, TASK_NOT_FOUND, TASK_BLOCKED}
)

#: Allowed hunt-task transitions (fail closed on anything else).
HUNT_TRANSITIONS: dict[str, frozenset[str]] = {
    TASK_PENDING: frozenset({TASK_SEARCHING, TASK_BLOCKED}),
    TASK_SEARCHING: frozenset({TASK_CLAIM_FOUND, TASK_NOT_FOUND, TASK_BLOCKED}),
    TASK_CLAIM_FOUND: frozenset(),
    TASK_NOT_FOUND: frozenset({TASK_BLOCKED}),
    TASK_BLOCKED: frozenset(),
}

PLAN_PLANNED = "PLANNED"
PLAN_IN_PROGRESS = "IN_PROGRESS"
PLAN_COMPLETE = "COMPLETE"
PLAN_BLOCKED = "BLOCKED"


def validate_task_status(status: str) -> str:
    if status not in TASK_STATUSES:
        raise ValueError(f"hunt task status {status!r} is invalid")
    return status


def hunt_status_allowed(from_status: str, to_status: str) -> bool:
    validate_task_status(from_status)
    validate_task_status(to_status)
    return to_status in HUNT_TRANSITIONS[from_status]


def target_field_outcome_types(field: str) -> tuple[str, ...]:
    """Map a target field to controlled outcome types (empty for attributes)."""
    mapping: dict[str, tuple[str, ...]] = {
        "attendance": (REPORTED_ATTENDANCE,),
        "paid_tickets": (PAID_TICKETS,),
        "gross": (TICKET_GROSS,),
        "sellout": (EXPLICIT_SOLD_OUT_ASSERTION, EXPLICIT_NOT_SOLD_OUT_ASSERTION),
        "capacity": (VENUE_CAPACITY, EVENT_USABLE_CAPACITY),
        "ticket_price": (PRIMARY_FACE_VALUE_MIN, PRIMARY_FACE_VALUE_MAX),
    }
    return mapping.get(field, ())


def validate_target_field(field: str) -> str:
    if field not in HUNT_TARGET_FIELDS:
        raise ValueError(f"target_field {field!r} is not a huntable field")
    return field


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------
def event_key(engagement: dict[str, Any]) -> str:
    """Deterministic per-engagement canonical event id (same convention as the
    research corpus promotion, so hunt claims join the same events)."""
    def slug(value: Any) -> str:
        return (
            re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "unknown"
        )

    return (
        f"boxoffice_{slug(engagement.get('artist'))}_"
        f"{slug(engagement.get('venue'))}_"
        f"{slug(engagement.get('start_date') or engagement.get('dates_raw'))}"
    )


def build_hunt_plan(
    engagement: dict[str, Any],
    *,
    created_at: datetime | None = None,
    software_version: str = "data_flywheel_and_coverage_v1",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build (plan_row, task_rows) for one engagement.

    The plan covers every huntable target field; a single-show engagement
    already carries its reported fields (promoted into the claim ledger by the
    research corpus), but the hunter still plans the full field list so
    cross-source verification is explicit.
    """
    now = created_at or utc_now()
    canonical_id = event_key(engagement)
    plan_id = f"hunt_plan_{content_hash_of({'event': canonical_id})[:20]}"
    plan = {
        "plan_id": plan_id,
        "canonical_event_id": canonical_id,
        "artist_name": engagement.get("artist"),
        "venue_name": engagement.get("venue"),
        "market": engagement.get("market") or engagement.get("city"),
        "event_date": engagement.get("start_date"),
        "status": PLAN_PLANNED,
        "target_fields": list(HUNT_TARGET_FIELDS),
        "created_at": now.isoformat(),
        "knowledge_time": now.isoformat(),
        "software_version": software_version,
    }
    tasks: list[dict[str, Any]] = []
    for field in HUNT_TARGET_FIELDS:
        task_id = f"hunt_task_{content_hash_of({'plan': plan_id, 'field': field})[:20]}"
        outcome_types = target_field_outcome_types(field)
        tasks.append(
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "target_field": field,
                "outcome_type": outcome_types[0] if outcome_types else None,
                "status": TASK_PENDING,
                "claim_id": None,
                "source_provider": None,
                "source_url": None,
                "retrieved_at": None,
                "knowledge_time": now.isoformat(),
                "notes": None,
            }
        )
    return plan, tasks


# ---------------------------------------------------------------------------
# Claim construction from a hunt finding
# ---------------------------------------------------------------------------
def claim_from_hunt_finding(
    *,
    canonical_event_id: str,
    target_field: str,
    value_numeric: float | None = None,
    value_text: str | None = None,
    outcome_type: str | None = None,
    currency: str | None = None,
    unit: str | None = None,
    source_provider: str,
    source_name: str | None = None,
    source_url: str | None = None,
    source_document_id: str | None = None,
    event_time: str | None = None,
    source_publication_time: str | None = None,
    source_quality: str,
    rights_status: str,
    commercial_use_status: str,
    observation_class: str = OBSERVED_PUBLIC,
    software_version: str = "data_flywheel_and_coverage_v1",
    notes: str | None = None,
    claim_id: str | None = None,
    **kwargs: Any,
) -> OutcomeClaim:
    """Build a validated claim from one hunt finding.

    Raises via :class:`OutcomeClaim` validation on semantic corruption (e.g. a
    capacity claim carrying an attendance definition). Attribute fields have no
    outcome type and must never be forced into the ledger as claims.
    """
    validate_target_field(target_field)
    if target_field not in OUTCOME_FIELDS:
        raise ValueError(
            f"target_field {target_field!r} is attribute evidence, not a claimable outcome"
        )
    chosen = outcome_type or (target_field_outcome_types(target_field)[0])
    return OutcomeClaim.build(
        claim_id=claim_id,
        canonical_event_id=canonical_event_id,
        outcome_type=chosen,
        value_numeric=value_numeric,
        value_text=value_text,
        unit=unit,
        currency=currency,
        source_provider=source_provider,
        source_name=source_name or source_provider,
        source_url=source_url,
        source_document_id=source_document_id,
        event_time=event_time,
        source_publication_time=source_publication_time,
        source_quality=source_quality,
        observation_class=observation_class,
        rights_status=rights_status,
        commercial_use_status=commercial_use_status,
        notes=notes,
        software_version=software_version,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Execution statistics
# ---------------------------------------------------------------------------
HUNT_STAT_FIELDS = (
    "tasks_planned",
    "tasks_attempted",
    "tasks_successful",
    "claims_created",
    "unique_new_outcomes",
    "unique_new_events",
    "duplicate_claims",
    "conflicts",
    "rate_limited",
    "rights_blocked",
    "not_found",
    "parser_failed",
    "cost_usd",
)


def summarize_hunt_tasks(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Summarize hunt-task rows by their persisted status.

    ``tasks_attempted`` = any task that left PENDING (SEARCHING or later).
    ``tasks_successful`` = tasks that produced a claim. Search activity is
    never rewarded — only new decision-useful evidence is.
    """
    by_status: dict[str, int] = {}
    for task in tasks:
        status = validate_task_status(task.get("status") or TASK_PENDING)
        by_status[status] = by_status.get(status, 0) + 1
    attempted = len(tasks) - by_status.get(TASK_PENDING, 0)
    return {
        "tasks_planned": len(tasks),
        "tasks_attempted": attempted,
        "tasks_successful": by_status.get(TASK_CLAIM_FOUND, 0),
        "not_found": by_status.get(TASK_NOT_FOUND, 0),
        "blocked": by_status.get(TASK_BLOCKED, 0),
        "pending": by_status.get(TASK_PENDING, 0),
    }


def hunt_execution_stats(
    *,
    tasks: list[dict[str, Any]] | None = None,
    claims_created: int = 0,
    unique_new_outcomes: int = 0,
    unique_new_events: int = 0,
    duplicate_claims: int = 0,
    conflicts: int = 0,
    rate_limited: int = 0,
    rights_blocked: int = 0,
    parser_failed: int = 0,
    cost_usd: float = 0.0,
) -> dict[str, Any]:
    """Assemble the OUTCOME_HUNTER execution-statistics block.

    All counters are non-negative; per-run counters default to zero and are
    only meaningful once a live hunt source is wired. Execution is reported
    honestly — never implied by the number of plans created.
    """
    base: dict[str, Any] = {
        "tasks_planned": 0,
        "tasks_attempted": 0,
        "tasks_successful": 0,
        "claims_created": int(claims_created),
        "unique_new_outcomes": int(unique_new_outcomes),
        "unique_new_events": int(unique_new_events),
        "duplicate_claims": int(duplicate_claims),
        "conflicts": int(conflicts),
        "rate_limited": int(rate_limited),
        "rights_blocked": int(rights_blocked),
        "not_found": 0,
        "parser_failed": int(parser_failed),
        "cost_usd": float(cost_usd),
    }
    if tasks:
        summary = summarize_hunt_tasks(tasks)
        base.update(
            {
                "tasks_planned": summary["tasks_planned"],
                "tasks_attempted": summary["tasks_attempted"],
                "tasks_successful": summary["tasks_successful"],
                "not_found": summary["not_found"],
            }
        )
    for field in HUNT_STAT_FIELDS:
        base[field] = max(0.0, float(base[field])) if field == "cost_usd" else max(0, int(base[field]))
    return base
