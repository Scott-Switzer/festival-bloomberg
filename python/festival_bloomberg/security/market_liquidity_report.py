"""MARKET_LIQUIDITY_TAPE_V1 — success report + rights/cost scorecard (P10).

Assembles the milestone's SUCCESS TARGET over real collected data:

* canonical events considered, exact TM/SG/SH mappings
* multi-marketplace distribution, active pairs, observation depth percentiles
* price observations by marketplace (listing / low / avg / high)
* standard primary ranges vs current-available inventory ranges
* PIT event-marketplace-days, pair depth distribution
* cash cost / browser cost / Monid cost
* rights status
* forward artist-tape status
* per-provider credential/api-authorization/cost/commercial-rights scorecard
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any


def build_market_liquidity_report(conn, *, stages: dict[str, Any] | None = None) -> dict[str, Any]:
    stages = stages or {}
    as_of = date.today().isoformat()
    out: dict[str, Any] = {"status": "COMPLETE", "as_of": as_of, "milestone": "MARKET_LIQUIDITY_TAPE_V1"} | stages

    # ---- Canonical events considered / mappings ----
    out["canonical_events_considered"] = {
        "in_tape": _count(conn, "acquisition.event_tape_scale"),
    }
    out["marketplace_mappings"] = {
        "ticketmaster_exact": _mapping_count(conn, "ticketmaster", "EXACT_PROVIDER_ID"),
        "seatgeek_exact": _mapping_count(conn, "seatgeek", "EXACT_PROVIDER_ID"),
        "stubhub_exact": _mapping_count(conn, "stubhub", "EXACT_PROVIDER_ID"),
        "all": _count(conn, "acquisition.event_identifiers"),
    }
    out["multi_marketplace"] = {
        "events_2_plus": _flag_count(conn, "multi_marketplace_events"),
        "events_3_plus": _flag_count(conn, "pairs_3_plus"),
        "events_5_plus": _flag_count(conn, "pairs_5_plus"),
        "events_10_plus": _flag_count(conn, "pairs_10_plus"),
    }

    # ---- Price observations ----
    tele = _price_observation_telemetry(conn)
    out["price_observations"] = tele

    # ---- PIT event-marketplace-days + depth ----
    out["pit_event_marketplace_days"] = {
        "days_total": _sum(conn, "acquisition.event_tape_scale", "pit_event_marketplace_days"),
        "observation_depth_total": _sum(conn, "acquisition.event_tape_scale", "observation_depth"),
    }
    out["pair_depth_distribution"] = _tape_distribution(conn)

    # ---- Costs / rights scorecard (P10) ----
    out["provider_scorecard"] = _provider_scorecard(conn)

    # ---- Forward artist tape (P7) ----
    out["forward_artist_tape"] = _forward_tape_status(conn)

    # ---- Rights ----
    out["rights_status"] = {
        "default": "TERMS_REVIEW_REQUIRED",
        "commercial_use": "PROTOTYPE_ONLY",
        "note": "No inference of attendance/sales; listing-count change is NEVER a sale; availability ≠ commercial permission",
    }
    return out


def _count(conn, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def _sum(conn, table: str, col: str) -> int:
    try:
        return int(conn.execute(f"SELECT COALESCE(SUM({col}), 0) FROM {table}").fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def _flag_count(conn, col: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM acquisition.event_tape_scale WHERE {col}").fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def _mapping_count(conn, marketplace: str, status: str) -> int:
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM acquisition.event_identifiers WHERE marketplace = ? AND mapping_status = ?",
                [marketplace, status],
            ).fetchone()[0]
        )
    except Exception:  # noqa: BLE001
        return 0


def _price_observation_telemetry(conn) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT event_key),
                   COUNT(DISTINCT event_key || '|' || CAST(observed_at AS DATE)),
                   COUNT(lowest_public_offer), COUNT(average_public_offer),
                   COUNT(highest_public_offer), COUNT(standard_primary_min),
                   COUNT(current_available_min), COUNT(listing_count),
                   MIN(observed_at), MAX(observed_at)
            FROM acquisition.market_price_observations
            """
        ).fetchone()
        by_marketplace = {}
        for marketplace, n in conn.execute(
            "SELECT marketplace, COUNT(*) FROM acquisition.market_price_observations GROUP BY marketplace"
        ).fetchall():
            by_marketplace[marketplace] = int(n)
        return {
            "observations": int(row[0]),
            "events_with_observation": int(row[1]),
            "pit_event_marketplace_days": int(row[2]),
            "low_public_offer_observations": int(row[3]),
            "avg_public_offer_observations": int(row[4]),
            "high_public_offer_observations": int(row[5]),
            "standard_primary_range_observations": int(row[6]),
            "current_available_inventory_observations": int(row[7]),
            "listing_count_observations": int(row[8]),
            "first_observed_at": row[9].isoformat() if row[9] else None,
            "last_observed_at": row[10].isoformat() if row[10] else None,
            "by_marketplace": by_marketplace,
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "detail": str(e)[:300]}


def _tape_distribution(conn) -> dict[str, Any]:
    dist = {}
    total = 0
    try:
        rows = conn.execute(
            "SELECT marketplace_count, COUNT(*) FROM acquisition.event_tape_scale GROUP BY 1"
        ).fetchall()
        total = sum(int(r[1]) for r in rows)
        dist = {int(mp): int(n) for mp, n in rows}
    except Exception:  # noqa: BLE001
        pass
    return {"total_events": total, "counts_by_marketplace_count": dist}


def _provider_scorecard(conn) -> dict[str, Any]:
    try:
        rows = conn.execute(
            """
            SELECT provider, provider_kind, credential_state, auth_state,
                   api_calls, browser_calls, monid_calls, cost_usd,
                   useful_observations, detail, checked_at, rights_status,
                   commercial_use_status
            FROM acquisition.source_auth_status
            """
        ).fetchall()
        scorecard = {}
        for r in rows:
            key = f"{r[0]}.{r[1]}"
            scorecard[key] = {
                "credential_state": r[2], "auth_state": r[3],
                "api_calls": int(r[4]), "browser_calls": int(r[5]),
                "monid_calls": int(r[6]), "cost_usd": float(r[7]),
                "useful_observations": int(r[8]), "detail": r[9],
                "checked_at": r[10].isoformat() if r[10] else None,
                "rights_status": r[11], "commercial_use_status": r[12],
            }
        totals = {
            "cash_cost_usd": sum(float(v["cost_usd"]) for v in scorecard.values()),
            "browser_calls": sum(int(v["browser_calls"]) for v in scorecard.values()),
            "monid_calls": sum(int(v["monid_calls"]) for v in scorecard.values()),
            "oauth_or_api_calls": sum(int(v["api_calls"]) for v in scorecard.values()),
        }
        return {"providers": scorecard, "totals": totals}
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "detail": str(e)[:300]}


def _forward_tape_status(conn) -> dict[str, Any]:
    try:
        rows = conn.execute(
            """
            SELECT feed,
                   COUNT(*) FILTER (WHERE status = 'OBSERVED') AS observed,
                   COUNT(*) FILTER (WHERE status = 'BLOCKED') AS blocked,
                   MIN(period_date) AS min_period, MAX(period_date) AS max_period,
                   MEDIAN(freshness_days) AS med_fresh
            FROM metrics.artist_forward_tape
            GROUP BY feed
            """
        ).fetchall()
        feeds = {}
        for feed, observed, blocked, min_p, max_p, med_fresh in rows:
            feeds[feed] = {
                "observed": int(observed), "blocked": int(blocked),
                "first_period": min_p.isoformat() if min_p else None,
                "last_period": max_p.isoformat() if max_p else None,
                "median_freshness_days": int(med_fresh) if med_fresh is not None else None,
            }
        return {"feeds": feeds}
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "detail": str(e)[:300]}