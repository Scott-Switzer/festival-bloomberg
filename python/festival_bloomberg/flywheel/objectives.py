"""Data Flywheel objectives — coverage targets and decision metrics.

These are product-development targets (from the DATA_FLYWHEEL_AND_COVERAGE_V1
milestone), NOT statistically validated thresholds. They define what a
continuously growing live-event research warehouse should reach so that
comparable-event retrieval and future PIT underwriting studies are meaningful.

KPI vocabulary is deliberate and never interchangeable:

* ``OUTCOME_CLAIMS``            — source-backed claims in the ledger (one row
                                  per source assertion; conflicts coexist)
* ``UNIQUE_EVENTS_WITH_OUTCOMES`` — distinct canonical events with >= 1
                                  defensible outcome claim
* ``FULLY_SETTLED_EVENTS``      — distinct events with settlement/gross
                                  contribution evidence (P&L-adjacent)

Counting claims is NOT counting events, and neither is counting settlements.

The most important numbers are the four RATES — they measure progress toward
a real underwriting system, not raw database size:

    warm_start_rate                  share of target events with >= 3 knowable
                                     prior artist results at the target cutoff
    offer_time_reconstructable_rate  share of events with a known offer/booking
                                     cutoff (PIT validation possible)
    ticket_pace_coverage             share of events with forward ticket pace
    settlement_coverage              share of events with settlement evidence
"""

from __future__ import annotations

from dataclasses import dataclass

OBJECTIVE_VERSION_V1 = "data_flywheel_and_coverage_v1"


@dataclass(frozen=True)
class Objective:
    # ``objective_id`` (never a ``*key=`` keyword) so gitleaks' generic-api-key
    # rule never trips on dataclass assignments like ``objective_key=...``.
    objective_id: str
    metric_name: str
    definition: str
    target: float
    unit: str


#: Coverage targets + decision metrics. All targets are PROPOSED
#: product-development targets (unit = events/claims/artists/... or fraction
#: for the rate metrics).
MEDIUM_TERM_OBJECTIVES_V1: tuple[Objective, ...] = (
    # ---- core scale ------------------------------------------------------
    # Engagement vs performance is NEVER conflated. An engagement is a booking
    # (possibly a multi-show aggregate); a performance is one defensible
    # single-show/event record. Only the latter enters a metric called
    # PERFORMANCES, and every rate below names CANONICAL_PERFORMANCES as its
    # explicit eligible denominator.
    Objective(
        objective_id="CANONICAL_BOXSCORE_ENGAGEMENTS",
        metric_name="canonical_boxscore_engagements",
        definition="Canonical boxscore ENGAGEMENTS in the graph (resolved, deduplicated). Includes multi-show aggregates: an engagement is a booking, not a performance.",
        target=50_000.0,
        unit="engagements",
    ),
    Objective(
        objective_id="SINGLE_SHOW_ENGAGEMENTS",
        metric_name="single_show_engagements",
        definition="Defensible single-show engagements (multi-show aggregates excluded). The eligible population for performance-level measurement.",
        target=45_000.0,
        unit="engagements",
    ),
    Objective(
        objective_id="CANONICAL_PERFORMANCES",
        metric_name="canonical_performances",
        definition="Canonical single-performance event rows (resolved, deduplicated; multi-show aggregates are NEVER performances). Explicit eligible denominator for WARM_START_RATE / OFFER_TIME_RECONSTRUCTABLE_RATE / TICKET_PACE_COVERAGE / SETTLEMENT_COVERAGE.",
        target=50_000.0,
        unit="events",
    ),
    Objective(
        objective_id="OUTCOME_CLAIMS",
        metric_name="outcome_claims",
        definition="Source-backed single-show outcome CLAIMS in the ledger (attendance / paid tickets / gross / sell-out). Claims are not events.",
        target=5_000.0,
        unit="claims",
    ),
    Objective(
        objective_id="UNIQUE_EVENTS_WITH_OUTCOMES",
        metric_name="unique_events_with_outcomes",
        definition="Distinct canonical events with at least one defensible outcome claim.",
        target=2_500.0,
        unit="events",
    ),
    Objective(
        objective_id="FULLY_SETTLED_EVENTS",
        metric_name="fully_settled_events",
        definition="Distinct events with settlement/gross-contribution evidence (promoter contribution, settlement gross/net).",
        target=500.0,
        unit="events",
    ),
    Objective(
        objective_id="ARTISTS_WITH_3_PLUS_OUTCOMES",
        metric_name="artists_with_3_plus_outcomes",
        definition="Artists with at least three prior single-show outcome observations (warm-start comparable depth).",
        target=1_000.0,
        unit="artists",
    ),
    Objective(
        objective_id="MARKETS",
        metric_name="markets",
        definition="U.S. metros with venue/event coverage in the graph.",
        target=50.0,
        unit="metros",
    ),
    Objective(
        objective_id="CANONICAL_VENUES",
        metric_name="canonical_venues",
        definition="Venues with canonical identity and capacity/configuration evidence.",
        target=1_000.0,
        unit="venues",
    ),
    Objective(
        objective_id="CONTINUOUS_USEFUL_PERIOD",
        metric_name="continuous_useful_period_years",
        definition="Continuous useful period with dense outcome coverage (2018 onward).",
        target=8.0,
        unit="years",
    ),
    Objective(
        objective_id="FORWARD_TRACKED_FUTURE_EVENTS",
        metric_name="forward_tracked_future_events",
        definition="Future events on the forward watchlist with preserved time-sensitive observations.",
        target=2_000.0,
        unit="events",
    ),
    Objective(
        objective_id="PRIVATE_EVENTS_WITH_SETTLEMENT_EVIDENCE",
        metric_name="private_events_with_settlement_evidence",
        definition="Distinct events with OBSERVED_PRIVATE SETTLEMENT-TYPE evidence (PROMOTER_CONTRIBUTION / SETTLEMENT_GROSS / SETTLEMENT_NET). An attendance-only private import is NOT settlement evidence; this is not full settlement completeness.",
        target=500.0,
        unit="events",
    ),
    # ---- per-dimension outcome coverage (events with the dimension) ------
    Objective(
        objective_id="EVENTS_WITH_ATTENDANCE",
        metric_name="events_with_attendance",
        definition="Distinct events with an attendance claim (reported/paid/scanned).",
        target=2_500.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_PAID_TICKETS",
        metric_name="events_with_paid_tickets",
        definition="Distinct events with a paid-tickets / tickets-sold claim.",
        target=1_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_GROSS",
        metric_name="events_with_gross",
        definition="Distinct events with a ticket-gross claim.",
        target=3_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_SELLOUT",
        metric_name="events_with_sellout",
        definition="Distinct events with an explicit sold-out / not-sold-out assertion.",
        target=1_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_CAPACITY",
        metric_name="events_with_capacity",
        definition="Distinct events with a capacity claim (venue/usable/permit).",
        target=1_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_ONSALE_DATE",
        metric_name="events_with_onsale_date",
        definition="Distinct events with a known public onsale date.",
        target=2_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_ANNOUNCEMENT_DATE",
        metric_name="events_with_announcement_date",
        definition="Distinct events with a known announcement date.",
        target=2_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_3PLUS_PRIOR_ARTIST_RESULTS",
        metric_name="events_with_3plus_prior_artist_results",
        definition="Distinct events with >= 3 same-artist results published before the event (PIT knowable).",
        target=1_500.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_PRIOR_MARKET_RESULT",
        metric_name="events_with_prior_market_result",
        definition="Distinct events with >= 1 same-market result published before the event (PIT knowable).",
        target=2_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_PRIOR_VENUE_RESULT",
        metric_name="events_with_prior_venue_result",
        definition="Distinct events with >= 1 same-venue result published before the event (PIT knowable).",
        target=1_500.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_TICKET_PACE",
        metric_name="events_with_ticket_pace",
        definition="Distinct events with >= 2 forward ticket snapshots (pace evidence).",
        target=2_000.0,
        unit="events",
    ),
    Objective(
        objective_id="EVENTS_WITH_OFFER_OR_BOOKING_CUTOFF",
        metric_name="events_with_offer_or_booking_cutoff",
        definition="Distinct events with a known offer/booking cutoff (PIT validation possible).",
        target=2_000.0,
        unit="events",
    ),
    # ---- rates (fraction of canonical performances) ----------------------
    Objective(
        objective_id="WARM_START_RATE",
        metric_name="warm_start_rate",
        definition="Eligible denominator CANONICAL_PERFORMANCES (single-show). Numerator: events with >= 3 knowable prior artist results at cutoff.",
        target=0.5,
        unit="fraction",
    ),
    Objective(
        objective_id="OFFER_TIME_RECONSTRUCTABLE_RATE",
        metric_name="offer_time_reconstructable_rate",
        definition="Eligible denominator CANONICAL_PERFORMANCES (single-show). Numerator: events with a known offer/booking cutoff.",
        target=0.8,
        unit="fraction",
    ),
    Objective(
        objective_id="TICKET_PACE_COVERAGE",
        metric_name="ticket_pace_coverage",
        definition="Eligible denominator CANONICAL_PERFORMANCES (single-show). Numerator: events with forward ticket pace.",
        target=0.6,
        unit="fraction",
    ),
    Objective(
        objective_id="SETTLEMENT_COVERAGE",
        metric_name="settlement_coverage",
        definition="Eligible denominator CANONICAL_PERFORMANCES (single-show). Numerator: events with settlement evidence.",
        target=0.5,
        unit="fraction",
    ),
)

OBJECTIVES_BY_KEY_V1 = {obj.objective_id: obj for obj in MEDIUM_TERM_OBJECTIVES_V1}


def objective_rows(*, objective_version: str = OBJECTIVE_VERSION_V1) -> list[dict]:
    """Return the objective rows ready for ``flywheel.objectives`` insert."""
    return [
        {
            "objective_key": obj.objective_id,
            "objective_version": objective_version,
            "metric_name": obj.metric_name,
            "metric_definition": obj.definition,
            "medium_term_target": obj.target,
            "unit": obj.unit,
        }
        for obj in MEDIUM_TERM_OBJECTIVES_V1
    ]


def validate_objective_key(key: str) -> str:
    """Return the key or raise if it is not a registered objective."""
    if key not in OBJECTIVES_BY_KEY_V1:
        raise ValueError(f"objective_key {key!r} is not registered in {OBJECTIVE_VERSION_V1}")
    return key
