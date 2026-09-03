"""Monid provider for the Festival Signal Fabric.

Implements the current Monid API architecture:

* ``POST /v1/discover``          - find a suitable endpoint
* ``POST /v1/inspect``           - inspect schema + pricing
* ``POST /v1/run``               - execute (sync or async)
* ``GET  /v1/runs/{runId}``      - poll asynchronous runs
* ``GET  /v1/wallet/balance``    - account balance for cost accounting

Credentials come from ``MONID_API_KEY`` (environment only). With no key the
provider returns ``NOT_CONFIGURED`` — never placeholder tools or simulated
results. The provider records the exact endpoint, run ID, cost and latency,
and never logs secrets.
"""

from __future__ import annotations

import time
from typing import Any

from ..base import BaseProvider
from ..contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CostEstimate,
    ProviderHealth,
    content_hash_of,
    utc_now,
)
from ..transport import HttpResponse, TransportError

DEFAULT_BASE_URL = "https://api.monid.ai"

# These are the only generic acquisition intents this adapter may route. The
# endpoint/provider behind Monid still owns the concrete schema and rights
# decision; this list keeps browser and queue callers from inventing an
# unbounded scraping surface.
SUPPORTED_OPERATIONS = frozenset(
    {
        "SOCIAL_PROFILE",
        "SOCIAL_POSTS",
        "SOCIAL_COMMENTS",
        "VIDEO_SEARCH",
        "PLATFORM_DISCOVERY",
    }
)


def operation_for_request(request: AcquisitionRequest) -> str:
    """Resolve a bounded Monid intent without changing legacy requests."""
    requested = str(request.operation or "").strip().upper()
    if requested in SUPPORTED_OPERATIONS:
        return requested
    platform = str(request.platform or "").strip().lower()
    if platform in {"youtube", "video", "tiktok", "instagram"}:
        return "VIDEO_SEARCH" if platform == "youtube" else "SOCIAL_POSTS"
    return "PLATFORM_DISCOVERY"


class MonidProvider(BaseProvider):
    name = "monid"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        base_url: str | None = None,
        max_polls: int = 10,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (base_url or self.env.get("MONID_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_polls = max_polls
        self.poll_interval_seconds = poll_interval_seconds

    # -- interface ---------------------------------------------------------- #
    def health(self) -> ProviderHealth:
        key = self.secret("MONID_API_KEY")
        if key is None:
            return ProviderHealth(provider=self.name, healthy=False, last_error="no MONID_API_KEY")
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        key = self.secret("MONID_API_KEY")
        if key is None:
            return CostEstimate(provider=self.name, estimated_cost_usd=None)
        try:
            response = self._request(
                "GET",
                f"{self.base_url}/v1/wallet/balance",
                headers={"Authorization": f"Bearer {key}"},
            )
            if response.status == 200:
                balance = response.json()
                # Real API: {"balance": {"value": int, "currency": str}};
                # legacy fallback: {"balance_usd": float}.
                inner = balance.get("balance") or {}
                if "value" in inner:
                    estimated = float(inner.get("value") or 0.0)
                else:
                    estimated = float(balance.get("balance_usd") or 0.0)
                return CostEstimate(
                    provider=self.name,
                    estimated_cost_usd=estimated,
                    currency=str(inner.get("currency") or "USD"),
                    source="monid_wallet_balance",
                )
        except (TransportError, ValueError):
            pass
        return CostEstimate(provider=self.name, estimated_cost_usd=None)

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        key = self.secret("MONID_API_KEY")
        if key is None:
            return self._not_configured(request, "MONID_API_KEY not set")

        started = utc_now()
        operation = operation_for_request(request)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        # 1. discover
        try:
            discover = self._request(
                "POST",
                f"{self.base_url}/v1/discover",
                headers=headers,
                body={
                    "query": request.query,
                    "platform": request.platform,
                    "operation": operation,
                    "entity_id": request.entity_id,
                    "entity_type": request.entity_type,
                    "max_records": request.max_records,
                },
            )
        except TransportError as exc:
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc)
            )
        if discover.status == 401 or discover.status == 403:
            return self._fail(
                request,
                started,
                AcquisitionStatus.PROVIDER_ERROR,
                "authentication",
                f"http {discover.status}",
            )
        if discover.status == 429:
            return self._fail(
                request,
                started,
                AcquisitionStatus.RATE_LIMITED,
                "rate_limited",
                f"http {discover.status}",
            )
        if discover.status != 200:
            return self._fail(
                request,
                started,
                AcquisitionStatus.PROVIDER_ERROR,
                "discover",
                f"http {discover.status}",
            )

        try:
            discover_payload = discover.json()
        except ValueError:
            return self._fail(
                request, started, AcquisitionStatus.SCHEMA_INVALID, "discover_response"
            )

        # Real API returns {"results": [{... "endpoint": "/path"}]};
        # legacy fallback {"endpoints": [{"id": ...}]}.
        endpoints = discover_payload.get("results") or discover_payload.get("endpoints") or []

        if not endpoints:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=f"{self.base_url}/v1/discover",
                started_at=started,
                provider_metadata={"phase": "discover", "endpoints_found": 0},
            )

        first = endpoints[0]
        endpoint_id = first.get("endpoint") or first.get("id") or first.get("endpoint_id")
        if not endpoint_id:
            return self._fail(
                request, started, AcquisitionStatus.SCHEMA_INVALID, "discover_endpoint_id"
            )

        # 2. inspect
        try:
            inspect = self._request(
                "POST",
                f"{self.base_url}/v1/inspect",
                headers=headers,
                body={"endpoint_id": endpoint_id},
            )
        except TransportError as exc:
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc)
            )
        cost_per_call = None
        if inspect.status == 200:
            try:
                pricing = inspect.json().get("pricing") or {}
                cost_per_call = pricing.get("cost_per_call_usd") or pricing.get("cost_usd")
            except ValueError:
                pass

        # 3. run
        run_body: dict[str, Any] = {
            "endpoint_id": endpoint_id,
            "params": {
                "query": request.query,
                "platform": request.platform,
                "operation": operation,
                "limit": request.max_records,
            },
        }
        if request.start_time is not None:
            run_body["params"]["start_time"] = request.start_time.isoformat()
        if request.end_time is not None:
            run_body["params"]["end_time"] = request.end_time.isoformat()
        if request.market_id is not None:
            run_body["params"]["market_id"] = request.market_id
        try:
            run = self._request(
                "POST",
                f"{self.base_url}/v1/run",
                headers=headers,
                body=run_body,
            )
        except TransportError as exc:
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc)
            )
        if run.status == 429:
            return self._fail(
                request,
                started,
                AcquisitionStatus.RATE_LIMITED,
                "rate_limited",
                f"http {run.status}",
            )
        if run.status != 200:
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "run", f"http {run.status}"
            )

        try:
            run_payload = run.json()
        except ValueError:
            return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "run_response")

        run_id = run_payload.get("run_id")
        run_state = run_payload.get("status", "running")
        data = run_payload.get("data") or []

        # 4. poll asynchronous runs
        polls = 0
        while run_id and run_state in ("running", "queued", "pending") and polls < self.max_polls:
            time.sleep(self.poll_interval_seconds)
            try:
                status_resp = self._request(
                    "GET",
                    f"{self.base_url}/v1/runs/{run_id}",
                    headers={"Authorization": f"Bearer {key}"},
                )
            except TransportError as exc:
                return self._fail(
                    request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc)
                )
            if status_resp.status == 404:
                return self._fail(
                    request,
                    started,
                    AcquisitionStatus.PROVIDER_ERROR,
                    "run_not_found",
                    f"run {run_id}",
                )
            if status_resp.status != 200:
                return self._fail(
                    request,
                    started,
                    AcquisitionStatus.PROVIDER_ERROR,
                    "run_status",
                    f"http {status_resp.status}",
                )
            try:
                status_payload = status_resp.json()
            except ValueError:
                return self._fail(
                    request, started, AcquisitionStatus.SCHEMA_INVALID, "run_status_response"
                )
            run_state = status_payload.get("status", "unknown")
            data = status_payload.get("data") or data
            polls += 1

        if run_state in ("failed", "error"):
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "run_failed", run_state
            )

        # 5. normalize + hash
        records = self._normalize_records(data or [])
        raw_hash = content_hash_of(data or [])
        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS
            if run_state in ("completed", "succeeded", "success")
            else AcquisitionStatus.PARTIAL_SUCCESS,
            provider_endpoint=f"{self.base_url}/v1/run",
            started_at=started,
            record_count=len(records),
            cost_usd=float(cost_per_call) if cost_per_call is not None else None,
            raw_payload_hash=raw_hash,
            provider_metadata={
                "run_id": run_id,
                "endpoint_id": endpoint_id,
                "polls": polls,
                "final_state": run_state,
                "http_status_discover": discover.status,
                "http_status_run": run.status,
                "operation": operation,
                "supported_operations": sorted(SUPPORTED_OPERATIONS),
            },
            records=tuple(records),
        )

    # -- helpers ------------------------------------------------------------ #
    def _request(self, method: str, url: str, *, headers=None, body=None) -> HttpResponse:
        return self.transport.request(method, url, headers=headers, body=body, timeout_seconds=30.0)

    def _fail(self, request, started, status, category, detail=None) -> AcquisitionResult:
        return self._result(
            request,
            status=status,
            provider_endpoint=f"{self.base_url}/v1",
            started_at=started,
            error_category=category,
            provider_metadata={"detail": detail} if detail else {},
        )

    @staticmethod
    def _normalize_records(data: list[dict]) -> list[dict]:
        """Best-effort mapping of Monid provider records to the canonical shape."""
        from ...social.normalize import normalize_monid_record

        return [normalize_monid_record(item) for item in data]
