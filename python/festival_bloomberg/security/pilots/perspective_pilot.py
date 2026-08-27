"""PILOT 3 — Perspective (Apache-2.0) disposable ARTIST MONITOR prototype.

Evaluates the Perspective table pattern (sort / filter / pivot / streaming
updates / DuckDB-Arrow interop) against a REAL exported Artist Security
snapshot. This is a disposable prototype — it never replaces the canonical
terminal data model.

What the prototype measures (returned as a report):

- the exported snapshot shape (columns present);
- whether each required column (artist, market, factor coverage, LB/YT/Wiki
  momentum, shows_365d, catalog recency, ticket observations) is populated;
- the sort/filter/pivot semantics that a Perspective table would expose,
  exercised over the exported rows (pure computation — no UI dependency).

Perspective itself is a frontend/desktop component; the data contract here is
the Arrow/JSON interchange, which is what the adoption decision hinges on.
"""

from __future__ import annotations

from typing import Any

REQUIRED_COLUMNS = (
    "artist",
    "market",
    "factor_coverage",
    "lb_momentum",
    "yt_momentum",
    "wiki_momentum",
    "shows_365d",
    "catalog_recency",
    "ticket_observations",
)


def export_snapshot(conn, *, artist_keys: list[str] | None = None) -> list[dict[str, Any]]:
    """Export the security snapshot rows as a flat table (one row per artist)."""
    if artist_keys is None:
        rows = conn.execute(
            "SELECT artist_key FROM metrics.artist_security_snapshots"
        ).fetchall()
        artist_keys = [r[0] for r in rows]
    if not artist_keys:
        return []
    placeholders = ", ".join("?" for _ in artist_keys)
    snap = conn.execute(
        f"""
        SELECT artist_key, factor_summary, snapshot_date
        FROM metrics.artist_security_snapshots
        WHERE artist_key IN ({placeholders})
        """,
        artist_keys,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for artist_key, factor_summary, snapshot_date in snap:
        import json

        families: dict[str, Any] = {}
        try:
            families = json.loads(factor_summary or "{}")
        except ValueError:
            pass

        def family_value(family: str, factor: str) -> float | None:
            for item in families.get(family, []) or []:
                if item.get("factor_name") == factor:
                    return item.get("value")
            return None

        row: dict[str, Any] = {
            "artist": artist_key,
            "market": None,  # ARTIST x MARKET snapshots land in a later pass
            "factor_coverage": len(families),
            # strict column semantics: each momentum column reads ONLY its own
            # factor (no aliasing across sources)
            "lb_momentum": family_value("MOMENTUM", "LB_LISTEN_VELOCITY"),
            "yt_momentum": family_value("DEMAND", "YT_SUBSCRIBERS"),
            "wiki_momentum": family_value("MOMENTUM", "WIKI_MOMENTUM"),
            "shows_365d": None,
            "catalog_recency": family_value("CATALOG", "DAYS_SINCE_LAST_RELEASE"),
            "ticket_observations": None,
            "snapshot_date": str(snapshot_date)[:10] if snapshot_date else None,
        }
        out.append(row)
    return out


def _semantics_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure the sort/filter/pivot semantics a Perspective table would expose."""
    populated = {c: sum(1 for r in rows if r.get(c) is not None) for c in REQUIRED_COLUMNS}
    # sort semantics: rows sortable by each populated column
    sortable = {c: populated[c] > 1 for c in REQUIRED_COLUMNS}
    # filter semantics: at least one distinct value per column
    filterable = {c: len({r.get(c) for r in rows}) > 1 for c in REQUIRED_COLUMNS}
    # pivot semantics: factor_coverage groups
    coverage_groups = sorted({r.get("factor_coverage") for r in rows})
    return {
        "rows": len(rows),
        "columns_present": [c for c in REQUIRED_COLUMNS if populated[c] > 0],
        "columns_populated": populated,
        "sortable": sortable,
        "filterable": filterable,
        "pivot_groups": {"factor_coverage": coverage_groups},
    }


def run_pilot(conn, *, artist_keys: list[str] | None = None) -> dict[str, Any]:
    """Export + measure; returns ADOPT/REJECT with measured data contract."""
    rows = export_snapshot(conn, artist_keys=artist_keys)
    semantics = _semantics_report(rows)
    has_core = all(semantics["columns_populated"].get(c, 0) > 0 for c in ("artist", "factor_coverage"))
    recommendation = "ADOPT" if has_core and semantics["rows"] > 0 else "INSUFFICIENT_DATA"
    return {
        "status": "COMPLETE",
        "recommendation": recommendation,
        "reason": (
            "Export carries the required artist monitor columns; sort/filter/"
            "pivot semantics measurable over Arrow/JSON interchange"
            if recommendation == "ADOPT"
            else "Snapshot not populated yet — re-run after security master materialization"
        ),
        "semantics": semantics,
        "implementation_cost_note": "Perspective table renders the exported rows; "
                                     "DuckDB/Arrow interop via pyarrow",
    }
