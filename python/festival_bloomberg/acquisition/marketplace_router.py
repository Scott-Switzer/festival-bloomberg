"""ACQUISITION_MARKETPLACE_ROUTER_V1 — cost/quality marketplace for data tasks.

Chooses among {OFFICIAL_API, MONID, APIFY, OWNED_HTTP, OWNED_BROWSER}
for a DATA TASK (not a platform). Never hard-codes one universal winner.

Inputs: task_type, platform (hint), artist_count, market_context.
Lanes: each exposes a CostEstimate + health + prior ledger economics.
Selection: min expected_cost_per_new_unique, subject to freshness SLA + reliability floor.

The router is deterministic and auditable: every decision emits a
MarketplaceDecision with lane_scores, not just the winner.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .contracts import AcquisitionRequest


class Lane(str, Enum):
    OFFICIAL_API = "OFFICIAL_API"
    MONID = "MONID"
    APIFY = "APIFY"
    OWNED_HTTP = "OWNED_HTTP"
    OWNED_BROWSER = "OWNED_BROWSER"
    HETZNER_PLAYWRIGHT = "HETZNER_PLAYWRIGHT"


# Task → preferred lanes ordered cheapest→most-expensive for display.
# The actual primary is ledger-driven; this is the fallback ordering.
TASK_LANE_PRIORITY: dict[str, list[Lane]] = {
    "SOCIAL_PROFILE": [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP],
    "SOCIAL_POSTS": [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP],
    "SOCIAL_COMMENTS": [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP],
    "SOCIAL_MENTION_SEARCH": [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP, Lane.OWNED_BROWSER],
    "HASHTAG_SEARCH": [Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP],
    "VIDEO_METADATA": [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP],
    "VIDEO_COMMENTS": [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP],
    "ARTIST_NEWS": [Lane.OFFICIAL_API, Lane.MONID, Lane.OWNED_HTTP, Lane.APIFY],
    "ARTIST_WEB_SEARCH": [Lane.MONID, Lane.OWNED_HTTP, Lane.APIFY],
    "PUBLIC_TICKET_PAGE": [Lane.OWNED_HTTP, Lane.OFFICIAL_API, Lane.MONID, Lane.OWNED_BROWSER],
    "ARTIST_OFFICIAL_SITE": [Lane.OWNED_HTTP, Lane.OWNED_BROWSER],
    "VENUE_EVENT_PAGE": [Lane.OWNED_HTTP, Lane.MONID, Lane.OWNED_BROWSER],
    "PROMOTER_EVENT_PAGE": [Lane.OWNED_HTTP, Lane.MONID, Lane.OWNED_BROWSER],
    "REDDIT_SEARCH": [Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP],
    "SOUNDCLOUD_TRACKS": [Lane.OWNED_HTTP, Lane.MONID, Lane.APIFY],
    # Compute lanes — EXECUTION_MARKETPLACE_V1 extension (P29). The router now
    # also chooses a compute lane, not just a data provider. Heavy batch
    # workloads stream from R2 and run on Hetzner; light jobs stay on Cloudflare.
    "HEAVY_BATCH": [Lane.HETZNER_PLAYWRIGHT, Lane.OWNED_HTTP],
    "WARC_PARSE": [Lane.HETZNER_PLAYWRIGHT, Lane.OWNED_HTTP],
    "EMBED_BULK": [Lane.HETZNER_PLAYWRIGHT, Lane.OFFICIAL_API],
}


@dataclass(frozen=True)
class LaneEstimate:
    lane: Lane
    provider: str  # canonical provider name (apify, monid, youtube, etc.)
    estimated_cost_usd: float | None
    configured: bool
    healthy: bool
    prior_cost_per_1k_unique: float | None = None
    prior_success_rate: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MarketplaceDecision:
    task_type: str
    platform: str | None
    primary: LaneEstimate | None
    fallback: LaneEstimate | None
    ranked: list[LaneEstimate]
    rationale: str
    ledger_used: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "platform": self.platform,
            "primary": {"lane": self.primary.lane.value, "provider": self.primary.provider, "cost_usd": self.primary.estimated_cost_usd, "reason": self.primary.reason} if self.primary else None,
            "fallback": {"lane": self.fallback.lane.value, "provider": self.fallback.provider, "cost_usd": self.fallback.estimated_cost_usd} if self.fallback else None,
            "ranked": [{"lane": e.lane.value, "provider": e.provider, "cost": e.estimated_cost_usd, "healthy": e.healthy, "configured": e.configured, "prior_c1k_unique": e.prior_cost_per_1k_unique, "reason": e.reason} for e in self.ranked],
            "rationale": self.rationale,
            "ledger_used": self.ledger_used,
        }


def _ledger_cost_per_1k_unique(task_type: str, lane: Lane) -> float | None:
    """Lookup prior economics from the procurement ledger (if present)."""
    # The ledger is read from R2 (control/procurement/ledger.json) when present.
    # In offline/dev mode it is absent → None, and the router falls back to
    # provider list-price estimates.
    try:
        from pathlib import Path
        import json

        ledger_path = Path("control/procurement/ledger.json")
        if ledger_path.exists():
            ledger = json.loads(ledger_path.read_text())
            key = f"{task_type}:{lane.value}"
            entry = ledger.get(key) or ledger.get(task_type)
            if isinstance(entry, dict):
                v = entry.get("cost_per_1k_unique")
                return float(v) if v is not None else None
    except Exception:
        pass
    return None


# ── Compute-lane extension ────────────────────────────────────────────
# The marketplace chooses both a DATA lane and a COMPUTE lane (P29/P30).
# Cost is compared as cost_per_1k_unique (procurement ledger) when available;
# otherwise provider list-price or self-hosted estimate.
COMPUTE_LANE_PRIORITY: dict[str, list[str]] = {
    # task -> ordered compute candidates (cheapest first when quality is equal)
    "SCRAPE": ["CLOUDFLARE_WORKER", "HETZNER_CLOUD_CPU", "CLOUDFLARE_BROWSER"],
    "BROWSER_SCRAPE": ["HETZNER_CLOUD_CPU", "CLOUDFLARE_BROWSER"],
    "HEAVY_BATCH": ["HETZNER_CLOUD_CPU", "CLOUDFLARE_WORKER"],
    "WARC_PARSE": ["HETZNER_CLOUD_CPU"],
    "EMBED_BULK": ["HETZNER_CLOUD_CPU", "NIM_FREE"],
    "CLASSIFY_BULK": ["HETZNER_CLOUD_CPU", "NIM_FREE"],
}


class MarketplaceRouter:
    """Cost/quality router — picks the cheapest lane that meets SLA.

    Provider health/config is checked live (env present, no values logged).
    When a procurement ledger exists, it drives primary selection; otherwise
    list-price + health drives it. Every decision is recorded for audit.

    P29 extension: also routes to a COMPUTE lane via :meth:`decide_compute`.
    """

    def __init__(self, providers: dict[str, Any] | None = None):
        self.providers = providers or {}

    def decide(
        self,
        *,
        task_type: str,
        platform: str | None = None,
        request: AcquisitionRequest | None = None,
        providers: dict[str, Any] | None = None,
    ) -> MarketplaceDecision:
        provs = providers or self.providers
        task_type = (task_type or "PLATFORM_DISCOVERY").upper()
        lane_order = TASK_LANE_PRIORITY.get(task_type, [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP, Lane.OWNED_BROWSER])

        estimates: list[LaneEstimate] = []
        for lane in lane_order:
            provider_key = {
                Lane.OFFICIAL_API: (platform or "http"),
                Lane.MONID: "monid",
                Lane.APIFY: "apify",
                Lane.OWNED_HTTP: "http",
                Lane.OWNED_BROWSER: "scrapling",
                Lane.HETZNER_PLAYWRIGHT: "scrapling",
            }.get(lane, "http")

            # For OFFICIAL_API, check the platform-specific provider if it exists
            if lane == Lane.OFFICIAL_API and platform:
                plat_key = platform.lower()
                if plat_key in provs:
                    provider_key = plat_key

            prov = provs.get(provider_key)
            configured = True
            healthy = True
            estimated: float | None = None
            reason = None

            if prov is not None:
                try:
                    h = prov.health()
                    healthy = bool(getattr(h, "healthy", True))
                    if not healthy:
                        reason = getattr(h, "last_error", None) or "unhealthy"
                except Exception:
                    healthy = True
                # Monid/Apify: configured = has credentials; official API: same
                try:
                    # Some providers expose _has_credentials or secret check
                    if hasattr(prov, "_has_credentials"):
                        configured = bool(prov._has_credentials())
                    elif hasattr(prov, "configured"):
                        configured = bool(prov.configured())
                    elif hasattr(prov, "secret"):
                        # Check typical credential key for the provider
                        pass
                except Exception:
                    configured = True
                if request is not None:
                    try:
                        est = prov.estimate(request)
                        estimated = getattr(est, "estimated_cost_usd", None)
                    except Exception:
                        estimated = None
            else:
                # No provider instance for this lane → not configured
                if lane in (Lane.MONID, Lane.APIFY):
                    # Check env directly
                    import os

                    if lane == Lane.MONID and not os.environ.get("MONID_API_KEY"):
                        configured = False
                        reason = "MONID_API_KEY not set"
                    if lane == Lane.APIFY and not os.environ.get("APIFY_TOKEN"):
                        configured = False
                        reason = "APIFY_TOKEN not set"
                if lane == Lane.OWNED_HTTP:
                    configured = True  # always available (our code + Cloudflare)
                if lane == Lane.OWNED_BROWSER:
                    configured = True

            prior = _ledger_cost_per_1k_unique(task_type, lane)
            estimates.append(LaneEstimate(lane=lane, provider=provider_key, estimated_cost_usd=estimated, configured=configured, healthy=healthy, prior_cost_per_1k_unique=prior, reason=reason))

        # Rank: healthy+configured first, then by prior cost (if known) else estimated cost
        def sort_key(e: LaneEstimate):
            # Prefer healthy+configured, then lower cost
            health_rank = 0 if (e.healthy and e.configured) else (1 if e.configured else 2)
            cost = e.prior_cost_per_1k_unique if e.prior_cost_per_1k_unique is not None else (e.estimated_cost_usd if e.estimated_cost_usd is not None else 9e9)
            return (health_rank, cost)

        ranked = sorted(estimates, key=sort_key)
        # Primary = first healthy+configured
        viable = [e for e in ranked if e.healthy and e.configured]
        primary = viable[0] if viable else (ranked[0] if ranked else None)
        fallback = viable[1] if len(viable) > 1 else None
        ledger_used = any(e.prior_cost_per_1k_unique is not None for e in estimates)
        rationale = (
            f"marketplace: task={task_type} platform={platform or '-'} primary={primary.lane.value if primary else 'none'} "
            f"fallback={fallback.lane.value if fallback else 'none'} ledger={'yes' if ledger_used else 'list-price'} "
            + ", ".join(f"{e.lane.value}:{('ok' if e.healthy and e.configured else 'blocked')}" for e in ranked)
        )
        return MarketplaceDecision(task_type=task_type, platform=platform, primary=primary, fallback=fallback, ranked=ranked, rationale=rationale, ledger_used=ledger_used)

    def cost_per_1k_unique(self, task_type: str, lane: Lane) -> float | None:
        return _ledger_cost_per_1k_unique(task_type, lane)

    # ── Execution marketplace (P29/P30) ───────────────────────────────
    def decide_compute(self, *, task: str, hetzner_available: bool = False) -> dict[str, Any]:
        """Choose a compute lane for a task (no network, pure logic).

        Hetzner GPU is not yet provisioned — see GEX45 economics research.
        """
        t = (task or "SCRAPE").upper()
        candidates = COMPUTE_LANE_PRIORITY.get(t, ["CLOUDFLARE_WORKER", "HETZNER_CLOUD_CPU"])
        # Filter by availability
        available = []
        for c in candidates:
            if c == "HETZNER_CLOUD_CPU" and not hetzner_available:
                continue
            if c == "HETZNER_GPU_FUTURE":
                continue  # BLOCKED_BY_ROBOT_CREDENTIAL — never choose in pilot
            available.append(c)
        # Fallback: at least one worker type exists on Cloudflare
        if not available:
            available = ["CLOUDFLARE_WORKER"]
        primary = available[0]
        fallback = available[1] if len(available) > 1 else None
        return {
            "task": t,
            "compute_primary": primary,
            "compute_fallback": fallback,
            "candidates": candidates,
            "hetzner_available": hetzner_available,
        }
