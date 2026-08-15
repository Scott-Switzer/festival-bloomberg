"""Acquisition priority graph — value-of-information acquisition.

The prior milestone's uniform "hunt every event equally" instinct is wrong.
Recovering one event's cutoff can be worthless or can unlock three downstream
PIT-comparable targets, depending on the dependency graph. This module models
that graph from the persisted warehouse and produces a documented lexicographic
acquisition ordering — never an opaque 0–100 score.

Dependency model:

    Target event T (single-show, headcount, dated) is warm-start-eligible when
    >= min_prior same-artist PRIOR outcomes were actually knowable before T's
    decision cutoff. A prior P "knows" into T only when:
        P.start_date < T.start_date          (chronologically prior)
        P has a headcount                     (an outcome exists)
        P's result publication < T's cutoff   (knowable in time)

    So the missing facts that unlock research are of two kinds:
      (a) T's decision cutoff (ANNOUNCEMENT / GENERAL_ONSALE / BOOKING) —
          without it, T's warm-start is UNKNOWN;
      (b) P's result publication being pre-cutoff rather than retrospective —
          the current corpus's result-publication evidence is a single
          retrospective batch, so even a recovered cutoff yields zero known
          priors until result-publication dates are temporally ordered.

The priority tuple is a DOCUMENTED lexicographic ordering:

    (unlock_count,          # downstream same-artist targets this recovery advances
     repeat_frequency,      # how often the artist appears (repeated first)
     has_known_outcome,     # 1 when an economic outcome is already known
     source_path_count,     # 1 when a candidate source URL exists (cheap path)
     event_date)            # earliest first (earliest unlocks the most downstream)

sorted descending on the first four, ascending on event_date.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .cutoffs import (
    CUTOFF_ANNOUNCEMENT,
    CUTOFF_BOOKING_OR_OFFER,
    CUTOFF_GENERAL_ONSALE,
    CUTOFF_PRESALE,
)
from .pit import event_key_from_engagement

#: Cutoff types whose absence makes a target UNKNOWN for warm-start.
DECISION_CUTOFF_TYPES = frozenset(
    {
        CUTOFF_BOOKING_OR_OFFER,
        CUTOFF_ANNOUNCEMENT,
        CUTOFF_PRESALE,
        CUTOFF_GENERAL_ONSALE,
    }
)


def _single_show_outcome_events(conn) -> list[dict[str, Any]]:
    """Single-show, reported, dated, headcount-bearing events (the prior/target
    population). Multi-show aggregates are never outcomes and never priors."""
    rows = conn.execute(
        "SELECT engagement_id, artist, venue, market, start_date, headcount_total, source_url "
        "FROM research.boxoffice_engagements "
        "WHERE is_reported = TRUE "
        "  AND (is_multi_show IS NULL OR is_multi_show = FALSE) "
        "  AND headcount_total IS NOT NULL "
        "  AND start_date IS NOT NULL"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "engagement_id": r[0],
                "artist": r[1],
                "venue": r[2],
                "market": r[3],
                "start_date": r[4],
                "headcount_total": r[5],
                "source_url": r[6],
                "event_key": event_key_from_engagement(
                    {"artist": r[1], "venue": r[2], "market": r[3], "start_date": r[4]}
                ),
            }
        )
    return out


def _decision_cutoff_status(conn) -> dict[str, set[str]]:
    """event_key -> set of decision cutoff types with >= 1 evidence row."""
    rows = conn.execute(
        "SELECT canonical_event_id, cutoff_type FROM flywheel.pre_event_cutoff_evidence "
        "WHERE cutoff_type IN (?, ?, ?, ?)",
        [
            CUTOFF_BOOKING_OR_OFFER,
            CUTOFF_ANNOUNCEMENT,
            CUTOFF_PRESALE,
            CUTOFF_GENERAL_ONSALE,
        ],
    ).fetchall()
    status: dict[str, set[str]] = {}
    for event_key, cutoff_type in rows:
        status.setdefault(str(event_key), set()).add(str(cutoff_type))
    return status


def _result_availability(conn) -> dict[str, str | None]:
    """event_key -> earliest result-publication availability timestamp."""
    rows = conn.execute(
        "SELECT canonical_event_id, MIN(CASE "
        "  WHEN evidence_class IN ('OBSERVED_EXACT','OBSERVED_DAY','OBSERVED_MONTH') "
        "    THEN source_publication_time "
        "  WHEN evidence_class = 'ARCHIVE_CAPTURE_UPPER_BOUND' THEN archive_capture_time "
        "  WHEN evidence_class = 'SOURCE_PERIOD_BOUND' THEN source_period_end "
        "END) FROM flywheel.pit_reconstruction_evidence "
        "GROUP BY canonical_event_id"
    ).fetchall()
    return {str(r[0]): (r[1].isoformat() if r[1] else None) for r in rows if r[0]}


def build_warm_start_dependency_graph(
    conn, *, min_prior: int = 3, dimension: str = "artist"
) -> dict[str, Any]:
    """Compute the warm-start dependency graph from the persisted warehouse.

    Returns per-target counts (potential priors, known priors, downstream
    targets, missing decision cutoffs) plus a graph summary. Pure read.
    """
    events = _single_show_outcome_events(conn)
    cutoff_status = _decision_cutoff_status(conn)
    availability = _result_availability(conn)

    # Group by dimension value for prior lookup.
    dim_of = {"artist": "artist", "venue": "venue", "market": "market"}[dimension]
    by_dim: dict[Any, list[dict[str, Any]]] = {}
    for e in events:
        by_dim.setdefault(e[dim_of], []).append(e)

    targets: list[dict[str, Any]] = []
    for t in events:
        prior_pool = [p for p in by_dim.get(t[dim_of], []) if p["start_date"] < t["start_date"]]
        known = 0
        for p in prior_pool:
            avail = availability.get(p["event_key"])
            if avail is not None and avail < _date_iso(t["start_date"]):
                known += 1
        missing_cutoffs = sorted(DECISION_CUTOFF_TYPES - cutoff_status.get(t["event_key"], set()))
        downstream = sum(
            1 for d in by_dim.get(t[dim_of], []) if d["start_date"] > t["start_date"]
        )
        targets.append(
            {
                "event_key": t["event_key"],
                "engagement_id": t["engagement_id"],
                "artist": t["artist"],
                "venue": t["venue"],
                "market": t["market"],
                "start_date": _date_iso(t["start_date"]),
                "potential_priors": len(prior_pool),
                "known_priors": known,
                "warm_start_locked": len(prior_pool) >= min_prior and known < min_prior,
                "missing_decision_cutoffs": missing_cutoffs,
                "downstream_targets": downstream,
                "has_source_url": bool(t["source_url"]),
                "has_known_outcome": t["headcount_total"] is not None,
            }
        )
    return {
        "dimension": dimension,
        "min_prior": min_prior,
        "targets_total": len(targets),
        "warm_start_locked": sum(1 for t in targets if t["warm_start_locked"]),
        "targets_with_all_decision_cutoffs": sum(
            1 for t in targets if not t["missing_decision_cutoffs"]
        ),
        "targets": targets,
    }


def acquisition_priority(
    conn, *, min_prior: int = 3, dimension: str = "artist", limit: int | None = None
) -> list[dict[str, Any]]:
    """Rank acquisition targets by documented lexicographic priority.

    Value-of-information ordering (never an opaque score):
      unlock_count desc (downstream targets advanced by recovering this event),
      repeat_frequency desc (repeated artists/venues/markets first),
      has_known_outcome desc, source_path_count desc, event_date asc.
    """
    graph = build_warm_start_dependency_graph(conn, min_prior=min_prior, dimension=dimension)
    # repeat frequency = how many same-dimension events exist in total.
    repeat = _repeat_frequency(conn, dimension=dimension)
    ranked = []
    for t in graph["targets"]:
        ranked.append(
            {
                **t,
                "repeat_frequency": repeat.get(t[dimension], 0),
                "priority_tuple": (
                    t["downstream_targets"],
                    repeat.get(t[dimension], 0),
                    1 if t["has_known_outcome"] else 0,
                    1 if t["has_source_url"] else 0,
                    t["start_date"],
                ),
            }
        )
    ranked.sort(
        key=lambda t: (
            -t["priority_tuple"][0],
            -t["priority_tuple"][1],
            -t["priority_tuple"][2],
            -t["priority_tuple"][3],
            t["priority_tuple"][4],
        )
    )
    for i, t in enumerate(ranked, start=1):
        t["rank"] = i
    if limit is not None:
        return ranked[:limit]
    return ranked


def _repeat_frequency(conn, *, dimension: str) -> dict[Any, int]:
    col = {"artist": "artist", "venue": "venue", "market": "market"}[dimension]
    rows = conn.execute(
        f"SELECT {col}, COUNT(*) FROM research.boxoffice_engagements "
        "WHERE is_reported = TRUE "
        "  AND (is_multi_show IS NULL OR is_multi_show = FALSE) "
        "  AND headcount_total IS NOT NULL "
        f"GROUP BY {col}"
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows if r[0]}


def _date_iso(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]
