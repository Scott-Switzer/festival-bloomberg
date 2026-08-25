#!/usr/bin/env python3
"""PR #44 Source Bakeoff — Direct Apify event source evaluation.

CONSTRAINTS:
  - Total budget: $2.00
  - Per-source ceiling: $0.30
  - Single APIFY_TOKEN (no multi-key rotation)
  - Schema inspection first (free), execution only if compatible
  - Real dataset analysis, not actor metadata analysis

Workflow:
  1. Schema-only inspection of all candidates (free)
  2. Bounded execution of P0 sources (Songkick, Bandsintown, RA, Eventbrite, DICE)
  3. Secondary discovery if budget remains
  4. Ticketmaster overlap analysis
  5. Acceptance matrix
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from python.festival_bloomberg.acquisition.apify_direct import (
    is_configured,
    inspect_actor,
    run_actor,
)
from python.festival_bloomberg.localenv import load_local_env

# ─── Constants ──────────────────────────────────────────────────────────

TOTAL_BUDGET_USD = 2.00
PER_SOURCE_CEILING_USD = 0.30
BAKEOFF_VERSION = "source_bakeoff_v2"
OUTPUT_DIR = PROJECT_ROOT / "data" / "bakeoff"
RIGHTS_DEFAULT = "TERMS_REVIEW_REQUIRED"

# ─── Candidate Sources ──────────────────────────────────────────────────

P0_CANDIDATES = [
    # Songkick
    {"name": "Songkick (hoholabs)", "actor_id": "hoholabs~songkick-scraper",
     "category": "event_source", "source_platform": "songkick.com",
     "input": {"queryType": "artist", "artist": "Kendrick Lamar", "downloadDelay": 200},
     "query_context": "songkick_kendrick_lamar"},
    {"name": "Songkick (gio21)", "actor_id": "gio21~songkick-events-scraper",
     "category": "event_source", "source_platform": "songkick.com",
     "input": {"artist": "Kendrick Lamar", "maxItems": 25},
     "query_context": "songkick_gio21_kendrick"},
    {"name": "Songkick (aitorsm)", "actor_id": "aitorsm~songkick-events",
     "category": "event_source", "source_platform": "songkick.com",
     "input": {"city": "Los Angeles", "maxEvents": 25},
     "query_context": "songkick_aitorsm_la"},
    
    # Bandsintown
    {"name": "Bandsintown (hoholabs)", "actor_id": "hoholabs~bandsintown-scraper",
     "category": "event_source", "source_platform": "bandsintown.com",
     "input": {"artist": "Kendrick Lamar"},
     "query_context": "bandsintown_hoholabs_kendrick"},
    {"name": "Bandsintown (automation-lab)", "actor_id": "automation-lab~bandsintown-events-scraper",
     "category": "event_source", "source_platform": "bandsintown.com",
     "input": {"artistName": "Kendrick Lamar", "maxItems": 25},
     "query_context": "bandsintown_autolab_kendrick"},
    {"name": "Bandsintown (gio21)", "actor_id": "gio21~bandsintown-events-scraper",
     "category": "event_source", "source_platform": "bandsintown.com",
     "input": {"artist": "Kendrick Lamar", "maxItems": 25},
     "query_context": "bandsintown_gio21_kendrick"},
    
    # Resident Advisor
    {"name": "Resident Advisor (crawlerbros)", "actor_id": "crawlerbros~resident-advisor-scraper",
     "category": "event_source", "source_platform": "residentadvisor.net",
     "input": {"location": "Chicago", "maxItems": 20},
     "query_context": "ra_crawlerbros_chicago"},
    
    # Eventbrite (3+ variants)
    {"name": "Eventbrite (solidcode)", "actor_id": "solidcode~eventbrite-scraper",
     "category": "event_source", "source_platform": "eventbrite.com",
     "input": {"city": "Chicago", "maxResults": 20},
     "query_context": "eventbrite_solidcode_chicago"},
    {"name": "Eventbrite (epicscrapers)", "actor_id": "epicscrapers~eventbrite-scraper",
     "category": "event_source", "source_platform": "eventbrite.com",
     "input": {"location": "Los Angeles", "maxItems": 20},
     "query_context": "eventbrite_epic_la"},
    {"name": "Eventbrite (scrapesage)", "actor_id": "scrapesage~eventbrite-scraper",
     "category": "event_source", "source_platform": "eventbrite.com",
     "input": {"searchTerm": "music festival", "city": "Chicago", "maxResults": 20},
     "query_context": "eventbrite_scrapesage_chicago"},
    
    # DICE
    {"name": "DICE (hoholabs)", "actor_id": "hoholabs~dicefm-scraper",
     "category": "event_source", "source_platform": "dice.fm",
     "input": {"city": "London", "maxItems": 20},
     "query_context": "dice_hoholabs_london"},
    {"name": "DICE (solidcode)", "actor_id": "solidcode~dice-fm-scraper",
     "category": "event_source", "source_platform": "dice.fm",
     "input": {"city": "London", "maxEventsPerPage": 20},
     "query_context": "dice_solidcode_london"},
    {"name": "DICE (chalkandcheese)", "actor_id": "chalkandcheese~dice-fm-events-scraper",
     "category": "event_source", "source_platform": "dice.fm",
     "input": {"city": "London", "maxItems": 20},
     "query_context": "dice_cnc_london"},
]

SECONDARY_CANDIDATES = [
    # AllEvents
    {"name": "AllEvents", "actor_id": "solidcode~allevents-scraper",
     "category": "event_source", "source_platform": "allevents.in",
     "input": {"city": "Chicago", "maxItems": 20},
     "query_context": "allevents_chicago"},
    
    # Facebook Events
    {"name": "Facebook Events (unfenced-group)", "actor_id": "unfenced-group~facebook-events-scraper",
     "category": "event_source", "source_platform": "facebook.com",
     "input": {"city": "Chicago", "maxEvents": 20},
     "query_context": "fb_events_chicago"},
    {"name": "Facebook Events (scrapesmith)", "actor_id": "scrapesmith~facebook-events-scraper",
     "category": "event_source", "source_platform": "facebook.com",
     "input": {"location": "Chicago", "maxItems": 20},
     "query_context": "fb_events_scrapesmith_chicago"},
    
    # Fever
    {"name": "Fever (hoholabs)", "actor_id": "hoholabs~feverup-scraper",
     "category": "event_source", "source_platform": "feverup.com",
     "input": {"city": "Chicago", "maxItems": 20},
     "query_context": "fever_chicago"},
    
    # AXS
    {"name": "AXS", "actor_id": "lexis-solutions~axs-scraper",
     "category": "event_source", "source_platform": "axs.com",
     "input": {"searchTerm": "concert", "maxItems": 20},
     "query_context": "axs_scraper"},
]

TICKETMASTER_CONTROL = [
    {"name": "Ticketmaster Web (control)", "actor_id": "epicscrapers~ticketmaster-scraper",
     "category": "event_source", "source_platform": "ticketmaster.com",
     "input": {"searchTerm": "Kendrick Lamar", "maxItems": 10},
     "query_context": "tm_web_control"},
]


# ─── Helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(data: Any, n: int = 16) -> str:
    return hashlib.sha256(json.dumps(data, default=str, sort_keys=True).encode()).hexdigest()[:n]


def _safe_field_coverage(records: list[dict]) -> dict[str, float]:
    """Compute % of records where each field is non-null/non-empty."""
    if not records:
        return {}
    total = len(records)
    coverage: dict[str, int] = {}
    for rec in records:
        for key in rec:
            if key not in coverage:
                coverage[key] = 0
            val = rec[key]
            if val is not None and val != "" and val != []:
                coverage[key] += 1
    return {k: round(v / total * 100, 1) for k, v in coverage.items()}


def _has_coords(rec: dict) -> bool:
    """Check if record has lat/lon."""
    lat = rec.get("latitude") or rec.get("lat") or rec.get("location", {}).get("lat")
    lon = rec.get("longitude") or rec.get("lon") or rec.get("lng") or rec.get("location", {}).get("lng")
    return lat is not None and lon is not None and bool(lat) and bool(lon)


def _has_price(rec: dict) -> bool:
    """Check if record has any price information."""
    for key in ("price", "ticketPrice", "priceRange", "minPrice", "maxPrice", "cost", "offers"):
        val = rec.get(key)
        if val is not None and val != "" and val != []:
            return True
    return False


# ─── Main Bakeoff ───────────────────────────────────────────────────────

def run_bakeoff(*, dry_run: bool = True, execute_p0: bool = False,
                execute_secondary: bool = False, execute_tm_control: bool = False,
                max_execute: int = 999) -> dict[str, Any]:
    """Run the complete source bakeoff."""
    
    load_local_env()
    if not is_configured():
        return {"status": "NOT_CONFIGURED", "error": "APIFY_TOKEN not set"}
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "bakeoff_version": BAKEOFF_VERSION,
        "started_at": _now(),
        "budget_total_usd": TOTAL_BUDGET_USD,
        "per_source_ceiling_usd": PER_SOURCE_CEILING_USD,
        "dry_run": dry_run,
        "total_spent_usd": 0.0,
        "inspections": [],
        "executions": [],
        "verdicts": {},
    }
    
    # ── Phase 1: Schema Inspection (always, free) ──
    all_candidates = (
        P0_CANDIDATES +
        (SECONDARY_CANDIDATES if execute_secondary else []) +
        (TICKETMASTER_CONTROL if execute_tm_control else [])
    )
    
    print(f"=== PHASE 1: Schema Inspection ({len(all_candidates)} candidates) ===\n")
    
    for cand in all_candidates:
        aid = cand["actor_id"]
        print(f"  Inspecting: {cand['name']} ({aid})...", end=" ")
        inspection = inspect_actor(aid)
        
        entry = {
            "name": cand["name"],
            "actor_id": aid,
            "category": cand["category"],
            "source_platform": cand["source_platform"],
            "query_context": cand.get("query_context", ""),
            "status": inspection.get("status"),
            "title": inspection.get("title", ""),
            "schema_properties": inspection.get("schema_properties", []),
            "build_version": inspection.get("build_version", ""),
            "pricing_model": inspection.get("pricing_model"),
            "modified_at": inspection.get("modified_at"),
            "is_deprecated": inspection.get("is_deprecated", False),
            "stats": inspection.get("stats", {}),
            "inspected_at": _now(),
            "schema_hash": _hash(inspection.get("schema_properties", [])),
        }
        results["inspections"].append(entry)
        
        status = inspection.get("status")
        if status == "OBSERVED":
            print(f"OK (schema: {len(entry['schema_properties'])} fields)")
        else:
            print(f"FAILED ({status}: {inspection.get('error', '?')})")
    
    # ── Phase 2: Bounded Execution ──
    candidates_to_run = []
    if execute_p0:
        candidates_to_run = P0_CANDIDATES
    if execute_secondary:
        candidates_to_run += SECONDARY_CANDIDATES
    if execute_tm_control:
        candidates_to_run += TICKETMASTER_CONTROL
    
    if dry_run or not candidates_to_run:
        print(f"\n=== PHASE 2: Execution SKIPPED (dry_run={dry_run}, candidates={len(candidates_to_run)}) ===\n")
        results["status"] = "DRY_RUN_COMPLETED"
        _save_results(results)
        return results
    
    print(f"\n=== PHASE 2: Bounded Execution ({min(len(candidates_to_run), max_execute)} candidates) ===\n")
    print(f"  Budget: ${TOTAL_BUDGET_USD:.2f} total, ${PER_SOURCE_CEILING_USD:.2f} per source\n")
    
    executed = 0
    for cand in candidates_to_run:
        if executed >= max_execute:
            print(f"  Reached max_execute={max_execute}, stopping.")
            break
        if results["total_spent_usd"] >= TOTAL_BUDGET_USD:
            print(f"  Budget exhausted (${results['total_spent_usd']:.4f} >= ${TOTAL_BUDGET_USD:.2f}), stopping.")
            break
        
        aid = cand["actor_id"]
        name = cand["name"]
        print(f"  Running: {name} ({aid})...", end=" ")
        
        # Check source-level budget
        # Estimate: each run is ~$0.005-$0.05 with small input
        if results["total_spent_usd"] + 0.05 > TOTAL_BUDGET_USD:
            print(f"SKIPPED (would exceed budget)")
            continue
        
        start_time = time.monotonic()
        exec_result = run_actor(
            aid,
            cand.get("input", {}),
            max_polls=30,
            poll_interval=2.0,
            timeout=120,
        )
        elapsed = time.monotonic() - start_time
        
        status = exec_result.get("status")
        records = exec_result.get("records", [])
        cost = exec_result.get("cost_usd")
        
        # Track budget
        if cost is not None and isinstance(cost, (int, float)):
            results["total_spent_usd"] += float(cost)
        
        # Dataset analysis
        field_coverage = _safe_field_coverage(records)
        with_coords = sum(1 for r in records if _has_coords(r))
        with_price = sum(1 for r in records if _has_price(r))
        
        exec_entry = {
            "name": name,
            "actor_id": aid,
            "source_platform": cand["source_platform"],
            "query_context": cand.get("query_context", ""),
            "status": status,
            "run_id": exec_result.get("run_id"),
            "record_count": len(records),
            "cost_usd": cost,
            "latency_ms": round(elapsed * 1000),
            "final_state": exec_result.get("final_state"),
            "field_coverage": field_coverage,
            "records_with_coordinates": with_coords,
            "records_with_price": with_price,
            "sample_keys": list(records[0].keys()) if records else [],
            "executed_at": _now(),
            "rights_status": RIGHTS_DEFAULT,
        }
        
        # PIT fields
        pit_sources = 0
        for pk in ("publishedAt", "createdAt", "announcedAt", "onsaleAt", "publicationDate", "datePublished"):
            if any(pk in r for r in records if r.get(pk)):
                pit_sources += 1
        exec_entry["pit_fields_available"] = pit_sources
        
        results["executions"].append(exec_entry)
        executed += 1
        
        print(f"{status} ({len(records)} records, ${cost}, {elapsed:.1f}s)")
        
        # Save raw records
        raw_path = OUTPUT_DIR / f"{aid.replace('~', '_')}_raw.json"
        with open(raw_path, "w") as f:
            json.dump(exec_result, f, default=str, indent=2)
        exec_entry["raw_saved_to"] = str(raw_path)
        
        # Respect per-source ceiling
        if cost is not None and float(cost) > PER_SOURCE_CEILING_USD:
            print(f"    WARNING: cost ${cost} exceeds ${PER_SOURCE_CEILING_USD} per-source ceiling")
    
    results["status"] = "COMPLETED"
    results["executed_count"] = executed
    results["completed_at"] = _now()
    
    _save_results(results)
    return results


def _save_results(results: dict):
    """Save results to disk."""
    path = OUTPUT_DIR / "bakeoff_results.json"
    with open(path, "w") as f:
        json.dump(results, f, default=str, indent=2)
    print(f"\nResults saved to {path}")


def compute_acceptance_matrix(results: dict) -> list[dict]:
    """Generate acceptance matrix from bakeoff results."""
    matrix = []
    
    # Index inspections by actor_id
    inspections: dict[str, dict] = {}
    for insp in results.get("inspections", []):
        inspections[insp["actor_id"]] = insp
    
    for ex in results.get("executions", []):
        insp = inspections.get(ex["actor_id"], {})
        status = ex.get("status")
        success = status in ("COMPLETED", "SUCCEEDED")
        rec_count = ex.get("record_count", 0)
        cost = ex.get("cost_usd") or 0
        coverage = ex.get("field_coverage", {})
        
        # Key fields we care about
        has_id = any(k for k in coverage if "id" in k.lower())
        has_name = any(k for k in coverage if "name" in k.lower() or "title" in k.lower())
        has_date = any(k for k in coverage if "date" in k.lower())
        has_venue = any(k for k in coverage if "venue" in k.lower())
        has_coords = ex.get("records_with_coordinates", 0)
        has_price = ex.get("records_with_price", 0)
        has_lineup = any(k for k in coverage if any(w in k.lower() for w in ("lineup", "artist", "performer")))
        has_promoter = any(k for k in coverage if any(w in k.lower() for w in ("promoter", "organizer", "presenter")))
        has_ticket_url = any(k for k in coverage if any(w in k.lower() for w in ("ticket", "url", "link")))
        
        verdict = "INSPECT_ONLY"
        if success and rec_count > 0:
            # Compute value score
            value_score = (
                (1 if has_id else 0) +
                (1 if has_name else 0) +
                (1 if has_date else 0) +
                (1 if has_venue else 0) +
                (2 if has_coords > 0 else 0) +
                (2 if has_price > 0 else 0) +
                (1 if has_lineup else 0) +
                (1 if has_promoter else 0) +
                (1 if has_ticket_url else 0)
            )
            cost_per_rec = cost / rec_count if rec_count > 0 else 999
            
            if value_score >= 7 and cost_per_rec < 0.01:
                verdict = "PILOT_ONLY"
            elif value_score >= 5:
                verdict = "RESEARCH_ONLY"
            else:
                verdict = "REJECT"
        
        row = {
            "source": ex["name"],
            "actor_id": ex["actor_id"],
            "source_platform": ex["source_platform"],
            "schema_version": insp.get("build_version", "?"),
            "schema_hash": insp.get("schema_hash", "?"),
            "sample_size": rec_count,
            "success": success,
            "cost_usd": cost,
            "cost_per_record": round(cost / rec_count, 6) if rec_count > 0 else None,
            "field_count": len(coverage),
            "key_fields": list(coverage.keys()),
            "has_event_id": has_id,
            "has_event_name": has_name,
            "has_date": has_date,
            "has_venue": has_venue,
            "has_coordinates": has_coords > 0,
            "has_price": has_price,
            "has_lineup": has_lineup,
            "has_promoter": has_promoter,
            "has_ticket_url": has_ticket_url,
            "pit_fields_available": ex.get("pit_fields_available", 0),
            "rights_status": ex.get("rights_status", RIGHTS_DEFAULT),
            "verdict": verdict,
        }
        matrix.append(row)
    
    return matrix


# ─── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PR #44 Source Bakeoff")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Schema-only inspection (free)")
    parser.add_argument("--execute", action="store_true", default=False,
                        help="Execute P0 sources (spends money)")
    parser.add_argument("--execute-all", action="store_true", default=False,
                        help="Execute P0 + secondary + TM control")
    parser.add_argument("--max-execute", type=int, default=3,
                        help="Max sources to execute (default: 3)")
    parser.add_argument("--inspect-only", action="store_true", default=False,
                        help="Just inspect and print schema")
    args = parser.parse_args()
    
    dry_run = not (args.execute or args.execute_all)
    execute_p0 = args.execute or args.execute_all
    execute_secondary = args.execute_all
    execute_tm = args.execute_all
    
    if args.inspect_only:
        # Just schema inspection
        from python.festival_bloomberg.acquisition.apify_direct import inspect_actor
        load_local_env()
        print("=== Schema Inspection (free) ===\n")
        for cand in P0_CANDIDATES + SECONDARY_CANDIDATES:
            r = inspect_actor(cand["actor_id"])
            print(f"{cand['name']} ({cand['actor_id']}):")
            print(f"  Status: {r.get('status')}")
            print(f"  Title: {r.get('title','?')}")
            print(f"  Schema fields: {r.get('schema_properties', [])}")
            print(f"  Build: {r.get('build_version','?')}")
            print(f"  Modified: {r.get('modified_at','?')}")
            print(f"  Deprecated: {r.get('is_deprecated', False)}")
            print()
    else:
        result = run_bakeoff(
            dry_run=dry_run,
            execute_p0=execute_p0,
            execute_secondary=execute_secondary,
            execute_tm_control=execute_tm,
            max_execute=args.max_execute,
        )
        
        print(f"\n=== Summary ===")
        print(f"Status: {result.get('status')}")
        print(f"Inspected: {len(result.get('inspections', []))} actors")
        print(f"Executed: {len(result.get('executions', []))} sources")
        print(f"Spent: ${result.get('total_spent_usd', 0):.6f}")
        
        if result.get("executions"):
            print(f"\n=== Acceptance Matrix ===\n")
            matrix = compute_acceptance_matrix(result)
            for row in matrix:
                print(f"{row['source']}: {row['verdict']} "
                      f"(records={row['sample_size']}, cost=${row['cost_usd']}, "
                      f"fields={row['field_count']}, coords={row['has_coordinates']}, "
                      f"price={row['has_price']})")