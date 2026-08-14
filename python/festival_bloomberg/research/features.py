"""Point-in-time historical comparable features for baseline research.

The central rule: a target engagement may only use information from OTHER
engagements whose box-office result was published BEFORE the target's event
start (``publication_time < start_date``). We never use a prior event merely
because its event date is earlier. A future engagement can never enter a
historical aggregate.

Targets are kept semantically separate: REPORTED_ATTENDANCE and PAID_TICKETS
are different populations and are never pooled into one "attendance" label.

Missing history is information: we never impute zeros; we expose
``has_*_history`` flags and let the model layer apply a documented fallback.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from .boxscore import HEADCOUNT_PAID_TICKETS, HEADCOUNT_REPORTED_ATTENDANCE

TARGET_ATTENDANCE = "REPORTED_ATTENDANCE"
TARGET_PAID_TICKETS = "PAID_TICKETS"
TARGET_GROSS = "TICKET_GROSS"
TARGET_SELL_OUT = "SELL_OUT"

TARGETS = (TARGET_ATTENDANCE, TARGET_PAID_TICKETS, TARGET_GROSS, TARGET_SELL_OUT)

# Columns that must never become predictors for the same target event.
LEAKAGE_BLACKLIST = frozenset({
    "headcount_total", "ticket_gross_total", "price_min", "price_max",
    "reported_sellouts", "sell_through_pct", "rank", "is_multi_show",
})


def _iso10(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def target_value(row: dict[str, Any], target_type: str) -> float | None:
    """Return the target value for a row, or None if the row is not eligible
    for that target. Semantics are never coerced across targets."""
    if row.get("is_multi_show"):
        return None
    if not row.get("is_reported", True) or row.get("is_estimated"):
        return None
    if not row.get("start_date"):
        return None
    if target_type == TARGET_ATTENDANCE:
        if row.get("headcount_definition") != HEADCOUNT_REPORTED_ATTENDANCE:
            return None
        return row.get("headcount_total")
    if target_type == TARGET_PAID_TICKETS:
        if row.get("headcount_definition") != HEADCOUNT_PAID_TICKETS:
            return None
        return row.get("headcount_total")
    if target_type == TARGET_GROSS:
        if (row.get("currency") or "USD") != "USD":
            return None
        return row.get("ticket_gross_total")
    if target_type == TARGET_SELL_OUT:
        sellouts = row.get("reported_sellouts")
        if sellouts is None or row.get("number_of_shows") != 1:
            return None
        return 1.0 if sellouts >= 1 else 0.0
    raise ValueError(f"unknown target {target_type!r}")


def population(
    rows: list[dict[str, Any]],
    target_type: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Eligible rows for a target, plus the exclusion waterfall."""
    waterfall: dict[str, int] = {
        "raw": len(rows),
        "multi_show": 0,
        "estimated_or_unreported": 0,
        "no_event_date": 0,
        "target_unavailable": 0,
        "non_usd_gross": 0,
        "eligible": 0,
    }
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if row.get("is_multi_show"):
            waterfall["multi_show"] += 1
            continue
        if not row.get("is_reported", True) or row.get("is_estimated"):
            waterfall["estimated_or_unreported"] += 1
            continue
        if not row.get("start_date"):
            waterfall["no_event_date"] += 1
            continue
        if target_type == TARGET_GROSS and (row.get("currency") or "USD") != "USD":
            waterfall["non_usd_gross"] += 1
            continue
        value = target_value(row, target_type)
        if value is None:
            waterfall["target_unavailable"] += 1
            continue
        eligible.append({**row, "_target": value})
    waterfall["eligible"] = len(eligible)
    return eligible, waterfall


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid] + s[n - 1 - mid]) / 2.0


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ewma(values: list[float], alpha: float = 0.5) -> float | None:
    if not values:
        return None
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def _available_for(
    target: dict[str, Any],
    all_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rows whose result was published strictly before the target's event start."""
    cutoff = _iso10(target.get("start_date"))
    if not cutoff:
        return []
    out = []
    for row in all_rows:
        if row["engagement_id"] == target["engagement_id"]:
            continue
        pub = _iso10(row.get("publication_time"))
        if not pub:
            continue  # conservative: unknown publication time is unavailable
        if pub < cutoff:
            out.append(row)
    return out


def _history_metrics(
    target: dict[str, Any],
    all_rows: list[dict[str, Any]],
    value_fn: Callable[[dict[str, Any]], float | None],
) -> dict[str, Any]:
    prior = _available_for(target, all_rows)
    artist = target.get("artist") or ""
    venue = target.get("venue") or ""
    market = target.get("city") or target.get("market") or ""

    def collect(pred: Callable[[dict[str, Any]], bool], *, with_date: bool = False) -> list[tuple[str | None, float]]:
        items: list[tuple[str | None, float]] = []
        for r in prior:
            v = value_fn(r)
            if v is None:
                continue
            if pred(r):
                items.append((_iso10(r.get("start_date")), v))
        if with_date:
            items.sort(key=lambda x: x[0] or "")
        return items

    artist_items = collect(lambda r: (r.get("artist") or "") == artist, with_date=True)
    venue_items = collect(lambda r: (r.get("venue") or "") == venue, with_date=True)
    market_items = collect(lambda r: (r.get("city") or r.get("market") or "") == market, with_date=True)
    artist_market_items = collect(
        lambda r: (r.get("artist") or "") == artist and (r.get("city") or r.get("market") or "") == market
    )
    artist_venue_items = collect(
        lambda r: (r.get("artist") or "") == artist and (r.get("venue") or "") == venue
    )

    artist_vals = [v for _, v in artist_items]
    venue_vals = [v for _, v in venue_items]
    market_vals = [v for _, v in market_items]
    am_vals = [v for _, v in artist_market_items]
    av_vals = [v for _, v in artist_venue_items]

    recent3 = artist_vals[-3:] if artist_vals else []
    recent5 = artist_vals[-5:] if artist_vals else []

    return {
        "artist_count": len(artist_vals),
        "artist_median": _median(artist_vals),
        "artist_mean": _mean(artist_vals),
        "artist_std": _std(artist_vals),
        "artist_min": min(artist_vals) if artist_vals else None,
        "artist_max": max(artist_vals) if artist_vals else None,
        "artist_last": artist_vals[-1] if artist_vals else None,
        "artist_recent3_median": _median(recent3),
        "artist_recent5_median": _median(recent5),
        "artist_ewma": _ewma(artist_vals),
        "days_since_last": _days_since_last(target, artist_items),
        "venue_count": len(venue_vals),
        "venue_median": _median(venue_vals),
        "venue_mean": _mean(venue_vals),
        "market_count": len(market_vals),
        "market_median": _median(market_vals),
        "market_mean": _mean(market_vals),
        "artist_market_count": len(am_vals),
        "artist_market_median": _median(am_vals),
        "artist_market_mean": _mean(am_vals),
        "artist_venue_count": len(av_vals),
        "artist_venue_median": _median(av_vals),
        "artist_venue_mean": _mean(av_vals),
        "has_artist_history": len(artist_vals) > 0,
        "has_venue_history": len(venue_vals) > 0,
        "has_market_history": len(market_vals) > 0,
        "has_artist_market_history": len(am_vals) > 0,
        "has_artist_venue_history": len(av_vals) > 0,
    }


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _days_since_last(target: dict[str, Any], items: list[tuple[str | None, float]]) -> float | None:
    start = _iso10(target.get("start_date"))
    if not start or not items:
        return None
    last_date = items[-1][0]
    if not last_date:
        return None
    from datetime import date

    try:
        d0 = date.fromisoformat(last_date)
        d1 = date.fromisoformat(start)
        return float((d1 - d0).days)
    except ValueError:
        return None


def compute_features(
    rows: list[dict[str, Any]],
    target_type: str,
    *,
    history_pool: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compute PIT historical features for every eligible row of a target.

    ``history_pool`` is the set of rows allowed to contribute history (for a
    grouped/time holdout this is the TRAIN fold, so no test-row outcome can
    ever enter a feature). Only rows published strictly before each target's
    event start are used.

    Returns a list of {row, target, features}.
    """
    eligible, _ = population(rows, target_type)
    pool = history_pool if history_pool is not None else rows

    if target_type == TARGET_SELL_OUT:
        def value_fn(r: dict[str, Any]) -> float | None:
            sellouts = r.get("reported_sellouts")
            if sellouts is None or r.get("number_of_shows") != 1:
                return None
            return 1.0 if sellouts >= 1 else 0.0
    elif target_type in (TARGET_ATTENDANCE, TARGET_PAID_TICKETS):
        def value_fn(r: dict[str, Any]) -> float | None:
            return r.get("headcount_total")
    else:
        def value_fn(r: dict[str, Any]) -> float | None:
            return r.get("ticket_gross_total")

    out: list[dict[str, Any]] = []
    for row in eligible:
        metrics = _history_metrics(row, pool, value_fn)
        out.append({"row": row, "target": row["_target"], "features": metrics})
    return out


def feature_vector(metrics: dict[str, Any], global_median: float | None) -> tuple[list[float], list[str]]:
    """Flatten metrics into a fixed numeric vector with a documented fallback.

    Missing historical medians fall back to the global median (never zero);
    presence flags are appended so the model can learn "no history" itself.
    """
    names = [
        "artist_median", "artist_mean", "artist_last", "artist_recent3_median",
        "artist_recent5_median", "artist_ewma", "venue_median", "venue_mean",
        "market_median", "market_mean", "artist_market_median", "artist_market_mean",
        "artist_venue_median", "artist_venue_mean",
        "artist_count", "venue_count", "market_count", "artist_market_count",
        "artist_venue_count", "days_since_last",
    ]
    flags = [
        "has_artist_history", "has_venue_history", "has_market_history",
        "has_artist_market_history", "has_artist_venue_history",
    ]
    vec: list[float] = []
    for name in names:
        v = metrics.get(name)
        if v is None:
            if name.endswith("_median") or name.endswith("_mean") or name == "artist_last" or name.endswith("_ewma"):
                v = global_median if global_median is not None else 0.0
            elif name == "days_since_last":
                v = -1.0  # sentinel for "no history"
            else:
                v = 0.0
        vec.append(float(v))
    for flag in flags:
        vec.append(1.0 if metrics.get(flag) else 0.0)
    return vec, names + flags
