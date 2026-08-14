"""Ticketmaster ↔ SeatGeek event resolution.

GATE 1: exact shared external event ID
GATE 2: same canonical artist + local date + canonical venue
GATE 3: same artist + date + strong venue-name match = REVIEWABLE_MATCH

Artist + date alone never merges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..events.reconcile import canonical_venue_id, normalize_venue_name, strong_venue_match


@dataclass
class EventMatch:
    canonical_event_id: str
    ticketmaster_event_id: str | None
    seatgeek_event_id: str | None
    gate: str
    artist_id: str | None
    local_date: str | None
    venue_name: str | None


def match_ticketmaster_seatgeek(
    *,
    ticketmaster: dict[str, Any],
    seatgeek: dict[str, Any],
    canonical_event_id: str,
    artist_id: str | None,
) -> EventMatch | None:
    tm_id = ticketmaster.get("ticketmaster_event_id") or ticketmaster.get("platform_object_id")
    sg_id = seatgeek.get("seatgeek_event_id") or seatgeek.get("platform_object_id")
    if tm_id and sg_id and str(tm_id) == str(sg_id):
        return EventMatch(
            canonical_event_id=canonical_event_id,
            ticketmaster_event_id=str(tm_id),
            seatgeek_event_id=str(sg_id),
            gate="GATE_1_EXTERNAL_ID",
            artist_id=artist_id,
            local_date=str(ticketmaster.get("local_date") or ""),
            venue_name=ticketmaster.get("venue_name"),
        )
    tm_date = str(ticketmaster.get("local_date") or "")
    sg_date = str(seatgeek.get("local_date") or str(seatgeek.get("datetime_local") or "")[:10])
    if not artist_id or not tm_date or tm_date != sg_date:
        return None
    tm_vid = ticketmaster.get("ticketmaster_venue_id") or canonical_venue_id(
        ticketmaster.get("venue_name"), ticketmaster.get("city")
    )
    sg_vid = seatgeek.get("seatgeek_venue_id")
    tm_venue = canonical_venue_id(ticketmaster.get("venue_name"), ticketmaster.get("city"))
    sg_venue = canonical_venue_id(seatgeek.get("venue_name"), seatgeek.get("city"))
    if tm_venue and sg_venue and tm_venue == sg_venue:
        return EventMatch(
            canonical_event_id=canonical_event_id,
            ticketmaster_event_id=str(tm_id) if tm_id else None,
            seatgeek_event_id=str(sg_id) if sg_id else None,
            gate="GATE_2_ARTIST_DATE_VENUE",
            artist_id=artist_id,
            local_date=tm_date,
            venue_name=ticketmaster.get("venue_name"),
        )
    if strong_venue_match(ticketmaster.get("venue_name"), seatgeek.get("venue_name")):
        same_city = normalize_venue_name(ticketmaster.get("city")) == normalize_venue_name(seatgeek.get("city"))
        if same_city:
            return EventMatch(
                canonical_event_id=canonical_event_id,
                ticketmaster_event_id=str(tm_id) if tm_id else None,
                seatgeek_event_id=str(sg_id) if sg_id else None,
                gate="GATE_3_REVIEWABLE_MATCH",
                artist_id=artist_id,
                local_date=tm_date,
                venue_name=ticketmaster.get("venue_name"),
            )
    return None
