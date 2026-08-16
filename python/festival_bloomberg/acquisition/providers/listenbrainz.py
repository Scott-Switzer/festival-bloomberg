"""ListenBrainz artist-statistics provider (key-free, CC0).

Endpoints (see ListenBrainz statistics API docs):

    GET https://api.listenbrainz.org/1/stats/artist/{artist_mbid}/listeners

returns the *sitewide* aggregate for a MusicBrainz artist: the total listen
count and the top-N individual listeners in a given time range. ``artist_mbid``
is REQUIRED — an artist without a MusicBrainz ID has no ListenBrainz statistics
row and is reported as ``NO_RESULTS``, never a fabricated zero.

Semantics preserved:

- These metrics are an ATTENTION/CONSUMPTION SAMPLE (CC0), never LOCAL_DEMAND
  and never a ticket-demand proxy.
- ``LISTENBRAINZ_LISTEN_COUNT`` = the provider's ``total_listen_count``.
- ``LISTENBRAINZ_LISTENER_COUNT`` = the size of the top-N ``listeners`` list
  (a *sample*, not a census; provenance records that explicitly).
- 204/404 (no statistics computed / unknown entity) is ``NO_RESULTS`` with a
  ``missing`` marker, so UNKNOWN is never encoded as zero.
- 429 is ``RATE_LIMITED`` and is never retried aggressively.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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

ARTIST_LISTENERS = "ARTIST_LISTENERS"
BASE_URL = "https://api.listenbrainz.org/1/stats/artist"
DEFAULT_RANGE = "all_time"
PROVIDER_VERSION = "listenbrainz_stats_v1"
CONTENT_ROLE = "ATTENTION_CONSUMPTION_SAMPLE"


class ListenBrainzProvider(BaseProvider):
    name = "listenbrainz"

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True)

    def configured(self) -> bool:
        # Key-free public provider: always configured.
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
        mbid = (request.external_id or "").strip()
        if not mbid:
            return self._result(
                request,
                status=AcquisitionStatus.SCHEMA_INVALID,
                provider_endpoint=BASE_URL,
                started_at=started,
                cost_usd=0.0,
                error_category="mbid_required",
                provider_metadata={
                    "reason": "ListenBrainz artist stats are keyed by MusicBrainz ID; "
                              "NULL stays NULL for artists without an MBID",
                },
            )

        # A range override may arrive via the request query (e.g. "week",
        # "month", "year", "all_time"). Default stays all_time.
        stats_range = (request.query or "").strip() or DEFAULT_RANGE
        if stats_range not in ("all_time", "year", "month", "week", "today"):
            stats_range = DEFAULT_RANGE
        url = f"{BASE_URL}/{mbid}/listeners?range={stats_range}"
        try:
            response = self.transport.request(
                "GET",
                url,
                headers={"Accept": "application/json", "User-Agent": "festival-bloomberg-research/1.0"},
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
                provider_metadata={"detail": "http 429", "retry_after_hint": "respect Retry-After / back off"},
            )
        # 204 = stats not calculated; 404 = entity unknown. Both mean "no data",
        # never a fabricated zero.
        if response.status in (204, 404):
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=url,
                started_at=started,
                cost_usd=0.0,
                error_category="missing",
                provider_metadata={"http_status": response.status, "missing": True},
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

        data = (payload or {}).get("payload") or {}
        # total_listen_count may legitimately be 0/None for artists with no
        # listens; still return the record with NULL value (never fabricate).
        listeners = data.get("listeners") or []
        record = {
            "platform": "listenbrainz",
            "provider": "listenbrainz",
            "object_type": "artist_listen_stat",
            "platform_object_id": mbid,
            "artist_mbid": mbid,
            "artist_name": data.get("artist_name"),
            "total_listen_count": data.get("total_listen_count"),
            "listener_count_sample": len(listeners),
            "stats_range": data.get("range") or stats_range,
            "from_ts": data.get("from_ts"),
            "to_ts": data.get("to_ts"),
            "last_updated": data.get("last_updated"),
            "source_url": url,
            "retrieved_at": utc_now().isoformat(),
            "knowledge_time": utc_now().isoformat(),
            "content_role": CONTENT_ROLE,
            "content_role_method": "provider_object_type",
            "provider_version": PROVIDER_VERSION,
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
                "provider_version": PROVIDER_VERSION,
                "artist_mbid": mbid,
                "stats_range": record["stats_range"],
                "missing": False,
            },
            records=(record,),
        )


def ts_to_date(ts: Any) -> str | None:
    """Unix epoch seconds -> ``YYYY-MM-DD`` (UTC), or None when absent."""
    if ts is None:
        return None
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
