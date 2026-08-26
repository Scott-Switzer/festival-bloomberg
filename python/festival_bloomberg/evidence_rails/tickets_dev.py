"""tickets.dev adapter — DEEP rail provider.

tickets.dev is a real-time ticket scraping API that returns ONE normalized
snapshot schema across Ticketmaster, SeatGeek, StubHub, Vivid Seats, TickPick,
Gametime, Viagogo. This module implements two capabilities:

  1. CATALOG (never billed, documented endpoint)
     GET /v1/events?query=... — cross-marketplace event mapping. One catalog
     row carries the same event's Ticketmaster / Vivid / StubHub / SeatGeek
     IDs and URLs. This feeds the cross-market event security master.

  2. CAPTURE (billed with a live key; free fixtures with the sandbox key)
     GET /v1/capture/{source}?url=... — a full normalized snapshot: event
     metadata + derived stats (get-in/median/avg/max, listing/ticket counts)
     + every listing (section/row/quantity/base price/fee/all-in).

PRICE SEMANTICS (tickets.dev docs, verified 2026-08-25):
    ticketPrice = face price per ticket, BEFORE fees
    fee         = per-ticket fees
    totalPrice  = ALL-IN price PER TICKET (excl. sales tax), normalized
                  across marketplaces
    stats       = per-ticket all-in on the same basis as totalPrice,
                  computed per LISTING (a 10-seat listing counts once)

    Therefore: all_in_price = totalPrice. NEVER divide by quantity.

SANDBOX SEMANTICS:
    The public sandbox key `tk_test_sandbox` never bills and returns fixtures
    with the exact same schema as live captures. The response BODY does not
    advertise sandbox (by design); the `Tickets-Sandbox: true` header does.
    Sandbox fixtures must NEVER enter the production warehouse — the
    collector gates DEEP on a live key (see collect_ticket_market.py).

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
            data = json.loads(resp.read().decode())
            data["_sandbox"] = resp.headers.get("Tickets-Sandbox", "").lower() == "true"
            return data
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        return {"error": f"HTTP {e.code}", "detail": body}
    except Exception as e:  # noqa: BLE001 — network layer must not kill the rail
        return {"error": str(e)}


def is_sandbox() -> bool:
    """True when no live TICKETS_DEV_API_KEY is configured.

    The sandbox key can never spend credits, so the configured-key check is
    the reliable gate. The `Tickets-Sandbox: true` response header is also
    captured onto parsed payloads (`_sandbox` key) for verification.
    """
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


def persist_catalog_mappings(
    conn,
    *,
    event_key: str,
    mappings: list[dict[str, Any]],
    mapping_method: str = "TICKETS_DEV_CATALOG",
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "PROTOTYPE_ONLY",
) -> int:
    """Persist catalog mappings into the canonical event_identifiers master.

    The catalog returns provider-verified event IDs, so MATCHED mappings are
    EXACT_PROVIDER_ID (not fuzzy page matches). Writes to the same
    event_identifiers contract the URL resolver and the Buyer Workspace read.
    """
    written = 0
    for m in mappings:
        marketplace = m.get("marketplace")
        if not marketplace:
            continue
        iid = "id::" + _h(f"{event_key}|{marketplace}")
        conn.execute(
            """INSERT INTO acquisition.event_identifiers (
                identifier_id, event_key, marketplace, marketplace_event_id,
                marketplace_event_url, mapping_status, mapping_method,
                confidence, first_resolved_at, last_verified_at,
                source_evidence, rights_status, commercial_use_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (event_key, marketplace) DO UPDATE SET
                marketplace_event_id = excluded.marketplace_event_id,
                marketplace_event_url = excluded.marketplace_event_url,
                mapping_status = excluded.mapping_status,
                mapping_method = excluded.mapping_method,
                confidence = excluded.confidence,
                last_verified_at = excluded.last_verified_at,
                source_evidence = excluded.source_evidence,
                rights_status = excluded.rights_status,
                commercial_use_status = excluded.commercial_use_status""",
            [
                iid, event_key, marketplace, m.get("marketplace_event_id"),
                m.get("marketplace_event_url"),
                "EXACT_PROVIDER_ID" if m.get("matched") else "HIGH_CONFIDENCE",
                mapping_method, 1.0 if m.get("matched") else 0.6,
                _now(), _now(), m.get("source_evidence"),
                rights_status, commercial_use_status,
            ],
        )
        written += 1
    return written


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

    sandbox=True on the result when the Tickets-Sandbox header (or the
    absence of a live key) says the payload is a fixture.
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
    sandbox = is_sandbox() or bool(data.pop("_sandbox", False))
    return {"status": "OK", "url": url, "sandbox": sandbox, "snapshot": data}


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

    PRICE SEMANTICS: tickets.dev totalPrice is the ALL-IN price PER TICKET,
    so all_in_price = totalPrice (never divided by quantity). ticketPrice is
    the per-ticket face price and fee is the per-ticket fee.

    Never interpret disappearance as a sale. Callers classify only
    LISTING_APPEARED / DISAPPEARED / PRICE_CHANGED / QUANTITY_CHANGED.
    """
    rows = []
    for l in snapshot.get("listings", []):
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
            "quantity": _int(l.get("quantity")),
            "ticket_price": _num(l.get("ticketPrice")),
            "fee": _num(l.get("fee")),
            "all_in_price": _num(l.get("totalPrice")),  # all-in PER TICKET
            "currency": snapshot.get("currency"),
            "observed_at": observed_at or snapshot.get("capturedAt") or _now(),
            "wave_label": wave_label,
        })
    return rows


def _listing_key(event_key: str, marketplace: str, row: dict[str, Any]) -> str:
    """Stable listing key: provider id when present, else deterministic
    synthetic key over (section, row, quantity, all_in_price)."""
    pid = row.get("provider_listing_id") or ""
    if pid:
        return f"lst::{_h(f'{event_key}|{marketplace}|{pid}')}"
    _mat = "|".join(str(row.get(k)) for k in ("section", "row_label", "quantity", "all_in_price"))
    return f"lst::{_h(f'{event_key}|{marketplace}|{_mat}')}"


def persist_listings(
    conn,
    rows: list[dict[str, Any]],
    *,
    source_snapshot_id: str | None = None,
    raw_payload_hash: str | None = None,
) -> int:
    """Append listing observations (immutable) + update current-state cache.

    - marketplace_listing_observations: ONE append-only row per observed
      listing per capture. Never updated or deleted. This is the historical
      truth — there is no truncation.
    - marketplace_listings: current-state cache with lifecycle transitions:
      LISTING_APPEARED / LISTING_PRICE_CHANGED / LISTING_QUANTITY_CHANGED /
      LISTING_REAPPEARED. A repeated UNCHANGED listing keeps its previous
      status (never resets to LISTING_APPEARED).
    - last_seen_at always stays the last observation CONTAINING the listing.
    Returns count of listings written.
    """
    written = 0
    now = _now()
    for r in rows:
        ekey = r["event_key"]
        mp = r["marketplace"]
        lid = _listing_key(ekey, mp, r)
        observed_at = r["observed_at"]

        # 1) Append-only observation row (immutable truth).
        conn.execute(
            """INSERT INTO acquisition.marketplace_listing_observations (
                listing_observation_id, event_key, marketplace,
                provider_listing_id, listing_key, observed_at,
                inventory_type, section, row_label, seats, quantity,
                ticket_price, fee, all_in_price, currency, status,
                source_snapshot_id, raw_payload_hash,
                rights_status, commercial_use_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OBSERVED', ?, ?, ?, ?)""",
            [
                f"lobs::{_h(f'{lid}|{observed_at}')}",
                ekey, mp, r.get("provider_listing_id") or "", lid,
                observed_at, r.get("inventory_type"), r.get("section"),
                r.get("row_label"), r.get("seats"), r.get("quantity"),
                r.get("ticket_price"), r.get("fee"), r.get("all_in_price"),
                r.get("currency"), source_snapshot_id, raw_payload_hash,
                "TERMS_REVIEW_REQUIRED", "PROTOTYPE_ONLY",
            ],
        )

        # 2) Current-state cache with correct lifecycle semantics.
        existing = conn.execute(
            "SELECT status, first_missing_at, disappeared_at FROM acquisition.marketplace_listings WHERE listing_id = ?",
            [lid],
        ).fetchone()

        if existing:
            prev_status = existing[0]
            price_changed = _price_changed(conn, lid, r.get("all_in_price"))
            qty_changed = _qty_changed(conn, lid, r.get("quantity"))
            if prev_status == "LISTING_DISAPPEARED":
                status = "LISTING_REAPPEARED"
            elif price_changed:
                status = "LISTING_PRICE_CHANGED"
            elif qty_changed:
                status = "LISTING_QUANTITY_CHANGED"
            else:
                status = prev_status  # unchanged: keep previous status
            conn.execute(
                """UPDATE acquisition.marketplace_listings SET
                   last_seen_at = ?, last_observed_at = ?, status = ?,
                   first_missing_at = NULL, disappeared_at = NULL,
                   quantity = ?, ticket_price = ?, fee = ?, all_in_price = ?
                   WHERE listing_id = ?""",
                [observed_at, observed_at, status,
                 r.get("quantity"), r.get("ticket_price"), r.get("fee"),
                 r.get("all_in_price"), lid],
            )
        else:
            conn.execute(
                """INSERT INTO acquisition.marketplace_listings (
                    listing_id, event_key, marketplace, provider_listing_id,
                    inventory_type, section, row_label, seats, quantity,
                    ticket_price, fee, all_in_price, currency,
                    first_seen_at, last_seen_at, last_observed_at, status,
                    rights_status, commercial_use_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [lid, ekey, mp, r.get("provider_listing_id") or "",
                 r.get("inventory_type"), r.get("section"), r.get("row_label"),
                 r.get("seats"), r.get("quantity"), r.get("ticket_price"),
                 r.get("fee"), r.get("all_in_price"), r.get("currency"),
                 observed_at, observed_at, observed_at, "LISTING_APPEARED",
                 "TERMS_REVIEW_REQUIRED", "PROTOTYPE_ONLY"],
            )
        written += 1
    return written


def _price_changed(conn, listing_id: str, new_price: float | None) -> bool:
    row = conn.execute(
        "SELECT all_in_price FROM acquisition.marketplace_listings WHERE listing_id = ?",
        [listing_id],
    ).fetchone()
    if row is None:
        return False
    old = row[0]
    if old is None or new_price is None:
        return False
    return abs(float(old) - float(new_price)) > 0.005


def _qty_changed(conn, listing_id: str, new_qty: int | None) -> bool:
    row = conn.execute(
        "SELECT quantity FROM acquisition.marketplace_listings WHERE listing_id = ?",
        [listing_id],
    ).fetchone()
    if row is None:
        return False
    old = row[0]
    if old is None or new_qty is None:
        return False
    return int(old) != int(new_qty)


def mark_disappeared_listings(conn, event_key: str, marketplace: str,
                              seen_ids: set[str], observed_at: str) -> int:
    """Mark listings no longer present as LISTING_DISAPPEARED.

    Semantics (review-corrected):
      - last_seen_at is UNTOUCHED — it stays the last observation that
        CONTAINED the listing.
      - first_missing_at = the first observation where the listing was absent
        (set once, never overwritten).
      - disappeared_at = the latest observation where it is still absent.
      - A DISAPPEARED observation row is appended to the immutable
        marketplace_listing_observations log (status='DISAPPEARED', null
        prices) so the lifecycle transition is preserved.

    NOT a sale — a listing may be withdrawn, repriced/relisted, or
    transferred.
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
               SET status = 'LISTING_DISAPPEARED',
                   first_missing_at = COALESCE(first_missing_at, ?),
                   disappeared_at = ?
               WHERE listing_id = ?""",
            [observed_at, observed_at, lid],
        )
        # Immutable transition log entry (prices null — the listing was absent).
        conn.execute(
            """INSERT INTO acquisition.marketplace_listing_observations (
                listing_observation_id, event_key, marketplace,
                provider_listing_id, listing_key, observed_at,
                inventory_type, section, row_label, seats, quantity,
                ticket_price, fee, all_in_price, currency, status,
                rights_status, commercial_use_status
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL,
                      NULL, NULL, NULL, NULL, 'DISAPPEARED', ?, ?)""",
            [
                f"lobs::{_h(f'{lid}|{observed_at}|gone')}",
                event_key, marketplace, pid, lid, observed_at,
                "TERMS_REVIEW_REQUIRED", "PROTOTYPE_ONLY",
            ],
        )
        n += 1
    return n


# ── Raw evidence store (content-addressed, hash-deduped) ────────────────

def persist_raw_evidence(
    conn,
    *,
    event_key: str,
    marketplace: str,
    payload: Any,
    payload_type: str = "SNAPSHOT_JSON",
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "PROTOTYPE_ONLY",
) -> dict[str, Any]:
    """Upsert a canonicalized raw payload into raw_evidence_store by hash.

    Identical payloads reuse ONE raw row (ref_count += 1, last_seen_at
    bumped). New observation/snapshot rows are still created separately —
    this store only dedupes the raw evidence itself.

    Returns {payload_hash, is_new, ref_count}.
    """
    canonical = json.dumps(payload, default=str, sort_keys=True).encode("utf-8")
    h = hashlib.sha256(canonical).hexdigest()[:24]
    now = _now()
    existing = conn.execute(
        "SELECT ref_count FROM acquisition.raw_evidence_store WHERE payload_hash = ?",
        [h],
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE acquisition.raw_evidence_store SET ref_count = ref_count + 1, last_seen_at = ? WHERE payload_hash = ?",
            [now, h],
        )
        return {"payload_hash": h, "is_new": False, "ref_count": int(existing[0]) + 1}
    conn.execute(
        """INSERT INTO acquisition.raw_evidence_store (
            payload_hash, marketplace, event_key, payload_type, payload,
            byte_size, first_seen_at, last_seen_at, ref_count,
            rights_status, commercial_use_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        [h, marketplace, event_key, payload_type, canonical,
         len(canonical), now, now, rights_status, commercial_use_status],
    )
    return {"payload_hash": h, "is_new": True, "ref_count": 1}


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
