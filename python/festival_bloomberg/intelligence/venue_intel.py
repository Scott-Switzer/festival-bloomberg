"""Venue intelligence: capacity claims + venue coverage.

Capacity is a CLAIM. Conflicting claims are never collapsed silently; every
claim carries its source and evidence class. Coordinates/geography are derived
with a derivation version; H3 cells are geography, never demand.
"""

from __future__ import annotations

import json
from typing import Any

CAPACITY_KINDS = (
    "MAXIMUM_VENUE_CAPACITY", "EVENT_USABLE_CAPACITY", "TICKET_CAPACITY",
    "SEATED_CAPACITY", "GA_CAPACITY", "UNKNOWN_CAPACITY_TYPE",
)


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def venue_capacity_profile(conn, *, venue_key: str) -> dict[str, Any]:
    """Consolidated capacity + geography profile for one venue.

    Returns all distinct claims (never merged), the resolved canonical venue
    row, and derived geography with a derivation version.
    """
    venues = _rows(conn, "SELECT * FROM core.venues WHERE venue_key = ?", [venue_key])
    if not venues:
        return {"venue_key": venue_key, "status": "UNKNOWN"}
    v = venues[0]
    claims = _rows(
        conn,
        """
        SELECT claim_id, capacity_value, capacity_kind, configuration_description,
               provider, source, source_url, effective_from, effective_to,
               knowledge_time, claim_status, usage_label
        FROM economics.venue_capacity_claims
        WHERE canonical_venue_id = ?
        ORDER BY COALESCE(effective_from, '9999') , knowledge_time
        """,
        [venue_key],
    )
    kinds: dict[str, list[float]] = {}
    for c in claims:
        kinds.setdefault(c["capacity_kind"], []).append(c["capacity_value"])
    # A conflict is a kind with >1 DISTINCT non-null value (never collapsed).
    conflicting = any(
        len({v for v in vals if v is not None}) > 1
        for vals in kinds.values()
    )
    capacity = {
        "claim_count": len(claims),
        "claims": claims,
        "conflicting_claims": conflicting,
        "by_kind": {
            kind: {
                "values": vals,
                "min": min(vals), "max": max(vals),
                "latest": vals[-1] if vals else None,
            }
            for kind, vals in kinds.items()
            if vals and any(val is not None for val in vals)
        },
    }
    if v.get("capacity") is not None:
        capacity.setdefault("by_kind", {}).setdefault("MAXIMUM_VENUE_CAPACITY", {
            "values": [v["capacity"]], "min": v["capacity"],
            "max": v["capacity"], "latest": v["capacity"],
        })
        capacity["claim_count"] = max(capacity["claim_count"], 1)

    lat, lon = v.get("latitude"), v.get("longitude")
    geo = {
        "latitude": lat, "longitude": lon,
        "coordinate_source": v.get("source_system"),
        "derivation_version": "venue_intel_v1",
        "market": v.get("city"),
        "region": v.get("region"), "country": v.get("country"),
        "h3": None,  # H3 derivation deferred: cells are geography, never demand
    }
    return {
        "venue_key": venue_key,
        "name": v.get("name"),
        "venue_type": v.get("venue_type"),
        "indoor_outdoor": ("OUTDOOR" if v.get("is_outdoor") else
                           "INDOOR" if v.get("is_outdoor") is False else "UNKNOWN"),
        "capacity": capacity,
        "geography": geo,
        "status": "OBSERVED",
    }


def venue_coverage(conn) -> dict[str, Any]:
    """Coverage percentages over the canonical venue master."""
    total = int(conn.execute("SELECT COUNT(*) FROM core.venues").fetchone()[0])
    if total == 0:
        return {"venue_count": 0, "coverage": {}}
    rows = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(capacity) AS with_capacity,
            COUNT(latitude) AS with_coords,
            COUNT(city) AS with_market,
            COUNT(ticketmaster_id) AS with_tm_id,
            COUNT(musicbrainz_id) AS with_mb_id
        FROM core.venues
        """
    ).fetchone()
    cols = [c[0] for c in conn.description]
    counts = dict(zip(cols, rows))
    claimed = int(conn.execute(
        "SELECT COUNT(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims"
    ).fetchone()[0])
    return {
        "venue_count": total,
        "coverage": {
            "capacity_column": round(counts["with_capacity"] / total, 4),
            "capacity_claims": round(claimed / total, 4),
            "coordinates": round(counts["with_coords"] / total, 4),
            "market": round(counts["with_market"] / total, 4),
            "ticketmaster_id": round(counts["with_tm_id"] / total, 4),
            "musicbrainz_id": round(counts["with_mb_id"] / total, 4),
        },
        "capacity_claim_count": int(conn.execute(
            "SELECT COUNT(*) FROM economics.venue_capacity_claims").fetchone()[0]),
    }
