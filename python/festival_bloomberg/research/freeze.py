"""Reproducible corpus freeze for baseline research.

A research result must be reproducible against the same frozen dataset. This
module reads the boxscore research warehouse, derives a per-engagement
``publication_time`` (when the box-office result became publicly available),
joins the Corpus V2 split manifests, and produces a checksummed manifest. New
scrapes never silently change a frozen score — the checksum changes first.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb

from ..acquisition.contracts import content_hash_of, utc_now
from .boxscore import SOURCE_BILLBOARD, SOURCE_POLLSTAR

CORPUS_VERSION = "public_boxscore_research_corpus_v2"
SOFTWARE_VERSION = "baseline_research_v1"

# A result becomes "available" for a target engagement only if it was
# published before the target's event start. Billboard is a single archived
# year-end compilation with no per-row publication date; we estimate its
# publication time as the latest event date on the page + a one-week chart lag.
_BILLBOARD_PUBLICATION_LAG_DAYS = 7


def _derive_publication_time(
    row: dict[str, Any],
    source_pub_by_url: dict[str, str],
    billboard_latest_event: str | None,
    billboard_pub_time: str | None,
) -> str | None:
    source = row.get("reporting_source")
    pub = source_pub_by_url.get(row.get("source_url") or "")
    if pub:
        return pub
    if source == SOURCE_BILLBOARD:
        return billboard_pub_time
    return None


def _year_infer_pollstar(
    row: dict[str, Any],
    source_pub_by_url: dict[str, str],
) -> str | None:
    """Pollstar 'Dates:' omits the year; the page publication year is the
    defensible inference for a current-week chart. Returns an ISO date for
    single-show rows whose date could not be parsed but can be year-inferred."""
    if row.get("reporting_source") != SOURCE_POLLSTAR:
        return None
    if row.get("start_date") is not None:
        return None
    if row.get("number_of_shows") != 1:
        return None
    pub = source_pub_by_url.get(row.get("source_url") or "")
    if not pub:
        return None
    dates_raw = (row.get("dates_raw") or "").strip()
    year = pub[:4]
    # reuse the boxscore date parser with the inferred year
    from .boxscore import _dates_from_raw

    start, end, _shows = _dates_from_raw(dates_raw, year_hint=int(year))
    return start


def freeze_research_corpus(
    db_path: str | Path,
    *,
    corpus_version: str = CORPUS_VERSION,
) -> dict[str, Any]:
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        cur = connection.execute(
            "SELECT * FROM research.boxoffice_engagements ORDER BY engagement_id"
        )
        columns = [c[0] for c in cur.description]
        engagements = [dict(zip(columns, r)) for r in cur.fetchall()]
        sources = connection.execute(
            "SELECT source_url, publication_date FROM research.boxoffice_sources"
        ).fetchall()
        resolutions = connection.execute(
            "SELECT raw_engagement_id, canonical_engagement_id, resolution_status "
            "FROM research.boxoffice_engagement_resolutions"
        ).fetchall()
        splits = connection.execute(
            "SELECT split_type, canonical_engagement_id, fold FROM research.research_splits"
        ).fetchall()
    finally:
        connection.close()

    source_pub_by_url = {
        url: _iso(pub) for url, pub in sources if pub is not None
    }

    # Billboard publication-time estimate (year-end compilation + chart lag).
    billboard_dates = sorted(
        e["start_date"] for e in engagements
        if e.get("reporting_source") == SOURCE_BILLBOARD and e.get("start_date")
    )
    billboard_pub_time = None
    if billboard_dates:
        latest = billboard_dates[-1].isoformat()
        billboard_pub_time = _add_days(latest, _BILLBOARD_PUBLICATION_LAG_DAYS)

    canonical_by_raw = {r[0]: r[1] for r in resolutions}
    folds_by_canonical: dict[str, dict[str, str]] = defaultdict(dict)
    for split_type, canonical_id, fold in splits:
        folds_by_canonical[canonical_id][split_type] = fold

    rows: list[dict[str, Any]] = []
    for e in engagements:
        start = e.get("start_date")
        if start is None:
            inferred = _year_infer_pollstar(e, source_pub_by_url)
            if inferred:
                start = inferred
                e = {**e, "start_date": inferred}
        canonical_id = canonical_by_raw.get(e["engagement_id"], e["engagement_id"])
        rows.append({
            "engagement_id": e["engagement_id"],
            "canonical_engagement_id": canonical_id,
            "artist": e.get("artist"),
            "venue": e.get("venue"),
            "city": e.get("city"),
            "market": e.get("market"),
            "country": e.get("country"),
            "tour": e.get("tour"),
            "start_date": _iso(start),
            "end_date": _iso(e.get("end_date")),
            "number_of_shows": e.get("number_of_shows"),
            "is_multi_show": bool(e.get("is_multi_show")),
            "headcount_total": e.get("headcount_total"),
            "headcount_definition": e.get("headcount_definition"),
            "ticket_gross_total": e.get("ticket_gross_total"),
            "currency": e.get("currency"),
            "price_min": e.get("price_min"),
            "price_max": e.get("price_max"),
            "reported_sellouts": e.get("reported_sellouts"),
            "sell_through_pct": e.get("sell_through_pct"),
            "reporting_source": e.get("reporting_source"),
            "publication_time": _derive_publication_time(
                e, source_pub_by_url, billboard_dates[-1].isoformat() if billboard_dates else None, billboard_pub_time
            ),
            "rights_status": e.get("rights_status"),
            "rank": e.get("rank"),
            "is_reported": bool(e.get("is_reported", True)),
            "is_estimated": bool(e.get("is_estimated")),
            "folds": dict(folds_by_canonical.get(canonical_id, {})),
        })

    # The Corpus V2 TIME folds were computed while Pollstar rows still had
    # NULL event dates (their "Dates:" field is year-less), so they were all
    # dumped into TEST. Now that dates are year-inferred, recompute the TIME
    # fold deterministically (same ~80th-percentile cutoff rule) so the
    # chronological holdout is correct. Group folds (artist/venue/market/tour)
    # are date-independent and are carried over unchanged.
    _recompute_time_folds(rows)

    checksum = content_hash_of(sorted(
        (json.dumps(r, sort_keys=True, default=str) for r in rows)
    ))

    return {
        "corpus_version": corpus_version,
        "software_version": SOFTWARE_VERSION,
        "generated_at": utc_now().isoformat(),
        "checksum": checksum,
        "source_db": str(db_path),
        "row_count": len(rows),
        "billboard_publication_time_estimate": billboard_pub_time,
        "rows": rows,
    }


def _recompute_time_folds(rows: list[dict[str, Any]]) -> None:
    distinct = sorted({r["start_date"] for r in rows if r.get("start_date")})
    if len(distinct) < 2:
        return
    # Train = earliest ~80% of distinct dates, test = the remainder. Using a
    # train-count (not an index into the sorted *row* list) keeps the cutoff
    # between dates and always leaves at least one date in test.
    train_count = max(1, int(len(distinct) * 0.8))
    cutoff = distinct[train_count - 1]
    for r in rows:
        sd = r.get("start_date")
        fold = "TRAIN" if sd and sd <= cutoff else "TEST"
        folds = dict(r.get("folds") or {})
        folds["TIME"] = fold
        r["folds"] = folds


def corpus_checksum(rows: list[dict[str, Any]]) -> str:
    return content_hash_of(sorted(
        (json.dumps(r, sort_keys=True, default=str) for r in rows)
    ))


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _add_days(iso: str, days: int) -> str:
    from datetime import date, timedelta

    y, m, d = iso[:10].split("-")
    return (date(int(y), int(m), int(d)) + timedelta(days=days)).isoformat()


def write_manifest(manifest: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return p


def load_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
