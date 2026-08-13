"""Provenance helpers: knowledge_time and validity windows.

``knowledge_time`` is the earliest defensible timestamp at which the
information is evidenced to have been knowable. For public observations the
source publication time is used when it is trustworthy and not in the
future; otherwise retrieval time is the conservative fallback. A timestamp
can never be invented to make data "older" than it is.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return utc(parsed)


def knowledge_time_for(
    published_at: datetime | str | None,
    retrieved_at: datetime | str,
) -> datetime:
    """Earliest defensible knowledge time.

    Uses ``published_at`` only when it is present and not later than
    ``retrieved_at``; otherwise falls back to retrieval time. Never invents
    an earlier timestamp.
    """
    retrieved = utc(parse_iso(retrieved_at) if isinstance(retrieved_at, str) else retrieved_at)
    if retrieved is None:
        retrieved = utc(datetime.now(timezone.utc))
    published = (
        parse_iso(published_at) if isinstance(published_at, str) else utc(published_at)
    )
    if published is not None and published <= retrieved:
        return published
    return retrieved


def valid_at(valid_from, valid_to, at: datetime) -> bool:
    """True when the validity window covers ``at`` (open-ended allowed)."""
    at = utc(at)
    if valid_from is not None and utc(valid_from) > at:
        return False
    if valid_to is not None and utc(valid_to) <= at:
        return False
    return True
