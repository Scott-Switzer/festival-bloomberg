"""Independent event-outcome dimensions.

EVENT_STATUS, PERFORMANCE_RECORDED, SOLD_OUT_STATUS, ATTENDANCE, and
CAPACITY_UTILIZATION are never combined into a score. OFFSALE is not
SOLD_OUT. Zero secondary listings is not SOLD_OUT. Setlist presence is
not attendance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .snapshots import map_ticketmaster_status

EXPLICIT_SOLD_OUT = "EXPLICIT_SOLD_OUT"
EXPLICIT_NOT_SOLD_OUT = "EXPLICIT_NOT_SOLD_OUT"
SOLD_OUT_UNKNOWN = "UNKNOWN"


def infer_sold_out_from_offsale(event_status: str | None) -> str:
    """OFFSALE is not sold out."""
    del event_status
    return SOLD_OUT_UNKNOWN


def infer_sold_out_from_listing_count(listing_count: int | None) -> str:
    """Zero secondary listings is not sold out."""
    del listing_count
    return SOLD_OUT_UNKNOWN


def sold_out_from_explicit_evidence(text: str | None, *, source: str | None) -> str:
    if not text or not source:
        return SOLD_OUT_UNKNOWN
    blob = text.lower()
    official = source.lower() in {"official_venue", "official_promoter", "official_artist", "reputable_reported"}
    if not official:
        return SOLD_OUT_UNKNOWN
    if "sold out" in blob or "sold-out" in blob:
        return EXPLICIT_SOLD_OUT
    if "not sold out" in blob or "tickets available" in blob:
        return EXPLICIT_NOT_SOLD_OUT
    return SOLD_OUT_UNKNOWN


@dataclass
class EventOutcome:
    outcome_id: str
    canonical_event_id: str
    event_status: str
    performance_recorded_by_setlistfm: bool
    sold_out_status: str
    attendance_value: float | None
    attendance_source: str | None
    attendance_context: str | None
    capacity_utilization: float | None
    utilization_status: str
    supporting_claim_ids: list[str]
    supporting_observation_ids: list[str]
    retrieved_at: str
    knowledge_time: str

    def to_row(self) -> dict[str, Any]:
        row = self.__dict__.copy()
        return row


def historical_setlist_outcome(
    *,
    event_id: str,
    has_setlist_observation: bool,
    retrieved_at: str,
    knowledge_time: str,
    observation_ids: list[str],
) -> EventOutcome:
    return EventOutcome(
        outcome_id=f"out_{event_id}_{knowledge_time[:16].replace(':', '')}",
        canonical_event_id=event_id,
        event_status="COMPLETED_UNKNOWN",
        performance_recorded_by_setlistfm=bool(has_setlist_observation),
        sold_out_status=SOLD_OUT_UNKNOWN,
        attendance_value=None,
        attendance_source=None,
        attendance_context=None,
        capacity_utilization=None,
        utilization_status="UNKNOWN",
        supporting_claim_ids=[],
        supporting_observation_ids=observation_ids,
        retrieved_at=retrieved_at,
        knowledge_time=knowledge_time,
    )


def prospective_outcome(
    *,
    event_id: str,
    ticketmaster_status: str | None,
    retrieved_at: str,
    knowledge_time: str,
    observation_ids: list[str],
    listing_count: int | None = None,
) -> EventOutcome:
    del listing_count
    return EventOutcome(
        outcome_id=f"out_{event_id}_{knowledge_time[:16].replace(':', '')}",
        canonical_event_id=event_id,
        event_status=map_ticketmaster_status(ticketmaster_status),
        performance_recorded_by_setlistfm=False,
        sold_out_status=SOLD_OUT_UNKNOWN,
        attendance_value=None,
        attendance_source=None,
        attendance_context=None,
        capacity_utilization=None,
        utilization_status="UNKNOWN",
        supporting_claim_ids=[],
        supporting_observation_ids=observation_ids,
        retrieved_at=retrieved_at,
        knowledge_time=knowledge_time,
    )
