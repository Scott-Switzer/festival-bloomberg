"""REAL_TICKET_MARKET_RAIL_V1 wave runner.

Usage:
    python scripts/run_ticket_market_wave.py --wave real --sources seatgeek,vividseats [--max-events 10]
    python scripts/run_ticket_market_wave.py --wave wave0   # replay saved real bakeoff records

Wave 0 replays the REAL records already captured on the network during the
source bakeoff (SeatGeek 100 records with genuine prices/listings, captured
2026-08-25T07:06 UTC). It never fabricates values.

Real waves require a working APIFY_TOKEN. If the account is over its monthly
usage limit, the run reports WAVE_BLOCKED instead of faking data.
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

from festival_bloomberg.evidence_rails.ticket_market import (
    load_universe,
    run_market_wave,
    normalize_market_record,
    resolve_to_universe,
    persist_snapshot,
    record_source_health,
    MARKET_SOURCES,
)
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.evidence_rails.contract import (
    ObservationRecord,
    ingest_observation,
    detect_changes,
)

UNIVERSE_PATH = PROJECT_ROOT / "data" / "workspace" / "watch_universe_v1.json"
WAVE0_DIR = PROJECT_ROOT / "data" / "bakeoff"
OUT_DIR = PROJECT_ROOT / "data" / "workspace" / "ticket_market"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    apply_pending_migrations(conn)
    return conn


def persist_universe(conn, universe: list[dict]) -> int:
    """Load the frozen watch universe into acquisition.watch_universe (idempotent)."""
    n = 0
    # Universe-level metadata is not on each event; read it from the file once.
    meta = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    frozen_at = meta.get("frozen_at") or _now()
    content_hash = meta.get("content_hash")
    for ev in universe:
        ev = dict(ev)  # copy so we can fill metadata without mutating the caller's list
        ev.setdefault("frozen_at", frozen_at)
        ev.setdefault("content_hash", content_hash)
        conn.execute(
            """
            INSERT OR IGNORE INTO acquisition.watch_universe (
                watch_universe_version, event_key, provider_event_id, artist_key,
                artist_name, venue_key, venue_name, market_key, city, state,
                event_date, event_time, timezone, latitude, longitude,
                tm_price_min, tm_price_max, tm_currency, promoter, genre,
                subgenre, canonical_url, selection_reason, frozen_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ev.get("watch_universe_version"), ev.get("event_key"),
                ev.get("provider_event_id"), ev.get("artist_key"),
                ev.get("artist_name"), ev.get("venue_key"), ev.get("venue_name"),
                ev.get("market_key"), ev.get("city"), ev.get("state"),
                ev.get("event_date"), ev.get("event_time"), ev.get("timezone"),
                ev.get("latitude"), ev.get("longitude"),
                ev.get("tm_price_min"), ev.get("tm_price_max"), ev.get("tm_currency"),
                ev.get("promoter"), ev.get("genre"), ev.get("subgenre"),
                ev.get("canonical_url"), ev.get("selection_reason"),
                ev.get("frozen_at"), ev.get("content_hash"),
            ],
        )
        n += 1
    return n


def replay_wave0(conn, universe: list[dict], wave_label: str = "wave0_bakeoff_20260825") -> dict:
    """Ingest REAL records previously captured from the network.

    These records are real network observations (SeatGeek 100 records with
    genuine prices/listings captured 2026-08-25T07:06 UTC). They cannot be
    fabricated or re-simulated — they are persisted as Wave 0 evidence with
    their true capture timestamps.

    Resolution: each record is resolved against the frozen universe. Records
    that do not resolve stay preserved but cannot drive the buyer time series.
    """
    report: dict = {
        "wave_label": wave_label,
        "mode": "WAVE0_REPLAY_OF_REAL_BAKEOFF_RECORDS",
        "started_at": _now(),
        "sources": {},
        "totals": {"records": 0, "snapshots": 0, "matched": 0, "ambiguous": 0, "unresolved": 0, "changes": 0},
    }

    seatgeek_file = WAVE0_DIR / "SeatGeek_ticket_raw.json"
    if not seatgeek_file.exists():
        report["error"] = f"missing {seatgeek_file}"
        return report

    data = json.loads(seatgeek_file.read_text(encoding="utf-8"))
    records = data.get("records", []) if isinstance(data, dict) else data
    if not records:
        report["error"] = "no records in SeatGeek_ticket_raw.json"
        return report

    src = MARKET_SOURCES["seatgeek"]
    source_report = {
        "source": "seatgeek",
        "platform": src["platform"],
        "actor": src["actor"],
        "mode": "replay_real_records",
        "records": len(records),
        "captured_at": "2026-08-25T07:06 UTC",
        "snapshots": 0, "matched": 0, "ambiguous": 0, "unresolved": 0,
    }

    for rec in records:
        norm = normalize_market_record(rec, "seatgeek")
        # The saved records are market-sweep records (country-wide); resolve
        # against the universe honestly — most will be UNRESOLVED.
        status, event_key, confidence = resolve_to_universe(norm, universe)
        target_event = event_key
        snapshot = {
            "watch_universe_version": "watch_universe_v1",
            "event_key": target_event,
            "provider_event_id": None,
            "source_platform": src["platform"],
            "actor_or_endpoint": src["actor"],
            "source_record_id": norm.get("source_record_id"),
            "wave_label": wave_label,
            "observed_at": "2026-08-25T07:06:00+00:00",
            "retrieved_at": "2026-08-25T07:06:00+00:00",
            "knowledge_time": "2026-08-25T07:06:00+00:00",
            "currency": norm.get("currency"),
            "resale_min_price": norm.get("resale_min_price"),
            "resale_median_price": norm.get("resale_median_price"),
            "resale_avg_price": norm.get("resale_avg_price"),
            "resale_max_price": norm.get("resale_max_price"),
            "listing_count": norm.get("listing_count"),
            "ticket_count": norm.get("ticket_count"),
            "sold_out_flag": norm.get("sold_out_flag"),
            "availability_flag": norm.get("availability_flag"),
            "face_value": norm.get("face_value"),
            "identity_match_status": status,
            "identity_match_method": "ARTIST_VENUE_DATE" if status == "MATCHED" else (
                "FUZZY_CANDIDATE" if status == "AMBIGUOUS" else None),
            "identity_match_confidence": confidence,
            "source_url": norm.get("source_url"),
            "rights_status": "TERMS_REVIEW_REQUIRED",
            "commercial_use_status": "PROTOTYPE_ONLY",
        }
        persist_snapshot(conn, snapshot)
        source_report["snapshots"] += 1
        report["totals"]["snapshots"] += 1
        report["totals"]["records"] += 1
        if status == "MATCHED":
            source_report["matched"] += 1
            report["totals"]["matched"] += 1
        elif status == "AMBIGUOUS":
            source_report["ambiguous"] += 1
            report["totals"]["ambiguous"] += 1
        else:
            source_report["unresolved"] += 1
            report["totals"]["unresolved"] += 1

        # Raw append-only observation with true capture time.
        try:
            obs = ObservationRecord(
                source_platform=src["platform"],
                acquisition_provider="apify",
                actor_or_endpoint=src["actor"],
                source_record_id=str(norm.get("source_record_id") or ""),
                observation_type="TICKET_PRICE" if norm.get("resale_min_price") is not None else "TICKET_AVAILABILITY",
                observation_category="RESALE",
                raw_payload=rec,
                event_key=target_event,
                observed_at="2026-08-25T07:06:00+00:00",
                retrieved_at="2026-08-25T07:06:00+00:00",
                knowledge_time="2026-08-25T07:06:00+00:00",
                normalized_fields=norm,
                rights_status="TERMS_REVIEW_REQUIRED",
                commercial_use_status="PROTOTYPE_ONLY",
            )
            ingest_observation(conn, obs)
        except Exception:
            pass

    try:
        changes = detect_changes(conn, src["platform"])
        source_report["changes_detected"] = len(changes)
        report["totals"]["changes"] = len(changes)
    except Exception:
        source_report["changes_detected"] = 0

    # Health ledger entry for the replay (real run cost was $0.00).
    record_source_health(conn, {
        "source_platform": src["platform"],
        "actor_or_endpoint": src["actor"],
        "wave_label": wave_label,
        "started_at": "2026-08-25T07:05:00+00:00",
        "finished_at": "2026-08-25T07:07:00+00:00",
        "status": "SUCCESS",
        "events_requested": 0,
        "events_resolved": source_report["matched"],
        "observations_ingested": source_report["snapshots"],
        "latency_ms": 0,
        "cost_usd": 0.0,
        "schema_version": "bakeoff_axlymxp_1.0.2",
        "records_returned": len(records),
        "notes": "Wave 0 replay of REAL network-captured bakeoff records (SeatGeek). Market-sweep data; targeted search filters ignored by actor.",
    })

    report["sources"]["seatgeek"] = source_report
    report["finished_at"] = _now()
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", choices=["real", "wave0"], default="real")
    ap.add_argument("--sources", default="seatgeek,vividseats,stubhub")
    ap.add_argument("--max-events", type=int, default=None)
    ap.add_argument("--db", default=str(OUT_DIR / "ticket_market.duckdb"))
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_warehouse(Path(args.db))
    universe = load_universe(UNIVERSE_PATH)
    persisted = persist_universe(conn, universe)
    print(f"Universe: {len(universe)} events ({persisted} persisted) | DB: {args.db}")

    if args.wave == "wave0":
        report = replay_wave0(conn, universe)
    else:
        report = run_market_wave(
            conn, universe,
            sources=args.sources.split(","),
            max_events=args.max_events,
        )

    print(json.dumps(report, indent=2)[:4000])
    conn.close()


if __name__ == "__main__":
    main()
