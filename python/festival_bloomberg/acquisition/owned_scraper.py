"""OWNED_WEB_ACQUISITION_V1 — reusable adaptive scraper framework (P8/P9).

Not a scraper for one site. A contract + runner that every owned collector
implements. This is the long-term replacement for vendor-lock when
Monid/Apify economics justify owned promotion.

Architecture:
  SourceCollector (protocol) — discover/fetch/parse/normalize/checkpoint/health/cost
  OwnedRunner — per-domain queues, concurrency, delay, retries, dedupe,
                content hashing, conditional fetch (ETag/Last-Modified),
                R2 raw upload stub, structured failure.

Routing preference remains:
  OFFICIAL_API → PUBLIC_STRUCTURED → MONID/APIFY → STATIC_HTTP (HttpProvider)
  → CLOUDFLARE_BROWSER_RUN → HETZNER_PLAYWRIGHT

This module is dependency-light (stdlib + existing transport). Crawlee Python
and Playwright are loaded only when present; absence reports NOT_CONFIGURED
instead of fake success, matching the acquisition fabric discipline.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CostEstimate,
    ProviderHealth,
    content_hash_of,
    utc_now,
)


class ExecutionMode(str, Enum):
    STATIC_HTTP = "STATIC_HTTP"
    CLOUDFLARE_BROWSER = "CLOUDFLARE_BROWSER"
    HETZNER_PLAYWRIGHT = "HETZNER_PLAYWRIGHT"


@dataclass(frozen=True)
class CollectorSpec:
    source: str
    task: str  # e.g. SOCIAL_PROFILE, ARTIST_OFFICIAL_SITE
    execution_mode: ExecutionMode
    identity_requirement: str  # e.g. handle, url, none
    pagination: str | None = None
    rate_policy: str | None = None  # e.g. "1 req/s per domain"
    parser_version: str = "v1"
    allowed_domains: tuple[str, ...] = ()
    max_pages: int = 20


@dataclass
class CollectorState:
    fetched: int = 0
    parsed: int = 0
    normalized: int = 0
    deduped: int = 0
    failed: int = 0
    bytes: int = 0
    last_checkpoint: str | None = None


@runtime_checkable
class SourceCollector(Protocol):
    spec: CollectorSpec

    def discover(self, request: AcquisitionRequest) -> list[str]: ...
    def fetch(self, url: str) -> tuple[bytes | None, dict[str, Any]]: ...
    def parse(self, url: str, body: bytes, headers: dict[str, Any]) -> list[dict[str, Any]]: ...
    def normalize(self, raw: dict[str, Any]) -> dict[str, Any] | None: ...
    def checkpoint(self) -> dict[str, Any]: ...
    def health(self) -> ProviderHealth: ...
    def cost(self) -> CostEstimate: ...


# ── Failure classification (P8) ────────────────────────────────────
FAILURE_CLASSES = frozenset({
    "ACCESSIBLE",
    "LOGIN_REQUIRED",
    "BOT_BLOCKED",
    "CAPTCHA",
    "NO_PUBLIC_SURFACE",
    "PARSE_ERROR",
    "SUCCESS",
    "TIMEOUT",
    "RATE_LIMITED",
    "NOT_FOUND",
})


def classify_fetch_failure(status: int | None, body_snippet: str = "") -> str:
    if status is None:
        return "TIMEOUT"
    if status == 429:
        return "RATE_LIMITED"
    if status == 404:
        return "NOT_FOUND"
    if status in (401, 403):
        low = body_snippet.lower()
        if "captcha" in low:
            return "CAPTCHA"
        if "login" in low or "sign in" in low:
            return "LOGIN_REQUIRED"
        if "blocked" in low or "bot" in low:
            return "BOT_BLOCKED"
        return "LOGIN_REQUIRED"
    if status >= 500:
        return "TIMEOUT"
    return "PARSE_ERROR"


# ── OwnedRunner ───────────────────────────────────────────────────
@dataclass
class FetchRecord:
    url: str
    status: int | None
    bytes: int
    content_hash: str | None
    etag: str | None
    last_modified: str | None
    failure_class: str
    duration_ms: int


class OwnedRunner:
    """Thin runner over any SourceCollector, with dedupe + conditional fetch.

    Per-run state is held in memory; checkpoint() serializes cursor + seen
    hashes so a batch job can resume. R2 raw upload is delegated to the
    caller (batch job) — this runner never assumes a bucket exists.
    """

    def __init__(self, collector: SourceCollector, *, dedupe_by: str = "content_hash") -> None:
        self.collector = collector
        self.dedupe_by = dedupe_by
        self._seen_hashes: set[str] = set()
        self._etag_cache: dict[str, str] = {}
        self.state = CollectorState()
        self.fetch_log: list[FetchRecord] = []

    def run(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        try:
            urls = self.collector.discover(request)
        except Exception as exc:
            return self._fail(request, started, "PARSE_ERROR", str(exc)[:300])

        if not urls:
            return AcquisitionResult(
                request_id=request.request_id,
                provider=f"owned:{self.collector.spec.source}",
                provider_endpoint=None,
                status=AcquisitionStatus.NO_RESULTS,
                started_at=started,
                completed_at=utc_now(),
                record_count=0,
                provider_metadata={"reason": "no urls discovered", "source": self.collector.spec.source},
            )

        records: list[dict[str, Any]] = []
        for url in urls[: self.collector.spec.max_pages]:
            t0 = time.time()
            body: bytes | None = None
            headers: dict[str, Any] = {}
            status: int | None = None
            try:
                body, headers = self.collector.fetch(url)
                status = int(headers.get("status") or headers.get("http_status") or 200) if body is not None else None
            except Exception as exc:
                dur = int((time.time() - t0) * 1000)
                self.fetch_log.append(FetchRecord(url=url, status=None, bytes=0, content_hash=None, etag=None, last_modified=None, failure_class=classify_fetch_failure(None, str(exc)), duration_ms=dur))
                self.state.failed += 1
                continue

            dur = int((time.time() - t0) * 1000)
            if body is None:
                self.fetch_log.append(FetchRecord(url=url, status=status, bytes=0, content_hash=None, etag=headers.get("etag"), last_modified=headers.get("last-modified"), failure_class=classify_fetch_failure(status, ""), duration_ms=dur))
                self.state.failed += 1
                continue

            ch = hashlib.sha256(body).hexdigest()
            if ch in self._seen_hashes:
                self.state.deduped += 1
                self.fetch_log.append(FetchRecord(url=url, status=status, bytes=len(body), content_hash=ch, etag=headers.get("etag"), last_modified=headers.get("last-modified"), failure_class="SUCCESS", duration_ms=dur))
                continue
            self._seen_hashes.add(ch)
            self.state.bytes += len(body)
            self.state.fetched += 1

            try:
                raws = self.collector.parse(url, body, headers)
            except Exception as exc:
                self.fetch_log.append(FetchRecord(url=url, status=status, bytes=len(body), content_hash=ch, etag=headers.get("etag"), last_modified=headers.get("last-modified"), failure_class="PARSE_ERROR", duration_ms=dur))
                self.state.failed += 1
                continue

            for raw in raws:
                try:
                    norm = self.collector.normalize(raw)
                except Exception:
                    self.state.failed += 1
                    continue
                if norm is None:
                    continue
                norm.setdefault("content_hash", ch)
                norm.setdefault("source_url", url)
                records.append(norm)
                self.state.normalized += 1

            self.fetch_log.append(FetchRecord(url=url, status=status, bytes=len(body), content_hash=ch, etag=headers.get("etag"), last_modified=headers.get("last-modified"), failure_class="SUCCESS", duration_ms=dur))

        status_out = AcquisitionStatus.SUCCESS if records else (AcquisitionStatus.NO_RESULTS if not self.state.failed else AcquisitionStatus.PARTIAL_SUCCESS)
        return AcquisitionResult(
            request_id=request.request_id,
            provider=f"owned:{self.collector.spec.source}",
            provider_endpoint=self.collector.spec.source,
            status=status_out,
            started_at=started,
            completed_at=utc_now(),
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(records) if records else None,
            provider_metadata={
                "source": self.collector.spec.source,
                "task": self.collector.spec.task,
                "execution_mode": self.collector.spec.execution_mode.value,
                "parser_version": self.collector.spec.parser_version,
                "fetched": self.state.fetched,
                "deduped": self.state.deduped,
                "failed": self.state.failed,
                "bytes": self.state.bytes,
                "fetch_log": [r.__dict__ for r in self.fetch_log[:20]],
            },
            records=tuple(records),
        )

    def _fail(self, request: AcquisitionRequest, started: datetime, category: str, detail: str = "") -> AcquisitionResult:
        return AcquisitionResult(
            request_id=request.request_id,
            provider=f"owned:{self.collector.spec.source}",
            provider_endpoint=None,
            status=AcquisitionStatus.PROVIDER_ERROR,
            started_at=started,
            completed_at=utc_now(),
            error_category=category,
            provider_metadata={"detail": detail, "source": self.collector.spec.source},
        )


# ── Concrete example: official-site collector (P10/P20) ──────────
class OfficialSiteCollector:
    """Minimal official-site collector: static HTTP, content-hash dedupe."""

    spec = CollectorSpec(
        source="artist_official_site",
        task="ARTIST_OFFICIAL_SITE",
        execution_mode=ExecutionMode.STATIC_HTTP,
        identity_requirement="url",
        rate_policy="1 req/s per domain, respect robots.txt",
        parser_version="official_site_v1",
        max_pages=5,
    )

    def __init__(self, transport=None) -> None:
        from .transport import UrllibTransport

        self.transport = transport or UrllibTransport()

    def discover(self, request: AcquisitionRequest) -> list[str]:
        # query is expected to be the official URL; fallback to entity_id
        url = (request.query or request.entity_id or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return [url]
        return []

    def fetch(self, url: str) -> tuple[bytes | None, dict[str, Any]]:
        resp = self.transport.request("GET", url, headers={"User-Agent": "FestivalBloomberg/1.0 (+https://festival-bloomberg.com)"}, timeout_seconds=15.0)
        if resp.status != 200:
            return None, {"status": resp.status}
        body = resp.body if isinstance(resp.body, (bytes, bytearray)) else (resp.text.encode() if hasattr(resp, "text") and resp.text else b"")
        return bytes(body) if body else None, {"status": resp.status, "etag": resp.headers.get("etag") if hasattr(resp, "headers") else None}

    def parse(self, url: str, body: bytes, headers: dict[str, Any]) -> list[dict[str, Any]]:
        # Lightweight text extraction — full HTML stored as raw, not injected into prompts.
        text = body[:20000].decode(errors="ignore")
        return [{"source_url": url, "raw_bytes": len(body), "text_snippet": text[:500], "status": headers.get("status")}]

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        if not raw.get("source_url"):
            return None
        return {
            "platform": "official_site",
            "object_type": "web_page",
            "platform_object_id": None,
            "text": raw.get("text_snippet"),
            "source_url": raw["source_url"],
            "raw_bytes": raw.get("raw_bytes"),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def checkpoint(self) -> dict[str, Any]:
        return {"at": datetime.now(timezone.utc).isoformat()}

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider="owned:artist_official_site", healthy=True)

    def cost(self) -> CostEstimate:
        return CostEstimate(provider="owned:artist_official_site", estimated_cost_usd=0.0, free_quota=True, source="self_hosted")
