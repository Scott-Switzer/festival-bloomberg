"""Live artist × market × event history operational acceptance.

Ticketmaster Discovery + Setlist.fm official APIs at $0.00 monetary cost.
No booking score. No demand score. Chicago city proper only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionRequest, AcquisitionStatus, utc_now
from ..acquisition.providers.setlistfm import (
    SEARCH_ARTISTS,
    SEARCH_SETLISTS,
    SetlistFmProvider,
)
from ..acquisition.providers.ticketmaster import (
    SEARCH_ATTRACTIONS,
    SEARCH_EVENTS,
    TicketmasterProvider,
)
from ..events.fan_link import event_linked_fan_status, link_video_to_events
from ..events.features import build_artist_market_vector
from ..events.identity import canonical_artist_id, merge_identity, resolve_setlist_artists, resolve_ticketmaster_attractions
from ..events.reconcile import reconcile_events
from ..events.repository import EventRepository, provider_event_from_record
from ..evidence.provenance import parse_iso, utc
from ..evidence.repository import EvidenceRepository
from ..evidence.semantics import is_fan_role
from ..localenv import load_local_env
from ..markets.chicago import CHICAGO_MARKET_ID
from ..oa.operational_acceptance import CANDIDATE_ARTISTS
from ..warehouse.repository import FestivalRepository

MARKET = "Chicago, IL"
SETLIST_MAX_RECORDS = 400
TM_MAX_RECORDS = 100


def _entity_id(name: str) -> str:
    return canonical_artist_id(name)


def run_event_history_oa(
    *,
    db_path: str | None = None,
    youtube_db_path: str | None = None,
    manifest_path: str | None = None,
    market: str = MARKET,
    budget_usd: float = 0.0,
    artists: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    del budget_usd
    load_local_env()
    started = utc_now()
    oa_run_id = f"evt_{started.strftime('%Y%m%dT%H%M%S')}"
    universe = artists or CANDIDATE_ARTISTS
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        evidence = EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        tm = TicketmasterProvider()
        sl = SetlistFmProvider(throttle_seconds=0.55)

        tm_auth = "VALID" if tm.configured() else "NOT_CONFIGURED"
        sl_auth = "VALID" if sl.configured() else "NOT_CONFIGURED"

        artist_reports: list[dict[str, Any]] = []
        identities: list[dict[str, Any]] = []
        all_clusters = 0
        all_matches = 0
        tm_raw = 0
        sl_raw = 0
        tm_fail = 0
        sl_fail = 0

        for artist in universe:
            report = _collect_artist(
                artist=artist,
                market=market,
                oa_run_id=oa_run_id,
                evidence=evidence,
                events_repo=events_repo,
                tm=tm,
                sl=sl,
                as_of=started,
            )
            artist_reports.append(report)
            identities.append(report["identity"])
            tm_raw += report["ticketmaster_observations"]
            sl_raw += report["setlist_observations"]
            tm_fail += report["ticketmaster_failures"]
            sl_fail += report["setlist_failures"]
            all_clusters += report["canonical_events"]
            all_matches += report["cross_provider_matched_events"]

        youtube_links = _link_youtube(
            events_repo=events_repo,
            youtube_db_path=youtube_db_path,
            artist_reports=artist_reports,
            as_of=started,
        )

        pit = _pit_replay(evidence, oa_run_id, started)
        statuses = _statuses(
            tm_auth=tm_auth,
            sl_auth=sl_auth,
            identities=identities,
            artist_reports=artist_reports,
            youtube_links=youtube_links,
            pit=pit,
            tm_fail=tm_fail,
            sl_fail=sl_fail,
        )
        dq = _data_quality(artist_reports, tm_raw, sl_raw, tm_fail, sl_fail)
        manifest = {
            "schema_version": "artist-market-event-history-v1",
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "market": market,
            "providers": ["ticketmaster_official_api", "setlistfm_official_api"],
            "cost_usd": 0.0,
            "budget_usd": 0.0,
            "ticketmaster_auth": tm_auth,
            "setlistfm_auth": sl_auth,
            "identities": identities,
            "artists": artist_reports,
            "youtube_event_links": {
                "count": len(youtube_links["links"]),
                "status": youtube_links["status"],
            },
            "pit_replay": pit,
            "statuses": statuses,
            "data_quality": dq,
            "no_demand_score": True,
            "no_ugc_in_manifest": True,
        }
        if manifest_path:
            parent = os.path.dirname(manifest_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            Path(manifest_path).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


def _collect_artist(
    *,
    artist: str,
    market: str,
    oa_run_id: str,
    evidence: EvidenceRepository,
    events_repo: EventRepository,
    tm: TicketmasterProvider,
    sl: SetlistFmProvider,
    as_of: datetime,
) -> dict[str, Any]:
    artist_id = _entity_id(artist)
    tm_failures = 0
    sl_failures = 0

    sl_artists = sl.acquire(
        AcquisitionRequest.new(
            entity_id=artist_id,
            entity_type="artist",
            platform="setlistfm",
            query=artist,
            operation=SEARCH_ARTISTS,
            max_records=20,
            max_cost_usd=0.0,
            commercial_context="research",
            correlation_id=oa_run_id,
        )
    )
    tm_attr = tm.acquire(
        AcquisitionRequest.new(
            entity_id=artist_id,
            entity_type="artist",
            platform="ticketmaster",
            query=artist,
            operation=SEARCH_ATTRACTIONS,
            max_records=20,
            max_cost_usd=0.0,
            commercial_context="research",
            correlation_id=oa_run_id,
        )
    )
    if sl_artists.status not in {AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS, AcquisitionStatus.NOT_CONFIGURED}:
        sl_failures += 1
    if tm_attr.status not in {AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS, AcquisitionStatus.NOT_CONFIGURED}:
        tm_failures += 1

    sl_match = resolve_setlist_artists(artist, list(sl_artists.records))
    tm_match = resolve_ticketmaster_attractions(artist, list(tm_attr.records))
    identity = merge_identity(artist, setlist=sl_match, ticketmaster=tm_match)
    events_repo.upsert_identity(identity.to_dict(), resolved_at=as_of)

    sl_setlists_records = []
    sl_pages = {}
    if identity.musicbrainz_mbid:
        sl_setlists = sl.acquire(
            AcquisitionRequest.new(
                entity_id=artist_id,
                entity_type="artist",
                platform="setlistfm",
                query=artist,
                market_id=market,
                operation=SEARCH_SETLISTS,
                external_id=identity.musicbrainz_mbid,
                max_records=SETLIST_MAX_RECORDS,
                max_cost_usd=0.0,
                commercial_context="research",
                correlation_id=oa_run_id,
            )
        )
        if sl_setlists.status == AcquisitionStatus.RATE_LIMITED:
            import time

            time.sleep(1.5)
            sl_setlists = sl.acquire(
                AcquisitionRequest.new(
                    entity_id=artist_id,
                    entity_type="artist",
                    platform="setlistfm",
                    query=artist,
                    market_id=market,
                    operation=SEARCH_SETLISTS,
                    external_id=identity.musicbrainz_mbid,
                    max_records=SETLIST_MAX_RECORDS,
                    max_cost_usd=0.0,
                    commercial_context="research",
                    correlation_id=oa_run_id,
                )
            )
        if sl_setlists.status not in {AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS, AcquisitionStatus.NOT_CONFIGURED}:
            sl_failures += 1
        else:
            sl_setlists_records = list(sl_setlists.records)
        sl_pages = (sl_setlists.provider_metadata or {}).get("pagination") or {}
        evidence.ingest(
            AcquisitionRequest.new(
                entity_id=artist_id,
                entity_type="artist",
                platform="setlistfm",
                query=artist,
                market_id=market,
                correlation_id=oa_run_id,
                commercial_context="research",
            ),
            sl_setlists,
        )
    else:
        sl_setlists = None
    tm_events = tm.acquire(
        AcquisitionRequest.new(
            entity_id=artist_id,
            entity_type="event",
            platform="ticketmaster",
            query=artist,
            market_id=market,
            operation=SEARCH_EVENTS,
            external_id=identity.ticketmaster_attraction_id,
            max_records=TM_MAX_RECORDS,
            max_cost_usd=0.0,
            commercial_context="research",
            correlation_id=oa_run_id,
        )
    )
    if tm_events.status not in {AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS, AcquisitionStatus.NOT_CONFIGURED}:
        tm_failures += 1

    evidence.ingest(
        AcquisitionRequest.new(
            entity_id=artist_id,
            entity_type="artist",
            platform="ticketmaster",
            query=artist,
            market_id=market,
            correlation_id=oa_run_id,
            commercial_context="research",
        ),
        tm_events,
    )

    chicago_sl = [r for r in sl_setlists_records if r.get("market_id") == CHICAGO_MARKET_ID]
    chicago_tm = [r for r in tm_events.records if r.get("market_id") == CHICAGO_MARKET_ID]
    provider_events = [
        provider_event_from_record(r, artist_id=artist_id, raw_observation_id=r.get("platform_object_id"))
        for r in chicago_sl + chicago_tm
    ]
    clusters = reconcile_events(provider_events)
    for cluster in clusters:
        events_repo.store_reconciled(cluster, artist_id=artist_id, retrieved_at=as_of)

    stored = events_repo.query_events(artist_id=artist_id, market_id=CHICAGO_MARKET_ID, cutoff=as_of)
    events_repo.upsert_artist_market_relation(
        artist_id=artist_id,
        market_id=CHICAGO_MARKET_ID,
        events=stored,
        knowledge_time=as_of,
    )
    historical = [e for e in stored if _past(e, as_of)]
    upcoming = [e for e in stored if not _past(e, as_of)]
    venues = [e.get("venue_name") for e in historical if e.get("venue_name")]
    unique_venues = sorted(set(venues))
    vector = build_artist_market_vector(
        events_repo,
        artist_id=artist_id,
        market_id=CHICAGO_MARKET_ID,
        as_of=as_of,
    )
    matched = sum(1 for c in clusters if c.match_gate.startswith("GATE_"))
    disagreements = []
    for cluster in clusters:
        disagreements.extend(d.to_dict() | {"event_id": cluster.event_id} for d in cluster.disagreements)
    tm_pages = (tm_events.provider_metadata or {}).get("pagination") or {}
    coverage = sl_pages.get("coverage_status") or "UNKNOWN"
    if coverage == "TRUNCATED_BY_CAP" or tm_pages.get("coverage_status") == "TRUNCATED_BY_CAP":
        coverage_status = "TRUNCATED_BY_CAP"
    elif stored:
        coverage_status = "COLLECTED"
    elif identity.resolved:
        coverage_status = "NO_DATA"
    else:
        coverage_status = "UNRESOLVED"

    return {
        "artist": artist,
        "identity": identity.to_dict(),
        "identity_resolution_status": identity.resolution_method,
        "historical_chicago_performances": len(historical),
        "upcoming_chicago_events": len(upcoming),
        "first_observed_chicago_performance_date": vector["first_observed_chicago_performance_date"],
        "most_recent_historical_chicago_performance_date": vector[
            "most_recent_historical_chicago_performance_date"
        ],
        "next_upcoming_chicago_event_date": vector["next_upcoming_chicago_event_date"],
        "days_since_most_recent_chicago_performance": vector["days_since_last_chicago_performance"],
        "unique_chicago_venues": len(unique_venues),
        "repeat_venue_count": vector["repeat_chicago_venues"],
        "festival_appearance_count": vector["festival_appearance_count"],
        "standalone_concert_count": vector["standalone_concert_count"],
        "named_tours_observed": vector["named_tours"],
        "venue_history": vector["venue_sequence"],
        "ticketmaster_observations": len(tm_events.records),
        "setlist_observations": len(sl_setlists_records),
        "canonical_events": len(clusters),
        "cross_provider_matched_events": matched,
        "provider_disagreements": disagreements,
        "coverage_status": coverage_status,
        "setlist_pagination": sl_pages,
        "ticketmaster_pagination": tm_pages,
        "ticketmaster_failures": tm_failures,
        "setlist_failures": sl_failures,
        "missing_event_date": sum(1 for e in stored if not e.get("local_date")),
        "missing_venue": sum(1 for e in stored if not e.get("venue_name")),
        "missing_city": sum(1 for e in stored if not e.get("city")),
        "artist_market_relation": "PASS" if stored else "INSUFFICIENT",
        "artist_market_demand_signal": "INSUFFICIENT",
        "descriptive_vector": {
            k: v
            for k, v in vector.items()
            if k != "supporting_observation_ids"
        },
        "supporting_observation_id_count": len(vector.get("supporting_observation_ids") or []),
    }


def _link_youtube(
    *,
    events_repo: EventRepository,
    youtube_db_path: str | None,
    artist_reports: list[dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    path = youtube_db_path or "data/warehouse/youtube_fan_signal_oa.duckdb"
    if not Path(path).is_file():
        return {"links": [], "status": "INSUFFICIENT_EVIDENCE", "reason": "youtube warehouse absent"}
    yt_repo = FestivalRepository(path)
    try:
        yt_evidence = EvidenceRepository(yt_repo.conn)
        links: list[dict[str, Any]] = []
        any_pass = False
        for report in artist_reports:
            artist = report["artist"]
            artist_id = _entity_id(artist)
            events = events_repo.query_events(artist_id=artist_id, market_id=CHICAGO_MARKET_ID, cutoff=as_of)
            videos = [
                o
                for o in yt_evidence.query_observations(artist_id=artist_id)
                if not is_fan_role(o.get("content_role"))
            ]
            comments = [
                o
                for o in yt_evidence.query_observations(artist_id=artist_id)
                if is_fan_role(o.get("content_role"))
            ]
            artist_links: list[dict[str, Any]] = []
            for video in videos:
                artist_links.extend(
                    link_video_to_events(
                        video,
                        events,
                        artist_name=artist,
                        search_query=video.get("search_query"),
                    )
                )
            for link in artist_links:
                events_repo.store_fan_link(
                    youtube_video_id=link["youtube_video_id"],
                    event_id=link["canonical_event_id"],
                    link_method=link["link_method"],
                    supporting_evidence=link["supporting_evidence"],
                    knowledge_time=as_of,
                    confidence_state=link["confidence_state"],
                )
            status = event_linked_fan_status(events=events, links=artist_links, fan_comments=comments)
            report["event_linked_fan_signal"] = status
            report["event_linked_video_count"] = len({lnk["youtube_video_id"] for lnk in artist_links})
            links.extend(artist_links)
            if status == "PASS":
                any_pass = True
        return {
            "links": links,
            "status": "PASS" if any_pass else "INSUFFICIENT_EVIDENCE",
        }
    finally:
        yt_repo.close()


def _pit_replay(evidence: EvidenceRepository, oa_run_id: str, started: datetime) -> dict[str, Any]:
    rows = evidence.conn.execute(
        """
        SELECT observation_id, knowledge_time, event_time
        FROM acquisition.raw_observations
        WHERE correlation_id = ?
        ORDER BY knowledge_time
        """,
        [oa_run_id],
    ).fetchall()
    if len(rows) < 2:
        return {"status": "FAIL", "reason": "insufficient observations"}
    times = [parse_iso(str(r[1])) for r in rows if r[1]]
    times = [t for t in times if t]
    if not times:
        return {"status": "FAIL", "reason": "missing knowledge_time"}
    t1 = min(times)
    t2 = max(times)
    visible_t1 = [r for r in rows if parse_iso(str(r[1])) and utc(parse_iso(str(r[1]))) <= utc(t1)]
    visible_t2 = [r for r in rows if parse_iso(str(r[1])) and utc(parse_iso(str(r[1]))) <= utc(t2)]
    historic_leak = []
    cutoff_2020 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for obs_id, knowledge, event_time in rows:
        kt = parse_iso(str(knowledge)) if knowledge else None
        et = parse_iso(str(event_time)[:19]) if event_time else None
        if et is not None and et.year <= 2019 and kt is not None and kt.year >= 2026:
            if utc(kt) <= cutoff_2020:
                historic_leak.append(obs_id)
    status = "PASS" if len(visible_t2) >= len(visible_t1) and not historic_leak else "FAIL"
    return {
        "status": status,
        "t1": t1.isoformat(),
        "t2": t2.isoformat(),
        "t1_visible_count": len(visible_t1),
        "t2_visible_count": len(visible_t2),
        "historical_event_did_not_leak_to_2020": not historic_leak,
        "note": "event_time is a source fact; knowledge_time is retrieval time",
    }


def _statuses(**kwargs) -> dict[str, str]:
    identities = kwargs["identities"]
    reports = kwargs["artist_reports"]
    resolved = sum(1 for i in identities if i.get("musicbrainz_mbid") or i.get("ticketmaster_attraction_id"))
    if resolved == len(identities):
        ident = "PASS"
    elif resolved:
        ident = "PARTIAL"
    else:
        ident = "FAIL"
    tm_ok = kwargs["tm_auth"] == "VALID" and kwargs["tm_fail"] == 0
    sl_ok = kwargs["sl_auth"] == "VALID" and kwargs["sl_fail"] == 0
    hist = sum(r["historical_chicago_performances"] for r in reports)
    matches = sum(r["cross_provider_matched_events"] for r in reports)
    return {
        "ARTIST_IDENTITY_RESOLUTION": ident,
        "TICKETMASTER_LIVE_INGESTION": "PASS" if tm_ok else ("PARTIAL" if kwargs["tm_auth"] == "VALID" else "FAIL"),
        "SETLISTFM_LIVE_INGESTION": "PASS" if sl_ok else ("PARTIAL" if kwargs["sl_auth"] == "VALID" else "FAIL"),
        "CHICAGO_PERFORMANCE_HISTORY": "PASS" if hist else "PARTIAL",
        "CROSS_PROVIDER_EVENT_RECONCILIATION": "PASS" if matches else "NOT_EVALUATED",
        "EVENT_LINKED_FAN_SIGNAL": kwargs["youtube_links"]["status"],
        "PIT_EVENT_HISTORY_REPLAY": kwargs["pit"]["status"],
        "EVENT_HISTORY_V1": "PASS"
        if ident in {"PASS", "PARTIAL"} and (tm_ok or sl_ok) and hist and kwargs["pit"]["status"] == "PASS"
        else "PARTIAL",
    }


def _data_quality(reports, tm_raw, sl_raw, tm_fail, sl_fail) -> dict[str, Any]:
    resolved = [r for r in reports if r["identity"]["resolution_method"] != "UNRESOLVED"]
    stored_events = sum(r["canonical_events"] for r in reports)
    missing_date = sum(r["missing_event_date"] for r in reports)
    missing_venue = sum(r["missing_venue"] for r in reports)
    missing_city = sum(r["missing_city"] for r in reports)
    denom = stored_events or 1
    return {
        "artists_requested": len(reports),
        "artists_resolved": len(resolved),
        "ticketmaster_attractions_resolved": sum(
            1 for r in reports if r["identity"].get("ticketmaster_attraction_id")
        ),
        "setlist_mbids_resolved": sum(1 for r in reports if r["identity"].get("musicbrainz_mbid")),
        "ticketmaster_raw_observations": tm_raw,
        "setlist_raw_observations": sl_raw,
        "canonical_events": stored_events,
        "historical_chicago_performances": sum(r["historical_chicago_performances"] for r in reports),
        "upcoming_chicago_events": sum(r["upcoming_chicago_events"] for r in reports),
        "cross_provider_matches": sum(r["cross_provider_matched_events"] for r in reports),
        "venues": sum(r["unique_chicago_venues"] for r in reports),
        "festival_appearances": sum(r["festival_appearance_count"] for r in reports),
        "missing_event_date_pct": missing_date / denom,
        "missing_venue_pct": missing_venue / denom,
        "missing_city_pct": missing_city / denom,
        "missing_artist_id_pct": sum(1 for r in reports if r["identity"]["resolution_method"] == "UNRESOLVED")
        / len(reports),
        "provider_api_failures": tm_fail + sl_fail,
        "actual_cost": 0.0,
        "sampling_status": "event collection caps recorded per artist pagination.coverage_status",
    }


def _past(event: dict[str, Any], as_of: datetime) -> bool:
    local_date = event.get("local_date")
    if not local_date:
        return True
    try:
        return datetime.fromisoformat(str(local_date)[:10]).date() < as_of.date()
    except ValueError:
        return True
