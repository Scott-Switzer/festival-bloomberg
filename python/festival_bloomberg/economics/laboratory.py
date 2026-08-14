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


CORE_ECONOMIC_TYPES = (
    "PAID_ATTENDANCE",
    "SCANNED_ATTENDANCE",
    "REPORTED_ATTENDANCE",
    "TICKETS_SOLD",
    "EXPLICIT_SOLD_OUT_ASSERTION",
    "EVENT_USABLE_CAPACITY",
    "PRIMARY_FACE_VALUE_MIN",
    "PRIMARY_FACE_VALUE_MAX",
    "TICKET_GROSS",
    "ARTIST_GUARANTEE",
    "PROMOTER_CONTRIBUTION",
)


def economic_coverage_report(economics_repo, events_repo) -> dict[str, Any]:
    """Economic outcome coverage V2: per-type coverage with quality, rights,
    and conflict breakdowns, plus the research-vs-commercial split."""
    claims = economics_repo.query_outcome_claims()
    event_ids = {e["event_id"] for e in events_repo.query_events()}

    by_type = defaultdict(lambda: {"events": set(), "grade": Counter(), "rights": Counter(), "conflicts": 0})
    for c in claims:
        bucket = by_type[c["outcome_type"]]
        bucket["events"].add(c["canonical_event_id"])
        bucket["grade"][c["source_quality"]] += 1
        bucket["rights"][c["rights_status"]] += 1
        if c.get("conflict_group_id"):
            bucket["conflicts"] += 1

    scorecard: dict[str, Any] = {}
    for outcome_type in CORE_ECONOMIC_TYPES:
        bucket = by_type.get(outcome_type, {"events": set(), "grade": Counter(), "rights": Counter(), "conflicts": 0})
        events_with = bucket["events"]
        scorecard[outcome_type] = {
            "events_known": len(events_with),
            "events_unknown": max(0, len(event_ids) - len(events_with)),
            "coverage_pct": round(len(events_with) / len(event_ids) * 100, 2) if event_ids else 0.0,
            "a_tier": sum(v for k, v in bucket["grade"].items() if k.startswith("A_")),
            "b_tier": sum(v for k, v in bucket["grade"].items() if k.startswith("B_")),
            "c_d_tier": sum(v for k, v in bucket["grade"].items() if k.startswith(("C_", "D_"))),
            "commercially_usable": sum(v for k, v in bucket["rights"].items() if k in ("OPEN_COMMERCIAL_OK", "OPEN_WITH_ATTRIBUTION")),
            "research_only": sum(v for k, v in bucket["rights"].items() if k == "RESEARCH_ONLY"),
            "conflicts": bucket["conflicts"],
        }

    # Naive economics readiness: an event needs attendance + capacity or
    # tickets_sold + gross together.
    attendance_events = by_type.get("PAID_ATTENDANCE", {"events": set()})["events"] \
        | by_type.get("SCANNED_ATTENDANCE", {"events": set()})["events"] \
        | by_type.get("REPORTED_ATTENDANCE", {"events": set()})["events"]
    capacity_events = by_type.get("EVENT_USABLE_CAPACITY", {"events": set()})["events"] \
        | by_type.get("VENUE_CAPACITY", {"events": set()})["events"]
    tickets_events = by_type.get("TICKETS_SOLD", {"events": set()})["events"]
    gross_events = by_type.get("TICKET_GROSS", {"events": set()})["events"]

    return {
        "events_searched": len(event_ids),
        "scorecard": scorecard,
        "events_with_attendance_and_capacity": len(attendance_events & capacity_events),
        "events_with_tickets_and_gross": len(tickets_events & gross_events),
        "events_with_attendance": len(attendance_events),
        "events_with_tickets_sold": len(tickets_events),
        "events_with_sold_out": len(by_type.get("EXPLICIT_SOLD_OUT_ASSERTION", {"events": set()})["events"]),
        "events_with_gross": len(gross_events),
        "events_with_event_capacity": len(by_type.get("EVENT_USABLE_CAPACITY", {"events": set()})["events"]),
        "events_with_guarantee": len(by_type.get("ARTIST_GUARANTEE", {"events": set()})["events"]),
        "events_with_promoter_contribution": len(by_type.get("PROMOTER_CONTRIBUTION", {"events": set()})["events"]),
        "research_commercial_split": research_commercial_split(economics_repo),
    }


def research_commercial_split(economics_repo) -> dict[str, Any]:
    """Research-only vs commercial-eligible corpus sizes by rights status."""
    claims = economics_repo.query_outcome_claims()
    commercial = {
        "OPEN_COMMERCIAL_OK",
        "OPEN_WITH_ATTRIBUTION",
        "TERMS_REVIEW_REQUIRED",
    }
    research = 0
    eligible = 0
    unknown = 0
    for c in claims:
        status = c["rights_status"]
        if status in commercial:
            eligible += 1
        elif status == "RESEARCH_ONLY":
            research += 1
        else:
            unknown += 1
    return {
        "commercial_eligible_claims": eligible,
        "research_only_claims": research,
        "unknown_rights_claims": unknown,
        "commercial_eligible_events": len({c["canonical_event_id"] for c in claims if c["rights_status"] in commercial}),
        "research_only_events": len({c["canonical_event_id"] for c in claims if c["rights_status"] == "RESEARCH_ONLY"}),
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
