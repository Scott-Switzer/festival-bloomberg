"""Deterministic candidate extractors (PASS 1 and PASS 2).

Structured extraction ALWAYS precedes an LLM. These pure functions turn a
source document's raw text/HTML into CANDIDATE claims with exact evidence
spans. They NEVER persist anything and never decide admissibility — the
deterministic verifier does that. Every candidate carries:

    source_document_id / source_url / evidence_span_start / evidence_span_end /
    evidence_span_hash / extractor_kind / candidate cutoff fields

PASS 1 = JSON-LD / Schema.org Event / OpenGraph machine-readable fields.
PASS 2 = temporal date-language patterns resolved against a defensible
         publication anchor.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from ..acquisition.contracts import content_hash_of
from .cutoffs import (
    CUTOFF_ANNOUNCEMENT,
    CUTOFF_EVENT_DATE,
    CUTOFF_GENERAL_ONSALE,
    CUTOFF_PRESALE,
    CUTOFF_TICKET_PRICE_OBSERVATION,
    GRANULARITY_DAY,
    GRANULARITY_EXACT,
)

EXTRACTOR_JSONLD = "DETERMINISTIC_JSONLD"
EXTRACTOR_OPENTABLE = "DETERMINISTIC_OPENTABLE"
EXTRACTOR_DATE_LANG = "DETERMINISTIC_DATE_LANG"

_SCRIPT_LD = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_OG_META = re.compile(
    r"<meta[^>]+property=[\"']og:([a-z_:]+)[\"'][^>]+content=[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
_TIME_TAG = re.compile(r"<time[^>]*datetime=[\"']([^\"']+)[\"'][^>]*>(.*?)</time>", re.IGNORECASE | re.DOTALL)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# PASS 2 temporal language patterns. Each: (cutoff_type, regex, kind).
_DATE_LANG_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        CUTOFF_GENERAL_ONSALE,
        re.compile(r"tickets?\s+(?:go|goes|go|will go)\s+on\s+sale\s+(?:on\s+)?(.{0,40}?)(?:\.|$|\n)", re.IGNORECASE),
        "onsale_phrase",
    ),
    (
        CUTOFF_GENERAL_ONSALE,
        re.compile(r"(?:general|public)\s+(?:on-?sale|sale)\s+(?:begins?|starts?|opens?)\s+(?:on\s+)?(.{0,40}?)(?:\.|$|\n)", re.IGNORECASE),
        "onsale_phrase",
    ),
    (
        CUTOFF_PRESALE,
        re.compile(r"presale\s+(?:begins?|starts?|opens?)\s+(?:on\s+)?(.{0,40}?)(?:\.|$|\n)", re.IGNORECASE),
        "presale_phrase",
    ),
    (
        CUTOFF_GENERAL_ONSALE,
        re.compile(r"\bon\s+sale\s+(?:now|today)\b", re.IGNORECASE),
        "onsale_now",
    ),
    (
        CUTOFF_ANNOUNCEMENT,
        re.compile(r"\bannounced\s+(?:today|now)\b", re.IGNORECASE),
        "announced_now",
    ),
    (
        CUTOFF_TICKET_PRICE_OBSERVATION,
        re.compile(r"tickets?\s+(?:from|starting\s+at|start\s+at)\s+\$?([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE),
        "price_phrase",
    ),
]

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _span_hash(text: str, start: int, end: int) -> str:
    return hashlib.sha256(text[start:end].encode("utf-8")).hexdigest()[:32]


def _candidate(
    *,
    canonical_event_id: str,
    cutoff_type: str,
    source_document_id: str,
    source_url: str,
    evidence_text: str,
    span_start: int,
    span_end: int,
    extractor_kind: str,
    candidate_value: str | None = None,
    lower_bound: str | None = None,
    upper_bound: str | None = None,
    granularity: str = GRANULARITY_DAY,
    evidence_class: str = "OBSERVED_DAY",
    interpretation: str = "",
    source_publication_time: str | None = None,
) -> dict[str, Any]:
    return {
        "canonical_event_id": canonical_event_id,
        "cutoff_type": cutoff_type,
        "candidate_value": candidate_value,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "granularity": granularity,
        "evidence_class": evidence_class,
        "source_document_id": source_document_id,
        "source_url": source_url,
        "evidence_text": evidence_text,
        "evidence_span_start": span_start,
        "evidence_span_end": span_end,
        "evidence_span_hash": _span_hash(evidence_text, 0, len(evidence_text)),
        "extractor_kind": extractor_kind,
        "interpretation": interpretation,
        "source_publication_time": source_publication_time,
    }


def extract_jsonld_candidates(
    html: str,
    *,
    canonical_event_id: str,
    source_document_id: str,
    source_url: str,
) -> list[dict[str, Any]]:
    """PASS 1: Schema.org Event / MusicEvent from application/ld+json blocks.

    Only fields the Event schema actually exposes are extracted; anything the
    markup does not state is never invented. Evidence span = the whole script
    block; the JSON pointer path is recorded in ``interpretation``.
    """
    out: list[dict[str, Any]] = []
    for m in _SCRIPT_LD.finditer(html):
        raw = m.group(1)
        start, end = m.start(), m.end()
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for node in _event_nodes(payload):
            path = node.get("_path", "")
            if node.get("startDate"):
                out.append(_candidate(
                    canonical_event_id=canonical_event_id,
                    cutoff_type=CUTOFF_EVENT_DATE,
                    source_document_id=source_document_id,
                    source_url=source_url,
                    evidence_text=raw,
                    span_start=start, span_end=end,
                    extractor_kind=EXTRACTOR_JSONLD,
                    candidate_value=str(node["startDate"])[:10],
                    interpretation=f"schema.org Event.startDate @ {path}",
                ))
            offers = node.get("offers") or []
            if isinstance(offers, dict):
                offers = [offers]
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                price = offer.get("price") or offer.get("lowPrice")
                if price is not None:
                    out.append(_candidate(
                        canonical_event_id=canonical_event_id,
                        cutoff_type=CUTOFF_TICKET_PRICE_OBSERVATION,
                        source_document_id=source_document_id,
                        source_url=source_url,
                        evidence_text=raw,
                        span_start=start, span_end=end,
                        extractor_kind=EXTRACTOR_JSONLD,
                        candidate_value=str(price),
                        interpretation=f"schema.org Offer.price @ {path}",
                    ))
            for key in ("onsaleStart", "presaleStart", "ticketSaleStart"):
                if node.get(key):
                    ct = CUTOFF_GENERAL_ONSALE if key != "presaleStart" else CUTOFF_PRESALE
                    out.append(_candidate(
                        canonical_event_id=canonical_event_id,
                        cutoff_type=ct,
                        source_document_id=source_document_id,
                        source_url=source_url,
                        evidence_text=raw,
                        span_start=start, span_end=end,
                        extractor_kind=EXTRACTOR_JSONLD,
                        candidate_value=str(node[key])[:10],
                        interpretation=f"schema.org Event.{key} @ {path}",
                    ))
    return out


def _event_nodes(payload: Any, path: str = "$") -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if str(payload.get("@type", "")).lower() in ("event", "musicevent", "concerto", "musicevent"):
            nodes.append({**payload, "_path": path})
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for i, item in enumerate(graph):
                nodes.extend(_event_nodes(item, f"{path}.@graph[{i}]"))
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            nodes.extend(_event_nodes(item, f"{path}[{i}]"))
    return nodes


def deterministic_pass(
    html: str,
    *,
    canonical_event_id: str,
    source_document_id: str,
    source_url: str,
    publication_time: datetime | None = None,
) -> list[dict[str, Any]]:
    """Run PASS 1 (JSON-LD/OpenGraph) then PASS 2 (date language) in order.

    Deterministic extraction ALWAYS precedes an LLM: any candidate that a
    structured extractor can produce never goes to the model, and every
    candidate from this pass carries ``extractor_kind`` = DETERMINISTIC_*.
    """
    return (
        extract_jsonld_candidates(
            html,
            canonical_event_id=canonical_event_id,
            source_document_id=source_document_id,
            source_url=source_url,
        )
        + extract_opengraph_candidates(
            html,
            canonical_event_id=canonical_event_id,
            source_document_id=source_document_id,
            source_url=source_url,
        )
        + extract_date_language_candidates(
            html,
            canonical_event_id=canonical_event_id,
            source_document_id=source_document_id,
            source_url=source_url,
            publication_time=publication_time,
        )
    )


def extract_opengraph_candidates(
    html: str,
    *,
    canonical_event_id: str,
    source_document_id: str,
    source_url: str,
) -> list[dict[str, Any]]:
    """PASS 1: OpenGraph article publication metadata (publication evidence).

    ``og:article:published_time`` / ``og:updated_time`` are PUBLICATION
    evidence, never a decision cutoff; they are returned as candidate
    RESULT_PUBLICATION-style publication observations with a span.
    """
    out: list[dict[str, Any]] = []
    for m in _OG_META.finditer(html):
        prop = m.group(1)
        if prop not in ("article:published_time", "article:modified_time"):
            continue
        value = m.group(2)
        out.append(_candidate(
            canonical_event_id=canonical_event_id,
            cutoff_type="RESULT_PUBLICATION",
            source_document_id=source_document_id,
            source_url=source_url,
            evidence_text=value,
            span_start=m.start(), span_end=m.end(),
            extractor_kind=EXTRACTOR_OPENTABLE,
            candidate_value=value,
            granularity=GRANULARITY_EXACT,
            evidence_class="OBSERVED_EXACT",
            interpretation=f"og:{prop}",
        ))
    return out


def extract_date_language_candidates(
    text: str,
    *,
    canonical_event_id: str,
    source_document_id: str,
    source_url: str,
    publication_time: datetime | None = None,
) -> list[dict[str, Any]]:
    """PASS 2: temporal date-language patterns.

    Relative phrases ("on sale now", "announced today", "Friday") are resolved
    against the document's defensible publication timestamp. "On sale now"
    from a page published at T proves onsale happened NO LATER than T: it is
    stored as an UPPER BOUND (evidence_class ARCHIVE_CAPTURE_UPPER_BOUND),
    never an exact onsale. A relative weekday without a defensible anchor is
    NOT resolved (the verifier rejects it).
    """
    out: list[dict[str, Any]] = []
    for cutoff_type, pattern, kind in _DATE_LANG_PATTERNS:
        for m in pattern.finditer(text):
            span_start, span_end = m.start(), m.end()
            phrase = m.group(1).strip() if m.lastindex and m.group(1) else ""
            ev_text = text[span_start:span_end]
            if kind == "onsale_now":
                # "on sale now" at publication T => onsale <= T (a BOUND).
                out.append(_candidate(
                    canonical_event_id=canonical_event_id,
                    cutoff_type=cutoff_type,
                    source_document_id=source_document_id,
                    source_url=source_url,
                    evidence_text=ev_text,
                    span_start=span_start, span_end=span_end,
                    extractor_kind=EXTRACTOR_DATE_LANG,
                    upper_bound=publication_time.isoformat() if publication_time else None,
                    granularity=GRANULARITY_EXACT,
                    evidence_class="ARCHIVE_CAPTURE_UPPER_BOUND",
                    interpretation="'on sale now' is an upper bound, not an exact onsale",
                    source_publication_time=publication_time.isoformat() if publication_time else None,
                ))
            elif kind == "announced_now":
                out.append(_candidate(
                    canonical_event_id=canonical_event_id,
                    cutoff_type=cutoff_type,
                    source_document_id=source_document_id,
                    source_url=source_url,
                    evidence_text=ev_text,
                    span_start=span_start, span_end=span_end,
                    extractor_kind=EXTRACTOR_DATE_LANG,
                    upper_bound=publication_time.isoformat() if publication_time else None,
                    granularity=GRANULARITY_EXACT,
                    evidence_class="ARCHIVE_CAPTURE_UPPER_BOUND",
                    interpretation="'announced today' is an upper bound, not an exact announcement",
                    source_publication_time=publication_time.isoformat() if publication_time else None,
                ))
            elif kind == "price_phrase":
                out.append(_candidate(
                    canonical_event_id=canonical_event_id,
                    cutoff_type=cutoff_type,
                    source_document_id=source_document_id,
                    source_url=source_url,
                    evidence_text=ev_text,
                    span_start=span_start, span_end=span_end,
                    extractor_kind=EXTRACTOR_DATE_LANG,
                    candidate_value=phrase.replace(",", ""),
                    interpretation="face price phrase",
                    source_publication_time=publication_time.isoformat() if publication_time else None,
                ))
            else:
                resolved = _resolve_date_phrase(phrase, publication_time)
                out.append(_candidate(
                    canonical_event_id=canonical_event_id,
                    cutoff_type=cutoff_type,
                    source_document_id=source_document_id,
                    source_url=source_url,
                    evidence_text=ev_text,
                    span_start=span_start, span_end=span_end,
                    extractor_kind=EXTRACTOR_DATE_LANG,
                    candidate_value=resolved,
                    granularity=GRANULARITY_DAY,
                    evidence_class="OBSERVED_DAY" if resolved else "UNKNOWN",
                    interpretation=f"{kind}: {phrase!r}" + ("" if resolved else " (unresolved relative date)"),
                    source_publication_time=publication_time.isoformat() if publication_time else None,
                ))
    return out


def _resolve_date_phrase(phrase: str, publication_time: datetime | None) -> str | None:
    """Resolve an absolute month/day phrase; relative weekdays only resolve
    against a defensible anchor, else return None (the verifier rejects).
    A trailing time clause ("March 3 at 10 a.m.") is ignored — the date
    portion alone is what a DAY-granularity claim can support."""
    if not phrase:
        return None
    low = phrase.lower().strip().rstrip(",")
    # Absolute "March 3" / "March 3, 2024" / "2024-03-03", possibly followed
    # by a time clause.
    m = re.search(
        r"\b(?P<month>[a-z]+)\s+(?P<day>[0-9]{1,2})(?:,\s*(?P<year>[0-9]{4}))?\b",
        low,
    )
    if m and m.group("month") in _MONTHS:
        year = int(m.group("year")) if m.group("year") else (publication_time.year if publication_time else None)
        if year is None:
            return None
        return f"{year:04d}-{_MONTHS[m.group('month')]:02d}-{int(m.group('day')):02d}"
    m = re.match(r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})$", low)
    if m:
        return low
    # Relative weekday requires a defensible anchor.
    if low in _WEEKDAYS and publication_time is not None:
        target = _WEEKDAYS.index(low)
        delta = (target - publication_time.weekday()) % 7
        if delta == 0:
            delta = 7  # "next Friday" — future weekday from publication
        d = publication_time.date() + timedelta(days=delta)
        return d.isoformat()
    return None
