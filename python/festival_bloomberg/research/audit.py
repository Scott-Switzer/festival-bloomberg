"""Corpus audit for the public boxscore research panel.

Answers the questions that matter before any model is fit:

* how many *independent* artists / venues / markets / promoters / tours do we
  actually have (not just row count)?
* how concentrated is the panel (Herfindahl + top-N shares)?
* what is each source's selection mechanism (we never pretend it is random)?
* what is the temporal and market coverage (Chicago tagged explicitly)?
* is the panel ready for grouped/time-held-out baseline research?

This module computes diagnostics and produces deterministic, leakage-safe
split manifests. It fits NO models.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime
from typing import Any

from ..acquisition.contracts import utc_now
from .boxscore import (
    HEADCOUNT_PAID_TICKETS,
    HEADCOUNT_REPORTED_ATTENDANCE,
    SOURCE_BILLBOARD,
    SOURCE_POLLSTAR,
    SOURCE_TOURING_DATA,
)
from .resolution import normalize

SPLIT_TIME = "TIME"
SPLIT_ARTIST_GROUP = "ARTIST_GROUP"
SPLIT_VENUE_GROUP = "VENUE_GROUP"
SPLIT_MARKET_GROUP = "MARKET_GROUP"
SPLIT_TOUR_GROUP = "TOUR_GROUP"

VERDICT_NOT_READY = "NOT_READY"
VERDICT_NARROW = "NARROW_RESEARCH_READY"
VERDICT_READY = "RESEARCH_READY"

# ---------------------------------------------------------------------------
# concentration
# ---------------------------------------------------------------------------
def _iso(value: Any) -> str | None:
    """Normalize a DATE column value (str or datetime.date) to an ISO string."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def hhi(values: list[str]) -> float:
    """Herfindahl-Hirschman index over a categorical series (0..1)."""
    if not values:
        return 0.0
    counts = Counter(values)
    total = len(values)
    return sum((c / total) ** 2 for c in counts.values())


def _top_share(values: list[str], k: int = 5) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    total = len(values)
    top = sum(c for _, c in counts.most_common(k))
    return top / total


def corpus_diversity(engagements: list[dict[str, Any]]) -> dict[str, Any]:
    artists = [e.get("artist") or "" for e in engagements]
    venues = [e.get("venue") or "" for e in engagements]
    markets = [e.get("city") or e.get("market") or "" for e in engagements]
    promoters = [e.get("promoter") or "" for e in engagements]
    tours = [e.get("tour") for e in engagements if e.get("tour")]
    sources = [e.get("reporting_source") or "" for e in engagements]

    per_artist = Counter(a for a in artists if a)
    per_tour = Counter(t for t in tours)

    def _distinct_and_median(counter: Counter) -> tuple[int, int, int]:
        if not counter:
            return 0, 0, 0
        counts = sorted(counter.values())
        median = counts[len(counts) // 2]
        return len(counter), median, max(counts)

    na, med_a, max_a = _distinct_and_median(per_artist)
    nt, med_t, max_t = _distinct_and_median(per_tour)

    return {
        "rows": len(engagements),
        "distinct_artists": na,
        "distinct_tours": nt,
        "distinct_venues": len({v for v in venues if v}),
        "distinct_markets": len({m for m in markets if m}),
        "distinct_promoters": len({p for p in promoters if p}),
        "distinct_sources": len({s for s in sources if s}),
        "hhi_artist": round(hhi(artists), 6),
        "hhi_venue": round(hhi(venues), 6),
        "hhi_market": round(hhi(markets), 6),
        "hhi_source": round(hhi(sources), 6),
        "top_artist_share": round(_top_share(artists, 1), 6),
        "top5_artist_share": round(_top_share(artists, 5), 6),
        "top_venue_share": round(_top_share(venues, 1), 6),
        "top5_venue_share": round(_top_share(venues, 5), 6),
        "top_promoter_share": round(_top_share(promoters, 1), 6),
        "median_engagements_per_artist": med_a,
        "max_engagements_per_artist": max_a,
        "median_engagements_per_tour": med_t,
        "max_engagements_per_tour": max_t,
    }


# ---------------------------------------------------------------------------
# venue size bins (capacity-aware but never fabricated)
# ---------------------------------------------------------------------------
_BINS = [
    ("<1,000", None, 1000),
    ("1,000-3,000", 1000, 3000),
    ("3,000-8,000", 3000, 8000),
    ("8,000-20,000", 8000, 20000),
    ("20,000+", 20000, None),
]


def venue_size_bins(engagements: list[dict[str, Any]]) -> dict[str, Any]:
    """Bin single-show engagements by reported capacity where known."""
    binned: dict[str, int] = {name: 0 for name, _, _ in _BINS}
    unknown = 0
    for e in engagements:
        capacity = e.get("capacity_total")
        if e.get("is_multi_show") or capacity is None:
            unknown += 1
            continue
        placed = False
        for name, lo, hi in _BINS:
            if (lo is None or capacity >= lo) and (hi is None or capacity < hi):
                binned[name] += 1
                placed = True
                break
        if not placed:
            unknown += 1
    # Pollstar tier labels are metadata only; report them separately.
    tiers = Counter(e.get("capacity_tier") or "UNKNOWN" for e in engagements if e.get("reporting_source") == SOURCE_POLLSTAR)
    return {"by_capacity_bin": binned, "unknown_or_multi_show": unknown, "pollstar_tiers": dict(tiers)}


# ---------------------------------------------------------------------------
# selection metadata (explicit per-source sampling mechanism)
# ---------------------------------------------------------------------------
SELECTION_METADATA: dict[str, dict[str, str]] = {
    SOURCE_BILLBOARD: {
        "selection_method": "BILLBOARD_BOXSCORE_CHART",
        "ranking_or_chart_status": "ranked weekly chart (top reported engagements)",
        "known_threshold": "chart-ranked, not exhaustive of all events",
        "unknown_threshold": "chart cutoff not documented for archived page",
        "coverage_scope": "single archived Billboard Current Boxscore snapshot",
    },
    SOURCE_POLLSTAR: {
        "selection_method": "POLLSTAR_HOT_TICKETS_CHART",
        "ranking_or_chart_status": "top 20 reported engagements per week (top 5 per capacity tier)",
        "known_threshold": "top-5-per-tier ranking; excludes below-threshold events",
        "unknown_threshold": "exact eligibility window varies by week",
        "coverage_scope": "biweekly Hot Tickets pages (Jan-May 2024)",
    },
    SOURCE_TOURING_DATA: {
        "selection_method": "TOURING_DATA_REPORTED_TOUR",
        "ranking_or_chart_status": "reported box-office per tour (reported vs estimated marked)",
        "known_threshold": "only tours the publisher chose to compile",
        "unknown_threshold": "tour selection mechanism undocumented",
        "coverage_scope": "individual tour pages linked from the Data category",
    },
}


def selection_metadata(source: str) -> dict[str, str]:
    return dict(SELECTION_METADATA.get(source, {
        "selection_method": "UNKNOWN",
        "ranking_or_chart_status": "UNKNOWN",
        "known_threshold": "UNKNOWN",
        "unknown_threshold": "UNKNOWN",
        "coverage_scope": "UNKNOWN",
    }))


# ---------------------------------------------------------------------------
# temporal + market coverage
# ---------------------------------------------------------------------------
def temporal_coverage(engagements: list[dict[str, Any]]) -> dict[str, Any]:
    dates = [_iso(e.get("start_date")) for e in engagements if e.get("start_date")]
    dates = [d for d in dates if d]
    years = [d[:4] for d in dates]
    by_year = Counter(years)
    by_source_year = Counter(
        (e.get("reporting_source"), _iso(e.get("start_date"))[:4])
        for e in engagements
        if _iso(e.get("start_date"))
    )
    return {
        "earliest_date": min(dates) if dates else None,
        "latest_date": max(dates) if dates else None,
        "distinct_years": len(by_year),
        "engagements_by_year": dict(sorted(by_year.items())),
        "engagements_by_source_year": {f"{s}:{y}": n for (s, y), n in sorted(by_source_year.items())},
    }


def market_coverage(engagements: list[dict[str, Any]]) -> dict[str, Any]:
    chicago = [
        e for e in engagements
        if "chicago" in normalize(e.get("city") or e.get("market") or "")
    ]
    return {
        "chicago_engagements": len(chicago),
        "chicago_artists": len({e.get("artist") for e in chicago if e.get("artist")}),
        "chicago_venues": len({e.get("venue") for e in chicago if e.get("venue")}),
    }


# ---------------------------------------------------------------------------
# baseline target readiness (no model is trained)
# ---------------------------------------------------------------------------
def target_readiness(engagements: list[dict[str, Any]]) -> dict[str, Any]:
    single = [e for e in engagements if not e.get("is_multi_show") and e.get("is_reported", True) and not e.get("is_estimated")]
    reported = [e for e in single if e.get("headcount_definition") == HEADCOUNT_REPORTED_ATTENDANCE and e.get("headcount_total") is not None]
    paid = [e for e in single if e.get("headcount_definition") == HEADCOUNT_PAID_TICKETS and e.get("headcount_total") is not None]
    gross = [e for e in single if e.get("ticket_gross_total") is not None]
    sellout = [e for e in single if e.get("reported_sellouts") is not None]
    sell_through = [e for e in single if e.get("sell_through_pct") is not None]
    return {
        "single_show_reported": len(single),
        "REPORTED_ATTENDANCE": len(reported),
        "PAID_TICKETS": len(paid),
        "TICKET_GROSS": len(gross),
        "SELL_OUT": len(sellout),
        "SELL_THROUGH": len(sell_through),
    }


def baseline_readiness(engagements: list[dict[str, Any]]) -> dict[str, Any]:
    """Model-free baseline-research readiness verdict, with reasons."""
    single = [e for e in engagements if not e.get("is_multi_show") and e.get("is_reported", True) and not e.get("is_estimated")]
    labeled = [e for e in single if e.get("headcount_total") is not None]
    artists = [e.get("artist") or "" for e in labeled]
    venues = [e.get("venue") or "" for e in labeled]
    years = sorted({(_iso(e["start_date"]) or "")[:4] for e in labeled if _iso(e.get("start_date"))})
    year_span = (int(years[-1]) - int(years[0])) if years else 0

    n = len(labeled)
    n_artists = len({a for a in artists if a})
    n_venues = len({v for v in venues if v})
    artist_hhi = hhi(artists)
    venue_hhi = hhi(venues)

    reasons: list[str] = []
    if n < 50:
        reasons.append(f"only {n} single-show reported headcount engagements (< 50)")
    if n_artists < 25:
        reasons.append(f"only {n_artists} distinct artists (< 25)")
    if n_venues < 15:
        reasons.append(f"only {n_venues} distinct venues (< 15)")
    if year_span < 2:
        reasons.append(f"temporal span {year_span} years (< 2)")

    if n < 50 or n_artists < 10 or n_venues < 5:
        verdict = VERDICT_NOT_READY
    elif n < 100 or n_artists < 25 or n_venues < 15 or year_span < 2 or artist_hhi > 0.25 or venue_hhi > 0.4:
        verdict = VERDICT_NARROW
        if not reasons:
            reasons.append("diversity/concentration below full research threshold")
    else:
        verdict = VERDICT_READY

    return {
        "verdict": verdict,
        "labeled_single_show": n,
        "distinct_artists": n_artists,
        "distinct_venues": n_venues,
        "year_span": year_span,
        "artist_hhi": round(artist_hhi, 6),
        "venue_hhi": round(venue_hhi, 6),
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# deterministic, leakage-safe split manifests
# ---------------------------------------------------------------------------
def _stable_hash(value: str, seed: int = 42) -> int:
    return int(hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()[:8], 16)


def _fold_by_hash(group_key: str) -> str:
    return "TRAIN" if _stable_hash(group_key) % 2 == 0 else "TEST"


def build_research_splits(canonicals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministic manifests for TIME + grouped holds (never trains a model)."""
    rows: list[dict[str, Any]] = []
    created_at = utc_now().isoformat()
    summary: dict[str, Any] = {}

    dated = sorted(
        [c for c in canonicals if c.get("start_date")],
        key=lambda c: _iso(c["start_date"]) or "",
    )
    # time split at ~80th percentile date (train = earlier, test = later)
    time_rows: list[dict[str, Any]] = []
    if len({_iso(c["start_date"]) for c in dated}) >= 2:
        cutoff = _iso(dated[int(len(dated) * 0.8)]["start_date"]) or "9999"
        for c in canonicals:
            start = _iso(c.get("start_date"))
            fold = "TRAIN" if (start or "9999") <= cutoff else "TEST"
            time_rows.append(_split_row(SPLIT_TIME, c, fold, start or "undated", created_at))
        rows.extend(time_rows)
        summary[SPLIT_TIME] = {"cutoff": cutoff, "train": sum(1 for r in time_rows if r["fold"] == "TRAIN"), "test": sum(1 for r in time_rows if r["fold"] == "TEST")}
    else:
        summary[SPLIT_TIME] = {"cutoff": None, "train": 0, "test": 0, "note": "insufficient distinct dates"}

    group_specs = [
        (SPLIT_ARTIST_GROUP, lambda c: c.get("artist") or "unknown"),
        (SPLIT_VENUE_GROUP, lambda c: c.get("venue") or "unknown"),
        (SPLIT_MARKET_GROUP, lambda c: c.get("city") or c.get("market") or "unknown"),
        # tour identity when known; fall back to artist so an artist's untoured
        # engagements are never split across folds either.
        (SPLIT_TOUR_GROUP, lambda c: c.get("tour") or c.get("artist") or "unknown"),
    ]
    for split_type, key_fn in group_specs:
        grouped: dict[str, str] = {}
        for c in canonicals:
            grouped[c["canonical_engagement_id"]] = key_fn(c)
        fold_by_group: dict[str, str] = {}
        for c in canonicals:
            g = grouped[c["canonical_engagement_id"]]
            fold_by_group[g] = _fold_by_hash(f"{split_type}:{g}")
            rows.append(_split_row(split_type, c, fold_by_group[g], g, created_at))
        summary[split_type] = {
            "train": sum(1 for r in rows if r["split_type"] == split_type and r["fold"] == "TRAIN"),
            "test": sum(1 for r in rows if r["split_type"] == split_type and r["fold"] == "TEST"),
        }

    return rows, summary


def _split_row(split_type: str, canonical: dict[str, Any], fold: str, group_key: str, created_at: str) -> dict[str, Any]:
    cid = canonical["canonical_engagement_id"]
    return {
        "split_id": f"split_{split_type}_{cid}",
        "split_type": split_type,
        "canonical_engagement_id": cid,
        "fold": fold,
        "group_key": group_key,
        "created_at": created_at,
        "seed": 42,
        "deterministic": True,
    }
