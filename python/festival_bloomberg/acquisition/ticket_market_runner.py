"""Shared ticket-market acquisition runner.

Single production implementation used by:
  - Local: scripts/collect_ticket_market.py
  - Cloud: cloud_entrypoint.py

No separate implementations of routing, cost semantics, Monid fetch,
tickets.dev capture, raw evidence, normalization, listing lifecycle,
or persistence.

All economics and evidence contracts come from:
  - festival_bloomberg.evidence_rails.router
  - festival_bloomberg.evidence_rails.url_resolver
  - festival_bloomberg.evidence_rails.tickets_dev
  - festival_bloomberg.evidence_rails.ticket_market
  - festival_bloomberg.evidence_rails.contract
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from festival_bloomberg.evidence_rails.router import (
    route_observation,
    MEASURED_COST,
    COST_BASIS,
    RAIL_FAST,
    RAIL_DEEP,
)
from festival_bloomberg.evidence_rails.url_resolver import (
    fetch_page,
    extract_from_page,
)
from festival_bloomberg.evidence_rails.tickets_dev import (
    capture,
    normalize_capture_snapshot,
    listings_from_snapshot,
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
)

RETRYABLE_MARKERS = ("timeout", "timed out", "502", "503", "transport", "connection", "temporarily")
MAX_ATTEMPTS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_retryable(err: str) -> bool:
    el = (err or "").lower()
    return any(m in el for m in RETRYABLE_MARKERS)


def with_retry(fn, *args, **kwargs):
    """Run fn with one bounded retry on retryable failures. Returns (result, attempts)."""
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
        except Exception as e:
            err = str(e)
            if attempt < MAX_ATTEMPTS and _is_retryable(err):
                last = {"error": err}
                time.sleep(1.0)
                continue
            return {"error": err}, attempt
    return last or {"error": "unknown failure"}, MAX_ATTEMPTS


def load_mappings(conn) -> dict[tuple[str, str], dict]:
    """Load MATCHED mappings from the canonical security master."""
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


def collect_fast(conn, ev: dict, mp_url: str, mp: str, wave_label: str) -> dict:
    """FAST rail: Monid targeted fetch → JSON-LD extraction → snapshot.

    Returns an error dict when the fetch did not produce a usable page.
    Cost: uses the REAL router economics (MONID_HTML = $0.0009 MEASURED,
    MONID_FETCH = $0 when free tier succeeds).
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
        "cost_basis": "MEASURED",
        "provider": f"monid_{page.get('provider')}",
        "extracted": extracted,
    }


def collect_deep(conn, ev: dict, mp_url: str, mp: str, wave_label: str) -> dict:
    """DEEP rail: tickets.dev capture → normalized snapshot + listings.

    Only called when a LIVE TICKETS_DEV_API_KEY is configured.
    Sandbox fixtures NEVER reach this path.
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
        "cost_basis": "CONTRACT_VALIDATED_ONLY" if res.get("sandbox") else COST_BASIS["TICKETS_DEV_CAPTURE"],
        "provider": f"tickets_dev_capture:{src}",
        "sandbox": res.get("sandbox"),
        "raw_snapshot": snapshot,
        "listings": listings_from_snapshot(
            snapshot,
            event_key=ev.get("event_key"),
            wave_label=wave_label,
        ),
    }


def run_observation_wave(
    conn,
    universe: list[dict],
    *,
    source: str | None = None,
    fast: bool = True,
    deep: bool = False,
    max_cost: float = 2.00,
    max_fetch: int | None = None,
    wave_label: str = "cloud_wave",
    dry_run: bool = False,
    tickets_dev_live_key: bool | None = None,
) -> dict:
    """Run one observation wave. Returns a structured run report.

    This is the SINGLE production implementation used by both local and cloud.
    """
    if tickets_dev_live_key is None:
        tickets_dev_live_key = not is_sandbox()
    mappings = load_mappings(conn)
    report: dict = {
        "wave_label": wave_label,
        "mode": "FAST+DEEP" if fast and deep else ("FAST" if fast else "DEEP"),
        "started_at": _now(),
        "budget": {"max_cost_usd": max_cost, "spent_usd": 0.0},
        "methods": {},
        "totals": {
            "attempted": 0, "fetches": 0, "snapshots": 0, "listings": 0,
            "cost_usd": 0.0, "errors": [], "warnings": [],
            "skipped_budget": 0, "skipped_deep_no_live_key": 0,
        },
    }

    fetch_count = 0

    def _can_afford(expected: float) -> bool:
        return report["totals"]["cost_usd"] + expected <= max_cost + 1e-9

    def _tally(method: str, calls: int, cost: float) -> None:
        m = report["methods"].setdefault(method, {"calls": 0, "cost_usd": 0.0})
        m["calls"] += calls
        m["cost_usd"] = round(m["cost_usd"] + cost, 4)

    for ev in universe:
        if not _can_afford(0.0):
            break
        if max_fetch is not None and fetch_count >= max_fetch:
            break

        ekey = ev.get("event_key")
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
                    marketplace=mp, has_mapped_url=True,
                    needs_listings=False, cadence="daily",
                )
                if not _can_afford(route["cost_per_call"]):
                    report["totals"]["skipped_budget"] += 1
                    break
                if dry_run:
                    report["totals"]["cost_usd"] += route["cost_per_call"]
                    _tally(route["method"], 1, route["cost_per_call"])
                    continue
                r, attempts = with_retry(
                    collect_fast, conn, ev, mp_url, mp, wave_label,
                )
                if "error" in r:
                    report["totals"]["errors"].append({
                        "event": ekey, "mp": mp, "rail": "FAST",
                        "error": r["error"], "attempts": attempts,
                    })
                    continue
                report["totals"]["fetches"] += 1
                fetch_count += 1
                report["totals"]["cost_usd"] += r["cost_usd"]
                persist_snapshot(conn, r["snapshot"])
                report["totals"]["snapshots"] += 1
                _tally("MONID_FAST", 1, r["cost_usd"])

            if deep:
                route = route_observation(
                    marketplace=mp, has_mapped_url=True,
                    needs_listings=True, cadence="weekly",
                    tickets_dev_live_key=tickets_dev_live_key,
                )
                if route["method"] != "TICKETS_DEV_DEEP":
                    report["totals"]["skipped_deep_no_live_key"] += 1
                    _tally("DEEP_UNAVAILABLE", 1, 0.0)
                    continue
                if not _can_afford(route["cost_per_call"]):
                    report["totals"]["skipped_budget"] += 1
                    break
                if dry_run:
                    report["totals"]["cost_usd"] += route["cost_per_call"]
                    _tally(route["method"], 1, route["cost_per_call"])
                    continue
                r, attempts = with_retry(
                    collect_deep, conn, ev, mp_url, mp, wave_label,
                )
                if "error" in r:
                    report["totals"]["errors"].append({
                        "event": ekey, "mp": mp, "rail": "DEEP",
                        "error": r["error"], "attempts": attempts,
                    })
                    continue
                if r.get("sandbox"):
                    report["totals"]["errors"].append({
                        "event": ekey, "mp": mp, "rail": "DEEP",
                        "error": "SANDBOX_FIXTURE_REJECTED",
                        "attempts": attempts,
                    })
                    continue
                report["totals"]["fetches"] += 1
                fetch_count += 1
                report["totals"]["cost_usd"] += r["cost_usd"]
                persist_snapshot(conn, r["snapshot"])
                report["totals"]["snapshots"] += 1
                if r.get("listings"):
                    report["totals"]["listings"] += len(r["listings"])
                _tally("TICKETS_DEV_DEEP", 1, r["cost_usd"])

    report["finished_at"] = _now()
    report["budget"]["spent_usd"] = round(report["totals"]["cost_usd"], 4)
    return report
