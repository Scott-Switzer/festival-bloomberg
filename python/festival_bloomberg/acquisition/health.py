"""Provider health registry and simple circuit breaker.

Tracks consecutive failures per provider. A provider that exceeds the
failure threshold is marked unhealthy and skipped by the router until it is
explicitly reset or the threshold window elapses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import ProviderHealth, utc_now


@dataclass
class ProviderHealthRegistry:
    failure_threshold: int = 3
    _state: dict[str, ProviderHealth] = field(default_factory=dict)

    def record_success(self, provider: str) -> None:
        self._state[provider] = ProviderHealth(
            provider=provider,
            healthy=True,
            last_error=None,
            consecutive_failures=0,
            last_success_at=utc_now(),
        )

    def record_failure(self, provider: str, error: str | None = None) -> None:
        previous = self._state.get(provider)
        failures = (previous.consecutive_failures if previous else 0) + 1
        self._state[provider] = ProviderHealth(
            provider=provider,
            healthy=failures < self.failure_threshold,
            last_error=error,
            consecutive_failures=failures,
            last_success_at=previous.last_success_at if previous else None,
        )

    def is_healthy(self, provider: str) -> bool:
        state = self._state.get(provider)
        if state is None:
            return True
        return state.healthy

    def reset(self, provider: str) -> None:
        self._state.pop(provider, None)

    def health(self, provider: str) -> ProviderHealth:
        return self._state.get(
            provider,
            ProviderHealth(provider=provider, healthy=True),
        )
