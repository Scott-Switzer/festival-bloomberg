"""PRE_EVENT_CUTOFF_ACQUISITION_V1 — live operational acceptance.

This milestone answers ONE question:

    What was actually knowable BEFORE a promoter decided to book the show?

It derives decision-time cutoff evidence from REAL persisted data (never
fabricated) and measures warm-start coverage at each decision point:

    * EVENT_DATE          — the scheduled show date (known for the corpus).
    * RESULT_PUBLICATION  — when the result became public (from PIT evidence).
    * ANNOUNCEMENT        — forward events only: first-seen upper bound.
    * BOOKING_OR_OFFER    — forward events only: booking <= announcement bound.
    * PRESALE / ONSALE    — honest UNKNOWN unless a source exposes them.

Nothing here hammers throttled providers or invents timestamps. Historical
announcement/onsale/booking cutoffs remain UNKNOWN because the public corpus
carries result-publication dates, not pre-event decision dates; that is the
binding data bottleneck and it is reported, not hidden.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..acquisition.contracts import utc_now
from ..flywheel.acquisition_accounting import (
    build_acquisition_run_row,
    derive_metrics,
)
from ..flywheel.cutoffs import (
    CUTOFF_ANNOUNCEMENT,
    CUTOFF_BOOKING_OR_OFFER,
    CUTOFF_EVENT_DATE,
    CUTOFF_GENERAL_ONSALE,
    CUTOFF_PRESALE,
    CUTOFF_RESULT_PUBLICATION,
    CONSERVATIVE_BOUND_PIT,
    STRICT_PIT,
    decision_time_coverage,
    derive_event_date_cutoff,
    derive_forward_announcement_and_booking_bounds,
    derive_result_publication_cutoff,
    prior_outcome_distribution,
    reconstruction_candidates,
)
from ..flywheel.repository import FlywheelRepository
from ..localenv import load_local_env
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "pre_event_cutoff_acquisition_v1"


def run_pre_event_cutoff_oa(
    *,
    research_db: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/pre_event_cutoff_acquisition_v1.json",
) -> dict[str, Any]:
    """Run the bounded pre-event-cutoff OA. Pure warehouse derivation from
    persisted evidence; no live HTTP, no fabricated timestamps."""
    load_local_env()
    started = utc_now()
    oa_run_id = f"pre_event_cutoff_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(research_db)
    try:
        flywheel = FlywheelRepository(repo.conn)
        research = ResearchRepository(repo.conn)

        # -------------------------------------------------------------
        # 0. Read persisted inputs (never fabricated)
        # -------------------------------------------------------------
        engagements = research.query_engagements(is_reported=True)
        single_show = [
            e for e in engagements if not e.get("is_multi_show") and e.get("start_date")
        ]
        pit_rows = flywheel.query_pit_evidence()
        forward_events = flywheel.query_forward_events()

        # -------------------------------------------------------------
        # 1. Derive + persist decision-time cutoffs (append-only)
        # -------------------------------------------------------------
        inserted_event_date = 0
        inserted_result_publication = 0
        inserted_forward_announcement = 0
        inserted_forward_booking = 0

        for eng in single_show:
            row = derive_event_date_cutoff(eng)
            if row and flywheel.insert_pre_event_cutoff(row):
                inserted_event_date += 1

        for pit_row in pit_rows:
            row = derive_result_publication_cutoff(pit_row)
            if row and flywheel.insert_pre_event_cutoff(row):
                inserted_result_publication += 1

        for fw in forward_events:
            for row in derive_forward_announcement_and_booking_bounds(fw):
                if not flywheel.insert_pre_event_cutoff(row):
                    continue
                if row["cutoff_type"] == CUTOFF_ANNOUNCEMENT:
                    inserted_forward_announcement += 1
                elif row["cutoff_type"] == CUTOFF_BOOKING_OR_OFFER:
                    inserted_forward_booking += 1

        # NEW decision-useful cutoffs = genuinely new pre-event evidence only.
        # EVENT_DATE and RESULT_PUBLICATION re-express facts the corpus already
        # knew (the scheduled date and the persisted result-publication
        # evidence); they are derived bookkeeping, not new acquisition. The
        # forward ANNOUNCEMENT/BOOKING first-seen bounds ARE new pre-event
        # evidence that did not exist in the decision-time ledger before.
        new_cutoffs = inserted_forward_announcement + inserted_forward_booking

        # -------------------------------------------------------------
        # 2. Warm-start by cutoff (the central acceptance metric)
        # -------------------------------------------------------------
        warm_start: dict[str, Any] = {}
        for cutoff_type in (
            CUTOFF_BOOKING_OR_OFFER,
            CUTOFF_ANNOUNCEMENT,
            CUTOFF_GENERAL_ONSALE,
            CUTOFF_EVENT_DATE,
            CUTOFF_RESULT_PUBLICATION,
        ):
            warm_start[cutoff_type] = {
                "artist": {
                    "strict": prior_outcome_distribution(
                        repo.conn, cutoff_type=cutoff_type, dimension="artist", mode=STRICT_PIT
                    ),
                    "conservative": prior_outcome_distribution(
                        repo.conn, cutoff_type=cutoff_type, dimension="artist", mode=CONSERVATIVE_BOUND_PIT
                    ),
                }
            }
        # Venue/market priors at EVENT_DATE (the one cutoff that exists for the
        # historical corpus).
        event_date_extras = {
            "venue": prior_outcome_distribution(
                repo.conn, cutoff_type=CUTOFF_EVENT_DATE, dimension="venue", mode=STRICT_PIT
            ),
            "market": prior_outcome_distribution(
                repo.conn, cutoff_type=CUTOFF_EVENT_DATE, dimension="market", mode=STRICT_PIT
            ),
        }

        # -------------------------------------------------------------
        # 3. Decision-time coverage + historical cutoff matrix
        # -------------------------------------------------------------
        coverage = decision_time_coverage(repo.conn)
        matrix = _cutoff_matrix_summary(repo.conn, single_show)
        candidates = reconstruction_candidates(single_show)

        # -------------------------------------------------------------
        # 4. Acquisition accounting (honest: pure derivation, no HTTP)
        # -------------------------------------------------------------
        run = build_acquisition_run_row(
            provider="pre_event_cutoff_derivation",
            pipeline="PRE_EVENT_CUTOFFS",
            started_at=started,
            http_requests=None,  # pure warehouse derivation, no HTTP
            records_parsed=len(single_show) + len(pit_rows) + len(forward_events),
            new_cutoffs=new_cutoffs,
            monetary_cost_usd=0.0,
            detail=(
                "derived EVENT_DATE + RESULT_PUBLICATION cutoffs from the "
                "research corpus and ANNOUNCEMENT/BOOKING first-seen bounds from "
                "the forward watchlist; no live HTTP, no fabricated timestamps"
            ),
        )
        flywheel.insert_acquisition_run(run)
        flywheel.insert_acquisition_metrics(derive_metrics(run))

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "research_db": research_db,
            "cutoffs_inserted": {
                "event_date_derived": inserted_event_date,
                "result_publication_derived": inserted_result_publication,
                "forward_announcement_bound": inserted_forward_announcement,
                "forward_booking_bound": inserted_forward_booking,
                "new_decision_useful_cutoffs": new_cutoffs,
                "note": (
                    "event_date/result_publication re-express facts the corpus "
                    "already knew; forward announcement/booking bounds are the "
                    "genuinely NEW pre-event cutoff evidence."
                ),
            },
            "warm_start_by_cutoff": warm_start,
            "event_date_venue_market_priors": event_date_extras,
            "decision_time_coverage": coverage,
            "historical_cutoff_matrix_summary": matrix,
            "reconstruction_candidates": {
                "single_show_candidates": len(candidates),
                "note": (
                    "EVENT-DIRECTED reconstruction candidates generated for "
                    "announcement/onsale lookups; NO retrieval performed this "
                    "run (CDX throttled, keyed providers unset)."
                ),
            },
            "comparable_engine_readiness": _readiness_gate(warm_start, coverage),
            "provider_cost_usd": 0.0,
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


def _cutoff_matrix_summary(conn, single_show: list[dict[str, Any]]) -> dict[str, Any]:
    """EVENT x CUTOFF matrix summary over the single-show historical universe.

    Statuses: EXACT (observed instant), UPPER_BOUND (bound-only), INTERVAL
    (lower+upper), UNKNOWN (no evidence row). Unknown is reported, not zeroed.
    """
    from ..flywheel.cutoffs import event_key_from_engagement

    universe = {event_key_from_engagement(e): e["engagement_id"] for e in single_show}
    rows = conn.execute(
        "SELECT canonical_event_id, cutoff_type, cutoff_kind, cutoff_timestamp, "
        "lower_bound, upper_bound FROM flywheel.pre_event_cutoff_evidence"
    ).fetchall()

    status_by_type: dict[str, dict[str, int]] = {}
    for cutoff_type in (
        CUTOFF_BOOKING_OR_OFFER,
        CUTOFF_ANNOUNCEMENT,
        CUTOFF_PRESALE,
        CUTOFF_GENERAL_ONSALE,
        CUTOFF_EVENT_DATE,
        CUTOFF_RESULT_PUBLICATION,
    ):
        status_by_type[cutoff_type] = {"EXACT": 0, "UPPER_BOUND": 0, "INTERVAL": 0}

    covered: dict[str, set[str]] = {}
    for canonical, cutoff_type, kind, ts, lo, hi in rows:
        if canonical not in universe:
            continue
        status_by_type.setdefault(cutoff_type, {"EXACT": 0, "UPPER_BOUND": 0, "INTERVAL": 0})
        if ts:
            status_by_type[cutoff_type]["EXACT"] += 1
        elif lo and hi:
            status_by_type[cutoff_type]["INTERVAL"] += 1
        else:
            status_by_type[cutoff_type]["UPPER_BOUND"] += 1
        covered.setdefault(cutoff_type, set()).add(canonical)

    total = len(universe)
    summary = {}
    for cutoff_type, statuses in status_by_type.items():
        covered_count = len(covered.get(cutoff_type, set()))
        summary[cutoff_type] = {
            **statuses,
            "UNKNOWN": total - covered_count,
            "total_universe": total,
        }
    return summary


def _readiness_gate(warm_start: dict[str, Any], coverage: dict[str, Any]) -> dict[str, str]:
    """Can we evaluate comparable retrieval at each decision point?

    READY / PARTIAL / NOT_READY with the usable target-event N. This is a
    data-readiness statement, not a claim that the corpus is complete.
    """

    def prior_targets(cutoff_type: str) -> int:
        node = warm_start.get(cutoff_type, {}).get("artist", {})
        return (node.get("conservative") or {}).get("targets_with_known_cutoff", 0)

    def gate(n: int) -> str:
        if n >= 50:
            return "READY"
        if n > 0:
            return "PARTIAL"
        return "NOT_READY"

    result_publication_n = (warm_start.get(CUTOFF_RESULT_PUBLICATION, {})
                            .get("artist", {}).get("strict", {})
                            .get("targets_with_known_cutoff", 0))
    return {
        "EVENT_DATE": {
            "status": gate(prior_targets(CUTOFF_EVENT_DATE)),
            "usable_target_events": prior_targets(CUTOFF_EVENT_DATE),
        },
        "ONSALE": {
            "status": gate(prior_targets(CUTOFF_GENERAL_ONSALE)),
            "usable_target_events": prior_targets(CUTOFF_GENERAL_ONSALE),
        },
        "ANNOUNCEMENT": {
            "status": gate(prior_targets(CUTOFF_ANNOUNCEMENT)),
            "usable_target_events": prior_targets(CUTOFF_ANNOUNCEMENT),
        },
        "BOOKING_OR_OFFER": {
            "status": gate(prior_targets(CUTOFF_BOOKING_OR_OFFER)),
            "usable_target_events": prior_targets(CUTOFF_BOOKING_OR_OFFER),
        },
        "RESULT_PUBLICATION": {
            "status": gate(result_publication_n),
            "usable_target_events": result_publication_n,
        },
    }


if __name__ == "__main__":
    result = run_pre_event_cutoff_oa()
    print(json.dumps(result, indent=2, default=str))
