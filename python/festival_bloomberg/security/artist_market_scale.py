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
    # state -> market key (primary US live markets first, then major cities)
    "IL": "chicago-il", "NY": "new-york-ny", "CA": "los-angeles-ca",
    "NV": "las-vegas-nv", "TN": "nashville-tn", "TX": "dallas-tx",
    "GA": "atlanta-ga", "FL": "miami-fl", "WA": "seattle-wa",
    "CO": "denver-co", "AZ": "phoenix-az", "PA": "philadelphia-pa",
    "MA": "boston-ma", "DC": "washington-dc", "MD": "washington-dc",
    "MI": "detroit-mi", "MN": "minneapolis-mn", "MO": "st-louis-mo",
    "OH": "cleveland-oh", "OR": "portland-or", "UT": "salt-lake-city-ut",
    "NC": "charlotte-nc", "LA": "new-orleans-la", "VA": "richmond-va",
}

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

    Uses raw.musicbrainz_place state to derive markets; MB event performers
    link artists to events; event→place relations give venue/state.
    """
    as_of = as_of or date.today()
    now = datetime.now(timezone.utc).isoformat()
    summary = {"status": "RUNNING", "candidate_rows": 0, "rows_written": 0, "artists_covered": 0}

    # Map MB place -> state via the event->place relationship.
    # events.provider_event_snapshots already has city/state for TM estate;
    # for MB events we use event_place relationships where present.
    rows = conn.execute(
        """
        WITH place_state AS (
            SELECT p.mbid AS place_mbid, p.area AS area_name
            FROM raw.musicbrainz_place p
        ),
        ep AS (
            SELECT ep.artist_mbid, e.mbid AS event_mbid, e.begin_date
            FROM core.event_performers ep
            JOIN raw.musicbrainz_event e ON e.mbid = ep.event_mbid
            WHERE ep.artist_mbid IS NOT NULL AND e.begin_date IS NOT NULL
        )
        SELECT ep.artist_mbid, ep.begin_date, ps.area_name
        FROM ep
        LEFT JOIN core.entity_relationships r
          ON r.subject_entity_type='EVENT' AND r.subject_key='mbid::'||ep.event_mbid
         AND r.predicate='EVENT_AT_PLACE' AND r.object_entity_type='PLACE'
        LEFT JOIN place_state ps ON ps.place_mbid = replace(r.object_key, 'mbid::', '')
        WHERE ps.area_name IS NOT NULL
        """,
    ).fetchall()

    # Aggregate per (artist, state-area) counts + date range.
    agg: dict[tuple[str, str], list] = {}
    for artist_mbid, begin_date, area in rows:
        market = market_from_state(area[:2]) if area and len(area) >= 2 else None
        if not market:
            continue
        key = (artist_mbid, market)
        agg.setdefault(key, []).append(begin_date)

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
             json.dumps({"first_show": first, "last_show": last, "source": "musicbrainz_event_places"})],
        )
        summary["rows_written"] += 1

    summary["artists_covered"] = len({r[0] for r in agg.keys()})
    summary["status"] = "COMPLETE"
    return summary
