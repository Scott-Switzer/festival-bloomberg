"""Event discovery rail — ingest external event records into the observation contract.

For each source (Eventbrite, DICE, Songkick, etc.), this module:
1. Fetches records via Apify direct
2. Normalizes into ObservationRecords
3. Ingests into acquisition.external_event_observations
4. Detects changes against prior observations
5. Updates coverage snapshots
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import (
    ObservationRecord,
    ingest_observations_batch,
    detect_changes,
)

# Import the direct Apify client
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from festival_bloomberg.acquisition.apify_direct import run_actor, inspect_actor
from festival_bloomberg.localenv import load_local_env


SOURCE_MAP = {
    "songkick": {
        "platform": "songkick.com",
        "actor": "gio21~songkick-events-scraper",
    },
    "bandsintown": {
        "platform": "bandsintown.com",
        "actor": "automation-lab~bandsintown-events-scraper",
    },
    "eventbrite": {
        "platform": "eventbrite.com",
        "actor": "scrapesage~eventbrite-scraper",
    },
    "dice": {
        "platform": "dice.fm",
        "actor": "hoholabs~dicefm-scraper",
    },
    "resident_advisor": {
        "platform": "residentadvisor.net",
        "actor": "crawlerbros~resident-advisor-scraper",
    },
    "allevents": {
        "platform": "allevents.in",
        "actor": "solidcode~allevents-scraper",
    },
    "fever": {
        "platform": "feverup.com",
        "actor": "hoholabs~feverup-scraper",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_to_observation(record: dict, source: str, provider: str, actor: str) -> ObservationRecord:
    """Normalize a raw scraper record into a common observation."""
    
    # Extract core identity fields (varies by source)
    record_id = (
        record.get("eventId") or record.get("id") or 
        record.get("url") or ""
    )
    if isinstance(record_id, (int, float)):
        record_id = str(int(record_id))
    
    event_name = record.get("name") or record.get("title") or record.get("eventName") or ""
    
    # Date extraction
    event_time = (
        record.get("startDate") or record.get("date") or 
        record.get("datetime") or record.get("startDateLocal") or None
    )
    
    # Venue
    venue = record.get("venueName") or record.get("venue_name") or record.get("venue") or None
    if isinstance(venue, dict):
        venue = venue.get("name", "")
    
    # Artist
    artist = record.get("artistName") or record.get("artist_name") or None
    if not artist:
        performers = record.get("performers") or []
        if isinstance(performers, list) and performers:
            p0 = performers[0]
            artist = p0.get("name", "") if isinstance(p0, dict) else str(p0)
    
    # Price detection
    price_min = record.get("price_min") or record.get("minTicketPrice") or record.get("minPrice")
    price_max = record.get("price_max") or record.get("maxTicketPrice") or record.get("maxPrice")
    currency = record.get("currency") or record.get("price_currency") or None
    
    # PIT fields
    pub_time = record.get("publishedDate") or record.get("announcement_date") or None
    onsale = record.get("onsale_start") or record.get("sale_start_date") or None
    
    now = _now()
    
    return ObservationRecord(
        source_platform=source,
        acquisition_provider=provider,
        actor_or_endpoint=actor,
        source_record_id=str(record_id),
        observation_type="EVENT_DISCOVERY",
        observation_category="PRIMARY",
        raw_payload=record,
        observed_at=now,
        retrieved_at=record.get("scrapedAt") or now,
        source_publication_time=pub_time,
        onsale_time=onsale,
        event_time=event_time,
        knowledge_time=pub_time or now,
        normalized_fields={
            "event_name": event_name,
            "artist_name": artist,
            "venue_name": venue,
            "price_min": price_min,
            "price_max": price_max,
            "currency": currency,
        },
        rights_status="TERMS_REVIEW_REQUIRED",
        commercial_use_status="PROTOTYPE_ONLY",
    )


def ingest_source(conn, source_key: str, input_body: dict[str, Any],
                  max_polls: int = 30, poll_interval: float = 2.0,
                  timeout: int = 120) -> dict[str, Any]:
    """Run one source's scraper and ingest results into the observation contract.
    
    Returns: { source, records_fetched, observations_ingested, cost_usd, changes_detected }
    """
    load_local_env()
    src = SOURCE_MAP.get(source_key)
    if not src:
        return {"status": "UNKNOWN_SOURCE", "source": source_key}
    
    platform = src["platform"]
    actor = src["actor"]
    
    # Execute scraper
    result = run_actor(actor, input_body, max_polls=max_polls,
                       poll_interval=poll_interval, timeout=timeout)
    
    if result.get("status") not in ("COMPLETED", "SUCCEEDED"):
        return {
            "status": "SCRAPE_FAILED",
            "source": source_key,
            "error": result.get("error") or result.get("final_state", "UNKNOWN"),
        }
    
    records = result.get("records", [])
    if not records:
        return {
            "status": "NO_RECORDS",
            "source": source_key,
            "cost_usd": result.get("cost_usd"),
        }
    
    # Normalize and ingest
    observations = [
        normalize_to_observation(rec, platform, "apify", actor)
        for rec in records
    ]
    
    oids = ingest_observations_batch(conn, observations)
    
    # Detect changes
    changes = detect_changes(conn, platform)
    
    return {
        "status": "INGESTED",
        "source": source_key,
        "platform": platform,
        "actor": actor,
        "records_fetched": len(records),
        "observations_ingested": len(oids),
        "cost_usd": result.get("cost_usd"),
        "changes_detected": len(changes),
        "run_id": result.get("run_id"),
    }


def observe_universe(conn, universe: list[dict[str, Any]], sources: list[str] | None = None):
    """Run one observation wave over a watch universe.
    
    universe: list of { source_key, input_body } dicts
    sources: optional filter to specific source_keys
    
    Returns aggregated results.
    """
    if sources is None:
        sources = list(SOURCE_MAP.keys())
    
    results = []
    total_cost = 0
    total_records = 0
    total_ingested = 0
    total_changes = 0
    
    for item in universe:
        sk = item["source_key"]
        if sk not in sources:
            continue
        
        r = ingest_source(conn, sk, item["input_body"])
        results.append(r)
        
        if r.get("status") == "INGESTED":
            total_cost += float(r.get("cost_usd") or 0)
            total_records += r.get("records_fetched", 0)
            total_ingested += r.get("observations_ingested", 0)
            total_changes += r.get("changes_detected", 0)
    
    return {
        "status": "WAVE_COMPLETE",
        "sources_targeted": len(universe),
        "sources_ingested": sum(1 for r in results if r.get("status") == "INGESTED"),
        "sources_failed": sum(1 for r in results if r.get("status") in ("SCRAPE_FAILED", "NO_RECORDS")),
        "total_records": total_records,
        "total_observations": total_ingested,
        "total_changes": total_changes,
        "total_cost_usd": total_cost,
        "results": results,
    }