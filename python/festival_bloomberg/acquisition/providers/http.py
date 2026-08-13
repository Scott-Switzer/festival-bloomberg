"""Generic HTTP provider for approved official/open endpoints.

Used for sources where the "official API" is a simple GET endpoint (RSS
feeds, GDELT, open data files). The request ``query`` must be an ``http(s)``
URL; policy approval is enforced by the router, never here.
"""

from __future__ import annotations

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
from ..transport import TransportError


class HttpProvider(BaseProvider):
    name = "http"

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="open_endpoint")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        url = request.query
        if not (url.startswith("http://") or url.startswith("https://")):
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint="http",
                started_at=utc_now(),
                error_category="query_not_url",
                provider_metadata={"reason": "query must be an http(s) URL"},
            )

        started = utc_now()
        try:
            response = self.transport.request("GET", url, timeout_seconds=30.0)
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="network",
                provider_metadata={"detail": str(exc)},
            )

        if response.status == 429:
            return self._result(
                request,
                status=AcquisitionStatus.RATE_LIMITED,
                provider_endpoint=url,
                started_at=started,
                error_category="rate_limited",
                provider_metadata={"http_status": response.status},
            )
        if response.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="http",
                provider_metadata={"http_status": response.status},
            )

        raw_hash = content_hash_of(response.body)
        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS,
            provider_endpoint=url,
            started_at=started,
            record_count=1,
            cost_usd=0.0,
            raw_payload_hash=raw_hash,
            provider_metadata={
                "http_status": response.status,
                "content_type": response.headers.get("Content-Type"),
                "bytes": len(response.body),
            },
            records=(
                {
                    "platform": request.platform,
                    "object_type": "raw_document",
                    "platform_object_id": None,
                    "text": None,
                    "source_url": url,
                    "raw_bytes": len(response.body),
                },
            ),
        )
