"""Local event competition features (raw counts, no opaque score).

For each event: same-market music events on the same day, +-3 / +-7 / +-14
days. Raw counts only — competition is never inferred from unrelated
entertainment without a semantic definition.

PIT contract (research-safe):

* the target event is always excluded (``platform_object_id != target_event_id``);
* a competing event must be knowable before the research cutoff. Knowability is
  established from the event's ``knowledge_time``: a competitor is counted in
  ``known`` only if ``knowledge_time < cutoff``;
* competitors whose knowability cannot be established (NULL knowledge_time) or
  that became known only at/after the cutoff are counted separately in
  ``unknown_knowability`` — NEVER silently as zero competitors;
* ``coverage`` = known / (known + unknown_knowability), 1.0 when no competing
  events are observed at all.

Without a ``research_cutoff`` the result is a NON-PIT current-warehouse view
(marked ``NON_PIT``), not historical evidence.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

WINDOWS = (0, 3, 7, 14)


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


def _knowable_before(kt: Any, cutoff: date) -> bool | None:
    """True if ``kt`` is strictly before cutoff, False if at/after it, None if
    knowability cannot be established (NULL or unparseable)."""
    if kt is None:
        return None
    try:
        ktd = kt.date() if hasattr(kt, "date") else date.fromisoformat(str(kt)[:10])
    except (ValueError, TypeError):
        return None
    return ktd < cutoff


def competition_for_event(
    conn,
    *,
    target_event_id: str | None,
    event_date: str | None,
    market: str | None,
    research_cutoff: str | None = None,
) -> dict[str, Any]:
    """PIT-safe competition counts for one event (same market, +-14 days).

    ``target_event_id`` is required so the target can be excluded from its own
    competitor set. ``research_cutoff`` enables PIT knowability gating; without
    it the result is a NON-PIT current view.
    """
    out: dict[str, Any] = {
        "market": market,
        "event_date": event_date,
        "target_event_id": target_event_id,
        "research_cutoff": research_cutoff,
        "windows": {
            f"pm{w}": {"known": 0, "unknown_knowability": 0, "coverage": None}
            for w in WINDOWS
        },
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
    rows = _rows(
        conn,
        """
        SELECT platform_object_id, local_date, knowledge_time
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
          AND LOWER(COALESCE(city, '')) = ?
          AND platform_object_id != ?
          AND COALESCE(local_date, '') BETWEEN ? AND ?
        """,
        [market.lower(), target_event_id, lo, hi],
    )

    for r in rows:
        d = _d(r.get("local_date"))
        if d is None:
            continue
        delta = abs((d - center).days)
        if cutoff_d is None:
            known, unknown = 1, 0
        else:
            kb = _knowable_before(r.get("knowledge_time"), cutoff_d)
            # False (known only at/after cutoff) and None (unknowable) are both
            # "cannot be confirmed PIT-known", never a silent zero competitor.
            known, unknown = (1, 0) if kb is True else (0, 1)
        for w in WINDOWS:
            if delta <= w:
                out["windows"][f"pm{w}"]["known"] += known
                out["windows"][f"pm{w}"]["unknown_knowability"] += unknown

    for w in WINDOWS:
        cell = out["windows"][f"pm{w}"]
        total = cell["known"] + cell["unknown_knowability"]
        cell["coverage"] = round(cell["known"] / total, 4) if total else 1.0

    out["status"] = "NON_PIT" if cutoff_d is None else "OBSERVED"
    return out


def market_competition_profile(conn, *, market: str) -> dict[str, Any]:
    """Aggregate competition texture for a market (event-level density)."""
    rows = _rows(
        conn,
        """
        SELECT local_date, platform_object_id, city
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster' AND LOWER(COALESCE(city, '')) = ?
        ORDER BY local_date
        """,
        [market.lower()],
    )
    if not rows:
        return {"market": market, "status": "UNKNOWN", "event_count": 0}
    by_date: dict[str, int] = {}
    for r in rows:
        d = str(r["local_date"] or "")[:10]
        if d:
            by_date[d] = by_date.get(d, 0) + 1
    dates = sorted(by_date)
    same_day = max(by_date.values()) if by_date else 0
    # busiest_date is the argmax daily count, not the chronologically latest date.
    busiest_date = max(by_date.items(), key=lambda kv: kv[1])[0] if by_date else None
    return {
        "market": market,
        "status": "OBSERVED",
        "event_count": len(rows),
        "distinct_dates": len(dates),
        "max_events_same_day": same_day,
        "busiest_date": busiest_date,
    }
