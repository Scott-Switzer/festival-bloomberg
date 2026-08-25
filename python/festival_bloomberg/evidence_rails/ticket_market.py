"""Real ticket-market observation rail.

Observes a frozen watch universe of real music events through external
marketplaces (SeatGeek, Vivid Seats, StubHub, ...) and writes normalized,
append-only market snapshots into the evidence estate.

Every marketplace observation is resolved against the frozen universe
(artist + venue + date) before it may drive the buyer-facing time series.
AMBIGUOUS and UNRESOLVED rows are preserved but cannot drive the time series.

Marketplace numbers are LISTING / AVAILABILITY PROXIES:
  - listing_count / ticket_count are NOT tickets sold
  - sold_out_flag is a marketplace availability state, not demand
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import ObservationRecord, ingest_observation, detect_changes

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from festival_bloomberg.acquisition.apify_direct import run_actor, inspect_actor
from festival_bloomberg.localenv import load_local_env


# ── Source registry ────────────────────────────────────────────────────

MARKET_SOURCES = {
    "seatgeek": {
        "platform": "seatgeek.com",
        "actor": "axlymxp~seatgeek-event-scraper",
        "category": "RESALE",
    },
    "vividseats": {
        "platform": "vividseats.com",
        "actor": "hoholabs~vividseats-scraper",
        "category": "RESALE",
    },
    "stubhub": {
        "platform": "stubhub.com",
        "actor": "lentic_clockss~stubhub-scraper",
        "category": "RESALE",
    },
    "gametime": {
        "platform": "gametime.co",
        "actor": "lexis-solutions~gametime-scraper",
        "category": "RESALE",
    },
    "tickpick": {
        "platform": "tickpick.com",
        "actor": "automation-lab~tickpick-events-tickets-scraper",
        "category": "RESALE",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Input builders (bounded, schema-driven) ───────────────────────────

def build_source_input(
    source_key: str,
    *,
    artist_name: str,
    city: str,
    state: str,
    event_date: str,
    max_items: int = 10,
) -> dict[str, Any]:
    """Build a bounded input body for one marketplace query.

    The query is scoped to a single artist + city + event date so results
    resolve cleanly to the watch universe. Never unbounded.
    """
    if source_key == "seatgeek":
        return {
            "searchQuery": artist_name,
            "category": "concert",
            "city": city,
            "state": state,
            "dateFrom": event_date,
            "dateTo": event_date,
            "maxItems": max_items,
            "sort": "date,asc",
        }
    if source_key == "vividseats":
        return {
            "queryType": "events",
            "q": artist_name,
            "dateFrom": event_date,
            "dateTo": event_date,
            "rows": max_items,
        }
    if source_key == "stubhub":
        # StubHub event-level lookup via mode=eventSearch + query.
        return {
            "mode": "eventSearch",
            "query": artist_name,
            "city": city,
            "maxResults": max_items,
            "includeListings": True,
        }
    if source_key == "gametime":
        # URL-only actor — needs explicit event URLs, not search.
        return {"startUrls": [], "maxItems": max_items}
    if source_key == "tickpick":
        return {
            "searchQueries": [f"{artist_name} {city}"],
            "maxItems": max_items,
        }
    return {}


# ── Normalization ──────────────────────────────────────────────────────

def normalize_market_record(
    record: dict[str, Any],
    source_key: str,
) -> dict[str, Any]:
    """Extract normalized market fields from one marketplace record.

    Returns a dict with keys matching acquisition.ticket_market_snapshots.
    Only fields the source genuinely exposes are set; the rest stay None.
    """
    n: dict[str, Any] = {}
    rec = record or {}

    # Source record id + url
    if source_key == "seatgeek":
        n["source_record_id"] = str(rec.get("event_id") or "")
        n["source_url"] = rec.get("url")
        n["resale_min_price"] = _num(rec.get("lowest_price"))
        n["resale_median_price"] = _num(rec.get("median_price"))
        n["resale_avg_price"] = _num(rec.get("average_price"))
        n["resale_max_price"] = _num(rec.get("highest_price"))
        n["listing_count"] = _int(rec.get("listing_count") or rec.get("visible_listing_count"))
        n["ticket_count"] = _int(rec.get("ticket_count"))
        n["face_value"] = _num(rec.get("lowest_sg_base_price"))
        n["sold_out_flag"] = rec.get("status") == "sold_out" or (
            rec.get("is_sold_out") is True
        )
        n["availability_flag"] = rec.get("status") in ("normal", "available") or (
            rec.get("is_available") is True
        )
        n["artist_name"] = (
            rec.get("primary_performer") or rec.get("performers", [{}])[0].get("name")
            if isinstance(rec.get("performers"), list) and rec.get("performers")
            else rec.get("primary_performer")
        )
        n["venue_name"] = rec.get("venue_name")
        n["venue_city"] = rec.get("venue_city")
        n["venue_state"] = rec.get("venue_state")
        n["event_date"] = str(rec.get("datetime_local") or "")[:10]

    elif source_key == "vividseats":
        n["source_record_id"] = str(rec.get("event_id") or rec.get("id") or "")
        n["source_url"] = rec.get("event_url") or rec.get("url")
        n["resale_min_price"] = _num(rec.get("minPrice") or rec.get("lowest_price"))
        n["resale_median_price"] = _num(rec.get("medianPrice"))
        n["resale_avg_price"] = _num(rec.get("avgPrice") or rec.get("average_price"))
        n["resale_max_price"] = _num(rec.get("maxPrice") or rec.get("highest_price"))
        n["listing_count"] = _int(rec.get("listingCount") or rec.get("listing_count"))
        n["ticket_count"] = _int(rec.get("ticketCount") or rec.get("ticket_count"))
        n["face_value"] = _num(rec.get("faceValue") or rec.get("face_value"))
        n["sold_out_flag"] = rec.get("soldOut") is True or rec.get("is_sold_out") is True
        n["availability_flag"] = rec.get("isAvailable") is True or rec.get("available") is True
        n["artist_name"] = rec.get("performer") or rec.get("artist") or rec.get("headliner")
        venue_raw = rec.get("venue") or rec.get("venue_name")
        if isinstance(venue_raw, dict):
            n["venue_name"] = venue_raw.get("name")
            n["venue_city"] = venue_raw.get("city") or venue_raw.get("venueCity")
            n["venue_state"] = venue_raw.get("state") or venue_raw.get("venueState")
        else:
            n["venue_name"] = venue_raw
            n["venue_city"] = rec.get("venueCity") or rec.get("city")
            n["venue_state"] = rec.get("venueState") or rec.get("state")
        n["event_date"] = str(rec.get("eventDate") or rec.get("date") or "")[:10]

    elif source_key == "stubhub":
        n["source_record_id"] = str(rec.get("eventId") or rec.get("id") or "")
        n["source_url"] = rec.get("eventUrl") or rec.get("url")
        # StubHub event search returns listing-level rows when includeListings.
        listings = rec.get("listings") or []
        if listings:
            prices = [
                _num(l.get("price") or l.get("allInPrice") or l.get("faceValue"))
                for l in listings if _num(l.get("price") or l.get("allInPrice"))
            ]
            if prices:
                n["resale_min_price"] = min(prices)
                n["resale_max_price"] = max(prices)
                n["resale_avg_price"] = round(sum(prices) / len(prices), 2)
                n["resale_median_price"] = _median(prices)
            n["listing_count"] = len(listings)
            n["ticket_count"] = sum(
                _int(l.get("quantity") or 1) for l in listings
            ) or None
            first = listings[0]
            n["section"] = first.get("section")
            n["row_label"] = first.get("row")
            n["quantity"] = _int(first.get("quantity"))
            n["face_value"] = _num(first.get("faceValue"))
            n["all_in_price"] = _num(first.get("allInPrice"))
        n["artist_name"] = rec.get("performer") or rec.get("eventName")
        n["venue_name"] = rec.get("venueName") or rec.get("venue")
        n["venue_city"] = rec.get("venueCity") or rec.get("city")
        n["event_date"] = str(rec.get("eventDate") or rec.get("date") or "")[:10]

    elif source_key == "gametime":
        n["source_record_id"] = str(rec.get("event_id") or rec.get("id") or "")
        n["source_url"] = rec.get("url")
        n["resale_min_price"] = _num(rec.get("lowest_price") or rec.get("min_price"))
        n["resale_max_price"] = _num(rec.get("highest_price") or rec.get("max_price"))
        n["listing_count"] = _int(rec.get("listing_count") or rec.get("inventory_count"))
        n["artist_name"] = rec.get("event_name") or rec.get("name")
        n["venue_name"] = rec.get("venue_name") or rec.get("venue")
        n["event_date"] = str(rec.get("event_date") or rec.get("date") or "")[:10]

    elif source_key == "tickpick":
        n["source_record_id"] = str(rec.get("event_id") or rec.get("id") or "")
        n["source_url"] = rec.get("url")
        n["resale_min_price"] = _num(rec.get("lowest_price") or rec.get("min_price"))
        n["resale_max_price"] = _num(rec.get("highest_price") or rec.get("max_price"))
        n["listing_count"] = _int(rec.get("listing_count"))
        n["ticket_count"] = _int(rec.get("ticket_count"))
        n["artist_name"] = rec.get("event_name") or rec.get("name")
        n["venue_name"] = rec.get("venue_name") or rec.get("venue")
        n["event_date"] = str(rec.get("event_date") or rec.get("date") or "")[:10]

    n["currency"] = rec.get("currency") or rec.get("price_currency")
    # Unknown / unmapped sources must still be safe: preserve the record id.
    n.setdefault("source_record_id", str(rec.get("id") or rec.get("event_id") or ""))
    return n


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return round((s[mid - 1] + s[mid]) / 2, 2)


# ── Event resolution ───────────────────────────────────────────────────

def resolve_to_universe(
    normalized: dict[str, Any],
    universe: list[dict[str, Any]],
) -> tuple[str, str, float | None]:
    """Resolve a marketplace observation against the frozen universe.

    Match hierarchy:
      1. provider cross-ID (universe provider_event_id == marketplace id) — rare
      2. artist + venue + date (normalized names + date)
      3. deterministic normalized name match (artist + date only)
      4. fuzzy => AMBIGUOUS, never automatic unique

    Returns (status, event_key, confidence).
    status: MATCHED | AMBIGUOUS | UNRESOLVED
    """
    obs_artist = _norm(normalized.get("artist_name"))
    obs_venue = _norm(normalized.get("venue_name"))
    obs_date = str(normalized.get("event_date") or "")[:10]

    if not obs_artist:
        return ("UNRESOLVED", None, None)

    candidates: list[tuple[float, dict]] = []
    for ev in universe:
        uni_artist = _norm(ev.get("artist_name"))
        uni_venue = _norm(ev.get("venue_name"))
        uni_date = str(ev.get("event_date") or "")[:10]

        if not uni_artist:
            continue
        score = 0.0
        # Artist must match (exact normalized).
        if obs_artist != uni_artist:
            continue
        score += 0.5
        # Venue: a present-but-mismatched venue must NOT match.
        if obs_venue and uni_venue:
            if obs_venue == uni_venue:
                score += 0.3
            elif obs_venue:
                continue  # venue present and differs => not this event
        # Date match (may be absent or slightly shifted).
        if obs_date and uni_date:
            if obs_date == uni_date:
                score += 0.2
            elif _days_apart(obs_date, uni_date) <= 1:
                score += 0.1
        candidates.append((score, ev))

    if not candidates:
        return ("UNRESOLVED", None, None)

    candidates.sort(key=lambda c: c[0], reverse=True)
    best_score, best_ev = candidates[0]

    # Require artist + (venue or date) for MATCHED.
    if best_score >= 0.7:
        return ("MATCHED", best_ev["event_key"], round(best_score, 2))
    if best_score >= 0.5:
        return ("AMBIGUOUS", best_ev["event_key"], round(best_score, 2))
    return ("UNRESOLVED", None, None)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = s.replace("&", "and")
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    s = " ".join(s.split())
    # Drop venue noise words for loose venue comparison.
    return s


def _days_apart(a: str, b: str) -> int:
    try:
        from datetime import date as _date
        da = _date.fromisoformat(a[:10])
        db = _date.fromisoformat(b[:10])
        return abs((da - db).days)
    except (ValueError, TypeError):
        return 999


# ── Snapshot persistence ───────────────────────────────────────────────

def persist_snapshot(conn, snapshot: dict[str, Any]) -> str:
    """Insert one normalized market snapshot. Returns snapshot_id."""
    import hashlib
    material = json.dumps(
        {
            "event_key": snapshot.get("event_key"),
            "source_platform": snapshot.get("source_platform"),
            "source_record_id": snapshot.get("source_record_id"),
            "observed_at": snapshot.get("observed_at"),
            "resale_min_price": snapshot.get("resale_min_price"),
            "resale_median_price": snapshot.get("resale_median_price"),
            "listing_count": snapshot.get("listing_count"),
        },
        default=str, sort_keys=True,
    )
    sid = "tm::" + hashlib.sha256(material.encode()).hexdigest()[:24]

    conn.execute(
        """
        INSERT OR IGNORE INTO acquisition.ticket_market_snapshots (
            snapshot_id, watch_universe_version, event_key, provider_event_id,
            source_platform, actor_or_endpoint, source_record_id,
            wave_label, observed_at, retrieved_at, knowledge_time,
            currency, resale_min_price, resale_median_price, resale_avg_price,
            resale_max_price, listing_count, ticket_count, sold_out_flag,
            availability_flag, face_value, all_in_price, section, row_label,
            quantity, identity_match_status, identity_match_method,
            identity_match_confidence, source_url, raw_payload_hash,
            rights_status, commercial_use_status, parser_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            sid,
            snapshot.get("watch_universe_version"),
            snapshot.get("event_key"),
            snapshot.get("provider_event_id"),
            snapshot.get("source_platform"),
            snapshot.get("actor_or_endpoint"),
            snapshot.get("source_record_id"),
            snapshot.get("wave_label"),
            snapshot.get("observed_at"),
            snapshot.get("retrieved_at"),
            snapshot.get("knowledge_time") or snapshot.get("observed_at"),
            snapshot.get("currency"),
            snapshot.get("resale_min_price"),
            snapshot.get("resale_median_price"),
            snapshot.get("resale_avg_price"),
            snapshot.get("resale_max_price"),
            snapshot.get("listing_count"),
            snapshot.get("ticket_count"),
            snapshot.get("sold_out_flag"),
            snapshot.get("availability_flag"),
            snapshot.get("face_value"),
            snapshot.get("all_in_price"),
            snapshot.get("section"),
            snapshot.get("row_label"),
            snapshot.get("quantity"),
            snapshot.get("identity_match_status"),
            snapshot.get("identity_match_method"),
            snapshot.get("identity_match_confidence"),
            snapshot.get("source_url"),
            snapshot.get("raw_payload_hash"),
            snapshot.get("rights_status", "TERMS_REVIEW_REQUIRED"),
            snapshot.get("commercial_use_status", "PROTOTYPE_ONLY"),
            snapshot.get("parser_version", "ticket_market_rail_v1"),
        ],
    )
    return sid


def record_source_health(conn, entry: dict[str, Any]) -> None:
    """Append one run to the source health ledger."""
    import hashlib
    rid = "hl::" + hashlib.sha256(
        f"{entry.get('source_platform')}|{entry.get('started_at')}".encode()
    ).hexdigest()[:20]
    conn.execute(
        """
        INSERT OR REPLACE INTO acquisition.source_health_ledger (
            run_id, source_platform, actor_or_endpoint, wave_label,
            started_at, finished_at, status, error_category, error_detail,
            events_requested, events_resolved, observations_ingested,
            latency_ms, cost_usd, schema_hash, schema_version,
            records_returned, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            rid,
            entry.get("source_platform"),
            entry.get("actor_or_endpoint"),
            entry.get("wave_label"),
            entry.get("started_at"),
            entry.get("finished_at"),
            entry.get("status"),
            entry.get("error_category"),
            entry.get("error_detail"),
            entry.get("events_requested"),
            entry.get("events_resolved"),
            entry.get("observations_ingested"),
            entry.get("latency_ms"),
            entry.get("cost_usd"),
            entry.get("schema_hash"),
            entry.get("schema_version"),
            entry.get("records_returned"),
            entry.get("notes"),
        ],
    )


# ── Wave execution ─────────────────────────────────────────────────────

def run_market_wave(
    conn,
    universe: list[dict[str, Any]],
    *,
    sources: list[str] | None = None,
    max_items: int = 10,
    max_events: int | None = None,
) -> dict[str, Any]:
    """Run one REAL observation wave over the frozen universe.

    For each (source, event) pair: query the marketplace, resolve records
    against the universe, persist normalized snapshots + raw observations,
    then detect changes.

    Returns a wave report. This performs genuine network calls.
    """
    load_local_env()
    if sources is None:
        sources = list(MARKET_SOURCES.keys())

    wave_label = _now().replace(":", "").replace("-", "")[:15]
    events_to_observe = universe[:max_events] if max_events else universe
    report: dict[str, Any] = {
        "wave_label": wave_label,
        "events_requested": len(events_to_observe),
        "started_at": _now(),
        "sources": {},
        "totals": {"records": 0, "snapshots": 0, "matched": 0, "ambiguous": 0,
                   "unresolved": 0, "cost_usd": 0.0, "latency_ms": 0, "changes": 0},
    }

    for source_key in sources:
        src = MARKET_SOURCES.get(source_key)
        if not src:
            continue
        started = time.monotonic()
        started_at = _now()
        health: dict[str, Any] = {
            "source_platform": src["platform"],
            "actor_or_endpoint": src["actor"],
            "wave_label": wave_label,
            "started_at": started_at,
            "events_requested": len(events_to_observe),
            "events_resolved": 0,
            "observations_ingested": 0,
            "records_returned": 0,
            "cost_usd": 0.0,
        }
        source_report: dict[str, Any] = {
            "source": source_key,
            "platform": src["platform"],
            "actor": src["actor"],
            "events_requested": len(events_to_observe),
            "records_returned": 0,
            "snapshots": 0,
            "matched": 0, "ambiguous": 0, "unresolved": 0,
            "cost_usd": 0.0, "latency_ms": 0,
            "errors": [],
        }

        for ev in events_to_observe:
            input_body = build_source_input(
                source_key,
                artist_name=ev.get("artist_name", ""),
                city=ev.get("city", ""),
                state=ev.get("state", ""),
                event_date=str(ev.get("event_date", ""))[:10],
                max_items=max_items,
            )
            # URL-only sources without a usable query are skipped per event.
            if source_key == "gametime" and not input_body.get("startUrls"):
                continue

            result = run_actor(
                src["actor"], input_body,
                max_polls=15, poll_interval=2.0, timeout=90,
            )
            if result.get("status") not in ("COMPLETED", "SUCCEEDED"):
                source_report["errors"].append(
                    {"event": ev.get("event_key"), "error": result.get("final_state") or result.get("error")}
                )
                health.setdefault("error_categories", []).append(
                    result.get("final_state") or result.get("error") or "RUN_FAILED"
                )
                continue

            records = result.get("records", [])
            source_report["records_returned"] += len(records)
            report["totals"]["records"] += len(records)
            source_report["cost_usd"] += float(result.get("cost_usd") or 0)
            report["totals"]["cost_usd"] += float(result.get("cost_usd") or 0)
            source_report["latency_ms"] += int(result.get("latency_ms") or 0)
            health["cost_usd"] = source_report["cost_usd"]
            health["latency_ms"] = source_report["latency_ms"]

            for rec in records:
                norm = normalize_market_record(rec, source_key)
                norm["event_date"] = norm.get("event_date") or str(ev.get("event_date", ""))[:10]
                # Resolve against the universe.
                status, event_key, confidence = resolve_to_universe(norm, universe)
                # MATCHED -> canonical event key drives the series.
                # AMBIGUOUS -> keep best candidate key for provenance but flagged.
                # UNRESOLVED -> event_key stays None (preserved, cannot drive series).
                target_event = event_key

                snapshot = {
                    "watch_universe_version": ev.get("watch_universe_version"),
                    "event_key": target_event,
                    "provider_event_id": ev.get("provider_event_id"),
                    "source_platform": src["platform"],
                    "actor_or_endpoint": src["actor"],
                    "source_record_id": norm.get("source_record_id"),
                    "wave_label": wave_label,
                    "observed_at": _now(),
                    "retrieved_at": _now(),
                    "knowledge_time": _now(),
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
                    "all_in_price": norm.get("all_in_price"),
                    "section": norm.get("section"),
                    "row_label": norm.get("row_label"),
                    "quantity": norm.get("quantity"),
                    "identity_match_status": status,
                    "identity_match_method": "ARTIST_VENUE_DATE" if status == "MATCHED" else (
                        "FUZZY_CANDIDATE" if status == "AMBIGUOUS" else None
                    ),
                    "identity_match_confidence": confidence,
                    "source_url": norm.get("source_url"),
                    "raw_payload_hash": None,
                    "rights_status": "TERMS_REVIEW_REQUIRED",
                    "commercial_use_status": "PROTOTYPE_ONLY",
                }
                sid = persist_snapshot(conn, snapshot)
                source_report["snapshots"] += 1
                report["totals"]["snapshots"] += 1

                if status == "MATCHED":
                    source_report["matched"] += 1
                    report["totals"]["matched"] += 1
                elif status == "AMBIGUOUS":
                    source_report["ambiguous"] += 1
                    report["totals"]["ambiguous"] += 1
                else:
                    source_report["unresolved"] += 1
                    report["totals"]["unresolved"] += 1

                # Also persist a raw observation for the append-only contract.
                try:
                    obs = ObservationRecord(
                        source_platform=src["platform"],
                        acquisition_provider="apify",
                        actor_or_endpoint=src["actor"],
                        source_record_id=str(norm.get("source_record_id") or sid),
                        observation_type="TICKET_PRICE" if norm.get("resale_min_price") is not None else "TICKET_AVAILABILITY",
                        observation_category=src["category"],
                        raw_payload=rec,
                        event_key=target_event,
                        market_key=ev.get("market_key"),
                        observed_at=_now(),
                        knowledge_time=_now(),
                        normalized_fields=norm,
                        rights_status="TERMS_REVIEW_REQUIRED",
                        commercial_use_status="PROTOTYPE_ONLY",
                    )
                    ingest_observation(conn, obs)
                except Exception:
                    pass  # raw observation is best-effort; snapshot is authoritative

        # Detect changes for this platform.
        try:
            changes = detect_changes(conn, src["platform"])
            source_report["changes_detected"] = len(changes)
            report["totals"]["changes"] += len(changes)
        except Exception:
            source_report["changes_detected"] = 0

        health["finished_at"] = _now()
        health["status"] = "SUCCESS" if source_report["records_returned"] > 0 else (
            "PARTIAL" if source_report["errors"] else "NO_RECORDS"
        )
        health["events_resolved"] = source_report["matched"]
        health["observations_ingested"] = source_report["snapshots"]
        health["records_returned"] = source_report["records_returned"]
        health["schema_version"] = "live"
        record_source_health(conn, health)

        source_report["latency_ms"] = int((time.monotonic() - started) * 1000)
        report["totals"]["latency_ms"] += source_report["latency_ms"]
        report["sources"][source_key] = source_report

    report["finished_at"] = _now()
    return report


def load_universe(path: Path | str) -> list[dict[str, Any]]:
    """Load the frozen watch universe JSON into event dicts."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    events = data.get("events", [])
    for ev in events:
        ev.setdefault("watch_universe_version", data.get("watch_universe_version"))
    return events
