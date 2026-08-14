"""EVENT_GRAPH pipeline — MusicBrainz identity backbone.

MusicBrainz's core database and specified dumps are CC0, so it can become a
primary identity layer rather than a transient API dependency. This module is
the first, API-based slice of that backbone: artist-name resolution to MBIDs
with graded resolution methods. A local dump ingestion pass (also CC0) can
slot in behind the same ``flywheel.event_graph_identities`` table later.

Identity is a CLAIM, not a fact: the same artist name may resolve to several
MBIDs (homonyms), and the same entity keeps separate provider rows. Nothing is
ever merged silently here.
"""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.transport import HttpResponse, TransportError, UrllibTransport

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"

RESOLUTION_EXACT_MBID = "EXACT_MBID"
RESOLUTION_NORMALIZED_NAME = "NORMALIZED_NAME_MATCH"
RESOLUTION_FUZZY = "FUZZY_MATCH"
RESOLUTION_UNRESOLVED = "UNRESOLVED"
RESOLUTION_MANUAL = "MANUAL"

#: MusicBrainz data is CC0; the project's registry of resolved identities
#: records that license explicitly per row.
MB_LICENSE = "CC0 (MusicBrainz data)"

_PUNCTUATION = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """NFKC-normalized, lowercased, punctuation-collapsed matching form."""
    decomposed = unicodedata.normalize("NFKC", name or "").lower()
    return _PUNCTUATION.sub(" ", decomposed).strip()


def name_similarity(left: str, right: str) -> float:
    """Token-aware similarity in [0, 1]; exact normalized names score 1.0."""
    a, b = normalize_name(left), normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if a_tokens and b_tokens:
        jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
        if jaccard >= 0.5:
            return jaccard
    return SequenceMatcher(None, a, b).ratio()


def select_best_match(name: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Score MusicBrainz search results and pick the best candidate.

    Returns a dict with ``musicbrainz_id``, ``musicbrainz_name``,
    ``musicbrainz_type``, ``musicbrainz_country``, ``resolution_method``,
    ``match_confidence`` and ``score``. Never fabricates an id: when nothing
    clears the fuzzy bar the method is UNRESOLVED with a null id.
    """
    if not results:
        return {
            "musicbrainz_id": None,
            "musicbrainz_name": None,
            "musicbrainz_type": None,
            "musicbrainz_country": None,
            "resolution_method": RESOLUTION_UNRESOLVED,
            "match_confidence": None,
            "score": 0.0,
        }
    scored = []
    for result in results:
        mbid = result.get("id")
        mb_name = result.get("name") or result.get("sort-name") or ""
        score = name_similarity(name, mb_name)
        scored.append((score, result, mbid, mb_name))
    scored.sort(key=lambda item: item[0], reverse=True)
    score, best, mbid, mb_name = scored[0]

    if score >= 0.99:
        method, confidence = RESOLUTION_EXACT_MBID, 1.0
    elif score >= 0.85:
        method, confidence = RESOLUTION_NORMALIZED_NAME, score
    elif score >= 0.6:
        method, confidence = RESOLUTION_FUZZY, score
    else:
        return {
            "musicbrainz_id": None,
            "musicbrainz_name": mb_name,
            "musicbrainz_type": best.get("type"),
            "musicbrainz_country": best.get("country"),
            "resolution_method": RESOLUTION_UNRESOLVED,
            "match_confidence": score,
            "score": score,
        }

    return {
        "musicbrainz_id": mbid,
        "musicbrainz_name": mb_name,
        "musicbrainz_type": best.get("type"),
        "musicbrainz_country": best.get("country"),
        "resolution_method": method,
        "match_confidence": confidence,
        "score": score,
    }


def build_identity_row(
    *,
    entity_name: str,
    entity_type: str = "ARTIST",
    entity_key: str | None = None,
    selection: dict[str, Any],
    source_provider: str = "musicbrainz",
    source_url: str | None = None,
    raw_payload_hash: str | None = None,
    retrieved_at: datetime | None = None,
    knowledge_time: datetime | None = None,
    parser_version: str = "musicbrainz_search_v1",
    software_version: str = "data_flywheel_and_coverage_v1",
    rights_status: str = "OPEN_COMMERCIAL_OK",
    commercial_use_status: str = "OPEN_COMMERCIAL_OK",
) -> dict[str, Any]:
    """Build a ``flywheel.event_graph_identities`` row (pure)."""
    now = retrieved_at or utc_now()
    knowledge = knowledge_time or now
    normalized = normalize_name(entity_name)
    mbid = selection.get("musicbrainz_id")
    method = selection.get("resolution_method") or RESOLUTION_UNRESOLVED
    identity_id = f"identity_{content_hash_of({
        'type': entity_type,
        'name': normalized,
        'mbid': mbid,
        'method': method,
    })[:20]}"
    return {
        "identity_id": identity_id,
        "entity_type": entity_type,
        "entity_key": entity_key or f"name::{normalized}",
        "entity_name": entity_name,
        "normalized_name": normalized,
        "musicbrainz_id": mbid,
        "musicbrainz_name": selection.get("musicbrainz_name"),
        "musicbrainz_type": selection.get("musicbrainz_type"),
        "musicbrainz_country": selection.get("musicbrainz_country"),
        "wikidata_id": None,
        "ticketmaster_id": None,
        "resolution_method": method,
        "match_confidence": selection.get("match_confidence"),
        "source_provider": source_provider,
        "source_url": source_url,
        "retrieved_at": now.isoformat(),
        "knowledge_time": knowledge.isoformat(),
        "license": MB_LICENSE,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "raw_payload_hash": raw_payload_hash,
        "parser_version": parser_version,
        "software_version": software_version,
    }


class MusicBrainzClient:
    """Keyless MusicBrainz search client.

    MusicBrainz requires an identifying User-Agent and ~1 request/second.
    Responses are CC0 data. Handles the documented 503 rate-limit response.
    """

    name = "musicbrainz"

    def __init__(
        self,
        transport: UrllibTransport | None = None,
        *,
        user_agent: str | None = None,
        rate_limit_seconds: float = 1.0,
    ) -> None:
        self.transport = transport or UrllibTransport(user_agent=user_agent)
        self.rate_limit_seconds = rate_limit_seconds
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def search_artist(self, name: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search artists by name; returns the raw ``artists`` list."""
        from urllib.parse import quote, urlencode

        self._throttle()
        query = urlencode(
            {
                "query": f"artist:{quote(name)}",
                "fmt": "json",
                "limit": str(limit),
            }
        )
        url = f"{MUSICBRAINZ_API}/artist?{query}"
        try:
            response = self.transport.request("GET", url, timeout_seconds=30.0)
        except TransportError as exc:
            raise MusicBrainzError(f"network failure: {exc}") from None
        if response.status == 503:
            raise MusicBrainzRateLimited("MusicBrainz rate limit (503); retry later")
        if response.status == 429:
            raise MusicBrainzRateLimited("MusicBrainz rate limit (429); retry later")
        if response.status != 200:
            raise MusicBrainzError(f"MusicBrainz http {response.status}")
        payload = _safe_json(response)
        return list((payload or {}).get("artists") or [])

    def resolve_artist(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Search + select + build an identity row for one artist name."""
        raw = self.search_artist(name)
        selection = select_best_match(name, raw)
        return build_identity_row(
            entity_name=name,
            entity_type="ARTIST",
            selection=selection,
            source_url=f"{MUSICBRAINZ_API}/artist",
            raw_payload_hash=content_hash_of(raw) if raw else None,
            **kwargs,
        )


class MusicBrainzError(RuntimeError):
    pass


class MusicBrainzRateLimited(MusicBrainzError):
    pass


def _safe_json(response: HttpResponse) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise MusicBrainzError("response not JSON") from exc
    return payload if isinstance(payload, dict) else {}
