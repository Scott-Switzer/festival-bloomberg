"""Coverage measurement for the Data Flywheel.

The acquisition metric is decision coverage — "how much does this source
improve our ability to validate a decision model?" — not row counts. Every
measurement is computed from the persisted warehouse (never fabricated), and
each run appends rows to ``flywheel.coverage_snapshots`` so coverage history
is auditable and never rewritten.

KPI vocabulary (see flywheel/objectives.py):
    OUTCOME_CLAIMS               claims in the ledger (claims != events)
    UNIQUE_EVENTS_WITH_OUTCOMES  distinct events with >= 1 defensible claim
    FULLY_SETTLED_EVENTS         distinct events with settlement evidence
The four RATES (warm_start_rate, offer_time_reconstructable_rate,
ticket_pace_coverage, settlement_coverage) measure progress toward a real
underwriting system, not raw database size.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..economics.outcome_claims import (
    ATTENDANCE_TYPES,
    CAPACITY_TYPES,
    PAID_TICKETS,
    SETTLEMENT_TYPES,
    SOLD_OUT_TYPES,
    TICKET_GROSS,
    TICKET_NET,
    TICKETS_SOLD,
)
from .objectives import (
    OBJECTIVES_BY_KEY_V1,
    OBJECTIVE_VERSION_V1,
    validate_objective_key,
)

#: Claim types that constitute a defensible single-show outcome row.
OUTCOME_CLAIM_TYPES = tuple(
    sorted(
        set(ATTENDANCE_TYPES)
        | {PAID_TICKETS, TICKETS_SOLD}
        | {TICKET_GROSS, TICKET_NET}
        | set(SOLD_OUT_TYPES)
    )
)

BELOW_TARGET = "BELOW_TARGET"
AT_TARGET = "AT_TARGET"
ABOVE_TARGET = "ABOVE_TARGET"


def _table_exists(conn, schema: str, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchone()
    return bool(row and row[0] and row[0] > 0)


# ---------------------------------------------------------------------------
# Individual measurements (pure: read the warehouse, write nothing)
# ---------------------------------------------------------------------------
def count_canonical_engagements(conn) -> int:
    """Canonical boxscore ENGAGEMENTS (bookings, incl. multi-show aggregates).

    Prefers the resolved corpus (``research.canonical_boxoffice_engagements``)
    and falls back to the raw engagement corpus when resolution has not run.
    An engagement is NOT a performance — never use this for PERFORMANCES.
    """
    canonical = conn.execute(
        "SELECT COUNT(*) FROM research.canonical_boxoffice_engagements"
    ).fetchone()[0]
    if canonical and canonical > 0:
        return int(canonical)
    return int(
        conn.execute("SELECT COUNT(*) FROM research.boxoffice_engagements").fetchone()[0]
    )


def count_single_show_engagements(conn) -> int:
    """Defensible single-show engagements (multi-show aggregates excluded).

    Same preference order as ``count_canonical_engagements``. Only reported,
    non-aggregate rows qualify.
    """
    canonical = conn.execute(
        "SELECT COUNT(*) FROM research.canonical_boxoffice_engagements "
        "WHERE (is_multi_show IS NULL OR is_multi_show = FALSE)"
    ).fetchone()[0]
    if canonical and canonical > 0:
        return int(canonical)
    row = conn.execute(
        "SELECT COUNT(*) FROM research.boxoffice_engagements "
        "WHERE is_reported = TRUE "
        "  AND (is_multi_show IS NULL OR is_multi_show = FALSE)"
    ).fetchone()
    return int(row[0]) if row else 0


def count_canonical_performances(conn) -> int:
    """Defensible single-performance denominator.

    Only defensible single-show/event records enter a metric called
    PERFORMANCES. When a dedicated canonical event-performance table exists
    (``research.canonical_event_performances``) it is preferred; until then the
    eligible denominator is the single-show engagement count. Multi-show
    aggregates are NEVER counted as performances.
    """
    if _table_exists(conn, "research", "canonical_event_performances"):
        row = conn.execute(
            "SELECT COUNT(*) FROM research.canonical_event_performances"
        ).fetchone()
        if row and row[0] and row[0] > 0:
            return int(row[0])
    return count_single_show_engagements(conn)


def count_outcome_claims(conn) -> int:
    """Source-backed single-show outcome claims in the ledger (claims, not events)."""
    placeholders = ",".join("?" for _ in OUTCOME_CLAIM_TYPES)
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM economics.event_outcome_claims "
            f"WHERE outcome_type IN ({placeholders})",
            list(OUTCOME_CLAIM_TYPES),
        ).fetchone()[0]
    )


def _count_distinct_events_with_types(conn, claim_types) -> int:
    placeholders = ",".join("?" for _ in claim_types)
    return int(
        conn.execute(
            f"SELECT COUNT(DISTINCT canonical_event_id) FROM economics.event_outcome_claims "
            f"WHERE outcome_type IN ({placeholders})",
            list(claim_types),
        ).fetchone()[0]
    )


def count_unique_events_with_outcomes(conn) -> int:
    """Distinct canonical events with at least one defensible outcome claim."""
    return _count_distinct_events_with_types(conn, OUTCOME_CLAIM_TYPES)


def count_fully_settled_events(conn) -> int:
    """Distinct events with settlement/gross-contribution evidence."""
    return _count_distinct_events_with_types(conn, SETTLEMENT_TYPES)


def count_artists_with_3_plus_outcomes(conn) -> int:
    """Artists with >= 3 distinct single-show reported outcome engagements.

    Measured on the research corpus (the claims ledger does not carry an
    artist column); private-dataset artists join this count when their imports
    populate the research corpus or carry artist identity in their claims.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT artist
            FROM research.boxoffice_engagements
            WHERE is_reported = TRUE
              AND (is_multi_show IS NULL OR is_multi_show = FALSE)
              AND headcount_total IS NOT NULL
            GROUP BY artist
            HAVING COUNT(*) >= 3
        )
        """
    ).fetchone()
    return int(row[0]) if row else 0


def count_markets(conn) -> int:
    """Distinct markets across the research corpus and the event graph."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT market AS market FROM research.boxoffice_engagements
            WHERE market IS NOT NULL AND market <> ''
            UNION
            SELECT market_id AS market FROM events.events
            WHERE market_id IS NOT NULL AND market_id <> ''
        )
        """
    ).fetchone()
    return int(row[0]) if row else 0


def count_canonical_venues(conn) -> int:
    """Canonical venue rows in the event graph (fallback: research corpus)."""
    venues = int(conn.execute("SELECT COUNT(*) FROM events.venues").fetchone()[0])
    if venues > 0:
        return venues
    row = conn.execute(
        "SELECT COUNT(DISTINCT venue) FROM research.boxoffice_engagements "
        "WHERE venue IS NOT NULL AND venue <> ''"
    ).fetchone()
    return int(row[0]) if row else 0


def continuous_useful_period_years(conn, *, floor_year: int = 2018) -> int:
    """Distinct calendar years >= floor_year with reported outcome rows."""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT EXTRACT(YEAR FROM start_date))
        FROM research.boxoffice_engagements
        WHERE is_reported = TRUE
          AND headcount_total IS NOT NULL
          AND start_date IS NOT NULL
          AND EXTRACT(YEAR FROM start_date) >= ?
        """,
        [floor_year],
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def count_forward_tracked_future_events(conn, *, as_of: date | None = None) -> int:
    """Future events currently on the forward watchlist."""
    cutoff = as_of or date.today()
    row = conn.execute(
        """
        SELECT COUNT(*) FROM flywheel.forward_watch_events
        WHERE tracking_status = 'TRACKING'
          AND (event_date IS NULL OR event_date >= ?)
        """,
        [cutoff.isoformat()],
    ).fetchone()
    return int(row[0]) if row else 0


def count_private_events_with_settlement_evidence(conn) -> int:
    """Distinct canonical events with OBSERVED_PRIVATE SETTLEMENT-TYPE evidence.

    A private attendance/capacity/ticket claim does NOT establish settlement:
    only PROMOTER_CONTRIBUTION / SETTLEMENT_GROSS / SETTLEMENT_NET claims from
    OBSERVED_PRIVATE imports count. This is settlement EVIDENCE, not full
    settlement completeness.
    """
    placeholders = ",".join("?" for _ in SETTLEMENT_TYPES)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT canonical_event_id)
        FROM economics.event_outcome_claims
        WHERE observation_class = 'OBSERVED_PRIVATE'
          AND outcome_type IN ({placeholders})
        """,
        list(SETTLEMENT_TYPES),
    ).fetchone()
    return int(row[0]) if row else 0


def count_events_with_cutoff(conn, cutoff_column: str) -> int:
    """Distinct events with a known decision cutoff column (e.g. 'booking_cutoff')."""
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM economics.event_decision_cutoffs
        WHERE {cutoff_column} IS NOT NULL
        """
    ).fetchone()
    return int(row[0]) if row else 0


def count_events_with_ticket_pace(conn) -> int:
    """Distinct events with >= 2 forward ticket snapshots (pace evidence)."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT canonical_event_id
            FROM economics.primary_ticket_snapshots
            WHERE canonical_event_id IS NOT NULL
            GROUP BY canonical_event_id
            HAVING COUNT(*) >= 2
        )
        """
    ).fetchone()
    return int(row[0]) if row else 0


_PRIOR_DIMENSIONS = {"artist": "artist", "market": "market", "venue": "venue"}


def count_events_with_prior_results(
    conn, *, dimension: str, min_prior: int = 1
) -> int:
    """Events with >= min_prior same-dimension results published before them.

    Strict point-in-time: a prior engagement only counts when its
    ``source_publication_time`` is known and earlier than the target event's
    start date. Never backdated, never estimated, never multi-show.
    """
    if dimension not in _PRIOR_DIMENSIONS:
        raise ValueError(f"dimension {dimension!r} not in {sorted(_PRIOR_DIMENSIONS)}")
    col = _PRIOR_DIMENSIONS[dimension]
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT b.engagement_id
            FROM research.boxoffice_engagements b
            WHERE b.is_reported = TRUE
              AND (b.is_multi_show IS NULL OR b.is_multi_show = FALSE)
              AND b.headcount_total IS NOT NULL
              AND b.start_date IS NOT NULL
              AND (
                  SELECT COUNT(*) FROM research.boxoffice_engagements p
                  WHERE p.{col} = b.{col}
                    AND p.is_reported = TRUE
                    AND (p.is_multi_show IS NULL OR p.is_multi_show = FALSE)
                    AND p.headcount_total IS NOT NULL
                    AND p.start_date IS NOT NULL
                    AND p.source_publication_time IS NOT NULL
                    AND p.start_date < b.start_date
                    AND p.source_publication_time < b.start_date
              ) >= ?
        )
        """,
        [min_prior],
    ).fetchone()
    return int(row[0]) if row else 0


def _canonical_event_key_sql(table_alias: str, *, include_date: bool) -> str:
    """SQL expression reproducing ``pit.event_key_from_engagement``.

    The canonical event key is ``boxoffice_{artist}_{venue}_{start_date}``
    with artist/venue lowercased and spaces replaced by dashes (the same
    convention the PIT evidence rows are written with). The date suffix is
    part of the key; without it, distinct events at the same venue collapse.
    """
    date_part = (
        f"coalesce(cast({table_alias}.start_date as varchar), '')"
        if include_date
        else "''"
    )
    return (
        f"'boxoffice_' || replace(lower(coalesce({table_alias}.artist, 'unknown')), ' ', '-') "
        f"|| '_' || replace(lower(coalesce({table_alias}.venue, 'unknown')), ' ', '-') "
        f"|| '_' || {date_part}"
    )


def count_events_with_prior_results_pit(
    conn,
    *,
    dimension: str,
    min_prior: int = 1,
    evidence_classes: frozenset[str] | None = None,
) -> int:
    """Events with >= min_prior same-dimension results whose publication is
    PROVEN by persisted PIT reconstruction evidence before the target start.

    Strict point-in-time using the evidence table (not the raw engagement
    column, which is NULL across the corpus): a prior engagement counts only
    when a PIT evidence row for its canonical event carries a defensible
    ``source_publication_time`` earlier than the target event's start date.
    ``evidence_classes`` gates the validation mode (STRICT_PIT classes by
    default; conservative upper bounds may be passed explicitly). UNKNOWN is
    never upgraded.
    """
    if dimension not in _PRIOR_DIMENSIONS:
        raise ValueError(f"dimension {dimension!r} not in {sorted(_PRIOR_DIMENSIONS)}")
    col = _PRIOR_DIMENSIONS[dimension]
    if evidence_classes is None:
        from .pit import STRICT_PIT_CLASSES

        evidence_classes = STRICT_PIT_CLASSES
    if not evidence_classes:
        return 0
    placeholders = ",".join("?" for _ in evidence_classes)
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT b.engagement_id
            FROM research.boxoffice_engagements b
            WHERE b.is_reported = TRUE
              AND (b.is_multi_show IS NULL OR b.is_multi_show = FALSE)
              AND b.headcount_total IS NOT NULL
              AND b.start_date IS NOT NULL
              AND (
                  SELECT COUNT(*) FROM research.boxoffice_engagements p
                  JOIN flywheel.pit_reconstruction_evidence e
                    ON e.canonical_event_id = {_canonical_event_key_sql('p', include_date=True)}
                  WHERE p.{col} = b.{col}
                    AND p.is_reported = TRUE
                    AND (p.is_multi_show IS NULL OR p.is_multi_show = FALSE)
                    AND p.headcount_total IS NOT NULL
                    AND p.start_date IS NOT NULL
                    AND p.start_date < b.start_date
                    AND e.evidence_class IN ({placeholders})
                    AND e.source_publication_time IS NOT NULL
                    AND e.source_publication_time < b.start_date
              ) >= ?
        )
        """,
        [*evidence_classes, min_prior],
    ).fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Measurement orchestration
# ---------------------------------------------------------------------------
def measure_coverage(conn, *, as_of: datetime | None = None) -> list[dict[str, Any]]:
    """Measure actual coverage vs every registered objective/metric.

    Returns one row per objective with actual/target/ratio/status; writes
    nothing. ``evidence_query`` names the persisted source of the actual.
    Rate metrics are derived from their numerator count and the canonical
    performance denominator (0.0 when the denominator is empty, never NaN).
    """
    measured_at = as_of or datetime.utcnow()
    today = measured_at.date()

    canonical = count_canonical_performances(conn)

    # PIT reconstruction metrics — from the EVIDENCE table, never the raw
    # (NULL) engagement column. UNKNOWN is never upgraded; conservative
    # bounds join only under CONSERVATIVE_BOUND_PIT.
    from .pit import (
        ARCHIVE_CAPTURE_UPPER_BOUND,
        CONSERVATIVE_BOUND_CLASSES,
        OBSERVED_MONTH,
        SOURCE_PERIOD_BOUND,
        STRICT_PIT_CLASSES,
        count_events_reconstructable,
    )

    strict_pit_events = count_events_reconstructable(conn, mode="STRICT_PIT")
    conservative_events = count_events_reconstructable(conn, mode="CONSERVATIVE_BOUND_PIT")
    strict_warm = count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=3, evidence_classes=STRICT_PIT_CLASSES
    )
    conservative_warm = count_events_with_prior_results_pit(
        conn, dimension="artist", min_prior=3,
        evidence_classes=frozenset(STRICT_PIT_CLASSES) | CONSERVATIVE_BOUND_CLASSES,
    )

    counts: dict[str, tuple[float, str]] = {
        "CANONICAL_BOXSCORE_ENGAGEMENTS": (float(count_canonical_engagements(conn)), "research.canonical_boxoffice_engagements / research.boxoffice_engagements (engagements incl. multi-show aggregates)"),
        "SINGLE_SHOW_ENGAGEMENTS": (float(count_single_show_engagements(conn)), "research.canonical_boxoffice_engagements / research.boxoffice_engagements (is_multi_show = FALSE)"),
        "CANONICAL_PERFORMANCES": (float(canonical), "research.canonical_event_performances if present else single-show engagements (multi-show NEVER performances)"),
        "OUTCOME_CLAIMS": (float(count_outcome_claims(conn)), "economics.event_outcome_claims (outcome claim types)"),
        "UNIQUE_EVENTS_WITH_OUTCOMES": (float(count_unique_events_with_outcomes(conn)), "economics.event_outcome_claims (distinct events, defensible types)"),
        "FULLY_SETTLED_EVENTS": (float(count_fully_settled_events(conn)), "economics.event_outcome_claims (settlement types)"),
        "ARTISTS_WITH_3_PLUS_OUTCOMES": (float(count_artists_with_3_plus_outcomes(conn)), "research.boxoffice_engagements (reported, single-show, headcount present)"),
        "MARKETS": (float(count_markets(conn)), "research.boxoffice_engagements.market UNION events.events.market_id"),
        "CANONICAL_VENUES": (float(count_canonical_venues(conn)), "events.venues / research.boxoffice_engagements.venue"),
        "CONTINUOUS_USEFUL_PERIOD": (float(continuous_useful_period_years(conn)), "research.boxoffice_engagements.start_date distinct years >= 2018"),
        "FORWARD_TRACKED_FUTURE_EVENTS": (float(count_forward_tracked_future_events(conn, as_of=today)), "flywheel.forward_watch_events (TRACKING, event_date >= today)"),
        "PRIVATE_EVENTS_WITH_SETTLEMENT_EVIDENCE": (float(count_private_events_with_settlement_evidence(conn)), "economics.event_outcome_claims (OBSERVED_PRIVATE AND settlement outcome types)"),
        "EVENTS_WITH_ATTENDANCE": (float(_count_distinct_events_with_types(conn, ATTENDANCE_TYPES)), "economics.event_outcome_claims (attendance types)"),
        "EVENTS_WITH_PAID_TICKETS": (float(_count_distinct_events_with_types(conn, {PAID_TICKETS, TICKETS_SOLD})), "economics.event_outcome_claims (paid-ticket types)"),
        "EVENTS_WITH_GROSS": (float(_count_distinct_events_with_types(conn, {TICKET_GROSS, TICKET_NET})), "economics.event_outcome_claims (gross types)"),
        "EVENTS_WITH_SELLOUT": (float(_count_distinct_events_with_types(conn, SOLD_OUT_TYPES)), "economics.event_outcome_claims (sold-out assertion types)"),
        "EVENTS_WITH_CAPACITY": (float(_count_distinct_events_with_types(conn, CAPACITY_TYPES)), "economics.event_outcome_claims (capacity types)"),
        "EVENTS_WITH_ONSALE_DATE": (float(count_events_with_cutoff(conn, "onsale_cutoff")), "economics.event_decision_cutoffs.onsale_cutoff"),
        "EVENTS_WITH_ANNOUNCEMENT_DATE": (float(count_events_with_cutoff(conn, "announcement_cutoff")), "economics.event_decision_cutoffs.announcement_cutoff"),
        "EVENTS_WITH_3PLUS_PRIOR_ARTIST_RESULTS": (float(count_events_with_prior_results(conn, dimension="artist", min_prior=3)), "research.boxoffice_engagements PIT self-join (artist, >=3 priors)"),
        "EVENTS_WITH_PRIOR_MARKET_RESULT": (float(count_events_with_prior_results(conn, dimension="market", min_prior=1)), "research.boxoffice_engagements PIT self-join (market, >=1 prior)"),
        "EVENTS_WITH_PRIOR_VENUE_RESULT": (float(count_events_with_prior_results(conn, dimension="venue", min_prior=1)), "research.boxoffice_engagements PIT self-join (venue, >=1 prior)"),
        "EVENTS_WITH_TICKET_PACE": (float(count_events_with_ticket_pace(conn)), "economics.primary_ticket_snapshots (>=2 per event)"),
        "EVENTS_WITH_OFFER_OR_BOOKING_CUTOFF": (float(count_events_with_cutoff(conn, "booking_cutoff")), "economics.event_decision_cutoffs.booking_cutoff"),
        "STRICT_PIT_RECONSTRUCTABLE": (float(strict_pit_events), "flywheel.pit_reconstruction_evidence (STRICT_PIT-eligible classes)"),
        "CONSERVATIVE_BOUND_PIT_RECONSTRUCTABLE": (float(conservative_events), "flywheel.pit_reconstruction_evidence (CONSERVATIVE_BOUND_PIT-eligible classes)"),
        "STRICT_PIT_WARM_START_EVENTS": (float(strict_warm), "research.boxoffice_engagements JOIN flywheel.pit_reconstruction_evidence (artist, >=3 STRICT_PIT-valid priors)"),
        "CONSERVATIVE_BOUND_WARM_START_EVENTS": (float(conservative_warm), "research.boxoffice_engagements JOIN flywheel.pit_reconstruction_evidence (artist, >=3 priors incl. conservative bounds)"),
    }

    # Derived rates. The eligible denominator is ALWAYS CANONICAL_PERFORMANCES
    # (single-show performances), never the full engagement count — multi-show
    # aggregates are not comparable decision targets. Zero when the denominator
    # is empty, never NaN.
    denominator = canonical if canonical > 0 else 0.0
    rates: dict[str, tuple[float, str, str]] = {
        "WARM_START_RATE": (
            counts["EVENTS_WITH_3PLUS_PRIOR_ARTIST_RESULTS"][0] / denominator if denominator else 0.0,
            "EVENTS_WITH_3PLUS_PRIOR_ARTIST_RESULTS / CANONICAL_PERFORMANCES (eligible denominator: single-show performances)",
            "fraction",
        ),
        "OFFER_TIME_RECONSTRUCTABLE_RATE": (
            counts["EVENTS_WITH_OFFER_OR_BOOKING_CUTOFF"][0] / denominator if denominator else 0.0,
            "EVENTS_WITH_OFFER_OR_BOOKING_CUTOFF / CANONICAL_PERFORMANCES (eligible denominator: single-show performances)",
            "fraction",
        ),
        "TICKET_PACE_COVERAGE": (
            counts["EVENTS_WITH_TICKET_PACE"][0] / denominator if denominator else 0.0,
            "EVENTS_WITH_TICKET_PACE / CANONICAL_PERFORMANCES (eligible denominator: single-show performances)",
            "fraction",
        ),
        "SETTLEMENT_COVERAGE": (
            counts["FULLY_SETTLED_EVENTS"][0] / denominator if denominator else 0.0,
            "FULLY_SETTLED_EVENTS / CANONICAL_PERFORMANCES (eligible denominator: single-show performances)",
            "fraction",
        ),
    }

    rows: list[dict[str, Any]] = []
    for key, (actual, evidence) in counts.items():
        rows.append(_measurement_row(key, actual, evidence, measured_at))

    for key, (actual, evidence, unit) in rates.items():
        row = _measurement_row(key, actual, evidence, measured_at)
        row["unit"] = unit
        rows.append(row)

    rows.sort(key=lambda row: row["objective_key"])
    return rows


def _measurement_row(
    key: str, actual: float, evidence: str, measured_at: datetime
) -> dict[str, Any]:
    validate_objective_key(key)
    objective = OBJECTIVES_BY_KEY_V1[key]
    target = objective.target
    ratio = (actual / target) if target else 0.0
    if ratio >= 1.0:
        status = AT_TARGET if ratio == 1.0 else ABOVE_TARGET
    else:
        status = BELOW_TARGET
    return {
        "objective_version": OBJECTIVE_VERSION_V1,
        "measured_at": measured_at.isoformat(),
        "objective_key": key,
        "metric_name": objective.metric_name,
        "actual_value": actual,
        "target_value": target,
        "coverage_ratio": ratio,
        "unit": objective.unit,
        "status": status,
        "delta": actual - target,
        "evidence_query": evidence,
        "notes": objective.definition,
    }


def snapshot_id(measured_at: datetime, objective_key: str) -> str:
    stamp = measured_at.strftime("%Y%m%dT%H%M%S%f")
    return f"cov_{stamp}_{objective_key}"
