"""Deterministic acquisition router for ticket-market observations.

No LLM decides the source. A measured policy routes each observation:

    MONID_FAST        — cheap high-frequency event-level snapshots (JSON-LD /
                        structured page state). Cost ~$0.0009/call.
    TICKETS_DEV_DEEP  — lower-frequency high-information captures: listing
                        level state, fee normalization, section/row/quantity,
                        primary vs resale, seat maps. $0.02–0.05/capture live.
    APIFY_FALLBACK    — specialist Actor only when empirically it beats both.

Policy hypothesis (must be tested, not assumed):
    * Event-level price/availability daily  -> MONID_FAST
    * Listing-level deep capture (weekly /
      T-30/T-14/T-7/T-3/T-1)                -> TICKETS_DEV_DEEP
    * Everything else                       -> measured fallback

Rights: all marketplace-page acquisition remains TERMS_REVIEW_REQUIRED unless
cleared. Publicly accessible != commercially licensed.
"""

from __future__ import annotations

from typing import Any

# Measured economics (2026-08-25 probes).
MEASURED_COST = {
    "MONID_HTML": 0.0009,       # context.dev/web/scrape/html per call
    "MONID_FETCH": 0.0,         # tinyfish/fetch free tier
    "TICKETS_DEV_CAPTURE": 0.03,  # advertised $0.02-0.05; mid assumption
    "APIFY_ACTOR": 0.45,        # measured minimum charge per actor run
}

RAIL_FAST = "FAST"
RAIL_DEEP = "DEEP"


def route_observation(
    *,
    marketplace: str,
    has_mapped_url: bool,
    needs_listings: bool = False,
    cadence: str = "daily",
    tickets_dev_live_key: bool = False,
    monid_available: bool = True,
) -> dict[str, Any]:
    """Pick a deterministic acquisition method for one (event × marketplace).

    Returns {method, rail, provider, cost_per_call, reason}.
    """
    if needs_listings or cadence in ("weekly", "milestone", "T_minus"):
        if tickets_dev_live_key:
            return {
                "method": "TICKETS_DEV_DEEP",
                "rail": RAIL_DEEP,
                "provider": "tickets.dev",
                "cost_per_call": MEASURED_COST["TICKETS_DEV_CAPTURE"],
                "reason": "listing-level capture required; tickets.dev returns the only normalized listing contract",
            }
        if monid_available and has_mapped_url:
            return {
                "method": "MONID_FAST_DEEP_FALLBACK",
                "rail": RAIL_DEEP,
                "provider": "monid",
                "cost_per_call": MEASURED_COST["MONID_HTML"],
                "reason": "no live tickets.dev key; Monid HTML is the working fallback for event-level state",
            }
        return {
            "method": "APIFY_FALLBACK",
            "rail": RAIL_DEEP,
            "provider": "apify",
            "cost_per_call": MEASURED_COST["APIFY_ACTOR"],
            "reason": "fallback only; actors measured at ~$0.45/run with filter-ignore risk",
        }

    # FAST rail — event-level state, high frequency.
    if monid_available:
        if has_mapped_url:
            return {
                "method": "MONID_FAST",
                "rail": RAIL_FAST,
                "provider": "monid",
                "cost_per_call": MEASURED_COST["MONID_HTML"],
                "reason": "known URL + event-level JSON-LD extraction is the cheapest measured path",
            }
        return {
            "method": "MONID_FAST_UNMAPPED",
            "rail": RAIL_FAST,
            "provider": "monid",
            "cost_per_call": 0.0,
            "reason": "no URL mapping yet; tinyfish/search is free but resolution is one-time work",
        }
    return {
        "method": "APIFY_FALLBACK",
        "rail": RAIL_FAST,
        "provider": "apify",
        "cost_per_call": MEASURED_COST["APIFY_ACTOR"],
        "reason": "Monid unavailable; fallback to actor",
    }


def monthly_cost(
    events: int,
    *,
    fast_per_day: int = 1,
    deep_per_month: int = 4,
    days: int = 30,
    route: dict[str, Any] | None = None,
    tickets_dev_live_key: bool = False,
) -> dict[str, Any]:
    """Project monthly cost for a universe under the routing policy.

    FAST rail: events × fast_per_day × days × MONID_HTML cost.
    DEEP rail: events × deep_per_month × TICKETS_DEV cost (or Monid fallback).
    """
    r = route or route_observation(
        marketplace="*",
        has_mapped_url=True,
        needs_listings=True,
        cadence="weekly",
        tickets_dev_live_key=tickets_dev_live_key,
    )
    fast_cost = events * fast_per_day * days * MEASURED_COST["MONID_HTML"]
    deep_cc = (
        MEASURED_COST["TICKETS_DEV_CAPTURE"]
        if tickets_dev_live_key else MEASURED_COST["MONID_HTML"]
    )
    deep_cost = events * deep_per_month * deep_cc
    return {
        "events": events,
        "fast": {
            "per_day": fast_per_day,
            "calls_per_month": events * fast_per_day * days,
            "cost_usd": round(fast_cost, 2),
        },
        "deep": {
            "per_month": deep_per_month,
            "calls_per_month": events * deep_per_month,
            "cost_usd": round(deep_cost, 2),
            "provider": "tickets.dev" if tickets_dev_live_key else "monid_fallback",
        },
        "total_monthly_usd": round(fast_cost + deep_cost, 2),
        "cost_per_event_month": round((fast_cost + deep_cost) / events, 3) if events else 0,
        "disclaimer": "listing/ticket counts are availability proxies, never tickets sold.",
    }


def deep_cadence(proposed_date: str | None, now: str | None = None) -> str:
    """Pick the deep-rail cadence relative to the event (T-minus buckets)."""
    from datetime import date, datetime, timezone

    if not proposed_date:
        return "weekly"
    try:
        d = date.fromisoformat(str(proposed_date)[:10])
        today = (
            datetime.now(timezone.utc).date()
            if now is None else date.fromisoformat(str(now)[:10])
        )
        days_out = (d - today).days
    except (ValueError, TypeError):
        return "weekly"
    if days_out <= 3:
        return "daily"
    if days_out <= 7:
        return "T-7"
    if days_out <= 14:
        return "T-14"
    if days_out <= 30:
        return "T-30"
    return "weekly"
