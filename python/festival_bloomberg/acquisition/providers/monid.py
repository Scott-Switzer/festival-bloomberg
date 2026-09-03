"""Monid provider for the Festival Signal Fabric.

Implements the current Monid API contract (verified against
https://docs.monid.ai/api):

* ``POST /v1/discover``      ``{query, limit}`` → matching endpoints
* ``POST /v1/inspect``       ``{provider, endpoint}`` → input schema + pricing
* ``POST /v1/run``           ``{provider, endpoint, input}`` → 200 sync | 202 async
* ``GET  /v1/runs/{runId}``  poll asynchronous runs
* ``GET  /v1/wallet/balance`` account balance for cost accounting

Discipline (Monid's own rule): DISCOVER → INSPECT → RUN. The input payload is
built from the endpoint's inspected ``inputSchema`` properties — the adapter
never guesses an endpoint's schema. When inspect does not expose a schema
(observed with some actors), the endpoint must be in the explicit pinned
allowlist below with schema sourced from the provider's published
documentation; anything else refuses to run.

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

#: Endpoints whose inspect() does not expose an inputSchema (observed live
#: with ``/apidojo/tiktok-profile-scraper``). They may only run with an
#: explicitly pinned input builder sourced from the provider's published
#: documentation. Never add an endpoint here without a documented schema.
PINNED_ENDPOINT_INPUTS: dict[str, dict[str, Any]] = {
    "/apidojo/tiktok-profile-scraper": {
        "schema_source": "apify.com/apidojo/tiktok-profile-scraper Input tab",
        "input": {"username": "{handle}", "limit": "{max_records}"},
    },
    "/streamers/youtube-comments-scraper": {
        "schema_source": "apify.com/streamers/youtube-comments-scraper Input tab",
        "input": {"videoUrls": ["{canonical_url}"], "maxComments": "{max_records}"},
    },
}

#: Aliases matched (case-insensitively) against inspected inputSchema property
#: names. Only these are ever populated; unknown schema keys stay untouched.
_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "handle": ("username", "profile", "user", "handle", "query", "q"),
    "search": ("searchTerms", "search", "keywords", "searchKeywords", "query", "q"),
    "max_records": ("maxItems", "maxResults", "maxComments", "resultsLimit", "limit"),
    "start_time": ("startDate", "start_date", "publishedAfter", "startTime"),
    "end_time": ("endDate", "end_date", "publishedBefore", "endTime"),
    "market": ("market", "marketId", "geo", "countryCode"),
}


def operation_for_request(request: AcquisitionRequest) -> str:
    """Resolve a bounded Monid intent without changing legacy requests."""
    requested = str(request.operation or "").strip().upper()
    if requested in SUPPORTED_OPERATIONS:
        return requested
    platform = str(request.platform or "").strip().lower()
    if platform in {"youtube", "video", "tiktok", "instagram"}:
        return "VIDEO_SEARCH" if platform == "youtube" else "SOCIAL_POSTS"
    return "PLATFORM_DISCOVERY"


def _discovery_query(request: AcquisitionRequest, operation: str) -> str:
    """Natural-language catalog query derived from a bounded intent."""
    platform = str(request.platform or "").strip().lower()
    entity = str(request.entity_id or request.query or "").strip()
    intent_phrases = {
        "SOCIAL_PROFILE": "profile stats and followers",
        "SOCIAL_POSTS": "recent posts",
        "SOCIAL_COMMENTS": "comments and engagement",
        "VIDEO_SEARCH": "video search",
        "PLATFORM_DISCOVERY": "public profile data",
    }
    phrase = intent_phrases.get(operation, "public profile data")
    if platform:
        return f"{platform} {phrase} for {entity}".strip()
    return f"{phrase} for {entity}".strip()


class MonidProvider(BaseProvider):
    name = "monid"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        base_url: str | None = None,
        max_polls: int = 30,
        poll_interval_seconds: float = 2.0,
        discover_limit: int = 5,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (base_url or self.env.get("MONID_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_polls = max_polls
        self.poll_interval_seconds = poll_interval_seconds
        self.discover_limit = max(1, min(int(discover_limit), 20))

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
                headers=self._headers(key),
            )
            if response.status == 200:
                payload = response.json()
                inner = payload.get("balance") or {}
                if "value" in inner:
                    return CostEstimate(
                        provider=self.name,
                        estimated_cost_usd=float(inner.get("value") or 0.0),
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
        headers = self._headers(key)

        # 1. DISCOVER — natural-language catalog search for the bounded intent.
        try:
            discover = self._request(
                "POST",
                f"{self.base_url}/v1/discover",
                headers=headers,
                body={"query": _discovery_query(request, operation), "limit": self.discover_limit},
            )
        except TransportError as exc:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc))
        if discover.status in (401, 403):
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "authentication", f"http {discover.status}")
        if discover.status == 429:
            return self._fail(request, started, AcquisitionStatus.RATE_LIMITED, "rate_limited", f"http {discover.status}")
        if discover.status != 200:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "discover", f"http {discover.status}")
        try:
            discover_payload = discover.json()
        except ValueError:
            return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "discover_response")
        results = discover_payload.get("results") or []
        if not results:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=f"{self.base_url}/v1/discover",
                started_at=started,
                provider_metadata={"phase": "discover", "endpoints_found": 0, "operation": operation},
            )
        candidate = results[0]
        provider_slug = candidate.get("provider")
        endpoint_path = candidate.get("endpoint")
        if not provider_slug or not endpoint_path:
            return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "discover_fields")
        price_info = candidate.get("price") or {}

        # 2. INSPECT — schema + pricing for the discovered endpoint.
        input_schema: dict[str, Any] = {}
        cost_per_call = _price_amount(price_info)
        try:
            inspect = self._request(
                "POST",
                f"{self.base_url}/v1/inspect",
                headers=headers,
                body={"provider": provider_slug, "endpoint": endpoint_path},
            )
            if inspect.status == 200:
                inspected = inspect.json()
                input_schema = inspected.get("inputSchema") or {}
                inspected_price = _price_amount(inspected.get("price") or {})
                if inspected_price is not None:
                    cost_per_call = inspected_price
        except (TransportError, ValueError):
            pass  # inspect is best-effort; run still proceeds when schema is known

        # 3. RUN — build input from the schema; never invent keys.
        input_payload, schema_used = self._build_input(
            request=request,
            operation=operation,
            endpoint_path=endpoint_path,
            input_schema=input_schema,
        )
        if input_payload is None:
            return self._fail(
                request,
                started,
                AcquisitionStatus.SCHEMA_INVALID,
                "input_schema_unknown",
                f"endpoint {provider_slug}{endpoint_path} exposes no inputSchema and is not pinned",
            )
        try:
            run = self._request(
                "POST",
                f"{self.base_url}/v1/run",
                headers=headers,
                body={"provider": provider_slug, "endpoint": endpoint_path, "input": input_payload},
            )
        except TransportError as exc:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc))
        if run.status == 429:
            return self._fail(request, started, AcquisitionStatus.RATE_LIMITED, "rate_limited", f"http {run.status}")

        # 4. POLL asynchronous runs (202 with runId) or use the sync 200 body.
        run_payload: dict[str, Any] = {}
        run_id: str | None = None
        run_state = "COMPLETED"
        if run.status in (200, 202):
            try:
                run_payload = run.json()
            except ValueError:
                return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "run_response")
            run_id = run_payload.get("runId") or run_payload.get("run_id")
            run_state = str(run_payload.get("status") or "COMPLETED").upper()
        else:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "run", f"http {run.status}")

        polls = 0
        while run_id and run_state in ("RUNNING", "QUEUED", "PENDING", "STARTED") and polls < self.max_polls:
            time.sleep(self.poll_interval_seconds)
            try:
                status_resp = self._request(
                    "GET",
                    f"{self.base_url}/v1/runs/{run_id}",
                    headers=headers,
                )
            except TransportError as exc:
                return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc))
            if status_resp.status == 404:
                return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "run_not_found", f"run {run_id}")
            if status_resp.status != 200:
                return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "run_status", f"http {status_resp.status}")
            try:
                run_payload = status_resp.json()
            except ValueError:
                return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "run_status_response")
            run_state = str(run_payload.get("status") or "UNKNOWN").upper()
            polls += 1

        if run_state == "FAILED":
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "run_failed", "monid infrastructure error")

        provider_http = (run_payload.get("providerResponse") or {}).get("httpStatus")
        output = run_payload.get("output")
        if provider_http and int(provider_http) >= 400:
            category = "not_found" if int(provider_http) == 404 else (
                "rate_limited" if int(provider_http) == 429 else "provider_http"
            )
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, category, f"provider http {provider_http}")
        if output is None:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=f"{self.base_url}/v1/run",
                started_at=started,
                cost_usd=_billing_usd(run_payload),
                provider_metadata={
                    "phase": "run",
                    "provider": provider_slug,
                    "endpoint": endpoint_path,
                    "run_id": run_id,
                    "polls": polls,
                    "final_state": run_state,
                    "operation": operation,
                    "schema_used": schema_used,
                    "supported_operations": sorted(SUPPORTED_OPERATIONS),
                },
            )

        # 5. NORMALIZE + HASH.
        records = self._normalize_output(output)
        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS,
            provider_endpoint=f"{self.base_url}/v1/run",
            started_at=started,
            record_count=len(records),
            cost_usd=_billing_usd(run_payload) if _billing_usd(run_payload) is not None else cost_per_call,
            raw_payload_hash=content_hash_of(output),
            provider_metadata={
                "phase": "run",
                "provider": provider_slug,
                "endpoint": endpoint_path,
                "run_id": run_id,
                "polls": polls,
                "final_state": run_state,
                "provider_http_status": provider_http,
                "operation": operation,
                "schema_used": schema_used,
                "cost_per_call_usd": cost_per_call,
                "supported_operations": sorted(SUPPORTED_OPERATIONS),
            },
            records=tuple(records),
        )

    # -- helpers ------------------------------------------------------------ #
    def _headers(self, key: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key or self.secret('MONID_API_KEY') or ''}",
            "Content-Type": "application/json",
        }

    def _build_input(
        self,
        *,
        request: AcquisitionRequest,
        operation: str,
        endpoint_path: str,
        input_schema: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """Build a run input from the inspected schema (or a pinned fallback).

        Returns ``(None, ...)`` when the schema is unknown and the endpoint is
        not pinned — the adapter refuses to guess.
        """
        properties = input_schema.get("properties") if isinstance(input_schema, dict) else None
        if properties:
            return self._schema_input(request, properties, operation), "inspected_inputSchema"
        pinned = PINNED_ENDPOINT_INPUTS.get(endpoint_path)
        if pinned:
            return self._pinned_input(request, pinned["input"]), f"pinned:{pinned['schema_source']}"
        return None, "unknown_schema"

    def _schema_input(
        self, request: AcquisitionRequest, properties: dict[str, Any], operation: str
    ) -> dict[str, Any]:
        """Populate only schema properties that match known aliases.

        Unknown schema keys stay untouched — nothing is invented.
        """
        # Build a lookup of lowercased property name -> (canonical name, info).
        prop_lookup = {
            str(name).lower(): (name, info) for name, info in properties.items()
        }
        values: dict[str, Any] = {
            "handle": request.entity_id or request.query,
            "search": request.query or request.entity_id,
            "max_records": request.max_records,
            "start_time": request.start_time.isoformat() if request.start_time else None,
            "end_time": request.end_time.isoformat() if request.end_time else None,
            "market": request.market_id,
        }
        output: dict[str, Any] = {}
        for alias, candidates in _INPUT_ALIASES.items():
            value = values.get(alias)
            if value is None:
                continue
            matched = next(
                (prop_lookup[c.lower()] for c in candidates if c.lower() in prop_lookup),
                None,
            )
            if matched is None:
                continue
            prop_name, prop_info = matched
            prop_type = str(prop_info.get("type") or "").lower()
            if "array" in prop_type:
                output[prop_name] = [value] if not isinstance(value, list) else value
            elif "number" in prop_type or "integer" in prop_type:
                try:
                    output[prop_name] = int(value)
                except (TypeError, ValueError):
                    output[prop_name] = value
            else:
                output[prop_name] = value
        return output

    def _pinned_input(self, request: AcquisitionRequest, template: dict[str, Any]) -> dict[str, Any]:
        handle = request.entity_id or request.query
        url = getattr(request, "canonical_url", None) or f"https://www.tiktok.com/@{handle}"
        max_records = request.max_records or 20

        def fill(value: Any) -> Any:
            if isinstance(value, str):
                return (
                    value.replace("{handle}", str(handle))
                    .replace("{max_records}", str(max_records))
                    .replace("{canonical_url}", str(url))
                )
            if isinstance(value, list):
                return [fill(item) for item in value]
            return value

        return {key: fill(value) for key, value in template.items()}

    def _normalize_output(self, output: Any) -> list[dict[str, Any]]:
        from ...social.normalize import normalize_monid_record

        if isinstance(output, list):
            items = output
        elif isinstance(output, dict):
            items = output.get("data") or output.get("results") or output.get("items") or [output]
        else:
            items = []
        return [normalize_monid_record(item) for item in items if isinstance(item, dict)]

    def _request(self, method: str, url: str, *, headers=None, body=None) -> HttpResponse:
        return self.transport.request(method, url, headers=headers, body=body, timeout_seconds=45.0)

    def _fail(self, request, started, status, category, detail=None) -> AcquisitionResult:
        return self._result(
            request,
            status=status,
            provider_endpoint=f"{self.base_url}/v1",
            started_at=started,
            error_category=category,
            provider_metadata={"detail": detail} if detail else {},
        )


def _price_amount(price: dict[str, Any]) -> float | None:
    amount = price.get("amount")
    if isinstance(amount, dict):
        amount = amount.get("value")
    if amount is None:
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def _billing_usd(run_payload: dict[str, Any]) -> float | None:
    billing = run_payload.get("billing") or {}
    calculated = billing.get("calculatedCost") or {}
    value = calculated.get("value")
    if value is None:
        return None
    try:
        return float(value) / 1_000_000.0  # MICRO_DOLLAR
    except (TypeError, ValueError):
        return None