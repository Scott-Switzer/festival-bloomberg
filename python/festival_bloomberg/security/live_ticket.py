"""ARTIST_SECURITY_1000_SCALE_V1 — P5: live + ticket joins for the universe.

For every ARTIST_SECURITY_1000 security this module materializes:

* SHOWS_30D / SHOWS_90D / SHOWS_365D          (from real performance evidence)
* FESTIVAL_APPEARANCES                        (event_type == festival)
* MARKETS_PLAYED / VENUES_PLAYED              (distinct markets/venues)
* DAYS_SINCE_LAST_SHOW                        (vs as_of; NULL when none)
* venue progression indicators                (ordered venue-name progression)
* FUTURE events                               (provider_event_snapshots >= as_of)
* event-marketplace mappings + ticket observation counts + multi-marketplace
  event counts (marketplace listing observations)

Evidence sources:
1. SetlistFM official API (key-free content; SETLISTFM_API_KEY): per-artist
   setlists with eventDate (EVENT_TIME), venue, city/state → market. This is
   the PRIMARY historical performance rail for the universe.
2. MusicBrainz event dump (raw.musicbrainz_event begin_date) joined to
   core.event_performers — event_type (festival) comes from there.
3. events.provider_event_snapshots (Ticketmaster estate) — future events.
4. acquisition.marketplace_listing_observations — ticket evidence counts.

RULES:
* Never infer attendance; never infer sales from listing disappearance.
* as_of is the observation date; retrieved_at is provenance only.
* UNKNOWN stays NULL.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..acquisition.contracts import AcquisitionRequest
from ..acquisition.providers.setlistfm import SetlistFmProvider
from ..attention.listenbrainz import artist_key_for
from ..identity.spotify import normalize_name

SOFTWARE_VERSION = "artist_live_ticket_v1"
LIVE_VERSION = "artist_live_stats_v1000_v1"

#: Top US live markets for P10 (city, state) — ranked by provider estate depth.
TOP_US_MARKETS: tuple[tuple[str, str], ...] = (
    ("Las Vegas", "NV"),
    ("New York", "NY"),
    ("Chicago", "IL"),
    ("Los Angeles", "CA"),
    ("Nashville", "TN"),
    ("Denver", "CO"),
    ("Atlanta", "GA"),
    ("San Francisco", "CA"),
    ("Seattle", "WA"),
    ("Dallas", "TX"),
)


def market_key_for(city: str | None, state_code: str | None) -> str | None:
    """Canonical hyphenated market key (matches watch universe: ``chicago-il``)."""
    if not city or not state_code:
        return None
    city_norm = normalize_name(city).replace(" ", "-")
    return f"{city_norm}-{state_code.strip().lower()}"


def performance_key(*, artist_key: str, show_date: str, venue_name: str, source: str) -> str:
    material = "|".join([artist_key, show_date, venue_name or "", source, SOFTWARE_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def live_stat_key(*, artist_key: str, as_of: str) -> str:
    material = "|".join([artist_key, as_of, LIVE_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# SetlistFM performance history collection
# ---------------------------------------------------------------------------

def collect_setlistfm_history(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    api_key: str | None,
    max_records: int = 200,
    min_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    """Per-artist setlist history → metrics.artist_performance_observations.

    Bounded: ``max_records`` setlists per artist (paginated; the API caps at
    MAX_PAGES). Shows are EVENT_TIME (eventDate). Never attendance.
    """
    if not api_key:
        return {
            "status": "NOT_CONFIGURED",
            "detail": "SETLISTFM_API_KEY not set",
            "artists_eligible": 0,
            "rows_persisted": 0,
        }
    provider = SetlistFmProvider(transport=transport, env={"SETLISTFM_API_KEY": api_key})
    provider.throttle_seconds = min_interval_seconds
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_eligible": 0,
        "artists_with_shows": 0,
        "artists_no_results": 0,
        "artists_error": 0,
        "rows_persisted": 0,
        "rate_limited": False,
    }
    for artist in universe:
        mbid = artist.get("mbid")
        artist_key = artist["artist_key"]
        name = artist.get("artist_name") or artist_key
        if not mbid:
            continue
        summary["artists_eligible"] += 1
        req = AcquisitionRequest.new(
            entity_id=mbid,
            entity_type="artist",
            platform="setlistfm",
            query=name,
            operation="GET_ARTIST_SETLISTS",
            external_id=mbid,
            max_records=max_records,
            commercial_context="research",
        )
        result = provider.acquire(req)
        if result.status.value == "RATE_LIMITED":
            summary["rate_limited"] = True
            summary["status"] = "RATE_LIMITED_STOPPED"
            break
        if result.status.value == "NO_RESULTS":
            summary["artists_no_results"] += 1
            continue
        if result.status.value != "SUCCESS":
            summary["artists_error"] += 1
            continue
        added = 0
        for rec in result.records:
            show_date = (rec.get("event_time") or "")[:10]
            if not show_date:
                continue
            try:
                date.fromisoformat(show_date)
            except ValueError:
                continue
            venue = rec.get("venue_name")
            city = rec.get("city")
            state = rec.get("state_code")
            event_type = _event_type_of(rec)
            market = market_key_for(city, state)
            pkey = performance_key(
                artist_key=artist_key, show_date=show_date,
                venue_name=venue, source="setlistfm",
            )
            exists = conn.execute(
                "SELECT 1 FROM metrics.artist_performance_observations WHERE performance_key = ?",
                [pkey],
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO metrics.artist_performance_observations
                    (performance_key, artist_key, show_date, venue_name, venue_key,
                     city, state_code, country_code, market_key, event_type,
                     source_system, source_url, retrieved_at, rights_status,
                     commercial_use_status, evidence_json, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'setlistfm', ?, ?,
                        'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', ?, CURRENT_TIMESTAMP)
                """,
                [
                    pkey, artist_key, show_date, venue, rec.get("venue_id"),
                    city, state, rec.get("country_code"), market, event_type,
                    rec.get("canonical_url"),
                    result.completed_at.isoformat(),
                    json.dumps({
                        "setlist_id": rec.get("setlist_id"),
                        "tour_name": rec.get("tour_name"),
                        "market_context_method": rec.get("market_context_method"),
                        "semantics": "PERFORMANCE_HISTORY; never attendance",
                    }, default=str),
                ],
            )
            added += 1
        summary["rows_persisted"] += added
        if added:
            summary["artists_with_shows"] += 1
    summary["status"] = "COMPLETE"
    return summary


def _event_type_of(rec: dict[str, Any]) -> str:
    et = (rec.get("event_type") or "").upper()
    if et in ("TOUR_DATE", "FESTIVAL"):
        return et
    return "CONCERT"


# ---------------------------------------------------------------------------
# Live statistics materialization (from real performance evidence)
# ---------------------------------------------------------------------------

def derive_live_statistics(
    conn,
    *,
    universe: list[dict[str, Any]],
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """SHOWS_30D/90D/365D + markets/venues/days_since_last from evidence."""
    as_of = as_of or date.today()
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    keys = [a["artist_key"] for a in universe]
    if not keys:
        return {"status": "EMPTY_UNIVERSE", "rows_written": 0}

    # Load performance evidence for the universe (setlistfm + musicbrainz).
    rows = conn.execute(
        """
        SELECT artist_key, show_date, market_key, venue_name, event_type
        FROM metrics.artist_performance_observations
        WHERE artist_key IN (SELECT UNNEST(?))
        """,
        [keys],
    ).fetchall()
    by_artist: dict[str, list[dict[str, Any]]] = {}
    for artist_key, show_date, market_key, venue, event_type in rows:
        d = show_date
        if isinstance(d, str):
            try:
                d = date.fromisoformat(str(d)[:10])
            except ValueError:
                continue
        by_artist.setdefault(artist_key, []).append({
            "show_date": d, "market_key": market_key,
            "venue": venue, "event_type": event_type,
        })

    written = 0
    for artist in universe:
        artist_key = artist["artist_key"]
        shows = sorted(by_artist.get(artist_key, []), key=lambda r: r["show_date"])
        if not shows:
            continue
        cutoff_30 = as_of - timedelta(days=30)
        cutoff_90 = as_of - timedelta(days=90)
        cutoff_365 = as_of - timedelta(days=365)
        past = [s for s in shows if s["show_date"] <= as_of]
        shows_30 = sum(1 for s in past if s["show_date"] >= cutoff_30)
        shows_90 = sum(1 for s in past if s["show_date"] >= cutoff_90)
        shows_365 = sum(1 for s in past if s["show_date"] >= cutoff_365)
        festivals_365 = sum(
            1 for s in past
            if s["show_date"] >= cutoff_365 and str(s.get("event_type") or "").upper() == "FESTIVAL"
        )
        markets_365 = len({
            s["market_key"] for s in past
            if s["show_date"] >= cutoff_365 and s.get("market_key")
        })
        venues_365 = len({
            s["venue"] for s in past
            if s["show_date"] >= cutoff_365 and s.get("venue")
        })
        days_since_last = None
        if past:
            last = past[-1]["show_date"]
            if last <= as_of:
                days_since_last = (as_of - last).days
        venue_progression = [s["venue"] for s in past if s.get("venue")]
        stat_key = live_stat_key(artist_key=artist_key, as_of=as_of.isoformat())
        conn.execute(
            """
            INSERT INTO metrics.artist_live_statistics
                (stat_key, artist_key, as_of, shows_30d, shows_90d, shows_365d,
                 markets_365d, unique_venues_365d, festival_appearances_365d,
                 days_since_last_show, venue_progression, source_system,
                 source_version, retrieved_at, rights_status,
                 commercial_use_status, evidence_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'setlistfm+musicbrainz',
                    ?, ?, 'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', ?,
                    CURRENT_TIMESTAMP)
            ON CONFLICT (stat_key) DO UPDATE SET
                shows_30d = excluded.shows_30d,
                shows_90d = excluded.shows_90d,
                shows_365d = excluded.shows_365d,
                markets_365d = excluded.markets_365d,
                unique_venues_365d = excluded.unique_venues_365d,
                festival_appearances_365d = excluded.festival_appearances_365d,
                days_since_last_show = excluded.days_since_last_show,
                venue_progression = excluded.venue_progression,
                evidence_json = excluded.evidence_json
            """,
            [
                stat_key, artist_key, as_of.isoformat(), shows_30, shows_90, shows_365,
                markets_365 if markets_365 else None,
                venues_365 if venues_365 else None,
                festivals_365 if festivals_365 else None,
                days_since_last,
                json.dumps(venue_progression, default=str) if venue_progression else None,
                LIVE_VERSION, retrieved_at,
                json.dumps({
                    "shows_sampled": len(past),
                    "evidence": "artist_performance_observations",
                    "semantics": "PERFORMANCE_HISTORY; never attendance",
                }, default=str),
            ],
        )
        written += 1
    return {"status": "COMPLETE", "rows_written": written, "as_of": as_of.isoformat()}


# ---------------------------------------------------------------------------
# Future events + ticket evidence joins (provider estate + marketplace)
# ---------------------------------------------------------------------------

def join_future_events_and_tickets(
    conn,
    *,
    universe: list[dict[str, Any]],
    as_of: date | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Future events (provider estate) + marketplace ticket evidence per artist.

    Future events come from events.provider_event_snapshots (TM estate)
    matched by artist name → security universe. Ticket evidence counts come
    from acquisition.marketplace_listing_observations (event-keyed). This
    never infers attendance or sales.
    """
    as_of = as_of or date.today()
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    keys = [a["artist_key"] for a in universe]
    if not keys:
        return {"status": "EMPTY_UNIVERSE"}

    # artist_key -> normalized names for matching provider estate artist_name
    name_to_key: dict[str, str] = {}
    for a in universe:
        nm = normalize_name(a.get("artist_name") or a["artist_key"])
        if nm:
            name_to_key[nm] = a["artist_key"]

    estate = conn.execute(
        """
        SELECT artist_name, platform_object_id, city, state_code, local_date,
               venue_name, event_status
        FROM events.provider_event_snapshots
        WHERE artist_name IS NOT NULL
        """,
    ).fetchall()

    future_by_artist: dict[str, int] = {}
    markets_future: dict[str, set[str]] = {}
    matched_events: dict[str, set[str]] = {}
    for artist_name, event_id, city, state, local_date, venue, status in estate:
        nm = normalize_name(artist_name)
        key = name_to_key.get(nm)
        if not key:
            continue
        if not local_date:
            continue
        try:
            d = date.fromisoformat(str(local_date)[:10])
        except ValueError:
            continue
        if d >= as_of and status != "cancelled":
            future_by_artist[key] = future_by_artist.get(key, 0) + 1
            m = market_key_for(city, state)
            if m:
                markets_future.setdefault(key, set()).add(m)
            matched_events.setdefault(key, set()).add(event_id)

    # ticket observation counts (event-keyed marketplace observations)
    ticket_counts: dict[str, int] = {}
    try:
        obs = conn.execute(
            """
            SELECT m.event_key, COUNT(*) AS n
            FROM acquisition.marketplace_listing_observations o
            LEFT JOIN acquisition.marketplace_event_mappings m
              ON m.mapping_id = o.event_key
            GROUP BY m.event_key
            """
        ).fetchall()
        event_to_artist: dict[str, str] = {}
        for artist_key, event_ids in matched_events.items():
            for eid in event_ids:
                event_to_artist[f"event::tm:{eid}"] = artist_key
                event_to_artist[eid] = artist_key
        for event_key, n in obs:
            artist_key = event_to_artist.get(event_key) or event_to_artist.get(str(event_key))
            if artist_key:
                ticket_counts[artist_key] = ticket_counts.get(artist_key, 0) + int(n)
    except Exception:  # noqa: BLE001 — marketplace tables may be empty
        ticket_counts = {}

    # persist future/ticket evidence as artist×market factor observations
    rows_written = 0
    for artist_key in keys:
        fut = future_by_artist.get(artist_key)
        if fut is not None:
            rows_written += _market_factor_obs(
                conn, artist_key=artist_key, market_key=None,
                factor_name="FUTURE_EVENTS", value=fut, unit="events",
                as_of=as_of, retrieved_at=retrieved_at,
                source="ticketmaster_estate",
            )
        tickets = ticket_counts.get(artist_key)
        if tickets is not None:
            rows_written += _market_factor_obs(
                conn, artist_key=artist_key, market_key=None,
                factor_name="TICKET_OBSERVATIONS", value=tickets, unit="observations",
                as_of=as_of, retrieved_at=retrieved_at,
                source="marketplace_listings",
            )
        for market_key in sorted(markets_future.get(artist_key, set())):
            rows_written += _market_factor_obs(
                conn, artist_key=artist_key, market_key=market_key,
                factor_name="UPCOMING_MARKET_EVENTS", value=1, unit="events",
                as_of=as_of, retrieved_at=retrieved_at,
                source="ticketmaster_estate",
            )
    return {
        "status": "COMPLETE",
        "artists_with_future_events": len(future_by_artist),
        "artists_with_ticket_evidence": len(ticket_counts),
        "rows_written": rows_written,
    }


def _market_factor_obs(
    conn,
    *,
    artist_key: str,
    market_key: str | None,
    factor_name: str,
    value: float | None,
    unit: str,
    as_of: date,
    retrieved_at: str,
    source: str,
) -> int:
    if market_key is None:
        # Global (artist-level) LIVE evidence → the global factor table; the
        # market table's market_key is NOT NULL.
        obs_key = hashlib.sha256(
            f"{artist_key}|{factor_name}|{as_of.isoformat()}|{SOFTWARE_VERSION}".encode("utf-8")
        ).hexdigest()[:32]
        exists = conn.execute(
            "SELECT 1 FROM metrics.artist_factor_observations WHERE factor_observation_key = ?",
            [obs_key],
        ).fetchone()
        if exists:
            return 0
        conn.execute(
            """
            INSERT INTO metrics.artist_factor_observations
                (factor_observation_key, artist_key, factor_family, factor_name,
                 value, value_unit, as_of, retrieved_at, source_system, source_version,
                 rights_status, commercial_use_status, evidence_json, ingested_at)
            VALUES (?, ?, 'LIVE', ?, ?, ?, ?, ?, ?, ?, 'TERMS_REVIEW_REQUIRED',
                    'PROTOTYPE_ONLY', ?, CURRENT_TIMESTAMP)
            """,
            [
                obs_key, artist_key, factor_name, value, unit,
                as_of.isoformat(), retrieved_at, source, SOFTWARE_VERSION,
                json.dumps({"semantics": "OBSERVABLE_EVIDENCE; never demand forecast"}, default=str),
            ],
        )
        return 1
    obs_key = hashlib.sha256(
        f"{artist_key}|{market_key}|{factor_name}|{as_of.isoformat()}|{SOFTWARE_VERSION}".encode("utf-8")
    ).hexdigest()[:32]
    exists = conn.execute(
        "SELECT 1 FROM metrics.artist_market_factor_observations WHERE observation_key = ?",
        [obs_key],
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO metrics.artist_market_factor_observations
            (observation_key, artist_key, market_key, factor_family, factor_name,
             value, value_unit, as_of, retrieved_at, source_system, source_version,
             rights_status, commercial_use_status, evidence_json, ingested_at)
        VALUES (?, ?, ?, 'LIVE', ?, ?, ?, ?, ?, ?, ?, 'TERMS_REVIEW_REQUIRED',
                'PROTOTYPE_ONLY', ?, CURRENT_TIMESTAMP)
        """,
        [
            obs_key, artist_key, market_key, factor_name, value, unit,
            as_of.isoformat(), retrieved_at, source, SOFTWARE_VERSION,
            json.dumps({"semantics": "OBSERVABLE_EVIDENCE; never demand forecast"}, default=str),
        ],
    )
    return 1


def run_live_ticket(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    setlistfm_api_key: str | None,
    as_of: date | None = None,
    min_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    """Full P5 pass: setlist history → live stats → future/ticket joins."""
    hist = collect_setlistfm_history(
        conn, transport, universe=universe, api_key=setlistfm_api_key,
        min_interval_seconds=min_interval_seconds,
    )
    stats = derive_live_statistics(conn, universe=universe, as_of=as_of)
    joins = join_future_events_and_tickets(conn, universe=universe, as_of=as_of)
    return {
        "status": "COMPLETE",
        "setlistfm_history": hist,
        "live_statistics": stats,
        "future_ticket_joins": joins,
        "software_version": SOFTWARE_VERSION,
    }
