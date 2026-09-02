"""Artist × Market expansion for ARTIST_SECURITY_25000.

Materializes asm.artist_market_security_v1 rows ONLY where evidence exists:
historical MB events with a resolvable market, festival series appearances,
and ticket evidence. No Cartesian product; no demand forecast.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

MARKET_MAP = {
    # state/province -> market key (primary US live markets)
    "IL": "chicago-il", "NY": "new-york-ny", "CA": "los-angeles-ca",
    "NV": "las-vegas-nv", "TN": "nashville-tn", "TX": "dallas-tx",
    "GA": "atlanta-ga", "FL": "miami-fl", "WA": "seattle-wa",
    "CO": "denver-co", "AZ": "phoenix-az", "PA": "philadelphia-pa",
    "MA": "boston-ma", "DC": "washington-dc", "MD": "washington-dc",
    "MI": "detroit-mi", "MN": "minneapolis-mn", "MO": "st-louis-mo",
    "OH": "cleveland-oh", "OR": "portland-or", "UT": "salt-lake-city-ut",
    "NC": "charlotte-nc", "LA": "new-orleans-la", "VA": "richmond-va",
}

# City name (lowercased) -> market key.  MB place.area carries city names,
# not state/province names, so this is the primary mapping path.
CITY_MARKET_MAP: dict[str, str] = {
    # US major markets
    "new york": "new-york-ny", "los angeles": "los-angeles-ca",
    "chicago": "chicago-il", "nashville": "nashville-tn",
    "las vegas": "las-vegas-nv", "austin": "austin-tx",
    "miami": "miami-fl", "atlanta": "atlanta-ga",
    "seattle": "seattle-wa", "san francisco": "san-francisco-ca",
    "denver": "denver-co", "phoenix": "phoenix-az",
    "philadelphia": "philadelphia-pa", "boston": "boston-ma",
    "washington": "washington-dc", "detroit": "detroit-mi",
    "minneapolis": "minneapolis-mn", "portland": "portland-or",
    "dallas": "dallas-tx", "houston": "houston-tx",
    "charlotte": "charlotte-nc", "new orleans": "new-orleans-la",
    "salt lake city": "salt-lake-city-ut", "hollywood": "los-angeles-ca",
    "brooklyn": "new-york-ny", "baltimore": "baltimore-md",
    "saint louis": "st-louis-mo", "st. louis": "st-louis-mo",
    "columbus": "columbus-oh", "cleveland": "cleveland-oh",
    "indianapolis": "indianapolis-in", "kansas city": "kansas-city-mo",
    "memphis": "memphis-tn", "sacramento": "sacramento-ca",
    "san diego": "san-diego-ca", "tampa": "tampa-fl",
    "orlando": "orlando-fl", "pittsburgh": "pittsburgh-pa",
    # International major markets
    "london": "london-uk", "manchester": "manchester-uk",
    "birmingham": "birmingham-uk", "glasgow": "glasgow-uk",
    "edinburgh": "edinburgh-uk", "bristol": "bristol-uk",
    "paris": "paris-fr", "berlin": "berlin-de",
    "münchen": "munich-de", "munich": "munich-de",
    "hamburg": "hamburg-de", "köln": "cologne-de",
    "amsterdam": "amsterdam-nl", "rotterdam": "rotterdam-nl",
    "toronto": "toronto-on", "vancouver": "vancouver-bc",
    "montreal": "montreal-qc", "ottawa": "ottawa-on",
    "calgary": "calgary-ab", "edmonton": "edmonton-ab",
    "sydney": "sydney-au", "melbourne": "melbourne-au",
    "tokyo": "tokyo-jp", "osaka": "osaka-jp",
    "stockholm": "stockholm-se", "oslo": "oslo-no",
    "copenhagen": "copenhagen-dk", "helsinki": "helsinki-fi",
    "dublin": "dublin-ie", "madrid": "madrid-es",
    "barcelona": "barcelona-es", "rome": "rome-it",
    "milan": "milan-it", "zürich": "zurich-ch",
    "brussels": "brussels-be", "prague": "prague-cz",
    "warsaw": "warsaw-pl", "budapest": "budapest-hu",
    "mexico city": "mexico-city-mx", "são paulo": "sao-paulo-br",
    "buenos aires": "buenos-aires-ar", "bogotá": "bogota-co",
    "seoul": "seoul-kr", "bangkok": "bangkok-th",
    "singapore": "singapore-sg", "hong kong": "hong-kong-hk",
    "mumbai": "mumbai-in", "delhi": "delhi-in",
}


def market_from_city(city: str | None) -> str | None:
    """Resolve a city name (as found in raw.musicbrainz_place.area) to a market key."""
    if not city:
        return None
    c = city.strip().lower()
    return CITY_MARKET_MAP.get(c)

# Full state/province name -> 2-letter code (MB place areas carry names).
STATE_NAME_TO_CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC", "Ontario": "ON", "British Columbia": "BC",
    "Quebec": "QC", "Alberta": "AB", "Washington, D.C.": "DC",
}


def market_from_state(state: str | None) -> str | None:
    if not state:
        return None
    s = state.strip()
    code = STATE_NAME_TO_CODE.get(s)
    if not code:
        code = s.upper()
    return MARKET_MAP.get(code)


def row_key(*, artist_key: str, market_key: str, as_of: date, version: str = "artist_market_25k_v1") -> str:
    return hashlib.sha256(f"{artist_key}|{market_key}|{as_of.isoformat()}|{version}".encode()).hexdigest()[:32]


def expand_artist_market(
    conn, *,
    as_of: date | None = None,
    min_evidence: int = 1,
) -> dict[str, Any]:
    """Build artist×market rows from MB event performers joined to places.

    Uses raw.musicbrainz_place area to derive markets; MB event performers
    link artists to events; event→place relations (from entity_relationships
    OR from the event payload) give venue/city.
    """
    as_of = as_of or date.today()
    now = datetime.now(timezone.utc).isoformat()
    summary = {"status": "RUNNING", "candidate_rows": 0, "rows_written": 0, "artists_covered": 0}

    # Strategy 1: Use core.entity_relationships EVENT_AT_PLACE links (fast).
    rows = conn.execute(
        """
        SELECT ep.artist_mbid, e.begin_date, p.area
        FROM core.event_performers ep
        JOIN raw.musicbrainz_event e ON e.mbid = ep.event_mbid
        JOIN core.entity_relationships r
          ON r.subject_entity_type = 'EVENT'
          AND r.subject_key = 'mbid::' || ep.event_mbid
          AND r.predicate = 'EVENT_AT_PLACE'
          AND r.object_entity_type = 'PLACE'
        JOIN raw.musicbrainz_place p ON p.mbid = replace(r.object_key, 'mbid::', '')
        WHERE ep.artist_mbid IS NOT NULL
          AND e.begin_date IS NOT NULL
          AND p.area IS NOT NULL
        """,
    ).fetchall()

    # Strategy 2: Parse event payload for place relations (covers events without
    # entity_relationships links — the majority).
    payload_rows = conn.execute(
        """
        SELECT ep.artist_mbid, e.begin_date, e.payload
        FROM core.event_performers ep
        JOIN raw.musicbrainz_event e ON e.mbid = ep.event_mbid
        WHERE ep.artist_mbid IS NOT NULL
          AND e.begin_date IS NOT NULL
          AND e.payload IS NOT NULL
          AND 'mbid::' || ep.event_mbid NOT IN (
              SELECT subject_key FROM core.entity_relationships
              WHERE predicate = 'EVENT_AT_PLACE'
          )
        """,
    ).fetchall()

    seen = set()
    for artist_mbid, begin_date, area in rows:
        market = market_from_city(area) or market_from_state(area) if area else None
        if not market:
            continue
        key = (artist_mbid, market, str(begin_date))
        if key not in seen:
            seen.add(key)
            agg_key = (artist_mbid, market)
            # Will be handled below

    # Aggregate from Strategy 1
    agg: dict[tuple[str, str], list] = {}
    for artist_mbid, begin_date, area in rows:
        market = market_from_city(area) or market_from_state(area) if area else None
        if not market:
            continue
        agg.setdefault((artist_mbid, market), []).append(begin_date)

    # Strategy 2: parse payloads for place relations
    import json as _json
    for artist_mbid, begin_date, payload_raw in payload_rows:
        try:
            payload = _json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:
            continue
        for rel in payload.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            if rel.get("target-type") != "place" or rel.get("type") != "held at":
                continue
            place = rel.get("place") or {}
            place_mbid = place.get("id") if isinstance(place, dict) else None
            if not place_mbid:
                continue
            # Look up area from raw.musicbrainz_place
            area_row = conn.execute(
                "SELECT area FROM raw.musicbrainz_place WHERE mbid = ?",
                [place_mbid],
            ).fetchone()
            if not area_row or not area_row[0]:
                continue
            market = market_from_city(area_row[0]) or market_from_state(area_row[0])
            if market:
                agg.setdefault((artist_mbid, market), []).append(begin_date)

    # Join canonical artist keys.
    mbid_to_key = dict(conn.execute(
        "SELECT musicbrainz_id, artist_key FROM core.artists WHERE musicbrainz_id IS NOT NULL"
    ).fetchall())

    for (artist_mbid, market), dates in agg.items():
        artist_key = mbid_to_key.get(artist_mbid)
        if not artist_key:
            continue
        if len(dates) < min_evidence:
            continue
        summary["candidate_rows"] += 1
        dlist = sorted(d for d in dates if d)
        first, last = dlist[0], dlist[-1]
        try:
            last_d = date.fromisoformat(str(last))
            days_since = (as_of - last_d).days if last_d <= as_of else None
        except ValueError:
            days_since = None
        conn.execute(
            """
            INSERT INTO asm.artist_market_security_v1
                (row_key, artist_key, market_key, as_of, historical_shows,
                 days_since_last_market_show, market_venues_played, venue_progression,
                 upcoming_market_events, nearby_competing_events, ticket_evidence_count,
                 source_system, source_version, retrieved_at, rights_status,
                 commercial_use_status, evidence_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL,
                    'musicbrainz', 'artist_market_25k_v1', ?,
                    'SOURCE_LICENSE_REVIEWED', 'INTERNAL_ANALYTICS_ONLY', ?, CURRENT_TIMESTAMP)
            ON CONFLICT (row_key) DO UPDATE SET historical_shows = excluded.historical_shows,
                days_since_last_market_show = excluded.days_since_last_market_show,
                evidence_json = excluded.evidence_json
            """,
            [row_key(artist_key=artist_key, market_key=market, as_of=as_of),
             artist_key, market, as_of, len(dates), days_since, now,
             _json.dumps({"first_show": first, "last_show": last, "source": "musicbrainz_event_places"})],
        )
        summary["rows_written"] += 1

    summary["artists_covered"] = len({r[0] for r in agg.keys()})
    summary["status"] = "COMPLETE"
    return summary
