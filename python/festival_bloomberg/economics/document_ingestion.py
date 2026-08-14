"""Historical outcome document ingestion.

Turns raw documents (HTML / wikitext / TXT / PDF-extracted text) into
*immutable evidence* plus *candidate outcome claims*, with strict semantic
classification. The point is provenance + semantic honesty: a number is
never converted from one outcome type to another to inflate coverage.

Design rules:

* RAW DOCUMENT -> evidence record (hash, source, times, rights) -> candidates
* capacity != attendance; permit != attendance; planned/expected != actual;
  "estimated crowd" is REPORTED_ATTENDANCE (weak), not PAID_ATTENDANCE.
* ambiguous phrases are flagged ``review_required`` and never silently
  classified as a strong outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

# ---------------------------------------------------------------------------
# Document evidence
# ---------------------------------------------------------------------------
@dataclass
class DocumentEvidence:
    source_url: str | None
    source_name: str
    provider: str
    publisher: str | None
    document_title: str | None
    publication_time: str | None
    retrieved_at: str
    text: str
    content_hash: str
    rights_status: str = "UNKNOWN"
    commercial_use_status: str = "UNKNOWN"
    document_id: str | None = None

    @classmethod
    def build(
        cls,
        *,
        text: str,
        source_name: str,
        provider: str,
        source_url: str | None = None,
        publisher: str | None = None,
        document_title: str | None = None,
        publication_time: str | None = None,
        retrieved_at: str | None = None,
        rights_status: str = "UNKNOWN",
        commercial_use_status: str = "UNKNOWN",
        document_id: str | None = None,
    ) -> "DocumentEvidence":
        retrieved = retrieved_at or utc_now().isoformat()
        return cls(
            source_url=source_url,
            source_name=source_name,
            provider=provider,
            publisher=publisher,
            document_title=document_title,
            publication_time=publication_time,
            retrieved_at=retrieved,
            text=text,
            content_hash=content_hash_of(text),
            rights_status=rights_status,
            commercial_use_status=commercial_use_status,
            document_id=document_id,
        )


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\u00a0]+")
_REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_FILE_LINK = re.compile(r"\[\[(?:File|Image):[^\]|]*(\|[^\]]*)?\]\]", re.IGNORECASE)


def strip_html(html: str) -> str:
    """Best-effort HTML -> readable text (no external deps)."""
    if not html:
        return ""
    text = _COMMENT.sub(" ", html)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = _TAG.sub(" ", text)
    return _collapse(text)


def strip_wikitext(wikitext: str) -> str:
    """Wikitext -> readable prose, best-effort (no external deps)."""
    if not wikitext:
        return ""
    text = _COMMENT.sub(" ", wikitext)
    text = _REF.sub(" ", text)
    text = _FILE_LINK.sub(" ", text)
    # nested template removal (bounded passes)
    for _ in range(4):
        new = _TEMPLATE.sub(" ", text)
        if new == text:
            break
        text = new
    # internal links -> display text
    text = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1) or "", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    # external links -> url text
    text = re.sub(r"\[https?://[^\]\s]*\s([^\]]*)\]", r"\1", text)
    text = re.sub(r"https?://[^\s\]|]+", " ", text)
    # emphasis / headings
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"^\s*=+\s*", "", text, flags=re.MULTILINE)
    text = text.replace("|", " ")
    return _collapse(text)


def _collapse(text: str) -> str:
    text = _WS.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Infobox field extraction (Wikipedia festival / venue infoboxes)
# ---------------------------------------------------------------------------
INFOBOX_KEY = re.compile(r"^\s*\|\s*([a-z_ ]+?)\s*=\s*(.*)$", re.IGNORECASE | re.MULTILINE)


def extract_infobox_fields(wikitext: str) -> dict[str, str]:
    """Extract normalized ``| key = value`` infobox fields (values are raw
    wikitext; callers decide how to interpret each key)."""
    fields: dict[str, str] = {}
    for match in INFOBOX_KEY.finditer(wikitext):
        key = _collapse(match.group(1)).lower().replace(" ", "_")
        value = match.group(2).strip()
        if key and key not in fields:
            fields[key] = value
    return fields


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------
NUMBER = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
MILLION = r"(\d{1,3}(?:\.\d+)?)\s*(million|m)"


@dataclass
class OutcomeCandidate:
    outcome_type: str
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    matched_text: str
    confidence: str
    review_required: bool
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _is_negated_attendance(prefix: str) -> str | None:
    """Return a semantic override when the pre-number phrase changes meaning.

    ``estimated`` is deliberately NOT treated as "expected": an estimate is a
    (weak) report, while expected/planned/projected is a forward-looking plan.
    """
    p = prefix.lower()
    if re.search(r"\b(expected|planned|projected|anticipated)\b", p):
        return "EXPECTED"
    if re.search(r"\b(capacity of|capacity|seating capacity|max(imum)? (of|capacity)|up to|as many as)\b", p):
        return "CAPACITY"
    if re.search(r"\b(permit|permitted|maximum)\b", p):
        return "PERMIT"
    return None


def extract_outcome_candidates(text: str) -> list[OutcomeCandidate]:
    """Strict, semantics-preserving extraction of numeric outcome candidates.

    Returns candidates only where the phrase is reasonably clear. Anything
    ambiguous is still returned but flagged ``review_required``.
    """
    candidates: list[OutcomeCandidate] = []

    # --- attendance: "N (paid|scanned|reported)? attendees/people/scanned" -- #
    att_pat = re.compile(
        rf"(?P<prefix>.{{0,90}}?)(?P<num>{NUMBER})\s*(?P<qual>paid|scanned|reported)?\s*(?P<unit>attendees|attendance|people|persons|fans|concertgoers|festivalgoers|attended)\b",
        re.IGNORECASE,
    )
    for m in att_pat.finditer(text):
        prefix = m.group("prefix")
        num = _to_float(m.group("num"))
        phrase = m.group(0)
        qual = (m.group("qual") or "").lower()
        override = _is_negated_attendance(prefix)
        if override == "EXPECTED":
            candidates.append(_candidate(
                "EXPECTED_ATTENDANCE", num, phrase, "persons",
                "expected/planned attendance is not actual attendance", review=True,
            ))
        elif override in ("CAPACITY", "PERMIT"):
            candidates.append(_candidate(
                "PERMIT_CAPACITY_LIMIT" if override == "PERMIT" else "VENUE_CAPACITY",
                num, phrase, "persons",
                "capacity/permit phrase is not attendance", review=True,
            ))
        elif qual == "paid":
            candidates.append(_candidate(
                "PAID_ATTENDANCE", num, phrase, "persons",
                "explicit paid attendance",
            ))
        elif qual == "scanned":
            candidates.append(_candidate(
                "SCANNED_ATTENDANCE", num, phrase, "persons",
                "explicit scanned attendance",
            ))
        elif re.search(r"\bestimated\b|\bcrowd of\b|\babout\b|\bover\b|\bmore than\b|\bnearly\b|\balmost\b", prefix, re.IGNORECASE):
            candidates.append(_candidate(
                "REPORTED_ATTENDANCE", num, phrase, "persons",
                "reported/estimated attendance, not verified paid attendance",
            ))
        else:
            candidates.append(_candidate(
                "REPORTED_ATTENDANCE", num, phrase, "persons",
                "attendance phrase; paid-vs-scanned not established",
            ))

    # --- bare "N scanned" / "N paid" --------------------------------------- #
    scanned_pat = re.compile(rf"(?P<num>{NUMBER})\s*(scanned|checked ?in)\b", re.IGNORECASE)
    for m in scanned_pat.finditer(text):
        num = _to_float(m.group("num"))
        candidates.append(_candidate(
            "SCANNED_ATTENDANCE", num, m.group(0), "persons", "explicit scanned/checked-in attendance",
        ))

    # --- attendance: "attendance of N" / "crowd of N" ---------------------- #
    att_of_pat = re.compile(
        rf"(?P<qual>expected|planned|projected|reported|actual|estimated|record)?\s*(attendance|crowd)\s+of\s+(?:about|over|more than|an estimated|approximately)?\s*(?P<num>{NUMBER})",
        re.IGNORECASE,
    )
    for m in att_of_pat.finditer(text):
        num = _to_float(m.group("num"))
        qual = (m.group("qual") or "").lower()
        if qual in ("expected", "planned", "projected"):
            candidates.append(_candidate(
                "EXPECTED_ATTENDANCE", num, m.group(0), "persons",
                "expected/planned attendance is not actual attendance", review=True,
            ))
        else:
            candidates.append(_candidate(
                "REPORTED_ATTENDANCE", num, m.group(0), "persons",
                "reported/estimated attendance, not verified paid attendance",
            ))

    # --- tickets sold ------------------------------------------------------ #
    tickets_pat = re.compile(
        rf"(?P<num>{NUMBER})\s*(tickets sold|tickets were sold|tickets have been sold)\b",
        re.IGNORECASE,
    )
    for m in tickets_pat.finditer(text):
        num = _to_float(m.group("num"))
        candidates.append(_candidate(
            "TICKETS_SOLD", num, m.group(0), "tickets", "tickets-sold phrase",
        ))

    # --- explicit sold-out assertions -------------------------------------- #
    soldout_pat = re.compile(r".{0,120}?\b(sold out|sell-?out)\b", re.IGNORECASE)
    for m in soldout_pat.finditer(text):
        phrase = m.group(0)
        if re.search(r"tickets|passes|show|event|festival|gig|concert|tour", phrase, re.IGNORECASE):
            candidates.append(_candidate(
                "EXPLICIT_SOLD_OUT_ASSERTION", None, phrase, None, "explicit sold-out phrase",
            ))

    # --- gross: "grossed $N" and "$N million gross/box office" -------------- #
    gross_before_pat = re.compile(
        rf"(?:grossed|gross of|grossing)\s*\$?(?P<num>{NUMBER}|{MILLION})\s*(?P<mult>million)?",
        re.IGNORECASE,
    )
    for m in gross_before_pat.finditer(text):
        value = _parse_money(m.group("num"), m.group(0))
        candidates.append(_candidate(
            "TICKET_GROSS", value, m.group(0), "USD",
            "gross phrase (currency/net may need confirmation)",
        ))
    gross_after_pat = re.compile(
        rf"\$?(?P<num>{NUMBER}|{MILLION})\s*(?:million)?\s*(?:in )?(?:gross|box ?office|ticket sales|revenue)\b",
        re.IGNORECASE,
    )
    for m in gross_after_pat.finditer(text):
        value = _parse_money(m.group("num"), m.group(0))
        candidates.append(_candidate(
            "TICKET_GROSS", value, m.group(0), "USD",
            "gross/revenue phrase (currency may need confirmation)",
        ))

    # --- primary price range ----------------------------------------------- #
    price_pat = re.compile(rf"\$(?P<lo>{NUMBER})\s*(?:-|–|—|to)\s*\$(?P<hi>{NUMBER})")
    for m in price_pat.finditer(text):
        candidates.append(_candidate(
            "PRIMARY_FACE_VALUE_MIN_MAX", None, m.group(0), "USD",
            "published price range (min/max kept separate by caller)",
            extra={"min": _to_float(m.group("lo")), "max": _to_float(m.group("hi"))},
        ))

    # Dedupe overlapping matches: one claim per (type, value) per document.
    seen: set[tuple[str, str]] = set()
    deduped: list[OutcomeCandidate] = []
    for c in candidates:
        key = (c.outcome_type, str(c.value_numeric) if c.value_numeric is not None else c.matched_text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _parse_money(raw: str | None, phrase: str) -> float | None:
    if not raw:
        return None
    num = _to_float(raw.replace("m", "").replace("M", ""))
    if num is None:
        return None
    if re.search(r"million|\bm\b", phrase, re.IGNORECASE):
        num *= 1_000_000
    return num


def _candidate(
    outcome_type: str,
    value: float | None,
    matched: str,
    unit: str | None,
    notes: str,
    *,
    review: bool = False,
    extra: dict[str, Any] | None = None,
) -> OutcomeCandidate:
    return OutcomeCandidate(
        outcome_type=outcome_type,
        value_numeric=value,
        value_text=None if value is not None else matched.strip(),
        unit=unit,
        matched_text=matched.strip(),
        confidence="LOW" if review else "MEDIUM",
        review_required=review,
        notes=notes,
        extra=extra or {},
    )
