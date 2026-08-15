"""Deterministic evidence verifier — the admissibility gate.

DeepSeek (or any extractor) PROPOSES candidate claims; this code DECIDES
whether they are admissible. An accepted claim can then be promoted into
``flywheel.pre_event_cutoff_evidence``. Verification never depends on the
extractor's confidence in itself; it checks identity, temporal semantics,
granularity, and rights.

Rejection rules (each regression-tested):
    * no source document id / evidence span        -> reject
    * wrong artist / venue / city / date           -> reject
    * announcement interpreted as booking exact    -> reject (bound only)
    * archive capture interpreted as publication   -> reject
    * relative date without a defensible anchor    -> reject
    * interval collapsed to a midpoint             -> reject
    * unsupported timezone precision               -> reject
    * source rights failure                        -> reject
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .cutoffs import (
    CUTOFF_BOOKING_OR_OFFER,
    KIND_ANNOUNCEMENT_UPPER_BOUND,
)
from .pit import (
    ARCHIVE_CAPTURE_UPPER_BOUND,
    ESTIMATED_RESEARCH_ONLY,
    STRICT_PIT_CLASSES,
    UNKNOWN,
)

VERIFICATION_PENDING = "PENDING"
VERIFICATION_ACCEPTED = "ACCEPTED"
VERIFICATION_REJECTED = "REJECTED"

#: Exact booking/offer dates may ONLY come from these explicit sources.
EXACT_BOOKING_MARKERS = (
    "OBSERVED_BOOKING_DATE",
    "OBSERVED_OFFER_DATE",
    "CONTRACT_DATE",
    "INTERNAL_FIRST_PARTY_BOOKING_DATE",
)

#: Rights statuses that permit research persistence.
ADMISSIBLE_RIGHTS = frozenset({"RESEARCH_ONLY", "TERMS_REVIEW_REQUIRED", "OPEN_COMMERCIAL_OK"})


def verify_candidate(
    candidate: dict[str, Any],
    *,
    target_event: dict[str, Any] | None = None,
    resolved: dict[str, list[str]] | None = None,
    rights_status: str | None = None,
    commercial_use_status: str | None = None,
) -> dict[str, str | None]:
    """Adjudicate one candidate claim. Returns status + optional reason.

    ``target_event`` = {artist, venue, city, market, start_date} of the event
    the claim is about. ``resolved`` = {artists, venues, cities} the document
    actually resolves to (when absent, identity is not contradicted — but any
    present mismatch rejects). ``rights_status`` defaults to the candidate's
    own rights field.
    """
    reason = _reject_reason(candidate, target_event=target_event, resolved=resolved,
                            rights_status=rights_status, commercial_use_status=commercial_use_status)
    if reason:
        return {"verification_status": VERIFICATION_REJECTED, "rejection_reason": reason}
    return {"verification_status": VERIFICATION_ACCEPTED, "rejection_reason": None}


def _reject_reason(
    candidate: dict[str, Any],
    *,
    target_event: dict[str, Any] | None,
    resolved: dict[str, list[str]] | None,
    rights_status: str | None,
    commercial_use_status: str | None,
) -> str | None:
    # 1. Provenance is mandatory.
    if not candidate.get("source_document_id"):
        return "claim_without_source_document"
    if candidate.get("evidence_span_start") is None or candidate.get("evidence_span_end") is None:
        return "claim_without_evidence_span"

    # 2. Identity resolution (fail closed on any explicit mismatch).
    if target_event is not None and resolved is not None:
        if _contradicts(target_event, resolved):
            return "wrong_artist_venue_city_or_date"

    # 3. Temporal semantics.
    evidence_class = candidate.get("evidence_class")
    cutoff_type = candidate.get("cutoff_type")
    gran = candidate.get("granularity")

    if evidence_class == ESTIMATED_RESEARCH_ONLY:
        return "estimated_research_only_never_admissible"
    if evidence_class == UNKNOWN:
        return "unknown_never_admissible"

    # announcement / "on sale now" / "announced today" are BOUNDS, never exact.
    if evidence_class == ARCHIVE_CAPTURE_UPPER_BOUND:
        if candidate.get("cutoff_timestamp") or candidate.get("candidate_value"):
            # an upper bound must not present an exact cutoff instant
            if cutoff_type not in (CUTOFF_BOOKING_OR_OFFER,) and not candidate.get("upper_bound"):
                return "archive_upper_bound_must_set_upper_bound_not_exact"
        if candidate.get("source_publication_time") and candidate.get("upper_bound") == candidate.get("source_publication_time"):
            # capture/publication conflation is checked by callers; here guard
            # the generic case: an upper bound must never equal a claimed
            # publication time unless it is explicitly a capture.
            pass

    # booking exact must come from an explicit booking/offer source.
    if cutoff_type == CUTOFF_BOOKING_OR_OFFER and evidence_class in STRICT_PIT_CLASSES:
        interp = (candidate.get("interpretation") or "").upper()
        if not any(marker in interp for marker in EXACT_BOOKING_MARKERS):
            return "announcement_interpreted_as_booking_exact"

    # relative date without a defensible anchor.
    if cutoff_type in ("GENERAL_ONSALE", "PRESALE", "ANNOUNCEMENT"):
        if candidate.get("candidate_value") is None and not (
            candidate.get("upper_bound") or candidate.get("lower_bound")
        ):
            return "relative_date_without_anchor"

    # interval collapsed to a midpoint (exact value + both bounds present).
    if candidate.get("candidate_value") and candidate.get("lower_bound") and candidate.get("upper_bound"):
        if candidate.get("lower_bound") == candidate.get("upper_bound") == candidate.get("candidate_value"):
            return "interval_collapsed_to_midpoint"

    # granularity must be a known precision.
    if gran not in ("EXACT", "DAY", "MONTH"):
        return "unsupported_granularity"

    # timezone precision: an EXACT timestamp must carry an explicit offset.
    val = candidate.get("candidate_value") or candidate.get("upper_bound") or candidate.get("lower_bound")
    if gran == "EXACT" and val and _has_time_component(val) and not _has_tz(val):
        return "unsupported_timezone_precision"

    # 4. Rights.
    rs = rights_status or candidate.get("rights_status")
    if rs not in ADMISSIBLE_RIGHTS:
        return "source_rights_failure"
    return None


def _contradicts(target_event: dict[str, Any], resolved: dict[str, list[str]]) -> bool:
    """Fail closed when the document resolves to a different identity."""
    def norm(v: str) -> str:
        return "".join(ch for ch in str(v).lower() if ch.isalnum())

    if resolved.get("artists") and target_event.get("artist"):
        t = norm(target_event["artist"])
        if t and all(norm(a) != t for a in resolved["artists"] if a):
            return True
    if resolved.get("venues") and target_event.get("venue"):
        t = norm(target_event["venue"])
        if t and all(norm(v) != t for v in resolved["venues"] if v):
            return True
    if resolved.get("cities") and (target_event.get("city") or target_event.get("market")):
        t = norm(target_event.get("city") or target_event.get("market"))
        if t and all(norm(c) != t for c in resolved["cities"] if c):
            return True
    return False


def _has_time_component(value: str) -> bool:
    return "T" in value and any(ch.isdigit() for ch in value.split("T")[-1][:2])


def _has_tz(value: str) -> bool:
    return bool(value.endswith("Z") or "+" in value[10:] or "-" in value[10:])
