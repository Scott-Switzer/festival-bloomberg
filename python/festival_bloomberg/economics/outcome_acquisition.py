"""Economic outcome acquisition — orchestration for real public evidence.

Turns curated public sources (Wikipedia festival/tour articles first, then
official/archived pages) into source-backed outcome claims via the document
ingestion layer. This module is the *"where did the number come from"* answer:
every claim keeps its source, quality tier, rights, and times.

The curated registry is deliberately small and provenance-backed: quality over
count. Wikipedia is an aggregator (C tier, CC BY-SA -> OPEN_WITH_ATTRIBUTION);
a future pass can layer A-tier government/promoter/venue documents on top
without changing the claim semantics.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.transport import HttpTransport, TransportError, UrllibTransport
from .document_ingestion import (
    DocumentEvidence,
    extract_infobox_fields,
    extract_outcome_candidates,
    strip_wikitext,
)
from .outcome_claims import (
    EVENT_USABLE_CAPACITY,
    EXPLICIT_SOLD_OUT_ASSERTION,
    GRADE_C_OTHER_PUBLIC,
    OBSERVED_PUBLIC,
    RIGHTS_OPEN_WITH_ATTRIBUTION,
    REPORTED_ATTENDANCE,
    OutcomeClaim,
)

MEDIAWIKI_API = "https://en.wikipedia.org/w/api.php"


@dataclass
class PublicOutcomeSource:
    event_id: str
    event_label: str
    event_time: str | None
    venue_name: str
    market: str
    wikipedia_title: str | None = None
    url: str | None = None
    source_provider: str = "wikipedia_mediawiki_api"
    source_name: str = "en.wikipedia.org"
    source_quality: str = GRADE_C_OTHER_PUBLIC
    rights_status: str = RIGHTS_OPEN_WITH_ATTRIBUTION
    commercial_use_status: str = RIGHTS_OPEN_WITH_ATTRIBUTION
    publisher: str = "Wikipedia (Wikimedia Foundation)"


# Curated Chicago music-festival sources (provenance-backed). Each entry maps
# to one canonical festival edition event id. Attendance/capacity in the
# infobox are festival-level aggregates; the claim notes say so explicitly.
CURATED_SOURCES: list[PublicOutcomeSource] = [
    PublicOutcomeSource(
        event_id="festival_lollapalooza_chicago",
        event_label="Lollapalooza (Chicago)",
        event_time=None,
        venue_name="Grant Park",
        market="Chicago, IL",
        wikipedia_title="Lollapalooza",
    ),
    PublicOutcomeSource(
        event_id="festival_riot_fest_chicago",
        event_label="Riot Fest (Chicago)",
        event_time=None,
        venue_name="Douglass Park",
        market="Chicago, IL",
        wikipedia_title="Riot Fest",
    ),
    PublicOutcomeSource(
        event_id="festival_pitchfork_chicago",
        event_label="Pitchfork Music Festival (Chicago)",
        event_time=None,
        venue_name="Union Park",
        market="Chicago, IL",
        wikipedia_title="Pitchfork Music Festival",
    ),
    PublicOutcomeSource(
        event_id="festival_north_coast_chicago",
        event_label="North Coast Music Festival",
        event_time=None,
        venue_name="SeatGeek Stadium",
        market="Bridgeview/Chicago, IL",
        wikipedia_title="North Coast Music Festival",
    ),
]


def fetch_wikipedia_wikitext(title: str, transport: HttpTransport) -> str:
    """Fetch raw wikitext for a Wikipedia article via the canonical transport."""
    params = urllib.parse.urlencode({
        "action": "query",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
        "titles": title,
        "redirects": "1",
    })
    url = f"{MEDIAWIKI_API}?{params}"
    response = transport.request(
        "GET",
        url,
        headers={"User-Agent": "FestivalBloomberg/0.1 (research; outcome-acquisition)"},
        timeout_seconds=30.0,
    )
    if response.status != 200:
        raise TransportError(f"wikipedia http {response.status}")
    payload = response.json()
    pages = (payload.get("query") or {}).get("pages") or []
    if not pages:
        return ""
    slots = pages[0].get("revisions", [{}])[0].get("slots", {})
    return slots.get("main", {}).get("content", "") or ""


class EconomicOutcomeAcquirer:
    """Fetch curated sources, extract claims, insert them (append-only)."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self.transport = transport or UrllibTransport()

    # -- public ------------------------------------------------------------- #
    def acquire_source(self, src: PublicOutcomeSource) -> list[OutcomeClaim]:
        """Fetch one source and return outcome claims (not yet persisted)."""
        if src.wikipedia_title:
            wikitext = fetch_wikipedia_wikitext(src.wikipedia_title, self.transport)
            text = strip_wikitext(wikitext)
            infobox = extract_infobox_fields(wikitext)
        elif src.url:
            raise NotImplementedError("direct-URL sources use the Common Crawl / HTTP path")
        else:
            return []

        evidence = DocumentEvidence.build(
            text=text,
            source_name=src.source_name,
            provider=src.source_provider,
            publisher=src.publisher,
            source_url=f"https://en.wikipedia.org/wiki/{urllib.parse.quote(src.wikipedia_title)}",
            document_title=src.wikipedia_title,
            document_id=src.event_id,
            rights_status=src.rights_status,
            commercial_use_status=src.commercial_use_status,
        )

        claims: list[OutcomeClaim] = []
        claims.extend(self._infobox_claims(src, infobox, evidence))
        claims.extend(self._candidate_claims(src, text, evidence))
        return claims

    def run(self, econ_repo, sources: list[PublicOutcomeSource] | None = None) -> dict[str, Any]:
        sources = sources if sources is not None else CURATED_SOURCES
        inserted = 0
        errors = 0
        fetched = 0
        by_event: dict[str, list[OutcomeClaim]] = defaultdict(list)
        for src in sources:
            try:
                claims = self.acquire_source(src)
                fetched += 1
            except (TransportError, ValueError, KeyError) as exc:
                errors += 1
                continue
            for claim in claims:
                if econ_repo.insert_outcome_claim(claim):
                    inserted += 1
                    by_event[src.event_id].append(claim)

        conflicts = assign_conflict_groups(econ_repo)
        return {
            "sources_attempted": len(sources),
            "sources_fetched": fetched,
            "sources_failed": errors,
            "claims_inserted": inserted,
            "events_with_claims": len(by_event),
            "conflict_groups": conflicts,
            "provider_cost_usd": 0.0,
        }

    # -- claim builders ----------------------------------------------------- #
    def _infobox_claims(
        self, src: PublicOutcomeSource, infobox: dict[str, str], evidence: DocumentEvidence
    ) -> list[OutcomeClaim]:
        claims: list[OutcomeClaim] = []
        attendance_raw = infobox.get("attendance")
        capacity_raw = infobox.get("capacity")
        if attendance_raw:
            value = _parse_infobox_number(attendance_raw)
            if value is not None:
                claims.append(self._build(
                    src, evidence, REPORTED_ATTENDANCE, value,
                    unit="persons",
                    notes="festival aggregate attendance (multi-day), reported/not verified paid",
                ))
        if capacity_raw:
            value = _parse_infobox_number(capacity_raw)
            if value is not None:
                claims.append(self._build(
                    src, evidence, EVENT_USABLE_CAPACITY, value,
                    unit="persons",
                    notes="festival daily usable capacity (infobox capacity field)",
                ))
        return claims

    # Body-text numeric attendance/tickets/gross in a multi-edition festival
    # article is edition/city-ambiguous (e.g. Lollapalooza 1996 vs Stockholm
    # 2022), so it is NOT auto-attributed to the Chicago festival. Only
    # sold-out assertions and explicit sales figures survive; infobox fields
    # carry the canonical attendance/capacity.
    _BODY_SAFE_TYPES = {
        EXPLICIT_SOLD_OUT_ASSERTION,
        "TICKETS_SOLD",
        "TICKET_GROSS",
    }

    def _candidate_claims(
        self, src: PublicOutcomeSource, text: str, evidence: DocumentEvidence
    ) -> list[OutcomeClaim]:
        claims: list[OutcomeClaim] = []
        sold_out_emitted = False
        for candidate in extract_outcome_candidates(text):
            if candidate.outcome_type not in self._BODY_SAFE_TYPES:
                continue
            if candidate.outcome_type == EXPLICIT_SOLD_OUT_ASSERTION:
                if sold_out_emitted:
                    continue
                sold_out_emitted = True
                notes = "explicit sold-out assertion (may span multiple editions)"
            else:
                notes = candidate.notes + " (body text; edition attribution not verified)"
            value = candidate.value_numeric
            claims.append(self._build(
                src, evidence, candidate.outcome_type, value,
                unit=candidate.unit,
                notes=notes,
                matched_text=candidate.matched_text,
            ))
        return claims

    def _build(
        self,
        src: PublicOutcomeSource,
        evidence: DocumentEvidence,
        outcome_type: str,
        value: float | None,
        *,
        unit: str | None = None,
        notes: str = "",
        matched_text: str | None = None,
    ) -> OutcomeClaim:
        claim_id = f"claim_{content_hash_of({
            'event': src.event_id,
            'type': outcome_type,
            'value': value,
            'source': src.source_name,
        })[:20]}"
        return OutcomeClaim.build(
            claim_id=claim_id,
            canonical_event_id=src.event_id,
            outcome_type=outcome_type,
            value_numeric=value,
            value_text=matched_text if value is None else None,
            unit=unit,
            source_provider=src.source_provider,
            source_name=src.source_name,
            source_url=evidence.source_url,
            source_document_id=evidence.document_id,
            event_time=src.event_time,
            source_publication_time=evidence.publication_time,
            retrieved_at=evidence.retrieved_at,
            knowledge_time=evidence.retrieved_at,
            evidence_observation_id=evidence.content_hash,
            raw_payload_hash=evidence.content_hash,
            source_quality=src.source_quality,
            rights_status=src.rights_status,
            commercial_use_status=src.commercial_use_status,
            observation_class=OBSERVED_PUBLIC,
            notes=notes,
        )


def assign_conflict_groups(econ_repo) -> int:
    """Assign a shared conflict_group_id to same-(event,type) claims whose
    values differ. Never mutates values; only labels the group."""
    claims = econ_repo.query_outcome_claims()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in claims:
        groups[(c["canonical_event_id"], c["outcome_type"])].append(c)

    assigned = 0
    for (event_id, outcome_type), members in groups.items():
        values = {str(m["value_numeric"]) for m in members if m["value_numeric"] is not None}
        if len(values) <= 1:
            continue
        gid = f"conflict_{content_hash_of({'event': event_id, 'type': outcome_type})[:16]}"
        for m in members:
            if not m.get("conflict_group_id"):
                econ_repo.conn.execute(
                    "UPDATE economics.event_outcome_claims SET conflict_group_id = ? WHERE claim_id = ?",
                    [gid, m["claim_id"]],
                )
        econ_repo.conn.commit()
        assigned += 1
    return assigned


def _parse_infobox_number(raw: str) -> float | None:
    """Extract a number from an infobox value like '400,000<ref>...</ref>'."""
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", " ", raw, flags=re.DOTALL)
    match = re.search(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None
