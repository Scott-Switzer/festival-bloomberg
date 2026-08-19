"""Local event competition features (raw counts, no opaque score).

For each event: same-market music events on the same day, +-3 / +-7 / +-14
days, plus large-event and festival counts. Raw counts only — competition is
never inferred from unrelated entertainment without a semantic definition.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

WINDOWS = (0, 3, 7, 14)


def _rows(conn, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _events_in_window(conn, *, market: str | None, center: str | None, days: int) -> int:
    """Count distinct Ticketmaster music events within +-days of ``center``."""
    if not market or not center:
        return 0
    try:
        c = date.fromisoformat(str(center)[:10])
    except ValueError:
        return 0
    lo = (c - timedelta(days=days)).isoformat()
    hi = (c + timedelta(days=days)).isoformat()
    return int(_rows(
        conn,
        """
        SELECT COUNT(DISTINCT platform_object_id) AS n
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster' AND LOWER(COALESCE(city, '')) = ?
          AND COALESCE(local_date, '') BETWEEN ? AND ?
        """,
        [market.lower(), lo, hi],
    )[0]["n"])


def competition_for_event(conn, *, event_date: str | None, market: str | None) -> dict[str, Any]:
    """Competition counts for one event (same market, +-14 days)."""
    out: dict[str, Any] = {
        "market": market, "event_date": event_date,
        "windows": {}, "large_events": None, "festival_events": None,
    }
    if not event_date or not market:
        out["status"] = "UNKNOWN"
        return out
    for days in WINDOWS:
        out["windows"][f"pm{days}"] = _events_in_window(
            conn, market=market, center=event_date, days=days)
    out["status"] = "OBSERVED"
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
    return {
        "market": market,
        "status": "OBSERVED",
        "event_count": len(rows),
        "distinct_dates": len(dates),
        "max_events_same_day": same_day,
        "busiest_date": dates[-1] if by_date else None,
    }
