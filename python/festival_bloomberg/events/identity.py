"""Canonical artist identity across Ticketmaster, Setlist.fm, and YouTube.

One person, many external IDs. Fuzzy matches never become canonical.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..evidence.semantics import ResolutionMethod


def canonical_artist_id(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def normalize_artist_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(ascii_only.lower().strip().split())


@dataclass
class IdentityResolution:
    canonical_artist_id: str
    display_name: str
    musicbrainz_mbid: str | None = None
    ticketmaster_attraction_id: str | None = None
    setlistfm_mbid: str | None = None
    youtube_channel_id: str | None = None
    resolution_method: str = ResolutionMethod.UNRESOLVED.value
    setlist_status: str = "UNRESOLVED"
    ticketmaster_status: str = "UNRESOLVED"
    ambiguities: list[str] = field(default_factory=list)
    supporting_observation_ids: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.resolution_method not in {
            ResolutionMethod.UNRESOLVED.value,
            ResolutionMethod.FUZZY_REVIEW_REQUIRED.value,
            ResolutionMethod.UNKNOWN.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_artist_id": self.canonical_artist_id,
            "display_name": self.display_name,
            "musicbrainz_mbid": self.musicbrainz_mbid,
            "ticketmaster_attraction_id": self.ticketmaster_attraction_id,
            "setlistfm_mbid": self.setlistfm_mbid,
            "youtube_channel_id": self.youtube_channel_id,
            "resolution_method": self.resolution_method,
            "setlist_status": self.setlist_status,
            "ticketmaster_status": self.ticketmaster_status,
            "ambiguities": list(self.ambiguities),
            "supporting_observation_ids": list(self.supporting_observation_ids),
        }


def resolve_setlist_artists(query_name: str, artists: list[dict]) -> tuple[dict | None, str, list[str]]:
    """Return (match, method, ambiguities). Unique exact/normalized only."""
    target = normalize_artist_name(query_name)
    exact = [a for a in artists if normalize_artist_name(a.get("artist_name") or a.get("name") or "") == target]
    if len(exact) == 1:
        match = exact[0]
        raw_name = match.get("artist_name") or match.get("name") or ""
        method = (
            ResolutionMethod.EXACT_MBID.value
            if match.get("artist_mbid") or match.get("mbid")
            else ResolutionMethod.EXACT_ALIAS.value
        )
        if normalize_artist_name(raw_name) != target:
            method = ResolutionMethod.NORMALIZED_NAME_MATCH.value
        if raw_name.strip() == query_name.strip() and (match.get("artist_mbid") or match.get("mbid")):
            method = ResolutionMethod.EXACT_MBID.value
        elif raw_name.strip().lower() == query_name.strip().lower() and raw_name.strip() != query_name.strip():
            method = ResolutionMethod.NORMALIZED_NAME_MATCH.value
        return match, method, []
    if len(exact) > 1:
        ids = [a.get("artist_mbid") or a.get("mbid") or a.get("artist_name") for a in exact]
        return None, ResolutionMethod.UNRESOLVED.value, [f"ambiguous setlist artists: {ids}"]
    return None, ResolutionMethod.UNRESOLVED.value, ["no exact setlist artist match"]


def resolve_ticketmaster_attractions(query_name: str, attractions: list[dict]) -> tuple[dict | None, str, list[str]]:
    target = normalize_artist_name(query_name)
    exact = [
        a
        for a in attractions
        if normalize_artist_name(a.get("attraction_name") or a.get("name") or "") == target
    ]
    if len(exact) == 1:
        match = exact[0]
        raw_name = match.get("attraction_name") or match.get("name") or ""
        if raw_name.strip() == query_name.strip():
            method = ResolutionMethod.EXACT_PLATFORM_ID.value
        elif normalize_artist_name(raw_name) == target:
            method = ResolutionMethod.NORMALIZED_NAME_MATCH.value
        else:
            method = ResolutionMethod.EXACT_ALIAS.value
        if not (match.get("ticketmaster_attraction_id") or match.get("platform_object_id")):
            return None, ResolutionMethod.UNRESOLVED.value, ["ticketmaster match missing attraction id"]
        return match, method, []
    if len(exact) > 1:
        ids = [a.get("ticketmaster_attraction_id") or a.get("platform_object_id") for a in exact]
        return None, ResolutionMethod.UNRESOLVED.value, [f"ambiguous ticketmaster attractions: {ids}"]
    return None, ResolutionMethod.UNRESOLVED.value, ["no exact ticketmaster attraction match"]


def merge_identity(name: str, *, setlist: tuple, ticketmaster: tuple) -> IdentityResolution:
    sl_match, sl_method, sl_amb = setlist
    tm_match, tm_method, tm_amb = ticketmaster
    result = IdentityResolution(
        canonical_artist_id=canonical_artist_id(name),
        display_name=name,
        ambiguities=[*sl_amb, *tm_amb],
    )
    if sl_match:
        result.musicbrainz_mbid = sl_match.get("artist_mbid") or sl_match.get("mbid")
        result.setlistfm_mbid = result.musicbrainz_mbid
        result.setlist_status = sl_method
        result.supporting_observation_ids.append(sl_match.get("platform_object_id") or "")
    else:
        result.setlist_status = sl_method
    if tm_match:
        result.ticketmaster_attraction_id = tm_match.get("ticketmaster_attraction_id") or tm_match.get(
            "platform_object_id"
        )
        result.ticketmaster_status = tm_method
        result.supporting_observation_ids.append(tm_match.get("platform_object_id") or "")
    else:
        result.ticketmaster_status = tm_method
    result.supporting_observation_ids = [i for i in result.supporting_observation_ids if i]

    methods = [m for m in (sl_method, tm_method) if m not in {ResolutionMethod.UNRESOLVED.value}]
    if not methods:
        result.resolution_method = ResolutionMethod.UNRESOLVED.value
    elif sl_method == ResolutionMethod.EXACT_MBID.value:
        result.resolution_method = ResolutionMethod.EXACT_MBID.value
    elif tm_method == ResolutionMethod.EXACT_PLATFORM_ID.value:
        result.resolution_method = ResolutionMethod.EXACT_PLATFORM_ID.value
    elif ResolutionMethod.NORMALIZED_NAME_MATCH.value in methods:
        result.resolution_method = ResolutionMethod.NORMALIZED_NAME_MATCH.value
    else:
        result.resolution_method = methods[0]
    return result
