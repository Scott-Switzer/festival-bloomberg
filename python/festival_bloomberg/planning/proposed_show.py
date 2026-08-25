"""Unified proposed-show object for the Buyer Decision Workspace V2.

A proposed show is a first-class product object that organizes existing
evidence around one coherent underwriting unit:

    ARTIST x MARKET x DATE x VENUE x DEAL

Each section is assembled by calling into proven components (competitive calendar,
show economics, venue capacity, artist scorecard, comparable events) -- never by
reimplementing them.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from .competitive_calendar import calendar_for_proposed_show
from .candidates import artist_scorecard

EVIDENCE_KNOWN = "KNOWN"
EVIDENCE_ASSUMED = "ASSUMED"
EVIDENCE_UNKNOWN = "UNKNOWN"
EVIDENCE_CONFLICTING = "CONFLICTING"


def _h(material: str, n: int = 32) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:n]


def _d(value: Any) -> date | None:
    if value is None:
        return None
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def create_proposed_show(
    conn,
    *,
    project_key: str,
    artist_name: str,
    artist_key: str | None = None,
    musicbrainz_id: str | None = None,
    market: str = "",
    city: str | None = None,
    state_code: str | None = None,
    venue_key: str | None = None,
    venue_name: str | None = None,
    venue_configuration: str | None = None,
    proposed_date: str,
    deal_type: str | None = None,
    artist_guarantee: float | None = None,
    backend_percentage: float | None = None,
    backend_basis: str | None = None,
    decision_cutoff: str | None = None,
    research_cutoff: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    key = _h(f"ps::{project_key}::{artist_key or artist_name}::{market}::{proposed_date}")
    conn.execute(
        """
        INSERT INTO planning.proposed_shows
            (proposed_show_key, project_key, artist_key, artist_name, musicbrainz_id,
             market, city, state_code, venue_key, venue_name, venue_configuration,
             proposed_date, deal_type, artist_guarantee, backend_percentage,
             backend_basis, decision_cutoff, research_cutoff, notes,
             scenario_version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, now(), now())
        ON CONFLICT (proposed_show_key) DO UPDATE SET
            artist_name = excluded.artist_name,
            musicbrainz_id = excluded.musicbrainz_id,
            venue_key = excluded.venue_key,
            venue_name = excluded.venue_name,
            venue_configuration = excluded.venue_configuration,
            deal_type = excluded.deal_type,
            artist_guarantee = excluded.artist_guarantee,
            backend_percentage = excluded.backend_percentage,
            backend_basis = excluded.backend_basis,
            decision_cutoff = excluded.decision_cutoff,
            research_cutoff = excluded.research_cutoff,
            notes = excluded.notes,
            scenario_version = scenario_version + 1,
            updated_at = now()
        """,
        [key, project_key, artist_key, artist_name, musicbrainz_id, market, city,
         state_code, venue_key, venue_name, venue_configuration, proposed_date,
         deal_type, artist_guarantee, backend_percentage, backend_basis,
         decision_cutoff, research_cutoff, notes],
    )
    return get_proposed_show(conn, key)


def list_proposed_shows(conn, project_key: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT * FROM planning.proposed_shows WHERE project_key = ? ORDER BY proposed_date, artist_name",
        [project_key],
    )


def get_proposed_show(conn, proposed_show_key: str) -> dict[str, Any] | None:
    rows = _rows(
        conn, "SELECT * FROM planning.proposed_shows WHERE proposed_show_key = ?",
        [proposed_show_key],
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Evidence classification
# ---------------------------------------------------------------------------
def _classify(value: Any, provenance: str) -> str:
    """Classify a field value + its provenance into one evidence status."""
    if provenance in ("UNKNOWN", None) or value is None:
        return EVIDENCE_UNKNOWN
    if provenance == "USER_ASSUMPTION":
        return EVIDENCE_ASSUMED
    if provenance in ("OBSERVED_PUBLIC", "OBSERVED_PRIVATE", "DERIVED"):
        return EVIDENCE_KNOWN
    return EVIDENCE_UNKNOWN


# ---------------------------------------------------------------------------
# Buyer decision view (the Bloomberg-style dense page)
# ---------------------------------------------------------------------------
def buyer_decision_view(
    serving_conn,
    workspace_conn,
    *,
    proposed_show_key: str,
) -> dict[str, Any]:
    """Assemble the full buyer decision view for one proposed show.

    Calls into existing components; never reimplements their logic.
    """
    show = get_proposed_show(workspace_conn, proposed_show_key)
    if show is None:
        return {"status": "NOT_FOUND", "proposed_show_key": proposed_show_key}

    artist_name = show.get("artist_name") or ""
    artist_key = show.get("artist_key")
    venue_name = show.get("venue_name")
    venue_key = show.get("venue_key")
    proposed_date = show.get("proposed_date")
    city = show.get("city")
    market = show.get("market")
    research_cutoff = str(show["research_cutoff"])[:19] if show.get("research_cutoff") else None

    view: dict[str, Any] = {
        "status": "OBSERVED",
        "proposed_show_key": proposed_show_key,
        # ---- 1. SHOW HEADER -----------------------------------------------
        "header": {
            "artist_name": artist_name,
            "artist_key": artist_key,
            "market": market,
            "city": city,
            "venue_name": venue_name,
            "venue_key": venue_key,
            "venue_configuration": show.get("venue_configuration"),
            "proposed_date": str(proposed_date)[:10] if proposed_date else None,
            "deal_type": show.get("deal_type"),
            "artist_guarantee": show.get("artist_guarantee"),
            "decision_cutoff": str(show["decision_cutoff"])[:19] if show.get("decision_cutoff") else None,
            "research_cutoff": research_cutoff,
            "scenario_version": show.get("scenario_version"),
        },
        # ---- 2. EVIDENCE STATUS -------------------------------------------
        "evidence_status": {
            EVIDENCE_KNOWN: [],
            EVIDENCE_ASSUMED: [],
            EVIDENCE_UNKNOWN: [],
            EVIDENCE_CONFLICTING: [],
        },
        # ---- 3. VENUE / CAPACITY ------------------------------------------
        "venue_capacity": _venue_section(serving_conn, venue_key, show.get("venue_configuration")),
        # ---- 4. COMPETITIVE CALENDAR --------------------------------------
        "competitive_calendar": _calendar_section(serving_conn, show, research_cutoff),
        # ---- 5. COMPARABLE EVENTS -----------------------------------------
        "comparable_events": _comparable_section(serving_conn, artist_name),
        # ---- 6. ARTIST / ATTENTION CONTEXT --------------------------------
        "artist_context": _artist_section(serving_conn, artist_key, artist_name),
        # ---- 7. SHOW ECONOMICS --------------------------------------------
        "show_economics": _economics_section(workspace_conn, proposed_show_key),
        # ---- 8. RISKS / WARNINGS ------------------------------------------
        "risks": [],
        # ---- 9. PROVENANCE ------------------------------------------------
        "provenance": _provenance_section(serving_conn, workspace_conn, show),
    }

    # Derive risks from evidence gaps and conflicts.
    view["risks"] = _derive_risks(view)
    # Populate evidence status.
    view["evidence_status"] = _build_evidence_status(view)

    return view


# ---------------------------------------------------------------------------
# Section assemblers
# ---------------------------------------------------------------------------
def _venue_section(conn, venue_key: str | None, configuration: str | None) -> dict[str, Any]:
    """Assemble venue/capacity evidence from the existing capacity module."""
    if not venue_key:
        return {"status": "UNKNOWN", "reason": "no venue key provided"}
    try:
        from ..economics.capacity import assess_venue_claims
        claims = assess_venue_claims(conn, venue_key=venue_key)
    except Exception:
        claims = {"status": "UNKNOWN", "venue_key": venue_key, "claims": []}
    # Filter for relevant configuration if specified.
    relevant_claims = claims.get("claims", [])
    safe_count = 0
    conflicting_count = 0
    for c in relevant_claims:
        if c.get("evidence_class") == "PREFILL_SAFE":
            safe_count += 1
        if c.get("disposition") == "CONFLICTING":
            conflicting_count += 1
    return {
        "status": claims.get("status", "UNKNOWN"),
        "venue_key": venue_key,
        "configuration": configuration,
        "claims_total": len(relevant_claims),
        "safe_prefill_count": safe_count,
        "conflicting_count": conflicting_count,
        "safe_claims": [c for c in relevant_claims if c.get("evidence_class") == "PREFILL_SAFE"],
        "all_claims": relevant_claims,
        "review_required": conflicting_count > 0,
    }


def _calendar_section(conn, show: dict[str, Any], research_cutoff: str | None) -> dict[str, Any]:
    """Reuse competitive_calendar from PR #43."""
    proposed_date = show.get("proposed_date")
    city = show.get("city")
    venue_id = show.get("venue_key")
    if not proposed_date:
        return {"status": "UNKNOWN", "reason": "no proposed date"}
    try:
        return calendar_for_proposed_show(
            conn,
            city=city,
            date=str(proposed_date)[:10] if isinstance(proposed_date, date) else str(proposed_date),
            venue_id=venue_id,
            research_cutoff=research_cutoff,
        )
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}


def _comparable_section(conn, artist_name: str) -> dict[str, Any]:
    """Comparable-event context using the existing comparable engine."""
    if not artist_name:
        return {"status": "UNKNOWN", "reason": "no artist name"}
    try:
        card = artist_scorecard(conn, artist_name=artist_name)
        comparables = card.get("comparables", {})
        market_history = card.get("market_history", {})
        return {
            "status": "OBSERVED",
            "gross": comparables.get("gross", {}),
            "attendance": comparables.get("attendance", {}),
            "market_history": market_history,
            "source": "boxoffice_research_corpus_v1 + artist_scorecard",
        }
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}


def _artist_section(conn, artist_key: str | None, artist_name: str) -> dict[str, Any]:
    """Artist identity + attention context using existing scorecard."""
    if not artist_name:
        return {"status": "UNKNOWN", "reason": "no artist name"}
    try:
        card = artist_scorecard(conn, artist_key=artist_key, artist_name=artist_name)
        return {
            "status": "OBSERVED",
            "identity": card.get("identity", {}),
            "attention": card.get("attention", {}),
            "live": card.get("live", {}),
            "festival": card.get("festival", {}),
            "coverage": card.get("coverage", {}),
        }
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}


def _economics_section(workspace_conn, proposed_show_key: str) -> dict[str, Any]:
    """Show-economics section: check for linked scenario, replay if possible."""
    # Look for a show-economics scenario linked to this proposed show.
    try:
        rows = _rows(
            workspace_conn,
            "SELECT scenario_key FROM planning.show_economics_scenarios "
            "WHERE json_extract(identity_context, '$.proposed_show_key') = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            [proposed_show_key],
        )
        if rows:
            from ..economics.show_economics_repository import load_show_economics_scenario
            scenario = load_show_economics_scenario(workspace_conn, rows[0]["scenario_key"])
            return {
                "status": "LINKED",
                "scenario_key": rows[0]["scenario_key"],
                "derived_outputs": scenario.get("derived_outputs"),
                "input_ledger": _summarize_input_ledger(scenario.get("inputs", {})),
            }
    except Exception:
        pass
    return {"status": "NO_LINKED_SCENARIO"}


def _summarize_input_ledger(inputs: dict[str, Any]) -> dict[str, Any]:
    """Summarize TypedInput provenance into evidence counts."""
    summary = {EVIDENCE_KNOWN: [], EVIDENCE_ASSUMED: [], EVIDENCE_UNKNOWN: []}
    for field_name, typed in (inputs or {}).items():
        value = typed.get("value")
        provenance = typed.get("provenance", "UNKNOWN")
        status = _classify(value, provenance)
        summary[status].append(field_name)
    return summary


def _provenance_section(serving_conn, workspace_conn, show: dict[str, Any]) -> dict[str, Any]:
    """Provenance: what sources inform each section."""
    return {
        "competitive_calendar": "events.provider_event_snapshots (Ticketmaster Discovery API)",
        "venue_capacity": "economics.capacity.assess_venue_claims (Wikidata/Wikipedia)",
        "comparable_events": "boxoffice_research_corpus_v1 (Pollstar/Billboard Boxscore)",
        "artist_context": "core.artists + metrics.artist_attention_observations + events.provider_event_snapshots",
        "show_economics": "planning.show_economics_scenarios (deterministic engine)",
        "source_count": 5,
        "has_external_augmentation": False,
    }


def _derive_risks(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive explicit risks/warnings from evidence gaps, never opinion."""
    risks: list[dict[str, Any]] = []

    # Capacity conflicts.
    vc = view.get("venue_capacity", {})
    if vc.get("review_required"):
        risks.append({
            "severity": "WARNING",
            "type": "CAPACITY_CONFLICT",
            "detail": f"{vc.get('conflicting_count', 0)} conflicting capacity claims — review required",
        })

    # Calendar: check PIT status.
    cc = view.get("competitive_calendar", {})
    if cc.get("status") == "UNKNOWN":
        risks.append({
            "severity": "WARNING",
            "type": "MISSING_COMPETITIVE_CALENDAR",
            "detail": "No competitive calendar data available",
        })
    if cc.get("pit_mode") == "NON_PIT":
        risks.append({
            "severity": "INFO",
            "type": "NON_PIT_CALENDAR",
            "detail": "Competitive calendar is non-PIT (no research cutoff); counts may include post-decision events",
        })
    unknown_count = len(cc.get("unknown_knowledge_time", []))
    if unknown_count > 0:
        risks.append({
            "severity": "INFO",
            "type": "INCOMPLETE_KNOWLEDGE_TIME",
            "detail": f"{unknown_count} competing events have unknown knowledge time",
        })

    # Economics: check for missing scenario.
    econ = view.get("show_economics", {})
    if econ.get("status") == "NO_LINKED_SCENARIO":
        risks.append({
            "severity": "WARNING",
            "type": "MISSING_ECONOMICS",
            "detail": "No show-economics scenario linked to this proposed show",
        })

    # Comparable: check for unavailable data.
    comp = view.get("comparable_events", {})
    gross_status = comp.get("gross", {}).get("status")
    if gross_status in ("UNKNOWN", None):
        risks.append({
            "severity": "INFO",
            "type": "NO_GROSS_COMPARABLES",
            "detail": "No comparable gross data available for this artist",
        })

    # Artist: check identity resolution.
    artist = view.get("artist_context", {})
    if not artist.get("identity", {}).get("matched"):
        risks.append({
            "severity": "WARNING",
            "type": "ARTIST_NOT_RESOLVED",
            "detail": f"Artist '{view.get('header', {}).get('artist_name', '')}' not resolved in canonical identity",
        })

    # Evidence: count assumptions.
    evidence = view.get("evidence_status", {})
    assumed_count = len(evidence.get(EVIDENCE_ASSUMED, []))
    unknown_count_ev = len(evidence.get(EVIDENCE_UNKNOWN, []))
    if assumed_count > 3:
        risks.append({
            "severity": "WARNING",
            "type": "ASSUMPTION_HEAVY",
            "detail": f"{assumed_count} fields are user assumptions — review before committing",
        })
    if unknown_count_ev > 3:
        risks.append({
            "severity": "INFO",
            "type": "LARGE_UNKNOWN_SURFACE",
            "detail": f"{unknown_count_ev} fields are unknown",
        })

    return risks


def _build_evidence_status(view: dict[str, Any]) -> dict[str, Any]:
    """Classify all observable evidence dimensions."""
    status: dict[str, list[str]] = {
        EVIDENCE_KNOWN: [],
        EVIDENCE_ASSUMED: [],
        EVIDENCE_UNKNOWN: [],
        EVIDENCE_CONFLICTING: [],
    }
    header = view.get("header", {})
    for field in ["artist_name", "artist_key", "market", "venue_name", "proposed_date"]:
        if header.get(field):
            status[EVIDENCE_KNOWN].append(f"header.{field}")
        else:
            status[EVIDENCE_UNKNOWN].append(f"header.{field}")
    for field in ["deal_type", "artist_guarantee", "decision_cutoff", "research_cutoff"]:
        if header.get(field):
            status[EVIDENCE_KNOWN].append(f"header.{field}")
        else:
            status[EVIDENCE_UNKNOWN].append(f"header.{field}")

    vc = view.get("venue_capacity", {})
    if vc.get("status") == "UNKNOWN":
        status[EVIDENCE_UNKNOWN].append("venue_capacity")
    elif vc.get("review_required"):
        status[EVIDENCE_CONFLICTING].append("venue_capacity")
    else:
        status[EVIDENCE_KNOWN].append("venue_capacity")

    cc = view.get("competitive_calendar", {})
    if cc.get("status") in ("OBSERVED",):
        status[EVIDENCE_KNOWN].append("competitive_calendar")
    else:
        status[EVIDENCE_UNKNOWN].append("competitive_calendar")

    comp = view.get("comparable_events", {})
    if comp.get("status") == "OBSERVED":
        status[EVIDENCE_KNOWN].append("comparable_events")
    else:
        status[EVIDENCE_UNKNOWN].append("comparable_events")

    artist = view.get("artist_context", {})
    if artist.get("identity", {}).get("matched"):
        status[EVIDENCE_KNOWN].append("artist_identity")
    else:
        status[EVIDENCE_UNKNOWN].append("artist_identity")

    econ = view.get("show_economics", {})
    if econ.get("status") == "LINKED":
        status[EVIDENCE_KNOWN].append("show_economics")
    else:
        status[EVIDENCE_UNKNOWN].append("show_economics")

    return status


# ---------------------------------------------------------------------------
# Scenario comparison (Phase 3)
# ---------------------------------------------------------------------------
def compare_proposals(
    serving_conn,
    workspace_conn,
    *,
    proposed_show_keys: list[str],
    project_key: str | None = None,
) -> dict[str, Any]:
    """Side-by-side comparison of 2+ proposed shows.

    Each show gets the full buyer decision view. The comparison highlight
    table marks which dimensions differ between scenarios.
    """
    if len(proposed_show_keys) < 2:
        return {"status": "INSUFFICIENT_SHOWS", "detail": "Need at least 2 proposed shows to compare"}

    views = [
        buyer_decision_view(serving_conn, workspace_conn, proposed_show_key=key)
        for key in proposed_show_keys
    ]

    headers = [v.get("header", {}) for v in views]

    comparison = {
        "status": "OBSERVED",
        "project_key": project_key,
        "scenario_count": len(views),
        "scenarios": [],
        "differences": _diff_scenarios(views),
        "comparison_table": _build_comparison_table(headers, views),
    }

    for i, view in enumerate(views):
        comparison["scenarios"].append({
            "proposed_show_key": proposed_show_keys[i],
            "header": view.get("header", {}),
            "venue_capacity": _diffable_venue(view.get("venue_capacity", {})),
            "competitive_summary": _diffable_calendar(view.get("competitive_calendar", {})),
            "comparable_summary": _diffable_comparable(view.get("comparable_events", {})),
            "economics_summary": _diffable_economics(view.get("show_economics", {})),
            "evidence_status": view.get("evidence_status", {}),
            "risk_count": len(view.get("risks", [])),
            "risks": view.get("risks", []),
        })

    return comparison


def _diff_scenarios(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify dimensions that differ across scenarios."""
    if len(views) < 2:
        return []
    diffs: list[dict[str, Any]] = []
    headers = [v.get("header", {}) for v in views]

    dims = [
        ("date", "proposed_date"),
        ("venue", "venue_name"),
        ("configuration", "venue_configuration"),
        ("market", "market"),
        ("deal_type", "deal_type"),
        ("guarantee", "artist_guarantee"),
        ("cutoff", "decision_cutoff"),
    ]
    for label, key in dims:
        values = [h.get(key) for h in headers]
        if len(set(str(v) for v in values)) > 1:
            diffs.append({
                "dimension": label,
                "differs": True,
                "scenario_values": [str(v) for v in values],
            })

    venue_safe = [v.get("venue_capacity", {}).get("safe_prefill_count", 0) for v in views]
    if len(set(venue_safe)) > 1:
        diffs.append({"dimension": "venue_safe_capacity", "differs": True, "scenario_values": venue_safe})

    calendar_counts = [
        len(v.get("competitive_calendar", {}).get("rows", []))
        for v in views
    ]
    if len(set(calendar_counts)) > 1:
        diffs.append({"dimension": "competing_event_count", "differs": True, "scenario_values": calendar_counts})

    return diffs


def _build_comparison_table(headers: list[dict[str, Any]], views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Row-oriented comparison table for tabular display."""
    rows: list[dict[str, Any]] = []
    specs = [
        ("Artist", lambda h: h.get("artist_name", "")),
        ("Market", lambda h: h.get("market", "")),
        ("Date", lambda h: str(h.get("proposed_date", ""))[:10]),
        ("Venue", lambda h: h.get("venue_name", "")),
        ("Configuration", lambda h: h.get("venue_configuration", "")),
        ("Deal Type", lambda h: h.get("deal_type", "—")),
        ("Guarantee", lambda h: f"${h.get('artist_guarantee'):,.0f}" if h.get("artist_guarantee") else "—"),
        ("Decision Cutoff", lambda h: str(h.get("decision_cutoff", ""))[:10] if h.get("decision_cutoff") else "—"),
    ]
    for label, fn in specs:
        vals = [fn(h) for h in headers]
        differs = len(set(vals)) > 1
        rows.append({"dimension": label, "values": vals, "differs": differs})

    # Add evidence-level comparisons.
    row_labels = [
        ("Competing Events (total)", lambda v: len(v.get("competitive_calendar", {}).get("rows", []))),
        ("Venue Safe Capacity", lambda v: v.get("venue_capacity", {}).get("safe_prefill_count", "—")),
        ("Evidence Risks", lambda v: len(v.get("risks", []))),
        ("Known Fields", lambda v: len(v.get("evidence_status", {}).get("KNOWN", []))),
        ("Unknown Fields", lambda v: len(v.get("evidence_status", {}).get("UNKNOWN", []))),
    ]
    for label, fn in row_labels:
        vals = [fn(v) for v in views]
        differs = len(set(str(x) for x in vals)) > 1
        rows.append({"dimension": label, "values": [str(x) for x in vals], "differs": differs})

    return rows


# Diffable extractors for side-by-side comparison.
def _diffable_venue(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": sec.get("status"),
        "safe_prefill_count": sec.get("safe_prefill_count"),
        "conflicting_count": sec.get("conflicting_count"),
        "review_required": sec.get("review_required"),
    }


def _diffable_calendar(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": sec.get("status"),
        "pit_mode": sec.get("pit_mode"),
        "total_competing": len(sec.get("rows", [])),
        "known_at_cutoff": len(sec.get("known_at_cutoff", [])),
        "observed_post_cutoff": len(sec.get("observed_after_cutoff", [])),
        "unknown_knowledge_time": len(sec.get("unknown_knowledge_time", [])),
    }


def _diffable_comparable(sec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": sec.get("status"),
        "gross_median": sec.get("gross", {}).get("weighted_median"),
        "gross_status": sec.get("gross", {}).get("status"),
    }


def _diffable_economics(sec: dict[str, Any]) -> dict[str, Any]:
    return {"status": sec.get("status"), "scenario_key": sec.get("scenario_key")}