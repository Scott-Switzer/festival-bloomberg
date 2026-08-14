"""Wikimedia (Wikipedia) provider — a key-free, CC-licensed real source.

Fetches the current page as an *immutable revision*: one ``action=query`` call
returns the revision id, revision timestamp and the plain-text extract of the
same revision together, so the content is provably tied to a stable version.

The record therefore carries ``knowledge_time_source = "source_revision"`` and
``source_revision_id`` / ``source_revision_time``; retrospective availability
is backdated to the revision time only because the exact version identity is
proven. The content role is ``ENCYCLOPEDIC`` — never ``FAN_GENERATED``.

The provider makes no paid call; ``estimate`` reports ``$0.00``.
"""

from __future__ import annotations

import urllib.parse

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

ACTION_BASE = "https://en.wikipedia.org/w/api.php"
PAGE_BASE = "https://en.wikipedia.org/wiki/"


def _title_slug(title: str) -> str:
    return title.strip().replace(" ", "_")


def page_url(title: str) -> str:
    return PAGE_BASE + urllib.parse.quote(_title_slug(title))


def revision_url(title: str) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "revisions|extracts",
            "rvprop": "ids|timestamp",
            "explaintext": "1",
            "format": "json",
            "formatversion": "2",
            "titles": title,
            "redirects": "1",
        }
    )
    return f"{ACTION_BASE}?{params}"


class WikimediaProvider(BaseProvider):
    name = "wikimedia"

    def health(self) -> ProviderHealth:
        # Key-free public source; healthy when the transport is present.
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="open_endpoint")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        title = request.query
        if not title or not title.strip():
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                error_category="query_not_title",
                provider_metadata={"reason": "query must be a Wikipedia page title"},
            )

        url = revision_url(title)
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

        try:
            payload = response.json()
        except (ValueError, TypeError):
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=url,
                started_at=started,
                error_category="response_not_json",
            )

        page = self._first_page(payload)
        if page is None:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=url,
                started_at=started,
                error_category="page_not_found",
            )

        extract = page.get("extract")
        if not extract:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=url,
                started_at=started,
                error_category="no_extract",
            )

        revisions = page.get("revisions") or []
        revision = revisions[0] if revisions else {}
        revid = revision.get("revid")
        timestamp = revision.get("timestamp")

        # Only backdate knowledge_time when an immutable revision id + time and
        # the corresponding content are all present in the same response.
        knowledge_time_source = "source_revision" if (revid and timestamp) else "retrieval"

        text = str(extract)
        record = {
            "platform": "wikipedia",
            "object_type": "encyclopedic_article",
            "platform_object_id": str(revid) if revid else None,
            "author_public_id": None,
            "text": text,
            "language": "en",
            "published_at": None,  # not a publication; revision time handled separately
            "media_type": "text",
            "canonical_url": page_url(page.get("title") or title),
            "source_url": url,
            "content_hash": content_hash_of(text),
            "content_role": "ENCYCLOPEDIC",
            "content_role_method": "source_type",
            "resolution_method": "EXACT_CANONICAL_URL",
            "resolution_evidence": "explicit Wikipedia title resolved to a canonical page URL",
            "source_revision_id": str(revid) if revid else None,
            "source_revision_time": timestamp,
            "knowledge_time_source": knowledge_time_source,
            "engagement": {},
        }

        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS,
            provider_endpoint=url,
            started_at=started,
            record_count=1,
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(payload),
            provider_metadata={
                "revid": revid,
                "revision_time": timestamp,
                "knowledge_time_source": knowledge_time_source,
            },
            records=(record,),
        )

    @staticmethod
    def _first_page(payload: dict) -> dict | None:
        pages = (payload or {}).get("query", {}).get("pages") or []
        if not pages:
            return None
        return pages[0] if isinstance(pages, list) else next(iter(pages.values()), None)
