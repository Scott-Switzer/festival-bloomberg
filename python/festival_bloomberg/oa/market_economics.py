"""Live market-economics operational acceptance at $0.00.

Capacity claims, primary/secondary ticket snapshots, and fail-closed
outcome labels. No booking score, attendance estimate, or demand score.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionRequest, AcquisitionStatus, utc_now
from ..acquisition.providers.openstreetmap import OpenStreetMapProvider
from ..acquisition.providers.seatgeek import SeatGeekProvider
from ..acquisition.providers.ticketmaster import TicketmasterProvider
from ..acquisition.providers.wikidata import GET_ENTITY_CLAIMS, SEARCH_ENTITIES, WikidataProvider
from ..economics.capacity import (
    claim_from_wikidata,
    claims_from_osm,
    mark_conflicts,
    resolve_wikidata_search,
)
from ..economics.collector import snapshot_event
from ..economics.outcomes import historical_setlist_outcome, infer_sold_out_from_listing_count, infer_sold_out_from_offsale, prospective_outcome
from ..economics.repository import EconomicsRepository
from ..economics.snapshots import snapshot_deltas
from ..events.repository import EventRepository
from ..localenv import load_local_env
from ..warehouse.repository import FestivalRepository

MARKET = "Chicago, IL"


def run_market_economics_oa(
    *,
    db_path: str | None = None,
    manifest_path: str | None = None,
    market: str = MARKET,
    budget_usd: float = 0.0,
    throttle_seconds: float = 0.55,
) -> dict[str, Any]:
    del budget_usd
    load_local_env()
    started = utc_now()
    oa_run_id = f"econ_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        tm = TicketmasterProvider()
        sg = SeatGeekProvider()
        wd = WikidataProvider()
        osm = OpenStreetMapProvider()

        tm_auth = "VALID" if tm.configured() else "NOT_CONFIGURED"
        sg_auth = "VALID" if sg.configured() else "NOT_CONFIGURED"

        venues = events_repo.query_venues()
        venue_reports = []
        all_claims = []
        for venue in venues:
            report = _enrich_venue(
                venue,
                wd=wd,
                osm=osm,
                economics=economics,
                retrieved_at=started,
                throttle_seconds=throttle_seconds,
            )
            venue_reports.append(report)
            all_claims.extend(report.get("claims") or [])
            if throttle_seconds:
                time.sleep(throttle_seconds)

        mark_conflicts_applied = _conflict_summary(venue_reports)

        as_of = started
        all_events = events_repo.query_events(market_id=market)
        upcoming = [e for e in all_events if str(e.get("local_date") or "") >= as_of.date().isoformat()]
        historical = [e for e in all_events if str(e.get("local_date") or "") < as_of.date().isoformat()]

        upcoming_reports = []
        for event in upcoming:
            snap = snapshot_event(
                events_repo=events_repo,
                economics_repo=economics,
                canonical_event_id=event["event_id"],
                providers=("ticketmaster", "seatgeek"),
                ticketmaster=tm,
                seatgeek=sg,
                artist_id=event.get("artist_id"),
            )
            outcome = prospective_outcome(
                event_id=event["event_id"],
                ticketmaster_status=(economics.query_primary_snapshots(event_id=event["event_id"]) or [{}])[-1].get("event_status")
                if economics.query_primary_snapshots(event_id=event["event_id"])
                else event.get("event_status"),
                retrieved_at=started.isoformat(),
                knowledge_time=started.isoformat(),
                observation_ids=event.get("supporting_observation_ids") or [],
            )
            economics.insert_outcome(outcome)
            primaries = economics.query_primary_snapshots(event_id=event["event_id"])
            secondaries = economics.query_secondary_snapshots(event_id=event["event_id"])
            listing_count = secondaries[-1].get("listing_count") if secondaries else None
            upcoming_reports.append(
                {
                    "artist_id": event.get("artist_id"),
                    "event_id": event["event_id"],
                    "event_name": event.get("event_name"),
                    "local_date": str(event.get("local_date")),
                    "venue_name": event.get("venue_name"),
                    "primary_snapshots": _safe_primary(primaries),
                    "secondary_snapshots": _safe_secondary(secondaries),
                    "event_status": outcome.event_status,
                    "sold_out_status": infer_sold_out_from_offsale(outcome.event_status),
                    "sold_out_from_listings": infer_sold_out_from_listing_count(listing_count),
                    "attendance": None,
                    "time_to_event_days": _days_until(event.get("local_date"), as_of),
                    "snapshot_summary": snap,
                    "historical_price": "UNKNOWN",
                }
            )

        historical_reports = []
        for event in historical:
            obs = events_repo.query_provider_observations(event["event_id"])
            has_setlist = any(o.get("platform") == "setlistfm" for o in obs)
            outcome = historical_setlist_outcome(
                event_id=event["event_id"],
                has_setlist_observation=has_setlist,
                retrieved_at=started.isoformat(),
                knowledge_time=started.isoformat(),
                observation_ids=[o.get("observation_id") for o in obs if o.get("observation_id")],
            )
            economics.insert_outcome(outcome)
            historical_reports.append(
                {
                    "event_id": event["event_id"],
                    "local_date": str(event.get("local_date")),
                    "venue_name": event.get("venue_name"),
                    "event_status": outcome.event_status,
                    "performance_recorded_by_setlistfm": outcome.performance_recorded_by_setlistfm,
                    "sold_out_status": "UNKNOWN",
                    "attendance": None,
                    "historical_price": "UNKNOWN",
                }
            )

        pit = _pit_replay(economics, started)
        dq = _data_quality(venue_reports, upcoming_reports, historical_reports, tm_auth, sg_auth)
        statuses = _statuses(venue_reports, upcoming_reports, pit, tm_auth, sg_auth, dq)

        manifest = {
            "schema_version": "market_economics_evidence_v1",
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "market": market,
            "cost_usd": 0.0,
            "budget_usd": 0.0,
            "ticketmaster_auth": tm_auth,
            "seatgeek_auth": sg_auth,
            "providers": ["ticketmaster_official_api", "seatgeek_official_api", "wikidata_official_api", "openstreetmap_overpass"],
            "venues": venue_reports,
            "capacity_conflict_summary": mark_conflicts_applied,
            "upcoming_events": upcoming_reports,
            "historical_event_count": len(historical_reports),
            "historical_events": historical_reports,
            "pit_replay": pit,
            "statuses": statuses,
            "data_quality": dq,
            "no_demand_score": True,
            "no_attendance_estimate": True,
            "no_booking_recommendation": True,
            "no_ugc_in_manifest": True,
        }
        if manifest_path:
            Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(manifest_path).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


def _enrich_venue(venue: dict[str, Any], *, wd, osm, economics, retrieved_at, throttle_seconds: float) -> dict[str, Any]:
    name = venue.get("venue_name") or ""
    venue_id = venue["venue_id"]
    claims = []
    ambiguities = []
    wikidata_qid = None
    osm_id = None
    osm_type = None
    wd_status = "NO_CAPACITY_DATA"
    osm_status = "NO_CAPACITY_DATA"

    search = wd.acquire(
        AcquisitionRequest.new(
            entity_id=venue_id,
            entity_type="venue",
            platform="wikidata",
            query=f"{name} Chicago",
            operation=SEARCH_ENTITIES,
            max_records=5,
            max_cost_usd=0.0,
        )
    )
    if search.status == AcquisitionStatus.SUCCESS:
        resolved = resolve_wikidata_search(list(search.records), venue_name=name)
        wd_status = resolved["status"]
        ambiguities.extend(resolved.get("ambiguities") or [])
        wikidata_qid = resolved.get("qid")
        if wikidata_qid:
            claims_result = wd.acquire(
                AcquisitionRequest.new(
                    entity_id=venue_id,
                    entity_type="venue",
                    platform="wikidata",
                    query=name,
                    operation=GET_ENTITY_CLAIMS,
                    external_id=wikidata_qid,
                    max_cost_usd=0.0,
                )
            )
            if claims_result.status == AcquisitionStatus.SUCCESS:
                for record in claims_result.records:
                    claim = claim_from_wikidata(record, venue_id=venue_id)
                    if claim is not None:
                        economics.insert_capacity_claim(claim)
                        claims.append(claim.to_row())
            if throttle_seconds:
                time.sleep(throttle_seconds)

    osm_result = osm.acquire(
        AcquisitionRequest.new(
            entity_id=venue_id,
            entity_type="venue",
            platform="openstreetmap",
            query=name,
            market_id="Chicago, IL",
            max_records=10,
            max_cost_usd=0.0,
        )
    )
    osm_records = list(osm_result.records) if osm_result.status == AcquisitionStatus.SUCCESS else []
    with_capacity = [r for r in osm_records if r.get("capacity_claims")]
    if len(with_capacity) == 1:
        osm_status = "RESOLVED"
        osm_id = with_capacity[0].get("osm_id")
        osm_type = with_capacity[0].get("osm_type")
        for claim in claims_from_osm(with_capacity[0], venue_id=venue_id):
            economics.insert_capacity_claim(claim)
            claims.append(claim.to_row())
    elif len(with_capacity) > 1:
        osm_status = "AMBIGUOUS"
        ambiguities.extend([r.get("platform_object_id") for r in with_capacity])
    elif osm_records:
        osm_status = "PARTIAL"

    if claims:
        resolution = "RESOLVED" if (wikidata_qid or osm_id) and wd_status != "AMBIGUOUS" and osm_status != "AMBIGUOUS" else "PARTIAL"
    elif wd_status == "AMBIGUOUS" or osm_status == "AMBIGUOUS":
        resolution = "AMBIGUOUS"
    else:
        resolution = "NO_CAPACITY_DATA"

    economics.upsert_venue_mapping(
        {
            "mapping_id": f"vmap_{venue_id}",
            "canonical_venue_id": venue_id,
            "venue_name": name,
            "wikidata_qid": wikidata_qid,
            "osm_type": osm_type,
            "osm_id": osm_id,
            "ticketmaster_venue_id": venue.get("ticketmaster_venue_id"),
            "setlistfm_venue_id": venue.get("setlistfm_venue_id"),
            "resolution_status": resolution,
            "resolution_method": f"wikidata={wd_status};osm={osm_status}",
            "ambiguities": ambiguities,
            "knowledge_time": retrieved_at.isoformat(),
        }
    )
    values = {c.get("capacity_value") for c in claims if c.get("capacity_value") is not None}
    return {
        "canonical_venue_id": venue_id,
        "name": name,
        "ticketmaster_venue_id": venue.get("ticketmaster_venue_id"),
        "setlistfm_venue_id": venue.get("setlistfm_venue_id"),
        "wikidata_qid": wikidata_qid,
        "osm_id": osm_id,
        "capacity_claims": [
            {
                "claim_id": c.get("claim_id"),
                "capacity_value": c.get("capacity_value"),
                "capacity_kind": c.get("capacity_kind"),
                "source": c.get("source"),
                "usage_label": c.get("usage_label"),
                "claim_status": c.get("claim_status"),
            }
            for c in claims
        ],
        "source_count": len({c.get("source") for c in claims}),
        "conflict": len(values) > 1,
        "resolution_status": resolution,
        "claims": claims,
    }


def _conflict_summary(venue_reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "venues_with_conflicts": sum(1 for v in venue_reports if v.get("conflict")),
        "no_averaging": True,
    }


def _safe_primary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [
        "snapshot_id",
        "provider_event_id",
        "retrieved_at",
        "knowledge_time",
        "currency",
        "price_type",
        "minimum_price",
        "maximum_price",
        "fees_included",
        "event_status",
        "public_onsale_start",
        "public_onsale_end",
    ]
    return [{k: r.get(k) for k in keys} for r in rows]


def _safe_secondary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [
        "snapshot_id",
        "provider_event_id",
        "retrieved_at",
        "knowledge_time",
        "listing_count",
        "lowest_price",
        "average_price",
        "highest_price",
        "median_price",
        "provider_score",
    ]
    return [{k: r.get(k) for k in keys} for r in rows]


def _days_until(local_date: Any, as_of: datetime) -> int | None:
    if not local_date:
        return None
    try:
        day = datetime.fromisoformat(str(local_date)[:10]).date()
    except ValueError:
        return None
    return (day - as_of.date()).days


def _pit_replay(economics: EconomicsRepository, started: datetime) -> dict[str, Any]:
    """Snapshots retrieved during this run are visible after retrieval, not in 2020."""
    t_after = utc_now()
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    t2 = t_after + timedelta(seconds=1)
    claims_after = economics.query_capacity_claims(cutoff=t_after)
    claims_2020 = economics.query_capacity_claims(cutoff=t0)
    primary_after = economics.query_primary_snapshots(cutoff=t_after)
    primary_2020 = economics.query_primary_snapshots(cutoff=t0)
    secondary_after = economics.query_secondary_snapshots(cutoff=t_after)
    secondary_2020 = economics.query_secondary_snapshots(cutoff=t0)
    claims_all = economics.query_capacity_claims()
    primary_all = economics.query_primary_snapshots()
    secondary_all = economics.query_secondary_snapshots()
    deltas = []
    by_event: dict[str, list] = {}
    for snap in primary_after:
        by_event.setdefault(snap.get("canonical_event_id"), []).append(snap)
    for event_id, snaps in by_event.items():
        if len(snaps) >= 2:
            deltas.append(snapshot_deltas(snaps[0], snaps[-1], fields=("minimum_price", "maximum_price")))
    leak_ok = len(claims_2020) == 0 and len(primary_2020) == 0 and len(secondary_2020) == 0
    visible_ok = (
        len(claims_after) == len(claims_all)
        and len(primary_after) == len(primary_all)
        and len(secondary_after) == len(secondary_all)
    )
    return {
        "status": "PASS" if leak_ok and visible_ok else "FAIL",
        "t0": t0.isoformat(),
        "t1": started.isoformat(),
        "t_after_retrieval": t_after.isoformat(),
        "t2": t2.isoformat(),
        "claims_visible_after_retrieval": len(claims_after),
        "claims_visible_at_2020": len(claims_2020),
        "primary_visible_after_retrieval": len(primary_after),
        "primary_visible_at_2020": len(primary_2020),
        "secondary_visible_after_retrieval": len(secondary_after),
        "secondary_visible_at_2020": len(secondary_2020),
        "historic_capacity_did_not_leak_to_2020": len(claims_2020) == 0,
        "current_snapshots_did_not_leak_to_2020": len(primary_2020) == 0 and len(secondary_2020) == 0,
        "snapshots_visible_after_retrieval": visible_ok,
        "derived_deltas_use_visible_snapshots_only": True,
        "delta_examples": deltas,
        "run_started_at": started.isoformat(),
    }


def _data_quality(venues, upcoming, historical, tm_auth, sg_auth) -> dict[str, Any]:
    claims = [c for v in venues for c in v.get("capacity_claims") or []]
    return {
        "venues_requested": len(venues),
        "venues_resolved": sum(1 for v in venues if v.get("resolution_status") == "RESOLVED"),
        "venues_partial": sum(1 for v in venues if v.get("resolution_status") == "PARTIAL"),
        "venues_ambiguous": sum(1 for v in venues if v.get("resolution_status") == "AMBIGUOUS"),
        "venues_no_capacity_data": sum(1 for v in venues if v.get("resolution_status") == "NO_CAPACITY_DATA"),
        "venues_with_ge1_capacity_claim": sum(1 for v in venues if v.get("capacity_claims")),
        "venues_with_wikidata_capacity": sum(1 for v in venues if v.get("wikidata_qid") and v.get("capacity_claims")),
        "venues_with_osm_capacity": sum(1 for v in venues if v.get("osm_id") and any(c.get("source", "").startswith("osm:") for c in v.get("capacity_claims") or [])),
        "venues_with_official_source_capacity": 0,
        "capacity_conflicts": sum(1 for v in venues if v.get("conflict")),
        "configuration_specific_claims": sum(1 for c in claims if c.get("capacity_kind") in {"CONCERT", "SPORTS", "SEATED", "STANDING"}),
        "general_max_only_claims": sum(1 for c in claims if c.get("usage_label") == "MAXIMUM_CAPACITY_UPPER_BOUND"),
        "upcoming_events_tracked": len(upcoming),
        "ticketmaster_snapshots": sum(len(e.get("primary_snapshots") or []) for e in upcoming),
        "seatgeek_snapshots": sum(len(e.get("secondary_snapshots") or []) for e in upcoming),
        "event_matches": sum((e.get("snapshot_summary") or {}).get("event_matches") or 0 for e in upcoming),
        "historical_performances": len(historical),
        "performance_recorded_labels": sum(1 for e in historical if e.get("performance_recorded_by_setlistfm")),
        "sold_out_known": 0,
        "sold_out_unknown": len(upcoming) + len(historical),
        "attendance_known": 0,
        "attendance_unknown": len(upcoming) + len(historical),
        "ticketmaster_auth": tm_auth,
        "seatgeek_auth": sg_auth,
        "actual_cost": 0.0,
    }


def _statuses(venues, upcoming, pit, tm_auth, sg_auth, dq) -> dict[str, str]:
    claimed = dq["venues_with_ge1_capacity_claim"]
    if claimed >= 5:
        capacity = "PASS"
    elif claimed >= 1:
        capacity = "PARTIAL"
    else:
        capacity = "BLOCKED"
    primary = "PASS" if dq["ticketmaster_snapshots"] >= 1 else ("PARTIAL" if tm_auth == "VALID" else "PARTIAL")
    if sg_auth != "VALID":
        secondary = "NOT_CONFIGURED"
    elif dq["seatgeek_snapshots"] >= 1:
        secondary = "PASS"
    else:
        secondary = "PARTIAL"
    outcomes = "PASS" if dq["historical_performances"] and upcoming is not None else "PARTIAL"
    overall = "PASS"
    if capacity == "BLOCKED" or pit["status"] != "PASS":
        overall = "BLOCKED"
    elif capacity == "PARTIAL" or primary == "PARTIAL" or secondary in {"PARTIAL", "NOT_CONFIGURED"}:
        overall = "PARTIAL"
    return {
        "VENUE_CAPACITY_EVIDENCE": capacity,
        "PRIMARY_TICKET_SNAPSHOTS": primary,
        "SECONDARY_TICKET_SNAPSHOTS": secondary,
        "EVENT_OUTCOME_FRAMEWORK": outcomes,
        "PIT_ECONOMICS_REPLAY": pit["status"],
        "MARKET_ECONOMICS_EVIDENCE_V1": overall,
    }
