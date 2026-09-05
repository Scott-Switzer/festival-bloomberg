"""Lifecycle-aware collection cadence for ticket-market pairs.

Suggested policy (global budget overrides):
  >120d       weekly
  120–60d     2x/week
  60–30d      daily
  30–14d      daily
  14–7d       2x/day
  7–3d        3x/day
  3–1d        4x/day
  show day    4–6x/day
  post-show   stop

Implemented measured policy for V2 (what we can sustain):
  same buckets, but intervals are advisory for due selection;
  hard budget + provider limits always win.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

# cadence_label -> minimum hours between successful observations
CADENCE_HOURS = {
    "weekly": 168,
    "2x_week": 84,
    "daily": 24,
    "2x_day": 12,
    "3x_day": 8,
    "4x_day": 6,
    "show_day": 4,
    "stopped": 10**9,
}


def lifecycle_bucket(event_date: str | date | None, *, today: date | None = None) -> str:
    if not event_date:
        return ">120"
    try:
        d = event_date if isinstance(event_date, date) else date.fromisoformat(str(event_date)[:10])
    except (ValueError, TypeError):
        return ">120"
    t = today or datetime.now(timezone.utc).date()
    days = (d - t).days
    if days < 0:
        return "past"
    if days < 1:
        return "show_day"
    if days < 3:
        return "1-3"
    if days < 7:
        return "<7"
    if days < 14:
        return "7-14"
    if days < 30:
        return "14-30"
    if days < 60:
        return "30-60"
    if days < 120:
        return "60-120"
    return ">120"


def cadence_for_bucket(bucket: str) -> str:
    return {
        "past": "stopped",
        "show_day": "show_day",
        "1-3": "4x_day",
        "<7": "3x_day",
        "7-14": "2x_day",
        "14-30": "daily",
        "30-60": "daily",
        "60-120": "2x_week",
        ">120": "weekly",
    }.get(bucket, "weekly")


def next_due_after(now: datetime, cadence: str) -> datetime:
    hours = CADENCE_HOURS.get(cadence, 24)
    return now + timedelta(hours=hours)


def prioritize_due_pairs(
    pairs: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Deterministic priority under budget: nearest → weakest depth → stale.

    No predicted demand.
    """
    now = now or datetime.now(timezone.utc)

    def sort_key(p: dict[str, Any]) -> tuple:
        ed = p.get("event_date")
        try:
            days = (date.fromisoformat(str(ed)[:10]) - now.date()).days
        except Exception:  # noqa: BLE001
            days = 9999
        obs = int(p.get("observation_count") or 0)
        last = p.get("last_succeeded_at") or p.get("last_attempted_at") or ""
        # nearer first, then fewer observations, then older last success
        return (days if days >= 0 else 10_000 + abs(days), obs, str(last), p.get("event_key") or "", p.get("marketplace") or "")

    due = []
    for p in pairs:
        if (p.get("cadence_label") or "") == "stopped":
            continue
        if p.get("lifecycle_bucket") == "past":
            continue
        next_due = p.get("next_due_at")
        if next_due:
            try:
                nd = datetime.fromisoformat(str(next_due).replace("Z", "+00:00"))
                if nd.tzinfo is None:
                    nd = nd.replace(tzinfo=timezone.utc)
                if nd > now:
                    continue
            except Exception:  # noqa: BLE001
                pass
        due.append(p)
    due.sort(key=sort_key)
    if limit is not None:
        return due[:limit]
    return due
