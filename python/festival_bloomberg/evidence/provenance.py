"""Provenance helpers: knowledge_time and validity windows.

``knowledge_time`` is the earliest defensible timestamp at which *this
system* knew the observation. Live mutable retrievals (YouTube comments,
video statistics) use retrieval time. Immutable revision identity (Wikipedia
revisions) may use the proven revision timestamp. Source publication time is
stored separately and never silently becomes knowledge_time.
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


def retrieval_knowledge_time(retrieved_at: datetime | str) -> datetime:
    """Knowledge time for live mutable retrievals (YouTube comments, stats).

    Source ``publishedAt`` is stored separately. Festival Bloomberg only
    learned the current representation at retrieval time unless an immutable
    historical snapshot/version is independently proven.
    """
    retrieved = utc(parse_iso(retrieved_at) if isinstance(retrieved_at, str) else retrieved_at)
    if retrieved is None:
        retrieved = utc(datetime.now(timezone.utc))
    assert retrieved is not None
    return retrieved


def knowledge_time_for(
    published_at: datetime | str | None,
    retrieved_at: datetime | str,
) -> datetime:
    """Publication-backed knowledge time — only when that is defensible.

    Callers that lack an immutable snapshot MUST use
    :func:`retrieval_knowledge_time` instead of this helper. Uses
    ``published_at`` only when it is present and not later than
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
