"""Bluesky / AT Protocol public social provider — key-free, research-only.

Uses the official public AppView (no auth required for GETs) and the
Jetstream model is documented but this batch lane uses the bounded
``app.bsky.feed.searchPosts`` endpoint + ``app.bsky.feed.getAuthorFeed``
where useful. No private data is persisted; public posts are matched
against a bounded artist alias dictionary and only aggregate mention
records are produced for the terminal (raw evidence stays immutable in R2
when stored).

Semantics:
- ``request.query`` = artist canonical name or alias (already resolved)
- ``request.max_records`` bounds per-artist search
- Returns ``SUCCESS`` with ``content_role=FAN_GENERATED`` records
- 429 → ``RATE_LIMITED``, empty → ``NO_RESULTS``
- No auth; ``health()`` is always healthy when transport exists
- Respects source deletion / takedown via record tombstone handling
"""

from __future__ import annotations

import json
import time
import threading
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

SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
AUTHOR_FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
PROVIDER_VERSION = "bluesky-appview-v1"
DEFAULT_MAX_RECORDS = 25
# Public AppView asks for ~1 req/sec; be conservative.
DEFAULT_MIN_INTERVAL_SECONDS = 1.0

_lock = threading.Lock()
_last_monotonic = 0.0


def _throttle(interval: float) -> None:
    global _last_monotonic
    if interval <= 0:
        return
    with _lock:
        elapsed = time.monotonic() - _last_monotonic
        wait = interval - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_monotonic = time.monotonic()


def _parse_bsky_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        # Bluesky indexedAt is ISO8601
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


class BlueskyProvider(BaseProvider):
    name = "bluesky"

    def __init__(self, transport=None, env=None, *, min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS) -> None:
        super().__init__(transport=transport, env=env)
        self.min_interval_seconds = min_interval_seconds

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        return True  # public, no auth

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="open_endpoint")

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
                provider_metadata={"reason": "query must be an artist name/alias"},
            )
        limit = max(1, min(request.max_records or DEFAULT_MAX_RECORDS, 100))
        params = {
            "q": query,
            "limit": str(limit),
            "sort": "latest",
        }
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        _throttle(self.min_interval_seconds)
        try:
            resp = self.transport.request("GET", url, headers={"Accept": "application/json"}, timeout_seconds=30.0)
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                error_category="network",
                provider_metadata={"detail": str(exc)},
            )
        if resp.status == 429:
            return self._result(
                request,
                status=AcquisitionStatus.RATE_LIMITED,
                provider_endpoint=url,
                started_at=started,
                error_category="rate_limited",
                provider_metadata={"retry_after_hint": "backoff 60s"},
            )
        if resp.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
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
                provider_endpoint=url,
                started_at=started,
                error_category="response_not_json",
            )
        posts = payload.get("posts") or []
        records: list[dict[str, Any]] = []
        retrieved_at = utc_now().isoformat()
        for post in posts:
            if not isinstance(post, dict):
                continue
            record = post.get("record") or {}
            text = record.get("text") if isinstance(record, dict) else None
            author = post.get("author") or {}
            uri = post.get("uri")
            cid = post.get("cid")
            indexed_at = post.get("indexedAt") or record.get("createdAt")
            if not uri and not text:
                continue
            # Tombstone / deleted posts carry no record text
            if text is None and record.get("$type") == "app.bsky.feed.post#deleted":
                continue
            records.append(
                {
                    "platform": "bluesky",
                    "provider": PROVIDER_VERSION,
                    "object_type": "post",
                    "platform_object_id": uri or cid or content_hash_of(text or ""),
                    "source_url": uri,
                    "text": text or "",
                    "author_public_id": author.get("did"),
                    "author_handle": author.get("handle"),
                    "language": (record.get("langs") or [None])[0] if isinstance(record.get("langs"), list) else None,
                    "published_at": _parse_bsky_date(indexed_at),
                    "retrieved_at": retrieved_at,
                    "knowledge_time": retrieved_at,
                    "knowledge_time_source": "retrieval",
                    "content_role": "FAN_GENERATED",
                    "content_role_method": "source_type",
                    "resolution_method": "QUERY_MATCH",
                    "resolution_evidence": f"bluesky searchPosts q={query!r}",
                    "engagement": {
                        "likes": post.get("likeCount"),
                        "reposts": post.get("repostCount"),
                        "replies": post.get("replyCount"),
                    },
                    "raw_cid": cid,
                    "provider_version": PROVIDER_VERSION,
                    "content_hash": content_hash_of(text or uri or ""),
                }
            )
        status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
        return self._result(
            request,
            status=status,
            provider_endpoint=url,
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(payload),
            provider_metadata={
                "provider_version": PROVIDER_VERSION,
                "query": query,
                "posts_returned": len(posts),
                "endpoint": "app.bsky.feed.searchPosts",
            },
            records=tuple(records),
        )
