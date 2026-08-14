"""FORWARD_WATCH — the data that becomes impossible to recreate later.

Every legally observable future event enters a forward watchlist:

    EVENT DISCOVERED -> ANNOUNCEMENT -> PRESALE -> ONSALE -> D+1 -> D+3 ->
    D+7 -> D+14 -> WEEKLY -> T-30 -> T-14 -> T-7 -> T-3 -> T-1 -> SHOW ->
    SETTLEMENT

Each observation preserves status, price range, ticket classes, listing
counts, secondary aggregates, inventory when legitimately exposed, venue
configuration, and the change since the last observation. Public Discovery-API
status is NOT an internal ticket-count feed — that distinction stays explicit
in ``observation_class`` / ``source_provider``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

#: Ordered milestone ladder (the memo's forward-capture cadence).
MILESTONE_LADDER: tuple[str, ...] = (
    "DISCOVERED",
    "ANNOUNCEMENT",
    "PRESALE",
    "ONSALE",
    "D+1",
    "D+3",
    "D+7",
    "D+14",
    "WEEKLY",
    "T-30",
    "T-14",
    "T-7",
    "T-3",
    "T-1",
    "SHOW",
    "SETTLEMENT",
)

#: Assumed settlement window after a show (documented assumption, not a fact).
SETTLEMENT_WINDOW_DAYS = 30

TRACKING_STATUSES = frozenset({"TRACKING", "SETTLED", "CANCELLED", "DROPPED"})


def validate_milestone(milestone: str) -> str:
    if milestone not in MILESTONE_LADDER:
        raise ValueError(f"milestone {milestone!r} is not in the forward ladder")
    return milestone


def compute_milestones(
    event_date: date | None,
    *,
    onsale_date: date | None = None,
    first_seen: datetime | None = None,
) -> list[dict[str, Any]]:
    """Schedule the milestone ladder for one event.

    ``due_at`` is None whenever the anchor is unknown (announcement, presale,
    and onsale-relative windows before the onsale date is observed). Every
    entry carries a ``basis`` so a consumer can see what the date means and
    never mistakes an assumption for a fact.
    """
    ladder: list[dict[str, Any]] = []
    first_seen_at = first_seen or utc_now()

    ladder.append(
        {"milestone": "DISCOVERED", "due_at": first_seen_at.isoformat(), "basis": "first_seen"}
    )
    for milestone in ("ANNOUNCEMENT", "PRESALE"):
        ladder.append({"milestone": milestone, "due_at": None, "basis": "observed_live"})

    if onsale_date:
        ladder.append({"milestone": "ONSALE", "due_at": onsale_date.isoformat(), "basis": "onsale_date"})
    else:
        ladder.append({"milestone": "ONSALE", "due_at": None, "basis": "observed_live"})

    # D+N milestones mean DAYS AFTER ONSALE. They are anchored ONLY to a known
    # onsale date; an unknown onsale date means the D+N timestamps are unknown
    # (UNKNOWN_ONSALE != EVENT_DATE — the event-relative T-N ladder runs
    # independently below and must never be conflated with onsale-relative
    # capture windows).
    for offset in (1, 3, 7, 14):
        if onsale_date:
            due = onsale_date + timedelta(days=offset)
            basis = "onsale+offset"
        else:
            due = None
            basis = "onsale_unknown"
        ladder.append({"milestone": f"D+{offset}", "due_at": due.isoformat() if due else None, "basis": basis})

    ladder.append({"milestone": "WEEKLY", "due_at": None, "basis": "recurring_weekly"})

    if event_date:
        for offset in (30, 14, 7, 3, 1):
            due = event_date - timedelta(days=offset)
            ladder.append(
                {"milestone": f"T-{offset}", "due_at": due.isoformat(), "basis": "event-relative"}
            )
        ladder.append({"milestone": "SHOW", "due_at": event_date.isoformat(), "basis": "event"})
        settlement = event_date + timedelta(days=SETTLEMENT_WINDOW_DAYS)
        ladder.append(
            {
                "milestone": "SETTLEMENT",
                "due_at": settlement.isoformat(),
                "basis": f"assumption: {SETTLEMENT_WINDOW_DAYS}-day settlement window",
            }
        )
    else:
        for offset in (30, 14, 7, 3, 1):
            ladder.append({"milestone": f"T-{offset}", "due_at": None, "basis": "event_unknown"})
        ladder.append({"milestone": "SHOW", "due_at": None, "basis": "event_unknown"})
        ladder.append({"milestone": "SETTLEMENT", "due_at": None, "basis": "event_unknown"})

    return ladder


def register_event_row(
    *,
    provider: str,
    provider_event_id: str,
    artist_name: str | None = None,
    venue_name: str | None = None,
    market: str | None = None,
    event_date: date | None = None,
    event_time: datetime | None = None,
    event_status: str | None = None,
    first_seen_at: datetime | None = None,
    source_url: str | None = None,
    rights_status: str,
    commercial_use_status: str,
    observation_class: str,
    software_version: str = "data_flywheel_and_coverage_v1",
    tracking_status: str = "TRACKING",
) -> dict[str, Any]:
    """Build a ``flywheel.forward_watch_events`` row (pure)."""
    if tracking_status not in TRACKING_STATUSES:
        raise ValueError(f"tracking_status {tracking_status!r} is invalid")
    first_seen = first_seen_at or utc_now()
    watch_id = f"watch_{content_hash_of({
        'provider': provider,
        'event': provider_event_id,
        'seen': first_seen.isoformat(),
    })[:20]}"
    return {
        "watch_event_id": watch_id,
        "provider": provider,
        "provider_event_id": provider_event_id,
        "artist_name": artist_name,
        "venue_name": venue_name,
        "market": market,
        "event_date": event_date.isoformat() if event_date else None,
        "event_time": event_time.isoformat() if event_time else None,
        "event_status": event_status,
        "first_seen_at": first_seen.isoformat(),
        "tracking_started_at": first_seen.isoformat(),
        "tracking_status": tracking_status,
        "knowledge_time": first_seen.isoformat(),
        "source_url": source_url,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "observation_class": observation_class,
        "software_version": software_version,
    }


def inventory_change(previous: float | None, current: float | None) -> float | None:
    """Change in legitimately-exposed inventory since the last observation."""
    if previous is None or current is None:
        return None
    return current - previous


def build_observation_row(
    *,
    watch_event_id: str,
    observed_at: datetime | None = None,
    milestone: str | None = None,
    event_status: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    currency: str | None = None,
    ticket_classes: list[dict[str, Any]] | None = None,
    listing_count: int | None = None,
    secondary_lowest_price: float | None = None,
    secondary_median_price: float | None = None,
    inventory_available: float | None = None,
    inventory_change_since_last: float | None = None,
    venue_configuration: str | None = None,
    source_provider: str,
    source_url: str | None = None,
    rights_status: str,
    commercial_use_status: str,
    observation_class: str,
    software_version: str = "data_flywheel_and_coverage_v1",
    raw_payload_hash: str | None = None,
) -> dict[str, Any]:
    """Build a ``flywheel.forward_watch_observations`` row (pure)."""
    if milestone is not None:
        validate_milestone(milestone)
    now = observed_at or utc_now()
    observation_id = f"fwo_{content_hash_of({
        'watch': watch_event_id,
        'at': now.isoformat(),
        'provider': source_provider,
        'status': event_status,
        'raw': raw_payload_hash or '',
    })[:20]}"
    return {
        "observation_id": observation_id,
        "watch_event_id": watch_event_id,
        "observed_at": now.isoformat(),
        "retrieved_at": now.isoformat(),
        "knowledge_time": now.isoformat(),
        "milestone": milestone,
        "event_status": event_status,
        "price_min": price_min,
        "price_max": price_max,
        "currency": currency,
        "ticket_classes": ticket_classes,
        "listing_count": listing_count,
        "secondary_lowest_price": secondary_lowest_price,
        "secondary_median_price": secondary_median_price,
        "inventory_available": inventory_available,
        "inventory_change_since_last": inventory_change_since_last,
        "venue_configuration": venue_configuration,
        "source_provider": source_provider,
        "source_url": source_url,
        "raw_payload_hash": raw_payload_hash,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "observation_class": observation_class,
        "software_version": software_version,
    }
