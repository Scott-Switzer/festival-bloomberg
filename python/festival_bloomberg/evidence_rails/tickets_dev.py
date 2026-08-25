"""tickets.dev adapter — DEEP rail provider.

tickets.dev is a real-time ticket scraping API that returns ONE normalized
snapshot schema across Ticketmaster, SeatGeek, StubHub, Vivid Seats, TickPick,
Gametime, Viagogo. This module implements two capabilities:

  1. CATALOG (never billed)
     GET /v1/events?query=... — cross-marketplace event mapping. One catalog
     row carries the same event's Ticketmaster / Vivid / StubHub / SeatGeek
     IDs and URLs. This is the cross-market event security master feed.

  2. CAPTURE (billed with a live key; free fixtures with the sandbox key)
     GET /v1/capture/{source}?url=... — a full normalized snapshot: event
     metadata + derived stats (get-in/median/avg/max, listing/ticket counts)
     + every listing (section/row/quantity/base price/fee/all-in).

The public sandbox key `tk_test_sandbox` never bills and returns fixtures with
the exact same schema as live captures, which lets the parser contract be
developed and tested at zero cost.

RIGHTS: marketplace page observation remains TERMS_REVIEW_REQUIRED for
commercial redistribution. The capture endpoint scrapes live marketplace
state; having the technical ability to observe is not a data license.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ..localenv import load_local_env

BASE_URL = "https://api.tickets.dev/v1"
SANDBOX_KEY = "tk_test_sandbox"
# The sandbox key is public by design (documented on tickets.dev) and cannot
# spend credits. Never print a live key.

# Marketplaces the catalog indexes today (docs, 2026-08-25): TM, Vivid,
# GoTickets, Paciolan. SeatGeek/StubHub/TickPick/Gametime/Viagogo are rolling
# out (capture already works, catalog answers source_not_indexed).
CATALOG_INDEXED = {"ticketmaster", "vividseats", "gotickets", "paciolan"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key() -> str:
    load_local_env()
    return os.environ.get("TICKETS_DEV_API_KEY") or SANDBOX_KEY


def _request(method: str, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
    """Low-level request. Returns parsed JSON or an error dict."""
    key = _key()
    url = f"{BASE_URL}{path}"
    if query:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        if qs:
            url = f"{url}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        return {"error": f"HTTP {e.code}", "detail": body}
    except Exception as e:  # noqa: BLE001 — network layer must not kill the rail
        return {"error": str(e)}


def is_sandbox() -> bool:
    """True when no live TICKETS_DEV_API_KEY is configured."""
    load_local_env()
    return not os.environ.get("TICKETS_DEV_API_KEY")


# ── 1. Catalog (never billed) ────────────────────────────────────────────

def catalog_lookup(query: str, *, source: str | None = None,
                   from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
    """Search the free event catalog.

    query may be an event/venue/performer name, a slug, or any marketplace
    event URL. Returns {status, events: [...]} where each event carries a
    `sources` array: [{marketplace, eventId, url}].
    """
    q = {"query": query}
    if source:
        q["source"] = source
    if from_date:
        q["from"] = from_date
    if to_date:
        q["to"] = to_date
    data = _request("GET", "/events", q)
    if "error" in data:
        return {"status": "ERROR", **data}
    events = data.get("events", [])
    return {"status": "OK", "total": data.get("total", len(events)), "events": events}


def catalog_mappings_for_event(
    artist: str, venue: str, city: str, event_date: str,
) -> dict[str, Any]:
    """Resolve a canonical event to cross-marketplace IDs via the catalog.

    Returns:
      {status, query, mappings: [{marketplace, eventId, url, matched}]}
    """
    query = f"{artist} {venue} {city}"
    res = catalog_lookup(query, from_date=event_date)
    if res.get("status") != "OK":
        return {"status": res.get("status", "ERROR"), "query": query, "mappings": [],
                "detail": res.get("detail") or res.get("error")}

    # Pick the best catalog event: exact artist + venue + date if possible.
    artist_l = artist.lower().strip()
    venue_l = venue.lower().strip()
    date_part = str(event_date)[:10]

    best: dict[str, Any] | None = None
    best_score = 0.0
    for ev in res.get("events", []):
        score = 0.0
        name = (ev.get("name") or "").lower()
        performers = " ".join(p.get("name", "") for p in ev.get("performers", [])).lower()
        vname = (ev.get("venue") or {}).get("name", "").lower()
        vcity = (ev.get("venue") or {}).get("city", "").lower()
        edate = str(ev.get("eventDateUtc") or ev.get("eventDateLocal") or "")[:10]
        if artist_l and (artist_l in name or artist_l in performers):
            score += 0.5
        if venue_l and venue_l in vname:
            score += 0.3
        if date_part and edate and edate == date_part:
            score += 0.2
        elif date_part and edate:
            score += 0.05
        if city and city.lower() == vcity:
            score += 0.1
        if score > best_score:
            best_score = score
            best = ev

    if best is None or best_score < 0.5:
        return {"status": "AMBIGUOUS", "query": query, "mappings": [], "best_score": best_score}

    mappings = []
    for s in best.get("sources", []):
        mappings.append({
            "marketplace": s.get("marketplace"),
            "marketplace_event_id": s.get("eventId"),
            "marketplace_event_url": s.get("url"),
            "matched": True,
        })
    return {
        "status": "MATCHED_EXACT" if best_score >= 0.8 else "HIGH_CONFIDENCE",
        "query": query,
        "catalog_event_id": best.get("id"),
        "catalog_updated_at": best.get("updatedAt"),
        "best_score": round(best_score, 2),
        "mappings": mappings,
    }


# ── 2. Capture (billed with live key, free fixtures with sandbox) ────────

def capture(url: str, *, source: str | None = None,
            include_venue_maps: bool = False) -> dict[str, Any]:
    """Run one capture of an event page URL.

    Returns {status, snapshot: {...}} where snapshot has the unified schema:
      eventId, eventName, venue{...}, performers, eventDateUtc/Local,
      currency, capturedAt, source, sourceUrl, stats{listingCount,
      ticketCount, getInPrice, medianPrice, avgPrice, maxPrice},
      listings[{listingId, inventoryType, section, row, quantity,
      ticketPrice, fee, totalPrice, sellableQuantities, ...}].
    """
    q: dict[str, Any] = {"url": url}
    if source:
        q["source"] = source
    if include_venue_maps:
        q["includeVenueMaps"] = "true"
    path = f"/capture/{source}" if source else "/capture"
    data = _request("GET", path, q)
    if "error" in data:
        return {"status": "ERROR", "url": url, **data}
    return {"status": "OK", "url": url, "sandbox": is_sandbox(), "snapshot": data}


# ── 3. Normalization into the rail contract ─────────────────────────────

def normalize_capture_snapshot(
    snapshot: dict[str, Any],
    *,
    event_key: str | None,
    wave_label: str,
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "PROTOTYPE_ONLY",
) -> dict[str, Any]:
    """Map a tickets.dev snapshot into the ticket_market_snapshots contract.

    All prices are all-in per ticket (tickets.dev derives stats on the same
    basis as totalPrice). No tickets-sold inference: listingCount/ticketCount
    are availability proxies.
    """
    stats = snapshot.get("stats") or {}
    venue = snapshot.get("venue") or {}
    performers = snapshot.get("performers") or []
    currency = snapshot.get("currency")

    # All-in price basis; fall back to getInPrice where fee breakdown absent.
    all_in_min = stats.get("getInPrice")

    return {
        "watch_universe_version": "watch_universe_v1",
        "event_key": event_key,
        "provider_event_id": None,
        "source_platform": f"{snapshot.get('source')}.com"
        if "." not in str(snapshot.get("source") or "")
        else snapshot.get("source"),
        "actor_or_endpoint": f"tickets_dev_capture:{snapshot.get('source')}",
        "source_record_id": snapshot.get("eventId"),
        "wave_label": wave_label,
        "observed_at": snapshot.get("capturedAt") or _now(),
        "retrieved_at": _now(),
        "knowledge_time": _now(),
        "currency": currency,
        "resale_min_price": all_in_min,
        "resale_median_price": stats.get("medianPrice"),
        "resale_avg_price": stats.get("avgPrice"),
        "resale_max_price": stats.get("maxPrice"),
        "listing_count": stats.get("listingCount"),
        "ticket_count": stats.get("ticketCount"),
        "sold_out_flag": (snapshot.get("note") or "").lower() in (
            "sold out", "sold out.", "no tickets available"),
        "availability_flag": int(stats.get("listingCount") or 0) > 0,
        "face_value": None,
        "all_in_price": all_in_min,
        "section": None,
        "row_label": None,
        "quantity": None,
        "identity_match_status": "MATCHED" if event_key else "UNRESOLVED",
        "identity_match_method": "TICKETS_DEV_CAPTURE_URL" if event_key else None,
        "identity_match_confidence": 1.0 if event_key else None,
        "source_url": snapshot.get("sourceUrl"),
        "raw_payload_hash": _payload_hash(snapshot),
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "parser_version": "tickets_dev_v1",
        # Extras for the DEEP rail
        "tickets_dev_snapshot": {
            "eventId": snapshot.get("eventId"),
            "eventName": snapshot.get("eventName"),
            "venueName": venue.get("name"),
            "venueCity": venue.get("city"),
            "venueState": venue.get("state"),
            "venueTimezone": venue.get("timezone"),
            "eventDateUtc": snapshot.get("eventDateUtc"),
            "eventDateLocal": snapshot.get("eventDateLocal"),
            "note": snapshot.get("note"),
            "sectionLevelUrl": snapshot.get("sectionLevelUrl"),
            "seatLevelUrl": snapshot.get("seatLevelUrl"),
            "performers": performers,
        },
    }


def listings_from_snapshot(
    snapshot: dict[str, Any],
    *,
    event_key: str,
    wave_label: str,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Extract listing-level rows from a capture snapshot (DEEP rail).

    Never interpret disappearance as a sale. Callers classify only
    LISTING_APPEARED / DISAPPEARED / PRICE_CHANGED / QUANTITY_CHANGED.
    """
    rows = []
    for l in snapshot.get("listings", []):
        quantity = _int(l.get("quantity"))
        total = _num(l.get("totalPrice"))
        ticket_price = _num(l.get("ticketPrice"))
        fee = _num(l.get("fee"))
        all_in = total / quantity if (total is not None and quantity) else None
        rows.append({
            "event_key": event_key,
            "marketplace": f"{snapshot.get('source')}.com"
            if "." not in str(snapshot.get("source") or "")
            else snapshot.get("source"),
            "provider_listing_id": str(l.get("listingId") or ""),
            "inventory_type": l.get("inventoryType"),
            "section": l.get("section"),
            "row_label": l.get("row"),
            "seats": l.get("seats"),
            "quantity": quantity,
            "ticket_price": ticket_price,
            "fee": fee,
            "all_in_price": all_in,
            "currency": snapshot.get("currency"),
            "observed_at": observed_at or snapshot.get("capturedAt") or _now(),
            "wave_label": wave_label,
        })
    return rows


def persist_listings(conn, rows: list[dict[str, Any]]) -> int:
    """Insert/update listing lifecycle rows. Returns count written.

    Stable listing IDs (when present) drive first_seen/last_seen + price
    history. Unstable/empty IDs get a synthetic deterministic id keyed on
    (event, marketplace, section, row, quantity, price) so repeated captures
    of the same listing group still track lifecycle.
    """
    written = 0
    for r in rows:
        ekey = r["event_key"]
        mp = r["marketplace"]
        pid = r.get("provider_listing_id") or ""
        if pid:
            lid = f"lst::{_h(f'{ekey}|{mp}|{pid}')}"
        else:
            _mat = "|".join(str(r.get(k)) for k in ("section", "row_label", "quantity", "all_in_price"))
            lid = f"lst::{_h(f'{ekey}|{mp}|{_mat}')}"
        observed_at = r["observed_at"]

        existing = conn.execute(
            "SELECT * FROM acquisition.marketplace_listings WHERE listing_id = ?",
            [lid],
        ).fetchone()
        cols = [c[0] for c in conn.description] if conn.description else []

        if existing:
            ex = dict(zip(cols, existing))
            price_changed = (
                _num(ex.get("all_in_price")) is not None
                and r.get("all_in_price") is not None
                and abs(_num(ex.get("all_in_price")) - r["all_in_price"]) > 0.005
            )
            qty_changed = (
                ex.get("quantity") is not None
                and r.get("quantity") is not None
                and ex["quantity"] != r["quantity"]
            )
            history = json.loads(ex.get("price_history_json") or "[]")
            history.append({
                "price": r.get("all_in_price"),
                "ticket_price": r.get("ticket_price"),
                "fee": r.get("fee"),
                "observed_at": observed_at,
            })
            status = "PRICE_CHANGED" if price_changed else (
                "QUANTITY_CHANGED" if qty_changed else "LISTING_APPEARED"
            )
            conn.execute(
                """UPDATE acquisition.marketplace_listings SET
                   last_seen_at = ?, last_observed_at = ?, status = ?,
                   quantity = ?, ticket_price = ?, fee = ?, all_in_price = ?,
                   price_history_json = ?
                   WHERE listing_id = ?""",
                [observed_at, observed_at, status, r.get("quantity"),
                 r.get("ticket_price"), r.get("fee"), r.get("all_in_price"),
                 json.dumps(history[-50:]), lid],
            )
        else:
            conn.execute(
                """INSERT INTO acquisition.marketplace_listings (
                    listing_id, event_key, marketplace, provider_listing_id,
                    inventory_type, section, row_label, seats, quantity,
                    ticket_price, fee, all_in_price, currency,
                    first_seen_at, last_seen_at, last_observed_at, status,
                    price_history_json, rights_status, commercial_use_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [lid, ekey, mp, pid, r.get("inventory_type"), r.get("section"),
                 r.get("row_label"), r.get("seats"), r.get("quantity"),
                 r.get("ticket_price"), r.get("fee"), r.get("all_in_price"),
                 r.get("currency"), observed_at, observed_at, observed_at,
                 "LISTING_APPEARED",
                 json.dumps([{
                     "price": r.get("all_in_price"),
                     "ticket_price": r.get("ticket_price"),
                     "fee": r.get("fee"),
                     "observed_at": observed_at,
                 }]),
                 "TERMS_REVIEW_REQUIRED", "PROTOTYPE_ONLY"],
            )
        written += 1
    return written


def mark_disappeared_listings(conn, event_key: str, marketplace: str,
                              seen_ids: set[str], observed_at: str) -> int:
    """Mark listings no longer present as LISTING_DISAPPEARED.

    NOT a sale — a listing may be withdrawn, repriced/relisted, or transferred.
    """
    rows = conn.execute(
        """SELECT listing_id, provider_listing_id FROM acquisition.marketplace_listings
           WHERE event_key = ? AND marketplace = ? AND status != 'LISTING_DISAPPEARED'""",
        [event_key, marketplace],
    ).fetchall()
    n = 0
    for lid, pid in rows:
        if pid and pid in seen_ids:
            continue
        if not pid:
            continue  # synthetic ids can't be reliably matched; leave alone
        conn.execute(
            """UPDATE acquisition.marketplace_listings
               SET status = 'LISTING_DISAPPEARED', last_seen_at = ?
               WHERE listing_id = ?""",
            [observed_at, lid],
        )
        n += 1
    return n


def _h(material: str, n: int = 24) -> str:
    return hashlib.sha256(material.encode()).hexdigest()[:n]


def _payload_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, default=str, sort_keys=True).encode()
    ).hexdigest()[:24]


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
