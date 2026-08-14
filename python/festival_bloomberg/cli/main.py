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
