"""Forward ticket-inventory watchlist foundation.

Touring Data's "Ticket Count" material (general-sale date, event date, venue,
estimated capacity, tickets available, tickets distributed) is a
subscription-gated (Patreon) product. We do NOT bypass that gate. This module
defines the schema-aligned model and validation so that a future authorized or
customer-authorized source can write INFERRED_INVENTORY_SIGNAL snapshots.

Crucial: these snapshots are FORWARD, INFERRED inventory signals. They are
never settled outcomes and must never be promoted to PAID_TICKETS /
TICKETS_SOLD outcome claims. ``classification`` is a closed set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..economics.outcome_claims import (
    OBSERVED_PUBLIC,
    RIGHTS_RESEARCH_ONLY,
    RIGHTS_TERMS_REVIEW_REQUIRED,
    RIGHTS_UNKNOWN,
)

INVENTORY_CLASS_INFERRED = "INFERRED_INVENTORY_SIGNAL"
INVENTORY_CLASS_UNKNOWN = "UNKNOWN"
INVENTORY_CLASSES = frozenset({INVENTORY_CLASS_INFERRED, INVENTORY_CLASS_UNKNOWN})

TICKET_COUNT_SOURCE = "touring_data_ticket_count"
TICKET_COUNT_ACCESS = "PATREON_RESTRICTED"

#: outcome types an inventory snapshot may never masquerade as
_FORBIDDEN_PROMOTION = frozenset({"PAID_TICKETS", "TICKETS_SOLD", "PAID_ATTENDANCE", "SCANNED_ATTENDANCE"})


@dataclass
class ForwardInventorySnapshot:
    snapshot_id: str
    classification: str
    retrieved_at: str
    rights_status: str
    commercial_use_status: str
    observation_class: str
    event_external_id: str | None = None
    artist: str | None = None
    venue: str | None = None
    market: str | None = None
    event_date: str | None = None
    general_sale_date: str | None = None
    snapshot_time: str | None = None
    estimated_capacity: float | None = None
    tickets_available: float | None = None
    tickets_distributed_or_sold_as_reported: float | None = None
    source_methodology: str | None = None
    source_url: str | None = None
    source_publication_time: str | None = None
    software_version: str = "public_boxscore_research_corpus_v2"

    def __post_init__(self) -> None:
        if self.classification not in INVENTORY_CLASSES:
            raise ValueError(
                f"classification {self.classification!r} is not an inventory signal class"
            )
        if self.classification == INVENTORY_CLASS_INFERRED and not self.source_methodology:
            raise ValueError("INFERRED_INVENTORY_SIGNAL requires source_methodology")

    def to_row(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def build(cls, **kwargs: Any) -> "ForwardInventorySnapshot":
        kwargs.setdefault("retrieved_at", utc_now().isoformat())
        kwargs.setdefault("classification", INVENTORY_CLASS_INFERRED)
        kwargs.setdefault("rights_status", RIGHTS_TERMS_REVIEW_REQUIRED)
        kwargs.setdefault("commercial_use_status", RIGHTS_TERMS_REVIEW_REQUIRED)
        kwargs.setdefault("observation_class", OBSERVED_PUBLIC)
        kwargs.setdefault("software_version", "public_boxscore_research_corpus_v2")
        snapshot_id = kwargs.pop(
            "snapshot_id",
            "inv_" + content_hash_of({
                "event": kwargs.get("event_external_id"),
                "snapshot_time": kwargs.get("snapshot_time"),
                "source_url": kwargs.get("source_url"),
            })[:20],
        )
        return cls(snapshot_id=snapshot_id, **kwargs)


def assert_not_promoted_to_outcome(outcome_type: str) -> None:
    """Guard: an inventory signal must never be promoted to a settled outcome."""
    if outcome_type in _FORBIDDEN_PROMOTION:
        raise ValueError(
            f"forward inventory snapshot cannot be promoted to {outcome_type!r}"
        )
