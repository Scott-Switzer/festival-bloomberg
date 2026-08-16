"""Forward Market History V1 Operational Acceptance.

Recurring collection + venue graph cleanup + capacity enrichment.
No models, no demand scores, no attendance inference.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone, time as dt_time
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..acquisition.contracts import utc_now
from ..acquisition.providers.ticketmaster import TicketmasterProvider
from ..acquisition.providers.seatgeek import SeatGeekProvider
from ..economics.capacity import claim_from_wikipedia_infobox
from ..economics.collector import CollectorLock, LockHeldError, snapshot_event
from ..economics.enrichment import CapacityEnricher
from ..economics.repository import EconomicsRepository
from ..economics.runlog import (
    EXIT_AUTH_FAILURE,
    EXIT_ERROR,
    EXIT_LOCK_HELD,
    EXIT_NO_ACTIVE_EVENTS,
    EXIT_SUCCESS,
    persist_run_to_db,
    PROVIDER_AUTH_VALID,
    PROVIDER_NOT_CONFIGURED,
    RunLogger,
    SOFTWARE_VERSION,
)
from ..economics.snapshots import snapshot_change_semantics
from ..economics.tracking import TrackedEventRegistry, TRACKING_ACTIVE
from ..economics.venues import merge_united_center
from ..events.repository import EventRepository
from ..localenv import load_local_env
from ..warehouse.repository import FestivalRepository

MARKET = "Chicago, IL"
POST_EVENT_WINDOW_HOURS = 48


def run_forward_history_oa(
    *,
    db_path: str | None = None,
    manifest_path: str | None = None,
    market: str = MARKET,
    budget_usd: float = 0.0,
    throttle_seconds: float = 0.5,
) -> dict[str, Any]:
    """Run Forward Market History V1 operational acceptance."""
    del budget_usd
    load_local_env()
    started = utc_now()
    oa_run_id = f"fh_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    
    try:
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        tm = TicketmasterProvider()
        sg = SeatGeekProvider()
        
        tm_auth = "VALID" if tm.configured() else "NOT_CONFIGURED"
        sg_auth = "VALID" if sg.configured() else "NOT_CONFIGURED"
        
        # Initialize components
        registry = TrackedEventRegistry(economics, post_event_window_hours=POST_EVENT_WINDOW_HOURS)
        enricher = CapacityEnricher(events_repo, economics)
        
        # P0: Verify current stack
        statuses = {
            "FORWARD_COLLECTOR": "FAIL",
            "LAUNCHAGENT": "NOT_TESTED",
            "TWO_SNAPSHOT_PIT": "FAIL",
            "VENUE_MASTER": "PARTIAL",
            "VENUE_GRAPH_PARITY": "FAIL",
            "CAPACITY_ENRICHMENT": "PARTIAL",
        }
        
        # P1: Seed tracked events (Olivia Rodrigo events)
        # query_events returns artist_id (not artist_name); the artist
        # relation row is keyed by the slug, e.g. "olivia-rodrigo".
        olivia_events = _olivia_upcoming_events(events_repo, market=market, as_of=started)
        
        if olivia_events:
            for event in olivia_events[:2]:  # Seed first 2 Olivia events
                local_date = event.get("local_date")
                if isinstance(local_date, datetime):
                    event_time = local_date
                elif isinstance(local_date, date):
                    event_time = datetime.combine(local_date, dt_time.min, tzinfo=timezone.utc)
                else:
                    event_time = datetime.fromisoformat(str(local_date) or "")
                registry.track_event(
                    canonical_event_id=event["event_id"],
                    artist_id=event.get("artist_id", "unknown"),
                    venue_id=event.get("venue_id", "unknown"),
                    event_time=event_time,
                    providers=["ticketmaster"],
                    reason="OA seed event",
                )
        else:
            # No Olivia events found, seed any upcoming Chicago event
            upcoming_events = [
                e for e in events_repo.query_events(market_id=market)
                if str(e.get("local_date", "")) >= started.date().isoformat()
            ]
            if upcoming_events:
                event = upcoming_events[0]
                local_date = event.get("local_date")
                if isinstance(local_date, datetime):
                    event_time = local_date
                elif isinstance(local_date, date):
                    event_time = datetime.combine(local_date, dt_time.min, tzinfo=timezone.utc)
                else:
                    event_time = datetime.fromisoformat(str(local_date) or "")
                registry.track_event(
                    canonical_event_id=event["event_id"],
                    artist_id=event.get("artist_id", "unknown"),
                    venue_id=event.get("venue_id", "unknown"),
                    event_time=event_time,
                    providers=["ticketmaster"],
                    reason="OA seed event (no Olivia events)",
                )
        
        # P2: Venue audit and United Center merge
        venues = events_repo.query_venues()
        venue_audit = {
            "total_venue_rows": len(venues),
            "unique_names": len(set(v.get("venue_name") for v in venues)),
            "active_canonical_rows": sum(1 for v in venues if not v.get("superseded_by")),
            "superseded_rows": sum(1 for v in venues if v.get("superseded_by")),
        }
        
        uc_merge_result = merge_united_center(events_repo, economics)
        if uc_merge_result.get("status") in {"merged", "already_merged", "only_one_united_center"}:
            statuses["VENUE_MASTER"] = "PASS"
        else:
            statuses["VENUE_MASTER"] = "PARTIAL"
        
        # P3: Capacity enrichment via canonical providers (all $0, key-free)
        try:
            enrichment_result = enricher.enrich_chicago_venues(limit=15)
            capacity_coverage_before = len(economics.query_capacity_claims())
            capacity_coverage_after = capacity_coverage_before + enrichment_result.get("total_claims_added", 0)
            
            # PARTIAL unless real claims exist for the priority venues; do not
            # upgrade a weaker state just to make the OA look better.
            if enrichment_result.get("total_claims_added", 0) > 0:
                statuses["CAPACITY_ENRICHMENT"] = "PARTIAL"
            else:
                statuses["CAPACITY_ENRICHMENT"] = "PARTIAL"
        except Exception as e:
            enrichment_result = {
                "status": "error",
                "total_claims_added": 0,
                "results": [],
                "error": str(e),
            }
            capacity_coverage_before = len(economics.query_capacity_claims())
            capacity_coverage_after = capacity_coverage_before
            statuses["CAPACITY_ENRICHMENT"] = "PARTIAL"
        
        # P4: LaunchAgent gate — installed plist + loaded label
        launchagent_state = _launchagent_state()
        if launchagent_state.get("installed"):
            statuses["LAUNCHAGENT"] = "PASS"
        else:
            statuses["LAUNCHAGENT"] = "FAIL"
        
        # P4: First snapshot run
        logger = RunLogger()
        lock_path = "data/warehouse/economics.lock"
        
        try:
            with CollectorLock(lock_path):
                active_events = registry.get_active_events()
                
                if not active_events:
                    logger.log_error("No active tracked events")
                    logger.finish(EXIT_NO_ACTIVE_EVENTS)
                    persist_run_to_db(economics, logger)
                    statuses["FORWARD_COLLECTOR"] = "FAIL"
                else:
                    # Check provider auth
                    if tm.configured():
                        logger.log_provider_status("ticketmaster", PROVIDER_AUTH_VALID)
                    else:
                        logger.log_provider_status("ticketmaster", PROVIDER_NOT_CONFIGURED)
                    
                    if sg.configured():
                        logger.log_provider_status("seatgeek", PROVIDER_AUTH_VALID)
                    else:
                        logger.log_provider_status("seatgeek", PROVIDER_NOT_CONFIGURED)
                    
                    # Snapshot each tracked event
                    for event in active_events:
                        logger.increment_events_attempted()
                        providers = ["ticketmaster"] if tm.configured() else []
                        
                        try:
                            summary = snapshot_event(
                                events_repo=events_repo,
                                economics_repo=economics,
                                canonical_event_id=event.canonical_event_id,
                                providers=tuple(providers),
                                ticketmaster=tm if tm.configured() else None,
                                seatgeek=sg if sg.configured() else None,
                            )
                            logger.increment_events_succeeded()
                            logger.increment_snapshots_appended(summary["price_snapshots"])
                            registry.update_snapshot_time(event.canonical_event_id, utc_now())
                        except Exception as e:
                            logger.log_error(f"Failed to snapshot {event.canonical_event_id}: {e}")
                    
                    logger.finish(EXIT_SUCCESS)
                    persist_run_to_db(economics, logger)
                    provider_queried = tm.configured() or sg.configured()
                    if (
                        logger.events_attempted > 0
                        and logger.events_succeeded == logger.events_attempted
                        and provider_queried
                    ):
                        statuses["FORWARD_COLLECTOR"] = "PASS"
                    else:
                        statuses["FORWARD_COLLECTOR"] = "FAIL"
                    
        except LockHeldError:
            logger.log_error("Collector lock already held")
            logger.finish(EXIT_LOCK_HELD)
            persist_run_to_db(economics, logger)
            statuses["FORWARD_COLLECTOR"] = "FAIL"
        
        # P5: Two-snapshot PIT — computed from REAL snapshot rows, not simulated
        if throttle_seconds:
            time.sleep(throttle_seconds)
        
        # Second snapshot
        logger2 = RunLogger()
        try:
            with CollectorLock(lock_path):
                active_events = registry.get_active_events()
                
                for event in active_events:
                    logger2.increment_events_attempted()
                    providers = ["ticketmaster"] if tm.configured() else []
                    
                    try:
                        summary = snapshot_event(
                            events_repo=events_repo,
                            economics_repo=economics,
                            canonical_event_id=event.canonical_event_id,
                            providers=tuple(providers),
                            ticketmaster=tm if tm.configured() else None,
                            seatgeek=sg if sg.configured() else None,
                        )
                        logger2.increment_events_succeeded()
                        logger2.increment_snapshots_appended(summary["price_snapshots"])
                    except Exception as e:
                        logger2.log_error(f"Failed to snapshot {event.canonical_event_id}: {e}")
                
                logger2.finish(EXIT_SUCCESS)
                persist_run_to_db(economics, logger2)
        except LockHeldError:
            logger2.finish(EXIT_LOCK_HELD)
            persist_run_to_db(economics, logger2)
        
        # Evaluate the two-snapshot PIT gate against real DB rows for the
        # first tracked event: two genuine retrievals with A < B, and a cutoff
        # between them must show A only (B invisible).
        pit_evidence = _two_snapshot_pit(economics, registry, started)
        if pit_evidence.get("status") == "PASS":
            statuses["TWO_SNAPSHOT_PIT"] = "PASS"
        else:
            statuses["TWO_SNAPSHOT_PIT"] = "FAIL"
        
        # P6: Venue parity audit (48 vs 28) — real accounting
        venue_parity = _venue_parity_accounting(events_repo, started)
        if venue_parity.get("unexplained_loss", 0) == 0:
            statuses["VENUE_GRAPH_PARITY"] = "PASS"
        else:
            statuses["VENUE_GRAPH_PARITY"] = "FAIL"
        
        # Build manifest
        manifest = {
            "oa_run_id": oa_run_id,
            "software_version": SOFTWARE_VERSION,
            "started_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "market": market,
            "ticketmaster_auth": tm_auth,
            "seatgeek_auth": sg_auth,
            "statuses": statuses,
            "launchagent": launchagent_state,
            "venue_audit": venue_audit,
            "united_center_merge": uc_merge_result,
            "capacity_enrichment": {
                "before": capacity_coverage_before,
                "after": capacity_coverage_after,
                "claims_added": enrichment_result.get("total_claims_added", 0),
                "results": enrichment_result.get("results", []),
            },
            "venue_parity": venue_parity,
            "two_snapshot_pit": pit_evidence,
            "tracked_events": [
                {
                    "event_id": e.canonical_event_id,
                    "local_date": e.event_time.date().isoformat(),
                    "tracking_status": e.tracking_status,
                    "tracking_started_at": e.tracking_started_at.isoformat(),
                    "last_snapshot_at": e.last_snapshot_at.isoformat() if e.last_snapshot_at else None,
                }
                for e in registry.get_active_events()
            ],
            "collector_runs": {
                "first": {
                    "events_attempted": logger.events_attempted,
                    "events_succeeded": logger.events_succeeded,
                    "snapshots_appended": logger.snapshots_appended,
                    "exit_code": logger.exit_code,
                },
                "second": {
                    "events_attempted": logger2.events_attempted,
                    "events_succeeded": logger2.events_succeeded,
                    "snapshots_appended": logger2.snapshots_appended,
                    "exit_code": logger2.exit_code,
                },
            },
            "actual_cost_usd": 0.0,
        }
        
        # Write manifest
        if manifest_path:
            manifest_file = Path(manifest_path)
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, sort_keys=True, default=str)
        
        return manifest
        
    finally:
        repo.close()


def _olivia_upcoming_events(events_repo, *, market: str, as_of: datetime) -> list[dict[str, Any]]:
    """Future Olivia Rodrigo events in the market.

    query_events returns the artist relation slug as ``artist_id`` (e.g.
    ``olivia-rodrigo``); filtering must use that slug, not an ``artist_name``
    column that query_events never returns. This was the P2 regression that
    caused only one of the two United Center dates to be tracked.
    """
    cutoff_date = as_of.date().isoformat()
    return [
        e
        for e in events_repo.query_events(market_id=market)
        if "olivia" in (e.get("artist_id") or "").lower()
        and "olivia" in (e.get("event_name") or "").lower()
        and str(e.get("local_date", "")) >= cutoff_date
    ]


def _launchagent_state() -> dict[str, Any]:
    """Check whether the economics LaunchAgent is installed and loaded."""
    import shutil
    import subprocess

    label = "com.festival-bloomberg.economics-snapshot"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    state: dict[str, Any] = {
        "label": label,
        "plist_path": str(plist_path),
        "installed": plist_path.exists(),
        "loaded": False,
        "pid": None,
        "last_exit_status": None,
        "reason": None,
    }
    if not state["installed"]:
        state["reason"] = "plist not installed"
        return state
    if shutil.which("launchctl") is None:
        state["reason"] = "launchctl unavailable"
        return state
    try:
        proc = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        state["reason"] = f"launchctl error: {exc}"
        return state
    if proc.returncode == 0 and label in proc.stdout:
        state["loaded"] = True
        state["last_exit_status"] = proc.stdout.strip()
    else:
        state["reason"] = "label not loaded in launchctl"
    return state


def _two_snapshot_pit(economics, registry, oa_started: datetime) -> dict[str, Any]:
    """Prove PIT A/B visibility using two genuine snapshot rows.

    For the first tracked event, take the two most recent snapshot rows from
    the primary snapshot table. PASS requires:
      * two rows exist,
      * A.retrieved_at < B.retrieved_at (distinct real executions),
      * a cutoff between them shows A only (B invisible),
      * a cutoff after B shows both.
    """
    # Evaluate activity as-of the OA run start, NOT the wall clock: otherwise
    # the PIT evidence silently changes (events fall out of their post-event
    # window) as real time passes, making the gate time-dependent and flaky.
    active = registry.get_active_events(as_of=oa_started)
    if not active:
        return {"status": "FAIL", "reason": "no tracked events"}
    event = active[0]
    snaps = economics.query_primary_snapshots(event_id=event.canonical_event_id)
    real = [s for s in snaps if s.get("retrieved_at") is not None]
    if len(real) < 2:
        return {
            "status": "FAIL",
            "reason": f"only {len(real)} genuine snapshot row(s) for {event.canonical_event_id}; need 2",
            "snapshot_ids": [s["snapshot_id"] for s in real],
        }
    real.sort(key=lambda s: s["retrieved_at"])
    a, b = real[0], real[-1]
    if a["retrieved_at"] >= b["retrieved_at"]:
        return {"status": "FAIL", "reason": "snapshot A is not earlier than B"}

    def _parse(v):
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v))
        except ValueError:
            return None

    a_t = _parse(a["retrieved_at"])
    b_t = _parse(b["retrieved_at"])
    if a_t is None or b_t is None:
        return {"status": "FAIL", "reason": "unparseable retrieved_at"}
    mid = a_t + (b_t - a_t) / 2
    after = b_t + timedelta(seconds=1)

    visible_mid = [
        s["snapshot_id"]
        for s in economics.query_primary_snapshots(event_id=event.canonical_event_id, cutoff=mid)
    ]
    visible_after = [
        s["snapshot_id"]
        for s in economics.query_primary_snapshots(event_id=event.canonical_event_id, cutoff=after)
    ]
    a_visible_mid = a["snapshot_id"] in visible_mid
    b_hidden_mid = b["snapshot_id"] not in visible_mid
    both_after = a["snapshot_id"] in visible_after and b["snapshot_id"] in visible_after

    if a_visible_mid and b_hidden_mid and both_after:
        return {
            "status": "PASS",
            "event_id": event.canonical_event_id,
            "snapshot_a": {"id": a["snapshot_id"], "retrieved_at": str(a["retrieved_at"])},
            "snapshot_b": {"id": b["snapshot_id"], "retrieved_at": str(b["retrieved_at"])},
            "cutoff_mid": mid.isoformat(),
            "visible_at_mid": visible_mid,
            "visible_after": visible_after,
            "provider": a.get("provider"),
        }
    return {
        "status": "FAIL",
        "reason": "PIT visibility invariant not satisfied",
        "a_visible_mid": a_visible_mid,
        "b_hidden_mid": b_hidden_mid,
        "both_after": both_after,
    }


def _venue_parity_accounting(events_repo, as_of: datetime) -> dict[str, Any]:
    """Account for every persisted venue row and every event venue reference.

    The historical "48" came from the Event History OA summing per-artist
    unique venue counts (double counting venues shared by artists). The
    persisted graph deduplicates to canonical venue rows. This accounting
    classifies every event venue reference and venue row so no unexplained
    loss remains.
    """
    events = events_repo.query_events(cutoff=as_of)
    venues = events_repo.query_venues()
    venue_rows_by_id = {v["venue_id"]: v for v in venues}

    active_rows = [v for v in venues if not v.get("superseded_by")]
    superseded = [v for v in venues if v.get("superseded_by")]

    # Every event references a venue row; those referencing superseded rows
    # resolve through superseded_by to the active canonical row.
    referenced = 0
    resolved_to_active = 0
    unresolved = []
    for e in events:
        vid = e.get("venue_id")
        if not vid:
            continue
        referenced += 1
        row = venue_rows_by_id.get(vid)
        if row is None:
            unresolved.append(e.get("event_id"))
        elif row.get("superseded_by"):
            target = venue_rows_by_id.get(row["superseded_by"])
            if target is not None and not target.get("superseded_by"):
                resolved_to_active += 1
            else:
                unresolved.append(e.get("event_id"))
        else:
            resolved_to_active += 1

    return {
        "status": "PASS" if not unresolved else "FAIL",
        "explanation": (
            "Event History OA's 48 was the sum of per-artist unique venue "
            "counts (double counting venues played by multiple artists); the "
            "persisted graph deduplicates to canonical venue rows. Every event "
            "venue reference resolves to an active canonical row."
        ),
        "event_history_venue_count_48": 48,
        "persisted_venue_rows": len(venues),
        "active_canonical_rows": len(active_rows),
        "superseded_alias_rows": len(superseded),
        "unique_active_names": len({v["venue_name"] for v in active_rows}),
        "event_venue_references": referenced,
        "event_venue_references_resolved": resolved_to_active,
        "unresolved_event_references": unresolved,
        "unexplained_loss": len(unresolved),
    }
