"""Design Partner Retrospective: studies, outcome vault, PIT reconstruction,
row eligibility, blind exports, and the baseline-readiness gate.

The central safety property: realized outcomes are stored separately (the
outcome vault) and the feature-side access path provably excludes them. A
study declares which outcome types are *hidden* (the targets + any realized
result) and which private outcome types are *allowed inputs* (capacity, deal
terms, costs known before the event). ``retrospective_inputs`` returns only
pre-cutoff evidence that is not a hidden outcome; tests assert this cannot
leak.

No model is trained anywhere in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from .outcome_claims import (
    ARTIST_BACKEND,
    ARTIST_GUARANTEE,
    ATTENDANCE_TYPES,
    CAPACITY_TYPES,
    COST_TYPES,
    COMP_TICKETS,
    EVENT_USABLE_CAPACITY,
    EXPLICIT_NOT_SOLD_OUT_ASSERTION,
    EXPLICIT_SOLD_OUT_ASSERTION,
    FNB_REVENUE,
    LABOR_COST,
    MARKETING_SPEND,
    MERCH_REVENUE,
    OBSERVED_PRIVATE,
    OBSERVED_PUBLIC,
    PAID_ATTENDANCE,
    PAID_TICKETS,
    PARKING_REVENUE,
    PERMIT_CAPACITY_LIMIT,
    PRIMARY_FACE_VALUE_MAX,
    PRIMARY_FACE_VALUE_MIN,
    PRODUCTION_COST,
    PROMOTER_CONTRIBUTION,
    PROMOTER_COST,
    REFUNDED_TICKETS,
    REPORTED_ATTENDANCE,
    REVENUE_TYPES,
    SCANNED_ATTENDANCE,
    SETTLEMENT_GROSS,
    SETTLEMENT_NET,
    SETTLEMENT_TYPES,
    SOLD_OUT_TYPES,
    SPONSOR_REVENUE,
    TICKET_GROSS,
    TICKET_NET,
    TICKET_TYPES,
    TICKETS_SOLD,
    VENUE_CAPACITY,
    VENUE_COST,
    VIP_REVENUE,
)

# ---------------------------------------------------------------------------
# Study statuses
# ---------------------------------------------------------------------------
STUDY_DRAFT = "DRAFT"
STUDY_VALIDATING = "VALIDATING"
STUDY_FROZEN = "FROZEN"
STUDY_READY_FOR_BASELINES = "READY_FOR_BASELINES"
STUDY_BLOCKED = "BLOCKED"
STUDY_SCORED = "SCORED"
STUDY_STATUSES = frozenset({
    STUDY_DRAFT, STUDY_VALIDATING, STUDY_FROZEN, STUDY_READY_FOR_BASELINES, STUDY_BLOCKED, STUDY_SCORED,
})

# Decision cutoff types (mirrors the cutoffs in migration 013).
CUTOFF_BOOKING = "BOOKING"
CUTOFF_ANNOUNCEMENT = "ANNOUNCEMENT"
CUTOFF_ONSALE = "ONSALE"
CUTOFF_EVENT = "EVENT"
CUTOFF_TYPES = frozenset({CUTOFF_BOOKING, CUTOFF_ANNOUNCEMENT, CUTOFF_ONSALE, CUTOFF_EVENT})

_CUTOFF_COLUMN = {
    CUTOFF_BOOKING: "booking_cutoff",
    CUTOFF_ANNOUNCEMENT: "announcement_cutoff",
    CUTOFF_ONSALE: "onsale_cutoff",
    CUTOFF_EVENT: "event_cutoff",
}

# ---------------------------------------------------------------------------
# Outcome vs input classification (defaults; each study may override)
# ---------------------------------------------------------------------------
# Realized results that must be hidden from retrospective inputs.
DEFAULT_HIDDEN_OUTCOMES = frozenset(
    ATTENDANCE_TYPES
    | TICKET_TYPES
    | SOLD_OUT_TYPES
    | {TICKET_GROSS, TICKET_NET, MERCH_REVENUE, FNB_REVENUE, PARKING_REVENUE, VIP_REVENUE, SPONSOR_REVENUE}
    | SETTLEMENT_TYPES
)

# Private fields that are legitimately knowable before the event and are
# therefore usable as *inputs* (never as the target unless explicitly chosen).
DEFAULT_ALLOWED_PRIVATE_INPUTS = frozenset(
    CAPACITY_TYPES
    | COST_TYPES
    | {ARTIST_GUARANTEE, ARTIST_BACKEND, PRIMARY_FACE_VALUE_MIN, PRIMARY_FACE_VALUE_MAX}
)

@dataclass
class RetrospectiveStudy:
    study_id: str
    customer_id: str
    dataset_id: str
    target: str
    decision_cutoff_type: str
    hidden_outcomes: frozenset[str]
    allowed_private_inputs: frozenset[str]
    status: str = STUDY_DRAFT
    event_ids: tuple[str, ...] = ()
    feature_policy_version: str = "design_partner_retrospective_v1"
    source_policy_version: str = "design_partner_retrospective_v1"
    created_at: str | None = None
    frozen_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STUDY_STATUSES:
            raise ValueError(f"invalid study status {self.status!r}")
        if self.decision_cutoff_type not in CUTOFF_TYPES:
            raise ValueError(f"invalid decision cutoff type {self.decision_cutoff_type!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "customer_id": self.customer_id,
            "dataset_id": self.dataset_id,
            "target": self.target,
            "decision_cutoff_type": self.decision_cutoff_type,
            "hidden_outcomes": sorted(self.hidden_outcomes),
            "allowed_private_inputs": sorted(self.allowed_private_inputs),
            "status": self.status,
            "event_ids": list(self.event_ids),
            "feature_policy_version": self.feature_policy_version,
            "source_policy_version": self.source_policy_version,
            "created_at": self.created_at or utc_now().isoformat(),
            "frozen_at": self.frozen_at,
        }


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Outcome vault
# ---------------------------------------------------------------------------
def vault_outcomes(economics_repo, study: RetrospectiveStudy) -> dict[str, Any]:
    """Place every hidden-outcome claim for the study's events into the vault.

    Idempotent: a stable vault_id means re-running never duplicates entries.
    Returns summary counts (never values).
    """
    hidden_types = study.hidden_outcomes
    vaulted = 0
    claims_vaulted: list[str] = []
    for event_id in study.event_ids:
        claims = economics_repo.query_outcome_claims(event_id=event_id)
        for claim in claims:
            if claim["outcome_type"] not in hidden_types:
                continue
            vault_id = f"vault_{content_hash_of({
                'study': study.study_id,
                'claim': claim['claim_id'],
            })[:20]}"
            economics_repo.insert_outcome_vault_entry({
                "vault_id": vault_id,
                "study_id": study.study_id,
                "canonical_event_id": event_id,
                "claim_id": claim["claim_id"],
                "outcome_type": claim["outcome_type"],
                "hidden": True,
            })
            vaulted += 1
            claims_vaulted.append(claim["claim_id"])
    return {
        "study_id": study.study_id,
        "hidden_outcome_types": sorted(hidden_types),
        "claims_vaulted": vaulted,
        "vaulted_claim_ids": claims_vaulted,
    }


def hidden_claim_ids(economics_repo, study: RetrospectiveStudy) -> set[str]:
    """The set of claim ids hidden from the feature side for this study."""
    hidden_types = study.hidden_outcomes
    hidden: set[str] = set()
    for event_id in study.event_ids:
        for claim in economics_repo.query_outcome_claims(event_id=event_id):
            if claim["outcome_type"] in hidden_types:
                hidden.add(claim["claim_id"])
    return hidden


def _is_feature_visible(claim: dict[str, Any], study: RetrospectiveStudy, cutoff: datetime | None) -> bool:
    if claim["outcome_type"] in study.hidden_outcomes:
        return False
    if cutoff is not None:
        knowledge = _parse(claim.get("knowledge_time"))
        if knowledge is not None and knowledge > cutoff:
            return False
    if claim["observation_class"] == OBSERVED_PUBLIC:
        return True
    # Private observations are visible only if they are declared allowed inputs.
    return claim["outcome_type"] in study.allowed_private_inputs


# ---------------------------------------------------------------------------
# Feature-side access (the leakage boundary)
# ---------------------------------------------------------------------------
def retrospective_inputs(economics_repo, study: RetrospectiveStudy) -> dict[str, Any]:
    """Return the feature-side view: pre-cutoff, non-outcome evidence.

    This is the ONLY path future feature reconstruction may use for a study.
    Hidden outcome claims are structurally excluded (by outcome_type), so the
    target cannot leak into inputs.
    """
    cutoffs = {c["canonical_event_id"]: c for c in economics_repo.query_decision_cutoffs()}
    cutoff_column = _CUTOFF_COLUMN[study.decision_cutoff_type]

    events: list[dict[str, Any]] = []
    visible_claim_ids: list[str] = []
    excluded_hidden: list[str] = []
    for event_id in study.event_ids:
        row = cutoffs.get(event_id, {})
        raw_cutoff = row.get(cutoff_column)
        cutoff = _parse(raw_cutoff)
        visible = []
        for claim in economics_repo.query_outcome_claims(event_id=event_id):
            if claim["outcome_type"] in study.hidden_outcomes:
                excluded_hidden.append(claim["claim_id"])
                continue
            if _is_feature_visible(claim, study, cutoff):
                visible.append(claim)
        events.append({
            "canonical_event_id": event_id,
            "decision_cutoff": cutoff.isoformat() if cutoff else None,
            "visible_claim_ids": [c["claim_id"] for c in visible],
            "visible_outcome_types": sorted({c["outcome_type"] for c in visible}),
        })
        visible_claim_ids.extend(c["claim_id"] for c in visible)

    hidden = hidden_claim_ids(economics_repo, study)
    assert not (set(visible_claim_ids) & hidden), "leak: hidden outcome exposed as input"

    return {
        "study_id": study.study_id,
        "decision_cutoff_type": study.decision_cutoff_type,
        "events": events,
        "visible_claim_ids": visible_claim_ids,
        "excluded_hidden_claim_ids": sorted(set(excluded_hidden)),
        "leakage_check": "PASS",
    }


def build_blind_export(economics_repo, study: RetrospectiveStudy) -> dict[str, Any]:
    """Two logically separated manifests.

    A. FEATURE-SIDE: event ids, cutoffs, allowed evidence ids (no outcomes).
    B. OUTCOME-SIDE: event ids, targets, realized values (revealed only for
       scoring). Future model code reads A and never B.
    """
    feature = retrospective_inputs(economics_repo, study)
    outcomes: list[dict[str, Any]] = []
    for event_id in study.event_ids:
        for claim in economics_repo.query_outcome_claims(event_id=event_id):
            if claim["outcome_type"] in study.hidden_outcomes:
                outcomes.append({
                    "canonical_event_id": event_id,
                    "outcome_type": claim["outcome_type"],
                    "value_numeric": claim.get("value_numeric"),
                    "value_text": claim.get("value_text"),
                    "currency": claim.get("currency"),
                    "claim_id": claim["claim_id"],
                    "source_quality": claim.get("source_quality"),
                })
    return {
        "feature_side_manifest": {
            "study_id": study.study_id,
            "decision_cutoff_type": study.decision_cutoff_type,
            "events": feature["events"],
        },
        "outcome_side_manifest": {
            "study_id": study.study_id,
            "target": study.target,
            "outcomes": outcomes,
        },
        "separated": True,
    }


# ---------------------------------------------------------------------------
# PIT reconstruction readiness
# ---------------------------------------------------------------------------
PIT_COMPLETE = "COMPLETE"
PIT_PARTIAL = "PARTIAL"
PIT_INSUFFICIENT = "INSUFFICIENT"
PIT_STATUSES = frozenset({PIT_COMPLETE, PIT_PARTIAL, PIT_INSUFFICIENT})


def pit_reconstructability(economics_repo, study: RetrospectiveStudy) -> list[dict[str, Any]]:
    cutoffs = {c["canonical_event_id"]: c for c in economics_repo.query_decision_cutoffs()}
    cutoff_column = _CUTOFF_COLUMN[study.decision_cutoff_type]
    results: list[dict[str, Any]] = []
    for event_id in study.event_ids:
        row = cutoffs.get(event_id, {})
        raw_cutoff = row.get(cutoff_column)
        cutoff = _parse(raw_cutoff)
        if cutoff is None:
            status, reason = PIT_INSUFFICIENT, "decision cutoff missing"
        else:
            pre_cutoff = [
                c for c in economics_repo.query_outcome_claims(event_id=event_id, cutoff=cutoff)
                if c["outcome_type"] not in study.hidden_outcomes
            ]
            if not pre_cutoff:
                status, reason = PIT_PARTIAL, "no public evidence before cutoff"
            else:
                status, reason = PIT_COMPLETE, "pre-cutoff evidence present"
        results.append({
            "canonical_event_id": event_id,
            "decision_cutoff": cutoff.isoformat() if cutoff else None,
            "status": status,
            "reason": reason,
        })
    return results


# ---------------------------------------------------------------------------
# Training-row eligibility (model-free)
# ---------------------------------------------------------------------------
EXCL_TARGET_MISSING = "TARGET_MISSING"
EXCL_ENTITY_UNRESOLVED = "ENTITY_UNRESOLVED"
EXCL_CUTOFF_MISSING = "CUTOFF_MISSING"
EXCL_PIT_EVIDENCE_INSUFFICIENT = "PIT_EVIDENCE_INSUFFICIENT"
EXCL_RIGHTS_BLOCK = "RIGHTS_BLOCK"
EXCL_INVALID_ACCOUNTING = "INVALID_ACCOUNTING"
EXCL_TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
EXCL_DUPLICATE_EVENT = "DUPLICATE_EVENT"
EXCL_OUTCOME_CONFLICT = "OUTCOME_CONFLICT"


def training_row_eligibility(economics_repo, study: RetrospectiveStudy) -> list[dict[str, Any]]:
    """Decide, per event, whether it could become a future out-of-sample row.

    This never trains a model; it only classifies exclusion reasons so the
    promoter can see how much of their history is usable.
    """
    cutoffs = {c["canonical_event_id"]: c for c in economics_repo.query_decision_cutoffs()}
    cutoff_column = _CUTOFF_COLUMN[study.decision_cutoff_type]
    pit = {p["canonical_event_id"]: p for p in pit_reconstructability(economics_repo, study)}

    results: list[dict[str, Any]] = []
    for event_id in study.event_ids:
        reasons: list[str] = []
        claims = economics_repo.query_outcome_claims(event_id=event_id)

        target_values = [c for c in claims if c["outcome_type"] == study.target]
        if not target_values:
            reasons.append(EXCL_TARGET_MISSING)
        elif len({c.get("value_numeric") for c in target_values if c.get("value_numeric") is not None}) > 1:
            reasons.append(EXCL_OUTCOME_CONFLICT)

        row = cutoffs.get(event_id, {})
        if row.get(cutoff_column) is None:
            reasons.append(EXCL_CUTOFF_MISSING)

        pit_status = pit.get(event_id, {}).get("status")
        if pit_status == PIT_INSUFFICIENT:
            reasons.append(EXCL_PIT_EVIDENCE_INSUFFICIENT)

        eligible = not reasons
        results.append({
            "canonical_event_id": event_id,
            "eligible": eligible,
            "exclusion_reason": reasons[0] if reasons else None,
            "all_reasons": reasons,
        })
    return results


# ---------------------------------------------------------------------------
# Baseline readiness gate
# ---------------------------------------------------------------------------
READINESS_NOT_READY = "NOT_READY"
READINESS_DESCRIPTIVE = "READY_FOR_DESCRIPTIVE_BASELINES"
READINESS_ATTENDANCE = "READY_FOR_ATTENDANCE_BASELINE"
READINESS_ECONOMIC = "READY_FOR_ECONOMIC_BASELINE"
READINESS_STATES = frozenset({
    READINESS_NOT_READY, READINESS_DESCRIPTIVE, READINESS_ATTENDANCE, READINESS_ECONOMIC,
})

# Conservative floors. Not authoritative thresholds; the floor below which a
# baseline would be fitting noise.
MIN_DESCRIPTIVE_ROWS = 5
MIN_ATTENDANCE_ROWS = 20
MIN_ECONOMIC_ROWS = 30


def baseline_readiness(economics_repo, events_repo, study: RetrospectiveStudy) -> dict[str, Any]:
    rows = training_row_eligibility(economics_repo, study)
    eligible = [r for r in rows if r["eligible"]]
    pit = pit_reconstructability(economics_repo, study)
    pit_complete = sum(1 for p in pit if p["status"] == PIT_COMPLETE)

    events = [e for e in events_repo.query_events() if e["event_id"] in study.event_ids]
    venues = {e.get("venue_name") or "UNKNOWN" for e in events}
    years = {str(_parse(e.get("local_date")).year) if _parse(e.get("local_date")) else "UNKNOWN" for e in events}

    attendance_target = study.target in ATTENDANCE_TYPES
    reasons: list[str] = []

    if len(eligible) >= MIN_ECONOMIC_ROWS and len(venues) >= 3 and len(years) >= 2 and pit_complete >= MIN_ECONOMIC_ROWS:
        verdict = READINESS_ECONOMIC
    elif attendance_target and len(eligible) >= MIN_ATTENDANCE_ROWS and pit_complete >= MIN_ATTENDANCE_ROWS:
        verdict = READINESS_ATTENDANCE
    elif len(eligible) >= MIN_DESCRIPTIVE_ROWS:
        verdict = READINESS_DESCRIPTIVE
    else:
        verdict = READINESS_NOT_READY

    if len(eligible) < MIN_DESCRIPTIVE_ROWS:
        reasons.append(f"only {len(eligible)} eligible rows (floor {MIN_DESCRIPTIVE_ROWS})")
    if pit_complete < MIN_DESCRIPTIVE_ROWS:
        reasons.append(f"PIT reconstruction incomplete for {len(rows) - pit_complete} events")
    if len(venues) < 2:
        reasons.append("venue diversity too low")
    if len(years) < 2:
        reasons.append("temporal coverage too narrow")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "eligible_rows": len(eligible),
        "total_rows": len(rows),
        "pit_complete": pit_complete,
        "venue_count": len(venues),
        "year_count": len(years),
        "target": study.target,
        "target_is_attendance": attendance_target,
        "notes": "readiness is a gate, not a model; nothing was trained",
    }
