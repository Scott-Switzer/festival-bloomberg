"""Explainable market competitive calendar for proposed shows.

Answers, for an ARTIST x MARKET x DATE x VENUE proposal: *what else is
happening around this show that could compete for audience, spend, venue
demand, transportation or market attention — and which of those events were
actually knowable when the buyer was deciding?*

Deliberately NOT a competition score. Every candidate row carries the raw,
explainable dimensions (segment/genre/subgenre/family, venue, distance,
exact date delta, window, knowledge status) so a buyer can see WHY an event
appears and decide for themselves.

PIT contract (shared with ``planning.competition``):

* one event never counts as multiple competitors (dedupe by
  ``platform_object_id``, earliest knowledge time wins);
* a competitor's knowability is its EARLIEST ``knowledge_time`` across
  snapshots, classified into exactly one of:
    - ``known_before_cutoff``    earliest < cutoff  (visible at decision time);
    - ``observed_post_cutoff``   earliest >= cutoff (NOT visible at cutoff, but
      NOT missing data either);
    - ``unknown_knowledge_time`` no valid knowledge time.
* without a ``research_cutoff`` the result is a NON-PIT current-warehouse view
  (marked ``NON_PIT``), never historical evidence;
* ``retrieved_at`` is never treated as historical availability.

Geography: same venue / same city / within 5 / 10 / 25 / 50 miles by exact
haversine distance. Missing coordinates => distance UNKNOWN, never assumed.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from .competition import BUCKET_KNOWN, BUCKET_POST, BUCKET_UNKNOWN, WINDOWS, _classify, _d

#: Approximate degrees of latitude for 50 miles (1 deg lat ~= 69 mi). Used only
#: as a coarse SQL pre-filter; the exact bucket uses haversine distance.
_LAT_BOX = 0.8
_LON_BOX = 0.8
_MAX_DISTANCE_MILES = 50.0

DISTANCE_SAME_VENUE = "same_venue"
DISTANCE_SAME_CITY = "same_city"
DISTANCE_WITHIN_5 = "within_5"
DISTANCE_WITHIN_10 = "within_10"
DISTANCE_WITHIN_25 = "within_25"
DISTANCE_WITHIN_50 = "within_50"
DISTANCE_BEYOND_50 = "beyond_50"
DISTANCE_UNKNOWN = "UNKNOWN"


def haversine_miles(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float | None:
    """Exact great-circle distance in miles. None if any coordinate missing."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    r_earth_miles = 3958.8
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_earth_miles * math.asin(math.sqrt(a))


def distance_bucket(miles: float | None, *, same_venue: bool = False, same_city: bool = False) -> str:
    """Bucket an exact distance. same_venue/same_city take precedence when the
    coordinates cannot be compared but identity is known."""
    if miles is None:
        return DISTANCE_UNKNOWN
    if same_venue:
        return DISTANCE_SAME_VENUE
    if same_city and miles == 0.0:
        return DISTANCE_SAME_CITY
    if miles <= 5.0:
        return DISTANCE_WITHIN_5
    if miles <= 10.0:
        return DISTANCE_WITHIN_10
    if miles <= 25.0:
        return DISTANCE_WITHIN_25
    if miles <= 50.0:
        return DISTANCE_WITHIN_50
    return DISTANCE_BEYOND_50


def _rows(conn, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _event_level_candidates(
    conn,
    *,
    city: str | None,
    state_code: str | None,
    target_lat: float | None,
    target_lon: float | None,
    lo: str,
    hi: str,
) -> list[dict[str, Any]]:
    """Event-level (deduped) candidate rows in the market +-14 day window.

    Universe = same city (case-insensitive) plus, when the target has
    coordinates, anything inside a coarse +/-0.8 deg box (exact distance is
    computed later; anything beyond 50 miles outside the same city is dropped).
    """
    params: list[Any] = [lo, hi]
    or_clauses: list[str] = []
    if city:
        or_clauses.append("LOWER(COALESCE(city, '')) = ?")
        params.append(city.lower())
    if target_lat is not None and target_lon is not None:
        or_clauses.append(
            "(latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?)"
        )
        params.extend([
            float(target_lat) - _LAT_BOX, float(target_lat) + _LAT_BOX,
            float(target_lon) - _LON_BOX, float(target_lon) + _LON_BOX,
        ])
    where = (
        "provider = 'ticketmaster' AND local_date IS NOT NULL "
        f"AND local_date BETWEEN ? AND ? AND ({' OR '.join(or_clauses)})"
    )
    return _rows(
        conn,
        f"""
        SELECT
            platform_object_id AS event_id,
            MIN(local_date) AS event_date,
            MIN(knowledge_time) AS earliest_knowledge_time,
            COUNT(*) AS snapshot_count,
            MIN(event_name) AS event_name,
            MIN(venue_id) AS venue_id,
            MIN(venue_name) AS venue_name,
            MIN(city) AS city,
            MIN(COALESCE(state_code, '')) AS state_code,
            arg_min(COALESCE(segment, ''), knowledge_time) AS segment,
            arg_min(COALESCE(segment_id, ''), knowledge_time) AS segment_id,
            arg_min(COALESCE(genre, ''), knowledge_time) AS genre,
            arg_min(COALESCE(genre_id, ''), knowledge_time) AS genre_id,
            arg_min(COALESCE(subgenre, ''), knowledge_time) AS subgenre,
            arg_min(COALESCE(subgenre_id, ''), knowledge_time) AS subgenre_id,
            arg_min(COALESCE(family, ''), knowledge_time) AS family,
            MIN(latitude) AS latitude,
            MIN(longitude) AS longitude
        FROM events.provider_event_snapshots
        WHERE {where}
        GROUP BY platform_object_id
        """,
        params,
    )


def _window_labels(delta: int) -> list[str]:
    labels = []
    for w in WINDOWS:
        if delta <= w:
            labels.append(f"pm{w}")
    return labels


def competitive_calendar(
    conn,
    *,
    city: str | None = None,
    state_code: str | None = None,
    target_event_id: str | None = None,
    target_date: str | None,
    target_venue_id: str | None = None,
    target_lat: float | None = None,
    target_lon: float | None = None,
    research_cutoff: str | None = None,
) -> dict[str, Any]:
    """Explainable competitive calendar for one proposed show date.

    Aggregates are raw counts (never a score). Rows carry every dimension a
    buyer needs to see why an event appears. The target event is always
    excluded. PIT tri-state is applied when ``research_cutoff`` is supplied.
    """
    out: dict[str, Any] = {
        "status": "UNKNOWN",
        "target": {
            "event_id": target_event_id,
            "date": target_date,
            "city": city,
            "state_code": state_code,
            "venue_id": target_venue_id,
            "lat": target_lat,
            "lon": target_lon,
        },
        "research_cutoff": research_cutoff,
        "pit_mode": "PIT" if research_cutoff else "NON_PIT",
        "windows": {},
        "distance": {},
        "rows": [],
        "known_at_cutoff": [],
        "observed_after_cutoff": [],
        "unknown_knowledge_time": [],
    }
    center = _d(target_date)
    if center is None:
        return out
    cutoff_d = _d(research_cutoff) if research_cutoff else None

    lo = (center - timedelta(days=WINDOWS[-1])).isoformat()
    hi = (center + timedelta(days=WINDOWS[-1])).isoformat()
    candidates = _event_level_candidates(
        conn,
        city=city,
        state_code=state_code,
        target_lat=target_lat,
        target_lon=target_lon,
        lo=lo,
        hi=hi,
    )

    windows: dict[str, dict[str, dict[str, int]]] = {}
    distance: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for w in WINDOWS:
        windows[f"pm{w}"] = {
            BUCKET_KNOWN: {},
            BUCKET_POST: {},
            BUCKET_UNKNOWN: {},
            "total": 0,
        }
    for bucket in (DISTANCE_SAME_VENUE, DISTANCE_SAME_CITY, DISTANCE_WITHIN_5,
                   DISTANCE_WITHIN_10, DISTANCE_WITHIN_25, DISTANCE_WITHIN_50,
                   DISTANCE_BEYOND_50, DISTANCE_UNKNOWN):
        distance[bucket] = 0

    for r in candidates:
        event_id = r.get("event_id")
        if target_event_id and event_id == target_event_id:
            continue
        d = _d(r.get("event_date"))
        if d is None:
            continue
        delta = abs((d - center).days)

        miles = haversine_miles(
            target_lat, target_lon,
            r.get("latitude"), r.get("longitude"),
        )
        same_venue = bool(target_venue_id and r.get("venue_id") and r["venue_id"] == target_venue_id)
        same_city = bool(city and r.get("city") and str(r["city"]).lower() == str(city).lower())
        # Outside the 50-mile box but in a different city: not part of the
        # geographic competition context.
        if (
            miles is not None and miles > _MAX_DISTANCE_MILES
            and not same_city and not same_venue
        ):
            continue
        bucket = distance_bucket(miles, same_venue=same_venue, same_city=same_city)
        if not same_venue and not same_city and miles is None:
            bucket = DISTANCE_UNKNOWN
        distance[bucket] += 1

        if cutoff_d is None:
            knowledge = BUCKET_KNOWN  # NON-PIT current view
        else:
            knowledge = _classify(r.get("earliest_knowledge_time"), cutoff_d)

        windows_here = _window_labels(delta)
        row = {
            "event_id": event_id,
            "event_name": r.get("event_name"),
            "event_date": str(d),
            "date_delta_days": delta,
            "windows": windows_here,
            "segment": r.get("segment") or None,
            "segment_id": r.get("segment_id") or None,
            "genre": r.get("genre") or None,
            "genre_id": r.get("genre_id") or None,
            "subgenre": r.get("subgenre") or None,
            "subgenre_id": r.get("subgenre_id") or None,
            "family": r.get("family") or None,
            "venue_name": r.get("venue_name"),
            "venue_id": r.get("venue_id"),
            "city": r.get("city"),
            "distance_miles": round(miles, 1) if miles is not None else None,
            "distance_bucket": bucket,
            "earliest_knowledge_time": str(r.get("earliest_knowledge_time")) if r.get("earliest_knowledge_time") else None,
            "knowledge_status": knowledge,
            "snapshot_count": r.get("snapshot_count"),
        }
        rows.append(row)
        for w_label in windows_here:
            cell = windows[w_label]
            seg = (r.get("segment") or "Undefined")
            cell[knowledge][seg] = cell[knowledge].get(seg, 0) + 1
            cell["total"] += 1
        if knowledge == BUCKET_KNOWN:
            out["known_at_cutoff"].append(row)
        elif knowledge == BUCKET_POST:
            out["observed_after_cutoff"].append(row)
        else:
            out["unknown_knowledge_time"].append(row)

    def _sort_key(r: dict[str, Any]) -> tuple:
        return (r["date_delta_days"], r.get("distance_miles") if r.get("distance_miles") is not None else 1e9,
                r.get("event_name") or "")

    rows.sort(key=_sort_key)
    out["known_at_cutoff"].sort(key=_sort_key)
    out["observed_after_cutoff"].sort(key=_sort_key)
    out["unknown_knowledge_time"].sort(key=_sort_key)

    for w_label, cell in windows.items():
        for k in (BUCKET_KNOWN, BUCKET_POST, BUCKET_UNKNOWN):
            cell[k] = dict(sorted(cell[k].items()))
    out["windows"] = windows
    out["distance"] = dict(sorted(distance.items()))
    out["rows"] = rows
    out["status"] = "OBSERVED"
    return out


def calendar_for_proposed_show(
    conn,
    *,
    city: str | None = None,
    state_code: str | None = None,
    date: str | None,
    venue_name: str | None = None,
    venue_id: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    research_cutoff: str | None = None,
) -> dict[str, Any]:
    """Competitive calendar for a proposed (not yet existing) show.

    A proposed show has no provider event id, so nothing is excluded; the
    proposed venue/coords still anchor geography.
    """
    return competitive_calendar(
        conn,
        city=city,
        state_code=state_code,
        target_event_id=None,
        target_date=date,
        target_venue_id=venue_id,
        target_lat=lat,
        target_lon=lon,
        research_cutoff=research_cutoff,
    )
