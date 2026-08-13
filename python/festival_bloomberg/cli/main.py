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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
