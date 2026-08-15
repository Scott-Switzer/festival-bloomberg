"""PRE_EVENT_CUTOFF_ACQUISITION_V1 — decision-time taxonomy + warm-start.

The binding research question is now pre-event knowability:

    P(Y_show | information available at booking/offer time)

not the post-show result corpus. This module defines the DECISION-TIME
TAXONOMY (never collapsed), the booking/offer evidence types (a public
announcement date is NEVER a booking date — at most it establishes a BOUND),
and the warm-start-by-cutoff measurement that answers, for every target
event and every cutoff, how many PRIOR artist/venue/market outcomes were
actually knowable.

PIT doctrine is inherited from ``flywheel.pit``: evidence classes gate
STRICT vs CONSERVATIVE validation; day-level evidence never leaks at
midnight; an archive/first-seen capture proves availability BY that time,
never original publication; UNKNOWN is never zero and never upgraded.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Iterable

from ..acquisition.contracts import content_hash_of, utc_now
from .pit import (
    ARCHIVE_CAPTURE_UPPER_BOUND,
    AVAILABILITY_TIMESTAMP_COLUMN,
    CONSERVATIVE_BOUND_PIT,
    OBSERVED_DAY,
    OBSERVED_EXACT,
    OBSERVED_MONTH,
    SOURCE_PERIOD_BOUND,
    STRICT_PIT,
    STRICT_PIT_CLASSES,
    event_key_from_engagement,
    mode_eligible,
    validate_evidence_class,
    validate_pit_mode,
)

CUTOFF_OBJECTIVE_VERSION = "pre_event_cutoff_acquisition_v1"

# ---------------------------------------------------------------------------
# Decision-time taxonomy (cutoff types are NEVER collapsed)
# ---------------------------------------------------------------------------
CUTOFF_BOOKING_OR_OFFER = "BOOKING_OR_OFFER"
CUTOFF_ANNOUNCEMENT = "ANNOUNCEMENT"
CUTOFF_PRESALE = "PRESALE"
CUTOFF_GENERAL_ONSALE = "GENERAL_ONSALE"
CUTOFF_TICKET_PRICE_OBSERVATION = "TICKET_PRICE_OBSERVATION"
CUTOFF_EVENT_DATE = "EVENT_DATE"
CUTOFF_RESULT_PUBLICATION = "RESULT_PUBLICATION"
CUTOFF_SETTLEMENT = "SETTLEMENT"

CUTOFF_TYPES = frozenset(
    {
        CUTOFF_BOOKING_OR_OFFER,
        CUTOFF_ANNOUNCEMENT,
        CUTOFF_PRESALE,
        CUTOFF_GENERAL_ONSALE,
        CUTOFF_TICKET_PRICE_OBSERVATION,
        CUTOFF_EVENT_DATE,
        CUTOFF_RESULT_PUBLICATION,
        CUTOFF_SETTLEMENT,
    }
)

# ---------------------------------------------------------------------------
# Cutoff evidence kinds (HOW the cutoff is evidenced)
# ---------------------------------------------------------------------------
KIND_OBSERVED = "OBSERVED"
KIND_OBSERVED_BOOKING_DATE = "OBSERVED_BOOKING_DATE"
KIND_OBSERVED_OFFER_DATE = "OBSERVED_OFFER_DATE"
KIND_CONTRACT_DATE = "CONTRACT_DATE"
KIND_INTERNAL_FIRST_PARTY_BOOKING_DATE = "INTERNAL_FIRST_PARTY_BOOKING_DATE"
KIND_ANNOUNCEMENT_UPPER_BOUND = "ANNOUNCEMENT_UPPER_BOUND"
KIND_FIRST_SEEN_UPPER_BOUND = "FIRST_SEEN_UPPER_BOUND"
KIND_ARCHIVE_CAPTURE_UPPER_BOUND = "ARCHIVE_CAPTURE_UPPER_BOUND"
KIND_ESTIMATED_RESEARCH_ONLY = "ESTIMATED_RESEARCH_ONLY"
KIND_UNKNOWN = "UNKNOWN"

CUTOFF_KINDS = frozenset(
    {
        KIND_OBSERVED,
        KIND_OBSERVED_BOOKING_DATE,
        KIND_OBSERVED_OFFER_DATE,
        KIND_CONTRACT_DATE,
        KIND_INTERNAL_FIRST_PARTY_BOOKING_DATE,
        KIND_ANNOUNCEMENT_UPPER_BOUND,
        KIND_FIRST_SEEN_UPPER_BOUND,
        KIND_ARCHIVE_CAPTURE_UPPER_BOUND,
        KIND_ESTIMATED_RESEARCH_ONLY,
        KIND_UNKNOWN,
    }
)

#: Kinds that are exact observed booking/offer dates (only for BOOKING cutoff).
BOOKING_EXACT_KINDS = frozenset(
    {
        KIND_OBSERVED_BOOKING_DATE,
        KIND_OBSERVED_OFFER_DATE,
        KIND_CONTRACT_DATE,
        KIND_INTERNAL_FIRST_PARTY_BOOKING_DATE,
    }
)

GRANULARITY_EXACT = "EXACT"
GRANULARITY_DAY = "DAY"
GRANULARITY_MONTH = "MONTH"
GRANULARITIES = frozenset({GRANULARITY_EXACT, GRANULARITY_DAY, GRANULARITY_MONTH})

BOUND_BOOKING_NO_LATER_THAN_ANNOUNCEMENT = "booking_no_later_than_announcement"
BOUND_ANNOUNCEMENT_NO_LATER_THAN_FIRST_SEEN = "announcement_no_later_than_first_seen"
BOUND_RESULT_NO_LATER_THAN_PUBLICATION = "result_available_no_later_than_publication"

#: Evidence class -> granularity (how precisely the timestamp is known).
CLASS_GRANULARITY = {
    OBSERVED_EXACT: GRANULARITY_EXACT,
    OBSERVED_DAY: GRANULARITY_DAY,
    OBSERVED_MONTH: GRANULARITY_MONTH,
    ARCHIVE_CAPTURE_UPPER_BOUND: GRANULARITY_EXACT,
    SOURCE_PERIOD_BOUND: GRANULARITY_DAY,
}


def validate_cutoff_type(cutoff_type: str) -> str:
    if cutoff_type not in CUTOFF_TYPES:
        raise ValueError(f"cutoff_type {cutoff_type!r} is not in the decision-time taxonomy")
    return cutoff_type


def validate_cutoff_kind(cutoff_kind: str) -> str:
    if cutoff_kind not in CUTOFF_KINDS:
        raise ValueError(f"cutoff_kind {cutoff_kind!r} is not in the cutoff-kind taxonomy")
    return cutoff_kind


def validate_granularity(granularity: str) -> str:
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity {granularity!r} is not in {sorted(GRANULARITIES)}")
    return granularity


# ---------------------------------------------------------------------------
# Row builder (pure, append-only)
# ---------------------------------------------------------------------------
def build_cutoff_evidence_row(
    *,
    canonical_event_id: str,
    cutoff_type: str,
    cutoff_kind: str,
    evidence_class: str,
    granularity: str,
    source_event_id: str | None = None,
    cutoff_timestamp: str | None = None,
    lower_bound: str | None = None,
    upper_bound: str | None = None,
    bound_semantics: str | None = None,
    source_provider: str | None = None,
    source_url: str | None = None,
    source_document_id: str | None = None,
    archive_capture_time: str | None = None,
    retrieved_at: str | None = None,
    knowledge_time: datetime | None = None,
    rights_status: str = "RESEARCH_ONLY",
    commercial_use_status: str = "RESEARCH_ONLY",
    confidence: str | None = None,
    software_version: str = CUTOFF_OBJECTIVE_VERSION,
) -> dict[str, Any]:
    """Build one ``flywheel.pre_event_cutoff_evidence`` row (pure)."""
    validate_cutoff_type(cutoff_type)
    validate_cutoff_kind(cutoff_kind)
    validate_evidence_class(evidence_class)
    validate_granularity(granularity)
    now = knowledge_time or utc_now()
    cutoff_id = f"cut_{content_hash_of({
        'event': canonical_event_id,
        'type': cutoff_type,
        'kind': cutoff_kind,
        'class': evidence_class,
        'ts': cutoff_timestamp,
        'lo': lower_bound,
        'hi': upper_bound,
        'doc': source_document_id,
    })[:24]}"
    return {
        "cutoff_id": cutoff_id,
        "canonical_event_id": canonical_event_id,
        "source_event_id": source_event_id,
        "cutoff_type": cutoff_type,
        "cutoff_kind": cutoff_kind,
        "evidence_class": evidence_class,
        "granularity": granularity,
        "cutoff_timestamp": cutoff_timestamp,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "bound_semantics": bound_semantics,
        "source_provider": source_provider,
        "source_url": source_url,
        "source_document_id": source_document_id,
        "archive_capture_time": archive_capture_time,
        "retrieved_at": retrieved_at,
        "knowledge_time": now.isoformat(),
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "confidence": confidence,
        "software_version": software_version,
    }


def _start_of_day(value: Any) -> str | None:
    """Day-granularity timestamp: the START of the documented day.

    For EVENT_DATE the cutoff means "the show happened on date D". Anything
    published ON day D may or may not have been knowable before the show, so a
    fail-closed comparison uses the START of day D (a result published the same
    day as the show is NOT counted as a prior). This mirrors the existing
    warm-start semantics (``prior.start_date < target.start_date``).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        try:
            d = date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return datetime.combine(d, time.min).isoformat()


# ---------------------------------------------------------------------------
# Derivation helpers (all REAL persisted data, never fabricated)
# ---------------------------------------------------------------------------
def derive_event_date_cutoff(engagement: dict[str, Any]) -> dict[str, Any] | None:
    """EVENT_DATE cutoff from a single-show engagement's real start_date.

    The event date is observed day-level evidence (the scheduled show date).
    Returns None for engagements without a start_date.
    """
    start = engagement.get("start_date")
    if start is None:
        return None
    return build_cutoff_evidence_row(
        canonical_event_id=event_key_from_engagement(engagement),
        source_event_id=engagement.get("engagement_id"),
        cutoff_type=CUTOFF_EVENT_DATE,
        cutoff_kind=KIND_OBSERVED,
        evidence_class=OBSERVED_DAY,
        granularity=GRANULARITY_DAY,
        cutoff_timestamp=_start_of_day(start),
        source_provider=engagement.get("reporting_source"),
        source_url=engagement.get("source_url"),
        source_document_id=engagement.get("engagement_id"),
        rights_status=engagement.get("rights_status") or "RESEARCH_ONLY",
        commercial_use_status=engagement.get("commercial_use_status") or "RESEARCH_ONLY",
    )


def derive_result_publication_cutoff(pit_row: dict[str, Any]) -> dict[str, Any] | None:
    """RESULT_PUBLICATION cutoff from a persisted PIT reconstruction row.

    OBSERVED_* rows carry an exact/observed publication instant
    (source_publication_time; end of day for OBSERVED_DAY) and set
    ``cutoff_timestamp``. ARCHIVE_CAPTURE_UPPER_BOUND / SOURCE_PERIOD_BOUND
    only prove availability BY some time: they set ``upper_bound`` (never
    ``cutoff_timestamp``) so STRICT excludes them and only CONSERVATIVE_BOUND
    may consume them. ESTIMATED_RESEARCH_ONLY / UNKNOWN have no availability
    and yield None.
    """
    evidence_class = pit_row.get("evidence_class")
    if evidence_class not in AVAILABILITY_TIMESTAMP_COLUMN:
        return None
    col = AVAILABILITY_TIMESTAMP_COLUMN[evidence_class]
    avail = pit_row.get(col)
    if avail is None:
        return None
    if isinstance(avail, (datetime, date)):
        avail = avail.isoformat()
    common = dict(
        canonical_event_id=pit_row.get("canonical_event_id"),
        source_event_id=pit_row.get("source_document_id"),
        cutoff_type=CUTOFF_RESULT_PUBLICATION,
        evidence_class=evidence_class,
        granularity=CLASS_GRANULARITY.get(evidence_class, GRANULARITY_EXACT),
        source_provider=pit_row.get("source_provider"),
        source_url=pit_row.get("source_url"),
        source_document_id=pit_row.get("source_document_id"),
        rights_status=pit_row.get("rights_status") or "RESEARCH_ONLY",
        commercial_use_status=pit_row.get("commercial_use_status") or "RESEARCH_ONLY",
    )
    if evidence_class == ARCHIVE_CAPTURE_UPPER_BOUND:
        return build_cutoff_evidence_row(
            cutoff_kind=KIND_ARCHIVE_CAPTURE_UPPER_BOUND,
            upper_bound=str(avail),
            bound_semantics=BOUND_RESULT_NO_LATER_THAN_PUBLICATION,
            archive_capture_time=str(avail),
            **common,
        )
    if evidence_class == SOURCE_PERIOD_BOUND:
        return build_cutoff_evidence_row(
            cutoff_kind=KIND_OBSERVED,
            upper_bound=str(avail),
            bound_semantics=BOUND_RESULT_NO_LATER_THAN_PUBLICATION,
            **common,
        )
    return build_cutoff_evidence_row(
        cutoff_kind=KIND_OBSERVED,
        cutoff_timestamp=str(avail),
        **common,
    )


def derive_forward_announcement_and_booking_bounds(
    forward_event: dict[str, Any],
) -> list[dict[str, Any]]:
    """ANNOUNCEMENT + BOOKING upper bounds for a real forward watch event.

    ``first_seen_at`` is our first retrieval of the event listing. That proves
    the listing existed no later than first_seen, so:

        announcement <= first_seen     (FIRST_SEEN_UPPER_BOUND)
        booking <= announcement <= first_seen   (ANNOUNCEMENT_UPPER_BOUND)

    Both are BOUNDS (upper_bound set, cutoff_timestamp NULL). A public
    announcement date is NEVER promoted to a booking date; the second row
    carries the inference explicitly in ``bound_semantics``.
    """
    first_seen = forward_event.get("first_seen_at")
    if not first_seen:
        return []
    if isinstance(first_seen, datetime):
        first_seen = first_seen.isoformat()
    else:
        first_seen = str(first_seen)
    canonical = forward_event.get("watch_event_id") or forward_event.get("provider_event_id")
    common = dict(
        canonical_event_id=canonical,
        source_event_id=forward_event.get("watch_event_id"),
        evidence_class=ARCHIVE_CAPTURE_UPPER_BOUND,
        granularity=GRANULARITY_EXACT,
        upper_bound=first_seen,
        archive_capture_time=first_seen,
        source_provider=forward_event.get("provider"),
        source_url=forward_event.get("source_url"),
        rights_status=forward_event.get("rights_status") or "RESEARCH_ONLY",
        commercial_use_status=forward_event.get("commercial_use_status") or "RESEARCH_ONLY",
    )
    announcement = build_cutoff_evidence_row(
        cutoff_type=CUTOFF_ANNOUNCEMENT,
        cutoff_kind=KIND_FIRST_SEEN_UPPER_BOUND,
        bound_semantics=BOUND_ANNOUNCEMENT_NO_LATER_THAN_FIRST_SEEN,
        **common,
    )
    booking = build_cutoff_evidence_row(
        cutoff_type=CUTOFF_BOOKING_OR_OFFER,
        cutoff_kind=KIND_ANNOUNCEMENT_UPPER_BOUND,
        bound_semantics=BOUND_BOOKING_NO_LATER_THAN_ANNOUNCEMENT,
        **common,
    )
    return [announcement, booking]


# ---------------------------------------------------------------------------
# Effective cutoff resolution under a PIT mode
# ---------------------------------------------------------------------------
def effective_cutoff_timestamp(row: dict[str, Any], *, mode: str) -> datetime | None:
    """Resolve the decision instant for a cutoff-evidence row under ``mode``.

    STRICT_PIT uses ONLY an exact observed ``cutoff_timestamp``. A bound-only
    row (booking/announcement upper bound) has no exact instant and is
    excluded — its event has no STRICT cutoff.

    CONSERVATIVE_BOUND_PIT may additionally consume a bound: an ``upper_bound``
    is used as a documented optimistic proxy ("happened no later than U", so
    priors published before U COULD have been known), and when only a
    ``lower_bound`` exists it is used fail-closed ("definitely known before
    L"). Bounds are never presented as exact.
    """
    validate_pit_mode(mode)
    ts = row.get("cutoff_timestamp")
    if ts:
        return _parse_ts(ts)
    if mode == CONSERVATIVE_BOUND_PIT:
        hi = row.get("upper_bound")
        if hi:
            return _parse_ts(hi)
        lo = row.get("lower_bound")
        if lo:
            return _parse_ts(lo)
    return None


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    try:
        s = str(value)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Warm-start by cutoff (the central acceptance metric)
# ---------------------------------------------------------------------------
_DIMENSION_COLUMNS = {"artist": "artist", "venue": "venue", "market": "market"}


def _prior_availability(conn, *, evidence_classes: Iterable[str]) -> dict[str, str | None]:
    """canonical_event_id -> earliest class-specific availability timestamp.

    Only evidence classes in ``evidence_classes`` are considered; their
    availability column is class-specific (never a blind COALESCE).
    ESTIMATED_RESEARCH_ONLY / UNKNOWN have no availability column and are
    never returned.
    """
    classes = sorted(set(evidence_classes))
    if not classes:
        return {}
    cases: list[str] = []
    for cls in classes:
        col = AVAILABILITY_TIMESTAMP_COLUMN.get(cls)
        if col is None:
            continue
        cases.append(f"WHEN evidence_class = ? THEN {col}")
    if not cases:
        return {}
    sql = (
        "SELECT canonical_event_id, MIN(CASE "
        + " ".join(cases)
        + " END) FROM flywheel.pit_reconstruction_evidence "
        "WHERE evidence_class IN ("
        + ",".join("?" for _ in classes)
        + ") GROUP BY canonical_event_id"
    )
    # Params: one per WHEN in the CASE (classes), then one per IN placeholder.
    params = list(classes) + list(classes)
    rows = conn.execute(sql, params).fetchall()
    return {str(r[0]): (r[1].isoformat() if r[1] else None) for r in rows if r[0]}


def prior_outcome_distribution(
    conn,
    *,
    cutoff_type: str,
    dimension: str = "artist",
    mode: str = STRICT_PIT,
    evidence_classes: Iterable[str] | None = None,
    min_prior: int = 3,
) -> dict[str, Any]:
    """Distribution of knowable same-dimension prior outcomes at a cutoff.

    For every single-show reported target engagement that has a cutoff
    evidence row of ``cutoff_type`` (resolved under ``mode``), counts how many
    PRIOR same-dimension results were actually knowable (their PIT
    availability timestamp strictly precedes the cutoff AND their event
    preceded the target). Returns the full distribution (0/1/2/3+/5+) plus
    the >= ``min_prior`` count — never just an average.

    Targets with no cutoff evidence of this type are UNKNOWN and reported
    separately; they are NOT silently zeroed.
    """
    validate_cutoff_type(cutoff_type)
    if dimension not in _DIMENSION_COLUMNS:
        raise ValueError(f"dimension {dimension!r} not in {sorted(_DIMENSION_COLUMNS)}")
    validate_pit_mode(mode)
    dim_col = _DIMENSION_COLUMNS[dimension]
    if evidence_classes is None:
        evidence_classes = STRICT_PIT_CLASSES if mode == STRICT_PIT else (
            STRICT_PIT_CLASSES | {ARCHIVE_CAPTURE_UPPER_BOUND, SOURCE_PERIOD_BOUND}
        )

    # 1. eligible targets: single-show, reported, with a headcount + date.
    targets = [
        row
        for row in conn.execute(
            "SELECT engagement_id, artist, venue, market, start_date, headcount_total "
            "FROM research.boxoffice_engagements "
            "WHERE is_reported = TRUE "
            "  AND (is_multi_show IS NULL OR is_multi_show = FALSE) "
            "  AND headcount_total IS NOT NULL "
            "  AND start_date IS NOT NULL"
        ).fetchall()
    ]
    target_keys = {
        event_key_from_engagement(
            {"artist": r[1], "venue": r[2], "market": r[3], "start_date": r[4]}
        ): {"engagement_id": r[0], "dim_value": r[1] if dimension == "artist" else (r[2] if dimension == "venue" else r[3]), "start_date": r[4]}
        for r in targets
    }

    # 2. cutoff evidence for this type, resolved under the mode.
    cutoffs: dict[str, datetime] = {}
    for row in conn.execute(
        "SELECT canonical_event_id, cutoff_timestamp, lower_bound, upper_bound "
        "FROM flywheel.pre_event_cutoff_evidence WHERE cutoff_type = ?",
        [cutoff_type],
    ).fetchall():
        eff = effective_cutoff_timestamp(
            {"cutoff_timestamp": row[1], "lower_bound": row[2], "upper_bound": row[3]},
            mode=mode,
        )
        if eff is not None:
            key = str(row[0])
            if key not in cutoffs or eff < cutoffs[key]:
                cutoffs[key] = eff

    # 3. prior availability per canonical event.
    availability = _prior_availability(conn, evidence_classes=evidence_classes)

    # 4. priors indexed by dimension value.
    prior_rows = conn.execute(
        "SELECT artist, venue, market, start_date, headcount_total "
        "FROM research.boxoffice_engagements "
        "WHERE is_reported = TRUE "
        "  AND (is_multi_show IS NULL OR is_multi_show = FALSE) "
        "  AND headcount_total IS NOT NULL "
        "  AND start_date IS NOT NULL"
    ).fetchall()
    priors_by_dim: dict[str, list[tuple[Any, str | None]]] = {}
    for r in prior_rows:
        dim_value = r[0] if dimension == "artist" else (r[1] if dimension == "venue" else r[2])
        if dim_value is None:
            continue
        key = event_key_from_engagement(
            {"artist": r[0], "venue": r[1], "market": r[2], "start_date": r[3]}
        )
        avail = availability.get(key)
        if avail is None:
            continue
        priors_by_dim.setdefault(dim_value, []).append((r[3], avail))

    # 5. distribution.
    distribution = {0: 0, 1: 0, 2: 0, 3: 0, 5: 0}
    ge_min = 0
    known_cutoff_targets = 0
    for key, target in target_keys.items():
        cutoff = cutoffs.get(key)
        if cutoff is None:
            continue  # UNKNOWN cutoff: reported separately, never zeroed
        known_cutoff_targets += 1
        dim_value = target["dim_value"]
        if dim_value is None:
            continue
        n = 0
        for prior_date, avail_ts in priors_by_dim.get(dim_value, []):
            if prior_date is None or target["start_date"] is None:
                continue
            if prior_date >= target["start_date"]:
                continue
            if _parse_ts(avail_ts) is not None and _parse_ts(avail_ts) < cutoff:
                n += 1
        if n >= 5:
            distribution[5] += 1
        elif n >= 3:
            distribution[3] += 1
        elif n == 2:
            distribution[2] += 1
        elif n == 1:
            distribution[1] += 1
        else:
            distribution[0] += 1
        if n >= min_prior:
            ge_min += 1

    return {
        "cutoff_type": cutoff_type,
        "dimension": dimension,
        "mode": mode,
        "min_prior": min_prior,
        "eligible_single_show_targets": len(target_keys),
        "targets_with_known_cutoff": known_cutoff_targets,
        "targets_with_unknown_cutoff": len(target_keys) - known_cutoff_targets,
        "prior_distribution": {
            "0": distribution[0],
            "1": distribution[1],
            "2": distribution[2],
            "3_plus": distribution[3] + distribution[5],
            "5_plus": distribution[5],
        },
        f"targets_with_{min_prior}_plus_priors": ge_min,
    }


# ---------------------------------------------------------------------------
# Decision-time coverage (section 10)
# ---------------------------------------------------------------------------
def decision_time_coverage(conn) -> dict[str, int]:
    """Decision-time cutoff coverage over the persisted evidence table.

    Counts are computed from ``flywheel.pre_event_cutoff_evidence`` only; the
    single-show historical universe is separated from the forward watch
    universe (watch_* canonical ids) so broad forward enrollment can never
    inflate a historical-coverage number.
    """
    def count(where: str, params: list[Any] | None = None) -> int:
        row = conn.execute(
            f"SELECT COUNT(DISTINCT canonical_event_id) FROM flywheel.pre_event_cutoff_evidence WHERE {where}",
            params or [],
        ).fetchone()
        return int(row[0]) if row and row[0] else 0

    historical_prefix = "canonical_event_id LIKE 'boxoffice\\_%' ESCAPE '\\'"
    return {
        "EVENTS_WITH_ANNOUNCEMENT_CUTOFF": count(f"{historical_prefix} AND cutoff_type = ?", [CUTOFF_ANNOUNCEMENT]),
        "EVENTS_WITH_PRESALE_CUTOFF": count(f"{historical_prefix} AND cutoff_type = ?", [CUTOFF_PRESALE]),
        "EVENTS_WITH_ONSALE_CUTOFF": count(f"{historical_prefix} AND cutoff_type = ?", [CUTOFF_GENERAL_ONSALE]),
        "EVENTS_WITH_BOOKING_EXACT": count(
            f"{historical_prefix} AND cutoff_type = ? AND cutoff_kind IN (?,?,?,?)",
            [CUTOFF_BOOKING_OR_OFFER, *sorted(BOOKING_EXACT_KINDS)],
        ),
        "EVENTS_WITH_BOOKING_UPPER_BOUND": count(
            f"{historical_prefix} AND cutoff_type = ? AND cutoff_kind = ?",
            [CUTOFF_BOOKING_OR_OFFER, KIND_ANNOUNCEMENT_UPPER_BOUND],
        ),
        "EVENTS_WITH_BOOKING_INTERVAL": count(
            f"{historical_prefix} AND cutoff_type = ? AND lower_bound IS NOT NULL AND upper_bound IS NOT NULL",
            [CUTOFF_BOOKING_OR_OFFER],
        ),
        "EVENTS_WITH_RESULT_PUBLICATION": count(
            f"{historical_prefix} AND cutoff_type = ?", [CUTOFF_RESULT_PUBLICATION]
        ),
        "EVENTS_WITH_EVENT_DATE": count(
            f"{historical_prefix} AND cutoff_type = ?", [CUTOFF_EVENT_DATE]
        ),
        # Forward universe (announcement/booking first-seen bounds).
        "FORWARD_EVENTS_WITH_ANNOUNCEMENT_BOUND": count(
            "cutoff_type = ? AND cutoff_kind = ?",
            [CUTOFF_ANNOUNCEMENT, KIND_FIRST_SEEN_UPPER_BOUND],
        ),
        "FORWARD_EVENTS_WITH_BOOKING_BOUND": count(
            "cutoff_type = ? AND cutoff_kind = ?",
            [CUTOFF_BOOKING_OR_OFFER, KIND_ANNOUNCEMENT_UPPER_BOUND],
        ),
    }


def reconstruction_candidates(engagements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """EVENT-DIRECTED historical reconstruction candidates (no crawling here).

    For each single-show engagement, produce the targeted lookup targets a
    future keyed/archival pass would use (artist / venue / event date). This
    documents the deterministic candidate universe; it performs NO retrieval.
    """
    out: list[dict[str, Any]] = []
    for eng in engagements:
        if eng.get("is_multi_show"):
            continue
        if not eng.get("start_date"):
            continue
        out.append(
            {
                "engagement_id": eng.get("engagement_id"),
                "artist": eng.get("artist"),
                "venue": eng.get("venue"),
                "market": eng.get("market") or eng.get("city"),
                "event_date": str(eng.get("start_date"))[:10],
                "source_url": eng.get("source_url"),
            }
        )
    return out
