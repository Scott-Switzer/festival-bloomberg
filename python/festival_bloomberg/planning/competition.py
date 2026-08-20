"""Local event competition features (raw counts, no opaque score).

``events.provider_event_snapshots`` is a SNAPSHOT table: one Ticketmaster event
may have many rows. Competition is therefore computed over an EVENT-LEVEL
relation (one row per ``platform_object_id``), so one event never counts as
multiple competitors.

For each event: same-market music events on the same day, +-3 / +-7 / +-14
days. Raw counts only — competition is never inferred from unrelated
entertainment without a semantic definition.

PIT contract (research-safe):

* the target event is always excluded (``platform_object_id != target_event_id``);
* a competing event's knowability is its EARLIEST ``knowledge_time`` across its
  snapshots (the first time it was observable). It is classified into exactly
  one of three buckets relative to the research cutoff:
    - ``known_before_cutoff``    earliest_knowledge_time < cutoff  (this IS the
      competition feature value);
    - ``observed_post_cutoff``   earliest_knowledge_time >= cutoff (NOT visible
      at the decision cutoff, but NOT missing data either);
    - ``unknown_knowledge_time`` no valid knowledge_time (genuine missingness).
* ``knowledge_time_coverage`` = (known + observed_post) / all candidate
  competitors, and ``unknown_rate`` = unknown / all — reported separately so
  post-cutoff events are never mistaken for missing data and missing data is
  never mistaken for zero competition.

Without a ``research_cutoff`` the result is a NON-PIT current-warehouse view
(marked ``NON_PIT``), not historical evidence.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

WINDOWS = (0, 3, 7, 14)

BUCKET_KNOWN = "known_before_cutoff"
BUCKET_POST = "observed_post_cutoff"
BUCKET_UNKNOWN = "unknown_knowledge_time"


def _rows(conn, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _d(value: Any) -> date | None:
    if value is None:
        return None
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _classify(kt: Any, cutoff: date) -> str:
    """Tri-state knowability of one event's earliest knowledge_time vs cutoff."""
    d = _d(kt)
    if d is None:
        return BUCKET_UNKNOWN
    return BUCKET_KNOWN if d < cutoff else BUCKET_POST


def _event_level(conn, *, market: str, target_event_id: str, lo: str, hi: str) -> list[dict[str, Any]]:
    """One row per distinct Ticketmaster event in-market, in the date window,
    with its earliest knowledge_time and snapshot count. The target event is
    excluded."""
    return _rows(
        conn,
        """
        SELECT
            platform_object_id AS event_id,
            MIN(local_date) AS event_date,
            MIN(knowledge_time) AS earliest_knowledge_time,
            COUNT(*) AS snapshot_count
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
          AND LOWER(COALESCE(city, '')) = ?
          AND platform_object_id != ?
        GROUP BY platform_object_id
        HAVING MIN(local_date) IS NOT NULL
           AND MIN(local_date) BETWEEN ? AND ?
        """,
        [market.lower(), target_event_id, lo, hi],
    )


def competition_for_event(
    conn,
    *,
    target_event_id: str | None,
    event_date: str | None,
    market: str | None,
    research_cutoff: str | None = None,
) -> dict[str, Any]:
    """PIT-safe, event-deduped competition counts for one event (+-14 days).

    ``target_event_id`` is required so the target can be excluded. With
    ``research_cutoff`` the counts are tri-state PIT; without it the result is
    a NON-PIT current view (every distinct event counts as known).
    """
    empty_window = {
        BUCKET_KNOWN: 0, BUCKET_POST: 0, BUCKET_UNKNOWN: 0,
        "knowledge_time_coverage": None, "unknown_rate": None,
    }
    out: dict[str, Any] = {
        "market": market,
        "event_date": event_date,
        "target_event_id": target_event_id,
        "research_cutoff": research_cutoff,
        "windows": {f"pm{w}": dict(empty_window) for w in WINDOWS},
        "status": "UNKNOWN",
    }
    if not event_date or not market or not target_event_id:
        return out

    center = _d(event_date)
    if center is None:
        return out
    cutoff_d = _d(research_cutoff) if research_cutoff else None

    lo = (center - timedelta(days=WINDOWS[-1])).isoformat()
    hi = (center + timedelta(days=WINDOWS[-1])).isoformat()
    rows = _event_level(conn, market=market, target_event_id=target_event_id, lo=lo, hi=hi)

    for r in rows:
        d = _d(r.get("event_date"))
        if d is None:
            continue
        delta = abs((d - center).days)
        if cutoff_d is None:
            bucket = BUCKET_KNOWN  # NON-PIT: every distinct event is "present"
        else:
            bucket = _classify(r.get("earliest_knowledge_time"), cutoff_d)
        for w in WINDOWS:
            if delta <= w:
                out["windows"][f"pm{w}"][bucket] += 1

    for w in WINDOWS:
        cell = out["windows"][f"pm{w}"]
        total = cell[BUCKET_KNOWN] + cell[BUCKET_POST] + cell[BUCKET_UNKNOWN]
        cell["knowledge_time_coverage"] = (
            round((cell[BUCKET_KNOWN] + cell[BUCKET_POST]) / total, 4) if total else 1.0
        )
        cell["unknown_rate"] = round(cell[BUCKET_UNKNOWN] / total, 4) if total else 0.0

    out["status"] = "NON_PIT" if cutoff_d is None else "OBSERVED"
    return out


def market_competition_profile(conn, *, market: str) -> dict[str, Any]:
    """Aggregate competition texture for a market (event-level density)."""
    rows = _rows(
        conn,
        """
        SELECT
            platform_object_id AS event_id,
            MIN(local_date) AS event_date,
            COUNT(*) AS snapshot_count
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster' AND LOWER(COALESCE(city, '')) = ?
        GROUP BY platform_object_id
        """,
        [market.lower()],
    )
    if not rows:
        return {"market": market, "status": "UNKNOWN", "event_count": 0}
    by_date: dict[str, int] = {}
    for r in rows:
        d = str(r["event_date"] or "")[:10]
        if d:
            # distinct events per day (deduped by GROUP BY above)
            by_date[d] = by_date.get(d, 0) + 1
    dates = sorted(by_date)
    same_day = max(by_date.values()) if by_date else 0
    # busiest_date is the argmax daily distinct-event count, not the latest date.
    busiest_date = max(by_date.items(), key=lambda kv: kv[1])[0] if by_date else None
    return {
        "market": market,
        "status": "OBSERVED",
        "event_count": len(rows),  # distinct events
        "distinct_dates": len(dates),
        "max_events_same_day": same_day,
        "busiest_date": busiest_date,
    }
