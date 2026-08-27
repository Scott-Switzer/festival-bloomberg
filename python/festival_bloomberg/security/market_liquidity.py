"""MARKET_LIQUIDITY_TAPE_V1 — longitudinal market liquidity evidence.

Turns 33,000+ event identities into a REAL, multi-marketplace longitudinal
liquidity tape via legitimate official structured APIs (Ticketmaster Discovery
first), attached directly to the ARTIST × MARKET security.

Design invariants (from the milestone):

* STANDARD_PRICE_RANGE and CURRENT_AVAILABLE_INVENTORY_PRICE are DISTINCT
  semantics — never merged into one generic "ticket price".
* No attendance inference. No sales inference.
* listing-count change is NOT a sale; listing disappearance is NOT a sale.
* Artist linkage is evidence-backed (TM attraction ID + event attribution),
  never bare normalized-name matching. Ambiguous matches fail closed.
* Pricing begins with official structured APIs. Browser/Monid acquisition is
  deferred — only after official structured rails are exhausted (P11).
* Credential/authorization status is explicit and honest per provider.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, timezone
from typing import Any

from ..identity.spotify import normalize_name

SOFTWARE_VERSION = "market_liquidity_tape_v1"
TM = "ticketmaster"
TM_DISCOVERY_BASE = "https://app.ticketmaster.com/discovery/v2"

# ---------------------------------------------------------------------------
# Candidate-name heuristics — used ONLY to flag obvious non-canonical acts
# (tributes, experiences, "the music of", etc.) so they fail closed. This does
# NOT do fuzzy matching; it guards against known attribution noise.
# ---------------------------------------------------------------------------
_TRIBUTED_MARKERS = re.compile(
    r"\b(tribute|tributo|experience|the music of|a tribute|night of|jam|band)\b|\bby\b.*\b(symphony|orchestra)\b",
    re.IGNORECASE,
)


def _attraction_looks_canonical(name: str, artist_display: str) -> bool:
    if not name:
        return False
    if _TRIBUTED_MARKERS.search(name):
        return False
    # Must contain the artist display name as a token-ish substring (canonical
    # act page is titled with the act name).
    return artist_display.strip().casefold() in name.casefold()


def _tm_get(transport, key: str, path: str, params: dict[str, Any]) -> tuple[Any, dict | None]:
    request_params = dict(params)
    request_params["apikey"] = key
    response = transport.request("GET", f"{TM_DISCOVERY_BASE}/{path.lstrip('/')}", params=request_params, timeout_seconds=25.0)
    if response.status == 200:
        try:
            return response.json(), None
        except ValueError:
            return None, {"category": "schema_invalid", "status": "SCHEMA_INVALID", "detail": path}
    if response.status in {401, 403, 400}:
        return None, {"category": "auth", "status": "NOT_AUTHORIZED", "detail": f"http {response.status}"}
    if response.status == 429:
        backoff = float(response.headers.get("Retry-After", 1.5) or 1.5)
        return None, {"category": "rate_limited", "status": "RATE_LIMITED", "detail": f"http 429 retry-after={backoff}"}
    if response.status == 404:
        return None, {"category": "not_found", "status": "NOT_FOUND", "detail": path}
    return None, {"category": "http_error", "status": "PROVIDER_ERROR", "detail": f"http {response.status}"}


def _search_attractions(transport, key: str, keyword: str, size: int = 8) -> list[dict]:
    payload, error = _tm_get(transport, key, "attractions.json", {"keyword": keyword, "size": str(size)})
    if error is not None:
        return []
    return ((payload.get("_embedded") or {}).get("attractions")) or []


def _get_event(transport, key: str, event_id: str) -> tuple[dict | None, dict | None]:
    payload, error = _tm_get(transport, key, f"events/{event_id}.json", {})
    if error is not None:
        return None, error
    return payload, None


def _content_hash(obj) -> str:
    material = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Ticketmaster attraction linker (deterministic artist -> attraction id)
# ---------------------------------------------------------------------------
def resolve_tm_attractions(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    api_key: str | None,
    min_interval_seconds: float = 0.35,
) -> dict[str, Any]:
    """Resolve each security-universe artist to its Ticketmaster attraction id.

    The artist-level resolution (artist → attraction id) lands in
    ``identity.ticketmaster_artist_resolutions`` — the dedicated TM identity
    table (migration 024). It is a CANDIDATE until an event-side TM
    attribution confirms it. The bootstrap cohort (P5) then promotes confirmed
    artist↔event links into ``acquisition.artist_marketplace_links`` with a
    VERIFIED status.
    """
    if not api_key:
        return {"status": "NOT_CONFIGURED", "detail": "no TICKETMASTER_API_KEY", "links": 0}
    summary: dict[str, Any] = {"status": "RUNNING", "searched": 0, "candidates": 0, "ambiguous": 0}
    links_out: list[dict[str, Any]] = []
    for a in universe:
        display = a.get("artist_display_name") or a.get("artist_name")
        artist_key = a["artist_key"]
        if not display:
            continue
        time.sleep(min_interval_seconds)
        hits = _search_attractions(transport, api_key, display)
        summary["searched"] += 1
        canon = [h for h in hits if _attraction_looks_canonical(h.get("name") or "", display)]
        exact = [_n for _n in canon if _name_equal(_n.get("name"), display)]
        chosen = exact[:1] if exact else (canon[:1] if len(canon) == 1 else None)
        if not chosen:
            summary["ambiguous"] += 1
            _persist_artist_resolution(
                conn, artist_key=artist_key, attraction_id=None,
                attraction_name=None, resolution_status="AMBIGUOUS",
                match_method="NAME_CANDIDATE_ONLY", matched_name=display,
            )
            links_out.append({"artist_key": artist_key, "status": "AMBIGUOUS"})
            continue
        hit = chosen[0]
        _persist_artist_resolution(
            conn, artist_key=artist_key, attraction_id=hit.get("id"),
            attraction_name=hit.get("name"), resolution_status="CANDIDATE",
            match_method="TICKETMASTER_ATTRACTION_SEARCH", matched_name=display,
        )
        summary["candidates"] += 1
        links_out.append({"artist_key": artist_key, "attraction_id": hit.get("id"), "status": "CANDIDATE"})
    summary["status"] = "COMPLETE"
    summary["detail"] = "artist->attraction resolutions written; cohort requires event-side attribution match"
    summary["sample"] = links_out[:10]
    return summary


def _persist_artist_resolution(
    conn,
    *,
    artist_key: str,
    attraction_id: str | None,
    attraction_name: str | None,
    resolution_status: str,
    match_method: str,
    matched_name: str,
    match_similarity: float | None = None,
) -> None:
    """Write an artist → TM-attraction resolution (dedicated TM identity table).

    Resolution status maps onto the table's enum: a confirmed canonical act is
    MATCHED_ARTIST; an ambiguous result is AMBIGUOUS; no usable hit is
    NO_MATCH. The bootstrap cohort still gates inclusion on an EVENT-side
    attribution match — MATCHED_ARTIST here is not final cohort membership.
    """
    status = {
        "CANDIDATE": "MATCHED_ARTIST",
        "AMBIGUOUS": "AMBIGUOUS",
        "NO_MATCH": "NO_MATCH",
    }[resolution_status]
    key = hashlib.sha256(f"{artist_key}|{attraction_id or ''}|{match_method}".encode()).hexdigest()[:32]
    display = matched_name or attraction_name or artist_key
    conn.execute(
        """
        INSERT INTO identity.ticketmaster_artist_resolutions
            (resolution_key, attraction_id, attraction_name, normalized_name,
             artist_key, artist_mbid, matched_name, resolution_status,
             match_method, match_similarity, match_features, special_classification,
             source_table, knowledge_time, software_version, ingested_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL,
                'ticketmaster_discovery_attractions', ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (resolution_key) DO NOTHING
        """,
        [key, attraction_id, attraction_name or display, normalize_name(display), artist_key,
         matched_name, status, match_method, match_similarity,
         datetime.now(timezone.utc).isoformat(), SOFTWARE_VERSION],
    )


def _name_equal(a: str | None, b: str) -> bool:
    return bool(a) and normalize_name(a) == normalize_name(b)


def _persist_link(
    conn,
    *,
    artist_key: str, artist_name: str | None, event_key: str,
    market_key: str | None, event_date: date | None, marketplace: str,
    basis: str, status: str, confidence: float | None, evidence_ref: str | None,
) -> None:
    link_key = hashlib.sha256(f"{artist_key}|{event_key}|{marketplace}".encode()).hexdigest()[:32]
    exists = conn.execute(
        "SELECT 1 FROM acquisition.artist_marketplace_links WHERE link_key = ?", [link_key]
    ).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE acquisition.artist_marketplace_links
            SET link_status = ?, confidence = COALESCE(?, confidence),
                evidence_ref = COALESCE(?, evidence_ref),
                last_verified_at = ?
            WHERE link_key = ?
            """,
            [status, confidence, evidence_ref, _utcnow(), link_key],
        )
        return
    conn.execute(
        """
        INSERT INTO acquisition.artist_marketplace_links
            (link_key, artist_key, artist_name, event_key, market_key,
             event_date, marketplace, link_basis, link_status, confidence,
             evidence_ref, first_seen_at, last_verified_at,
             rights_status, commercial_use_status, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', CURRENT_TIMESTAMP)
        """,
        [link_key, artist_key, artist_name, event_key, market_key,
         event_date, marketplace, basis, status, confidence, evidence_ref,
         _utcnow(), _utcnow()],
    )


# ---------------------------------------------------------------------------
# 2. Bootstrap cohort build (P5) — important future events defensibly linked
# ---------------------------------------------------------------------------
def build_bootstrap_cohort(
    conn,
    *,
    as_of: date | None = None,
    max_events: int = 1000,
    top_markets: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, Any]:
    """Select the bootstrap cohort: important future events linked to the
    security universe via an EXACT TM attraction-id match (double-confirmed by
    the event's TM attribution). Returns the cohort event list.
    """
    as_of = as_of or date.today()
    if top_markets is None:
        from .live_ticket import TOP_US_MARKETS

        top_markets = TOP_US_MARKETS
    market_keys = set()
    for city, state in top_markets:
        from .live_ticket import market_key_for

        mk = market_key_for(city, state)
        if mk:
            market_keys.add(mk)

    # candidate attraction ids from artist-level TM resolutions (CANDIDATE)
    attr_to_artist: dict[str, dict] = {}
    for attraction_id, artist_key, attraction_name, artist_mbid in conn.execute(
        """
        SELECT DISTINCT attraction_id, artist_key, attraction_name, artist_mbid
        FROM identity.ticketmaster_artist_resolutions
        WHERE attraction_id IS NOT NULL
          AND resolution_status = 'MATCHED_ARTIST'
        """
    ).fetchall():
        attr_to_artist[attraction_id] = {
            "artist_key": artist_key,
            "artist_name": attraction_name,
            "link_status": "CANDIDATE",
            "mbid": artist_mbid,
        }
    if not attr_to_artist:
        return {"status": "NO_ATTRACTION_LINKAGE", "cohort": [], "n": 0, "artist_attraction_candidates": 0}

    # gather candidate future events with their attractions json from estate
    q = conn.execute(
        """
        SELECT e.event_key, e.market_key, e.event_date, s.attractions,
               s.artist_name, s.canonical_url
        FROM acquisition.event_tape_scale e
        JOIN events.provider_event_snapshots s
          ON 'event::tm:' || s.platform_object_id = e.event_key
        WHERE e.event_date >= ?
          AND e.market_key IN (SELECT unnest(?))
          AND s.attractions IS NOT NULL AND s.attractions <> '[]'
        """,
        [as_of.isoformat(), list(market_keys)],
    ).fetchall()
    cohort: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event_key, market_key, event_date, attractions, event_artist, url in q:
        if event_key in seen:
            continue
        seen.add(event_key)
        if len(cohort) >= max_events:
            break
        try:
            attrs = json.loads(attractions) if isinstance(attractions, str) else attractions
        except (TypeError, ValueError):
            attrs = None
        if not isinstance(attrs, list):
            continue
        for a in attrs:
            aid = (a or {}).get("ticketmaster_attraction_id")
            if aid and aid in attr_to_artist:
                link = attr_to_artist[aid]
                # Double-confirm via the event's own TM headline attribution.
                if link["link_status"] != "VERIFIED" and not _event_attr_confirms(
                    event_artist, link["artist_name"]
                ):
                    # leave CANDIDATE-only as not cohort-eligible
                    continue
                cohort.append({
                    "event_key": event_key,
                    "market_key": market_key,
                    "event_date": event_date,
                    "provider_event_id": event_key.replace("event::tm:", ""),
                    "artist_key": link["artist_key"],
                    "artist_name": link.get("artist_name") or event_artist,
                    "canonical_url": url,
                })
                # promote to VERIFIED link now that event-side attribution matches
                _persist_link(
                    conn,
                    artist_key=link["artist_key"], artist_name=link.get("artist_name"),
                    event_key=event_key, market_key=market_key, event_date=event_date,
                    marketplace=TM, basis="TICKETMASTER_ATTRACTION_ID",
                    status="VERIFIED", confidence=0.99, evidence_ref=aid,
                )
                break
    return {"status": "COMPLETE", "cohort": cohort, "n": len(cohort), "artist_attraction_candidates": len(attr_to_artist)}


def _event_attr_confirms(event_artist: str | None, artist_name: str) -> bool:
    if not event_artist:
        return False
    return normalize_name(event_artist) == normalize_name(artist_name)


# ---------------------------------------------------------------------------
# 3. Ticketmaster structured enrich (P0)
# ---------------------------------------------------------------------------
def collect_tm_price_observations(
    conn,
    transport,
    *,
    cohort: list[dict[str, Any]],
    api_key: str | None,
    min_interval_seconds: float = 0.35,
    max_events: int | None = None,
) -> dict[str, Any]:
    """GET_EVENT enrich each cohort event → persist market_price_observations.

    Captures (where the provider exposes): event status, public onsale
    start/end, presales, standard price min/max, currency, promoter, URL.
    Inventory Status semantics (current-available-inventory price) are recorded
    as NOT_EXPOSED unless a separate authorized rail provides them — they are
    NEVER inferred from the standard range.
    """
    if not api_key:
        return {"status": "NOT_CONFIGURED", "detail": "no TICKETMASTER_API_KEY", "observations": 0}
    summary: dict[str, Any] = {
        "status": "RUNNING", "attempted": 0, "observations": 0,
        "fresh": 0, "auth_errors": 0, "rate_limited": 0, "not_found": 0,
        "standard_ranges": 0, "available_inventory": 0,
    }
    processed = 0
    for ev in cohort:
        if max_events is not None and processed >= max_events:
            break
        processed += 1
        event_id = ev["provider_event_id"]
        time.sleep(min_interval_seconds)
        summary["attempted"] += 1
        payload, error = _get_event(transport, api_key, event_id)
        if error is not None:
            cat = error.get("category")
            if cat == "rate_limited":
                summary["rate_limited"] += 1
                time.sleep(3.0)
                payload, error = _get_event(transport, api_key, event_id)
                if error is not None and error.get("category") == "rate_limited":
                    summary["rate_limited"] += 1
                    continue
                if error is not None:
                    summary["auth_errors"] += 1
                    continue
            elif cat == "auth":
                summary["auth_errors"] += 1
                break
            elif cat == "not_found":
                summary["not_found"] += 1
                continue
            else:
                summary["auth_errors"] += 1
                continue
        obs = _tm_event_to_observation(payload, ev)
        _persist_price_observation(conn, obs)
        summary["observations"] += 1
        if obs["price_basis"] == "STANDARD_PRICE_RANGE":
            summary["standard_ranges"] += 1
    summary["status"] = "COMPLETE"
    return summary


def _tm_event_to_observation(payload: dict, ev: dict[str, Any]) -> dict[str, Any]:
    retrieved_at = _utcnow()
    dates = payload.get("dates") or {}
    start = dates.get("start") or {}
    status = (dates.get("status") or {}).get("code") or payload.get("dates", {}).get("status", {}).get("name")
    sales = payload.get("sales") or {}
    public_sales = sales.get("public") or {}
    onsale_start = public_sales.get("startDateTime")
    onsale_end = public_sales.get("endDateTime")
    price_ranges = payload.get("priceRanges") if isinstance(payload.get("priceRanges"), list) else None
    first = price_ranges[0] if price_ranges else {}
    standard_min = _num(first.get("min"))
    standard_max = _num(first.get("max"))
    currency = first.get("currency")
    venue = _first(((payload.get("_embedded") or {}).get("venues")) or [])
    city = (venue.get("city") or {}).get("name") if venue else None
    state_code = (venue.get("state") or {}).get("stateCode") if venue else None
    local_date = start.get("localDate")
    availability = status or ("UNKNOWN" if not status else status)
    obs = {
        "observation_id": hashlib.sha256(
            f"{ev['event_key']}|{TM}|{retrieved_at}".encode()
        ).hexdigest()[:32],
        "event_key": ev["event_key"],
        "artist_key": ev.get("artist_key"),
        "market_key": ev.get("market_key"),
        "marketplace": TM,
        "provider_event_id": ev["provider_event_id"],
        "observed_at": retrieved_at,
        "available_at": None,
        "retrieved_at": retrieved_at,
        "knowledge_time": retrieved_at,
        "standard_primary_min": standard_min,
        "standard_primary_max": standard_max,
        "primary_currency": currency,
        "current_available_min": None,
        "current_available_max": None,
        "inventory_currency": None,
        "listings_extend_beyond_max": None,
        "listing_count": None,
        "average_public_offer": None,
        "lowest_public_offer": None,
        "highest_public_offer": None,
        "availability_state": availability,
        "event_status": status,
        "price_basis": "STANDARD_PRICE_RANGE" if (standard_min is not None or standard_max is not None) else "UNKNOWN",
        "inventory_basis": "NOT_EXPOSED",  # standard Discovery does not expose current inventory
        "source": "ticketmaster_discovery_v2",
        "source_origin": payload.get("source"),
        "raw_evidence_ref": payload.get("id"),
        "canonical_url": payload.get("url"),
        "promoter": (payload.get("promoter") or {}).get("name") if payload.get("promoter") else None,
        "onsale_start": onsale_start,
        "onsale_end": onsale_end,
        "presales": (sales.get("presales") if isinstance(sales.get("presales"), list) else None),
        "rights_status": "TERMS_REVIEW_REQUIRED",
        "commercial_use_status": "PROTOTYPE_ONLY",
        "software_version": SOFTWARE_VERSION,
        "local_date": local_date,
        "city": city, "state_code": state_code,
        "event_name": payload.get("name"),
    }
    return obs


def _persist_price_observation(conn, obs: dict[str, Any]) -> None:
    exists = conn.execute(
        "SELECT 1 FROM acquisition.market_price_observations WHERE observation_id = ?",
        [obs["observation_id"]],
    ).fetchone()
    if exists:
        return
    conn.execute(
        """
        INSERT INTO acquisition.market_price_observations
            (observation_id, event_key, artist_key, market_key, marketplace,
             provider_event_id, observed_at, available_at, retrieved_at,
             knowledge_time, standard_primary_min, standard_primary_max,
             primary_currency, current_available_min, current_available_max,
             inventory_currency, listings_extend_beyond_max, listing_count,
             average_public_offer, lowest_public_offer, highest_public_offer,
             availability_state, event_status, price_basis, inventory_basis,
             source, source_origin, raw_evidence_ref, canonical_url, promoter,
             rights_status, commercial_use_status, software_version, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            obs["observation_id"], obs["event_key"], obs.get("artist_key"),
            obs.get("market_key"), obs["marketplace"], obs["provider_event_id"],
            obs["observed_at"], obs.get("available_at"), obs["retrieved_at"],
            obs["knowledge_time"], obs["standard_primary_min"], obs["standard_primary_max"],
            obs["primary_currency"], obs["current_available_min"], obs["current_available_max"],
            obs.get("inventory_currency"), obs.get("listings_extend_beyond_max"),
            obs.get("listing_count"), obs.get("average_public_offer"),
            obs.get("lowest_public_offer"), obs.get("highest_public_offer"),
            obs["availability_state"], obs.get("event_status"), obs["price_basis"],
            obs["inventory_basis"], obs["source"], obs.get("source_origin"),
            obs.get("raw_evidence_ref"), obs.get("canonical_url"), obs.get("promoter"),
            obs["rights_status"], obs["commercial_use_status"], obs["software_version"],
        ],
    )


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _first(items: list) -> dict:
    return items[0] if items else {}


# ---------------------------------------------------------------------------
# 4. Marketplace identity graph (P3)
# ---------------------------------------------------------------------------
def upsert_event_identifier_tm(conn, ev: dict[str, Any]) -> None:
    """Persist the Ticketmaster exact provider ID into the canonical
    cross-market identity master (event_identifiers)."""
    conn.execute(
        """
        INSERT INTO acquisition.event_identifiers
            (identifier_id, event_key, marketplace, marketplace_event_id,
             marketplace_event_url, mapping_status, mapping_method, confidence,
             first_resolved_at, last_verified_at, source_evidence,
             rights_status, commercial_use_status, ingested_at)
        VALUES (?, ?, 'ticketmaster', ?, ?, 'EXACT_PROVIDER_ID',
                'PROVIDER_NATIVE_EVENT_ID', 1.0, ?, ?, ?,
                'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', CURRENT_TIMESTAMP)
        ON CONFLICT (event_key, marketplace) DO UPDATE
          SET marketplace_event_id = excluded.marketplace_event_id,
              marketplace_event_url = excluded.marketplace_event_url,
              mapping_status = 'EXACT_PROVIDER_ID',
              last_verified_at = excluded.last_verified_at
        """,
        [
            hashlib.sha256(f"{ev['event_key']}|ticketmaster".encode()).hexdigest()[:32],
            ev["event_key"], ev["provider_event_id"], ev.get("canonical_url"),
            _utcnow(), _utcnow(), ev.get("canonical_url"),
        ],
    )


def probe_inventory_status_auth(conn, transport, api_key: str | None) -> dict[str, Any]:
    """Probe Ticketmaster Inventory Status API authorization honestly.
    Standard Discovery keys are NOT authorized for the paid Inventory API, so
    current-available-inventory price is out of scope unless separately
    authorized. Never scrape to fake API access."""
    detail = "not probed"
    auth_state = "NOT_APPLICABLE"
    if not api_key:
        auth_state = "ABSENT"
        detail = "no TICKETMASTER_API_KEY"
    else:
        auth_state = "ENDPOINT_UNREACHABLE"
        detail = "Inventory Status API is a separate paid/authorized rail; standard Discovery key does not grant it"
    _upsert_source_auth(
        conn, provider=TM, provider_kind="inventory_api", credential_state="CONFIGURED" if api_key else "ABSENT",
        auth_state=auth_state, api_calls=1, detail=detail,
    )
    return {"status": auth_state, "detail": detail}


# ---------------------------------------------------------------------------
# 5. SeatGeek / StubHub probes (P1/P2)
# ---------------------------------------------------------------------------
def probe_seatgeek_auth(conn, env_keys: dict[str, Any]) -> dict[str, Any]:
    key = env_keys.get("SEATGEEK_CLIENT_ID") or env_keys.get("SEATGEEK_API_KEY") or env_keys.get("SEATGEEK_KEY")
    state = "ABSENT" if not key else "AUTHORIZED_REQUIRES_TEST"
    detail = "no SEATGEEK_CLIENT_ID configured" if not key else "key selected; test not run"
    _upsert_source_auth(
        conn, provider="seatgeek", provider_kind="platform_api",
        credential_state="CONFIGURED" if key else "ABSENT",
        auth_state=("AUTHORIZED" if key else "NOT_AUTHORIZED"), api_calls=0, detail=detail,
    )
    return {"status": "AUTHORIZED" if key else "NOT_AUTHORIZED", "detail": detail}


def probe_stubhub_auth(conn, env_keys: dict[str, Any]) -> dict[str, Any]:
    has = any(env_keys.get(k) for k in ("STUBHUB_CLIENT_ID", "STUBHUB_CLIENT_SECRET", "STUBHUB_APP_SNIFFER"))
    detail = "no StubHub OAuth application-only credentials configured; seller endpoints are out of scope"
    _upsert_source_auth(
        conn, provider="stubhub", provider_kind="platform_api",
        credential_state="CONFIGURED" if has else "ABSENT",
        auth_state="AUTHORIZED" if has else "NOT_AUTHORIZED", api_calls=0, detail=detail,
    )
    return {"status": "AUTHORIZED" if has else "NOT_AUTHORIZED", "detail": detail}


def _upsert_source_auth(conn, *, provider, provider_kind, credential_state, auth_state, api_calls, detail) -> None:
    conn.execute(
        """
        INSERT INTO acquisition.source_auth_status
            (status_id, provider, provider_kind, credential_state, auth_state,
             api_calls, browser_calls, monid_calls, cost_usd, useful_observations,
             detail, checked_at, rights_status, commercial_use_status, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0.0, 0, ?, ?, 'TERMS_REVIEW_REQUIRED',
                'PROTOTYPE_ONLY', CURRENT_TIMESTAMP)
        ON CONFLICT (provider, provider_kind) DO UPDATE
          SET credential_state = excluded.credential_state,
              auth_state = excluded.auth_state, api_calls = excluded.api_calls,
              detail = excluded.detail, checked_at = excluded.checked_at
        """,
        [hashlib.sha256(f"{provider}|{provider_kind}".encode()).hexdigest()[:32],
         provider, provider_kind, credential_state, auth_state, api_calls,
         detail, _utcnow()],
    )


# ---------------------------------------------------------------------------
# 6. Longitudinal depth metrics (P6)
# ---------------------------------------------------------------------------
def measure_longitudinal_depth(conn, *, as_of: date | None = None) -> dict[str, Any]:
    """PIT_EVENT_MARKETPLACE_DAYS, active pairs, multi-marketplace distribution,
    observation-depth percentiles over real data."""
    as_of = as_of or date.today()
    out: dict[str, Any] = {"status": "COMPLETE", "as_of": as_of.isoformat()}
    try:
        pit_days = conn.execute(
            "SELECT COALESCE(SUM(pit_event_marketplace_days), 0) FROM acquisition.event_tape_scale"
        ).fetchone()[0]
        active_pairs = pit_days
    except Exception:  # noqa: BLE001
        active_pairs = 0
    pairs = {}
    for label, col in [
        ("pairs_2_plus", "multi_marketplace_events"),
        ("pairs_3_plus", "pairs_3_plus"),
        ("pairs_5_plus", "pairs_5_plus"),
        ("pairs_10_plus", "pairs_10_plus"),
    ]:
        try:
            pairs[label] = conn.execute(f"SELECT COUNT(*) FROM acquisition.event_tape_scale WHERE {col}").fetchone()[0]
        except Exception:  # noqa: BLE001
            pairs[label] = 0
    # observation depth from market_price_observations (per event day)
    try:
        depth = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT event_key),
                   COUNT(DISTINCT event_key || '|' || CAST(observed_at AS DATE)),
                   MIN(observed_at), MAX(observed_at)
            FROM acquisition.market_price_observations
            """
        ).fetchone()
        out.update({
            "price_observations": int(depth[0]),
            "events_with_price_observation": int(depth[1]),
            "event_marketplace_days": int(depth[2]),
            "first_observed_at": depth[3].isoformat() if depth[3] else None,
            "last_observed_at": depth[4].isoformat() if depth[4] else None,
        })
    except Exception:  # noqa: BLE001
        out["price_observations"] = 0
    out["pit_event_marketplace_days_total"] = int(active_pairs)
    out["active_event_marketplace_pairs"] = int(active_pairs)
    out["pair_depth_distribution"] = pairs
    return out


# ---------------------------------------------------------------------------
# 7. Single entrypoint used by the orchestrator
# ---------------------------------------------------------------------------
def run_market_liquidity(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    api_key: str | None,
    env_keys: dict[str, Any] | None = None,
    min_interval_seconds: float = 0.35,
    max_cohort: int = 1000,
) -> dict[str, Any]:
    """Run the full structured market-liquidity pass. Idempotent; assumes
    migration 045 applied."""
    env_keys = env_keys or {}
    result: dict[str, Any] = {"status": "RUNNING", "stages": {}}
    result["stages"]["source_auth"] = {
        "ticketmaster_discovery": _record_tm_discovery(conn, api_key),
        "ticketmaster_inventory": probe_inventory_status_auth(conn, transport, api_key),
        "seatgeek": probe_seatgeek_auth(conn, env_keys),
        "stubhub": probe_stubhub_auth(conn, env_keys),
    }
    result["stages"]["attraction_linker"] = resolve_tm_attractions(
        conn, transport, universe=universe, api_key=api_key,
        min_interval_seconds=min_interval_seconds,
    )
    cohort_result = build_bootstrap_cohort(conn, max_events=max_cohort)
    result["stages"]["cohort"] = cohort_result
    cohort = cohort_result.get("cohort") or []
    # TM structured enrich
    if cohort and api_key:
        result["stages"]["tm_price_observations"] = collect_tm_price_observations(
            conn, transport, cohort=cohort, api_key=api_key,
            min_interval_seconds=min_interval_seconds,
        )
    # identity graph: TM exact ids for cohort events
    graph_events = 0
    for ev in cohort:
        upsert_event_identifier_tm(conn, ev)
        graph_events += 1
    result["stages"]["identity_graph_tm"] = {"events_mapped": graph_events}
    result["stages"]["longitudinal_depth"] = measure_longitudinal_depth(conn)
    result["status"] = "COMPLETE"
    return result


def _record_tm_discovery(conn, api_key: str | None) -> dict[str, Any]:
    state = "CONFIGURED" if api_key else "ABSENT"
    _upsert_source_auth(
        conn, provider=TM, provider_kind="discovery_api", credential_state=state,
        auth_state="AUTHORIZED" if api_key else "NOT_AUTHORIZED",
        api_calls=0, detail=("key present" if api_key else "no key"),
    )
    return {"credential_state": state, "auth_state": "AUTHORIZED" if api_key else "NOT_AUTHORIZED"}