"""ARTIST_SECURITY_1000_SCALE_V1 — P10: ARTIST × MARKET security objects.

Materializes ``asm.artist_market_security_v1`` for the top US live markets —
the descriptive bridge to the buyer product. For each (artist, market) pair
ONLY observable factors are derived:

* historical shows (performance evidence with a date < as_of in the market)
* days since last market show
* market venues played (distinct venues)
* venue progression (ordered venue names)
* upcoming market events (provider estate, date >= as_of)
* nearby competing events (other-artist events in the same market)
* ticket evidence in market (marketplace listing observations)

No demand forecast. No booking recommendation. No attendance inference.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from ..identity.spotify import normalize_name
from .live_ticket import TOP_US_MARKETS, market_key_for

SOFTWARE_VERSION = "artist_market_security_v1"
MARKET_VERSION = "artist_market_security_v1000_v1"


def row_key(*, artist_key: str, market_key: str, as_of: str) -> str:
    material = "|".join([artist_key, market_key, as_of, MARKET_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def normalize_market_city(city: str) -> str:
    """Normalize provider city names to market-city canonical forms."""
    norm = normalize_name(city)
    aliases = {
        "lasvegas": "las vegas",
        "losangeles": "los angeles",
        "newyork": "new york",
        "sf": "san francisco",
        "nyc": "new york",
    }
    return aliases.get(norm, norm)


def provider_estate_for_markets(conn) -> list[dict[str, Any]]:
    """Distinct future provider events with market + artist identity."""
    rows = conn.execute(
        """
        SELECT platform_object_id, artist_name, city, state_code, local_date,
               venue_name, genre
        FROM events.provider_event_snapshots
        WHERE artist_name IS NOT NULL
          AND local_date IS NOT NULL
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for event_id, artist_name, city, state, local_date, venue, genre in rows:
        mkey = market_key_for(city, state)
        if not mkey:
            continue
        try:
            d = date.fromisoformat(str(local_date)[:10])
        except ValueError:
            continue
        out.append({
            "event_id": event_id, "artist_name": artist_name,
            "market_key": mkey, "event_date": d, "venue_name": venue, "genre": genre,
        })
    return out


def build_artist_market_rows(
    conn,
    *,
    universe: list[dict[str, Any]],
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Compute and persist asm.artist_market_security_v1 rows."""
    as_of = as_of or date.today()
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    keys = [a["artist_key"] for a in universe]
    if not keys:
        return {"status": "EMPTY_UNIVERSE", "rows_written": 0}

    # canonical market keys for the top markets
    market_cities: dict[str, str] = {}
    for city, state in TOP_US_MARKETS:
        mkey = market_key_for(city, state)
        if mkey:
            market_cities[normalize_market_city(city)] = mkey

    # performance evidence per artist
    perf = conn.execute(
        """
        SELECT artist_key, show_date, market_key, venue_name
        FROM metrics.artist_performance_observations
        WHERE artist_key IN (SELECT UNNEST(?))
        """,
        [keys],
    ).fetchall()
    perf_by_artist: dict[str, list[dict[str, Any]]] = {}
    for artist_key, show_date, market_key, venue in perf:
        d = show_date
        if isinstance(d, str):
            try:
                d = date.fromisoformat(str(d)[:10])
            except ValueError:
                continue
        if not market_key:
            continue
        perf_by_artist.setdefault(artist_key, []).append({
            "show_date": d, "market_key": market_key, "venue": venue,
        })

    # future events from provider estate, per market
    estate = provider_estate_for_markets(conn)
    future_by_market: dict[str, list[dict[str, Any]]] = {}
    for ev in estate:
        future_by_market.setdefault(ev["market_key"], []).append(ev)

    # artist name → artist_key for estate matching
    name_to_key: dict[str, str] = {}
    for a in universe:
        nm = normalize_name(a.get("artist_name") or a["artist_key"])
        if nm:
            name_to_key[nm] = a["artist_key"]

    # ticket evidence: event→artist mapping via estate events
    event_artist: dict[str, str] = {}
    for ev in estate:
        nm = normalize_name(ev["artist_name"])
        if nm in name_to_key:
            event_artist[ev["event_id"]] = name_to_key[nm]
    ticket_by_artist_market: dict[tuple[str, str], int] = {}
    try:
        obs = conn.execute(
            "SELECT event_key, marketplace FROM acquisition.marketplace_listing_observations"
        ).fetchall()
        for event_key, _mkt in obs:
            eid = str(event_key).replace("event::tm:", "")
            artist_key = event_artist.get(eid)
            if not artist_key:
                continue
            # find the market for that event
            for ev in estate:
                if ev["event_id"] == eid:
                    ticket_by_artist_market[(artist_key, ev["market_key"])] = (
                        ticket_by_artist_market.get((artist_key, ev["market_key"]), 0) + 1
                    )
                    break
    except Exception:  # noqa: BLE001 — marketplace tables may be empty
        ticket_by_artist_market = {}

    written = 0
    rows_out: list[dict[str, Any]] = []
    for artist in universe:
        artist_key = artist["artist_key"]
        nm = normalize_name(artist.get("artist_name") or artist_key)
        shows = perf_by_artist.get(artist_key, [])
        for city_canonical, mkey in market_cities.items():
            market_shows = sorted(
                (s for s in shows if (s["market_key"] or "") == mkey),
                key=lambda s: s["show_date"],
            )
            past_shows = [s for s in market_shows if s["show_date"] <= as_of]
            historical_shows = len(past_shows)
            days_since_last = None
            venue_progression = None
            market_venues = None
            if past_shows:
                last = past_shows[-1]["show_date"]
                days_since_last = (as_of - last).days
                venue_progression = [s["venue"] for s in past_shows if s.get("venue")]
                market_venues = len({s["venue"] for s in past_shows if s.get("venue")})

            # upcoming + competing events in the market
            mkt_future = future_by_market.get(mkey, [])
            upcoming = sum(
                1 for ev in mkt_future
                if ev["event_date"] >= as_of and ev["artist_name"]
                and normalize_name(ev["artist_name"]) == nm
            )
            competing = sum(
                1 for ev in mkt_future
                if ev["event_date"] >= as_of and ev["artist_name"]
                and normalize_name(ev["artist_name"]) != nm
            )
            ticket_evidence = ticket_by_artist_market.get((artist_key, mkey), 0)

            # Only materialize markets where the artist has real evidence of
            # SOME kind (show, upcoming event, or ticket evidence). Empty
            # artist×market rows add no information.
            if not (historical_shows or upcoming or ticket_evidence):
                continue
            rkey = row_key(artist_key=artist_key, market_key=mkey, as_of=as_of.isoformat())
            exists = conn.execute(
                "SELECT 1 FROM asm.artist_market_security_v1 WHERE row_key = ?", [rkey]
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO asm.artist_market_security_v1
                    (row_key, artist_key, market_key, as_of, historical_shows,
                     days_since_last_market_show, market_venues_played,
                     venue_progression, upcoming_market_events,
                     nearby_competing_events, ticket_evidence_count,
                     source_system, source_version, retrieved_at, rights_status,
                     commercial_use_status, evidence_json, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'setlistfm+ticketmaster',
                        ?, ?, 'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', ?,
                        CURRENT_TIMESTAMP)
                """,
                [
                    rkey, artist_key, mkey, as_of.isoformat(),
                    historical_shows if historical_shows else None,
                    days_since_last,
                    market_venues,
                    json.dumps(venue_progression, default=str) if venue_progression else None,
                    upcoming if upcoming else None,
                    competing if competing else None,
                    ticket_evidence if ticket_evidence else None,
                    MARKET_VERSION, retrieved_at,
                    json.dumps({
                        "semantics": "OBSERVABLE_ARTIST_MARKET_FACTORS; no demand forecast",
                        "top_market": mkey,
                    }, default=str),
                ],
            )
            written += 1
            rows_out.append({"artist_key": artist_key, "market_key": mkey})

    return {
        "status": "COMPLETE",
        "rows_written": written,
        "markets_covered": list(market_cities.values()),
        "sample": rows_out[:10],
        "as_of": as_of.isoformat(),
    }
