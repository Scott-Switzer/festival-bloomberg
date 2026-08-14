"""Extensible market-entity registry.

Search queries are never evidence of market relation. A source object may be
assigned ``market_id`` only when independently supported by the object itself
(explicit source text, a trusted event relation, or a public geotag).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHICAGO_MARKET_ID = "Chicago, IL"

#: Explicit Chicago-context entities. Patterns are word-bounded where the
#: term is a common word; multi-word venue/festival names are literal.
CHICAGO_ENTITIES: tuple[dict[str, str], ...] = (
    {"term": "Chicago", "kind": "city", "pattern": r"\bChicago\b"},
    {"term": "United Center", "kind": "venue", "pattern": r"\bUnited Center\b"},
    {"term": "Soldier Field", "kind": "venue", "pattern": r"\bSoldier Field\b"},
    {"term": "Grant Park", "kind": "venue", "pattern": r"\bGrant Park\b"},
    {"term": "Lollapalooza", "kind": "festival", "pattern": r"\bLollapalooza\b"},
)


@dataclass(frozen=True)
class MarketAssignment:
    market_id: str | None
    method: str
    matched_terms: tuple[str, ...] = ()

    @property
    def assigned(self) -> bool:
        return self.market_id is not None and self.method != "UNKNOWN"


def chicago_entities() -> tuple[dict[str, str], ...]:
    return CHICAGO_ENTITIES


def assign_source_object_market(
    *,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    search_query: str | None = None,
    trusted_event_relation: bool = False,
    public_geotag_market_id: str | None = None,
) -> MarketAssignment:
    """Assign source-object market context.

    ``search_query`` is accepted only so callers can prove it was *not* used.
    A Chicago term in the search string never assigns ``market_id``.
    """
    del search_query  # explicitly unused: search is not evidence
    if public_geotag_market_id:
        return MarketAssignment(
            market_id=public_geotag_market_id,
            method="PUBLIC_GEOTAG",
        )
    if trusted_event_relation:
        return MarketAssignment(
            market_id=CHICAGO_MARKET_ID,
            method="TRUSTED_EVENT_RELATION",
        )

    haystacks = [title or "", description or ""]
    if tags:
        haystacks.append(" ".join(str(tag) for tag in tags if tag))
    blob = "\n".join(haystacks)
    matched: list[str] = []
    for entity in CHICAGO_ENTITIES:
        if re.search(entity["pattern"], blob, flags=re.IGNORECASE):
            matched.append(entity["term"])
    if matched:
        return MarketAssignment(
            market_id=CHICAGO_MARKET_ID,
            method="EXPLICIT_SOURCE_TEXT",
            matched_terms=tuple(matched),
        )
    return MarketAssignment(market_id=None, method="UNKNOWN")
