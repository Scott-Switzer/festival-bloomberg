"""Public Boxscore Research Corpus V2 — live operational acceptance.

Scales the V1 corpus across verified Pollstar Hot Tickets archives and
discovered Touring Data tour pages; resolves cross-source engagement identity;
measures source agreement; audits diversity/concentration/selection/temporal
coverage; computes a model-free baseline-readiness verdict; and writes
deterministic, leakage-safe split manifests.

No model is fitted. All sources remain RESEARCH_ONLY / TERMS_REVIEW_REQUIRED.
The forward Ticket Count watchlist is Patreon-gated and is reported honestly
as NOT_AVAILABLE (never bypassed). Bounded, $0.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.transport import UrllibTransport
from ..economics.repository import EconomicsRepository
from ..research.acquisition import (
    BOXSCORE_SOURCES,
    POLLSTAR_ARCHIVE,
    discover_touring_data_pages,
    parse_source_with_meta,
)
from ..research.audit import (
    baseline_readiness,
    build_research_splits,
    corpus_diversity,
    market_coverage,
    selection_metadata,
    target_readiness,
    temporal_coverage,
    venue_size_bins,
)
from ..research.boxscore import SOURCE_BILLBOARD, SOURCE_TOURING_DATA
from ..research.inventory import TICKET_COUNT_ACCESS, TICKET_COUNT_SOURCE
from ..research.repository import ResearchRepository
from ..research.resolution import cross_source_agreement, resolve_engagements
from ..warehouse.repository import FestivalRepository

RESEARCH_DB = (
    Path(__file__).resolve().parents[3] / "data" / "warehouse" / "boxoffice_research_v2.duckdb"
)

_MAX_TOURING_PAGES = 15

_URL_DATE = re.compile(r"/(20\d{2})/(\d{2})/(\d{2})/")


def _url_date(url: str) -> str | None:
    m = _URL_DATE.search(url)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _fetch(transport: UrllibTransport, url: str) -> tuple[int, str]:
    response = transport.request("GET", url, timeout_seconds=45)
    return response.status, response.body.decode("utf-8", errors="replace")


def run_boxscore_v2_oa(
    db_path: str | Path = RESEARCH_DB,
    *,
    report_path: str | Path = "reports/public_boxscore_research_corpus_v2.json",
) -> dict[str, Any]:
    repo = FestivalRepository(str(db_path))
    try:
        research = ResearchRepository(repo.conn)
        econ = EconomicsRepository(repo.conn)
        transport = UrllibTransport()

        ingestion: dict[str, Any] = {
            "pages_attempted": 0,
            "pages_fetched": 0,
            "pages_failed": 0,
            "engagements_inserted": 0,
            "skipped": {},
            "details": [],
        }
        skipped_agg: Counter = Counter()

        def ingest(source: str, url: str, content: str, publication_date: str | None, **kwargs: Any) -> None:
            ingestion["pages_attempted"] += 1
            parsed, meta = parse_source_with_meta(source, content, source_url=url, **kwargs)
            skipped_agg.update(meta.get("skipped", {}))
            inserted = 0
            for engagement in parsed:
                if research.insert_engagement(engagement):
                    inserted += 1
            ingestion["engagements_inserted"] += inserted
            research.insert_source({
                "source_id": f"src_{content_hash_of({'source': source, 'url': url})[:20]}",
                "reporting_source": source,
                "source_url": url,
                "publication_date": publication_date,
                "retrieved_at": utc_now().isoformat(),
                "content_hash": content_hash_of(content),
                "record_count": len(parsed),
                "rights_status": "RESEARCH_ONLY" if source != SOURCE_TOURING_DATA else "TERMS_REVIEW_REQUIRED",
                "commercial_use_status": "RESEARCH_ONLY" if source != SOURCE_TOURING_DATA else "TERMS_REVIEW_REQUIRED",
                **selection_metadata(source),
            })
            ingestion["details"].append({
                "source": source, "url": url, "parsed": len(parsed), "inserted": inserted,
                "publication_date": publication_date,
            })

        # 1. Billboard archived Current Boxscore (single page).
        #    (Pollstar pages are all covered by POLLSTAR_ARCHIVE below, so the
        #     April-18 page is not fetched twice.)
        for source, url, _kind in BOXSCORE_SOURCES:
            if source != SOURCE_BILLBOARD:
                continue
            try:
                status, text = _fetch(transport, url)
                if status != 200:
                    ingestion["pages_failed"] += 1
                    ingestion["details"].append({"source": source, "url": url, "status": status})
                    continue
                ingestion["pages_fetched"] += 1
                ingest(source, url, text, None)
            except Exception as exc:  # noqa: BLE001 - bounded live fetch
                ingestion["pages_failed"] += 1
                ingestion["details"].append({"source": source, "url": url, "error": str(exc)})

        # 2. Pollstar Hot Tickets archives (verified list).
        for url, pub_date in POLLSTAR_ARCHIVE:
            try:
                status, text = _fetch(transport, url)
                if status != 200:
                    ingestion["pages_failed"] += 1
                    ingestion["details"].append({"source": "pollstar", "url": url, "status": status})
                    continue
                ingestion["pages_fetched"] += 1
                ingest("pollstar", url, text, pub_date)
            except Exception as exc:  # noqa: BLE001
                ingestion["pages_failed"] += 1
                ingestion["details"].append({"source": "pollstar", "url": url, "error": str(exc)})

        # 3. Touring Data tour pages (discovered from the Data category).
        tour_pages = discover_touring_data_pages(transport, max_pages=_MAX_TOURING_PAGES)
        for url, slug in tour_pages:
            try:
                status, text = _fetch(transport, url)
                if status != 200:
                    ingestion["pages_failed"] += 1
                    ingestion["details"].append({"source": SOURCE_TOURING_DATA, "url": url, "status": status})
                    continue
                ingestion["pages_fetched"] += 1
                ingest(SOURCE_TOURING_DATA, url, text, _url_date(url), tour=slug)
            except Exception as exc:  # noqa: BLE001
                ingestion["pages_failed"] += 1
                ingestion["details"].append({"source": SOURCE_TOURING_DATA, "url": url, "error": str(exc)})

        ingestion["skipped"] = dict(skipped_agg)

        # Resolve cross-source engagement identity + agreement.
        engagements = research.query_engagements()
        canonicals, resolutions, resolution_stats = resolve_engagements(engagements)
        for canonical in canonicals:
            research.insert_canonical_engagement(canonical)
        for resolution in resolutions:
            research.insert_resolution(resolution)
        agreement = cross_source_agreement(engagements, resolutions, canonicals)

        # Diversity / selection / temporal / market / readiness audits.
        diversity = corpus_diversity(engagements)
        bins = venue_size_bins(engagements)
        temporal = temporal_coverage(engagements)
        markets = market_coverage(engagements)
        targets = target_readiness(engagements)
        readiness = baseline_readiness(engagements)

        # Deterministic split manifests.
        split_rows, split_summary = build_research_splits(canonicals)
        for row in split_rows:
            research.insert_split(row)

        # Promote single-show reported engagements to the outcome claim ledger
        # (research-only), preserving each source's raw claim independently.
        promotion = research.promote_single_show_engagements(econ)
        claims = econ.query_outcome_claims()
        claim_types = Counter(c["outcome_type"] for c in claims)
        claim_rights = Counter(c["rights_status"] for c in claims)

        # Forward ticket-inventory watchlist: Patreon-gated -> honest NOT_AVAILABLE.
        inventory = {
            "source": TICKET_COUNT_SOURCE,
            "access_status": TICKET_COUNT_ACCESS,
            "snapshots": 0,
            "note": "Ticket Count is subscription-gated; no bypass attempted.",
        }

        manifest: dict[str, Any] = {
            "software_version": "public_boxscore_research_corpus_v2",
            "generated_at": utc_now().isoformat(),
            "ingestion": ingestion,
            "raw_engagements": len(engagements),
            "resolution": resolution_stats,
            "cross_source_agreement": agreement,
            "diversity": diversity,
            "venue_size_bins": bins,
            "selection_metadata": {
                s: selection_metadata(s)
                for s in sorted({e.get("reporting_source") for e in engagements if e.get("reporting_source")})
            },
            "temporal_coverage": temporal,
            "market_coverage": markets,
            "target_coverage": targets,
            "baseline_readiness": readiness,
            "research_splits": split_summary,
            "promotion": {
                k: v for k, v in promotion.items() if k != "claim_ids"
            },
            "claims_by_type": dict(claim_types),
            "claims_by_rights": dict(claim_rights),
            "forward_inventory_watchlist": inventory,
            "rights": {
                "research_corpus": len(engagements),
                "commercial_eligible": 0,
                "verdict": "FAIL_CLOSED",
            },
            "provider_cost_usd": 0.0,
            "monid_usage": "NONE",
            "apify_usage": "NONE",
        }

        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_boxscore_v2_oa()
    print(json.dumps(result, indent=2, default=str))
