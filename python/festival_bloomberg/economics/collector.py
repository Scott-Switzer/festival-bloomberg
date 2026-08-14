"""Lock-safe, append-only ticket snapshot collector.

Suitable for LaunchAgent execution. Does not install a scheduler.
Cadence is not stored in the data model.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Any, TextIO

from ..acquisition.contracts import AcquisitionRequest, AcquisitionStatus, utc_now
from ..acquisition.providers.seatgeek import SEARCH_EVENTS as SG_SEARCH_EVENTS
from ..acquisition.providers.seatgeek import SeatGeekProvider
from ..acquisition.providers.ticketmaster import GET_EVENT as TM_GET_EVENT
from ..acquisition.providers.ticketmaster import TicketmasterProvider
from .compare import compare_primary_secondary
from .resolve import match_ticketmaster_seatgeek
from .snapshots import primary_snapshots_from_ticketmaster, secondary_snapshot_from_seatgeek


class CollectorLock:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fh: TextIO | None = None

    def __enter__(self) -> CollectorLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def snapshot_event(
    *,
    events_repo,
    economics_repo,
    canonical_event_id: str,
    providers: tuple[str, ...] = ("ticketmaster", "seatgeek"),
    ticketmaster: TicketmasterProvider | None = None,
    seatgeek: SeatGeekProvider | None = None,
    artist_id: str | None = None,
) -> dict[str, Any]:
    retrieved = utc_now()
    errors: list[str] = []
    tm_obs = 0
    sg_obs = 0
    primary_n = 0
    secondary_n = 0
    matches = 0
    event_rows = [
        e for e in events_repo.query_events() if e.get("event_id") == canonical_event_id
    ]
    event = event_rows[0] if event_rows else {"event_id": canonical_event_id}
    observations = events_repo.query_provider_observations(canonical_event_id)
    tm_id = next(
        (o.get("platform_object_id") for o in observations if o.get("platform") == "ticketmaster"),
        None,
    )
    if not tm_id and str(canonical_event_id).startswith("evt_ticketmaster_"):
        tm_id = canonical_event_id.replace("evt_ticketmaster_", "", 1)

    tm_record = None
    if "ticketmaster" in providers:
        tm = ticketmaster or TicketmasterProvider()
        if not tm.configured():
            errors.append("ticketmaster_not_configured")
        elif tm_id:
            result = tm.acquire(
                AcquisitionRequest.new(
                    entity_id=canonical_event_id,
                    entity_type="event",
                    platform="ticketmaster",
                    query=event.get("event_name") or canonical_event_id,
                    operation=TM_GET_EVENT,
                    external_id=str(tm_id),
                    max_cost_usd=0.0,
                )
            )
            if result.status == AcquisitionStatus.SUCCESS and result.records:
                tm_record = result.records[0]
                tm_obs = 1
                for snap in primary_snapshots_from_ticketmaster(
                    tm_record,
                    canonical_event_id=canonical_event_id,
                    raw_observation_id=result.raw_payload_hash,
                    retrieved_at=retrieved,
                ):
                    if economics_repo.insert_primary_snapshot(snap):
                        primary_n += 1
            elif result.status != AcquisitionStatus.NO_RESULTS:
                errors.append(f"ticketmaster_{result.status.value}")
        else:
            errors.append("ticketmaster_event_id_missing")

    sg_record = None
    if "seatgeek" in providers:
        sg = seatgeek or SeatGeekProvider()
        if not sg.configured():
            errors.append("seatgeek_not_configured")
        else:
            result = None
            if event.get("event_name") or event.get("venue_name"):
                result = sg.acquire(
                    AcquisitionRequest.new(
                        entity_id=canonical_event_id,
                        entity_type="event",
                        platform="seatgeek",
                        query=event.get("event_name") or "",
                        market_id=event.get("market_id") or "Chicago, IL",
                        operation=SG_SEARCH_EVENTS,
                        max_records=10,
                        max_cost_usd=0.0,
                    )
                )
            if result and result.status == AcquisitionStatus.SUCCESS:
                chosen = None
                for record in result.records:
                    if tm_record is not None:
                        matched = match_ticketmaster_seatgeek(
                            ticketmaster=tm_record,
                            seatgeek=record,
                            canonical_event_id=canonical_event_id,
                            artist_id=artist_id,
                        )
                        if matched is not None:
                            chosen = record
                            matches += 1
                            break
                    elif str(record.get("local_date")) == str(event.get("local_date")):
                        if (record.get("venue_name") or "").lower() == (event.get("venue_name") or "").lower():
                            chosen = record
                            break
                if chosen is None and result.records:
                    chosen = None  # do not take first unmatched result
                if chosen is not None:
                    sg_record = chosen
                    sg_obs = 1
                    snap = secondary_snapshot_from_seatgeek(
                        chosen,
                        canonical_event_id=canonical_event_id,
                        raw_observation_id=result.raw_payload_hash,
                        retrieved_at=retrieved,
                    )
                    if economics_repo.insert_secondary_snapshot(snap):
                        secondary_n += 1
            elif result is not None and result.status not in {
                AcquisitionStatus.NO_RESULTS,
                AcquisitionStatus.NOT_CONFIGURED,
            }:
                errors.append(f"seatgeek_{result.status.value}")

    comparison = None
    if tm_record is not None and sg_record is not None:
        primaries = economics_repo.query_primary_snapshots(event_id=canonical_event_id)
        secondaries = economics_repo.query_secondary_snapshots(event_id=canonical_event_id)
        if primaries and secondaries:
            comparison = compare_primary_secondary(primaries[-1], secondaries[-1])
            comparison["canonical_event_id"] = canonical_event_id
            economics_repo.insert_comparison(
                comparison,
                retrieved_at=retrieved.isoformat(),
                knowledge_time=retrieved.isoformat(),
            )

    return {
        "events_requested": 1,
        "provider_observations": tm_obs + sg_obs,
        "price_snapshots": primary_n + secondary_n,
        "primary_snapshots": primary_n,
        "secondary_snapshots": secondary_n,
        "event_matches": matches,
        "errors": errors,
        "actual_cost": 0.0,
        "comparison": comparison,
    }


def snapshot_upcoming(*, events_repo, economics_repo, market: str, providers: tuple[str, ...], as_of=None) -> dict[str, Any]:
    as_of = as_of or utc_now()
    upcoming = [
        e
        for e in events_repo.query_events(market_id=market)
        if str(e.get("local_date") or "") >= as_of.date().isoformat()
    ]
    summaries = []
    totals = {
        "events_requested": len(upcoming),
        "provider_observations": 0,
        "price_snapshots": 0,
        "errors": [],
        "actual_cost": 0.0,
    }
    lock_path = os.environ.get("FESTIVAL_BLOOMBERG_ECON_LOCK", "data/warehouse/economics.lock")
    with CollectorLock(lock_path):
        for event in upcoming:
            summary = snapshot_event(
                events_repo=events_repo,
                economics_repo=economics_repo,
                canonical_event_id=event["event_id"],
                providers=providers,
                artist_id=event.get("artist_id"),
            )
            summaries.append({"event_id": event["event_id"], **summary})
            totals["provider_observations"] += summary["provider_observations"]
            totals["price_snapshots"] += summary["price_snapshots"]
            totals["errors"].extend(summary["errors"])
    totals["events"] = summaries
    return totals
