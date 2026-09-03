"""Tiered artist collection policy.

The planner chooses acquisition cadence; the existing Monid/Governor queue
remains the execution and budget authority. Tier decisions are deterministic,
explainable, and never imply artist quality or ticket demand.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class CollectionTier(IntEnum):
    """Higher priority means a shorter collection interval."""

    COVERAGE_25000 = 4
    CORE_5000 = 3
    HOT_1000 = 2
    HOT_100 = 1


TIER_LIMITS: dict[CollectionTier, int] = {
    CollectionTier.HOT_100: 100,
    CollectionTier.HOT_1000: 1000,
    CollectionTier.CORE_5000: 5000,
    CollectionTier.COVERAGE_25000: 25000,
}

CADENCE_HOURS: dict[CollectionTier, int] = {
    CollectionTier.HOT_100: 1,
    CollectionTier.HOT_1000: 24,
    CollectionTier.CORE_5000: 72,
    CollectionTier.COVERAGE_25000: 168,
}

PROMOTION_SIGNALS = (
    "forward_event",
    "shortlist",
    "watchlist",
    "attention_shock",
    "major_release",
    "festival_announcement",
    "private_buyer_interest",
)


@dataclass(frozen=True)
class PromotionSignal:
    name: str
    active: bool
    evidence_ref: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True)
class TierDecision:
    artist_key: str
    tier: str
    cadence_hours: int
    signals: tuple[PromotionSignal, ...]
    reason: str

    @property
    def active_signals(self) -> tuple[str, ...]:
        return tuple(signal.name for signal in self.signals if signal.active)

    def as_dict(self) -> dict[str, Any]:
        return {
            "artist_key": self.artist_key,
            "tier": self.tier,
            "cadence_hours": self.cadence_hours,
            "active_signals": list(self.active_signals),
            "signals": [
                {
                    "name": signal.name,
                    "active": signal.active,
                    "evidence_ref": signal.evidence_ref,
                    "observed_at": signal.observed_at,
                }
                for signal in self.signals
            ],
            "reason": self.reason,
        }


def _signal(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value > 0
    return bool(value)


def decide_tier(
    artist: dict[str, Any],
    *,
    default_tier: CollectionTier = CollectionTier.COVERAGE_25000,
) -> TierDecision:
    """Return the highest requested tier supported by explicit signals.

    A requested ``tier`` on the artist is honored as a floor only when it is a
    known configured tier. Active promotion signals move the artist up to
    ``HOT_100`` for high-frequency monitoring; ordinary buyer/watchlist or
    event signals move it to ``HOT_1000``. No signal means the configured
    baseline remains in force.
    """
    artist_key = str(artist.get("artist_key") or artist.get("key") or "")
    requested = _coerce_tier(artist.get("tier"), default_tier)
    raw_signals = artist.get("signals") or {}
    signals: list[PromotionSignal] = []
    for name in PROMOTION_SIGNALS:
        raw = raw_signals.get(name) if isinstance(raw_signals, dict) else None
        if isinstance(raw, dict):
            active = _signal(raw.get("active"), name)
            evidence_ref = raw.get("evidence_ref") or raw.get("source_url")
            observed_at = raw.get("observed_at") or raw.get("knowledge_time")
        else:
            active = _signal(raw if raw is not None else artist.get(name), name)
            evidence_ref = None
            observed_at = None
        signals.append(PromotionSignal(name, active, evidence_ref, observed_at))

    active = {signal.name for signal in signals if signal.active}
    if {
        "attention_shock",
        "major_release",
        "festival_announcement",
        "private_buyer_interest",
    } & active:
        target = CollectionTier.HOT_100
        reason = "high-frequency attention/release/festival/buyer signal"
    elif {"forward_event", "shortlist", "watchlist"} & active:
        target = CollectionTier.HOT_1000
        reason = "daily core collection signal"
    else:
        target = requested
        reason = "configured baseline cadence"
    # IntEnum ordering is inverse priority: HOT_100 is 1. Do not demote an
    # explicitly configured hot tier because a separate signal is absent.
    chosen = min(requested, target)
    return TierDecision(
        artist_key=artist_key,
        tier=chosen.name,
        cadence_hours=CADENCE_HOURS[chosen],
        signals=tuple(signals),
        reason=reason,
    )


def _coerce_tier(value: Any, fallback: CollectionTier) -> CollectionTier:
    if isinstance(value, CollectionTier):
        return value
    text = str(value or "").strip().upper()
    for tier in CollectionTier:
        if text == tier.name:
            return tier
    return fallback


def plan_collection(
    artists: Iterable[dict[str, Any]],
    *,
    limit: int | None = None,
    default_tier: CollectionTier = CollectionTier.COVERAGE_25000,
) -> dict[str, Any]:
    """Build a bounded deterministic plan grouped by collection tier."""
    decisions = [decide_tier(artist, default_tier=default_tier) for artist in artists]
    decisions.sort(key=lambda decision: (CollectionTier[decision.tier], decision.artist_key))
    if limit is not None:
        decisions = decisions[: max(0, int(limit))]
    by_tier: dict[str, list[dict[str, Any]]] = {tier.name: [] for tier in CollectionTier}
    for decision in decisions:
        by_tier[decision.tier].append(decision.as_dict())
    return {
        "policy_version": "artist_collection_tiering_v1",
        "planned_count": len(decisions),
        "by_tier": by_tier,
        "cadence_hours": {tier.name: CADENCE_HOURS[tier] for tier in CollectionTier},
        "tier_limits": {tier.name: TIER_LIMITS[tier] for tier in CollectionTier},
        "governor_budget_required": True,
    }
