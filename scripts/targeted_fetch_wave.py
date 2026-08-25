"""Resilient targeted-fetch wave runner.

Fetches matched marketplace URLs one at a time, persisting each snapshot
immediately so a timeout never loses progress. Resumes where it left off
(skips URLs that already have a snapshot for this wave).

Usage:
    python scripts/targeted_fetch_wave.py --wave A --max-fetch 40
    python scripts/targeted_fetch_wave.py --wave B --max-fetch 40
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

from festival_bloomberg.evidence_rails.url_resolver import fetch_page, extract_from_page
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

OUT_DIR = PROJECT_ROOT / "data" / "workspace" / "ticket_market"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    apply_pending_migrations(conn)
    return conn


def run_wave(conn, wave_label: str, *, max_fetch: int = 40, skip_existing: bool = True) -> dict:
    report: dict = {
        "wave_label": wave_label,
        "mode": "TARGETED_SNAPSHOT",
        "started_at": _now(),
        "sources": {},
        "totals": {"fetches": 0, "parsed": 0, "snapshots": 0, "price_records": 0,
                   "cost_usd": 0.0, "changes": 0, "errors": [], "skipped_existing": 0},
    }

    cur = conn.execute(
        """
        SELECT mapping_id, event_key, artist_key, venue_key, market_key,
               marketplace, marketplace_event_url, resolution_status,
               resolution_confidence
        FROM acquisition.marketplace_event_mappings
        WHERE resolution_status IN ('MATCHED_EXACT', 'MATCHED_HIGH_CONFIDENCE')
          AND marketplace_event_url IS NOT NULL
        ORDER BY marketplace, mapping_id
        """
    )
    cols = [c[0] for c in cur.description] if cur.description else []
    mappings = [dict(zip(cols, r)) for r in cur.fetchall()]

    if skip_existing:
        existing = {
            r[0] for r in conn.execute(
                "SELECT source_url FROM acquisition.ticket_market_snapshots WHERE wave_label = ? AND source_url IS NOT NULL",
                [wave_label],
            ).fetchall()
        }
        before = len(mappings)
        mappings = [m for m in mappings if m["marketplace_event_url"] not in existing]
        report["totals"]["skipped_existing"] = before - len(mappings)

    mappings = mappings[:max_fetch]

    for i, m in enumerate(mappings):
        url = m["marketplace_event_url"]
        mp = m["marketplace"]
        event_key = m["event_key"]
        print(f"[{i+1}/{len(mappings)}] {mp} {url[:80]}", flush=True)
        try:
            page = fetch_page(url, fetch_provider="context.dev")
        except Exception as e:
            report["totals"]["errors"].append({"url": url, "error": str(e)})
            print(f"  ERROR {e}", flush=True)
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
        src_report = report["sources"].setdefault(src_key, {
            "platform": mp, "fetches": 0, "parsed": 0, "price_records": 0,
            "snapshots": 0, "cost_usd": 0.0,
        })
        src_report["fetches"] += 1
        src_report["cost_usd"] += cost_usd

        pg = page.get("page") or {}
        extracted = extract_from_page(pg, mp)
        report["totals"]["parsed"] += 1
        src_report["parsed"] += 1

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
        report["totals"]["snapshots"] += 1
        if snapshot["resale_min_price"] is not None:
            src_report["price_records"] += 1
            report["totals"]["price_records"] += 1

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

    for src_key, sr in report["sources"].items():
        try:
            changes = detect_changes(conn, sr["platform"])
            sr["changes_detected"] = len(changes)
            report["totals"]["changes"] += len(changes)
        except Exception:
            sr["changes_detected"] = 0

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
    ap.add_argument("--wave", choices=["A", "B"], default="A")
    ap.add_argument("--max-fetch", type=int, default=40)
    ap.add_argument("--db", default=str(OUT_DIR / "ticket_market.duckdb"))
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_warehouse(Path(args.db))
    report = run_wave(conn, f"wave_{args.wave}", max_fetch=args.max_fetch)
    print(json.dumps(report, indent=2)[:5000], flush=True)
    conn.close()


if __name__ == "__main__":
    main()
