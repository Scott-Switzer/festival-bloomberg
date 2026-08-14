"""Common Crawl historical-web provider.

Common Crawl is a free "web time machine": its CDX index lets us find
archived snapshots of a URL across monthly crawls. This provider:

* looks up archive captures for a target URL (``CC_INDEX_LOOKUP``),
* returns capture metadata (crawl ID, capture timestamp, WARC locator,
  status, content length, digest) as records — it does NOT download WARC
  records here (that is a separate, large-payload step),
* preserves PIT semantics: the *capture timestamp* is the archive's
  ``source_as_of``, while ``knowledge_time`` is the retrieval time (now).
  A page captured in 2021 must not be backdated into a 2019 feature set.

Rights are delegated to the UNDERLYING publisher/source: Common Crawl
availability does NOT grant universal commercial content rights. Claims
derived from Common Crawl must therefore carry the underlying source's
rights status, defaulting to UNKNOWN (fail closed).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

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

INDEX_API = "https://index.commoncrawl.org/"
PROVIDER_NAME = "commoncrawl_index"
PROVIDER_VERSION = "commoncrawl_cdx-v1"
CC_INDEX_LOOKUP = "CC_INDEX_LOOKUP"


class CommonCrawlProvider(BaseProvider):
    name = "commoncrawl"

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        return True

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="open_endpoint")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        retrieved_at = started.isoformat()
        url = (request.query or "").strip()
        operation = (request.operation or CC_INDEX_LOOKUP).upper()

        if operation != CC_INDEX_LOOKUP:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=INDEX_API,
                started_at=started,
                error_category="unsupported_operation",
                provider_metadata={"reason": f"unsupported operation {operation}"},
            )
        if not (url.startswith("http://") or url.startswith("https://")):
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=INDEX_API,
                started_at=started,
                error_category="query_not_url",
                provider_metadata={"reason": "query must be an http(s) URL"},
            )

        # CDX API: list available snapshots for this URL across crawls.
        index_coll = request.external_id or "CC-MAIN-2026-33"
        endpoint = f"{INDEX_API}{index_coll}-index"
        params = {
            "url": url,
            "output": "json",
            "limit": str(request.max_records or 10),
            "filter": "status:200",
            "fl": "timestamp,statuscode,urlkey,digest,length,mime",
        }
        try:
            response = self.transport.request(
                "GET",
                f"{endpoint}?{urlencode(params)}",
                headers={"User-Agent": "FestivalBloomberg/0.1 (research; historical-outcomes)"},
                timeout_seconds=45.0,
            )
        except TransportError as exc:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=endpoint,
                started_at=started,
                error_category="network",
                provider_metadata={"detail": str(exc)},
            )

        if response.status == 404:
            # No captures in this crawl index.
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=endpoint,
                started_at=started,
                error_category="no_captures",
                provider_metadata={"crawl_index": index_coll},
            )
        if response.status != 200:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=endpoint,
                started_at=started,
                error_category="http",
                provider_metadata={"http_status": response.status},
            )

        try:
            raw_text = response.body.decode("utf-8", errors="replace")
        except Exception:
            raw_text = ""

        captures = _parse_cdx_lines(raw_text)
        if not captures:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=endpoint,
                started_at=started,
                error_category="no_captures",
                provider_metadata={"crawl_index": index_coll},
            )

        records = tuple(
            _capture_record(c, request=request, retrieved_at=retrieved_at)
            for c in captures
        )
        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS,
            provider_endpoint=endpoint,
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(raw_text),
            provider_metadata={
                "crawl_index": index_coll,
                "provider_version": PROVIDER_VERSION,
                "captures": len(records),
                "cost_usd": 0.0,
            },
            records=records,
        )


def _parse_cdx_lines(raw_text: str) -> list[dict[str, str]]:
    """Parse CDX JSON-lines or plain CDX lines into dict rows."""
    out: list[dict[str, str]] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            import json

            row = json.loads(line)
            if isinstance(row, dict):
                out.append({k: str(v) for k, v in row.items()})
        except ValueError:
            # plain CDX: timestamp, statuscode, urlkey, digest, length, mime
            parts = line.split()
            if len(parts) >= 3:
                out.append(
                    {
                        "timestamp": parts[0],
                        "statuscode": parts[1],
                        "urlkey": parts[2],
                        "digest": parts[3] if len(parts) > 3 else None,
                        "length": parts[4] if len(parts) > 4 else None,
                        "mime": parts[5] if len(parts) > 5 else None,
                    }
                )
    return out


def _capture_record(capture: dict[str, str], *, request: AcquisitionRequest, retrieved_at: str) -> dict[str, Any]:
    """Normalize a CDX capture to a canonical record.

    The capture timestamp is the archive source_as_of. knowledge_time stays
    at retrieval (now) — the web archive does not backdate knowledge.
    """
    timestamp = capture.get("timestamp")
    crawl_id = request.external_id or "CC-MAIN"
    locator = (
        f"https://data.commoncrawl.org/{crawl_id}-index?url={request.query}"
    )
    return {
        "platform": "commoncrawl",
        "provider": PROVIDER_NAME,
        "object_type": "web_archive_capture",
        "platform_object_id": f"{request.query}#{timestamp}",
        "source_url": request.query,
        "source_as_of": _cdx_iso(timestamp) if timestamp else None,
        "retrieved_at": retrieved_at,
        "knowledge_time": retrieved_at,
        "knowledge_time_source": "retrieval",
        "crawl_id": crawl_id,
        "capture_timestamp": timestamp,
        "warc_locator": locator,
        "status_code": capture.get("statuscode"),
        "digest": capture.get("digest"),
        "content_length": capture.get("length"),
        "mime": capture.get("mime"),
        # Rights refer to the UNDERLYING publisher, not Common Crawl.
        "rights_status": "UNKNOWN",
        "commercial_use_status": "UNKNOWN",
        "provider_version": PROVIDER_VERSION,
        "content_hash": content_hash_of(
            {"url": request.query, "timestamp": timestamp, "digest": capture.get("digest")}
        ),
    }


def _cdx_iso(timestamp: str) -> str | None:
    """CDX timestamps are YYYYMMDDHHMMSS. Convert to ISO 8601 UTC."""
    t = timestamp.strip()
    if len(t) < 8 or not t.isdigit():
        return None
    year, month, day = t[0:4], t[4:6], t[6:8]
    hour = t[8:10] or "00"
    minute = t[10:12] or "00"
    second = t[12:14] or "00"
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"
