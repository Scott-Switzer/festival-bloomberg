"""Point-in-time historical attention panels.

Derives PIT features (7d / 30d / 90d / 365d windows, growth, trend,
volatility, spike) from time-indexed daily observations, respecting a hard
cutoff: only observations with observation day < cutoff may enter ANY window.
This is pure computation over already-frozen rows (no I/O, no network).

Critical rule for ListenBrainz-style sources: a row is admissible only if
listened_at < cutoff AND inserted_at < cutoff — late-imported historical
listens must never leak backward.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Callable


def _d(value: Any) -> date | None:
    if value is None:
        return None
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _std(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def daily_series(rows: list[dict[str, Any]], *, value_fn: Callable[[dict[str, Any]], float | None],
                 day_fn: Callable[[dict[str, Any]], Any],
                 inserted_fn: Callable[[dict[str, Any]], Any] | None = None,
                 cutoff: str | None = None) -> dict[date, float]:
    """Aggregate rows into a daily sum series, strictly before the cutoff.

    ``inserted_fn`` (when given) enforces the listened_at < cutoff AND
    inserted_at < cutoff rule: a row whose inserted_at is not < cutoff is
    excluded even if its listened_at day is.
    """
    cutoff_d = _d(cutoff)
    daily: dict[date, float] = defaultdict(float)
    for row in rows:
        day = _d(day_fn(row))
        if day is None:
            continue
        if cutoff_d is not None and not (day < cutoff_d):
            continue
        if inserted_fn is not None:
            ins = _d(inserted_fn(row))
            if cutoff_d is not None and (ins is None or not (ins < cutoff_d)):
                continue
        v = value_fn(row)
        if v is None:
            continue
        daily[day] += v
    return dict(daily)


def pit_features(
    daily: dict[date, float],
    *,
    cutoff: str,
    windows: tuple[int, ...] = (7, 30, 90, 365),
) -> dict[str, Any]:
    """PIT window sums + growth/trend/volatility/spike from a daily series.

    Windows are trailing (cutoff-1, cutoff-1-window]. Only days strictly
    before the cutoff are eligible.
    """
    cutoff_d = _d(cutoff)
    if cutoff_d is None:
        return {"status": "NO_CUTOFF"}
    out: dict[str, Any] = {"status": "OK", "cutoff": cutoff}
    for w in windows:
        lo = cutoff_d - timedelta(days=w)
        vals = [v for d, v in sorted(daily.items()) if lo <= d < cutoff_d]
        out[f"{w}d"] = round(sum(vals), 4) if vals else None
    # 30d growth: (most recent 30d) / (30d ending 60d before cutoff)
    recent = [v for d, v in sorted(daily.items()) if cutoff_d - timedelta(days=30) <= d < cutoff_d]
    prior = [v for d, v in sorted(daily.items()) if cutoff_d - timedelta(days=60) <= d < cutoff_d - timedelta(days=30)]
    s_recent, s_prior = sum(recent), sum(prior)
    if s_prior and s_recent is not None:
        out["growth_30d"] = round(s_recent / s_prior - 1.0, 4)
    else:
        out["growth_30d"] = None
    # 90d trend: (last 30d) vs (first 30d of the 90d window)
    tail = [v for d, v in sorted(daily.items()) if cutoff_d - timedelta(days=30) <= d < cutoff_d]
    head = [v for d, v in sorted(daily.items()) if cutoff_d - timedelta(days=90) <= d < cutoff_d - timedelta(days=60)]
    s_tail, s_head = sum(tail), sum(head)
    if s_head and s_tail is not None:
        out["trend_90d"] = round(s_tail / s_head - 1.0, 4)
    else:
        out["trend_90d"] = None
    # volatility: cv of the trailing 90d daily values
    vals90 = [v for d, v in sorted(daily.items()) if cutoff_d - timedelta(days=90) <= d < cutoff_d]
    m90, sd90 = _mean(vals90), _std(vals90)
    out["volatility_90d"] = round(sd90 / m90, 4) if (m90 and sd90 is not None) else None
    # spike: max single day in trailing 30d / mean of trailing 90d
    m90b = _mean(vals90)
    if vals90 and m90b:
        out["spike_30d"] = round(max(v for d, v in sorted(daily.items())
                                      if cutoff_d - timedelta(days=30) <= d < cutoff_d) / m90b, 4)
    else:
        out["spike_30d"] = None
    out["days_observed"] = len([d for d in daily if d < cutoff_d])
    return out
