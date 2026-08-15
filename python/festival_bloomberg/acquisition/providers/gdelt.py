"""GDELT DOC 2.0 news-discovery provider (key-free, metadata-only).

Queries the GDELT DOC API's ``artlist`` mode, which returns *article
metadata* (URL, title, domain, first-seen timestamp, language, source
country) for news coverage of a query. Full copyrighted article text is
NEVER fetched or persisted here; this provider only supplies the metadata
that lets the terminal say "this entity was in the news at time T" and link
to the publisher's own page.

Operational semantics (verified against the live API):

- **No auth.** The endpoint is open, but it rate-limits hard: the live API
  asked for *one request every 5 seconds*. We therefore enforce a
  conservative default minimum spacing of 5 seconds between requests and
  treat 429 as ``RATE_LIMITED`` (never retried aggressively, never a green
  status).
- **Recent-only.** DOC 2.0 explicit date search covers roughly the prior
  three months; a plain keyword query returns recent coverage. We persist
  the article's own ``seendate`` as ``published_at`` and keep
  ``retrieved_at`` / ``knowledge_time`` distinct.
- **Metadata only.** ``content_role = "news_metadata"``; there is no article
  body in the record, so nothing copyrighted is stored or redistributed.

``request.query`` is the raw GDELT query string (e.g. ``"Taylor Swift"``
for an exact-phrase match, or ``Glastonbury festival`` for a term query).
"""

from __future__ import annotations

import json
import threading
import time
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

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_MAX_RECORDS = 25
DEFAULT_MIN_INTERVAL_SECONDS = 5.0

#: Shared, process-wide last-request timestamp so concurrent calls (or
#: multiple provider instances) cannot burst the open endpoint.
_request_lock = threading.Lock()
_last_request_monotonic: float = 0.0


def _throttle(min_interval_seconds: float) -> None:
    global _last_request_monotonic
    if min_interval_seconds <= 0:
        return
    with _request_lock:
        elapsed = time.monotonic() - _last_request_monotonic
        wait = min_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_request_monotonic = time.monotonic()


def _parse_seendate(value: Any) -> str | None:
    """GDELT ``seendate`` is ``YYYYMMDDTHHMMSSZ``; normalize to ISO-8601."""
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        dt = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    except ValueError:
        try:
            dt = datetime.strptime(digits[:8], "%Y%m%d")
        except ValueError:
            return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class GdeltProvider(BaseProvider):
    name = "gdelt"

    def __init__(
        self,
        transport=None,
        env: dict[str, str] | None = None,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.min_interval_seconds = min_interval_seconds

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        return True

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(
            provider=self.name,
            estimated_cost_usd=0.0,
            free_quota=True,
            source="open_endpoint",
        )

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        query = (request.query or "").strip()
        if not query:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=DOC_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="query_required",
                provider_metadata={"reason": "query must be a GDELT query string"},
            )

        max_records = max(1, min(request.max_records or DEFAULT_MAX_RECORDS, 75))
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(max_records),
            "sort": "datedesc",
        }
        url = f"{DOC_URL}?{urllib.parse.urlencode(params)}"

        _throttle(self.min_interval_seconds)
        try:
            response = self.transport.request(
                "GET", url, headers={"Accept": "application/json"}, timeout_seconds=30.0
            )
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                cost_usd=0.0,
                error_category="network",
                provider_metadata={"detail": str(exc)},
            )

        if response.status == 429:
            return self._result(
                request,
                status=AcquisitionStatus.RATE_LIMITED,
                provider_endpoint=url,
                started_at=started,
                cost_usd=0.0,
                error_category="rate_limited",
                provider_metadata={
                    "detail": response.body.decode("utf-8", errors="replace")[:200],
                    "retry_after_hint": ">=5s (provider-documented minimum spacing)",
                },
            )
        if response.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=url,
                started_at=started,
                cost_usd=0.0,
                error_category="http",
                provider_metadata={"http_status": response.status},
            )

        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=url,
                started_at=started,
                cost_usd=0.0,
                error_category="response_not_json",
            )

        articles = (payload or {}).get("articles") or []
        records = []
        for art in articles:
            if not isinstance(art, dict):
                continue
            article_url = art.get("url")
            title = art.get("title")
            if not article_url and not title:
                continue
            published = _parse_seendate(art.get("seendate"))
            records.append(
                {
                    "platform": "gdelt",
                    "object_type": "news_mention",
                    "platform_object_id": content_hash_of(article_url or title),
                    "article_url": article_url,
                    "title": title,
                    "domain": art.get("domain"),
                    "published_at": published,
                    "language": art.get("language"),
                    "sourcecountry": art.get("sourcecountry"),
                    "source_url": url,
                    "retrieved_at": utc_now().isoformat(),
                    "knowledge_time": utc_now().isoformat(),
                    "content_role": "news_metadata",
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
                "provider_version": "gdelt-doc-v1",
                "query": query,
                "articles_returned": len(articles),
            },
            records=tuple(records),
        )
