"""Cross-provider event reconciliation.

GATE 1: exact shared external event ID
GATE 2: same canonical artist + local date + canonical venue
GATE 3: same canonical artist + local date + strong venue-name match (reviewable)

Artist + date alone never merges (festivals/multiple appearances).
Both provider observations are always preserved.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


def normalize_venue_name(name: str | None) -> str:
    folded = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", ascii_only.lower())
    return " ".join(cleaned.split())


@dataclass
class ProviderEvent:
    provider: str
    platform: str
    platform_object_id: str
    artist_id: str
    event_name: str | None
    local_date: str | None
    venue_id: str | None
    venue_name: str | None
    city: str | None
    event_type: str | None
    tour_name: str | None
    festival_name: str | None
    raw_observation_id: str | None
    knowledge_time: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Disagreement:
    dimension: str
    left_provider: str
    right_provider: str
    left_value: str | None
    right_value: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "left_provider": self.left_provider,
            "right_provider": self.right_provider,
            "left_value": self.left_value,
            "right_value": self.right_value,
        }


@dataclass
class ReconciledEvent:
    event_id: str
    match_gate: str
    members: list[ProviderEvent]
    disagreements: list[Disagreement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "match_gate": self.match_gate,
            "provider_count": len(self.members),
            "providers": [m.provider for m in self.members],
            "disagreements": [d.to_dict() for d in self.disagreements],
        }


def canonical_venue_id(venue_name: str | None, city: str | None) -> str | None:
    name = normalize_venue_name(venue_name)
    place = normalize_venue_name(city)
    if not name:
        return None
    return f"venue:{place}:{name}" if place else f"venue::{name}"


def strong_venue_match(left: str | None, right: str | None) -> bool:
    a = normalize_venue_name(left)
    b = normalize_venue_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


def _disagreements(left: ProviderEvent, right: ProviderEvent) -> list[Disagreement]:
    found: list[Disagreement] = []
    pairs = [
        ("date", left.local_date, right.local_date),
        ("venue", normalize_venue_name(left.venue_name), normalize_venue_name(right.venue_name)),
        ("city", (left.city or "").strip().lower(), (right.city or "").strip().lower()),
        ("event_type", left.event_type, right.event_type),
        ("artist", left.artist_id, right.artist_id),
        ("tour", left.tour_name, right.tour_name),
    ]
    for dimension, lval, rval in pairs:
        if lval and rval and lval != rval:
            found.append(
                Disagreement(
                    dimension=dimension,
                    left_provider=left.provider,
                    right_provider=right.provider,
                    left_value=str(lval),
                    right_value=str(rval),
                )
            )
    return found


def reconcile_events(observations: list[ProviderEvent]) -> list[ReconciledEvent]:
    """Cluster provider observations. Never drop a raw observation."""
    unmatched = list(observations)
    clusters: list[ReconciledEvent] = []
    used: set[int] = set()

    def take(index: int, gate: str, partners: list[int]) -> None:
        members = [unmatched[index]] + [unmatched[i] for i in partners]
        disagreements: list[Disagreement] = []
        for partner in partners:
            disagreements.extend(_disagreements(unmatched[index], unmatched[partner]))
        event_id = f"evt_{unmatched[index].platform}_{unmatched[index].platform_object_id}"
        if partners:
            event_id = f"evt_{unmatched[index].artist_id}_{unmatched[index].local_date}_{canonical_venue_id(unmatched[index].venue_name, unmatched[index].city) or index}"
        clusters.append(
            ReconciledEvent(
                event_id=event_id,
                match_gate=gate,
                members=members,
                disagreements=disagreements,
            )
        )
        used.add(index)
        used.update(partners)

    # GATE 1: exact shared platform object id across providers (rare)
    by_ext: dict[str, list[int]] = {}
    for i, obs in enumerate(unmatched):
        if obs.platform_object_id:
            by_ext.setdefault(obs.platform_object_id, []).append(i)
    for indexes in by_ext.values():
        providers = {unmatched[i].provider for i in indexes}
        if len(providers) > 1:
            take(indexes[0], "GATE_1_EXTERNAL_ID", indexes[1:])

    remaining = [i for i in range(len(unmatched)) if i not in used]
    consumed: set[int] = set()
    for i in remaining:
        if i in consumed:
            continue
        left = unmatched[i]
        partners_g2: list[int] = []
        partners_g3: list[int] = []
        for j in remaining:
            if j == i or j in consumed:
                continue
            right = unmatched[j]
            if left.provider == right.provider:
                continue
            if left.artist_id != right.artist_id or not left.local_date or left.local_date != right.local_date:
                continue
            left_vid = left.venue_id or canonical_venue_id(left.venue_name, left.city)
            right_vid = right.venue_id or canonical_venue_id(right.venue_name, right.city)
            if left_vid and right_vid and left_vid == right_vid:
                partners_g2.append(j)
            elif strong_venue_match(left.venue_name, right.venue_name):
                partners_g3.append(j)
        if partners_g2:
            take(i, "GATE_2_ARTIST_DATE_VENUE", partners_g2)
            consumed.add(i)
            consumed.update(partners_g2)
        elif partners_g3:
            take(i, "GATE_3_REVIEWABLE", partners_g3)
            consumed.add(i)
            consumed.update(partners_g3)

    for i, obs in enumerate(unmatched):
        if i in used:
            continue
        clusters.append(
            ReconciledEvent(
                event_id=f"evt_{obs.platform}_{obs.platform_object_id}",
                match_gate="UNMATCHED",
                members=[obs],
            )
        )
    return clusters
