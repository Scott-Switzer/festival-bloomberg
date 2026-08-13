"""Optional self-hosted adaptive provider backed by Scrapling.

Scrapling is deliberately NOT a hard dependency. When the package is not
installed the provider reports ``NOT_CONFIGURED`` (dependency missing) —
never placeholder success. When installed AND the source passes the policy
gate (enforced by the router), it fetches a public page and returns the
extracted text plus raw content hash.

Scraped content is UNTRUSTED DATA: it is returned as inert observation text
and must never be injected into agent prompts as instructions.
"""

from __future__ import annotations

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

#: Set by the import probe below; False when scrapling is not installed.
SCRAPLING_AVAILABLE = False
try:  # pragma: no cover - exercised only when scrapling is installed
    import scrapling  # type: ignore  # noqa: F401

    SCRAPLING_AVAILABLE = True
except ImportError:
    pass


class ScraplingProvider(BaseProvider):
    name = "scrapling"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        fetch_mode: str = "static",
        max_bytes: int = 2_000_000,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.fetch_mode = fetch_mode
        self.max_bytes = max_bytes

    def health(self) -> ProviderHealth:
        if not SCRAPLING_AVAILABLE:
            return ProviderHealth(provider=self.name, healthy=False, last_error="scrapling not installed")
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="self_hosted")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        if not SCRAPLING_AVAILABLE:  # pragma: no cover - depends on env
            return self._not_configured(request, "scrapling package not installed")

        url = request.query
        if not (url.startswith("http://") or url.startswith("https://")):
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint="scrapling",
                started_at=utc_now(),
                error_category="query_not_url",
                provider_metadata={"reason": "query must be an http(s) URL"},
            )

        started = utc_now()
        try:
            # Best-effort use of the installed version's fetch API. Keep the
            # integration small and isolated; policy + untrusted-data handling
            # live outside this class.
            from scrapling import Fetcher  # type: ignore

            fetcher = Fetcher()
            page = fetcher.get(url) if self.fetch_mode == "static" else fetcher.post(url, data={})
            html = getattr(page, "html_content", None) or getattr(page, "content", b"") or b""
            if isinstance(html, str):
                html = html.encode("utf-8")
        except Exception as exc:  # pragma: no cover - depends on scrapling
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint="scrapling",
                started_at=started,
                error_category="fetch",
                provider_metadata={"detail": str(exc)[:200]},
            )

        if len(html) > self.max_bytes:  # pragma: no cover
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint="scrapling",
                started_at=started,
                error_category="payload_too_large",
                provider_metadata={"bytes": len(html)},
            )

        raw_hash = content_hash_of(html)
        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS,
            provider_endpoint="scrapling",
            started_at=started,
            record_count=1,
            cost_usd=0.0,
            raw_payload_hash=raw_hash,
            provider_metadata={"bytes": len(html), "fetch_mode": self.fetch_mode},
            records=(
                {
                    "platform": request.platform,
                    "object_type": "web_page",
                    "platform_object_id": None,
                    "text": None,
                    "source_url": url,
                    "raw_bytes": len(html),
                },
            ),
        )
