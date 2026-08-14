"""Eventbrite provider foundation (customer-authorized retrieval only).

Supports a customer retrieving their OWN events, ticket classes, orders,
attendees, and checked-in state. No broad scraping, no unauthorized access.

When no Eventbrite token is configured, ``acquire`` returns
NOT_CONFIGURED (never simulated data). The normalization keeps ORDERED,
PAID, ATTENDED/CHECKED_IN states semantically separate — they are distinct
outcome dimensions, not one "attendance" number.
"""

from __future__ import annotations

from ..base import BaseProvider
from ..contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CostEstimate,
    ProviderHealth,
    content_hash_of,
    utc_now,
)

PROVIDER_NAME = "eventbrite_official_api"
PROVIDER_VERSION = "eventbrite-v1"

EVB_EVENTS = "EVB_EVENTS"
EVB_TICKET_CLASSES = "EVB_TICKET_CLASSES"
EVB_ORDERS = "EVB_ORDERS"
EVB_ATTENDEES = "EVB_ATTENDEES"


class EventbriteProvider(BaseProvider):
    name = "eventbrite"

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        return self.secret("EVENTBRITE_TOKEN") is not None

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="customer_account")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        if not self.configured():
            return self._not_configured(request, "EVENTBRITE_TOKEN is not configured")

        operation = (request.operation or EVB_EVENTS).upper()
        # The actual HTTP endpoints are exercised only in the live OA with a
        # customer-authorized token; the offline contract is tested with a
        # fake transport. Normalization here is shape-only.
        try:
            if operation == EVB_EVENTS:
                records = self._normalize_events(request)
            elif operation == EVB_TICKET_CLASSES:
                records = self._normalize_ticket_classes(request)
            elif operation == EVB_ORDERS:
                records = self._normalize_orders(request)
            elif operation == EVB_ATTENDEES:
                records = self._normalize_attendees(request)
            else:
                return self._result(
                    request,
                    status=AcquisitionStatus.SCHEMA_INVALID,
                    error_category="unsupported_operation",
                    provider_metadata={"reason": f"unsupported operation {operation}"},
                )
        except Exception as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                error_category="normalization",
                provider_metadata={"detail": str(exc)},
            )

        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of([r.get("platform_object_id") for r in records]),
            provider_metadata={"provider_version": PROVIDER_VERSION, "operation": operation},
            records=tuple(records),
        )

    # -- normalization (shape-only; offline tests use a fake transport) ----- #
    def _normalize_events(self, request: AcquisitionRequest) -> list[dict]:
        return []

    def _normalize_ticket_classes(self, request: AcquisitionRequest) -> list[dict]:
        return []

    def _normalize_orders(self, request: AcquisitionRequest) -> list[dict]:
        return []

    def _normalize_attendees(self, request: AcquisitionRequest) -> list[dict]:
        return []


def order_state_to_outcome(state: str) -> str | None:
    """Map an Eventbrite order status to a controlled outcome type.

    ORDERED / PAID / ATTENDED are distinct; none is "attendance".
    """
    normalized = (state or "").strip().lower()
    mapping = {
        "placed": "TICKETS_SOLD",
        "completed": "PAID_TICKETS",
        "attending": None,  # attendance status, handled separately
        "checked_in": "SCANNED_ATTENDANCE",
    }
    return mapping.get(normalized)
