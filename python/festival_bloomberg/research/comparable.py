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
- ``UNKNOWN != 0``: a missing component is NOT a neutral "moderately similar"
  distance. It is excluded from the observed-distance numerator, reported via
  a ``coverage_score``, and charged a separate, documented missingness penalty
  on the ranking distance.
- Target populations stay separate (REPORTED_ATTENDANCE vs PAID_TICKETS vs
  TICKET_GROSS vs SELL_OUT). No cross-population pooling.
- Leakage: fields in ``features.LEAKAGE_BLACKLIST`` may NEVER feed the
  same-target comparable fingerprint. ``price_min``/``price_max`` and
  ``number_of_shows`` in the frozen box-office corpus are outcome-record
  fields, not pre-event observations, so they are excluded here.

This module is pure computation over already-frozen rows; it performs no I/O
and no model fitting.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from .features import LEAKAGE_BLACKLIST

# Component names, in a stable order for decomposition reporting. Identity
# dimensions are categorical (0/1); calendar is continuous. Only these four are
# leakage-safe given the frozen corpus: price/shows live in the published
# box-office record and have no pre-event provenance.
COMPONENTS = ("artist", "venue", "market", "calendar")

# The raw row fields that feed the fingerprint. This is the canonical
# admissibility contract: it must stay disjoint from LEAKAGE_BLACKLIST.
FINGERPRINT_SOURCE_FIELDS = frozenset({
    "artist", "venue", "city", "market", "start_date",
})

# Hierarchical candidate strata, most-specific first. This mirrors the
# champion's identity ladder and preserves the structure the baseline found.
STRATA = (
    "SAME_ARTIST_VENUE",
    "SAME_ARTIST_MARKET",
    "SAME_ARTIST",
    "SAME_VENUE",
    "SAME_MARKET",
    "BROAD_FALLBACK",
)

# Target-specific weights (economically pre-specified, NOT tuned on test
# holds). Identity dominates everywhere; gross weights venue a little higher
# (venue scale is a first-order driver of gross, not just attendance).
DEFAULT_WEIGHTS: dict[str, float] = {
    "artist": 0.30, "venue": 0.30, "market": 0.25, "calendar": 0.15,
}
TARGET_WEIGHTS: dict[str, dict[str, float]] = {
    "REPORTED_ATTENDANCE": {"artist": 0.30, "venue": 0.30, "market": 0.25, "calendar": 0.15},
    "PAID_TICKETS": {"artist": 0.30, "venue": 0.30, "market": 0.25, "calendar": 0.15},
    "TICKET_GROSS": {"artist": 0.25, "venue": 0.35, "market": 0.25, "calendar": 0.15},
    "SELL_OUT": {"artist": 0.25, "venue": 0.30, "market": 0.25, "calendar": 0.20},
}

DEFAULT_K = 10
DEFAULT_MIN_STRATUM_SIZE = 3
DEFAULT_MIN_COVERAGE = 0.25
DEFAULT_MISSINGNESS_PENALTY = 1.0


def assert_admissibility_contract() -> None:
    """Fail loudly if any leakage-blacklisted field feeds the fingerprint."""
    overlap = FINGERPRINT_SOURCE_FIELDS & set(LEAKAGE_BLACKLIST)
    if overlap:
        raise ValueError(
            f"comparable fingerprint uses leakage-blacklisted fields: {sorted(overlap)}"
        )


def weights_for_target(target_type: str | None) -> dict[str, float]:
    return dict(TARGET_WEIGHTS.get(target_type, DEFAULT_WEIGHTS))


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
    return bool(ma) and bool(mb) and ma == mb


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


def _component_distances(
    target: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, float], dict[str, bool]]:
    """Per-component distance + observed flag.

    ``observed`` is False when the field is absent from either side, so that
    missing data is charged as coverage cost rather than imputed similarity.
    """
    distances: dict[str, float] = {}
    observed: dict[str, bool] = {}

    ta, ca = target.get("artist"), candidate.get("artist")
    observed["artist"] = bool(ta) and bool(ca)
    distances["artist"] = 0.0 if (observed["artist"] and ta == ca) else 1.0

    tv, cv = target.get("venue"), candidate.get("venue")
    observed["venue"] = bool(tv) and bool(cv)
    distances["venue"] = 0.0 if (observed["venue"] and tv == cv) else 1.0

    tm, cm = _market_of(target), _market_of(candidate)
    observed["market"] = bool(tm) and bool(cm)
    distances["market"] = 0.0 if (observed["market"] and tm == cm) else 1.0

    cal = calendar_distance(target.get("start_date"), candidate.get("start_date"))
    observed["calendar"] = cal is not None
    distances["calendar"] = cal if cal is not None else 1.0

    return distances, observed


def comparable_distance(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
    missingness_penalty: float = DEFAULT_MISSINGNESS_PENALTY,
) -> dict[str, Any]:
    """Decomposable distance from ``candidate`` to ``target``.

    Returns:

    - ``observed_distance`` — weighted distance across dimensions where BOTH
      sides have admissible values (never penalized for missingness).
    - ``coverage_score`` — observed weight / total weight in [0,1].
    - ``ranking_distance`` — observed_distance + penalty * missing fraction;
      this is what ordering uses.
    - ``components`` / ``observed`` / ``missing`` for full decomposition.
    """
    w = weights or DEFAULT_WEIGHTS
    distances, observed = _component_distances(target, candidate)

    total_weight = sum(w.get(c, 0.0) for c in COMPONENTS) or 1.0
    observed_weight = sum(w.get(c, 0.0) for c in COMPONENTS if observed.get(c))
    observed_distance = (
        sum(w.get(c, 0.0) * distances[c] for c in COMPONENTS if observed.get(c))
        / observed_weight
        if observed_weight > 0
        else 1.0
    )
    coverage = observed_weight / total_weight if total_weight > 0 else 0.0
    missing_frac = 1.0 - coverage
    ranking_distance = observed_distance + missingness_penalty * missing_frac
    # Ranking distance is bounded to [0, 1 + penalty] only in theory; clamp the
    # penalty contribution so a fully-missing row tops out at 1 + penalty.
    ranking_distance = min(ranking_distance, 1.0 + missingness_penalty)

    missing = [c for c in COMPONENTS if not observed.get(c)]
    return {
        "overall": round(observed_distance, 6),  # legacy alias: observed distance
        "observed_distance": round(observed_distance, 6),
        "coverage_score": round(coverage, 6),
        "ranking_distance": round(ranking_distance, 6),
        "missingness_penalty": missingness_penalty,
        "components": {c: round(distances[c], 6) for c in COMPONENTS},
        "observed": {c: bool(observed.get(c)) for c in COMPONENTS},
        "missing": missing,
    }


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float | None:
    """Weighted quantile of (value, weight) pairs. None on empty input."""
    if not values:
        return None
    pairs = sorted(zip(values, weights), key=lambda p: p[0])
    total = sum(weights)
    if total <= 0:
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


def _valuation(top: list[tuple[float, dict[str, Any], float, dict[str, Any]]]) -> dict[str, Any] | None:
    if not top:
        return None
    inv = [1.0 / (1.0 + ranking) for ranking, _c, _v, _d in top]
    values = [v for _o, _c, v, _d in top]
    return {
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


def _comp_payload(cand: dict[str, Any], value: float, d: dict[str, Any]) -> dict[str, Any]:
    return {
        "engagement_id": cand.get("engagement_id"),
        "artist": cand.get("artist"),
        "venue": cand.get("venue"),
        "market": cand.get("city") or cand.get("market"),
        "start_date": cand.get("start_date"),
        "value": value,
        "distance": d["observed_distance"],
        "ranking_distance": d["ranking_distance"],
        "coverage_score": d["coverage_score"],
        "components": d["components"],
        "missing": d["missing"],
    }


def _score_candidates(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    value_fn,
    *,
    weights: dict[str, float] | None,
    missingness_penalty: float,
    min_coverage: float,
) -> tuple[list[tuple[float, dict[str, Any], float, dict[str, Any]]], int]:
    """Score usable candidates by ranking distance; drop low-coverage rows."""
    scored = []
    for cand in candidates:
        v = value_fn(cand)
        if v is None:
            continue
        d = comparable_distance(
            target, cand, weights=weights, missingness_penalty=missingness_penalty
        )
        if d["coverage_score"] < min_coverage:
            continue
        scored.append((d["ranking_distance"], cand, v, d))
    scored.sort(key=lambda t: t[0])
    return scored, len(scored)


def retrieve_global(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    value_fn,
    k: int = DEFAULT_K,
    weights: dict[str, float] | None = None,
    missingness_penalty: float = DEFAULT_MISSINGNESS_PENALTY,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    target_engagement_id: str | None = None,
) -> dict[str, Any]:
    """Engine B: one global soft-distance comparable set over the whole pool."""
    pool = [
        c for c in candidates
        if not (target_engagement_id and c.get("engagement_id") == target_engagement_id)
    ]
    scored, n = _score_candidates(
        target, pool, value_fn, weights=weights,
        missingness_penalty=missingness_penalty, min_coverage=min_coverage,
    )
    top = scored[:k]
    valuation = _valuation(top)
    return {
        "comps": [_comp_payload(c, v, d) for _r, c, v, d in top],
        "valuation": valuation,
        "n_candidates": n,
        "stratum": "BROAD_FALLBACK",
        "coverage_score": round(
            sum(d["coverage_score"] for _r, _c, _v, d in top) / len(top), 6
        ) if top else 0.0,
    }


def _stratum_members(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    members: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        artist_ok = _same_artist(target, cand)
        venue_ok = _same_venue(target, cand)
        market_ok = _same_market(target, cand)
        if artist_ok and venue_ok:
            key = "SAME_ARTIST_VENUE"
        elif artist_ok and market_ok:
            key = "SAME_ARTIST_MARKET"
        elif artist_ok:
            key = "SAME_ARTIST"
        elif venue_ok:
            key = "SAME_VENUE"
        elif market_ok:
            key = "SAME_MARKET"
        else:
            key = "BROAD_FALLBACK"
        members.setdefault(key, []).append(cand)
    return members


def retrieve_stratum(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    value_fn,
    k: int = DEFAULT_K,
    weights: dict[str, float] | None = None,
    missingness_penalty: float = DEFAULT_MISSINGNESS_PENALTY,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_stratum_size: int = DEFAULT_MIN_STRATUM_SIZE,
    target_engagement_id: str | None = None,
) -> dict[str, Any]:
    """Engine C: hierarchical stratum + soft-distance reranking.

    Choose the most-specific stratum (SAME_ARTIST_VENUE → … → BROAD_FALLBACK)
    that has at least ``min_stratum_size`` usable outcome observations; within
    it, rank/weight candidates by the transparent soft distance.
    """
    pool = [
        c for c in candidates
        if not (target_engagement_id and c.get("engagement_id") == target_engagement_id)
    ]
    members = _stratum_members(target, pool)
    chosen: str | None = None
    scored: list[tuple[float, dict[str, Any], float, dict[str, Any]]] = []
    for stratum in STRATA:
        group = members.get(stratum, [])
        group_scored, _ = _score_candidates(
            target, group, value_fn, weights=weights,
            missingness_penalty=missingness_penalty, min_coverage=min_coverage,
        )
        if len(group_scored) >= min_stratum_size:
            chosen = stratum
            scored = group_scored
            break
    if chosen is None:
        # Fall back to the largest available stratum even if under the minimum.
        for stratum in STRATA:
            group = members.get(stratum, [])
            group_scored, _ = _score_candidates(
                target, group, value_fn, weights=weights,
                missingness_penalty=missingness_penalty, min_coverage=min_coverage,
            )
            if group_scored:
                chosen = stratum
                scored = group_scored
                break

    top = scored[:k]
    valuation = _valuation(top)
    return {
        "comps": [_comp_payload(c, v, d) for _r, c, v, d in top],
        "valuation": valuation,
        "n_candidates": len(scored),
        "stratum": chosen,
        "coverage_score": round(
            sum(d["coverage_score"] for _r, _c, _v, d in top) / len(top), 6
        ) if top else 0.0,
    }


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
