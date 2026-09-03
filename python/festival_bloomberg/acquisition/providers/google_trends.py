"""Google Trends official alpha contract.

This module is an adapter contract for Google's official Trends API alpha. It
never scrapes the Trends UI and never substitutes unofficial pytrends output.
Without the alpha credential/access grant it returns ``WAITLIST / AUTH_REQUIRED``
with no observations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

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

DEFAULT_BASE_URL = "https://trends.googleapis.com/v1alpha"
PROVIDER_VERSION = "google-trends-official-alpha-v1"
WAITLIST_AUTH_REQUIRED = "WAITLIST / AUTH_REQUIRED"
SUPPORTED_GRANULARITIES = frozenset({"daily", "weekly", "monthly"})
SUPPORTED_WINDOWS = frozenset({"5y", "5-year", "5_year"})


class GoogleTrendsProvider(BaseProvider):
    """Credential-gated official Trends API alpha adapter."""

    name = "google_trends"

    def __init__(self, transport=None, env=None, *, base_url: str | None = None) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (
            base_url or self.env.get("GOOGLE_TRENDS_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")

    def configured(self) -> bool:
        return bool(
            self.secret("GOOGLE_TRENDS_API_KEY") or self.secret("GOOGLE_TRENDS_ACCESS_TOKEN")
        )

    def health(self) -> ProviderHealth:
        if not self.configured():
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                last_error=WAITLIST_AUTH_REQUIRED,
            )
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(
            provider=self.name,
            estimated_cost_usd=None,
            free_quota=False,
            source="google_trends_alpha_quota",
        )

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        granularity = self._granularity(request)
        # The artist query is request.query; the historical window is a
        # contract constant for this adapter. Never parse an artist name as a
        # window (for example, "5 Seconds of Summer").
        window = "5y"
        if not self.configured():
            return self._result(
                request,
                status=AcquisitionStatus.NOT_CONFIGURED,
                error_category="credentials_missing",
                provider_metadata={
                    "readiness": WAITLIST_AUTH_REQUIRED,
                    "provider_version": PROVIDER_VERSION,
                    "granularity": granularity,
                    "window": window,
                    "ui_scraping": False,
                },
            )
        if window not in SUPPORTED_WINDOWS:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                error_category="unsupported_window",
                provider_metadata={
                    "window": window,
                    "supported_windows": sorted(SUPPORTED_WINDOWS),
                },
            )

        started = utc_now()
        term = request.entity_id or request.query
        params: dict[str, str] = {
            "q": request.query,
            "time": "today 5-y",
            "granularity": granularity,
        }
        if request.market_id:
            params["geo"] = request.market_id
        url = f"{self.base_url}/interest-over-time/{quote(str(term), safe='')}"
        try:
            response = self.transport.request(
                "GET",
                url,
                headers=self._headers(),
                params=params,
                timeout_seconds=30.0,
            )
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="network",
                provider_metadata=self._metadata(granularity, window, detail=str(exc)),
            )

        if response.status in (401, 403):
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="authentication",
                provider_metadata=self._metadata(
                    granularity, window, readiness=WAITLIST_AUTH_REQUIRED
                ),
            )
        if response.status == 429:
            return self._result(
                request,
                status=AcquisitionStatus.RATE_LIMITED,
                provider_endpoint=url,
                started_at=started,
                error_category="rate_limited",
                provider_metadata=self._metadata(granularity, window),
            )
        if response.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="http",
                provider_metadata=self._metadata(granularity, window, http_status=response.status),
            )
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=url,
                started_at=started,
                error_category="response_not_json",
                provider_metadata=self._metadata(granularity, window),
            )
        records = self._normalize(payload, request, granularity, started)
        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=url,
            started_at=started,
            record_count=len(records),
            raw_payload_hash=content_hash_of(payload),
            provider_metadata=self._metadata(
                granularity,
                window,
                http_status=response.status,
                record_count=len(records),
            ),
            records=tuple(records),
        )

    @staticmethod
    def _granularity(request: AcquisitionRequest) -> str:
        requested = str(request.order or request.operation or "weekly").strip().lower()
        return requested if requested in SUPPORTED_GRANULARITIES else "weekly"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = self.secret("GOOGLE_TRENDS_ACCESS_TOKEN")
        key = self.secret("GOOGLE_TRENDS_API_KEY")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if key:
            headers["X-Goog-Api-Key"] = key
        return headers

    @staticmethod
    def _metadata(granularity: str, window: str, **extra: Any) -> dict[str, Any]:
        return {
            "provider_version": PROVIDER_VERSION,
            "source": "Google Trends official API alpha",
            "granularity": granularity,
            "window": window,
            "region_support": True,
            "subregion_support": True,
            "ui_scraping": False,
            **extra,
        }

    @staticmethod
    def _normalize(
        payload: Any,
        request: AcquisitionRequest,
        granularity: str,
        retrieved_at: datetime,
    ) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("timeline") or payload.get("data") or payload.get("points") or []
        else:
            items = []
        rows: list[dict[str, Any]] = []
        for item in items[: request.max_records or 500]:
            if not isinstance(item, dict):
                continue
            observed = item.get("date") or item.get("time") or item.get("timestamp")
            rows.append(
                {
                    "platform": "google_trends",
                    "provider": PROVIDER_VERSION,
                    "object_type": "artist_search_interest",
                    "platform_object_id": str(observed) if observed else None,
                    "artist_key": request.entity_id,
                    "query": request.query,
                    "region": request.market_id,
                    "granularity": granularity,
                    "observed_at": observed,
                    "interest_value": item.get("value")
                    if item.get("value") is not None
                    else item.get("interest"),
                    "retrieved_at": retrieved_at.isoformat(),
                    "knowledge_time": retrieved_at.isoformat(),
                    "source": "google_trends_official_alpha",
                    "rights_status": "OFFICIAL_API_PENDING_ACCESS_REVIEW",
                    "commercial_use_status": "LICENSE_REQUIRED",
                }
            )
        return rows
