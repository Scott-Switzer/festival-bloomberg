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
  - hard budget: every paid call is PRE-AUTHORIZED (spent + expected_cost
    must fit inside --max-cost) before any network request
  - DEEP honors the router: without a live TICKETS_DEV_API_KEY the DEEP rail
    is DEEP_UNAVAILABLE — tickets.dev sandbox fixtures NEVER enter the
    warehouse (they are test-DB-only)
  - one bounded retry per URL on retryable failures (timeout, 502/503,
    transport); auth / marketplace mismatch / budget blocks are not retried
  - raw payloads are content-addressed into raw_evidence_store (hash dedup)
  - identity comes from acquisition.event_identifiers (canonical security
    master), the same contract the URL resolver and Buyer Workspace use
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
)
from festival_bloomberg.evidence_rails.tickets_dev import (
    capture,
    normalize_capture_snapshot,
    listings_from_snapshot,
    persist_listings,
    mark_disappeared_listings,
    is_sandbox,
    persist_raw_evidence,
)
from festival_bloomberg.evidence_rails.ticket_market import (
    persist_snapshot,
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

# Retryable failure markers — everything else (auth, marketplace mismatch,
# budget block, unsupported URL) must NOT be retried.
RETRYABLE_MARKERS = ("timeout", "timed out", "502", "503", "transport", "connection", "temporarily")

MAX_ATTEMPTS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    apply_pending_migrations(conn)
    return conn


def _is_retryable(err: str) -> bool:
    el = (err or "").lower()
    return any(m in el for m in RETRYABLE_MARKERS)


def _with_retry(fn, *args, **kwargs):
    """Run fn with one bounded retry on retryable failures.

    Returns (result, attempts). Non-retryable failures (auth, mismatch,
    budget, unknown) return immediately after one attempt.
    """
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("error"):
                err = str(result.get("detail") or result.get("error") or "")
                if attempt < MAX_ATTEMPTS and _is_retryable(err):
                    last = result
                    time.sleep(1.0)
                    continue
                return result, attempt
            return result, attempt
        except Exception as e:  # noqa: BLE001 — bounded transport retry
            err = str(e)
            if attempt < MAX_ATTEMPTS and _is_retryable(err):
                last = {"error": err}
                time.sleep(1.0)
                continue
            return {"error": err}, attempt
    return last or {"error": "unknown failure"}, MAX_ATTEMPTS


def _load_mappings(conn) -> dict[tuple[str, str], dict]:
    """Load MATCHED mappings from the CANONICAL security master
    (acquisition.event_identifiers)."""
    cur = conn.execute(
        """SELECT event_key, marketplace, marketplace_event_id,
                  marketplace_event_url, mapping_status, confidence
           FROM acquisition.event_identifiers
           WHERE mapping_status IN ('EXACT_PROVIDER_ID', 'EXACT_PAGE_MATCH', 'HIGH_CONFIDENCE')
             AND marketplace_event_url IS NOT NULL"""
    )
    cols = [c[0] for c in cur.description] if cur.description else []
    out = {}
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        out[(d["event_key"], d["marketplace"])] = {
            "event_key": d["event_key"],
            "marketplace": d["marketplace"],
            "marketplace_event_id": d["marketplace_event_id"],
            "marketplace_event_url": d["marketplace_event_url"],
            "resolution_status": d["mapping_status"],
            "resolution_confidence": d["confidence"],
        }
    return out


def _monid_fast_snapshot(conn, ev: dict, mp_url: str, mp: str, wave_label: str) -> dict:
    """FAST rail: Monid targeted fetch → JSON-LD extraction → snapshot.

    Returns an error dict when the fetch did not produce a usable page
    (so the retry wrapper can retry once).
    """
    page = fetch_page(mp_url, fetch_provider="context.dev")
    if page.get("status") != "FETCHED":
        return {"error": page.get("status") or "FETCH_FAILED"}
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

    raw = persist_raw_evidence(
        conn,
        event_key=ev.get("event_key"),
        marketplace=mp,
        payload=pg if isinstance(pg, dict) else {"content": str(pg)[:4000]},
        payload_type="HTML_JSON",
    )

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
            "raw_payload_hash": raw.get("payload_hash"),
            "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY",
        },
        "cost_usd": cost_usd,
        "provider": f"monid_{page.get('provider')}",
        "extracted": extracted,
    }


def _tickets_dev_deep(conn, ev: dict, mp_url: str, mp: str, wave_label: str) -> dict:
    """DEEP rail: tickets.dev capture → normalized snapshot + listings.

    Only ever called when a LIVE TICKETS_DEV_API_KEY is configured (the
    router gates this). Sandbox fixtures never reach this path.
    """
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
    raw = persist_raw_evidence(
        conn,
        event_key=ev.get("event_key"),
        marketplace=mp,
        payload=snapshot,
        payload_type="SNAPSHOT_JSON",
    )
    norm["raw_payload_hash"] = raw.get("payload_hash")
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
    tickets_dev_live_key: bool | None = None,
    cohort_version: str | None = None,
    due_only: bool = False,
) -> dict:
    """Run one observation wave. Returns a structured run report.

    tickets_dev_live_key: override sandbox detection (tests use this to
    prove fixtures never reach the warehouse). Defaults to the configured
    key state.

    due_only: when True, select pairs via lifecycle cadence schedule
    (nearest / weakest depth / stale) instead of scanning the full universe.
    """
    if tickets_dev_live_key is None:
        tickets_dev_live_key = not is_sandbox()
    mappings = _load_mappings(conn)

    from festival_bloomberg.evidence_rails.collection_ledger import finish_run, start_run
    from festival_bloomberg.evidence_rails.cohort_cadence import prioritize_due_pairs

    run_id = f"run_{wave_label}_{int(time.time())}"
    candidate_pairs = len(mappings)
    due_pairs_meta: list[dict] = []
    try:
        rows = conn.execute(
            """SELECT s.event_key, s.marketplace, s.lifecycle_bucket, s.cadence_label,
                      s.next_due_at, s.observation_count, s.last_succeeded_at,
                      c.event_date
               FROM acquisition.ticket_market_pair_schedule s
               LEFT JOIN acquisition.ticket_market_cohort_pairs c
                 ON c.event_key = s.event_key AND c.marketplace = s.marketplace
                AND (? IS NULL OR c.cohort_version = ?)""",
            [cohort_version, cohort_version],
        ).fetchall()
        cols = ["event_key", "marketplace", "lifecycle_bucket", "cadence_label",
                "next_due_at", "observation_count", "last_succeeded_at", "event_date"]
        due_pairs_meta = [dict(zip(cols, r)) for r in rows]
    except Exception:  # noqa: BLE001 — schedule table may be absent on older DBs
        due_pairs_meta = []

    if due_only and due_pairs_meta:
        due = prioritize_due_pairs(due_pairs_meta, limit=max_fetch)
        universe = [
            next((e for e in universe if e.get("event_key") == p["event_key"]), {"event_key": p["event_key"]})
            for p in due
        ]
        # Restrict mappings iteration implicitly via filtered universe.
    due_count = len(prioritize_due_pairs(due_pairs_meta)) if due_pairs_meta else candidate_pairs

    try:
        start_run(
            conn,
            run_id=run_id,
            cohort_version=cohort_version,
            rail="FAST" if fast and not deep else ("DEEP" if deep and not fast else "FAST+DEEP"),
            wave_label=wave_label,
            budget_cap_usd=max_cost,
            candidate_pairs=candidate_pairs,
            due_pairs=due_count,
        )
    except Exception:  # noqa: BLE001 — ledger optional on pre-051 DBs
        run_id = ""

    report: dict = {
        "wave_label": wave_label,
        "run_id": run_id or None,
        "cohort_version": cohort_version,
        "mode": "FAST+DEEP" if fast and deep else ("FAST" if fast else "DEEP"),
        "started_at": _now(),
        "budget": {"max_cost_usd": max_cost, "spent_usd": 0.0},
        "methods": {},
        "totals": {"attempted": 0, "fetches": 0, "snapshots": 0, "listings": 0,
                   "cost_usd": 0.0, "errors": [], "warnings": [], "skipped_budget": 0,
                   "skipped_deep_no_live_key": 0, "retry_count": 0,
                   "budget_cap_usd": max_cost},
    }

    def _can_afford(expected: float) -> bool:
        """HARD budget: pre-authorize the next call before any network I/O."""
        return report["totals"]["cost_usd"] + expected <= max_cost + 1e-9

    def _tally(method: str, calls: int, cost: float) -> None:
        m = report["methods"].setdefault(method, {"calls": 0, "cost_usd": 0.0})
        m["calls"] += calls
        m["cost_usd"] = round(m["cost_usd"] + cost, 4)

    def _record_health(method: str, mp: str, cost: float, *, status: str,
                       error_category: str | None = None,
                       error_detail: str | None = None,
                       attempts: int = 1) -> None:
        ok = _health(conn, method, mp, wave_label, cost, status=status,
                     error_category=error_category, error_detail=error_detail,
                     attempts=attempts)
        if not ok:
            report["totals"]["warnings"].append(
                {"event": None, "mp": mp, "rail": method, "warning": "source-health persistence failed"})

    for ev in universe:
        if not _can_afford(0.0):
            break
        ekey = ev.get("event_key")

        # Which marketplaces do we have mapped URLs for?
        mp_keys = [k[1] for k in mappings if k[0] == ekey]
        if source:
            mp_keys = [m for m in mp_keys if m.startswith(source)]
        if not mp_keys:
            continue

        for mp in mp_keys:
            if not _can_afford(0.0):
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
                if not _can_afford(route["cost_per_call"]):
                    report["totals"]["skipped_budget"] += 1
                    break
                if dry_run:
                    report["totals"]["cost_usd"] += route["cost_per_call"]
                    _tally(route["method"], 1, route["cost_per_call"])
                    continue
                r, attempts = _with_retry(
                    _monid_fast_snapshot, conn, ev, mp_url, mp, wave_label,
                )
                if "error" in r:
                    report["totals"]["errors"].append(
                        {"event": ekey, "mp": mp, "rail": "FAST", "error": r["error"], "attempts": attempts})
                    _record_health("MONID_FAST", mp, 0.0, status="FAILED",
                                   error_category="FETCH_FAILED",
                                   error_detail=r["error"], attempts=attempts)
                    continue
                report["totals"]["fetches"] += 1
                report["totals"]["cost_usd"] += r["cost_usd"]
                persist_snapshot(conn, r["snapshot"])
                report["totals"]["snapshots"] += 1
                _tally("MONID_FAST", 1, r["cost_usd"])
                _raw_obs(conn, ev, mp, r["provider"], r["extracted"], wave_label)
                _record_health("MONID_FAST", mp, r["cost_usd"], status="SUCCESS",
                               attempts=attempts)

            if deep:
                route = route_observation(
                    marketplace=mp,
                    has_mapped_url=True,
                    needs_listings=True,
                    cadence="weekly",
                    tickets_dev_live_key=tickets_dev_live_key,
                )
                if route["method"] != "TICKETS_DEV_DEEP":
                    # No live tickets.dev key → the DEEP rail is unavailable.
                    # Sandbox fixtures are test-DB-only and must NEVER enter
                    # the warehouse.
                    report["totals"]["skipped_deep_no_live_key"] += 1
                    _tally("DEEP_UNAVAILABLE", 1, 0.0)
                    _record_health("DEEP_UNAVAILABLE", mp, 0.0, status="SKIPPED",
                                   error_category="NO_LIVE_TICKETS_DEV_KEY",
                                   error_detail="tickets.dev sandbox fixtures never enter the warehouse")
                    continue
                if not _can_afford(route["cost_per_call"]):
                    report["totals"]["skipped_budget"] += 1
                    break
                if dry_run:
                    report["totals"]["cost_usd"] += route["cost_per_call"]
                    _tally(route["method"], 1, route["cost_per_call"])
                    continue
                r, attempts = _with_retry(
                    _tickets_dev_deep, conn, ev, mp_url, mp, wave_label,
                )
                if "error" in r:
                    report["totals"]["errors"].append(
                        {"event": ekey, "mp": mp, "rail": "DEEP", "error": r["error"], "attempts": attempts})
                    _record_health("TICKETS_DEV_DEEP", mp, 0.0, status="FAILED",
                                   error_category="RUN_FAILED",
                                   error_detail=r["error"], attempts=attempts)
                    continue
                report["totals"]["fetches"] += 1
                report["totals"]["cost_usd"] += r["cost_usd"]
                sid = persist_snapshot(conn, r["snapshot"])
                report["totals"]["snapshots"] += 1
                n_listings = persist_listings(
                    conn, r.get("listings", []),
                    source_snapshot_id=sid,
                    raw_payload_hash=r["snapshot"].get("raw_payload_hash"),
                )
                report["totals"]["listings"] += n_listings
                seen = {l.get("provider_listing_id") for l in r.get("listings", []) if l.get("provider_listing_id")}
                mark_disappeared_listings(
                    conn, ev.get("event_key"), mp, seen, _now(),
                )
                method = "TICKETS_DEV_DEEP" if not r.get("sandbox") else "TICKETS_DEV_SANDBOX"
                _tally(method, 1, r["cost_usd"])
                _raw_obs(conn, ev, mp, r["provider"], r["raw_snapshot"], wave_label)
                _record_health(method, mp, r["cost_usd"], status="SUCCESS",
                               attempts=attempts)

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
    if run_id:
        try:
            status = "BUDGET_STOPPED" if report["totals"]["skipped_budget"] else "COMPLETE"
            err_classes: dict[str, int] = {}
            for e in report["totals"]["errors"]:
                key = str(e.get("error") or "UNKNOWN")[:80]
                err_classes[key] = err_classes.get(key, 0) + 1
            report["totals"]["error_classes"] = err_classes
            finish_run(conn, run_id, report["totals"], status=status)
        except Exception:  # noqa: BLE001
            report["totals"]["warnings"].append({"warning": "collection-run ledger finish failed"})
    return report


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
            error_detail: str | None = None, attempts: int = 1) -> bool:
    """Persist one source-health row. Returns False when persistence fails
    so callers surface a warning instead of silently losing health data."""
    try:
        conn.execute(
            """INSERT INTO acquisition.source_health_by_method (
                health_id, method, marketplace, wave_label, started_at,
                finished_at, status, error_category, error_detail,
                events_requested, events_resolved, observations_ingested,
                latency_ms, cost_usd, schema_version, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 0, ?, ?, ?)""",
            [
                f"hl::{method}::{mp}::{wave_label}::{int(time.time()*1000)}",
                method, mp, wave_label, _now(), _now(), status,
                error_category, error_detail, cost, "v2_20260825",
                f"attempts={attempts}",
            ],
        )
        return True
    except Exception:  # noqa: BLE001 — surface to caller as a warning
        return False


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
    ap.add_argument("--cohort-version", default=None)
    ap.add_argument("--due-only", action="store_true",
                    help="lifecycle-due pairs only (nearest / shallow / stale)")
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
          f"cohort={args.cohort_version} due_only={args.due_only} "
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
        cohort_version=args.cohort_version,
        due_only=args.due_only,
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
