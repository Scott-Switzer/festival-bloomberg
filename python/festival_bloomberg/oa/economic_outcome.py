"""Economic Outcome Acquisition V1 — live operational acceptance.

Runs the curated public-source acquirer against real Wikipedia festival
articles, seeds canonical festival events, exercises a bounded Common Crawl
capture-metadata lookup, and computes the honest economic coverage + model
readiness verdict.

This driver deliberately does NOT fabricate attendance/tickets/gross: it
reports exactly what free public sources yield, and measures how far that is
from a model-ready corpus.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.providers.commoncrawl import lookup_capture_offset
from ..acquisition.transport import UrllibTransport
from ..economics import laboratory
from ..economics.laboratory import economic_coverage_report
from ..economics.outcome_acquisition import (
    CURATED_SOURCES,
    EconomicOutcomeAcquirer,
)
from ..economics.readiness import evaluate_model_readiness
from ..economics.repository import EconomicsRepository
from ..events.repository import EventRepository
from ..warehouse.repository import FestivalRepository

DEFAULT_HISTORICAL_DB = (
    Path(__file__).resolve().parents[3] / "data" / "warehouse" / "artist_market_event_history.duckdb"
)

# Bounded Common Crawl probe URLs (capture metadata only; no WARC download).
CC_PROBE_URLS = [
    "https://www.lollapalooza.com/",
    "https://pitchforkmusicfestival.com/",
]


def seed_festival_events(events_repo) -> int:
    """Idempotently seed canonical festival events for the curated sources."""
    seeded = 0
    for src in CURATED_SOURCES:
        existing = events_repo.conn.execute(
            "SELECT event_id FROM events.events WHERE event_id = ?", [src.event_id]
        ).fetchone()
        if existing:
            continue
        venue_id = content_hash_of({"venue": src.venue_name})[:8]
        now = utc_now().isoformat()
        events_repo.conn.execute(
            """
            INSERT INTO events.events
                (event_id, event_type, event_name, event_time, local_date, venue_id,
                 venue_name, market_id, city, state, country, event_status,
                 provider_support_count, first_observed_at, last_observed_at,
                 knowledge_time, match_gate, supporting_observation_ids)
            VALUES (?, 'FESTIVAL', ?, NULL, NULL, ?, ?, ?, 'Chicago', 'Illinois', 'US',
                    'completed', 1, ?, ?, ?, 'UNMATCHED', ?)
            """,
            [
                src.event_id,
                src.event_label,
                venue_id,
                src.venue_name,
                src.market,
                now,
                now,
                now,
                json.dumps([src.event_id]),
            ],
        )
        events_repo.conn.execute(
            """
            INSERT INTO events.artist_event_relations
                (relation_id, artist_id, event_id, role, knowledge_time, supporting_observation_ids)
            VALUES (?, 'various-artists', ?, 'festival', ?, ?)
            """,
            [f"aer_{src.event_id}", src.event_id, now, json.dumps([src.event_id])],
        )
        events_repo.conn.commit()
        seeded += 1
    return seeded


def run_economic_outcome_oa(
    db_path: str | Path = DEFAULT_HISTORICAL_DB,
    *,
    report_path: str | Path | None = None,
    commoncrawl_probe: bool = True,
) -> dict[str, Any]:
    repo = FestivalRepository(str(db_path))
    try:
        events_repo = EventRepository(repo.conn)
        econ = EconomicsRepository(repo.conn)

        seeded = seed_festival_events(events_repo)
        acquirer = EconomicOutcomeAcquirer()
        acquisition = acquirer.run(econ)

        cc_probe: dict[str, Any] = {"attempted": 0, "captures_found": 0, "results": []}
        if commoncrawl_probe:
            transport = UrllibTransport()
            for url in CC_PROBE_URLS:
                try:
                    capture = lookup_capture_offset(transport, url)
                    cc_probe["attempted"] += 1
                    if capture:
                        cc_probe["captures_found"] += 1
                        cc_probe["results"].append({
                            "url": url,
                            "crawl_id": capture["crawl_id"],
                            "capture_timestamp": capture["timestamp"],
                            "warc_filename": capture["filename"],
                            "offset": capture["offset"],
                            "length": capture["length"],
                            "digest": capture["digest"],
                        })
                except Exception as exc:  # noqa: BLE001 - bounded live probe
                    cc_probe["results"].append({"url": url, "error": str(exc)})

        coverage = economic_coverage_report(econ, events_repo)
        readiness = evaluate_model_readiness(econ, events_repo)
        claims = econ.query_outcome_claims()
        by_type = Counter(c["outcome_type"] for c in claims)
        by_rights = Counter(c["rights_status"] for c in claims)
        by_grade = Counter(c["source_quality"] for c in claims)

        manifest: dict[str, Any] = {
            "software_version": "economic_outcome_acquisition_v1",
            "generated_at": utc_now().isoformat(),
            "festival_events_seeded": seeded,
            "sources_attempted": acquisition["sources_attempted"],
            "sources_fetched": acquisition["sources_fetched"],
            "sources_failed": acquisition["sources_failed"],
            "claims_inserted": acquisition["claims_inserted"],
            "events_searched": coverage["events_searched"],
            "events_with_economic_claims": len({c["canonical_event_id"] for c in claims}),
            "claims_by_type": dict(by_type),
            "claims_by_rights": dict(by_rights),
            "claims_by_grade": dict(by_grade),
            "events_with_attendance": coverage["events_with_attendance"],
            "events_with_tickets_sold": coverage["events_with_tickets_sold"],
            "events_with_sold_out": coverage["events_with_sold_out"],
            "events_with_gross": coverage["events_with_gross"],
            "events_with_event_capacity": coverage["events_with_event_capacity"],
            "events_with_guarantee": coverage["events_with_guarantee"],
            "events_with_promoter_contribution": coverage["events_with_promoter_contribution"],
            "research_commercial_split": coverage["research_commercial_split"],
            "scorecard": coverage["scorecard"],
            "commoncrawl_probe": cc_probe,
            "model_readiness": readiness,
            "monid_usage": "NONE",
            "apify_usage": "NONE",
            "provider_cost_usd": 0.0,
        }

        if report_path is not None:
            path = Path(report_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_economic_outcome_oa(
        report_path="reports/economic_outcome_acquisition_v1.json",
    )
    print(json.dumps(result, indent=2, default=str))
