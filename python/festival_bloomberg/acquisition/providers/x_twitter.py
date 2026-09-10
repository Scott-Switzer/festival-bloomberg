"""X / Twitter public search provider — official API only, fail-closed.

Uses the X API v2 ``/2/tweets/search/recent`` endpoint when a bearer
token is present. No scraping, no auth bypass, no CAPTCHA workarounds.

Credentials inspected (names only, never values):
- X_BEARER_TOKEN / TWITTER_BEARER_TOKEN / BEARER_TOKEN

When no credential exists: returns NOT_CONFIGURED (BLOCKED_BY_CREDENTIAL).
When credential exists but search fails: maps 429→RATE_LIMITED, 401/403→PROVIDER_ERROR.

Query semantics: ``request.query`` is the artist name/alias (already
validated). The provider appends ``-is:retweet`` to reduce duplicate
amplification. ``request.max_records`` bounds ``max_results`` (10..100).
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
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

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
SEARCH_URL_ALT = "https://api.twitter.com/2/tweets/search/recent"
PROVIDER_VERSION = "x_search_recent_v1"
DEFAULT_MAX_RESULTS = 25


def _bearer_present(env: dict[str, str]) -> str | None:
    for key in ("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN", "BEARER_TOKEN", "X_API_BEARER_TOKEN"):
        v = env.get(key)
        if v and v.strip():
            return key
    return None


class XProvider(BaseProvider):
    name = "x"

    def _creds(self) -> tuple[str | None, str | None]:
        # returns (token, key_name) or (None, None)
        for key in ("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN", "BEARER_TOKEN", "X_API_BEARER_TOKEN"):
            v = self.secret(key)
            if v:
                return v, key
        return None, None

    def health(self) -> ProviderHealth:
        token, _ = self._creds()
        if token is None:
            return ProviderHealth(provider=self.name, healthy=False, last_error="no bearer token")
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        token, _ = self._creds()
        return token is not None

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        # X recent search is paid; report $0 here until billing is wired —
        # never fabricate a charge.
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=False, source="x_api_v2")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        query = (request.query or "").strip()
        if not query:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=SEARCH_URL,
                started_at=started,
                error_category="query_required",
                provider_metadata={"reason": "query must be artist name"},
            )
        token, key_name = self._creds()
        if token is None:
            return self._not_configured(request, "X_BEARER_TOKEN / TWITTER_BEARER_TOKEN not set — BLOCKED_BY_CREDENTIAL")

        # Bounded max_results: 10..100
        max_results = max(10, min(request.max_records or DEFAULT_MAX_RESULTS, 100))
        # Reduce retweet amplification; keep query as-is otherwise.
        q = f"{query} -is:retweet"
        params = {
            "query": q,
            "max_results": str(max_results),
            "tweet.fields": "created_at,author_id,public_metrics,lang,entities",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        try:
            resp = self.transport.request(
                "GET",
                SEARCH_URL,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params=params,
                timeout_seconds=30.0,
            )
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=SEARCH_URL,
                started_at=started,
                error_category="network",
                provider_metadata={"detail": str(exc), "credential_key": key_name},
            )
        if resp.status == 429:
            return self._result(
                request,
                status=AcquisitionStatus.RATE_LIMITED,
                provider_endpoint=SEARCH_URL,
                started_at=started,
                error_category="rate_limited",
                provider_metadata={"http_status": 429, "credential_key": key_name},
            )
        if resp.status in (401, 403):
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=SEARCH_URL,
                started_at=started,
                error_category="auth_invalid",
                provider_metadata={"http_status": resp.status, "credential_key": key_name},
            )
        if resp.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=SEARCH_URL,
                started_at=started,
                error_category="http",
                provider_metadata={"http_status": resp.status},
            )
        try:
            payload = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=SEARCH_URL,
                started_at=started,
                error_category="response_not_json",
            )
        data = payload.get("data") or []
        includes = payload.get("includes") or {}
        users_by_id = {u.get("id"): u for u in (includes.get("users") or []) if isinstance(u, dict)}
        records: list[dict[str, Any]] = []
        retrieved_at = utc_now().isoformat()
        for tweet in data:
            if not isinstance(tweet, dict):
                continue
            tid = tweet.get("id")
            text = tweet.get("text")
            if not tid and not text:
                continue
            author_id = tweet.get("author_id")
            user = users_by_id.get(author_id) or {}
            created = tweet.get("created_at")
            try:
                published = datetime.fromisoformat(created.replace("Z", "+00:00")).isoformat() if created else None
            except Exception:
                published = created
            metrics = tweet.get("public_metrics") or {}
            records.append(
                {
                    "platform": "x",
                    "provider": PROVIDER_VERSION,
                    "object_type": "post",
                    "platform_object_id": tid or content_hash_of(text or ""),
                    "source_url": f"https://x.com/i/web/status/{tid}" if tid else None,
                    "text": text or "",
                    "author_public_id": author_id,
                    "author_username": user.get("username"),
                    "author_name": user.get("name"),
                    "language": tweet.get("lang"),
                    "published_at": published,
                    "retrieved_at": retrieved_at,
                    "knowledge_time": retrieved_at,
                    "knowledge_time_source": "retrieval",
                    "content_role": "FAN_GENERATED",
                    "content_role_method": "source_type",
                    "resolution_method": "QUERY_MATCH",
                    "resolution_evidence": f"x search recent q={query!r}",
                    "engagement": {
                        "likes": metrics.get("like_count"),
                        "reposts": metrics.get("retweet_count"),
                        "replies": metrics.get("reply_count"),
                        "quotes": metrics.get("quote_count"),
                        "views": metrics.get("impression_count"),
                    },
                    "provider_version": PROVIDER_VERSION,
                    "content_hash": content_hash_of(text or tid or ""),
                }
            )
        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=SEARCH_URL,
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(payload),
            provider_metadata={
                "provider_version": PROVIDER_VERSION,
                "query": query,
                "tweets_returned": len(data),
                "endpoint": "tweets/search/recent",
                "credential_key": key_name,
            },
            records=tuple(records),
        )
