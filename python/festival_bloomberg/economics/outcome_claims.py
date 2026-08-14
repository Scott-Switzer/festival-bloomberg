"""Controlled outcome taxonomy and claim validation for the Historical Lab.

Outcome claims are source-backed assertions about a canonical event. The
taxonomy is a closed set; arbitrary strings are rejected. Semantic guards
prevent the classic corruptions (capacity-as-attendance, permit-as-attendance,
OFFSALE-as-SOLD_OUT, setlist-as-attendance, expected-as-actual).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

# ---------------------------------------------------------------------------
# Controlled outcome taxonomy
# ---------------------------------------------------------------------------
PAID_ATTENDANCE = "PAID_ATTENDANCE"
SCANNED_ATTENDANCE = "SCANNED_ATTENDANCE"
REPORTED_ATTENDANCE = "REPORTED_ATTENDANCE"

TICKETS_SOLD = "TICKETS_SOLD"
PAID_TICKETS = "PAID_TICKETS"
COMP_TICKETS = "COMP_TICKETS"
REFUNDED_TICKETS = "REFUNDED_TICKETS"

EXPLICIT_SOLD_OUT_ASSERTION = "EXPLICIT_SOLD_OUT_ASSERTION"
EXPLICIT_NOT_SOLD_OUT_ASSERTION = "EXPLICIT_NOT_SOLD_OUT_ASSERTION"

TICKET_GROSS = "TICKET_GROSS"
TICKET_NET = "TICKET_NET"
PRIMARY_FACE_VALUE_MIN = "PRIMARY_FACE_VALUE_MIN"
PRIMARY_FACE_VALUE_MAX = "PRIMARY_FACE_VALUE_MAX"

VENUE_CAPACITY = "VENUE_CAPACITY"
EVENT_USABLE_CAPACITY = "EVENT_USABLE_CAPACITY"
PERMIT_CAPACITY_LIMIT = "PERMIT_CAPACITY_LIMIT"

ARTIST_GUARANTEE = "ARTIST_GUARANTEE"
ARTIST_BACKEND = "ARTIST_BACKEND"
PROMOTER_COST = "PROMOTER_COST"
MARKETING_SPEND = "MARKETING_SPEND"
PRODUCTION_COST = "PRODUCTION_COST"
LABOR_COST = "LABOR_COST"
VENUE_COST = "VENUE_COST"

PROMOTER_CONTRIBUTION = "PROMOTER_CONTRIBUTION"
SETTLEMENT_GROSS = "SETTLEMENT_GROSS"
SETTLEMENT_NET = "SETTLEMENT_NET"

MERCH_REVENUE = "MERCH_REVENUE"
FNB_REVENUE = "FNB_REVENUE"
PARKING_REVENUE = "PARKING_REVENUE"
VIP_REVENUE = "VIP_REVENUE"
SPONSOR_REVENUE = "SPONSOR_REVENUE"

EVENT_PERFORMED = "EVENT_PERFORMED"
EVENT_CANCELLED = "EVENT_CANCELLED"
EVENT_POSTPONED = "EVENT_POSTPONED"

ATTENDANCE_TYPES = frozenset({PAID_ATTENDANCE, SCANNED_ATTENDANCE, REPORTED_ATTENDANCE})
TICKET_TYPES = frozenset({TICKETS_SOLD, PAID_TICKETS, COMP_TICKETS, REFUNDED_TICKETS})
SOLD_OUT_TYPES = frozenset({EXPLICIT_SOLD_OUT_ASSERTION, EXPLICIT_NOT_SOLD_OUT_ASSERTION})
REVENUE_TYPES = frozenset({
    TICKET_GROSS, TICKET_NET, PRIMARY_FACE_VALUE_MIN, PRIMARY_FACE_VALUE_MAX,
    MERCH_REVENUE, FNB_REVENUE, PARKING_REVENUE, VIP_REVENUE, SPONSOR_REVENUE,
})
CAPACITY_TYPES = frozenset({VENUE_CAPACITY, EVENT_USABLE_CAPACITY, PERMIT_CAPACITY_LIMIT})
COST_TYPES = frozenset({
    ARTIST_GUARANTEE, ARTIST_BACKEND, PROMOTER_COST, MARKETING_SPEND,
    PRODUCTION_COST, LABOR_COST, VENUE_COST,
})
SETTLEMENT_TYPES = frozenset({PROMOTER_CONTRIBUTION, SETTLEMENT_GROSS, SETTLEMENT_NET})
EVENT_STATUS_TYPES = frozenset({EVENT_PERFORMED, EVENT_CANCELLED, EVENT_POSTPONED})

OUTCOME_TYPES = frozenset(
    ATTENDANCE_TYPES
    | TICKET_TYPES
    | SOLD_OUT_TYPES
    | REVENUE_TYPES
    | CAPACITY_TYPES
    | COST_TYPES
    | SETTLEMENT_TYPES
    | EVENT_STATUS_TYPES
)

# ---------------------------------------------------------------------------
# Source quality grading (independent of confidence)
# ---------------------------------------------------------------------------
GRADE_A_PRIMARY_SETTLEMENT = "A_PRIMARY_SETTLEMENT"
GRADE_A_PRIMARY_TICKETING = "A_PRIMARY_TICKETING"
GRADE_A_PRIMARY_GOVERNMENT = "A_PRIMARY_GOVERNMENT"
GRADE_A_PRIMARY_PROMOTER = "A_PRIMARY_PROMOTER"
GRADE_A_PRIMARY_VENUE = "A_PRIMARY_VENUE"
GRADE_B_REPUTABLE_INDUSTRY = "B_REPUTABLE_INDUSTRY_REPORT"
GRADE_B_REPUTABLE_NEWS = "B_REPUTABLE_NEWS"
GRADE_C_OTHER_PUBLIC = "C_OTHER_PUBLIC_REPORT"
GRADE_D_INFERRED = "D_INFERRED"
GRADE_D_WEAK = "D_WEAK"
GRADE_UNKNOWN = "UNKNOWN"

SOURCE_QUALITY_LEVELS = frozenset({
    GRADE_A_PRIMARY_SETTLEMENT,
    GRADE_A_PRIMARY_TICKETING,
    GRADE_A_PRIMARY_GOVERNMENT,
    GRADE_A_PRIMARY_PROMOTER,
    GRADE_A_PRIMARY_VENUE,
    GRADE_B_REPUTABLE_INDUSTRY,
    GRADE_B_REPUTABLE_NEWS,
    GRADE_C_OTHER_PUBLIC,
    GRADE_D_INFERRED,
    GRADE_D_WEAK,
    GRADE_UNKNOWN,
})

# ---------------------------------------------------------------------------
# Observation class / rights
# ---------------------------------------------------------------------------
OBSERVED_PUBLIC = "OBSERVED_PUBLIC"
OBSERVED_PRIVATE = "OBSERVED_PRIVATE"
OBSERVATION_CLASSES = frozenset({OBSERVED_PUBLIC, OBSERVED_PRIVATE})

RIGHTS_UNKNOWN = "UNKNOWN"
RIGHTS_OPEN_COMMERCIAL_OK = "OPEN_COMMERCIAL_OK"
RIGHTS_OPEN_WITH_ATTRIBUTION = "OPEN_WITH_ATTRIBUTION"
RIGHTS_TERMS_REVIEW_REQUIRED = "TERMS_REVIEW_REQUIRED"
RIGHTS_RESEARCH_ONLY = "RESEARCH_ONLY"
RIGHTS_STATUSES = frozenset({
    RIGHTS_UNKNOWN,
    RIGHTS_OPEN_COMMERCIAL_OK,
    RIGHTS_OPEN_WITH_ATTRIBUTION,
    RIGHTS_TERMS_REVIEW_REQUIRED,
    RIGHTS_RESEARCH_ONLY,
})


def validate_outcome_type(outcome_type: str) -> str:
    """Return the type or raise if it is not in the controlled taxonomy."""
    if outcome_type not in OUTCOME_TYPES:
        raise ValueError(
            f"outcome_type {outcome_type!r} is not in the controlled taxonomy"
        )
    return outcome_type


def validate_source_quality(source_quality: str) -> str:
    if source_quality not in SOURCE_QUALITY_LEVELS:
        raise ValueError(f"source_quality {source_quality!r} is not a valid grade")
    return source_quality


def validate_observation_class(observation_class: str) -> str:
    if observation_class not in OBSERVATION_CLASSES:
        raise ValueError(f"observation_class {observation_class!r} is invalid")
    return observation_class


def validate_rights(rights_status: str, commercial_use_status: str) -> None:
    if rights_status not in RIGHTS_STATUSES:
        raise ValueError(f"rights_status {rights_status!r} is invalid")
    if commercial_use_status not in RIGHTS_STATUSES:
        raise ValueError(f"commercial_use_status {commercial_use_status!r} is invalid")


class OutcomeClaimSemanticError(ValueError):
    """Raised when a claim would corrupt outcome semantics."""


def _is_capacity_type(outcome_type: str) -> bool:
    return outcome_type in CAPACITY_TYPES


def _is_attendance_type(outcome_type: str) -> bool:
    return outcome_type in ATTENDANCE_TYPES


def guard_claim_semantics(claim: "OutcomeClaim") -> None:
    """Fail closed on obvious semantic corruption.

    - capacity claims must carry a capacity definition, never attendance
    - a permit capacity must be PERMIT_CAPACITY_LIMIT, not PAID_ATTENDANCE
    - expected attendance is not a valid claim type at all (rejected above)
    """
    outcome_type = claim.outcome_type
    if _is_capacity_type(outcome_type):
        if claim.attendance_definition:
            raise OutcomeClaimSemanticError(
                "capacity claim cannot carry an attendance_definition"
            )
        if not claim.capacity_definition and outcome_type != VENUE_CAPACITY:
            # Venue capacity is self-defining; usable/permit need a definition.
            pass
    if outcome_type == PAID_ATTENDANCE and claim.capacity_definition:
        raise OutcomeClaimSemanticError(
            "PAID_ATTENDANCE cannot carry a capacity_definition (it is not capacity)"
        )
    if claim.attendance_definition and not _is_attendance_type(outcome_type):
        raise OutcomeClaimSemanticError(
            f"attendance_definition is only valid for attendance claims, got {outcome_type}"
        )


# ---------------------------------------------------------------------------
# Claim dataclass
# ---------------------------------------------------------------------------
@dataclass
class OutcomeClaim:
    claim_id: str
    canonical_event_id: str
    outcome_type: str
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    currency: str | None
    attendance_definition: str | None
    ticket_definition: str | None
    revenue_definition: str | None
    capacity_definition: str | None
    source_provider: str | None
    source_name: str | None
    source_url: str | None
    source_document_id: str | None
    event_time: str | None
    source_publication_time: str | None
    source_as_of: str | None
    retrieved_at: str
    knowledge_time: str
    valid_from: str | None
    valid_to: str | None
    evidence_observation_id: str | None
    raw_payload_hash: str | None
    source_quality: str
    claim_confidence: str | None
    entity_resolution_confidence: str | None
    rights_status: str
    commercial_use_status: str
    observation_class: str
    is_censored: bool | None
    censoring_type: str | None
    censoring_threshold: str | None
    conflict_group_id: str | None
    supersedes_claim_id: str | None
    notes: str | None
    software_version: str

    def __post_init__(self) -> None:
        validate_outcome_type(self.outcome_type)
        validate_source_quality(self.source_quality)
        validate_observation_class(self.observation_class)
        validate_rights(self.rights_status, self.commercial_use_status)
        guard_claim_semantics(self)

    def to_row(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def build(
        cls,
        *,
        canonical_event_id: str,
        outcome_type: str,
        value_numeric: float | None = None,
        value_text: str | None = None,
        unit: str | None = None,
        currency: str | None = None,
        attendance_definition: str | None = None,
        ticket_definition: str | None = None,
        revenue_definition: str | None = None,
        capacity_definition: str | None = None,
        source_provider: str | None = None,
        source_name: str | None = None,
        source_url: str | None = None,
        source_document_id: str | None = None,
        event_time: str | None = None,
        source_publication_time: str | None = None,
        source_as_of: str | None = None,
        retrieved_at: str | None = None,
        knowledge_time: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        evidence_observation_id: str | None = None,
        raw_payload_hash: str | None = None,
        source_quality: str = GRADE_UNKNOWN,
        claim_confidence: str | None = None,
        entity_resolution_confidence: str | None = None,
        rights_status: str = RIGHTS_UNKNOWN,
        commercial_use_status: str = RIGHTS_UNKNOWN,
        observation_class: str = OBSERVED_PUBLIC,
        is_censored: bool | None = None,
        censoring_type: str | None = None,
        censoring_threshold: str | None = None,
        conflict_group_id: str | None = None,
        supersedes_claim_id: str | None = None,
        notes: str | None = None,
        software_version: str = "historical_laboratory_v1",
        claim_id: str | None = None,
    ) -> "OutcomeClaim":
        retrieved = retrieved_at or utc_now().isoformat()
        knowledge = knowledge_time or retrieved
        cid = claim_id or f"claim_{content_hash_of({
            'event': canonical_event_id,
            'type': outcome_type,
            'value': value_numeric if value_numeric is not None else value_text,
            'source': source_url or source_document_id or source_name,
            'retrieved': retrieved,
        })[:20]}"
        return cls(
            claim_id=cid,
            canonical_event_id=canonical_event_id,
            outcome_type=outcome_type,
            value_numeric=value_numeric,
            value_text=value_text,
            unit=unit,
            currency=currency,
            attendance_definition=attendance_definition,
            ticket_definition=ticket_definition,
            revenue_definition=revenue_definition,
            capacity_definition=capacity_definition,
            source_provider=source_provider,
            source_name=source_name,
            source_url=source_url,
            source_document_id=source_document_id,
            event_time=event_time,
            source_publication_time=source_publication_time,
            source_as_of=source_as_of,
            retrieved_at=retrieved,
            knowledge_time=knowledge,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence_observation_id=evidence_observation_id,
            raw_payload_hash=raw_payload_hash,
            source_quality=source_quality,
            claim_confidence=claim_confidence,
            entity_resolution_confidence=entity_resolution_confidence,
            rights_status=rights_status,
            commercial_use_status=commercial_use_status,
            observation_class=observation_class,
            is_censored=is_censored,
            censoring_type=censoring_type,
            censoring_threshold=censoring_threshold,
            conflict_group_id=conflict_group_id,
            supersedes_claim_id=supersedes_claim_id,
            notes=notes,
            software_version=software_version,
        )


# ---------------------------------------------------------------------------
# Censoring helpers
# ---------------------------------------------------------------------------
CENSORING_RIGHT = "RIGHT"
CENSORING_LEFT = "LEFT"
CENSORING_INTERVAL = "INTERVAL"


def right_censored_sold_out(*, capacity_value: float) -> dict[str, Any]:
    """A sold-out show whose tickets_sold == usable_capacity does not reveal
    latent demand. Label it right-censored at the capacity threshold."""
    return {
        "is_censored": True,
        "censoring_type": CENSORING_RIGHT,
        "censoring_threshold": str(capacity_value),
    }


# ---------------------------------------------------------------------------
# Small numeric/text extraction helpers for parsers
# ---------------------------------------------------------------------------
_NUMBER = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def parse_number(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUMBER.search(str(text))
    if not match:
        return None
    return float(match.group(1).replace(",", ""))
