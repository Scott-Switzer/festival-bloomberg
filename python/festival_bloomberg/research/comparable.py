"""Transparent comparable-event retrieval + valuation (no ML).

The champion of `BASELINE_RESEARCH_V1` is the *hierarchical fallback*: a hard
ladder of most-specific historical medians (artist×venue → artist×market →
artist → venue → market → global). This module is the next object up: a
**decomposable weighted distance** over point-in-time-safe candidate events.

Every returned comparable carries the *why* — a per-component distance so a
promoter can see exactly what made two events similar — and the valuation is a
weighted quantile of eligible comp outcomes, never a single point masquerading
as a forecast.

Semantics preserved from the baseline:

- PIT admissibility: a candidate may only contribute if its result was
  published strictly before the target's event start (``publication_time <
  start_date``). A future event can never enter a historical comparable set.
- ``UNKNOWN != 0``: a missing component is a neutral distance, never an
  imputed zero.
- Target populations stay separate (REPORTED_ATTENDANCE vs PAID_TICKETS vs
  TICKET_GROSS). No cross-population pooling.

This module is pure computation over already-frozen rows; it performs no I/O
and no model fitting.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

# Component names, in a stable order for decomposition reporting.
COMPONENTS = ("artist", "venue", "market", "calendar", "price", "shows")

# Default weights (sum to 1.0). Identity dimensions dominate because they are
# the strongest observed structure in BASELINE_RESEARCH_V1; calendar/price are
# secondary. Exposed as a parameter so weight sensitivity can be run later.
DEFAULT_WEIGHTS: dict[str, float] = {
    "artist": 0.30,
    "venue": 0.20,
    "market": 0.15,
    "calendar": 0.15,
    "price": 0.10,
    "shows": 0.10,
}

DEFAULT_K = 10


def _iso10(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def _same_artist(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a.get("artist") or "") == (b.get("artist") or "")


def _same_venue(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a.get("venue") or "") == (b.get("venue") or "")


def _market_of(row: dict[str, Any]) -> str:
    return (row.get("city") or row.get("market") or "").strip().lower()


def _same_market(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ma, mb = _market_of(a), _market_of(b)
    return bool(ma) and ma == mb


def calendar_distance(a: str | None, b: str | None) -> float | None:
    """Circular month-of-year distance in [0,1]. None if either date missing."""
    if not a or not b:
        return None
    try:
        ma = date.fromisoformat(_iso10(a)).month
        mb = date.fromisoformat(_iso10(b)).month
    except ValueError:
        return None
    d = abs(ma - mb)
    d = min(d, 12 - d)
    return d / 6.0


def log_price_distance(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Scaled log-price-band distance in [0,1]. None if either price missing."""
    pa = a.get("price_min")
    pb = b.get("price_min")
    if pa is None or pb is None:
        return None
    try:
        la = math.log1p(float(pa))
        lb = math.log1p(float(pb))
    except (TypeError, ValueError):
        return None
    # A 10x price gap maps to ~1.0; equal prices map to 0.
    return min(1.0, abs(la - lb) / math.log(10.0))


def shows_distance(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    sa, sb = a.get("number_of_shows"), b.get("number_of_shows")
    if sa is None or sb is None:
        return None
    try:
        return min(1.0, abs(float(sa) - float(sb)) / 3.0)
    except (TypeError, ValueError):
        return None


def comparable_distance(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Decomposable distance from ``candidate`` to ``target`` in [0,1].

    Returns ``{overall, components: {...}, missing: [...]}``. Missing
    components use a neutral 0.5 (a documented prior, never zero) and are
    listed so the caller can see the coverage cost of the comparison.
    """
    w = weights or DEFAULT_WEIGHTS
    components: dict[str, float] = {}
    missing: list[str] = []

    components["artist"] = 0.0 if _same_artist(target, candidate) else 1.0
    components["venue"] = 0.0 if _same_venue(target, candidate) else 1.0
    components["market"] = 0.0 if _same_market(target, candidate) else 1.0

    cal = calendar_distance(target.get("start_date"), candidate.get("start_date"))
    components["calendar"] = cal if cal is not None else 0.5
    if cal is None:
        missing.append("calendar")

    price = log_price_distance(target, candidate)
    components["price"] = price if price is not None else 0.5
    if price is None:
        missing.append("price")

    shows = shows_distance(target, candidate)
    components["shows"] = shows if shows is not None else 0.5
    if shows is None:
        missing.append("shows")

    total_weight = sum(w.get(c, 0.0) for c in COMPONENTS) or 1.0
    overall = sum(w.get(c, 0.0) * components[c] for c in COMPONENTS) / total_weight
    return {
        "overall": round(overall, 6),
        "components": {c: round(components[c], 6) for c in COMPONENTS},
        "missing": missing,
    }


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float | None:
    """Weighted quantile of (value, weight) pairs. None on empty input."""
    if not values:
        return None
    pairs = sorted(zip(values, weights), key=lambda p: p[0])
    total = sum(weights)
    if total <= 0:
        # Degenerate: fall back to unweighted median.
        s = sorted(values)
        mid = len(s) // 2
        return (s[mid] + s[len(s) - 1 - mid]) / 2.0
    target_cum = q * total
    cum = 0.0
    for v, wt in pairs:
        cum += wt
        if cum >= target_cum:
            return v
    return pairs[-1][0]


def retrieve_comparables(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    value_fn,
    k: int = DEFAULT_K,
    weights: dict[str, float] | None = None,
    target_engagement_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve top-K comparable events with distance decomposition.

    ``candidates`` is the PIT-admissible history pool (already filtered by the
    caller); ``value_fn`` extracts the target outcome from a candidate row.
    Returns the ordered comps, the distance decomposition, and a valuation.
    """
    scored = []
    for cand in candidates:
        v = value_fn(cand)
        if v is None:
            continue
        if target_engagement_id and cand.get("engagement_id") == target_engagement_id:
            continue
        d = comparable_distance(target, cand, weights=weights)
        scored.append((d["overall"], cand, v, d))

    scored.sort(key=lambda t: t[0])
    top = scored[:k]
    if not top:
        return {"comps": [], "valuation": None, "n_candidates": 0}

    # Distance-weighted: closer comps count more. 1/(1+d) bounds the weight.
    inv = [1.0 / (1.0 + overall) for overall, _c, _v, _ in top]
    values = [v for _o, _c, v, _ in top]
    comps = [
        {
            "engagement_id": cand.get("engagement_id"),
            "artist": cand.get("artist"),
            "venue": cand.get("venue"),
            "market": cand.get("city") or cand.get("market"),
            "start_date": cand.get("start_date"),
            "value": v,
            "distance": overall,
            "components": d["components"],
            "missing": d["missing"],
        }
        for overall, cand, v, d in top
    ]
    valuation = {
        "k": len(top),
        "weighted_median": weighted_quantile(values, inv, 0.5),
        "p25": weighted_quantile(values, inv, 0.25),
        "p75": weighted_quantile(values, inv, 0.75),
        "p10": weighted_quantile(values, inv, 0.10),
        "p90": weighted_quantile(values, inv, 0.90),
        "min": min(values),
        "max": max(values),
        "effective_sample_size": round(sum(inv), 3),
    }
    return {"comps": comps, "valuation": valuation, "n_candidates": len(scored)}


def point_in_time_candidates(
    target: dict[str, Any],
    pool: list[dict[str, Any]],
    *,
    target_engagement_id: str | None = None,
) -> list[dict[str, Any]]:
    """Rows whose result was published strictly before the target's event start.

    Mirrors ``research.features._available_for``: unknown publication time is
    unavailable (conservative), and the target itself is always excluded.
    """
    cutoff = _iso10(target.get("start_date"))
    if not cutoff:
        return []
    out = []
    for row in pool:
        if target_engagement_id and row.get("engagement_id") == target_engagement_id:
            continue
        pub = _iso10(row.get("publication_time"))
        if not pub:
            continue
        if pub < cutoff:
            out.append(row)
    return out
