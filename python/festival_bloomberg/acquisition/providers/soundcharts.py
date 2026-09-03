"""Soundcharts licensed artist-intelligence provider.

Soundcharts is the licensed historical rail for audience and streaming
history. This adapter implements the endpoints documented at
https://developers.soundcharts.com/api/reference — the paths are verified
against the live public sandbox (``x-app-id: soundcharts`` /
``x-api-key: soundcharts``), not invented:

* ``GET /api/v2/artist/search/{term}``            — search by name (plan-gated)
* ``GET /api/v2/artist/{uuid}``                   — artist identity by UUID
* ``GET /api/v2/artist/{uuid}/current/stats``     — current stats snapshot
* ``GET /api/v2/artist/{uuid}/audience/{platform}`` — audience history
* ``GET /api/v2/artist/{uuid}/streaming/{platform}/listening`` — streaming history
* ``GET /api/v2/artist/{uuid}/streaming/{platform}`` — local streaming
* ``GET /api/v2/artist/{uuid}/audience/{platform}/report/available-dates``

Environment contract (fail closed — never simulated):

* ``SOUNDCHARTS_SANDBOX=1`` uses the free public sandbox credentials.
* ``SOUNDCHARTS_APP_ID`` + ``SOUNDCHARTS_API_KEY`` use the legacy headers.
* ``SOUNDCHARTS_CLIENT_ID`` + ``SOUNDCHARTS_CLIENT_SECRET`` use the current
  OAuth client-credentials token flow (``account.soundcharts.com/oauth/token``).

Without any credential the adapter returns ``NOT_CONFIGURED`` with
``AUTH_REQUIRED_FOR_REAL_BACKFILL``.

License note: Soundcharts ships an official ``pip install soundcharts`` Python
SDK (GPL-3.0). It is *not* added as a runtime dependency: GPL-3.0 copyleft
would impose distribution obligations on this project's dependency graph for a
thin JSON wrapper. The adapter below is a small MIT-compatible implementation
of the same documented REST contract, and the sandbox live tests pin the
contract to the real API.

Serving boundary: acquisition is asynchronous; no provider call ever happens
from a browser request, and history is never reconstructed from a current
value (``LICENSED_HISTORICAL`` vs ``SELF_OBSERVED_FORWARD``).
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
TOKEN_URL = "https://account.soundcharts.com/oauth/token"
SANDBOX_APP_ID = "soundcharts"
SANDBOX_API_KEY = "soundcharts"
PROVIDER_VERSION = "soundcharts-licensed-v2"
READINESS_AUTH_REQUIRED = "AUTH_REQUIRED_FOR_REAL_BACKFILL"
HISTORICAL_STRATEGY_LICENSED = "LICENSED_HISTORICAL"
HISTORICAL_STRATEGY_FORWARD = "SELF_OBSERVED_FORWARD"
RIGHTS_STATUS = "LICENSED_PROVIDER_PENDING_ACCOUNT_REVIEW"
COMMERCIAL_USE_STATUS = "LICENSE_REQUIRED"

ARTIST_SEARCH = "ARTIST_SEARCH"
ARTIST_BY_UUID = "ARTIST_BY_UUID"
CURRENT_STATS = "CURRENT_STATS"
AUDIENCE_HISTORY = "AUDIENCE_HISTORY"
STREAMING_HISTORY = "STREAMING_HISTORY"
LOCAL_STREAMING = "LOCAL_STREAMING"
AUDIENCE_REPORT_DATES = "AUDIENCE_REPORT_DATES"
SUPPORTED_OPERATIONS = frozenset(
    {
        ARTIST_SEARCH,
        ARTIST_BY_UUID,
        CURRENT_STATS,
        AUDIENCE_HISTORY,
        STREAMING_HISTORY,
        LOCAL_STREAMING,
        AUDIENCE_REPORT_DATES,
    }
)

#: Documented path templates. ``{platform}`` is the Soundcharts platform slug
#: (spotify, instagram, tiktok, youtube, ...) and must be lowercase.
_OPERATION_PATHS = {
    ARTIST_SEARCH: "/artist/search/{term}",
    ARTIST_BY_UUID: "/artist/{artist_id}",
    CURRENT_STATS: "/artist/{artist_id}/current/stats",
    AUDIENCE_HISTORY: "/artist/{artist_id}/audience/{platform}",
    STREAMING_HISTORY: "/artist/{artist_id}/streaming/{platform}/listening",
    LOCAL_STREAMING: "/artist/{artist_id}/streaming/{platform}",
    AUDIENCE_REPORT_DATES: "/artist/{artist_id}/audience/{platform}/report/available-dates",
}


class SoundchartsProvider(BaseProvider):
    """Soundcharts adapter with explicit licensed-history semantics.

    The sandbox mode is deterministic: with ``SOUNDCHARTS_SANDBOX=1`` the
    provider uses the documented public sandbox headers against the real API,
    so contract tests run against actual Soundcharts infrastructure.
    """

    name = "soundcharts"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        base_url: str | None = None,
        max_records: int = 500,
        sandbox: bool | None = None,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (
            base_url or self.env.get("SOUNDCHARTS_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.max_records = max(1, min(int(max_records), 5000))
        #: Explicit sandbox flag wins; otherwise env; otherwise False.
        if sandbox is not None:
            self.sandbox = bool(sandbox)
        else:
            self.sandbox = str(self.env.get("SOUNDCHARTS_SANDBOX") or "").lower() in {
                "1",
                "true",
                "yes",
            }
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    # -- readiness --------------------------------------------------------- #
    def configured(self) -> bool:
        if self.sandbox:
            return True
        return bool(
            (self.secret("SOUNDCHARTS_APP_ID") and self.secret("SOUNDCHARTS_API_KEY"))
            or (self.secret("SOUNDCHARTS_CLIENT_ID") and self.secret("SOUNDCHARTS_CLIENT_SECRET"))
        )

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

    # -- acquisition ------------------------------------------------------- #
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
                    note="search/artist-search is documented as plan-gated; "
                    "entity endpoints (artist/{uuid}, current/stats, audience, "
                    "streaming) are available on the sandbox",
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
                sandbox=self.sandbox,
            ),
            records=tuple(records),
        )

    @staticmethod
    def operation_for_request(request: AcquisitionRequest) -> str:
        requested = str(request.operation or "").strip().upper()
        if requested in SUPPORTED_OPERATIONS:
            return requested
        if request.external_id and str(request.external_id).startswith("11e8"):
            return CURRENT_STATS
        return ARTIST_SEARCH if not request.external_id else CURRENT_STATS

    # -- convenience wrappers --------------------------------------------- #
    def resolve_artist(self, name: str) -> AcquisitionResult:
        return self.acquire(
            AcquisitionRequest.new(
                entity_id=name,
                entity_type="artist",
                platform=self.name,
                query=name,
                operation=ARTIST_SEARCH,
            )
        )

    def artist_by_uuid(self, artist_id: str) -> AcquisitionResult:
        return self._for_artist(artist_id, ARTIST_BY_UUID)

    def current_stats(self, artist_id: str) -> AcquisitionResult:
        return self._for_artist(artist_id, CURRENT_STATS)

    def historical_audience(
        self, artist_id: str, *, platform: str = "instagram", start_time=None, end_time=None
    ) -> AcquisitionResult:
        return self._for_artist(
            artist_id,
            AUDIENCE_HISTORY,
            platform=platform,
            start_time=start_time,
            end_time=end_time,
        )

    def historical_streaming(
        self, artist_id: str, *, platform: str = "spotify", start_time=None, end_time=None
    ) -> AcquisitionResult:
        return self._for_artist(
            artist_id,
            STREAMING_HISTORY,
            platform=platform,
            start_time=start_time,
            end_time=end_time,
        )

    def local_streaming(
        self, artist_id: str, *, platform: str = "spotify", market_id: str | None = None
    ) -> AcquisitionResult:
        return self._for_artist(
            artist_id, LOCAL_STREAMING, platform=platform, market_id=market_id
        )

    def audience_report_dates(
        self, artist_id: str, *, platform: str = "spotify"
    ) -> AcquisitionResult:
        return self._for_artist(artist_id, AUDIENCE_REPORT_DATES, platform=platform)

    def _for_artist(self, artist_id: str, operation: str, **kwargs: Any) -> AcquisitionResult:
        platform_scope = kwargs.pop("platform", None)
        return self.acquire(
            AcquisitionRequest.new(
                entity_id=artist_id,
                entity_type="artist",
                platform=self.name,
                query=artist_id,
                external_id=artist_id,
                operation=operation,
                platform_scope=platform_scope,
                **kwargs,
            )
        )

    # -- auth -------------------------------------------------------------- #
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.sandbox:
            headers["X-App-Id"] = SANDBOX_APP_ID
            headers["X-Api-Key"] = SANDBOX_API_KEY
            return headers
        client_id = self.secret("SOUNDCHARTS_CLIENT_ID")
        client_secret = self.secret("SOUNDCHARTS_CLIENT_SECRET")
        if client_id and client_secret:
            headers["Authorization"] = f"Bearer {self._access_token_required(client_id, client_secret)}"
            return headers
        headers["X-App-Id"] = self.secret("SOUNDCHARTS_APP_ID") or ""
        headers["X-Api-Key"] = self.secret("SOUNDCHARTS_API_KEY") or ""
        return headers

    def _access_token_required(self, client_id: str, client_secret: str) -> str:
        now = datetime.now(UTC)
        if self._access_token and self._token_expires_at and now < self._token_expires_at:
            return self._access_token
        try:
            response = self.transport.request(
                "POST",
                TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": _basic_auth(client_id, client_secret),
                },
                body="grant_type=client_credentials",
                timeout_seconds=30.0,
            )
        except TransportError as exc:  # pragma: no cover - network path
            raise RuntimeError(f"soundcharts token request failed: {exc}") from exc
        if response.status != 200:
            raise RuntimeError(f"soundcharts token request failed: http {response.status}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("soundcharts token response missing access_token")
        expires_in = int(payload.get("expires_in") or 3600)
        self._access_token = token
        self._token_expires_at = now + datetime.timedelta(seconds=max(60, expires_in - 60))
        return token

    # -- request construction --------------------------------------------- #
    def _path_for(self, operation: str, request: AcquisitionRequest) -> str:
        artist_id = quote(str(request.external_id or request.entity_id), safe="")
        platform = str(request.platform_scope or "spotify").strip().lower()
        return _OPERATION_PATHS[operation].format(
            artist_id=artist_id, term=artist_id, platform=platform
        )

    def _params_for(self, operation: str, request: AcquisitionRequest) -> dict[str, str]:
        params: dict[str, str] = {}
        if operation == ARTIST_SEARCH:
            params["limit"] = str(min(request.max_records or self.max_records, 20))
            params["offset"] = "0"
        if request.start_time is not None:
            params["start_date"] = _date_iso(request.start_time)
        if request.end_time is not None:
            params["end_date"] = _date_iso(request.end_time)
        if request.market_id:
            params["market"] = request.market_id
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
                reason="no Soundcharts credentials configured "
                "(SOUNDCHARTS_APP_ID/API_KEY, CLIENT_ID/SECRET, or SOUNDCHARTS_SANDBOX=1)",
            ),
        )

    @staticmethod
    def _metadata(operation: str, **extra: Any) -> dict[str, Any]:
        historical = operation in {
            AUDIENCE_HISTORY,
            STREAMING_HISTORY,
            LOCAL_STREAMING,
            AUDIENCE_REPORT_DATES,
        }
        return {
            "operation": operation,
            "provider_version": PROVIDER_VERSION,
            "licensed_source": "Soundcharts",
            "historical_strategy": (
                HISTORICAL_STRATEGY_LICENSED if historical else HISTORICAL_STRATEGY_FORWARD
            ),
            "rights_status": RIGHTS_STATUS,
            "commercial_use_status": COMMERCIAL_USE_STATUS,
            **extra,
        }

    # -- normalization ----------------------------------------------------- #
    @staticmethod
    def _normalize_payload(
        payload: Any,
        *,
        request: AcquisitionRequest,
        operation: str,
        retrieved_at: str,
    ) -> list[dict[str, Any]]:
        """Map the documented Soundcharts envelopes to canonical records.

        Envelope contract (verified against the live sandbox):
        * singles: ``{"type": str, "object": {...}}``
        * lists:   ``{"items": [...], "page": {...}, "related": {...}}``
        * stats:   ``{"related": {...}, "social": [...], "streaming": [...],
                      "popularity": [...], "retention": [...], "score": [...]}``

        Value extraction is explicit per operation below; the adapter never
        guesses which provider field is a monthly listener or follower count.
        """
        records: list[dict[str, Any]] = []
        related = payload.get("related") if isinstance(payload, dict) else None
        related_artist = None
        if isinstance(related, dict):
            artist = related.get("artist") if isinstance(related.get("artist"), dict) else related
            related_artist = artist.get("uuid") or artist.get("name")

        if operation in {CURRENT_STATS}:
            # stats arrays: social/streaming/popularity/retention/score — each
            # item is {platform, value, date, evolution, percentEvolution}.
            for family in ("social", "streaming", "popularity", "retention", "score"):
                for item in payload.get(family) or []:
                    if not isinstance(item, dict):
                        continue
                    records.append(
                        _record(
                            request=request,
                            operation=f"{operation}:{family}",
                            object_id=f"{family}/{item.get('platform')}",
                            platform=item.get("platform") or request.platform,
                            data=item,
                            retrieved_at=retrieved_at,
                            related_artist=related_artist,
                        )
                    )
            return records

        if isinstance(payload, dict) and "object" in payload:
            obj = payload["object"]
            if isinstance(obj, dict):
                records.append(
                    _record(
                        request=request,
                        operation=operation,
                        object_id=obj.get("uuid"),
                        platform=request.platform_scope or request.platform,
                        data=obj,
                        retrieved_at=retrieved_at,
                        related_artist=related_artist,
                    )
                )
            return records

        items = payload.get("items") if isinstance(payload, dict) else payload
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return records
        for item in items[: request.max_records or 500]:
            if not isinstance(item, dict):
                continue
            records.append(
                _record(
                    request=request,
                    operation=operation,
                    object_id=item.get("date") or item.get("uuid"),
                    platform=request.platform_scope or request.platform,
                    data=item,
                    retrieved_at=retrieved_at,
                    related_artist=related_artist,
                )
            )
        return records


def _record(
    *,
    request: AcquisitionRequest,
    operation: str,
    object_id: Any,
    platform: Any,
    data: dict[str, Any],
    retrieved_at: str,
    related_artist: Any = None,
) -> dict[str, Any]:
    historical = operation in {
        AUDIENCE_HISTORY,
        STREAMING_HISTORY,
        LOCAL_STREAMING,
        AUDIENCE_REPORT_DATES,
    }
    return {
        "platform": str(platform or "soundcharts").lower(),
        "provider": PROVIDER_VERSION,
        "object_type": operation.lower(),
        "platform_object_id": object_id,
        "artist_key": request.entity_id,
        "operation": operation,
        "data": data,
        "related_artist": related_artist,
        "retrieved_at": retrieved_at,
        "knowledge_time": retrieved_at,
        "knowledge_time_source": "licensed_provider_retrieval",
        "source": "soundcharts",
        "rights_status": RIGHTS_STATUS,
        "commercial_use_status": COMMERCIAL_USE_STATUS,
        "historical_strategy": (
            HISTORICAL_STRATEGY_LICENSED if historical else HISTORICAL_STRATEGY_FORWARD
        ),
    }


def _date_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _basic_auth(client_id: str, client_secret: str) -> str:
    import base64

    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


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
        observed = data.get("date") or retrieved.date()
        rows.append(
            build_factor_observation(
                artist_key=artist_key,
                factor_family="DEMAND",
                factor_name=factor_name,
                platform=str(record.get("platform") or "soundcharts"),
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
                measurement_basis=_MEASUREMENT_BASIS.get(factor_name, "PROVIDER_REPORTED"),
                measurement_window=data.get("window") or data.get("period"),
                population_scope="ALL_LISTENERS_ON_PLATFORM",
                geographic_scope="GLOBAL",
                methodology_version=PROVIDER_VERSION,
                coverage_generation=generation,
            )
        )
    return rows


_MEASUREMENT_BASIS = {
    "MONTHLY_LISTENERS": "MONTHLY_ACTIVE_LISTENERS",
    "FOLLOWERS": "TOTAL_FOLLOWERS",
    "SUBSCRIBERS": "TOTAL_SUBSCRIBERS",
    "POPULARITY": "PROVIDER_POPULARITY_SCORE",
}