"""Wave B: re-fetch the exact URLs captured in Wave A and detect real deltas.

Unlike the full wave runner, this only re-observes URLs that already have a
Wave A snapshot, which is the correct "repeated observation" acceptance proof.
Persists append-only snapshots and reports per-URL change/no-change.

Usage:
    python scripts/wave_b_rerun.py --max-fetch 20
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

from festival_bloomberg.evidence_rails.url_resolver import fetch_page, extract_from_page
from festival_bloomberg.evidence_rails.ticket_market import persist_snapshot
from festival_bloomberg.migrations import apply_pending_migrations

OUT_DIR = PROJECT_ROOT / "data" / "workspace" / "ticket_market"
DB_PATH = OUT_DIR / "ticket_market.duckdb"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-fetch", type=int, default=20)
    args = ap.parse_args()

    conn = duckdb.connect(str(DB_PATH))
    apply_pending_migrations(conn)

    # URLs observed in Wave A (dedupe), with their wave-A price for comparison.
    rows = conn.execute(
        """
        SELECT DISTINCT source_platform, source_url, event_key,
               first_value(resale_min_price) OVER (
                   PARTITION BY source_url ORDER BY observed_at
               ) AS wave_a_price,
               first_value(currency) OVER (
                   PARTITION BY source_url ORDER BY observed_at
               ) AS currency
        FROM acquisition.ticket_market_snapshots
        WHERE wave_label = 'wave_A' AND source_url IS NOT NULL
        ORDER BY source_platform
        """
    ).fetchall()
    rows = rows[: args.max_fetch]

    results = []
    for i, (platform, url, event_key, price_a, currency) in enumerate(rows):
        print(f"[{i+1}/{len(rows)}] {platform} {url[:70]}", flush=True)
        try:
            page = fetch_page(url, fetch_provider="context.dev")
        except Exception as e:
            results.append({"url": url, "status": "FETCH_ERROR", "error": str(e)})
            print(f"  ERROR {e}", flush=True)
            continue

        pg = page.get("page") or {}
        extracted = extract_from_page(pg, platform)
        price_b = extracted.get("price") or extracted.get("price_min")
        availability = extracted.get("availability") or ""
        sold_out = "soldout" in str(availability).lower()

        changed = False
        if price_a is not None and price_b is not None:
            try:
                changed = abs(float(price_a) - float(price_b)) > 0.005
            except (TypeError, ValueError):
                changed = False

        results.append({
            "url": url,
            "platform": platform,
            "wave_a_price": price_a,
            "wave_b_price": price_b,
            "currency": currency,
            "changed": changed,
            "parsed": extracted.get("has_structured_data", False),
        })

        persist_snapshot(conn, {
            "watch_universe_version": "watch_universe_v1",
            "event_key": event_key,
            "provider_event_id": None,
            "source_platform": platform,
            "actor_or_endpoint": f"monid_{page.get('provider')}",
            "source_record_id": None,
            "wave_label": "wave_B",
            "observed_at": _now(),
            "retrieved_at": _now(),
            "knowledge_time": _now(),
            "currency": extracted.get("currency") or currency,
            "resale_min_price": price_b,
            "resale_median_price": None,
            "resale_avg_price": None,
            "resale_max_price": None,
            "listing_count": None,
            "ticket_count": None,
            "sold_out_flag": sold_out,
            "availability_flag": "instock" in str(availability).lower(),
            "identity_match_status": "MATCHED",
            "identity_match_method": "MONID_URL_RESOLVED_JSONLD",
            "identity_match_confidence": None,
            "source_url": url,
            "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY",
        })
        print(f"  A=${price_a} -> B=${price_b} changed={changed}", flush=True)

    n_changed = sum(1 for r in results if r.get("changed"))
    n_price = sum(1 for r in results if r.get("wave_b_price") is not None)
    summary = {
        "wave_B_rerun": {
            "urls_refetched": len(results),
            "with_price_both_waves": n_price,
            "price_changed": n_changed,
            "price_unchanged": n_price - n_changed,
            "results": results,
        }
    }
    print(json.dumps(summary, indent=2)[:4000], flush=True)

    conn.close()
    # Persist summary for the handoff.
    (OUT_DIR / "wave_b_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
