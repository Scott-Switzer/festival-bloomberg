"""Soundcharts licensed artist-intelligence provider.

Soundcharts is the licensed historical rail for audience and streaming
history. This adapter deliberately keeps the serving boundary asynchronous:
it returns normalized acquisition records, never calls an external provider
from a browser request, and never reconstructs history from a current value.

When ``SOUNDCHARTS_APP_ID`` and ``SOUNDCHARTS_API_KEY`` are absent the adapter
returns ``NOT_CONFIGURED`` with ``AUTH_REQUIRED_FOR_REAL_BACKFILL``. Contract
tests can inject a fake transport; no sandbox or synthetic values are used as
real observations.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from ...security.artist_factor_tape import build_factor_observation
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

DEFAULT_BASE_URL = "https://customer.api.soundcharts.com/api/v2"
PROVIDER_VERSION = "soundcharts-licensed-v1"
READINESS_AUTH_REQUIRED = "AUTH_REQUIRED_FOR_REAL_BACKFILL"
HISTORICAL_STRATEGY_LICENSED = "LICENSED_HISTORICAL"
HISTORICAL_STRATEGY_FORWARD = "SELF_OBSERVED_FORWARD"
RIGHTS_STATUS = "LICENSED_PROVIDER_PENDING_ACCOUNT_REVIEW"
COMMERCIAL_USE_STATUS = "LICENSE_REQUIRED"

ARTIST_RESOLVE = "ARTIST_RESOLVE"
CURRENT_STATS = "CURRENT_STATS"
AUDIENCE_HISTORY = "AUDIENCE_HISTORY"
STREAMING_HISTORY = "STREAMING_HISTORY"
LOCAL_STREAMING = "LOCAL_STREAMING"
AUDIENCE_REPORT = "AUDIENCE_REPORT"
SUPPORTED_OPERATIONS = frozenset(
    {
        ARTIST_RESOLVE,
        CURRENT_STATS,
        AUDIENCE_HISTORY,
        STREAMING_HISTORY,
        LOCAL_STREAMING,
        AUDIENCE_REPORT,
    }
)

_OPERATION_PATHS = {
    ARTIST_RESOLVE: "/artist/search",
    CURRENT_STATS: "/artist/{artist_id}/stats",
    AUDIENCE_HISTORY: "/artist/{artist_id}/audience/history",
    STREAMING_HISTORY: "/artist/{artist_id}/streaming/history",
    LOCAL_STREAMING: "/artist/{artist_id}/streaming/local",
    AUDIENCE_REPORT: "/artist/{artist_id}/audience/report",
}


class SoundchartsProvider(BaseProvider):
    """Soundcharts adapter with explicit licensed-history semantics."""

    name = "soundcharts"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        base_url: str | None = None,
        max_records: int = 500,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (
            base_url or self.env.get("SOUNDCHARTS_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.max_records = max(1, min(int(max_records), 5000))

    def configured(self) -> bool:
        return bool(self.secret("SOUNDCHARTS_APP_ID") and self.secret("SOUNDCHARTS_API_KEY"))

    def health(self) -> ProviderHealth:
        if not self.configured():
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                last_error=READINESS_AUTH_REQUIRED,
            )
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        # Trial/contract pricing is account-specific; unknown is not zero.
        return CostEstimate(
            provider=self.name,
            estimated_cost_usd=None,
            free_quota=False,
            source="soundcharts_account_quota",
        )

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        operation = self.operation_for_request(request)
        if not self.configured():
            return self._readiness_result(request, operation)

        started = utc_now()
        path = self._path_for(operation, request)
        params = self._params_for(operation, request)
        url = f"{self.base_url}{path}"
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
                provider_metadata=self._metadata(operation, detail=str(exc)),
            )

        if response.status in (401, 403):
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="authentication",
                provider_metadata=self._metadata(
                    operation,
                    readiness=READINESS_AUTH_REQUIRED,
                    http_status=response.status,
                ),
            )
        if response.status == 429:
            return self._result(
                request,
                status=AcquisitionStatus.RATE_LIMITED,
                provider_endpoint=url,
                started_at=started,
                error_category="rate_limited",
                provider_metadata=self._metadata(operation, http_status=429),
            )
        if response.status == 404:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=url,
                started_at=started,
                error_category="not_found",
                provider_metadata=self._metadata(operation, http_status=404),
            )
        if response.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="http",
                provider_metadata=self._metadata(operation, http_status=response.status),
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
                provider_metadata=self._metadata(operation),
            )

        records = self._normalize_payload(
            payload,
            request=request,
            operation=operation,
            retrieved_at=started.isoformat(),
        )
        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=url,
            started_at=started,
            record_count=len(records),
            cost_usd=None,
            raw_payload_hash=content_hash_of(payload),
            provider_metadata=self._metadata(
                operation,
                http_status=response.status,
                record_count=len(records),
            ),
            records=tuple(records),
        )

    @staticmethod
    def operation_for_request(request: AcquisitionRequest) -> str:
        requested = str(request.operation or "").strip().upper()
        if requested in SUPPORTED_OPERATIONS:
            return requested
        return ARTIST_RESOLVE if not request.external_id else CURRENT_STATS

    def resolve_artist(self, name: str) -> AcquisitionResult:
        return self.acquire(
            AcquisitionRequest.new(
                entity_id=name,
                entity_type="artist",
                platform=self.name,
                query=name,
                operation=ARTIST_RESOLVE,
            )
        )

    def historical_audience(
        self, artist_id: str, *, start_time=None, end_time=None
    ) -> AcquisitionResult:
        return self._for_artist(
            artist_id, AUDIENCE_HISTORY, start_time=start_time, end_time=end_time
        )

    def historical_streaming(
        self, artist_id: str, *, start_time=None, end_time=None
    ) -> AcquisitionResult:
        return self._for_artist(
            artist_id, STREAMING_HISTORY, start_time=start_time, end_time=end_time
        )

    def current_stats(self, artist_id: str) -> AcquisitionResult:
        return self._for_artist(artist_id, CURRENT_STATS)

    def local_streaming(self, artist_id: str, *, market_id: str | None = None) -> AcquisitionResult:
        return self._for_artist(artist_id, LOCAL_STREAMING, market_id=market_id)

    def audience_report_dates(self, artist_id: str) -> AcquisitionResult:
        return self._for_artist(artist_id, AUDIENCE_REPORT)

    def _for_artist(self, artist_id: str, operation: str, **kwargs: Any) -> AcquisitionResult:
        return self.acquire(
            AcquisitionRequest.new(
                entity_id=artist_id,
                entity_type="artist",
                platform=self.name,
                query=artist_id,
                external_id=artist_id,
                operation=operation,
                **kwargs,
            )
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-App-Id": self.secret("SOUNDCHARTS_APP_ID") or "",
            "X-Api-Key": self.secret("SOUNDCHARTS_API_KEY") or "",
        }

    def _path_for(self, operation: str, request: AcquisitionRequest) -> str:
        artist_id = quote(str(request.external_id or request.entity_id), safe="")
        return _OPERATION_PATHS[operation].format(artist_id=artist_id)

    def _params_for(self, operation: str, request: AcquisitionRequest) -> dict[str, str]:
        params: dict[str, str] = {}
        if operation == ARTIST_RESOLVE:
            params["query"] = request.query
        if request.start_time is not None:
            params["startDate"] = request.start_time.date().isoformat()
        if request.end_time is not None:
            params["endDate"] = request.end_time.date().isoformat()
        if request.market_id:
            params["market"] = request.market_id
        params["limit"] = str(min(request.max_records or self.max_records, self.max_records))
        return params

    def _readiness_result(self, request: AcquisitionRequest, operation: str) -> AcquisitionResult:
        return self._result(
            request,
            status=AcquisitionStatus.NOT_CONFIGURED,
            provider_endpoint=None,
            error_category="credentials_missing",
            provider_metadata=self._metadata(
                operation,
                readiness=READINESS_AUTH_REQUIRED,
                reason="SOUNDCHARTS_APP_ID and SOUNDCHARTS_API_KEY are not configured",
            ),
        )

    @staticmethod
    def _metadata(operation: str, **extra: Any) -> dict[str, Any]:
        return {
            "operation": operation,
            "provider_version": PROVIDER_VERSION,
            "licensed_source": "Soundcharts",
            "historical_strategy": (
                HISTORICAL_STRATEGY_LICENSED
                if operation
                in {
                    AUDIENCE_HISTORY,
                    STREAMING_HISTORY,
                    LOCAL_STREAMING,
                    AUDIENCE_REPORT,
                }
                else HISTORICAL_STRATEGY_FORWARD
            ),
            "rights_status": RIGHTS_STATUS,
            "commercial_use_status": COMMERCIAL_USE_STATUS,
            **extra,
        }

    @staticmethod
    def _normalize_payload(
        payload: Any,
        *,
        request: AcquisitionRequest,
        operation: str,
        retrieved_at: str,
    ) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = (
                payload.get("data")
                or payload.get("results")
                or payload.get("items")
                or payload.get("timeline")
                or [payload]
            )
        else:
            items = []
        if isinstance(items, dict):
            items = [items]
        records: list[dict[str, Any]] = []
        for item in items[: request.max_records or 500]:
            if not isinstance(item, dict):
                continue
            records.append(
                {
                    "platform": "soundcharts",
                    "provider": PROVIDER_VERSION,
                    "object_type": operation.lower(),
                    "platform_object_id": item.get("uuid") or item.get("id"),
                    "artist_key": request.entity_id,
                    "operation": operation,
                    "data": item,
                    "retrieved_at": retrieved_at,
                    "knowledge_time": retrieved_at,
                    "knowledge_time_source": "licensed_provider_retrieval",
                    "source": "soundcharts",
                    "rights_status": RIGHTS_STATUS,
                    "commercial_use_status": COMMERCIAL_USE_STATUS,
                    "historical_strategy": (
                        HISTORICAL_STRATEGY_LICENSED
                        if operation
                        in {AUDIENCE_HISTORY, STREAMING_HISTORY, LOCAL_STREAMING, AUDIENCE_REPORT}
                        else HISTORICAL_STRATEGY_FORWARD
                    ),
                }
            )
        return records


def factor_rows_from_records(
    records: list[dict[str, Any]],
    *,
    artist_key: str,
    generation: str,
    retrieved_at: datetime | None = None,
    value_getter: Callable[[dict[str, Any]], tuple[str, float | int | None, str] | None]
    | None = None,
) -> list[dict[str, Any]]:
    """Convert explicitly supplied Soundcharts values to factor-tape rows.

    This helper intentionally requires a caller-provided value mapping. It
    never guesses whether an arbitrary provider field is a monthly listener,
    follower, or popularity observation.
    """
    retrieved = retrieved_at or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for record in records:
        if value_getter is None:
            continue
        mapped = value_getter(record)
        if mapped is None:
            continue
        factor_name, value, unit = mapped
        data = record.get("data") or {}
        observed = data.get("date") or data.get("observation_date") or retrieved.date()
        rows.append(
            build_factor_observation(
                artist_key=artist_key,
                factor_family="DEMAND",
                factor_name=factor_name,
                platform="soundcharts",
                value=value,
                unit=unit,
                observation_time=observed,
                available_at=data.get("available_at"),
                knowledge_time=record.get("knowledge_time") or retrieved,
                retrieved_at=record.get("retrieved_at") or retrieved,
                source="soundcharts",
                evidence_ref=data.get("url"),
                source_scope="LICENSED_HISTORICAL",
                rights_status=RIGHTS_STATUS,
                commercial_use_status=COMMERCIAL_USE_STATUS,
                generation=generation,
            )
        )
    return rows
