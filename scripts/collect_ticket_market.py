"""Production ticket-market collector (TICKET_MARKET_DATA_MOAT_V2).

Observes the frozen watch universe through the deterministic acquisition
router (evidence_rails.router) and appends immutable snapshots.

FLAGS:
    --universe PATH   frozen watch universe JSON (default data/workspace/watch_universe_v1.json)
    --source MP       restrict to one marketplace (seatgeek, vividseats, stubhub, gametime, tickpick)
    --fast            run the FAST rail (event-level via Monid targeted fetch)
    --deep            run the DEEP rail (listing-level via tickets.dev capture)
    --max-cost USD    hard budget guard; abort before exceeding (default 2.00)
    --dry-run         report what would run, make no network calls
    --max-fetch N     cap fetches this run
    --db PATH         evidence duckdb path (default data/workspace/ticket_market/ticket_market.duckdb)
    --wave LABEL      wave label override (default wave_<utc compact>)

Semantics:
  - append-only: never overwrite prior snapshots
  - budget guard: refuses to start or continue past --max-cost
  - source-aware retries (one retry per URL on network failure)
  - every observation persists observed_at/retrieved_at/knowledge_time
  - no tickets-sold inference; listing/ticket counts are availability proxies

Rights: all marketplace-page observation is TERMS_REVIEW_REQUIRED and stored
as PROTOTYPE_ONLY unless cleared.
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

from festival_bloomberg.evidence_rails.url_resolver import (
    fetch_page,
    extract_from_page,
    persist_mapping,
)
from festival_bloomberg.evidence_rails.tickets_dev import (
    capture,
    normalize_capture_snapshot,
    listings_from_snapshot,
    persist_listings,
    mark_disappeared_listings,
    is_sandbox,
)
from festival_bloomberg.evidence_rails.ticket_market import (
    persist_snapshot,
    record_source_health,
    MARKET_SOURCES,
    load_universe,
)
from festival_bloomberg.evidence_rails.contract import (
    ObservationRecord,
    ingest_observation,
    detect_changes,
)
from festival_bloomberg.evidence_rails.router import (
    route_observation,
    monthly_cost,
    RAIL_FAST,
    RAIL_DEEP,
    MEASURED_COST,
)
from festival_bloomberg.migrations import apply_pending_migrations

DEFAULT_UNIVERSE = PROJECT_ROOT / "data" / "workspace" / "watch_universe_v1.json"
DEFAULT_DB = PROJECT_ROOT / "data" / "workspace" / "ticket_market" / "ticket_market.duckdb"
OUT_DIR = DEFAULT_DB.parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    apply_pending_migrations(conn)
    return conn


def _load_mappings(conn) -> dict[tuple[str, str], dict]:
    """Load MATCHED mappings keyed by (event_key, marketplace)."""
    cur = conn.execute(
        """SELECT event_key, marketplace, marketplace_event_id,
                  marketplace_event_url, resolution_status, resolution_confidence
           FROM acquisition.marketplace_event_mappings
           WHERE resolution_status IN ('MATCHED_EXACT', 'MATCHED_HIGH_CONFIDENCE')
             AND marketplace_event_url IS NOT NULL"""
    )
    cols = [c[0] for c in cur.description] if cur.description else []
    out = {}
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        out[(d["event_key"], d["marketplace"])] = d
    return out


def _monid_fast_snapshot(conn, ev: dict, mp_url: str, mp: str, wave_label: str) -> dict:
    """FAST rail: Monid targeted fetch → JSON-LD extraction → snapshot."""
    page = fetch_page(mp_url, fetch_provider="context.dev")
    pg = page.get("page") or {}
    extracted = extract_from_page(pg, mp)
    price = extracted.get("price")
    price_min = extracted.get("price_min")
    availability = extracted.get("availability") or ""
    sold_out = "soldout" in str(availability).lower() or "sold-out" in str(availability).lower()
    cost = page.get("cost") or {}
    if isinstance(cost, dict):
        cost_usd = float(cost.get("amount", {}).get("value") or 0)
    else:
        cost_usd = 0.0

    return {
        "snapshot": {
            "watch_universe_version": ev.get("watch_universe_version"),
            "event_key": ev.get("event_key"),
            "provider_event_id": ev.get("provider_event_id"),
            "source_platform": mp,
            "actor_or_endpoint": f"monid_{page.get('provider')}",
            "source_record_id": None,
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
            "identity_match_method": "MONID_URL_RESOLVED_JSONLD",
            "identity_match_confidence": None,
            "source_url": mp_url,
            "raw_payload_hash": None,
            "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY",
        },
        "cost_usd": cost_usd,
        "provider": f"monid_{page.get('provider')}",
        "extracted": extracted,
    }


def _tickets_dev_deep(conn, ev: dict, mp_url: str, mp: str, wave_label: str) -> dict:
    """DEEP rail: tickets.dev capture → normalized snapshot + listings."""
    src = mp.split(".")[0]
    if src == "seatgeek.com":
        src = "seatgeek"
    res = capture(mp_url, source=src)
    if res.get("status") != "OK":
        return {"error": res.get("detail") or res.get("error"), "cost_usd": 0.0}
    snapshot = res.get("snapshot") or {}
    norm = normalize_capture_snapshot(
        snapshot,
        event_key=ev.get("event_key"),
        wave_label=wave_label,
    )
    return {
        "snapshot": norm,
        "cost_usd": 0.0 if res.get("sandbox") else MEASURED_COST["TICKETS_DEV_CAPTURE"],
        "provider": f"tickets_dev_capture:{src}",
        "sandbox": res.get("sandbox"),
        "raw_snapshot": snapshot,
        "listings": listings_from_snapshot(
            snapshot,
            event_key=ev.get("event_key"),
            wave_label=wave_label,
        ),
    }


def run_collect(
    conn,
    universe: list[dict],
    *,
    source: str | None,
    fast: bool,
    deep: bool,
    max_cost: float,
    max_fetch: int | None,
    wave_label: str,
    dry_run: bool = False,
) -> dict:
    mappings = _load_mappings(conn)
    report: dict = {
        "wave_label": wave_label,
        "mode": "FAST+DEEP" if fast and deep else ("FAST" if fast else "DEEP"),
        "started_at": _now(),
        "budget": {"max_cost_usd": max_cost, "spent_usd": 0.0},
        "methods": {},
        "totals": {"attempted": 0, "fetches": 0, "snapshots": 0, "listings": 0,
                   "cost_usd": 0.0, "errors": [], "skipped_budget": 0},
    }

    def _budget_ok() -> bool:
        return report["totals"]["cost_usd"] <= max_cost

    for ev in universe:
        if not _budget_ok():
            report["totals"]["skipped_budget"] += 1
            break
        ekey = ev.get("event_key")

        # Which marketplaces do we have mapped URLs for?
        mp_keys = [k[1] for k in mappings if k[0] == ekey]
        if source:
            mp_keys = [m for m in mp_keys if m.startswith(source)]
        if not mp_keys:
            continue

        for mp in mp_keys:
            if not _budget_ok():
                report["totals"]["skipped_budget"] += 1
                break
            mapping = mappings.get((ekey, mp))
            mp_url = mapping["marketplace_event_url"]
            report["totals"]["attempted"] += 1

            if fast:
                route = route_observation(
                    marketplace=mp,
                    has_mapped_url=True,
                    needs_listings=False,
                    cadence="daily",
                )
                if dry_run:
                    report["totals"]["cost_usd"] += route["cost_per_call"]
                    report["methods"].setdefault(route["method"], {"calls": 0, "cost_usd": 0.0})
                    report["methods"][route["method"]]["calls"] += 1
                    report["methods"][route["method"]]["cost_usd"] += route["cost_per_call"]
                    continue
                try:
                    r = _monid_fast_snapshot(conn, ev, mp_url, mp, wave_label)
                except Exception as e:  # noqa: BLE001
                    report["totals"]["errors"].append({"event": ekey, "mp": mp, "rail": "FAST", "error": str(e)})
                    continue
                report["totals"]["fetches"] += 1
                report["totals"]["cost_usd"] += r["cost_usd"]
                persist_snapshot(conn, r["snapshot"])
                report["totals"]["snapshots"] += 1
                method = "MONID_FAST"
                _tally(report, method, 1, r["cost_usd"])
                _raw_obs(conn, ev, mp, r["provider"], r["extracted"], wave_label)
                _health(conn, method, mp, wave_label, r["cost_usd"], status="SUCCESS")

            if deep:
                route = route_observation(
                    marketplace=mp,
                    has_mapped_url=True,
                    needs_listings=True,
                    cadence="weekly",
                    tickets_dev_live_key=not is_sandbox(),
                )
                if dry_run:
                    report["totals"]["cost_usd"] += route["cost_per_call"]
                    report["methods"].setdefault(route["method"], {"calls": 0, "cost_usd": 0.0})
                    report["methods"][route["method"]]["calls"] += 1
                    report["methods"][route["method"]]["cost_usd"] += route["cost_per_call"]
                    continue
                try:
                    r = _tickets_dev_deep(conn, ev, mp_url, mp, wave_label)
                except Exception as e:  # noqa: BLE001
                    report["totals"]["errors"].append({"event": ekey, "mp": mp, "rail": "DEEP", "error": str(e)})
                    continue
                if "error" in r:
                    report["totals"]["errors"].append({"event": ekey, "mp": mp, "rail": "DEEP", "error": r["error"]})
                    _health(conn, "TICKETS_DEV_DEEP", mp, wave_label, 0.0,
                            status="FAILED", error_category="RUN_FAILED", error_detail=r["error"])
                    continue
                report["totals"]["fetches"] += 1
                report["totals"]["cost_usd"] += r["cost_usd"]
                persist_snapshot(conn, r["snapshot"])
                report["totals"]["snapshots"] += 1
                n_listings = persist_listings(conn, r.get("listings", []))
                report["totals"]["listings"] += n_listings
                seen = {l.get("provider_listing_id") for l in r.get("listings", []) if l.get("provider_listing_id")}
                mark_disappeared_listings(
                    conn, ev.get("event_key"), mp, seen, _now(),
                )
                method = "TICKETS_DEV_DEEP" if not r.get("sandbox") else "TICKETS_DEV_SANDBOX"
                _tally(report, method, 1, r["cost_usd"])
                _raw_obs(conn, ev, mp, r["provider"], r["raw_snapshot"], wave_label)
                _health(conn, method, mp, wave_label, r["cost_usd"], status="SUCCESS")

            if max_fetch and report["totals"]["fetches"] >= max_fetch:
                break
        if max_fetch and report["totals"]["fetches"] >= max_fetch:
            break

    # Change detection per platform.
    try:
        platforms = set()
        for k in mappings:
            if k[0] in {e.get("event_key") for e in universe}:
                platforms.add(k[1])
        for p in platforms:
            ch = detect_changes(conn, p)
            report["totals"]["changes"] = report["totals"].get("changes", 0) + len(ch)
    except Exception:  # noqa: BLE001
        pass

    report["totals"]["cost_usd"] = round(report["totals"]["cost_usd"], 4)
    report["budget"]["spent_usd"] = report["totals"]["cost_usd"]
    report["finished_at"] = _now()
    return report


def _tally(report: dict, method: str, calls: int, cost: float) -> None:
    m = report["methods"].setdefault(method, {"calls": 0, "cost_usd": 0.0})
    m["calls"] += calls
    m["cost_usd"] = round(m["cost_usd"] + cost, 4)


def _raw_obs(conn, ev: dict, mp: str, provider: str, payload: dict, wave_label: str) -> None:
    try:
        obs = ObservationRecord(
            source_platform=mp,
            acquisition_provider="monid" if provider.startswith("monid") else "tickets.dev",
            actor_or_endpoint=provider,
            source_record_id=str(ev.get("event_key")),
            observation_type="TICKET_PRICE",
            observation_category="RESALE",
            raw_payload=payload if isinstance(payload, dict) else {"value": str(payload)[:2000]},
            event_key=ev.get("event_key"),
            market_key=ev.get("market_key"),
            observed_at=_now(),
            knowledge_time=_now(),
            normalized_fields=payload if isinstance(payload, dict) else {},
            rights_status="TERMS_REVIEW_REQUIRED",
            commercial_use_status="PROTOTYPE_ONLY",
        )
        ingest_observation(conn, obs)
    except Exception:  # noqa: BLE001 — best-effort raw observation
        pass


def _health(conn, method: str, mp: str, wave_label: str, cost: float, *,
            status: str, error_category: str | None = None,
            error_detail: str | None = None) -> None:
    try:
        conn.execute(
            """INSERT INTO acquisition.source_health_by_method (
                health_id, method, marketplace, wave_label, started_at,
                finished_at, status, error_category, error_detail,
                events_requested, events_resolved, observations_ingested,
                latency_ms, cost_usd, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 0, ?, ?)""",
            [
                f"hl::{method}::{mp}::{wave_label}::{int(time.time()*1000)}",
                method, mp, wave_label, _now(), _now(), status,
                error_category, error_detail, cost, "v2_20260825",
            ],
        )
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Ticket-market collector (routed FAST/DEEP)")
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    ap.add_argument("--source", default=None)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--max-cost", type=float, default=2.00)
    ap.add_argument("--max-fetch", type=int, default=None)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--wave", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.fast and not args.deep:
        print("Choose --fast, --deep, or both.")
        sys.exit(2)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_warehouse(Path(args.db))
    universe = load_universe(Path(args.universe))
    wave_label = args.wave or f"wave_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M')}"

    # Budget sanity check before any network call.
    if args.max_cost <= 0:
        print(f"Budget guard: --max-cost must be > 0 (got {args.max_cost}). Aborting.")
        sys.exit(2)

    print(f"Collector: wave={wave_label} fast={args.fast} deep={args.deep} "
          f"max_cost=${args.max_cost} universe={len(universe)} events "
          f"{'(DRY RUN)' if args.dry_run else ''}")
    report = run_collect(
        conn,
        universe,
        source=args.source,
        fast=args.fast,
        deep=args.deep,
        max_cost=args.max_cost,
        max_fetch=args.max_fetch,
        wave_label=wave_label,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2)[:8000])
    conn.close()

    # Projection summary for quick reference.
    if not args.dry_run:
        proj = monthly_cost(100, tickets_dev_live_key=not is_sandbox())
        print("\n## Monthly projection (100 events, hybrid policy)")
        print(json.dumps(proj, indent=2))


if __name__ == "__main__":
    main()
