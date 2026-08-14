"""Link YouTube source objects to canonical live events.

A video is linked ONLY with explicit evidence:
- Chicago venue mention + artist + compatible date, or
- explicit festival name + artist + edition/year/date, or
- canonical event identifier/URL

Search query membership is never a link. Comments on a linked video are
comments on content linked to EVENT X — not commenter residence.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..markets.chicago import CHICAGO_MARKET_ID


def _blob(video: dict[str, Any]) -> str:
    parts = [video.get("text") or "", video.get("title") or "", video.get("description") or ""]
    meta = video.get("metadata_json") or {}
    if isinstance(meta, dict):
        parts.append(str(meta.get("description") or ""))
        parts.append(str(meta.get("text") or ""))
    return "\n".join(parts)


def _contains(haystack: str, needle: str | None) -> bool:
    if not needle:
        return False
    return re.search(rf"\b{re.escape(needle)}\b", haystack, flags=re.IGNORECASE) is not None


def _date_compatible(blob: str, local_date: str | None) -> tuple[bool, str | None]:
    if not local_date:
        return False, None
    year = str(local_date)[:4]
    compact = str(local_date)[:10]
    if compact and compact in blob:
        return True, compact
    # ISO date with slashes
    alt = compact.replace("-", "/") if compact else ""
    if alt and alt in blob:
        return True, alt
    if year and re.search(rf"\b{re.escape(year)}\b", blob):
        return True, year
    return False, None


def link_video_to_events(
    video: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    artist_name: str,
    search_query: str | None = None,
) -> list[dict[str, Any]]:
    """Return explicit event links. Search query is ignored as evidence."""
    del search_query
    blob = _blob(video)
    video_id = video.get("platform_object_id") or video.get("video_id")
    if not video_id or not blob.strip():
        return []
    if not _contains(blob, artist_name):
        return []

    links: list[dict[str, Any]] = []
    for event in events:
        venue = event.get("venue_name")
        festival = event.get("festival_name")
        local_date = str(event.get("local_date") or "")[:10] or None
        date_ok, date_evidence = _date_compatible(blob, local_date)
        if venue and _contains(blob, venue) and date_ok:
            links.append(
                {
                    "youtube_video_id": video_id,
                    "canonical_event_id": event["event_id"],
                    "link_method": "EXPLICIT_VENUE_AND_DATE",
                    "supporting_evidence": f"venue={venue}; date={date_evidence}",
                    "confidence_state": "EXPLICIT",
                }
            )
            continue
        if festival and _contains(blob, festival) and date_ok:
            links.append(
                {
                    "youtube_video_id": video_id,
                    "canonical_event_id": event["event_id"],
                    "link_method": "EXPLICIT_FESTIVAL_AND_DATE",
                    "supporting_evidence": f"festival={festival}; date={date_evidence}",
                    "confidence_state": "EXPLICIT",
                }
            )
            continue
        source_url = (video.get("canonical_url") or "") + " " + blob
        event_url = event.get("canonical_url") or ""
        if event_url and event_url in source_url:
            links.append(
                {
                    "youtube_video_id": video_id,
                    "canonical_event_id": event["event_id"],
                    "link_method": "CANONICAL_EVENT_URL",
                    "supporting_evidence": event_url,
                    "confidence_state": "EXPLICIT",
                }
            )
    return links


def event_linked_fan_status(
    *,
    events: list[dict[str, Any]],
    links: list[dict[str, Any]],
    fan_comments: list[dict[str, Any]],
) -> str:
    if not events:
        return "INSUFFICIENT_EVIDENCE"
    if not links:
        return "INSUFFICIENT_EVIDENCE"
    linked_videos = {link["youtube_video_id"] for link in links}
    fans = [
        c
        for c in fan_comments
        if (c.get("video_id") or c.get("parent_object_id")) in linked_videos
    ]
    if not fans:
        return "INSUFFICIENT_EVIDENCE"
    return "PASS"
