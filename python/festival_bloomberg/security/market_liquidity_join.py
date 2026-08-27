"""MARKET_LIQUIDITY_TAPE_V1 — P8: product join into artist × market security.

For each ARTIST × MARKET row in ``asm.artist_market_security_v1`` expose
descriptive market-liquidity evidence:

* marketplace_count — number of marketplaces with real observations
* price_observation_count — number of TM price observations in that market
* latest_tm_standard_min / latest_tm_standard_max / latest_tm_onsale_state
* latest_market_evidence_at / price_evidence_freshness_days
* seatgeek_listing_count / lowest / average / highest (where a real SG rail exists)

No predictive demand score. No booking recommendation. Purely descriptive.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

SOFTWARE_VERSION = "market_liquidity_tape_v1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def join_market_liquidity_into_security(conn) -> dict[str, Any]:
    """Update artist×market rows with the latest market-liquidity evidence.

    Idempotent: only fills NEW columns; never overwrites the observable
    artist/market factors derived by the security master.
    """
    # Aggregate market-price observations per (artist_key, market_key)
    price_rows = conn.execute(
        """
        SELECT artist_key, market_key,
               COUNT(*)                                  AS n_obs,
               COUNT(DISTINCT marketplace)                AS n_marketplaces,
               MAX(retrieved_at)                          AS latest_at,
               arg_max(standard_primary_min, retrieved_at) AS latest_min,
               arg_max(standard_primary_max, retrieved_at) AS latest_max,
               arg_max(COALESCE(availability_state, event_status), retrieved_at) AS latest_state
        FROM acquisition.market_price_observations
        WHERE artist_key IS NOT NULL AND market_key IS NOT NULL
        GROUP BY artist_key, market_key
        """
    ).fetchall()
    price_map: dict[tuple[str, str], dict[str, Any]] = {}
    for artist_key, market_key, n_obs, n_mp, latest_at, latest_min, latest_max, latest_state in price_rows:
        price_map[(artist_key, market_key)] = {
            "n_obs": int(n_obs), "n_mp": int(n_mp), "latest_at": latest_at,
            "min": latest_min, "max": latest_max, "state": latest_state,
        }

    # SeatGeek event-level public stats (where a future authorized SG rail wrote them)
    sg_rows = conn.execute(
        """
        SELECT artist_key, market_key,
               MAX(retrieved_at)                       AS sg_latest,
               arg_max(lowest_public_offer, retrieved_at)  AS sg_low,
               arg_max(average_public_offer, retrieved_at) AS sg_avg,
               arg_max(highest_public_offer, retrieved_at) AS sg_high,
               arg_max(listing_count, retrieved_at)        AS sg_listings
        FROM acquisition.market_price_observations
        WHERE marketplace = 'seatgeek'
          AND artist_key IS NOT NULL AND market_key IS NOT NULL
        GROUP BY artist_key, market_key
        """
    ).fetchall()
    sg_map: dict[tuple[str, str], dict[str, Any]] = {}
    for artist_key, market_key, _latest, low, avg, high, listings in sg_rows:
        sg_map[(artist_key, market_key)] = {
            "low": low, "avg": avg, "high": high, "listings": listings,
        }

    # update each artist×market row
    rows = conn.execute(
        "SELECT row_key, artist_key, market_key FROM asm.artist_market_security_v1"
    ).fetchall()
    as_of = date.today()
    updated = 0
    for row_key, artist_key, market_key in rows:
        pr = price_map.get((artist_key, market_key))
        sg = sg_map.get((artist_key, market_key))
        if not pr and not sg:
            continue
        counts = _count_by_market(conn, artist_key, market_key)
        freshness = None
        if pr and pr.get("latest_at"):
            freshness = max(0, (as_of - pr["latest_at"].date()).days) if hasattr(pr["latest_at"], "date") else None
        conn.execute(
            """
            UPDATE asm.artist_market_security_v1
            SET marketplace_count          = COALESCE(?, marketplace_count),
                price_observation_count    = COALESCE(?, price_observation_count),
                latest_tm_standard_min     = COALESCE(?, latest_tm_standard_min),
                latest_tm_standard_max     = COALESCE(?, latest_tm_standard_max),
                latest_tm_onsale_state     = COALESCE(?, latest_tm_onsale_state),
                latest_market_evidence_at  = COALESCE(?, latest_market_evidence_at),
                price_evidence_freshness_days = COALESCE(?, price_evidence_freshness_days),
                seatgeek_listing_count     = COALESCE(?, seatgeek_listing_count),
                seatgeek_lowest_price      = COALESCE(?, seatgeek_lowest_price),
                seatgeek_average_price     = COALESCE(?, seatgeek_average_price),
                seatgeek_highest_price     = COALESCE(?, seatgeek_highest_price)
            WHERE row_key = ?
            """,
            [
                (pr.get("n_mp") if pr else None),
                (pr.get("n_obs") if pr else None),
                (pr.get("min") if pr else None),
                (pr.get("max") if pr else None),
                (pr.get("state") if pr else None),
                (pr.get("latest_at") if pr else None),
                freshness,
                (sg.get("listings") if sg else None),
                (sg.get("low") if sg else None),
                (sg.get("avg") if sg else None),
                (sg.get("high") if sg else None),
                row_key,
            ],
        )
        updated += 1
    return {
        "status": "COMPLETE",
        "rows_updated": updated,
        "artists_with_price_evidence": len(price_map),
        "markets_with_sg_evidence": len(sg_map),
        "software_version": SOFTWARE_VERSION,
    }


def _count_by_market(conn, artist_key: str, market_key: str) -> dict[str, int]:
    """Sanity counts — not strictly needed beyond weighted tallies."""
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM acquisition.market_price_observations WHERE artist_key = ? AND market_key = ?",
            [artist_key, market_key],
        ).fetchone()[0]
    except Exception:  # noqa: BLE001
        n = 0
    return {"obs": int(n)}