"""Cross-source engagement resolution + agreement for the boxscore corpus.

The same engagement can appear in Billboard, Pollstar and Touring Data. This
module maps raw ``boxoffice_engagements`` rows onto deterministic
``canonical_boxoffice_engagements`` identities WITHOUT mutating raw rows, and
then measures how well independent sources agree on the numeric fields.

Resolution is graded and fail-safe:

* one raw row per canonical        -> DISTINCT (a unique engagement)
* 2+ rows, identical date + shows  -> EXACT_MATCH
* 2+ rows, overlapping date ranges -> PROBABLE_MATCH
* 2+ rows, no clean overlap        -> REVIEW_REQUIRED (never force-merged)

Raw values are NEVER overwritten; agreement is reported, not reconciled.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now


def _iso(value: Any) -> str | None:
    """Normalize a DATE column value (str or datetime.date) to an ISO string."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)

RESOLUTION_DISTINCT = "DISTINCT"
RESOLUTION_EXACT_MATCH = "EXACT_MATCH"
RESOLUTION_PROBABLE_MATCH = "PROBABLE_MATCH"
RESOLUTION_REVIEW_REQUIRED = "REVIEW_REQUIRED"

CONFIDENCE_UNIQUE = "UNIQUE"
CONFIDENCE_EXACT = "EXACT"
CONFIDENCE_PROBABLE = "PROBABLE"
CONFIDENCE_REVIEW = "REVIEW_REQUIRED"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(value: Any) -> str:
    if value is None:
        return ""
    return _NON_ALNUM.sub("", str(value).lower())


def _identity_key(e: dict[str, Any]) -> tuple[str, str, str]:
    """(artist, venue, city) — the cross-source identity spine."""
    return (
        normalize(e.get("artist")),
        normalize(e.get("venue")),
        normalize(e.get("city") or e.get("market")),
    )


def _date_overlaps(
    a_start: str | None,
    a_end: str | None,
    b_start: str | None,
    b_end: str | None,
) -> bool:
    a_s = _iso(a_start)
    b_s = _iso(b_start)
    if not a_s or not b_s:
        return False
    a_e = _iso(a_end) or a_s
    b_e = _iso(b_end) or b_s
    return a_s <= b_e and b_s <= a_e


def resolve_engagements(
    engagements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Group raw engagements into canonical identities (deterministic).

    Returns ``(canonicals, resolutions, stats)``. Raw rows are untouched.
    """
    by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in engagements:
        by_identity[_identity_key(e)].append(e)

    canonicals: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    created_at = utc_now().isoformat()

    for key, rows in sorted(by_identity.items()):
        # cluster rows by overlapping date ranges
        clusters: list[list[dict[str, Any]]] = []
        for row in sorted(rows, key=lambda r: (_iso(r.get("start_date")) or "", r.get("engagement_id") or "")):
            placed = False
            for cluster in clusters:
                anchor = cluster[0]
                if _date_overlaps(
                    anchor.get("start_date"), anchor.get("end_date"),
                    row.get("start_date"), row.get("end_date"),
                ) and anchor.get("number_of_shows") == row.get("number_of_shows"):
                    cluster.append(row)
                    placed = True
                    break
            if not placed:
                clusters.append([row])

        for cluster in clusters:
            ids = sorted({r["engagement_id"] for r in cluster})
            anchor = cluster[0]
            # The canonical id is derived from the cluster's raw engagement ids,
            # which are unique and deterministic — this guarantees 1:1 identity
            # even when an engagement's date could not be parsed (start_date is
            # NULL) and would otherwise collide on the identity key.
            canonical_id = "canon_" + content_hash_of({"ids": ids})[:20]

            if len(cluster) == 1:
                confidence = CONFIDENCE_UNIQUE
                status = RESOLUTION_DISTINCT
            else:
                same_date = len({r.get("start_date") for r in cluster}) == 1
                confidence = CONFIDENCE_EXACT if same_date else CONFIDENCE_PROBABLE
                status = RESOLUTION_EXACT_MATCH if same_date else RESOLUTION_PROBABLE_MATCH

            canonicals.append({
                "canonical_engagement_id": canonical_id,
                "artist": anchor.get("artist"),
                "venue": anchor.get("venue"),
                "market": anchor.get("market"),
                "city": anchor.get("city"),
                "state": anchor.get("state"),
                "country": anchor.get("country"),
                "tour": anchor.get("tour"),
                "start_date": min((_iso(r.get("start_date")) for r in cluster if _iso(r.get("start_date"))), default=None),
                "end_date": max((_iso(r.get("end_date")) for r in cluster if _iso(r.get("end_date"))), default=None),
                "number_of_shows": anchor.get("number_of_shows"),
                "is_multi_show": any(r.get("is_multi_show") for r in cluster),
                "resolution_confidence": confidence,
                "source_count": len(cluster),
                "software_version": "public_boxscore_research_corpus_v2",
            })

            for row in cluster:
                resolutions.append({
                    "resolution_id": "res_" + content_hash_of({
                        "raw": row["engagement_id"], "canonical": canonical_id,
                    })[:20],
                    "raw_engagement_id": row["engagement_id"],
                    "canonical_engagement_id": canonical_id,
                    "resolution_status": status,
                    "match_key": "|".join(key),
                    "created_at": created_at,
                })

    status_counts = Counter(r["resolution_status"] for r in resolutions)
    multi_source = sum(1 for c in canonicals if c["source_count"] >= 2)
    stats = {
        "raw_engagements": len(engagements),
        "canonical_engagements": len(canonicals),
        "resolution_status_counts": dict(status_counts),
        "canonicals_with_multiple_sources": multi_source,
        "cross_source_match_rate": round(multi_source / len(canonicals), 6) if canonicals else 0.0,
    }
    return canonicals, resolutions, stats


_AGREEMENT_FIELDS = ("headcount_total", "ticket_gross_total", "price_min", "price_max", "number_of_shows")


def cross_source_agreement(
    engagements: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    canonicals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure how independent sources agree on matched engagements.

    Never mutates raw values; only reports per-field differences for
    canonical engagements observed by 2+ distinct sources.
    """
    raw_by_id = {e["engagement_id"]: e for e in engagements}
    by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in resolutions:
        if r["resolution_status"] in (RESOLUTION_DISTINCT, RESOLUTION_REVIEW_REQUIRED):
            continue
        by_canonical[r["canonical_engagement_id"]].append(raw_by_id[r["raw_engagement_id"]])

    field_summary: dict[str, dict[str, float]] = {}
    matched = 0
    examples: list[dict[str, Any]] = []
    for canonical in canonicals:
        rows = by_canonical.get(canonical["canonical_engagement_id"], [])
        distinct_sources = {r.get("reporting_source") for r in rows}
        if len(rows) < 2 or len(distinct_sources) < 2:
            continue
        matched += 1
        example: dict[str, Any] = {
            "canonical_engagement_id": canonical["canonical_engagement_id"],
            "artist": canonical.get("artist"),
            "venue": canonical.get("venue"),
            "start_date": canonical.get("start_date"),
            "sources": sorted(distinct_sources),
        }
        for field in _AGREEMENT_FIELDS:
            values = [r.get(field) for r in rows if r.get(field) is not None]
            if len(values) < 2:
                continue
            lo, hi = min(values), max(values)
            spread = hi - lo
            pct = (spread / max(abs(hi), 1.0)) * 100.0
            field_summary.setdefault(field, {
                "comparisons": 0, "exact": 0, "abs_diff_sum": 0.0, "max_pct_diff": 0.0,
            })
            s = field_summary[field]
            s["comparisons"] += 1
            if spread == 0:
                s["exact"] += 1
            s["abs_diff_sum"] += spread
            s["max_pct_diff"] = max(s["max_pct_diff"], pct)
            example[field] = {"min": lo, "max": hi, "pct_diff": round(pct, 4)}
        if len(example) > 4:
            examples.append(example)

    summary = {}
    for field, s in field_summary.items():
        n = s["comparisons"]
        summary[field] = {
            "comparisons": n,
            "exact_agreements": s["exact"],
            "exact_rate": round(s["exact"] / n, 6) if n else 0.0,
            "mean_abs_diff": round(s["abs_diff_sum"] / n, 2) if n else 0.0,
            "max_pct_diff": round(s["max_pct_diff"], 4),
        }

    return {
        "matched_canonicals_compared": matched,
        "field_agreement": summary,
        "examples": examples[:20],
    }
