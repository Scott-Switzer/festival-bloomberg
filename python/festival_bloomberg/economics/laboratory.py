"""Historical Laboratory coverage and quality analytics.

Four reports, computed from the claim ledger and decision cutoffs:

1. DATA QUALITY — event/entity counts, duplicate rate, source mix,
   conflict rate, missingness, rights, knowledge-time completeness.
2. OUTCOME LABEL COVERAGE — per outcome type: events known vs unknown.
3. PIT FEATURE AVAILABILITY — per cutoff: how many claims were knowable.
4. SELECTION / MISSINGNESS — bias checks (venue/year/artist concentration,
   large/sold-out reporting bias).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .outcome_claims import (
    ATTENDANCE_TYPES,
    CAPACITY_TYPES,
    OBSERVED_PRIVATE,
    OBSERVED_PUBLIC,
    REVENUE_TYPES,
    SOLD_OUT_TYPES,
    TICKET_TYPES,
)


def data_quality_report(economics_repo, events_repo) -> dict[str, Any]:
    """Aggregate data-quality statistics across all outcome claims."""
    claims = economics_repo.query_outcome_claims()
    events = events_repo.query_events()

    total = len(claims)
    source_quality = Counter(c["source_quality"] for c in claims)
    rights = Counter(c["rights_status"] for c in claims)
    commercial = Counter(c["commercial_use_status"] for c in claims)
    obs_class = Counter(c["observation_class"] for c in claims)
    types = Counter(c["outcome_type"] for c in claims)

    conflict_groups = {
        c["conflict_group_id"] for c in claims if c.get("conflict_group_id")
    }
    conflicts = sum(
        1
        for gid in conflict_groups
        if len([c for c in claims if c.get("conflict_group_id") == gid]) > 1
    )

    with_value = sum(1 for c in claims if c["value_numeric"] is not None or c["value_text"])
    with_knowledge = sum(1 for c in claims if c["knowledge_time"])

    dup_rate = _duplicate_rate(claims)

    return {
        "claims_total": total,
        "claims_with_value": with_value,
        "claims_missing_value": total - with_value,
        "duplicate_rate": round(dup_rate, 4),
        "source_quality_distribution": dict(source_quality),
        "rights_distribution": dict(rights),
        "commercial_use_distribution": dict(commercial),
        "observation_class_distribution": dict(obs_class),
        "outcome_type_distribution": dict(types),
        "conflict_groups": len(conflict_groups),
        "conflicting_groups": conflicts,
        "knowledge_time_completeness": round(with_knowledge / total, 4) if total else 0.0,
        "canonical_events_loaded": len(events),
    }


def outcome_coverage_report(economics_repo, events_repo) -> dict[str, Any]:
    """Per-outcome-type coverage: how many events have a known label."""
    claims = economics_repo.query_outcome_claims()
    event_ids = {e["event_id"] for e in events_repo.query_events()}

    by_type = defaultdict(set)
    by_type_grade = defaultdict(Counter)
    for c in claims:
        by_type[c["outcome_type"]].add(c["canonical_event_id"])
        by_type_grade[c["outcome_type"]][c["source_quality"]] += 1

    report: dict[str, Any] = {}
    for outcome_type in sorted(by_type):
        events_with = by_type[outcome_type]
        report[outcome_type] = {
            "events_with_known": len(events_with),
            "events_unknown": max(0, len(event_ids) - len(events_with)),
            "coverage_pct": round(len(events_with) / len(event_ids) * 100, 2) if event_ids else 0.0,
            "source_quality_distribution": dict(by_type_grade[outcome_type]),
        }
    return report


def pit_availability_report(economics_repo) -> dict[str, Any]:
    """For each decision cutoff recorded, count claims knowable at/before it."""
    cutoffs = economics_repo.query_decision_cutoffs()
    claims = economics_repo.query_outcome_claims()

    report: dict[str, Any] = {"cutoffs": []}
    for row in cutoffs:
        entry: dict[str, Any] = {"event_id": row["canonical_event_id"]}
        for name in ("booking_cutoff", "announcement_cutoff", "onsale_cutoff", "event_cutoff"):
            value = row.get(name)
            entry[name] = value
            if not value:
                entry[f"{name}_knowable"] = None
                continue
            cutoff = _parse(value)
            knowable = 0
            if cutoff is not None:
                knowable = sum(
                    1
                    for c in claims
                    if c["canonical_event_id"] == row["canonical_event_id"]
                    and _parse(c["knowledge_time"]) is not None
                    and _parse(c["knowledge_time"]) <= cutoff
                )
            entry[f"{name}_knowable"] = knowable
        report["cutoffs"].append(entry)
    return report


def selection_bias_report(economics_repo, events_repo) -> dict[str, Any]:
    """Detect sample-selection bias in the outcome corpus."""
    claims = economics_repo.query_outcome_claims()
    events = events_repo.query_events()
    event_by_id = {e["event_id"]: e for e in events}

    events_with_claims = {
        c["canonical_event_id"] for c in claims if c.get("canonical_event_id")
    }

    venue_counter = Counter()
    year_counter = Counter()
    artist_counter = Counter()
    large_vs_small = {"with_claims": 0, "without_claims": 0}
    soldout_extra = 0
    total_with = len(events_with_claims)

    for e in events:
        eid = e["event_id"]
        venue_counter[e.get("venue_name") or "UNKNOWN"] += 1
        year = _year(e.get("local_date"))
        if year:
            year_counter[year] += 1
        artist_counter[e.get("artist_id") or "UNKNOWN"] += 1
        if eid in events_with_claims:
            large_vs_small["with_claims"] += 1

    large_vs_small["without_claims"] = max(0, len(events) - large_vs_small["with_claims"])

    # venue concentration among claimed events
    claimed_venues = Counter(
        event_by_id[eid].get("venue_name") or "UNKNOWN"
        for eid in events_with_claims
        if eid in event_by_id
    )

    sold_out_claims = sum(
        1 for c in claims if c["outcome_type"] in SOLD_OUT_TYPES
    )

    return {
        "events_total": len(events),
        "events_with_claims": total_with,
        "coverage_pct": round(total_with / len(events) * 100, 2) if events else 0.0,
        "large_vs_small": large_vs_small,
        "top_venues_by_events": dict(venue_counter.most_common(10)),
        "top_venues_by_claimed_events": dict(claimed_venues.most_common(10)),
        "events_by_year": dict(sorted(year_counter.items())),
        "distinct_artists": len(artist_counter),
        "sold_out_claims": sold_out_claims,
        "selection_bias_notes": [
            "large shows are more likely reported if venue concentration among claimed events exceeds the base distribution",
            "sold-out shows are more likely reported (see sold_out_claims vs total claims)",
        ],
    }


def _duplicate_rate(claims: list[dict[str, Any]]) -> float:
    total = len(claims)
    if total == 0:
        return 0.0
    unique = len({c["claim_id"] for c in claims})
    return 1.0 - (unique / total)


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _year(value: Any) -> str | None:
    dt = _parse(value)
    return str(dt.year) if dt else None
