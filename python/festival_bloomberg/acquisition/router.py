"""Deterministic, cost-aware acquisition router.

Routing priority (first provider that returns a real result wins):

1. approved official HTTP endpoint
2. configured approved Monid endpoint
3. configured approved direct Apify Actor
4. approved self-hosted Scrapling acquisition
5. explicit failure status

The router never retries forever (max one attempt per provider by default),
enforces the policy gate and session budget before any call, and can record
telemetry through a callback (e.g. the evidence repository).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable

from .contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    utc_now,
)
from .automation import automation_status, AutomationStatus
from .costs import SessionBudget
from .health import ProviderHealthRegistry
from .policy import PolicyGate

#: Default provider priority for a request without preferences.
DEFAULT_PRIORITY = ("http", "monid", "apify", "youtube", "scrapling")

#: Acquisition mechanism per provider (drives which policy dimensions apply).
MECHANISM_BY_PROVIDER = {
    "http": "api",
    "monid": "api",
    "apify": "api",
    "youtube": "api",
    "wikimedia": "api",
    "ticketmaster": "api",
    "spotify": "api",
    "setlistfm": "api",
    "seatgeek": "api",
    "wikidata": "api",
    "openstreetmap": "api",
    "nws": "api",
    "commoncrawl": "api",
    "google_trends": "api",
    "soundcharts": "api",
    "scrapling": "scraping",
}

TelemetryCallback = Callable[[AcquisitionRequest, AcquisitionResult], None]


class AcquisitionRouter:
    def __init__(
        self,
        providers: dict,
        *,
        policy_gate: PolicyGate | None = None,
        budget: SessionBudget | None = None,
        health: ProviderHealthRegistry | None = None,
        telemetry: TelemetryCallback | None = None,
        priority: tuple[str, ...] = DEFAULT_PRIORITY,
        max_attempts_per_provider: int = 1,
    ) -> None:
        self.providers = providers
        self.policy_gate = policy_gate or PolicyGate()
        self.budget = budget or SessionBudget()
        self.health = health or ProviderHealthRegistry()
        self.telemetry = telemetry
        self.priority = priority
        self.max_attempts_per_provider = max_attempts_per_provider

    # -- public ------------------------------------------------------------- #
    def route(self, request: AcquisitionRequest) -> AcquisitionResult:
        """Execute a request through the first viable provider."""
        disposition = automation_status(request.platform)
        if disposition == AutomationStatus.DISABLED:
            result = self._result(
                request,
                status=AcquisitionStatus.POLICY_DENIED,
                error_category="automation_disabled",
                provider_metadata={
                    "automation_status": disposition.value,
                    "rationale": "automated acquisition is disabled for this provider",
                },
            )
            self._emit(request, result)
            return result
        # coarse policy gate (API mechanism) before any provider work
        decision = self.policy_gate.evaluate(
            request.platform, request.commercial_context, mechanism="api"
        )
        if not decision.allowed:
            result = self._policy_result(request, decision.decision_id, decision.rationale)
            self._emit(request, result)
            return result

        # budget gate
        if not self.budget.can_afford(request.max_cost_usd):
            result = self._result(
                request,
                status=AcquisitionStatus.BUDGET_EXCEEDED,
                error_category="budget",
                provider_metadata={
                    "requested": request.max_cost_usd,
                    "remaining": self.budget.remaining,
                },
            )
            self._emit(request, result)
            return result

        ordered = self._ordered_providers(request)
        results: list[AcquisitionResult] = []
        for provider_name in ordered:
            provider = self.providers.get(provider_name)
            if provider is None:
                continue
            if not self.health.is_healthy(provider_name):
                results.append(
                    self._result(
                        request,
                        status=AcquisitionStatus.PROVIDER_ERROR,
                        provider=provider_name,
                        error_category="circuit_open",
                    )
                )
                continue

            # mechanism-specific policy gate (e.g. scraping vs official API)
            mechanism = MECHANISM_BY_PROVIDER.get(provider_name, "api")
            provider_decision = self.policy_gate.evaluate(
                request.platform, request.commercial_context, mechanism=mechanism
            )
            if not provider_decision.allowed:
                denied = self._result(
                    request,
                    status=AcquisitionStatus.POLICY_DENIED,
                    provider=provider_name,
                    error_category="policy",
                    policy_decision_id=provider_decision.decision_id,
                    provider_metadata={"rationale": provider_decision.rationale},
                )
                results.append(denied)
                self._emit(request, denied)
                continue

            result = self._attempt(provider, request)
            self._record_health(provider_name, result)
            self.budget.charge(provider_name, request.request_id, result.cost_usd)
            results.append(result)
            self._emit(request, result)

            if result.is_success or result.status == AcquisitionStatus.NO_RESULTS:
                return result

        if not results:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                error_category="no_providers",
                provider_metadata={"priority": list(ordered)},
            )

        final = results[-1]
        attempted = [r for r in results if r.status in AcquisitionStatus.attempted()]
        if not attempted and all(
            r.status == AcquisitionStatus.POLICY_DENIED for r in results
        ):
            return self._result(
                request,
                status=AcquisitionStatus.POLICY_DENIED,
                provider=final.provider,
                error_category="all_providers_policy_denied",
                policy_decision_id=final.policy_decision_id,
                provider_metadata={
                    "attempted": [r.provider for r in results],
                    "rationale": final.provider_metadata.get("rationale"),
                },
            )
        if not attempted and all(
            r.status == AcquisitionStatus.NOT_CONFIGURED for r in results
        ):
            return self._result(
                request,
                status=AcquisitionStatus.NOT_CONFIGURED,
                provider=final.provider,
                error_category="all_providers_not_configured",
                provider_metadata={
                    "attempted": [r.provider for r in results],
                    "reasons": [r.provider_metadata.get("reason") for r in results],
                },
            )
        return self._result(
            request,
            status=AcquisitionStatus.PROVIDER_ERROR,
            provider=final.provider,
            error_category=final.error_category or "all_providers_unavailable",
            provider_metadata={
                "attempted": [r.provider for r in results],
                "last_status": final.status.value,
            },
        )

    # -- internals ---------------------------------------------------------- #
    def _ordered_providers(self, request: AcquisitionRequest) -> list[str]:
        preferred = list(request.preferred_providers)
        remaining = [p for p in self.priority if p not in preferred]
        return preferred + remaining

    def _attempt(self, provider, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        result = provider.acquire(request)
        # Guard against providers returning a forged/empty success.
        if result.status == AcquisitionStatus.SUCCESS and result.record_count == 0 and not result.provider_metadata.get("explicit_no_records"):
            result = self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider=provider.name,
                started_at=started,
                error_category="empty_success",
                provider_metadata={"reason": "provider returned SUCCESS with zero records"},
            )
        return result

    def _record_health(self, provider_name: str, result: AcquisitionResult) -> None:
        if result.is_success or result.status == AcquisitionStatus.NO_RESULTS:
            self.health.record_success(provider_name)
        else:
            self.health.record_failure(provider_name, result.error_category)

    def _policy_result(self, request, decision_id: str, rationale: str) -> AcquisitionResult:
        return self._result(
            request,
            status=AcquisitionStatus.POLICY_DENIED,
            error_category="policy",
            policy_decision_id=decision_id,
            provider_metadata={"rationale": rationale},
        )

    def _result(
        self,
        request: AcquisitionRequest,
        *,
        status: AcquisitionStatus,
        provider: str | None = None,
        started_at: datetime | None = None,
        error_category: str | None = None,
        policy_decision_id: str | None = None,
        provider_metadata: dict | None = None,
    ) -> AcquisitionResult:
        started = started_at or utc_now()
        return AcquisitionResult(
            request_id=request.request_id,
            provider=provider or "router",
            provider_endpoint=None,
            status=status,
            started_at=started,
            completed_at=utc_now(),
            error_category=error_category,
            policy_decision_id=policy_decision_id,
            provider_metadata=provider_metadata or {},
        )

    def _emit(self, request: AcquisitionRequest, result: AcquisitionResult) -> None:
        if self.telemetry is not None:
            try:
                self.telemetry(request, result)
            except Exception:
                # Telemetry must never break acquisition.
                pass


def make_decision_id() -> str:
    return str(uuid.uuid4())
