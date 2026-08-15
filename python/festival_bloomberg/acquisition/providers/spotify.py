"""Spotify Web API provider (client-credentials flow, 2026 Dev Mode).

Uses the classic ``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET`` token
exchange. The 2026 Dev Mode API removed ``popularity``, ``followers``,
``genres`` and the artist top-tracks endpoint, so this provider ONLY persists
fields actually present in the live response (id, name, external_urls, images,
type, uri) and records ``fields_present`` so downstream code can never build
around a removed field.

Semantics:
- ``request.query`` = artist name; search returns identity/catalog records.
- The access token is cached and refreshed before expiry (shared dev-account
  quota; never more than one exchange per expiry window).
- 401 -> refresh once and retry; 429 -> RATE_LIMITED; empty items -> NO_RESULTS.
- A token exchange that fails with present-but-invalid credentials is
  ``PROVIDER_ERROR`` (auth_invalid), never a fabricated success.
"""

from __future__ import annotations

import base64
import json
import time
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

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
PROVIDER_VERSION = "spotify-devmode-v1"

#: Fields the 2026 API reliably returns for an artist search item.
#: popularity / followers / genres are deliberately NOT included (removed).
CATALOG_FIELDS = ("id", "name", "external_urls", "images", "type", "uri")


class SpotifyProvider(BaseProvider):
    name = "spotify"

    def __init__(self, transport: Any = None, env: dict[str, str] | None = None) -> None:
        super().__init__(transport=transport, env=env)
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

    # -- config ----------------------------------------------------------------
    def _has_credentials(self) -> bool:
        return bool(self.secret("SPOTIFY_CLIENT_ID") and self.secret("SPOTIFY_CLIENT_SECRET"))

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=self._has_credentials())

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(
            provider=self.name, estimated_cost_usd=0.0, free_quota=False,
            source="spotify_dev_mode_shared_quota",
        )

    # -- token management ------------------------------------------------------
    def _get_token(self) -> str | None:
        """Return a valid access token, refreshing only when expired."""
        now = time.time()
        if self._access_token and now < self._token_expiry:
            return self._access_token
        cid = self.secret("SPOTIFY_CLIENT_ID")
        csec = self.secret("SPOTIFY_CLIENT_SECRET")
        if not cid or not csec:
            return None
        basic = base64.b64encode(f"{cid}:{csec}".encode("utf-8")).decode("ascii")
        try:
            response = self.transport.request(
                "POST",
                TOKEN_URL,
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body=urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
                timeout_seconds=30.0,
            )
        except TransportError:
            return None
        if response.status != 200:
            return None
        try:
            payload = json.loads(response.body.decode("utf-8"))
            token = payload["access_token"]
            expires_in = int(payload.get("expires_in", 3600))
        except (ValueError, KeyError, UnicodeDecodeError, TypeError):
            return None
        self._access_token = token
        self._token_expiry = now + max(60, expires_in - 60)
        return token

    # -- acquisition -----------------------------------------------------------
    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        started = utc_now()
        name = (request.query or "").strip()
        if not name:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=API_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="request_invalid",
                provider_metadata={"detail": "query must be an artist name"},
            )
        if not self._has_credentials():
            return self._not_configured(request, "SPOTIFY_CLIENT_ID/SECRET missing")

        token = self._get_token()
        if token is None:
            return self._result(
                request,
                status=AcquisitionStatus.PROVIDER_ERROR,
                provider_endpoint=TOKEN_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="auth_invalid",
                provider_metadata={"detail": "token exchange failed with configured credentials"},
            )

        limit = min(int(getattr(request, "max_records", 10) or 10), 50)
        url = f"{API_URL}/search?{urlencode({'q': name, 'type': 'artist', 'limit': limit, 'market': 'US'})}"
        for attempt in range(2):  # one refresh-and-retry on 401
            try:
                response = self.transport.request(
                    "GET", url,
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/json"},
                    timeout_seconds=30.0,
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
                    provider_metadata={"http_status": 429},
                )
            if response.status == 401 and attempt == 0:
                self._access_token = None
                self._token_expiry = 0.0
                token = self._get_token()
                if token is not None:
                    continue
            if response.status != 200:
                return self._result(
                    request,
                    status=AcquisitionStatus.PROVIDER_ERROR,
                    provider_endpoint=url,
                    started_at=started,
                    cost_usd=0.0,
                    error_category="http_error",
                    provider_metadata={"http_status": response.status},
                )
            try:
                payload = json.loads(response.body.decode("utf-8"))
                items = payload["artists"]["items"]
                total = payload["artists"]["total"]
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                return self._result(
                    request,
                    status=AcquisitionStatus.SCHEMA_INVALID,
                    provider_endpoint=url,
                    started_at=started,
                    cost_usd=0.0,
                    error_category="schema_invalid",
                )
            records = tuple(
                _normalize_artist(item, started.isoformat()) for item in items
            )
            status = AcquisitionStatus.SUCCESS if records else AcquisitionStatus.NO_RESULTS
            return self._result(
                request,
                status=status,
                provider_endpoint=url,
                started_at=started,
                record_count=len(records),
                cost_usd=0.0,
                raw_payload_hash=content_hash_of([r.get("platform_object_id") for r in records]),
                provider_metadata={
                    "provider_version": PROVIDER_VERSION,
                    "search_total": total,
                    "market": "US",
                },
                records=records,
            )
        # unreachable unless the retry loop fell through
        return self._result(
            request,
            status=AcquisitionStatus.PROVIDER_ERROR,
            provider_endpoint=url,
            started_at=started,
            cost_usd=0.0,
            error_category="auth_invalid",
        )


def _normalize_artist(item: dict[str, Any], retrieved_at: str) -> dict:
    """Persist ONLY fields the 2026 response actually contains."""
    present = {k for k in CATALOG_FIELDS if k in item}
    record: dict[str, Any] = {
        "platform": "spotify",
        "provider": PROVIDER_VERSION,
        "object_type": "artist_identity",
        "platform_object_id": item.get("id"),
        "spotify_id": item.get("id"),
        "name": item.get("name"),
        "fields_present": sorted(present),
        "retrieved_at": retrieved_at,
        "knowledge_time": retrieved_at,
        "content_role": "catalog_identity",
        "content_hash": content_hash_of(item),
    }
    for field in ("external_urls", "images", "type", "uri"):
        if field in present:
            record[field] = item[field]
    return record
