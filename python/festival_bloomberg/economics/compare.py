"""PRIMARY_SECONDARY_MARKET_COMPARISON.

Not an arbitrage signal. Missing FX stays UNKNOWN — never a 1:1 fallback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

CONCEPT = "PRIMARY_SECONDARY_MARKET_COMPARISON"
COMPARABLE = "COMPARABLE"
PARTIALLY_COMPARABLE = "PARTIALLY_COMPARABLE"
NOT_COMPARABLE = "NOT_COMPARABLE"


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def compare_primary_secondary(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    fx_observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Descriptive comparison only. Never sets arbitrage_candidate."""
    p_cur = (primary.get("currency") or "").upper()
    s_cur = (secondary.get("currency") or "").upper()
    p_time = parse_dt(primary.get("retrieved_at") or primary.get("knowledge_time"))
    s_time = parse_dt(secondary.get("retrieved_at") or secondary.get("knowledge_time"))
    delta = None
    if p_time and s_time:
        delta = int(abs((s_time - p_time).total_seconds()))

    currency_consistency = "SAME" if p_cur and s_cur and p_cur == s_cur else "DIFFERENT_OR_MISSING"
    fx_conversion = "NOT_REQUIRED"
    if p_cur and s_cur and p_cur != s_cur:
        if fx_observation and fx_observation.get("rate") is not None:
            fx_conversion = "PIT_FX_APPLIED"
        else:
            fx_conversion = "UNKNOWN"

    fee_comparability = "UNKNOWN"
    if primary.get("fees_included") == "UNKNOWN" or secondary.get("fees_included") == "UNKNOWN":
        fee_comparability = "UNKNOWN"
    elif primary.get("fees_included") == secondary.get("fees_included"):
        fee_comparability = "SAME_DECLARATION"
    else:
        fee_comparability = "MISMATCHED"

    class_comparability = "UNKNOWN"
    if primary.get("price_type") and secondary.get("price_type"):
        class_comparability = (
            "SAME" if primary.get("price_type") == secondary.get("price_type") else "DIFFERENT"
        )
    else:
        class_comparability = "HETEROGENEOUS_OR_UNSPECIFIED"

    status = PARTIALLY_COMPARABLE
    if fx_conversion == "UNKNOWN":
        status = NOT_COMPARABLE
    elif fee_comparability == "MISMATCHED" or class_comparability == "DIFFERENT":
        status = NOT_COMPARABLE
    elif fee_comparability == "UNKNOWN" or class_comparability == "HETEROGENEOUS_OR_UNSPECIFIED":
        status = PARTIALLY_COMPARABLE
    elif currency_consistency == "SAME" and fee_comparability == "SAME_DECLARATION":
        status = COMPARABLE

    return {
        "concept": CONCEPT,
        "status": status,
        "primary_snapshot_id": primary.get("snapshot_id"),
        "secondary_snapshot_id": secondary.get("snapshot_id"),
        "timestamp_delta_seconds": delta,
        "currency_consistency": currency_consistency,
        "fee_comparability": fee_comparability,
        "class_comparability": class_comparability,
        "fx_conversion": fx_conversion,
        "ticketmaster_min": primary.get("minimum_price"),
        "ticketmaster_max": primary.get("maximum_price"),
        "seatgeek_low": secondary.get("lowest_price"),
        "seatgeek_average": secondary.get("average_price"),
        "seatgeek_high": secondary.get("highest_price"),
        "arbitrage_candidate": False,
        "no_1_to_1_fx_fallback": True,
    }
