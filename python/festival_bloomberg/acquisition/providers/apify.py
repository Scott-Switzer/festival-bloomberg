"""Apify Actor provider for the Festival Signal Fabric.

Runs a specific, approved Apify Actor by ID and reads its default dataset.
The originating information source is the *underlying platform*, never
"Apify" itself; Apify is only the acquisition provider.

Credentials come from ``APIFY_TOKEN``. No token means ``NOT_CONFIGURED``,
never placeholder success. No paid calls are made by tests or CI.
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
from ..transport import TransportError

DEFAULT_BASE_URL = "https://api.apify.com/v2"

# Actor defaults are intentionally explicit and bounded. They are secondary
# acquisition rails; known YouTube channel IDs should continue through the
# official API first.
DEFAULT_ACTORS = {
    "youtube": "streamers/youtube-scraper",
    "tiktok": "clockworks/tiktok-scraper",
    "instagram": "apify/instagram-api-scraper",
}
ACTOR_RIGHTS_STATUS = "TERMS_REVIEW_REQUIRED"
ACTOR_COMMERCIAL_USE_STATUS = "TERMS_REVIEW_REQUIRED"


class ApifyProvider(BaseProvider):
    name = "apify"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        actor_id: str | None = None,
        base_url: str | None = None,
        max_polls: int = 20,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        super().__init__(transport=transport, env=env)
        #: Actor selection: explicit, env override, or per-platform default.
        self.actor_id = (
            actor_id
            or self.env.get("APIFY_ACTOR_ID")
            or self._platform_default_actor()
            or DEFAULT_ACTORS.get(str(self.env.get("APIFY_ACTOR_PLATFORM") or "").lower())
        )
        self.base_url = (base_url or self.env.get("APIFY_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.max_polls = max_polls
        self.poll_interval_seconds = poll_interval_seconds

    def _platform_default_actor(self) -> str | None:
        platform = self.env.get("APIFY_ACTOR_PLATFORM")
        if platform:
            return self.env.get(f"APIFY_ACTOR_{platform.upper().replace('-', '_')}")
        return None

    def health(self) -> ProviderHealth:
        if self.secret("APIFY_TOKEN") is None:
            return ProviderHealth(provider=self.name, healthy=False, last_error="no APIFY_TOKEN")
        if self.actor_id is None:
            return ProviderHealth(
                provider=self.name, healthy=False, last_error="no actor_id configured"
            )
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        # Apify does not expose a per-run price without a prior run; the
        # caller's budget gate still applies ($0.00 default).
        return CostEstimate(provider=self.name, estimated_cost_usd=None)

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        token = self.secret("APIFY_TOKEN")
        if token is None:
            return self._not_configured(request, "APIFY_TOKEN not set")
        if self.actor_id is None:
            return self._not_configured(request, "no Apify actor configured")

        started = utc_now()
        auth = {"Authorization": f"Bearer {token}"}
        endpoint = f"{self.base_url}/acts/{self.actor_id}/runs"

        operation = str(request.operation or "PLATFORM_DISCOVERY").strip().upper()
        body: dict[str, Any] = {
            "input": {
                "query": request.query,
                "platform": request.platform,
                "operation": operation,
                "maxItems": request.max_records,
            }
        }
        if request.start_time is not None:
            body["input"]["startTime"] = request.start_time.isoformat()
        if request.end_time is not None:
            body["input"]["endTime"] = request.end_time.isoformat()
        if request.market_id is not None:
            body["input"]["marketId"] = request.market_id

        try:
            run = self.transport.request(
                "POST", endpoint, headers=auth, body=body, timeout_seconds=30.0
            )
        except TransportError as exc:
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc)
            )

        if run.status == 401 or run.status == 403:
            return self._fail(
                request,
                started,
                AcquisitionStatus.PROVIDER_ERROR,
                "authentication",
                f"http {run.status}",
            )
        if run.status == 429:
            return self._fail(
                request,
                started,
                AcquisitionStatus.RATE_LIMITED,
                "rate_limited",
                f"http {run.status}",
            )
        if run.status != 201 and run.status != 200:
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "run", f"http {run.status}"
            )

        try:
            run_payload = run.json()
        except ValueError:
            return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "run_response")

        run_id = run_payload.get("id")
        state = run_payload.get("status", "RUNNING")

        polls = 0
        while state in ("RUNNING", "READY", "QUEUED") and polls < self.max_polls:
            time.sleep(self.poll_interval_seconds)
            try:
                status_resp = self.transport.request(
                    "GET",
                    f"{self.base_url}/actor-runs/{run_id}",
                    headers=auth,
                    timeout_seconds=30.0,
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
                run_payload = status_resp.json()
            except ValueError:
                return self._fail(
                    request, started, AcquisitionStatus.SCHEMA_INVALID, "run_status_response"
                )
            state = run_payload.get("status", "UNKNOWN")
            polls += 1

        if state == "SUCCEEDED":
            dataset_id = run_payload.get("defaultDatasetId")
            records = []
            if dataset_id:
                try:
                    items_resp = self.transport.request(
                        "GET",
                        f"{self.base_url}/datasets/{dataset_id}/items",
                        headers=auth,
                        params={"limit": request.max_records},
                        timeout_seconds=30.0,
                    )
                except TransportError as exc:
                    return self._fail(
                        request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc)
                    )
                if items_resp.status == 200:
                    try:
                        records = items_resp.json()
                        if not isinstance(records, list):
                            records = [records]
                    except ValueError:
                        return self._fail(
                            request, started, AcquisitionStatus.SCHEMA_INVALID, "dataset_items"
                        )
            normalized = self._normalize_records(records)
            return self._result(
                request,
                status=AcquisitionStatus.SUCCESS,
                provider_endpoint=endpoint,
                started_at=started,
                record_count=len(normalized),
                cost_usd=None,
                raw_payload_hash=content_hash_of(records),
                provider_metadata={
                    "run_id": run_id,
                    "actor_id": self.actor_id,
                    "state": state,
                    "polls": polls,
                    "dataset_id": dataset_id,
                    "operation": operation,
                    "rights_status": ACTOR_RIGHTS_STATUS,
                    "commercial_use_status": ACTOR_COMMERCIAL_USE_STATUS,
                },
                records=tuple(normalized),
            )

        if state in ("FAILED", "ABORTED", "TIMED-OUT"):
            return self._fail(
                request, started, AcquisitionStatus.PROVIDER_ERROR, "run_failed", state
            )

        return self._result(
            request,
            status=AcquisitionStatus.PARTIAL_SUCCESS,
            provider_endpoint=endpoint,
            started_at=started,
            provider_metadata={
                "run_id": run_id,
                "state": state,
                "polls": polls,
                "operation": operation,
                "rights_status": ACTOR_RIGHTS_STATUS,
                "commercial_use_status": ACTOR_COMMERCIAL_USE_STATUS,
            },
        )

    def _fail(self, request, started, status, category, detail=None) -> AcquisitionResult:
        return self._result(
            request,
            status=status,
            provider_endpoint=f"{self.base_url}/acts/{self.actor_id or '?'}/runs",
            started_at=started,
            error_category=category,
            provider_metadata={"detail": detail} if detail else {},
        )

    @staticmethod
    def _normalize_records(records: list[dict]) -> list[dict]:
        from ...social.normalize import normalize_actor_record

        return [normalize_actor_record(item) for item in records]
