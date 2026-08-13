"""Budget enforcement for acquisition.

The default session budget is exactly ``$0.00``: no provider call may spend
money unless an explicit environment flag (``ALLOW_PAID_PROVIDER_SMOKE=1``)
or an explicit budget is configured. Unknown costs are treated as
``None`` (never fabricated); a provider reporting no cost is assumed free
for budget purposes but its cost is recorded as unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionBudget:
    """Tracks spending across a session and enforces a hard cap."""

    max_cost_usd: float = 0.0
    spent_usd: float = 0.0
    charges: list[dict] = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return self.max_cost_usd - self.spent_usd

    def can_afford(self, estimated_cost_usd: float | None) -> bool:
        """True when the estimate fits in the remaining budget.

        ``None`` (unknown cost) is treated as free for gating, but is
        recorded as unknown in telemetry — never as zero.
        """
        if estimated_cost_usd is None:
            return True
        return estimated_cost_usd <= self.remaining + 1e-9

    def charge(self, provider: str, request_id: str, cost_usd: float | None) -> None:
        if cost_usd is None:
            self.charges.append(
                {"provider": provider, "request_id": request_id, "cost_usd": None}
            )
            return
        self.spent_usd += cost_usd
        self.charges.append(
            {"provider": provider, "request_id": request_id, "cost_usd": cost_usd}
        )


def paid_providers_allowed() -> bool:
    """True only when the explicit smoke-test flag is set."""
    import os

    return os.environ.get("ALLOW_PAID_PROVIDER_SMOKE", "0") == "1"
