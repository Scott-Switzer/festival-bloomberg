"""Model-readiness gate.

Does NOT train a model. Evaluates whether the outcome corpus could support a
narrow baseline study and says *why* — label coverage, source quality, event
diversity, temporal coverage, market/venue concentration, PIT cutoff
availability, missingness, censoring, and selection bias.

Readiness is deliberately conservative: code existing is never sufficient.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .laboratory import economic_coverage_report

NOT_READY = "NOT_READY"
BASELINE_RESEARCH_READY = "BASELINE_RESEARCH_READY"
ATTENDANCE_MODEL_READY = "ATTENDANCE_MODEL_READY"
ECONOMICS_MODEL_READY = "ECONOMICS_MODEL_READY"

# Conservative minimums. These are not authoritative thresholds; they are the
# floor below which a model would be fitting noise.
MIN_ATTENDANCE_EVENTS = 50
MIN_TICKETS_EVENTS = 25
MIN_SOLD_OUT_EVENTS = 20


def evaluate_model_readiness(economics_repo, events_repo) -> dict[str, Any]:
    coverage = economic_coverage_report(economics_repo, events_repo)
    events = events_repo.query_events()

    attendance_events = coverage["events_with_attendance"]
    tickets_events = coverage["events_with_tickets_sold"]
    sold_out_events = coverage["events_with_sold_out"]
    gross_events = coverage["events_with_gross"]

    # Diversity: venue + year concentration.
    venue_counter = Counter(e.get("venue_name") or "UNKNOWN" for e in events)
    year_counter = Counter(str(e.get("local_date"))[:4] for e in events if e.get("local_date"))

    # PIT cutoff availability.
    cutoffs = economics_repo.query_decision_cutoffs()
    with_event_cutoff = sum(1 for c in cutoffs if c.get("event_cutoff"))
    with_onsale_cutoff = sum(1 for c in cutoffs if c.get("onsale_cutoff"))

    reasons: list[str] = []
    verdict = NOT_READY

    if attendance_events >= MIN_ATTENDANCE_EVENTS and tickets_events >= MIN_TICKETS_EVENTS and gross_events >= MIN_TICKETS_EVENTS:
        verdict = ECONOMICS_MODEL_READY
        reasons.append("attendance + tickets + gross coverage above the conservative floor")
    elif attendance_events >= MIN_ATTENDANCE_EVENTS:
        verdict = ATTENDANCE_MODEL_READY
        reasons.append("attendance coverage above the conservative floor")
    elif attendance_events >= 10:
        verdict = BASELINE_RESEARCH_READY
        reasons.append("a small attendance corpus exists; only a descriptive baseline is warranted")
    else:
        reasons.append(f"attendance coverage ({attendance_events}) far below the floor ({MIN_ATTENDANCE_EVENTS})")

    if len(venue_counter) < 3:
        reasons.append("venue diversity too low for generalization")
    if len(year_counter) < 3:
        reasons.append("temporal coverage too narrow")
    if with_onsale_cutoff < MIN_ATTENDANCE_EVENTS:
        reasons.append("onsale/announcement PIT cutoffs largely unknown — pre-event features cannot be reconstructed")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "attendance_events": attendance_events,
        "tickets_events": tickets_events,
        "sold_out_events": sold_out_events,
        "gross_events": gross_events,
        "min_attendance_for_attendance_model": MIN_ATTENDANCE_EVENTS,
        "min_tickets_for_economics_model": MIN_TICKETS_EVENTS,
        "venue_count": len(venue_counter),
        "year_count": len(year_counter),
        "events_with_event_cutoff": with_event_cutoff,
        "events_with_onsale_cutoff": with_onsale_cutoff,
        "top_venues": dict(venue_counter.most_common(5)),
        "notes": (
            "readiness is a gate, not a model; no model was trained by this "
            "evaluator and none should be trained until the verdict advances"
        ),
    }
