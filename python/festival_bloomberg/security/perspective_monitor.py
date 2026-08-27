"""ARTIST_SECURITY_1000_SCALE_V1 — P7: internal artist monitor (Perspective).

A REAL internal analyst monitor over ACTUAL ARTIST_SECURITY_1000 data — no
synthetic rows. Perspective (Apache-2.0) provides the professional grid
(sort / filter / pivot / group / search); this module provides the DATA
CONTRACT Perspective renders:

    artist | factor coverage | LB momentum | Wiki momentum | YT momentum |
    shows_365d | festival appearances | catalog recency | ticket observations |
    markets played | latest update | data confidence

This is an internal analyst monitor, NOT a product UI rewrite. The export is
a flat Arrow/JSON table (pyarrow in the warehouse, JSON for the browser) so
Perspective's Arrow interop consumes it directly. Sort/filter/pivot semantics
are measured over the real rows and reported.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ..attention.listenbrainz import artist_key_for

MONITOR_VERSION = "perspective_artist_monitor_v1000_v1"

#: Column semantics — each momentum column reads ONLY its own factor.
COLUMNS = (
    "artist",
    "factor_coverage",
    "lb_momentum",
    "wiki_momentum",
    "yt_momentum",
    "shows_365d",
    "festival_appearances",
    "catalog_recency",
    "ticket_observations",
    "markets_played",
    "latest_update",
    "data_confidence",
)


def export_monitor_rows(conn, *, artist_keys: list[str] | None = None) -> list[dict[str, Any]]:
    """Flat monitor table over REAL security data (one row per artist)."""
    if artist_keys is None:
        rows = conn.execute(
            "SELECT artist_key FROM metrics.artist_security_snapshots ORDER BY artist_key"
        ).fetchall()
        artist_keys = [r[0] for r in rows]
    if not artist_keys:
        return []
    placeholders = ", ".join("?" for _ in artist_keys)

    # factor observations per artist (real factor tape)
    factors = conn.execute(
        f"""
        SELECT artist_key, factor_name, value, as_of
        FROM metrics.artist_factor_observations
        WHERE artist_key IN ({placeholders})
        """,
        artist_keys,
    ).fetchall()
    by_artist: dict[str, dict[str, Any]] = {k: {"factors": {}} for k in artist_keys}
    for artist_key, factor_name, value, as_of in factors:
        entry = by_artist[artist_key]["factors"].setdefault(factor_name, {})
        if value is not None:
            entry["value"] = float(value)
        if as_of and (entry.get("as_of") is None or str(as_of) > str(entry["as_of"])):
            entry["as_of"] = str(as_of)[:10]

    # live statistics (real performance evidence)
    live = conn.execute(
        f"""
        SELECT artist_key, shows_365d, festival_appearances_365d, markets_365d,
               unique_venues_365d, days_since_last_show, as_of
        FROM metrics.artist_live_statistics
        WHERE artist_key IN ({placeholders})
        """,
        artist_keys,
    ).fetchall()
    for artist_key, shows_365, festivals, markets, venues, days_since, as_of in live:
        by_artist[artist_key]["live"] = {
            "shows_365d": shows_365,
            "festival_appearances": festivals,
            "markets_played": markets,
            "unique_venues": venues,
            "days_since_last_show": days_since,
            "as_of": str(as_of)[:10] if as_of else None,
        }

    # catalog statistics (real release evidence)
    catalog = conn.execute(
        f"""
        SELECT artist_key, releases_12m, days_since_last_release, catalog_depth, as_of
        FROM metrics.artist_catalog_statistics
        WHERE artist_key IN ({placeholders})
        """,
        artist_keys,
    ).fetchall()
    for artist_key, releases_12m, days_since, depth, as_of in catalog:
        by_artist[artist_key]["catalog"] = {
            "releases_12m": releases_12m,
            "days_since_last_release": days_since,
            "catalog_depth": depth,
            "as_of": str(as_of)[:10] if as_of else None,
        }

    # artist×market + ticket evidence
    market = conn.execute(
        f"""
        SELECT artist_key, market_key, factor_name, value
        FROM metrics.artist_market_factor_observations
        WHERE artist_key IN ({placeholders})
        """,
        artist_keys,
    ).fetchall()
    markets_played_map: dict[str, set[str]] = {}
    ticket_map: dict[str, int] = {}
    for artist_key, market_key, factor_name, value in market:
        if factor_name == "TICKET_OBSERVATIONS" and value:
            ticket_map[artist_key] = int(value)
        if market_key:
            markets_played_map.setdefault(artist_key, set()).add(market_key)

    # identity coverage (verified providers per artist) → data confidence proxy
    identity = conn.execute(
        f"""
        SELECT artist_key, COUNT(*) AS n_verified
        FROM identity.artist_provider_linkages
        WHERE artist_key IN ({placeholders}) AND resolution_status = 'VERIFIED'
        GROUP BY artist_key
        """,
        artist_keys,
    ).fetchall()
    identity_map = {r[0]: int(r[1]) for r in identity}

    out: list[dict[str, Any]] = []
    for artist_key in artist_keys:
        data = by_artist[artist_key]
        factors_map = data.get("factors", {})
        live = data.get("live") or {}
        catalog = data.get("catalog") or {}

        def fv(name: str) -> float | None:
            return factors_map.get(name, {}).get("value")

        latest_updates = [
            u for u in (
                factors_map.get("WIKI_VIEWS_28D", {}).get("as_of"),
                live.get("as_of"),
                catalog.get("as_of"),
            ) if u
        ]
        latest_update = max(latest_updates) if latest_updates else None
        n_verified = identity_map.get(artist_key, 0)
        confidence = round(min(1.0, 0.4 + 0.1 * n_verified), 2) if n_verified else None
        markets = markets_played_map.get(artist_key, set())
        out.append({
            "artist": artist_key,
            "factor_coverage": len({k.split("_")[0] for k in factors_map}),
            "lb_momentum": fv("LB_LISTEN_VELOCITY") or fv("LB_LISTENS_7D"),
            "wiki_momentum": fv("WIKI_MOMENTUM"),
            "yt_momentum": fv("YT_SUBSCRIBERS"),
            "shows_365d": live.get("shows_365d"),
            "festival_appearances": live.get("festival_appearances"),
            "catalog_recency": catalog.get("days_since_last_release"),
            "ticket_observations": ticket_map.get(artist_key),
            "markets_played": len(markets) if markets else None,
            "latest_update": latest_update,
            "data_confidence": confidence,
        })
    return out


def _semantics_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    populated = {c: sum(1 for r in rows if r.get(c) is not None) for c in COLUMNS}
    return {
        "rows": len(rows),
        "columns_present": [c for c in COLUMNS if populated[c] > 0],
        "columns_populated": populated,
        "sortable": {c: populated[c] > 1 for c in COLUMNS},
        "filterable": {c: len({r.get(c) for r in rows}) > 1 for c in COLUMNS},
        "pivot_groups": {
            "factor_coverage": sorted({r.get("factor_coverage") for r in rows}),
        },
        "searchable": populated["artist"] > 0,
    }


def run_monitor(
    conn,
    *,
    artist_keys: list[str] | None = None,
    out_path: str | None = None,
) -> dict[str, Any]:
    """Export the real monitor table + semantics; optionally write JSON."""
    rows = export_monitor_rows(conn, artist_keys=artist_keys)
    semantics = _semantics_report(rows)
    result = {
        "status": "COMPLETE",
        "monitor_version": MONITOR_VERSION,
        "semantics": semantics,
        "monitor_ready": semantics["rows"] > 0,
        "perspective_note": (
            "Perspective table renders these rows via Arrow/JSON interchange; "
            "sort/filter/pivot/group/search are Perspective-native. Internal "
            "analyst monitor only — not a product UI."
        ),
    }
    if out_path:
        import pathlib

        pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(out_path).write_text(
            json.dumps({"schema": list(COLUMNS), "rows": rows}, indent=2, default=str),
            encoding="utf-8",
        )
        result["export_path"] = out_path
    return result
