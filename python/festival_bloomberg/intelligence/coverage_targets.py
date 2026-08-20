"""Target-population coverage for dense-panel feature admission.

Comparable V2 admission must be judged on the TARGET RESEARCH POPULATION, not
on global warehouse coverage. This module builds the denominator populations
from the frozen baseline corpus (``reports/baseline_research_v1/``) and from
the live warehouse, then measures distinct-entity coverage per denominator.

A feature whose GLOBAL coverage is high but whose TIME-hold coverage is poor is
NOT admitted for research.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..events.identity import normalize_artist_name
from ..events.reconcile import normalize_venue_name

DEFAULT_CORPUS_PATH = "reports/baseline_research_v1/corpus_v1_manifest.json"


def _load_corpus(corpus_path: str | None = None) -> list[dict[str, Any]]:
    path = Path(corpus_path or DEFAULT_CORPUS_PATH)
    data = json.loads(path.read_text())
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("corpus manifest 'rows' is not a list")
    return rows


def load_baseline_targets(corpus_path: str | None = None) -> dict[str, Any]:
    """Target populations from the frozen baseline corpus.

    Returns normalized name sets for venues/artists (ALL vs TIME-hold) plus
    event counts. The TIME-hold population is ``folds.TIME == \"TEST\"``.
    """
    rows = _load_corpus(corpus_path)
    venues_all: set[str] = set()
    artists_all: set[str] = set()
    venues_time: set[str] = set()
    artists_time: set[str] = set()
    time_events = 0
    for r in rows:
        v = normalize_venue_name(r.get("venue"))
        a = normalize_artist_name(r.get("artist"))
        if v:
            venues_all.add(v)
        if a:
            artists_all.add(a)
        is_time = (r.get("folds") or {}).get("TIME") == "TEST"
        if is_time:
            time_events += 1
            if v:
                venues_time.add(v)
            if a:
                artists_time.add(a)
    return {
        "events_all": len(rows),
        "events_time": time_events,
        "venues_all": venues_all,
        "venues_time": venues_time,
        "artists_all": artists_all,
        "artists_time": artists_time,
    }


def _venue_names(conn) -> set[str]:
    names: set[str] = set()
    for r in conn.execute("SELECT name, normalized_name FROM core.venues").fetchall():
        for col in r:
            n = normalize_venue_name(col)
            if n:
                names.add(n)
    return names


def _artist_names(conn) -> set[str]:
    names: set[str] = set()
    for r in conn.execute("SELECT name, normalized_name FROM core.artists").fetchall():
        for col in r:
            n = normalize_artist_name(col)
            if n:
                names.add(n)
    return names


def _match_rate(targets: set[str], covered: set[str]) -> float:
    return round(len(targets & covered) / len(targets), 4) if targets else 0.0


def _pct(numerator: int, denominator: int) -> float:
    """Clamped fraction in [0, 1]; 0.0 when the denominator is empty."""
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, round(numerator / denominator, 4)))


def _venue_capacity_covered(conn) -> set[str]:
    """Distinct venue names with any admissible capacity observation."""
    names: set[str] = set()
    for r in conn.execute(
        """
        SELECT v.name, v.normalized_name
        FROM core.venues v
        WHERE v.capacity IS NOT NULL
           OR EXISTS (SELECT 1 FROM economics.venue_capacity_claims c
                      WHERE c.canonical_venue_id = v.venue_key)
        """
    ).fetchall():
        for col in r:
            n = normalize_venue_name(col)
            if n:
                names.add(n)
    return names


def _venue_coords_covered(conn) -> set[str]:
    names: set[str] = set()
    for r in conn.execute(
        "SELECT name, normalized_name FROM core.venues WHERE latitude IS NOT NULL"
    ).fetchall():
        for col in r:
            n = normalize_venue_name(col)
            if n:
                names.add(n)
    return names


def venue_coverage_by_target(conn, corpus_path: str | None = None) -> dict[str, Any]:
    """Venue coverage over the admission denominators.

    GLOBAL / TOP100 / FESTIVAL come from the warehouse; BASELINE and TIME come
    from the corpus. For EVERY denominator the capacity/coordinate numerator is
    the INTERSECTION of that target set with the covered set — never the global
    covered count divided by the target total.
    """
    targets = load_baseline_targets(corpus_path)
    all_venues = _venue_names(conn)
    cap = _venue_capacity_covered(conn)
    coords = _venue_coords_covered(conn)

    def _row(name: str, target_set: set[str]) -> dict[str, Any]:
        total = len(target_set)
        matched = target_set & all_venues
        cap_cov = target_set & cap
        coords_cov = target_set & coords
        return {
            "name": name,
            "total": total,
            "canonical_match_count": len(matched),
            "canonical_match_pct": _pct(len(matched), total),
            # backward-compatible aliases
            "matched": len(matched),
            "match_pct": _pct(len(matched), total),
            "capacity_count": len(cap_cov),
            "capacity_pct": _pct(len(cap_cov), total),
            "coords_count": len(coords_cov),
            "coords_pct": _pct(len(coords_cov), total),
        }

    out = {
        "GLOBAL_CANONICAL_VENUES": _row("GLOBAL_CANONICAL_VENUES", all_venues),
        "BASELINE_ALL_TARGETS": _row("BASELINE_ALL_TARGETS", targets["venues_all"]),
        "BASELINE_TIME_TARGETS": _row("BASELINE_TIME_TARGETS", targets["venues_time"]),
    }
    # TOP-100 venues by distinct event count (via provider snapshots, name-matched)
    top100 = _top_venues_by_event_count(conn, 100)
    out["TOP_100_EVENT_VENUES"] = _row("TOP_100_EVENT_VENUES", top100)
    festival = _festival_venues(conn)
    out["MAJOR_FESTIVAL_VENUES"] = _row("MAJOR_FESTIVAL_VENUES", festival)
    return out


def artist_coverage_by_target(conn, corpus_path: str | None = None) -> dict[str, Any]:
    """Artist coverage over global + baseline + time-hold populations."""
    targets = load_baseline_targets(corpus_path)
    all_artists = _artist_names(conn)

    def _row(total: int, matched: int) -> dict[str, Any]:
        return {
            "total": total,
            "matched": matched,
            "match_pct": round(matched / total, 4) if total else 0.0,
        }

    return {
        "GLOBAL_CANONICAL_ARTISTS": _row(len(all_artists), len(all_artists)),
        "BASELINE_ARTISTS": _row(len(targets["artists_all"]), len(targets["artists_all"] & all_artists)),
        "TIME_HOLD_ARTISTS": _row(len(targets["artists_time"]), len(targets["artists_time"] & all_artists)),
    }


def event_coverage_by_target(conn, corpus_path: str | None = None) -> dict[str, Any]:
    """Baseline event counts. Cross-walking corpus events to warehouse events
    requires artist+date+venue reconciliation and is reported as a separate task."""
    targets = load_baseline_targets(corpus_path)
    return {
        "BASELINE_EVENTS": targets["events_all"],
        "TIME_HOLD_EVENTS": targets["events_time"],
        "note": "event-level cross-walk to warehouse events requires reconciliation (artist+date+venue); not name-only",
    }


def _top_venues_by_event_count(conn, limit: int) -> set[str]:
    try:
        rows = conn.execute(
            """
            SELECT venue_name
            FROM events.provider_event_snapshots
            WHERE venue_name IS NOT NULL
            GROUP BY venue_name
            ORDER BY COUNT(DISTINCT platform_object_id) DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
    except Exception:
        return set()
    return {normalize_venue_name(r[0]) for r in rows if r[0]}


def _festival_venues(conn) -> set[str]:
    """Venue names appearing in the festival/event spine (best-effort)."""
    names: set[str] = set()
    for table in ("core.series_events", "core.festival_editions"):
        try:
            rows = conn.execute(f"SELECT * FROM {table} LIMIT 0").fetchall()
        except Exception:
            continue
        for col in ("venue_name", "venue", "name"):
            try:
                for r in conn.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL").fetchall():
                    n = normalize_venue_name(r[0])
                    if n:
                        names.add(n)
            except Exception:
                continue
    return names
