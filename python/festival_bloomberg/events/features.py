"""Descriptive artist × market vectors. No composite score."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..evidence.semantics import is_fan_role
from .repository import EventRepository


def build_artist_market_vector(
    events_repo: EventRepository,
    *,
    artist_id: str,
    market_id: str,
    as_of: datetime,
    fan_comments: list[dict[str, Any]] | None = None,
    event_links: list[dict[str, Any]] | None = None,
    inferences: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    events = events_repo.query_events(artist_id=artist_id, market_id=market_id, cutoff=as_of)
    historical = [e for e in events if _is_past(e, as_of)]
    upcoming = [e for e in events if not _is_past(e, as_of)]
    window_start = (as_of - timedelta(days=365)).date().isoformat()
    recent = [e for e in historical if str(e.get("local_date") or "") >= window_start]
    venues = [e.get("venue_name") for e in historical if e.get("venue_name")]
    unique_venues = sorted(set(venues))
    repeat_venues = sorted({v for v in unique_venues if venues.count(v) > 1})
    last_hist = max((str(e.get("local_date")) for e in historical if e.get("local_date")), default=None)
    first_hist = min((str(e.get("local_date")) for e in historical if e.get("local_date")), default=None)
    next_up = min((str(e.get("local_date")) for e in upcoming if e.get("local_date")), default=None)
    days_since = None
    if last_hist:
        last_dt = datetime.fromisoformat(str(last_hist)[:10])
        days_since = (as_of.date() - last_dt.date()).days

    linked_video_ids = {link["youtube_video_id"] for link in (event_links or [])}
    linked_comments = [
        c
        for c in (fan_comments or [])
        if is_fan_role(c.get("content_role"))
        and (c.get("video_id") or c.get("parent_object_id")) in linked_video_ids
    ]
    supporting = []
    for event in events:
        supporting.extend(event.get("supporting_observation_ids") or [])
    supporting.extend(c.get("observation_id") for c in linked_comments if c.get("observation_id"))

    sentiment = _label_shares(inferences.get("SENTIMENT") if inferences else None, linked_comments)
    intent = _intent_shares(inferences, linked_comments)

    return {
        "artist_id": artist_id,
        "market_id": market_id,
        "historical_chicago_performance_count": len(historical),
        "recent_chicago_performance_count_12m": len(recent),
        "days_since_last_chicago_performance": days_since,
        "upcoming_chicago_event_count": len(upcoming),
        "unique_chicago_venues": len(unique_venues),
        "repeat_chicago_venues": len(repeat_venues),
        "festival_appearance_count": sum(1 for e in historical if e.get("event_type") == "FESTIVAL_APPEARANCE"),
        "standalone_concert_count": sum(1 for e in historical if e.get("event_type") == "STANDALONE_CONCERT"),
        "named_tours": sorted({e.get("tour_name") for e in historical if e.get("tour_name")}),
        "first_observed_chicago_performance_date": first_hist,
        "most_recent_historical_chicago_performance_date": last_hist,
        "next_upcoming_chicago_event_date": next_up,
        "venue_sequence": [
            {"date": str(e.get("local_date")), "venue": e.get("venue_name")}
            for e in sorted(historical, key=lambda row: str(row.get("local_date") or ""))
            if e.get("venue_name")
        ],
        "event_linked_comment_count": len(linked_comments),
        "event_linked_unique_authors": len(
            {c.get("author_public_id") for c in linked_comments if c.get("author_public_id")}
        ),
        "event_linked_sentiment_distribution": sentiment,
        "event_linked_intent_distribution": intent,
        "intent_label": "EXPERIMENTAL_HEURISTIC_NOT_VALIDATED",
        "supporting_observation_ids": [s for s in supporting if s],
        "no_demand_score": True,
    }


def _is_past(event: dict[str, Any], as_of: datetime) -> bool:
    local_date = event.get("local_date")
    if not local_date:
        return True
    try:
        return datetime.fromisoformat(str(local_date)[:10]).date() < as_of.date()
    except ValueError:
        return True


def _label_shares(inferences: list[dict[str, Any]] | None, comments: list[dict[str, Any]]) -> dict[str, float | None]:
    if not comments:
        return {"positive": None, "neutral": None, "negative": None}
    by_obs = {row["observation_id"]: row.get("label") for row in (inferences or [])}
    labels = [by_obs.get(c["observation_id"]) for c in comments if c.get("observation_id") in by_obs]
    if not labels:
        return {"positive": None, "neutral": None, "negative": None}
    n = len(labels)
    return {
        "positive": labels.count("positive") / n,
        "neutral": labels.count("neutral") / n,
        "negative": labels.count("negative") / n,
        "note": "sentiment within sampled event-linked comments, not fanbase sentiment",
    }


def _intent_shares(inferences: dict[str, list[dict[str, Any]]] | None, comments: list[dict[str, Any]]) -> dict[str, Any]:
    if not inferences:
        return {}
    ids = {c["observation_id"] for c in comments if c.get("observation_id")}
    out: dict[str, Any] = {}
    for task, rows in inferences.items():
        if task == "SENTIMENT":
            continue
        labels = [row.get("label") for row in rows if row.get("observation_id") in ids]
        if not labels:
            out[task] = None
        else:
            out[task] = {label: labels.count(label) / len(labels) for label in sorted(set(labels))}
    return out
