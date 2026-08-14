"""Append-only primary and secondary ticket-market snapshots.

Each retrieval is a new observation. Historical rows are never updated
in place. Event start time is not knowledge_time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

PRIMARY_CONCEPT = "PRIMARY_TICKET_MARKET_SNAPSHOT"
SECONDARY_CONCEPT = "SECONDARY_TICKET_MARKET_SNAPSHOT"
FEES_UNKNOWN = "UNKNOWN"


def snapshot_bucket(retrieved_at: datetime | str) -> str:
    text = retrieved_at.isoformat() if isinstance(retrieved_at, datetime) else str(retrieved_at)
    return text[:16]  # YYYY-MM-DDTHH:MM


def map_ticketmaster_status(code: str | None) -> str:
    raw = (code or "").strip().lower()
    mapping = {
        "onsale": "ONSALE",
        "offsale": "OFFSALE",
        "canceled": "CANCELED",
        "cancelled": "CANCELED",
        "postponed": "POSTPONED",
        "rescheduled": "RESCHEDULED",
    }
    return mapping.get(raw, "UNKNOWN" if not raw else raw.upper())


@dataclass
class PrimaryTicketSnapshot:
    snapshot_id: str
    canonical_event_id: str | None
    provider: str
    provider_event_id: str | None
    retrieved_at: str
    knowledge_time: str
    snapshot_bucket: str
    currency: str | None
    price_type: str | None
    minimum_price: float | None
    maximum_price: float | None
    fees_included: str
    event_status: str | None
    public_onsale_start: str | None
    public_onsale_end: str | None
    source_url: str | None
    raw_observation_id: str | None
    raw_payload_hash: str | None

    def to_row(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class SecondaryTicketSnapshot:
    snapshot_id: str
    canonical_event_id: str | None
    provider: str
    provider_event_id: str | None
    retrieved_at: str
    knowledge_time: str
    snapshot_bucket: str
    currency: str | None
    listing_count: int | None
    lowest_price: float | None
    average_price: float | None
    highest_price: float | None
    median_price: float | None
    provider_score: float | None
    source_url: str | None
    raw_observation_id: str | None
    raw_payload_hash: str | None

    def to_row(self) -> dict[str, Any]:
        return self.__dict__.copy()


def primary_snapshots_from_ticketmaster(
    record: dict[str, Any],
    *,
    canonical_event_id: str | None,
    raw_observation_id: str | None,
    retrieved_at: datetime | None = None,
) -> list[PrimaryTicketSnapshot]:
    retrieved = retrieved_at or utc_now()
    retrieved_iso = retrieved.isoformat() if isinstance(retrieved, datetime) else str(retrieved)
    if record.get("retrieved_at"):
        retrieved_iso = str(record["retrieved_at"])
    knowledge = str(record.get("knowledge_time") or retrieved_iso)
    ranges = record.get("price_ranges") if isinstance(record.get("price_ranges"), list) else []
    if not ranges:
        ranges = [{}]
    snapshots: list[PrimaryTicketSnapshot] = []
    event_id = record.get("ticketmaster_event_id") or record.get("platform_object_id")
    status = record.get("event_status")
    for index, price_range in enumerate(ranges):
        payload_hash = content_hash_of(
            {
                "event": event_id,
                "range": price_range,
                "status": status,
                "retrieved": retrieved_iso,
            }
        )
        snapshots.append(
            PrimaryTicketSnapshot(
                snapshot_id=f"pts_{payload_hash[:16]}_{index}",
                canonical_event_id=canonical_event_id,
                provider="ticketmaster_official_api",
                provider_event_id=str(event_id) if event_id else None,
                retrieved_at=retrieved_iso,
                knowledge_time=knowledge,
                snapshot_bucket=snapshot_bucket(retrieved_iso),
                currency=price_range.get("currency") if isinstance(price_range, dict) else None,
                price_type=price_range.get("type") if isinstance(price_range, dict) else None,
                minimum_price=_float(price_range.get("min") if isinstance(price_range, dict) else None),
                maximum_price=_float(price_range.get("max") if isinstance(price_range, dict) else None),
                fees_included=FEES_UNKNOWN,
                event_status=status,
                public_onsale_start=record.get("onsale_start"),
                public_onsale_end=record.get("onsale_end"),
                source_url=record.get("canonical_url"),
                raw_observation_id=raw_observation_id,
                raw_payload_hash=payload_hash,
            )
        )
    return snapshots


def secondary_snapshot_from_seatgeek(
    record: dict[str, Any],
    *,
    canonical_event_id: str | None,
    raw_observation_id: str | None,
    retrieved_at: datetime | None = None,
) -> SecondaryTicketSnapshot:
    retrieved = retrieved_at or utc_now()
    retrieved_iso = retrieved.isoformat() if isinstance(retrieved, datetime) else str(retrieved)
    if record.get("retrieved_at"):
        retrieved_iso = str(record["retrieved_at"])
    knowledge = str(record.get("knowledge_time") or retrieved_iso)
    event_id = record.get("seatgeek_event_id") or record.get("platform_object_id")
    payload_hash = content_hash_of(
        {
            "event": event_id,
            "listing_count": record.get("listing_count"),
            "lowest": record.get("lowest_price"),
            "avg": record.get("average_price"),
            "high": record.get("highest_price"),
            "retrieved": retrieved_iso,
        }
    )
    return SecondaryTicketSnapshot(
        snapshot_id=f"sts_{payload_hash[:16]}",
        canonical_event_id=canonical_event_id,
        provider="seatgeek_official_api",
        provider_event_id=str(event_id) if event_id else None,
        retrieved_at=retrieved_iso,
        knowledge_time=knowledge,
        snapshot_bucket=snapshot_bucket(retrieved_iso),
        currency="USD",
        listing_count=_int(record.get("listing_count")),
        lowest_price=_float(record.get("lowest_price")),
        average_price=_float(record.get("average_price")),
        highest_price=_float(record.get("highest_price")),
        median_price=_float(record.get("median_price")),
        provider_score=_float(record.get("provider_score")),
        source_url=record.get("canonical_url"),
        raw_observation_id=raw_observation_id,
        raw_payload_hash=payload_hash,
    )


def snapshot_deltas(earlier: dict[str, Any], later: dict[str, Any], *, fields: tuple[str, ...]) -> dict[str, Any]:
    out = {
        "earlier_snapshot_id": earlier.get("snapshot_id"),
        "later_snapshot_id": later.get("snapshot_id"),
        "concept": "PRICE_SNAPSHOT_DELTA",
    }
    for field_name in fields:
        a = earlier.get(field_name)
        b = later.get(field_name)
        if a is None or b is None:
            out[f"{field_name}_delta"] = None
        else:
            try:
                out[f"{field_name}_delta"] = float(b) - float(a)
            except (TypeError, ValueError):
                out[f"{field_name}_delta"] = None
    return out


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
