"""MARKETPLACE_URL_RESOLUTION_AND_TARGETED_SNAPSHOT_V1 pilot runner.

Stage 1 — Resolve 20 canonical events to exact marketplace event URLs
          (tinyfish/search, FREE; validation against artist/venue/city/date)
Stage 2 — Persist mappings into acquisition.marketplace_event_mappings
Stage 3 — Targeted snapshot wave: fetch matched URLs, parse JSON-LD,
          persist ticket-market snapshots (Wave A / Wave B)

Usage:
    python scripts/run_marketplace_pilot.py --resolve --limit 20
    python scripts/run_marketplace_pilot.py --wave A --max-fetch 25
    python scripts/run_marketplace_pilot.py --wave B --max-fetch 25
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

from festival_bloomberg.evidence_rails.url_resolver import (
    MARKETPLACES,
    resolve_marketplace_url,
    persist_mapping,
    fetch_page,
    extract_from_page,
    monid_run,
)
from festival_bloomberg.evidence_rails.ticket_market import (
    persist_snapshot,
    record_source_health,
    MARKET_SOURCES,
)
from festival_bloomberg.evidence_rails.contract import (
    ObservationRecord,
    ingest_observation,
    detect_changes,
)
from festival_bloomberg.migrations import apply_pending_migrations

UNIVERSE_PATH = PROJECT_ROOT / "data" / "workspace" / "watch_universe_v1.json"
OUT_DIR = PROJECT_ROOT / "data" / "workspace" / "ticket_market"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    apply_pending_migrations(conn)
    return conn


def load_universe(n: int | None = None) -> list[dict]:
    data = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    events = data.get("events", [])
    return events[:n] if n else events


def resolve_pilot(conn, universe: list[dict], marketplaces: list[str],
                  events_per_marketplace: int = 20) -> dict:
    """Resolve up to `events_per_marketplace` events for each marketplace."""
    report: dict = {
        "mode": "URL_RESOLUTION",
        "started_at": _now(),
        "events_attempted": len(universe),
        "marketplaces": {},
        "totals": {"mappings": 0, "matched": 0, "ambiguous": 0, "not_found": 0},
    }

    for mp in marketplaces:
        mp_report = {"attempted": 0, "matched": 0, "ambiguous": 0, "not_found": 0,
                     "mappings": []}
        for ev in universe[:events_per_marketplace]:
            mp_report["attempted"] += 1
            try:
                r = resolve_marketplace_url(ev, mp, search_provider="tinyfish")
            except Exception as e:
                mp_report["not_found"] += 1
                continue

            status = r.get("status", "NOT_FOUND")
            bucket = {
                "MATCHED_EXACT": "matched",
                "MATCHED_HIGH_CONFIDENCE": "matched",
                "AMBIGUOUS": "ambiguous",
                "NOT_FOUND": "not_found",
            }.get(status, "not_found")
            mp_report[bucket] += 1

            if status in ("MATCHED_EXACT", "MATCHED_HIGH_CONFIDENCE", "AMBIGUOUS"):
                mid = persist_mapping(conn, {
                    "event_key": ev.get("event_key"),
                    "artist_key": ev.get("artist_key"),
                    "venue_key": ev.get("venue_key"),
                    "market_key": ev.get("market_key"),
                    "marketplace": mp,
                    "marketplace_event_url": r.get("url"),
                    "resolution_method": r.get("method"),
                    "resolution_status": status,
                    "resolution_confidence": r.get("confidence"),
                    "validation_checked": r.get("validation"),
                    "source_query": r.get("query"),
                    "source_result_url": r.get("url"),
                    "resolved_at": _now(),
                    "rights_status": "TERMS_REVIEW_REQUIRED",
                    "commercial_use_status": "PROTOTYPE_ONLY",
                })
                mp_report["mappings"].append({
                    "mapping_id": mid,
                    "event_key": ev.get("event_key"),
                    "artist": ev.get("artist_name"),
                    "venue": ev.get("venue_name"),
                    "date": str(ev.get("event_date"))[:10],
                    "url": r.get("url"),
                    "status": status,
                    "confidence": r.get("confidence"),
                    "validation": r.get("validation"),
                })
                report["totals"]["mappings"] += 1
                if status == "MATCHED_EXACT":
                    report["totals"]["matched"] += 1
                elif status == "AMBIGUOUS":
                    report["totals"]["ambiguous"] += 1
            else:
                report["totals"]["not_found"] += 1

        report["marketplaces"][mp] = mp_report

    report["finished_at"] = _now()
    return report


def run_targeted_wave(conn, wave_label: str, *, max_fetch: int = 25) -> dict:
    """Fetch matched marketplace URLs, parse JSON-LD, persist snapshots.

    This is a REAL network observation: every fetch is a live call.
    """
    # Get MATCHED mappings with a URL.
    cur = conn.execute(
        """
        SELECT mapping_id, event_key, artist_key, venue_key, market_key,
               marketplace, marketplace_event_url, resolution_status,
               resolution_confidence
        FROM acquisition.marketplace_event_mappings
        WHERE resolution_status IN ('MATCHED_EXACT', 'MATCHED_HIGH_CONFIDENCE')
          AND marketplace_event_url IS NOT NULL
        ORDER BY mapping_id
        LIMIT ?
        """,
        [max_fetch],
    )
    cols = [c[0] for c in cur.description] if cur.description else []
    mappings = [dict(zip(cols, r)) for r in cur.fetchall()]

    report: dict = {
        "wave_label": wave_label,
        "mode": "TARGETED_SNAPSHOT",
        "started_at": _now(),
        "mappings_fetched": len(mappings),
        "sources": {},
        "totals": {"fetches": 0, "parsed": 0, "matched_snapshots": 0,
                   "price_records": 0, "listing_records": 0, "cost_usd": 0.0,
                   "changes": 0, "errors": []},
    }

    for m in mappings:
        url = m["marketplace_event_url"]
        mp = m["marketplace"]
        event_key = m["event_key"]
        try:
            page = fetch_page(url, fetch_provider="context.dev")
        except Exception as e:
            report["totals"]["errors"].append({"url": url, "error": str(e)})
            continue
        report["totals"]["fetches"] += 1
        cost = page.get("cost") or {}
        if isinstance(cost, dict):
            cost_usd = float(cost.get("amount", {}).get("value") or 0)
        else:
            cost_usd = 0.0
        report["totals"]["cost_usd"] += cost_usd

        src_key = mp.split(".")[0]
        src = MARKET_SOURCES.get(src_key, {"platform": mp, "actor": f"monid_{page.get('provider')}", "category": "RESALE"})

        pg = page.get("page") or {}
        extracted = extract_from_page(pg, mp)

        report["totals"]["parsed"] += 1
        src_report = report["sources"].setdefault(src_key, {
            "platform": mp, "fetches": 0, "parsed": 0, "price_records": 0,
            "listing_records": 0, "snapshots": 0, "cost_usd": 0.0,
        })
        src_report["fetches"] += 1
        src_report["cost_usd"] += cost_usd

        # Build a normalized snapshot from the extraction.
        price = extracted.get("price")
        price_min = extracted.get("price_min")
        availability = extracted.get("availability") or ""
        sold_out = "soldout" in str(availability).lower() or "sold-out" in str(availability).lower()

        snapshot = {
            "watch_universe_version": "watch_universe_v1",
            "event_key": event_key,
            "provider_event_id": None,
            "source_platform": mp,
            "actor_or_endpoint": f"monid_{page.get('provider')}",
            "source_record_id": m.get("mapping_id"),
            "wave_label": wave_label,
            "observed_at": _now(),
            "retrieved_at": _now(),
            "knowledge_time": _now(),
            "currency": extracted.get("currency"),
            "resale_min_price": price if price is not None else price_min,
            "resale_median_price": None,
            "resale_avg_price": None,
            "resale_max_price": None,
            "listing_count": None,
            "ticket_count": None,
            "sold_out_flag": sold_out,
            "availability_flag": "instock" in str(availability).lower(),
            "identity_match_status": "MATCHED",
            "identity_match_method": "MONID_TINYFISH_SEARCH+JSONLD",
            "identity_match_confidence": m.get("resolution_confidence"),
            "source_url": url,
            "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY",
        }
        persist_snapshot(conn, snapshot)
        src_report["snapshots"] += 1
        src_report["parsed"] += 1
        report["totals"]["matched_snapshots"] += 1
        if snapshot["resale_min_price"] is not None:
            src_report["price_records"] += 1
            report["totals"]["price_records"] += 1

        # Raw observation for the append-only contract.
        try:
            obs = ObservationRecord(
                source_platform=mp,
                acquisition_provider="monid",
                actor_or_endpoint=f"monid_{page.get('provider')}",
                source_record_id=m.get("mapping_id"),
                observation_type="TICKET_PRICE" if price is not None else "TICKET_AVAILABILITY",
                observation_category=src["category"],
                raw_payload={"url": url, "extracted": extracted, "page_provider": page.get("provider")},
                event_key=event_key,
                market_key=m.get("market_key"),
                observed_at=_now(),
                knowledge_time=_now(),
                normalized_fields=extracted,
                rights_status="TERMS_REVIEW_REQUIRED",
                commercial_use_status="PROTOTYPE_ONLY",
            )
            ingest_observation(conn, obs)
        except Exception:
            pass

    # Detect changes across waves.
    for src_key in report["sources"]:
        try:
            changes = detect_changes(conn, report["sources"][src_key]["platform"])
            report["sources"][src_key]["changes_detected"] = len(changes)
            report["totals"]["changes"] += len(changes)
        except Exception:
            report["sources"][src_key]["changes_detected"] = 0

    # Health ledger per platform.
    for src_key, sr in report["sources"].items():
        record_source_health(conn, {
            "source_platform": sr["platform"],
            "actor_or_endpoint": "monid_context_dev_html",
            "wave_label": wave_label,
            "started_at": report["started_at"],
            "finished_at": _now(),
            "status": "SUCCESS" if sr["fetches"] > 0 else "NO_RECORDS",
            "events_requested": len(mappings),
            "events_resolved": sr["snapshots"],
            "observations_ingested": sr["snapshots"],
            "latency_ms": 0,
            "cost_usd": sr["cost_usd"],
            "schema_version": "monid_context_dev_20260825",
            "records_returned": sr["fetches"],
        })

    report["finished_at"] = _now()
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--wave", choices=["A", "B"], default="A")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--max-fetch", type=int, default=25)
    ap.add_argument("--db", default=str(OUT_DIR / "ticket_market.duckdb"))
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_warehouse(Path(args.db))
    universe = load_universe()

    if args.resolve:
        report = resolve_pilot(conn, universe, MARKETPLACES, events_per_marketplace=args.limit)
    else:
        report = run_targeted_wave(conn, f"wave_{args.wave}", max_fetch=args.max_fetch)

    print(json.dumps(report, indent=2)[:6000])
    conn.close()


if __name__ == "__main__":
    main()
