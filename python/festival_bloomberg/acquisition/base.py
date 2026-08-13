"""Canonical acquisition provider protocol.

The router knows providers only through this interface. Providers are
synchronous for determinism and testability; they may be wrapped in worker
threads by the CLI layer without changing their contract.

Every provider must:

* return :data:`AcquisitionStatus.NOT_CONFIGURED` (never simulated data) when
  required credentials are absent;
* report failure via an explicit status, never as an empty ``SUCCESS``;
* record a real cost when the provider reports one (``None`` otherwise);
* never fabricate observations.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CostEstimate,
    ProviderHealth,
    utc_now,
)
from .transport import HttpTransport, UrllibTransport


@runtime_checkable
class AcquisitionProvider(Protocol):
    name: str

    def health(self) -> ProviderHealth: ...

    def estimate(self, request: AcquisitionRequest) -> CostEstimate: ...

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult: ...


class BaseProvider:
    """Shared plumbing for providers: credentials, transport, result helpers."""

    name: str = "base"

    def __init__(
        self,
        transport: HttpTransport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.transport = transport or UrllibTransport()
        self.env = env if env is not None else os.environ

    # -- credentials -------------------------------------------------------- #
    def secret(self, name: str) -> str | None:
        value = self.env.get(name)
        if value is None or not value.strip():
            return None
        return value.strip()

    # -- result helpers ----------------------------------------------------- #
    def _not_configured(self, request: AcquisitionRequest, reason: str) -> AcquisitionResult:
        return self._result(
            request,
            status=AcquisitionStatus.NOT_CONFIGURED,
            provider_endpoint=None,
            error_category="credentials_missing",
            provider_metadata={"reason": reason},
        )

    def _result(
        self,
        request: AcquisitionRequest,
        *,
        status: AcquisitionStatus,
        provider_endpoint: str | None = None,
        started_at: datetime | None = None,
        record_count: int = 0,
        cost_usd: float | None = None,
        raw_payload_hash: str | None = None,
        raw_payload_location: str | None = None,
        policy_decision_id: str | None = None,
        error_category: str | None = None,
        provider_metadata: dict | None = None,
        records: tuple[dict, ...] = (),
    ) -> AcquisitionResult:
        started = started_at or utc_now()
        return AcquisitionResult(
            request_id=request.request_id,
            provider=self.name,
            provider_endpoint=provider_endpoint,
            status=status,
            started_at=started,
            completed_at=utc_now(),
            record_count=record_count,
            cost_usd=cost_usd,
            latency_ms=self._latency(started),
            raw_payload_hash=raw_payload_hash,
            raw_payload_location=raw_payload_location,
            policy_decision_id=policy_decision_id,
            error_category=error_category,
            provider_metadata=provider_metadata or {},
            records=records,
        )

    @staticmethod
    def _latency(started_at: datetime) -> int:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - started_at).total_seconds()
        return max(0, int(delta * 1000))
