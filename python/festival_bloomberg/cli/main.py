"""Festival Signal Fabric CLI.

Usage::

    python -m festival_bloomberg.cli evidence collect-social \\
        --artist "ARTIST NAME" --market "Chicago, IL" \\
        --since 2026-07-01T00:00:00Z --cutoff 2026-08-01T00:00:00Z \\
        --providers youtube,monid,scrapling --max-records 500

    python -m festival_bloomberg.cli evidence summarize-social \\
        --artist "ARTIST NAME" --market "Chicago, IL" \\
        --cutoff 2026-08-01T00:00:00Z

This milestone is evidence acquisition, NOT underwriting: the output contains
no PROCEED/PASS decision and no recommendation.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from ..acquisition.contracts import AcquisitionRequest
from ..acquisition.policy import PolicyGate
from ..acquisition.providers import default_providers
from ..acquisition.router import AcquisitionRouter
from ..evidence.repository import EvidenceRepository
from ..oa.operational_acceptance import run_operational_acceptance
from ..oa.youtube_fan_signal import run_youtube_fan_signal_oa
from ..social import features as feature_builder
from ..warehouse.repository import FestivalRepository

DEFAULT_PRIORITY = ("http", "monid", "apify", "youtube", "scrapling")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _entity_id_for_artist(artist_name: str) -> str:
    return artist_name.strip().lower().replace(" ", "-")


def build_router(evidence: EvidenceRepository, providers_csv: str) -> AcquisitionRouter:
    requested = [name.strip() for name in providers_csv.split(",") if name.strip()]
    providers = default_providers()
    available = {name: provider for name, provider in providers.items() if name in requested}
    router = AcquisitionRouter(
        providers=available,
        policy_gate=PolicyGate(),
        telemetry=evidence.ingest,
    )
    return router


def cmd_collect_social(args: argparse.Namespace) -> int:
    db_path = args.db
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        evidence = EvidenceRepository(repo.conn)
        router = build_router(evidence, args.providers)

        since = _parse_iso(args.since)
        cutoff = _parse_iso(args.cutoff)
        request = AcquisitionRequest.new(
            entity_id=_entity_id_for_artist(args.artist),
            entity_type="artist",
            platform=args.platform or "youtube",
            query=args.artist,
            market_id=args.market,
            start_time=since,
            end_time=cutoff,
            knowledge_cutoff=cutoff,
            max_records=args.max_records,
            preferred_providers=tuple(DEFAULT_PRIORITY),
            commercial_context="research",
        )

        result = router.route(request)
        raw_count = len(result.records) if result.is_success else 0

        canonical = evidence.query_observations(
            artist_id=request.entity_id,
            market_id=request.market_id,
            cutoff=cutoff,
        )

        print("=== FESTIVAL SIGNAL FABRIC — COLLECT SOCIAL ===")
        print(f"artist:            {args.artist}")
        print(f"entity_id:         {request.entity_id}")
        print(f"market:            {args.market or '(none)'}")
        print(f"knowledge cutoff:  {cutoff.isoformat() if cutoff else '(now)'}")
        print(f"time range:        {args.since or '(open)'} -> {args.cutoff or '(open)'}")
        print(f"status:            {result.status.value}")
        print(f"provider:          {result.provider}")
        print(f"cost_usd:          {result.cost_usd if result.cost_usd is not None else 'unknown'}")
        print(f"raw observations:  {raw_count}")
        print(f"canonical objects: {len(canonical)}")
        if result.provider_metadata:
            print(f"provider metadata: {result.provider_metadata}")
        if not result.is_success:
            print(f"error category:    {result.error_category}")
            print(f"detail:            {result.provider_metadata.get('detail') or result.provider_metadata.get('rationale') or ''}")
        sources = sorted({o["platform"] for o in canonical})
        providers_used = sorted(
            {
                row[0]
                for row in evidence.conn.execute(
                    "SELECT DISTINCT provider FROM acquisition.acquisition_runs WHERE request_id = ?",
                    [request.request_id],
                ).fetchall()
            }
        )
        print(f"sources:           {sources or '(none)'}")
        print(f"providers invoked: {providers_used}")
        print("NO RECOMMENDATION — evidence acquisition only.")
        return 0
    finally:
        repo.close()


def cmd_operational_acceptance(args: argparse.Namespace) -> int:
    """Run the live Signal Fabric operational acceptance (real public data).

    Live-only: performs real network fetches against a free, key-free,
    CC-licensed public source (Wikipedia) at $0 cost. Never uses fixtures.
    """
    import json
    import os

    db_path = args.db
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        evidence = EvidenceRepository(repo.conn)
        manifest = run_operational_acceptance(
            evidence,
            market=args.market,
            lookback_days=args.lookback_days,
            budget_usd=args.budget_usd,
            db_path=db_path,
        )

        manifest_path = args.manifest
        if manifest_path:
            parent = os.path.dirname(manifest_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, sort_keys=True)

        print("=== FESTIVAL SIGNAL FABRIC — OPERATIONAL ACCEPTANCE ===")
        print(f"market:              {manifest['market']}")
        print(f"budget_usd:          {manifest['budget_usd']}")
        print(f"selected artist:     {manifest['artist_selection']['selected_artist']}")
        print(f"provider readiness:  {manifest['provider_readiness']}")
        print(f"raw observations:    {manifest['observations']['raw_count']}")
        print(f"canonical objects:   {manifest['observations']['canonical_count']}")
        print(f"platforms:           {manifest['observations']['platforms']}")
        print(f"content roles:       {manifest['observations']['content_role_distribution']}")
        for key, value in manifest["statuses"].items():
            print(f"{key:34s} {value}")
        print(f"text sentiment:      {manifest['nlp']['text_sentiment']['distribution']}")
        print(f"fan sentiment:       {manifest['nlp']['fan_sentiment']['status']}")
        print(f"tweetnlp:            {manifest['nlp']['tweetnlp']['status']}")
        print(f"pit replay:          {manifest['pit_replay']['status']} "
              f"(scoped raw: {manifest['pit_replay'].get('scoped_raw_count', '?')})")
        print(f"cost_usd:            {manifest['cost_usd']}")
        if manifest_path:
            print(f"manifest:            {manifest_path}")
        print("NO RECOMMENDATION — live evidence acquisition only.")
        return 0
    finally:
        repo.close()


def cmd_youtube_fan_signal(args: argparse.Namespace) -> int:
    """Run the live YouTube fan-signal OA. Never prints secrets or raw UGC."""
    import json
    import os

    db_path = args.db
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        evidence = EvidenceRepository(repo.conn)
        manifest = run_youtube_fan_signal_oa(
            evidence,
            market=args.market,
            budget_usd=args.budget_usd,
            db_path=db_path,
            batch_universe=not args.no_batch,
            label_output=args.labels,
        )
        manifest_path = args.manifest
        if manifest_path:
            parent = os.path.dirname(manifest_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, sort_keys=True)

        print("=== FESTIVAL BLOOMBERG — REAL YOUTUBE FAN SIGNAL V1 ===")
        print(f"oa_run_id:           {manifest['oa_run_id']}")
        print(f"selected artist:     {manifest.get('selected_artist')}")
        cred = manifest.get("youtube_credential_status") or {}
        print(f"YOUTUBE_API_KEY:     {cred.get('configured')}")
        print(f"YOUTUBE_AUTH:        {cred.get('auth')}")
        print(f"quota_usage:         {manifest.get('quota_usage')}")
        print(f"cost_usd:            {manifest.get('cost_usd')}")
        for key, value in (manifest.get("statuses") or {}).items():
            print(f"{key:34s} {value}")
        if manifest_path:
            print(f"manifest:            {manifest_path}")
        print("NO RECOMMENDATION — observational evidence only.")
        return 0
    finally:
        repo.close()


def cmd_event_history_oa(args: argparse.Namespace) -> int:
    from ..oa.event_history import run_event_history_oa

    manifest = run_event_history_oa(
        db_path=args.db,
        youtube_db_path=args.youtube_db,
        manifest_path=args.manifest,
        market=args.market,
        budget_usd=args.budget_usd,
    )
    print("=== FESTIVAL BLOOMBERG — ARTIST × MARKET EVENT HISTORY V1 ===")
    print(f"oa_run_id:           {manifest['oa_run_id']}")
    print(f"TICKETMASTER_AUTH:   {manifest.get('ticketmaster_auth')}")
    print(f"SETLISTFM_AUTH:      {manifest.get('setlistfm_auth')}")
    print(f"cost_usd:            {manifest.get('cost_usd')}")
    for key, value in (manifest.get("statuses") or {}).items():
        print(f"{key:34s} {value}")
    print(f"manifest:            {args.manifest}")
    print("NO RECOMMENDATION — observational event history only.")
    return 0


def cmd_events_collect(args: argparse.Namespace) -> int:
    from ..oa.event_history import run_event_history_oa

    manifest = run_event_history_oa(
        db_path=args.db,
        manifest_path=args.manifest,
        market=args.market,
        artists=(args.artist,),
    )
    artist = (manifest.get("artists") or [{}])[0]
    print("=== EVENTS COLLECT ===")
    print(f"artist:              {args.artist}")
    print(f"market:              {args.market}")
    print(f"identity:            {artist.get('identity_resolution_status')}")
    print(f"historical:          {artist.get('historical_chicago_performances')}")
    print(f"upcoming:            {artist.get('upcoming_chicago_events')}")
    print(f"cost_usd:            0.0")
    return 0


def cmd_events_market_history(args: argparse.Namespace) -> int:
    from ..events.features import build_artist_market_vector
    from ..events.identity import canonical_artist_id
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        vector = build_artist_market_vector(
            events_repo,
            artist_id=canonical_artist_id(args.artist),
            market_id=args.market,
            as_of=datetime.now(timezone.utc),
        )
        print("=== MARKET HISTORY ===")
        for key, value in vector.items():
            if key == "supporting_observation_ids":
                print(f"supporting_ids:      {len(value)}")
                continue
            print(f"{key}: {value}")
        return 0
    finally:
        repo.close()


def cmd_events_market_history_batch(args: argparse.Namespace) -> int:
    from ..oa.event_history import run_event_history_oa

    manifest = run_event_history_oa(
        db_path=args.db,
        youtube_db_path=args.youtube_db,
        manifest_path=args.manifest,
        market=args.market,
    )
    print("=== MARKET HISTORY BATCH ===")
    print(f"artists:             {len(manifest.get('artists') or [])}")
    print(f"EVENT_HISTORY_V1:    {manifest['statuses'].get('EVENT_HISTORY_V1')}")
    print(f"cost_usd:            {manifest.get('cost_usd')}")
    return 0


def cmd_market_economics_oa(args: argparse.Namespace) -> int:
    from ..oa.market_economics import run_market_economics_oa

    manifest = run_market_economics_oa(
        db_path=args.db,
        manifest_path=args.manifest,
        market=args.market,
        budget_usd=args.budget_usd,
    )
    print("=== FESTIVAL BLOOMBERG — MARKET ECONOMICS EVIDENCE V1 ===")
    print(f"oa_run_id:           {manifest['oa_run_id']}")
    print(f"TICKETMASTER_AUTH:   {manifest.get('ticketmaster_auth')}")
    print(f"SEATGEEK_AUTH:       {manifest.get('seatgeek_auth')}")
    print(f"cost_usd:            {manifest.get('cost_usd')}")
    for key, value in (manifest.get("statuses") or {}).items():
        print(f"{key:34s} {value}")
    print(f"manifest:            {args.manifest}")
    print("NO RECOMMENDATION — capacity claims and ticket snapshots only.")
    return 0


def cmd_forward_history_oa(args: argparse.Namespace) -> int:
    from ..oa.forward_history import run_forward_history_oa

    manifest = run_forward_history_oa(
        db_path=args.db,
        manifest_path=args.manifest,
        market=args.market,
        budget_usd=args.budget_usd,
    )
    print("=== FESTIVAL BLOOMBERG — FORWARD MARKET HISTORY V1 ===")
    print(f"oa_run_id:           {manifest['oa_run_id']}")
    print(f"software_version:    {manifest.get('software_version')}")
    print(f"TICKETMASTER_AUTH:   {manifest.get('ticketmaster_auth')}")
    print(f"SEATGEEK_AUTH:       {manifest.get('seatgeek_auth')}")
    print(f"actual_cost_usd:     {manifest.get('actual_cost_usd')}")
    for key, value in (manifest.get("statuses") or {}).items():
        print(f"{key:34s} {value}")
    print(f"launchagent:         {manifest.get('launchagent')}")
    print(f"venue_audit:         {manifest.get('venue_audit')}")
    print(f"capacity_enrichment: {manifest.get('capacity_enrichment')}")
    print(f"two_snapshot_pit:    {manifest.get('two_snapshot_pit')}")
    print(f"tracked_events:      {manifest.get('tracked_events')}")
    print(f"collector_runs:      {manifest.get('collector_runs')}")
    print(f"manifest:            {args.manifest}")
    print("NO RECOMMENDATION — recurring collection and venue cleanup only.")
    return 0


def cmd_economics_snapshot_event(args: argparse.Namespace) -> int:
    from ..economics.collector import snapshot_event
    from ..economics.repository import EconomicsRepository
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        providers = tuple(p.strip() for p in args.providers.split(",") if p.strip())
        summary = snapshot_event(
            events_repo=events_repo,
            economics_repo=economics,
            canonical_event_id=args.event_id,
            providers=providers,
        )
        print("=== ECONOMICS SNAPSHOT EVENT ===")
        print(f"event_id:            {args.event_id}")
        print(f"events_requested:    {summary['events_requested']}")
        print(f"provider_obs:        {summary['provider_observations']}")
        print(f"price_snapshots:     {summary['price_snapshots']}")
        print(f"errors:              {summary['errors']}")
        print(f"actual_cost:         {summary['actual_cost']}")
        return 0
    finally:
        repo.close()


def cmd_economics_snapshot_upcoming(args: argparse.Namespace) -> int:
    from ..economics.collector import snapshot_upcoming
    from ..economics.repository import EconomicsRepository
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        providers = tuple(p.strip() for p in args.providers.split(",") if p.strip())
        summary = snapshot_upcoming(
            events_repo=events_repo,
            economics_repo=economics,
            market=args.market,
            providers=providers,
        )
        print("=== ECONOMICS SNAPSHOT UPCOMING ===")
        print(f"events_requested:    {summary['events_requested']}")
        print(f"provider_obs:        {summary['provider_observations']}")
        print(f"price_snapshots:     {summary['price_snapshots']}")
        print(f"errors:              {summary['errors']}")
        print(f"actual_cost:         {summary['actual_cost']}")
        return 0
    finally:
        repo.close()


def cmd_economics_tracked_events(args: argparse.Namespace) -> int:
    from ..economics.repository import EconomicsRepository
    from ..economics.tracking import TrackedEventRegistry
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        registry = TrackedEventRegistry(economics)
        
        events = registry.get_active_events()
        print("=== TRACKED EVENTS ===")
        print(f"active events:       {len(events)}")
        for event in events:
            print(f"  {event.canonical_event_id}: {event.tracking_status} (event: {event.event_time.isoformat()})")
        return 0
    finally:
        repo.close()


def cmd_economics_track_event(args: argparse.Namespace) -> int:
    from datetime import datetime
    from ..economics.repository import EconomicsRepository
    from ..economics.tracking import TrackedEventRegistry
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        registry = TrackedEventRegistry(economics)
        
        event_time = datetime.fromisoformat(args.event_time) if args.event_time else None
        if not event_time:
            # Try to get from events repo
            event_rows = [e for e in events_repo.query_events() if e.get("event_id") == args.event_id]
            if event_rows:
                event_time = datetime.fromisoformat(event_rows[0].get("local_date") or "")
        
        if not event_time:
            print("ERROR: event_time required or not found in events repo")
            return 1
        
        tracked = registry.track_event(
            canonical_event_id=args.event_id,
            artist_id=args.artist_id or "unknown",
            venue_id=args.venue_id or "unknown",
            event_time=event_time,
            providers=args.providers.split(",") if args.providers else None,
            reason=args.reason,
        )
        print("=== TRACK EVENT ===")
        print(f"event_id:            {tracked.canonical_event_id}")
        print(f"tracking_status:     {tracked.tracking_status}")
        print(f"tracking_started:    {tracked.tracking_started_at.isoformat()}")
        return 0
    finally:
        repo.close()


def cmd_economics_untrack_event(args: argparse.Namespace) -> int:
    from ..economics.repository import EconomicsRepository
    from ..economics.tracking import TrackedEventRegistry
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        registry = TrackedEventRegistry(economics)
        
        success = registry.untrack_event(args.event_id)
        print("=== UNTRACK EVENT ===")
        print(f"event_id:            {args.event_id}")
        print(f"removed:             {success}")
        return 0 if success else 1
    finally:
        repo.close()


def cmd_economics_snapshot_tracked(args: argparse.Namespace) -> int:
    import os
    from ..acquisition.contracts import utc_now
    from ..acquisition.providers.seatgeek import SeatGeekProvider
    from ..acquisition.providers.ticketmaster import TicketmasterProvider
    from ..economics.collector import LockHeldError, CollectorLock, snapshot_event
    from ..economics.repository import EconomicsRepository
    from ..economics.runlog import (
        EXIT_AUTH_FAILURE,
        EXIT_LOCK_HELD,
        EXIT_NO_ACTIVE_EVENTS,
        EXIT_SUCCESS,
        persist_run_to_db,
        PROVIDER_AUTH_FAILED,
        PROVIDER_AUTH_VALID,
        PROVIDER_NOT_CONFIGURED,
        RunLogger,
    )
    from ..economics.tracking import TrackedEventRegistry
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        registry = TrackedEventRegistry(economics)
        
        logger = RunLogger()
        lock_path = os.environ.get("FESTIVAL_BLOOMBERG_ECON_LOCK", "data/warehouse/economics.lock")
        
        try:
            with CollectorLock(lock_path):
                active_events = registry.get_active_events()
                
                if not active_events:
                    logger.log_error("No active tracked events")
                    logger.finish(EXIT_NO_ACTIVE_EVENTS)
                    persist_run_to_db(economics, logger)
                    return EXIT_NO_ACTIVE_EVENTS
                
                # Check provider auth
                tm = TicketmasterProvider()
                sg = SeatGeekProvider()
                
                if tm.configured():
                    logger.log_provider_status("ticketmaster", PROVIDER_AUTH_VALID)
                else:
                    logger.log_provider_status("ticketmaster", PROVIDER_NOT_CONFIGURED)
                    logger.log_error("Ticketmaster not configured")
                
                if sg.configured():
                    logger.log_provider_status("seatgeek", PROVIDER_AUTH_VALID)
                else:
                    logger.log_provider_status("seatgeek", PROVIDER_NOT_CONFIGURED)
                
                # Snapshot each tracked event
                for event in active_events:
                    logger.increment_events_attempted()
                    providers = []
                    if tm.configured():
                        providers.append("ticketmaster")
                    if sg.configured():
                        providers.append("seatgeek")
                    
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
                
                # Transition expired events
                registry.transition_expired_events()
                
                logger.finish(EXIT_SUCCESS)
                persist_run_to_db(economics, logger)
                
                print("=== SNAPSHOT TRACKED ===")
                print(f"run_id:              {logger.run_id}")
                print(f"events_attempted:    {logger.events_attempted}")
                print(f"events_succeeded:    {logger.events_succeeded}")
                print(f"snapshots_appended:  {logger.snapshots_appended}")
                print(f"provider_status:     {logger.provider_status}")
                print(f"exit_code:           {logger.exit_code}")
                return EXIT_SUCCESS
                
        except LockHeldError:
            logger.log_error("Collector lock already held")
            logger.finish(EXIT_LOCK_HELD)
            persist_run_to_db(economics, logger)
            return EXIT_LOCK_HELD
            
    finally:
        repo.close()


def cmd_economics_discover_upcoming(args: argparse.Namespace) -> int:
    from ..economics.repository import EconomicsRepository
    from ..economics.tracking import TrackedEventRegistry
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        registry = TrackedEventRegistry(economics)
        
        # Discover upcoming Chicago events for tracked artists
        # This is a placeholder - actual discovery logic would query Ticketmaster
        # for upcoming events matching tracked artist IDs in Chicago market
        
        print("=== DISCOVER UPCOMING ===")
        print("Discovery not yet implemented")
        return 0
    finally:
        repo.close()


def cmd_economics_venue_audit(args: argparse.Namespace) -> int:
    from ..economics.repository import EconomicsRepository
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        
        # Audit venue master
        venues = events_repo.query_venues()
        print("=== VENUE AUDIT ===")
        print(f"total venues:        {len(venues)}")
        
        # Count unique names
        unique_names = len(set(v.get("venue_name") for v in venues))
        print(f"unique names:         {unique_names}")
        
        # Check for duplicates
        name_counts = {}
        for v in venues:
            name = v.get("venue_name")
            name_counts[name] = name_counts.get(name, 0) + 1
        
        duplicates = {name: count for name, count in name_counts.items() if count > 1}
        if duplicates:
            print(f"duplicate names:      {duplicates}")
        else:
            print("duplicate names:      none")
        
        return 0
    finally:
        repo.close()


def cmd_economics_merge_united_center(args: argparse.Namespace) -> int:
    from ..economics.repository import EconomicsRepository
    from ..economics.venues import merge_united_center
    from ..events.repository import EventRepository

    repo = FestivalRepository(args.db) if args.db else FestivalRepository()
    try:
        EvidenceRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        economics = EconomicsRepository(repo.conn)
        
        result = merge_united_center(events_repo, economics)
        print("=== MERGE UNITED CENTER ===")
        print(f"status:              {result['status']}")
        if result['status'] == 'merged':
            print(f"canonical_venue_id:  {result['canonical_venue_id']}")
            print(f"source_venue_ids:    {result['source_venue_ids']}")
            print(f"merge_action_id:     {result['merge_action_id']}")
        return 0
    finally:
        repo.close()


def _backtest_db(args: argparse.Namespace):
    from ..warehouse.repository import FestivalRepository

    return FestivalRepository(args.db) if args.db else FestivalRepository(
        "data/warehouse/design_partner_retrospective.duckdb"
    )


def cmd_backtest_import(args: argparse.Namespace) -> int:
    from ..economics.partner_import import ingest_partner_files
    from ..economics.repository import EconomicsRepository
    from ..events.repository import EventRepository

    repo = _backtest_db(args)
    try:
        econ = EconomicsRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        file_paths = [p for p in args.events.split(",") if p.strip()]
        if not file_paths:
            print("ERROR: no input files provided")
            return 1
        report = ingest_partner_files(
            economics_repo=econ,
            file_paths=file_paths,
            customer_id=args.customer,
            dataset_id=args.dataset or f"ds_{args.customer}",
            sharing_policy=args.sharing_policy,
            events_repo=events_repo,
        )
        print("=== BACKTEST IMPORT ===")
        print(f"customer:            {args.customer}")
        print(f"dataset:             {report.dataset_id}")
        print(f"files read:          {report.files_read}")
        print(f"rows read:           {report.rows_read}")
        print(f"claims inserted:     {report.claims_inserted}")
        print(f"duplicates skipped:  {report.duplicates_skipped}")
        print(f"pii quarantined:     {report.pii_quarantined}")
        print(f"quality issues:      {len(report.quality_issues)}")
        print(f"reconciliation:      {len(report.reconciliation)}")
        print(f"events resolved:     {len(set(report.events_resolved))}")
        return 0
    finally:
        repo.close()


def cmd_backtest_audit(args: argparse.Namespace) -> int:
    from ..economics.audit_report import build_audit_report, write_audit_report
    from ..economics.repository import EconomicsRepository
    from ..events.repository import EventRepository
    from ..economics.retrospective import (
        CUTOFF_EVENT, DEFAULT_ALLOWED_PRIVATE_INPUTS, DEFAULT_HIDDEN_OUTCOMES, RetrospectiveStudy,
    )

    repo = _backtest_db(args)
    try:
        econ = EconomicsRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        dataset_row = econ.conn.execute(
            "SELECT customer_id FROM economics.customer_datasets WHERE dataset_id = ?",
            [args.dataset],
        ).fetchone()
        if not dataset_row:
            print(f"ERROR: dataset {args.dataset} not found — run `backtest import` first")
            return 1
        customer_id = dataset_row[0]
        event_ids = [
            r[0]
            for r in econ.conn.execute(
                "SELECT DISTINCT canonical_event_id FROM economics.event_outcome_claims WHERE source_name = ?",
                [args.dataset],
            ).fetchall()
        ]
        study = RetrospectiveStudy(
            study_id=f"audit_{args.dataset}",
            customer_id=customer_id,
            dataset_id=args.dataset,
            target="SCANNED_ATTENDANCE",
            decision_cutoff_type=CUTOFF_EVENT,
            hidden_outcomes=DEFAULT_HIDDEN_OUTCOMES,
            allowed_private_inputs=DEFAULT_ALLOWED_PRIVATE_INPUTS,
            event_ids=tuple(sorted(event_ids)),
        )
        econ.create_retrospective_study(study.to_dict())
        # Rebuild an ingestion-shaped report from what is already persisted.
        from ..economics.partner_import import PartnerIngestionReport

        ingestion = PartnerIngestionReport(dataset_id=args.dataset, customer_id=customer_id)
        audit = build_audit_report(ingestion=ingestion, economics_repo=econ, events_repo=events_repo, study=study)
        json_path = args.output_json or f"reports/promoter_data_audit_{args.dataset}.json"
        html_path = args.output_html or f"reports/promoter_data_audit_{args.dataset}.html"
        write_audit_report(audit, json_path=json_path, html_path=html_path)
        print("=== BACKTEST AUDIT ===")
        print(f"dataset:             {args.dataset}")
        print(f"events resolved:     {len(event_ids)}")
        print(f"baseline readiness:  {audit['baseline_readiness']['verdict']}")
        print(f"json report:         {json_path}")
        print(f"html report:         {html_path}")
        return 0
    finally:
        repo.close()


def cmd_backtest_create_study(args: argparse.Namespace) -> int:
    from ..economics.outcome_claims import validate_outcome_type
    from ..economics.repository import EconomicsRepository
    from ..economics.retrospective import (
        CUTOFF_TYPES, DEFAULT_ALLOWED_PRIVATE_INPUTS, DEFAULT_HIDDEN_OUTCOMES, RetrospectiveStudy,
    )

    repo = _backtest_db(args)
    try:
        econ = EconomicsRepository(repo.conn)
        target = args.target.strip().upper()
        try:
            validate_outcome_type(target)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
        cutoff = args.cutoff.upper()
        if cutoff not in CUTOFF_TYPES:
            print(f"ERROR: cutoff must be one of {sorted(CUTOFF_TYPES)}")
            return 1
        dataset_row = econ.conn.execute(
            "SELECT customer_id FROM economics.customer_datasets WHERE dataset_id = ?",
            [args.dataset],
        ).fetchone()
        if not dataset_row:
            print(f"ERROR: dataset {args.dataset} not found — run `backtest import` first")
            return 1
        event_ids = [
            r[0]
            for r in econ.conn.execute(
                "SELECT DISTINCT canonical_event_id FROM economics.event_outcome_claims WHERE source_name = ?",
                [args.dataset],
            ).fetchall()
        ]
        study = RetrospectiveStudy(
            study_id=args.study or f"study_{args.dataset}_{target.lower()}_{cutoff.lower()}",
            customer_id=dataset_row[0],
            dataset_id=args.dataset,
            target=target,
            decision_cutoff_type=cutoff,
            hidden_outcomes=DEFAULT_HIDDEN_OUTCOMES,
            allowed_private_inputs=DEFAULT_ALLOWED_PRIVATE_INPUTS,
            event_ids=tuple(sorted(event_ids)),
        )
        econ.create_retrospective_study(study.to_dict())
        print("=== BACKTEST CREATE STUDY ===")
        print(f"study:               {study.study_id}")
        print(f"target:              {study.target}")
        print(f"cutoff:              {study.decision_cutoff_type}")
        print(f"events:              {len(study.event_ids)}")
        return 0
    finally:
        repo.close()


def cmd_backtest_freeze(args: argparse.Namespace) -> int:
    from ..economics.repository import EconomicsRepository
    from ..economics.retrospective import STUDY_FROZEN, vault_outcomes, RetrospectiveStudy

    repo = _backtest_db(args)
    try:
        econ = EconomicsRepository(repo.conn)
        raw = econ.query_retrospective_study(args.study)
        if not raw:
            print(f"ERROR: study {args.study} not found — run `backtest create-study` first")
            return 1
        study = RetrospectiveStudy(
            study_id=raw["study_id"],
            customer_id=raw["customer_id"],
            dataset_id=raw["dataset_id"],
            target=raw["target"],
            decision_cutoff_type=raw["decision_cutoff_type"],
            hidden_outcomes=frozenset(raw["hidden_outcomes"]),
            allowed_private_inputs=frozenset(raw["allowed_private_inputs"]),
            event_ids=tuple(raw["event_ids"]),
        )
        vault = vault_outcomes(econ, study)
        econ.freeze_retrospective_study(args.study, status=STUDY_FROZEN)
        print("=== BACKTEST FREEZE ===")
        print(f"study:               {args.study}")
        print(f"status:              {STUDY_FROZEN}")
        print(f"claims vaulted:      {vault['claims_vaulted']}")
        return 0
    finally:
        repo.close()


def cmd_backtest_readiness(args: argparse.Namespace) -> int:
    from ..economics.repository import EconomicsRepository
    from ..economics.retrospective import (
        baseline_readiness, pit_reconstructability, training_row_eligibility, RetrospectiveStudy,
    )
    from ..events.repository import EventRepository

    repo = _backtest_db(args)
    try:
        econ = EconomicsRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        raw = econ.query_retrospective_study(args.study)
        if not raw:
            print(f"ERROR: study {args.study} not found")
            return 1
        study = RetrospectiveStudy(
            study_id=raw["study_id"],
            customer_id=raw["customer_id"],
            dataset_id=raw["dataset_id"],
            target=raw["target"],
            decision_cutoff_type=raw["decision_cutoff_type"],
            hidden_outcomes=frozenset(raw["hidden_outcomes"]),
            allowed_private_inputs=frozenset(raw["allowed_private_inputs"]),
            event_ids=tuple(raw["event_ids"]),
        )
        pit = pit_reconstructability(econ, study)
        eligibility = training_row_eligibility(econ, study)
        readiness = baseline_readiness(econ, events_repo, study)
        print("=== BACKTEST READINESS ===")
        print(f"study:               {args.study}")
        print(f"verdict:             {readiness['verdict']}")
        print(f"eligible rows:       {readiness['eligible_rows']} / {readiness['total_rows']}")
        print(f"pit complete:        {readiness['pit_complete']}")
        for reason in readiness["reasons"]:
            print(f"  - {reason}")
        return 0
    finally:
        repo.close()


def cmd_labels_export(args: argparse.Namespace) -> int:
    """Export a deterministic, unlabeled fan-text sample for human labeling."""
    import json

    from ..labels import export_fan_text

    db_path = args.db
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        evidence = EvidenceRepository(repo.conn)
        cutoff = _parse_iso(args.cutoff)
        rows = export_fan_text(
            evidence,
            artist_id=_entity_id_for_artist(args.artist),
            market_id=args.market,
            cutoff=cutoff,
            sample_size=args.sample_size,
        )

        if args.output and args.output != "-":
            import os

            parent = os.path.dirname(args.output)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, indent=2, ensure_ascii=False)
        else:
            print(json.dumps(rows, indent=2, ensure_ascii=False))

        print(f"=== LABELS EXPORT ===")
        print(f"artist:            {args.artist}")
        print(f"market:            {args.market or '(none)'}")
        print(f"sample size:       {len(rows)} (requested {args.sample_size})")
        print(f"manual fields:     all NULL (no labels fabricated)")
        return 0
    finally:
        repo.close()


def cmd_summarize_social(args: argparse.Namespace) -> int:
    db_path = args.db
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        evidence = EvidenceRepository(repo.conn)
        cutoff = _parse_iso(args.cutoff)
        features = feature_builder.build_artist_market_features(
            evidence,
            artist_id=_entity_id_for_artist(args.artist),
            market_id=args.market,
            cutoff=cutoff,
        )

        print("=== FESTIVAL SIGNAL FABRIC — SUMMARIZE SOCIAL ===")
        data = features.to_dict()
        for key, value in data.items():
            if key in ("source_observation_ids", "warnings"):
                continue
            print(f"{key:24s} {value}")
        if features.source_observation_ids:
            print(f"{'source_observation_ids':24s} {len(features.source_observation_ids)} ids")
        for warning in features.warnings:
            print(f"warning:           {warning}")
        model_versions = {
            row[0]
            for row in evidence.conn.execute(
                "SELECT DISTINCT model_version FROM acquisition.text_inferences"
            ).fetchall()
        }
        print(f"model versions:    {sorted(model_versions) or '(none yet)'}")
        print("NO RECOMMENDATION — feature summary only.")
        return 0
    finally:
        repo.close()


def cmd_partner_preview(args: argparse.Namespace) -> int:
    """One-command sanitized partner-file preview against an isolated DB.

    Never touches the canonical public warehouse. No private data leaves the
    local environment. Produces a structural summary + readiness tier — no
    predictions.
    """
    import json
    import os
    import tempfile
    from collections import Counter

    from ..economics.partner_import import ingest_partner_files
    from ..economics.partner_readiness import partner_readiness_tier, structural_coverage
    from ..economics.repository import EconomicsRepository
    from ..events.repository import EventRepository

    db_path = args.db or os.path.join(
        tempfile.gettempdir(), "festival_bloomberg_partner_preview.duckdb"
    )
    repo = FestivalRepository(db_path)
    try:
        econ = EconomicsRepository(repo.conn)
        events_repo = EventRepository(repo.conn)
        file_paths = [p for p in args.files.split(",") if p.strip()]
        if not file_paths:
            print("ERROR: no input files provided")
            return 1
        report = ingest_partner_files(
            economics_repo=econ,
            file_paths=file_paths,
            customer_id=args.customer,
            dataset_id=args.dataset or f"ds_{args.customer}",
            sharing_policy=args.sharing_policy,
            events_repo=events_repo,
        )
        coverage = structural_coverage(econ, events_repo)
        tier = partner_readiness_tier(coverage)

        summary = {
            "dataset_id": report.dataset_id,
            "customer_id": report.customer_id,
            "files_read": report.files_read,
            "rows_read": report.rows_read,
            "rows_rejected": len(report.errors),
            "quality_issues": len(report.quality_issues),
            "claims_inserted": report.claims_inserted,
            "duplicates_skipped": report.duplicates_skipped,
            "pii_quarantined": report.pii_quarantined,
            "mapping_summary": {
                f: dict(Counter(m["status"] for m in entries))
                for f, entries in report.mappings.items()
            },
            "structural_coverage": coverage,
            "readiness": tier,
            "sharing_policy": args.sharing_policy,
            "isolated_db": db_path,
            "no_predictions": True,
        }
        if args.output:
            parent = os.path.dirname(args.output)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, default=str)
            print(f"summary written: {args.output}")
        else:
            print(json.dumps(summary, indent=2, default=str))

        print("=== PARTNER PREVIEW ===")
        print(f"files read:        {report.files_read}")
        print(f"rows read:         {report.rows_read}")
        print(f"claims inserted:   {report.claims_inserted}")
        print(f"pii quarantined:   {report.pii_quarantined}")
        print(f"duplicates skipped:{report.duplicates_skipped}")
        print(f"readiness tier:    {tier['tier']}")
        print(f"isolated db:       {db_path}")
        print("NO PREDICTIONS — structural preview only.")
        return 0
    finally:
        repo.close()


def cmd_partner_value(args: argparse.Namespace) -> int:
    """Synthetic structural value curves (no prediction accuracy)."""
    import json

    from ..economics.partner_readiness import simulate_partner_value

    rows = simulate_partner_value()
    print(json.dumps(rows, indent=2, default=str))
    print("SYNTHETIC STRUCTURAL VALUE CURVES ONLY — no forecast accuracy simulated.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="festival")
    sub = parser.add_subparsers(dest="command", required=True)

    evidence = sub.add_parser("evidence", help="evidence acquisition and summaries")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)

    collect = evidence_sub.add_parser("collect-social", help="collect social evidence for an artist")
    collect.add_argument("--artist", required=True)
    collect.add_argument("--market", default=None)
    collect.add_argument("--since", default=None)
    collect.add_argument("--cutoff", default=None)
    collect.add_argument("--platform", default="youtube")
    collect.add_argument("--providers", default="youtube,monid,scrapling")
    collect.add_argument("--max-records", type=int, default=500)
    collect.add_argument("--db", default=None)
    collect.set_defaults(handler=cmd_collect_social)

    summarize = evidence_sub.add_parser("summarize-social", help="PIT-safe artist x market feature summary")
    summarize.add_argument("--artist", required=True)
    summarize.add_argument("--market", default=None)
    summarize.add_argument("--cutoff", default=None)
    summarize.add_argument("--db", default=None)
    summarize.set_defaults(handler=cmd_summarize_social)

    oa = sub.add_parser(
        "operational-acceptance-social",
        help="live Signal Fabric operational acceptance (real public data, $0)",
    )
    oa.add_argument("--market", default="Chicago, IL")
    oa.add_argument("--lookback-days", type=int, default=30)
    oa.add_argument("--budget-usd", type=float, default=0.0)
    oa.add_argument("--db", default=None)
    oa.add_argument("--manifest", default="reports/signal_fabric_live_oa.json")
    oa.set_defaults(handler=cmd_operational_acceptance)

    yt_oa = sub.add_parser(
        "operational-acceptance-youtube-fan-signal",
        help="live YouTube FAN_GENERATED OA (real comments, $0 monetary, quota counted)",
    )
    yt_oa.add_argument("--market", default="Chicago, IL")
    yt_oa.add_argument("--budget-usd", type=float, default=0.0)
    yt_oa.add_argument("--db", default=None)
    yt_oa.add_argument("--manifest", default="reports/youtube_fan_signal_oa.json")
    yt_oa.add_argument("--labels", default="reports/youtube_fan_labels.json")
    yt_oa.add_argument("--no-batch", action="store_true")
    yt_oa.set_defaults(handler=cmd_youtube_fan_signal)

    evt_oa = sub.add_parser(
        "operational-acceptance-event-history",
        help="live Ticketmaster + Setlist.fm artist×market event history OA ($0)",
    )
    evt_oa.add_argument("--market", default="Chicago, IL")
    evt_oa.add_argument("--budget-usd", type=float, default=0.0)
    evt_oa.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    evt_oa.add_argument("--youtube-db", default="data/warehouse/youtube_fan_signal_oa.duckdb")
    evt_oa.add_argument("--manifest", default="reports/artist_market_event_history_v1.json")
    evt_oa.set_defaults(handler=cmd_event_history_oa)

    events = sub.add_parser("events", help="artist × market event/performance history")
    events_sub = events.add_subparsers(dest="events_command", required=True)
    collect_ev = events_sub.add_parser("collect", help="collect Ticketmaster + Setlist.fm for one artist")
    collect_ev.add_argument("--artist", required=True)
    collect_ev.add_argument("--market", default="Chicago, IL")
    collect_ev.add_argument("--providers", default="ticketmaster,setlistfm")
    collect_ev.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    collect_ev.add_argument("--manifest", default="reports/artist_market_event_history_v1.json")
    collect_ev.set_defaults(handler=cmd_events_collect)

    hist = events_sub.add_parser("market-history", help="print stored artist×market event history")
    hist.add_argument("--artist", required=True)
    hist.add_argument("--market", default="Chicago, IL")
    hist.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    hist.set_defaults(handler=cmd_events_market_history)

    batch = events_sub.add_parser("market-history-batch", help="collect oa10 universe Chicago history")
    batch.add_argument("--universe", default="oa10")
    batch.add_argument("--market", default="Chicago, IL")
    batch.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    batch.add_argument("--youtube-db", default="data/warehouse/youtube_fan_signal_oa.duckdb")
    batch.add_argument("--manifest", default="reports/artist_market_event_history_v1.json")
    batch.set_defaults(handler=cmd_events_market_history_batch)

    econ_oa = sub.add_parser(
        "operational-acceptance-market-economics",
        help="live venue capacity + ticket snapshots + outcome labels OA ($0)",
    )
    econ_oa.add_argument("--market", default="Chicago, IL")
    econ_oa.add_argument("--budget-usd", type=float, default=0.0)
    econ_oa.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    econ_oa.add_argument("--manifest", default="reports/market_economics_evidence_v1.json")
    econ_oa.set_defaults(handler=cmd_market_economics_oa)

    fh_oa = sub.add_parser(
        "operational-acceptance-forward-history",
        help="recurring collection + venue cleanup + capacity enrichment OA ($0)",
    )
    fh_oa.add_argument("--market", default="Chicago, IL")
    fh_oa.add_argument("--budget-usd", type=float, default=0.0)
    fh_oa.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    fh_oa.add_argument("--manifest", default="reports/forward_market_history_v1.json")
    fh_oa.set_defaults(handler=cmd_forward_history_oa)

    economics = sub.add_parser("economics", help="venue capacity claims and ticket-market snapshots")
    economics_sub = economics.add_subparsers(dest="economics_command", required=True)
    snap_event = economics_sub.add_parser("snapshot-event", help="append-only snapshot for one canonical event")
    snap_event.add_argument("--event-id", required=True)
    snap_event.add_argument("--providers", default="ticketmaster,seatgeek")
    snap_event.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    snap_event.set_defaults(handler=cmd_economics_snapshot_event)

    snap_up = economics_sub.add_parser("snapshot-upcoming", help="snapshot upcoming events in a market")
    snap_up.add_argument("--market", default="Chicago, IL")
    snap_up.add_argument("--providers", default="ticketmaster,seatgeek")
    snap_up.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    snap_up.set_defaults(handler=cmd_economics_snapshot_upcoming)

    tracked = economics_sub.add_parser("tracked-events", help="list tracked events")
    tracked.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    tracked.set_defaults(handler=cmd_economics_tracked_events)

    track = economics_sub.add_parser("track-event", help="add event to tracking registry")
    track.add_argument("--event-id", required=True)
    track.add_argument("--artist-id", default=None)
    track.add_argument("--venue-id", default=None)
    track.add_argument("--event-time", default=None)
    track.add_argument("--providers", default=None)
    track.add_argument("--reason", default=None)
    track.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    track.set_defaults(handler=cmd_economics_track_event)

    untrack = economics_sub.add_parser("untrack-event", help="remove event from tracking registry")
    untrack.add_argument("--event-id", required=True)
    untrack.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    untrack.set_defaults(handler=cmd_economics_untrack_event)

    snap_tracked = economics_sub.add_parser("snapshot-tracked", help="snapshot all tracked events")
    snap_tracked.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    snap_tracked.set_defaults(handler=cmd_economics_snapshot_tracked)

    discover = economics_sub.add_parser("discover-upcoming", help="discover upcoming events for tracked artists")
    discover.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    discover.set_defaults(handler=cmd_economics_discover_upcoming)

    venue_audit = economics_sub.add_parser("venue-audit", help="audit venue master for duplicates")
    venue_audit.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    venue_audit.set_defaults(handler=cmd_economics_venue_audit)

    merge_uc = economics_sub.add_parser("merge-united-center", help="merge United Center duplicate venues")
    merge_uc.add_argument("--db", default="data/warehouse/artist_market_event_history.duckdb")
    merge_uc.set_defaults(handler=cmd_economics_merge_united_center)

    backtest = sub.add_parser("backtest", help="design-partner blind retrospective workflow")
    backtest_sub = backtest.add_subparsers(dest="backtest_command", required=True)

    bt_import = backtest_sub.add_parser("import", help="import a customer historical shows file (csv/tsv/xlsx)")
    bt_import.add_argument("--customer", required=True)
    bt_import.add_argument("--events", required=True, help="comma-separated paths to csv/tsv/xlsx files")
    bt_import.add_argument("--dataset", default=None)
    bt_import.add_argument("--sharing-policy", default="PRIVATE_ONLY",
                           choices=["PRIVATE_ONLY", "ANONYMIZED_POOL_OPT_IN", "AGGREGATE_BENCHMARK_OPT_IN"])
    bt_import.add_argument("--db", default=None)
    bt_import.set_defaults(handler=cmd_backtest_import)

    bt_audit = backtest_sub.add_parser("audit", help="generate a promoter data audit report for a dataset")
    bt_audit.add_argument("--dataset", required=True)
    bt_audit.add_argument("--output-json", default=None)
    bt_audit.add_argument("--output-html", default=None)
    bt_audit.add_argument("--db", default=None)
    bt_audit.set_defaults(handler=cmd_backtest_audit)

    bt_study = backtest_sub.add_parser("create-study", help="create a retrospective study")
    bt_study.add_argument("--dataset", required=True)
    bt_study.add_argument("--target", required=True)
    bt_study.add_argument("--cutoff", required=True,
                          choices=["booking", "announcement", "onsale", "event"])
    bt_study.add_argument("--study", default=None)
    bt_study.add_argument("--db", default=None)
    bt_study.set_defaults(handler=cmd_backtest_create_study)

    bt_freeze = backtest_sub.add_parser("freeze", help="freeze a study and vault its hidden outcomes")
    bt_freeze.add_argument("--study", required=True)
    bt_freeze.add_argument("--db", default=None)
    bt_freeze.set_defaults(handler=cmd_backtest_freeze)

    bt_readiness = backtest_sub.add_parser("readiness", help="report a study's baseline readiness (no model)")
    bt_readiness.add_argument("--study", required=True)
    bt_readiness.add_argument("--db", default=None)
    bt_readiness.set_defaults(handler=cmd_backtest_readiness)

    partner = sub.add_parser("partner", help="design-partner data activation (isolated, no public warehouse)")
    partner_sub = partner.add_subparsers(dest="partner_command", required=True)
    preview = partner_sub.add_parser("preview", help="one-command sanitized partner-file preview")
    preview.add_argument("--files", required=True, help="comma-separated csv/tsv/xlsx paths")
    preview.add_argument("--customer", default="preview_promoter")
    preview.add_argument("--dataset", default=None)
    preview.add_argument("--sharing-policy", default="PRIVATE_ONLY",
                         choices=["PRIVATE_ONLY", "ANONYMIZED_POOL_OPT_IN", "AGGREGATE_BENCHMARK_OPT_IN"])
    preview.add_argument("--db", default=None)
    preview.add_argument("--output", default=None)
    preview.set_defaults(handler=cmd_partner_preview)

    pvalue = partner_sub.add_parser("value", help="synthetic structural value curves")
    pvalue.set_defaults(handler=cmd_partner_value)

    labels = sub.add_parser("labels", help="deterministic human-labeling exports")
    labels_sub = labels.add_subparsers(dest="labels_command", required=True)
    export = labels_sub.add_parser("export-fan-text", help="export unlabeled fan text sample")
    export.add_argument("--artist", required=True)
    export.add_argument("--market", default=None)
    export.add_argument("--sample-size", type=int, default=100)
    export.add_argument("--cutoff", default=None)
    export.add_argument("--db", default=None)
    export.add_argument("--output", default="-")
    export.set_defaults(handler=cmd_labels_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
