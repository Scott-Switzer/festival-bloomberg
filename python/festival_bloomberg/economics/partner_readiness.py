"""Design-partner structural readiness: deterministic tiers, never a model.

The private-data flywheel already imports, validates, quarantines PII, and
builds outcome claims. This module answers the promoter-facing question:

    "How much of my history is actually usable, and for what?"

Three functions:

* :func:`structural_coverage` — counts, label coverage, PIT cutoff coverage,
  and repeat history computed from the persisted claim ledger + events.
* :func:`partner_readiness_tier` — maps that coverage to a deterministic tier.
  Row count alone NEVER advances a tier; the gates require label families and
  PIT cutoffs together.
* :func:`simulate_partner_value` — synthetic structural value curves across
  corpus sizes and scenario families. It never simulates prediction accuracy.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .laboratory import economic_coverage_report

# ---------------------------------------------------------------------------
# Readiness tiers (deterministic, conservative, documented gates)
# ---------------------------------------------------------------------------
TIER_STRUCTURAL_ONLY = "STRUCTURAL_ONLY"
TIER_RETROSPECTIVE_RESEARCH = "RETROSPECTIVE_RESEARCH_USABLE"
TIER_ECONOMICS_USABLE = "ECONOMICS_USABLE"
TIER_UNDERWRITING_RESEARCH = "UNDERWRITING_RESEARCH_CANDIDATE"

# Conservative minimums. These are floors below which a model would be fitting
# noise; they are not authoritative thresholds.
MIN_RETRO_EVENTS = 50
MIN_RETRO_ATTENDANCE = 25
MIN_RETRO_CUTOFF = 25

MIN_ECON_EVENTS = 100
MIN_ECON_ATTENDANCE = 50
MIN_ECON_TICKETS = 50
MIN_ECON_GROSS = 50
MIN_ECON_DEAL = 10

MIN_UNDERWRITE_EVENTS = 250
MIN_UNDERWRITE_ECON = 100


def structural_coverage(economics_repo, events_repo) -> dict[str, Any]:
    """Structural coverage from the persisted ledger (no prediction).

    Returns flat counts the readiness tier consumes, plus entity diversity and
    repeat rates. UNKNOWN/missing stays missing — never coerced to zero.
    """
    cov = economic_coverage_report(economics_repo, events_repo)
    events = events_repo.query_events()
    cutoffs = economics_repo.query_decision_cutoffs()

    artists = Counter()
    venues = Counter()
    markets = Counter()
    for e in events:
        a = e.get("artist_id")
        v = e.get("venue_name")
        m = e.get("market_id") or e.get("city")
        if a:
            artists[a] += 1
        if v:
            venues[str(v)] += 1
        if m:
            markets[str(m)] += 1

    events_with_booking = 0
    events_with_announcement = 0
    events_with_onsale = 0
    events_with_cutoff = 0
    for c in cutoffs:
        booking = c.get("booking_cutoff")
        announcement = c.get("announcement_cutoff")
        onsale = c.get("onsale_cutoff")
        if booking:
            events_with_booking += 1
        if announcement:
            events_with_announcement += 1
        if onsale:
            events_with_onsale += 1
        if booking or announcement or onsale or c.get("event_cutoff"):
            events_with_cutoff += 1

    return {
        "events": len(events),
        "distinct_artists": len(artists),
        "distinct_venues": len(venues),
        "distinct_markets": len(markets),
        "artist_repeats": sum(1 for n in artists.values() if n > 1),
        "venue_repeats": sum(1 for n in venues.values() if n > 1),
        "market_repeats": sum(1 for n in markets.values() if n > 1),
        "events_with_booking": events_with_booking,
        "events_with_announcement": events_with_announcement,
        "events_with_onsale": events_with_onsale,
        "events_with_cutoff": events_with_cutoff,
        "events_with_capacity": cov.get("events_with_event_capacity", 0),
        "events_with_tickets_sold": cov.get("events_with_tickets_sold", 0),
        "events_with_attendance": cov.get("events_with_attendance", 0),
        "events_with_gross": cov.get("events_with_gross", 0),
        "events_with_guarantee": cov.get("events_with_guarantee", 0),
        "events_with_settlement": cov.get("events_with_promoter_contribution", 0),
    }


def partner_readiness_tier(coverage: dict[str, Any]) -> dict[str, Any]:
    """Deterministic tier from structural coverage. Row count alone never wins."""
    events = int(coverage.get("events", 0) or 0)
    attendance = int(coverage.get("events_with_attendance", 0) or 0)
    tickets = int(coverage.get("events_with_tickets_sold", 0) or 0)
    gross = int(coverage.get("events_with_gross", 0) or 0)
    guarantee = int(coverage.get("events_with_guarantee", 0) or 0)
    settlement = int(coverage.get("events_with_settlement", 0) or 0)
    cutoff = int(coverage.get("events_with_cutoff", 0) or 0)
    distinct_artists = int(coverage.get("distinct_artists", 0) or 0)
    distinct_venues = int(coverage.get("distinct_venues", 0) or 0)

    reasons: list[str] = []
    tier = TIER_STRUCTURAL_ONLY

    if events == 0:
        reasons.append("no events imported")
    elif (
        events >= MIN_UNDERWRITE_EVENTS
        and gross >= MIN_UNDERWRITE_ECON
        and guarantee >= MIN_UNDERWRITE_ECON
        and settlement >= MIN_UNDERWRITE_ECON
        and cutoff >= MIN_UNDERWRITE_ECON
    ):
        tier = TIER_UNDERWRITING_RESEARCH
        reasons.append(
            "gross + guarantee + settlement + PIT cutoffs all above the "
            "underwriting-research floor (candidate only, not a claim of readiness)"
        )
    elif (
        events >= MIN_ECON_EVENTS
        and attendance >= MIN_ECON_ATTENDANCE
        and tickets >= MIN_ECON_TICKETS
        and gross >= MIN_ECON_GROSS
        and (guarantee >= MIN_ECON_DEAL or settlement >= MIN_ECON_DEAL)
    ):
        tier = TIER_ECONOMICS_USABLE
        reasons.append(
            "attendance + tickets + gross + deal/settlement evidence above the economics floor"
        )
    elif (
        events >= MIN_RETRO_EVENTS
        and attendance >= MIN_RETRO_ATTENDANCE
        and cutoff >= MIN_RETRO_CUTOFF
    ):
        tier = TIER_RETROSPECTIVE_RESEARCH
        reasons.append("attendance + PIT cutoffs above the retrospective baseline floor")
    else:
        reasons.append("below the retrospective baseline floor (structural only)")

    if distinct_venues < 3:
        reasons.append("venue diversity too low for generalization")
    if distinct_artists < 5:
        reasons.append("artist diversity too low for generalization")
    if events and cutoff < events // 2:
        reasons.append("PIT cutoff coverage below half of events")

    return {
        "tier": tier,
        "reasons": reasons,
        "thresholds": {
            "min_retrospective_events": MIN_RETRO_EVENTS,
            "min_retrospective_attendance": MIN_RETRO_ATTENDANCE,
            "min_retrospective_cutoff": MIN_RETRO_CUTOFF,
            "min_economics_events": MIN_ECON_EVENTS,
            "min_economics_attendance": MIN_ECON_ATTENDANCE,
            "min_economics_tickets": MIN_ECON_TICKETS,
            "min_economics_gross": MIN_ECON_GROSS,
            "min_underwriting_events": MIN_UNDERWRITE_EVENTS,
            "min_underwriting_econ": MIN_UNDERWRITE_ECON,
        },
    }


# ---------------------------------------------------------------------------
# Synthetic value simulator (structural only, no prediction accuracy)
# ---------------------------------------------------------------------------
# scenario family -> structural rates. These are illustrative; they do NOT
# represent real partners.
SCENARIO_FAMILIES: dict[str, dict[str, float]] = {
    "LOW_REPEAT_PROMOTER": {
        "artist_repeat": 0.2, "venue_repeat": 0.5, "market_repeat": 0.6,
        "cutoff": 0.5, "attendance": 0.6, "gross": 0.6, "guarantee": 0.4, "settlement": 0.5,
    },
    "REGIONAL_PROMOTER": {
        "artist_repeat": 0.4, "venue_repeat": 0.6, "market_repeat": 0.8,
        "cutoff": 0.7, "attendance": 0.7, "gross": 0.7, "guarantee": 0.6, "settlement": 0.6,
    },
    "VENUE_GROUP": {
        "artist_repeat": 0.3, "venue_repeat": 0.9, "market_repeat": 0.7,
        "cutoff": 0.6, "attendance": 0.6, "gross": 0.6, "guarantee": 0.5, "settlement": 0.5,
    },
    "MULTI_MARKET_PROMOTER": {
        "artist_repeat": 0.5, "venue_repeat": 0.5, "market_repeat": 0.5,
        "cutoff": 0.8, "attendance": 0.8, "gross": 0.8, "guarantee": 0.7, "settlement": 0.7,
    },
    "FESTIVAL_OPERATOR": {
        "artist_repeat": 0.7, "venue_repeat": 0.8, "market_repeat": 0.6,
        "cutoff": 0.9, "attendance": 0.9, "gross": 0.9, "guarantee": 0.8, "settlement": 0.8,
    },
}


def simulate_partner_value(
    sizes: tuple[int, ...] = (50, 100, 250, 500, 1000, 5000),
    families: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Structural value curves over corpus sizes and scenario families.

    Reports structural metrics and the resulting readiness tier. It NEVER
    simulates prediction accuracy — no forecast numbers are produced.
    """
    rows: list[dict[str, Any]] = []
    for family in families or tuple(SCENARIO_FAMILIES):
        rates = SCENARIO_FAMILIES[family]
        for size in sizes:
            coverage = {
                "events": size,
                "distinct_artists": max(1, int(size * 0.7)),
                "distinct_venues": max(1, int(size * (1.0 - rates["venue_repeat"]))),
                "distinct_markets": max(1, int(size * (1.0 - rates["market_repeat"]) + 1)),
                "artist_repeats": int(size * rates["artist_repeat"]),
                "venue_repeats": int(size * rates["venue_repeat"]),
                "market_repeats": int(size * rates["market_repeat"]),
                "events_with_cutoff": int(size * rates["cutoff"]),
                "events_with_attendance": int(size * rates["attendance"]),
                "events_with_tickets_sold": int(size * rates["attendance"]),
                "events_with_gross": int(size * rates["gross"]),
                "events_with_guarantee": int(size * rates["guarantee"]),
                "events_with_settlement": int(size * rates["settlement"]),
                "events_with_capacity": int(size * rates["attendance"]),
            }
            tier = partner_readiness_tier(coverage)
            rows.append({
                "family": family,
                "events": size,
                "warm_start_artist_events": coverage["artist_repeats"],
                "venue_repeats": coverage["venue_repeats"],
                "market_repeats": coverage["market_repeats"],
                "pit_cutoff_events": coverage["events_with_cutoff"],
                "gross_coverage": coverage["events_with_gross"],
                "guarantee_coverage": coverage["events_with_guarantee"],
                "settlement_coverage": coverage["events_with_settlement"],
                "readiness_tier": tier["tier"],
            })
    return rows
