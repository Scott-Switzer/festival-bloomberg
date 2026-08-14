"""YouTube Data API provider — the first high-integrity official social source.

Collects videos and top-level comment threads through the official API:

* ``GET /videos``            - snippets + statistics for a set of video IDs
* ``GET /commentThreads``    - top-level comments per video

Engagement counts are observed *at retrieval time* and are stored as
timestamped snapshots; they are never treated as historical values. Quota
units are recorded in ``provider_metadata`` and estimated USD cost is left
``None`` (free tier) rather than fabricated.
"""

from __future__ import annotations

from datetime import datetime

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

DEFAULT_BASE_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeProvider(BaseProvider):
    name = "youtube"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        base_url: str | None = None,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (base_url or self.env.get("YOUTUBE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

    def health(self) -> ProviderHealth:
        if self.secret("YOUTUBE_API_KEY") is None:
            return ProviderHealth(provider=self.name, healthy=False, last_error="no YOUTUBE_API_KEY")
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        # Free tier: quota units are finite but cost $0.00 per the doctrine.
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="free_tier")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        key = self.secret("YOUTUBE_API_KEY")
        if key is None:
            return self._not_configured(request, "YOUTUBE_API_KEY not set")

        started = utc_now()
        quota_units = 0
        records: list[dict] = []

        # 1. search for videos matching the query
        try:
            search = self.transport.request(
                "GET",
                f"{self.base_url}/search",
                params={
                    "part": "snippet",
                    "q": request.query,
                    "type": "video",
                    "maxResults": min(request.max_records, 50),
                    "key": key,
                },
                timeout_seconds=30.0,
            )
        except TransportError as exc:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc))
        quota_units += 100  # search costs 100 units

        if search.status == 403:
            return self._fail(request, started, AcquisitionStatus.RATE_LIMITED, "quota_exceeded", "http 403")
        if search.status == 401:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "authentication", "http 401")
        if search.status == 429:
            return self._fail(request, started, AcquisitionStatus.RATE_LIMITED, "rate_limited", "http 429")
        if search.status != 200:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "search", f"http {search.status}")

        try:
            search_payload = search.json()
        except ValueError:
            return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "search_response")

        video_ids = [item.get("id", {}).get("videoId") for item in search_payload.get("items", [])]
        video_ids = [vid for vid in video_ids if vid]
        if not video_ids:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=f"{self.base_url}/search",
                started_at=started,
                provider_metadata={"quota_units": quota_units},
            )

        # 2. fetch video snippets + statistics
        try:
            videos = self.transport.request(
                "GET",
                f"{self.base_url}/videos",
                params={
                    "part": "snippet,statistics",
                    "id": ",".join(video_ids[:50]),
                    "key": key,
                },
                timeout_seconds=30.0,
            )
        except TransportError as exc:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc))
        quota_units += 1  # videos costs 1 unit

        if videos.status == 429:
            return self._fail(request, started, AcquisitionStatus.RATE_LIMITED, "rate_limited", "http 429")
        if videos.status != 200:
            return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "videos", f"http {videos.status}")

        try:
            videos_payload = videos.json()
        except ValueError:
            return self._fail(request, started, AcquisitionStatus.SCHEMA_INVALID, "videos_response")

        for item in videos_payload.get("items", []):
            records.append(self._normalize_video(item))

        # 3. top-level comment threads (quota-limited)
        comment_limit = max(1, min(request.max_records // max(len(video_ids), 1), 50))
        if comment_limit > 0:
            for video_id in video_ids[:5]:
                try:
                    threads = self.transport.request(
                        "GET",
                        f"{self.base_url}/commentThreads",
                        params={
                            "part": "snippet",
                            "videoId": video_id,
                            "maxResults": comment_limit,
                            "key": key,
                        },
                        timeout_seconds=30.0,
                    )
                except TransportError as exc:
                    return self._fail(request, started, AcquisitionStatus.PROVIDER_ERROR, "network", str(exc))
                quota_units += 1  # commentThreads costs 1 unit
                if threads.status == 200:
                    try:
                        for item in threads.json().get("items", []):
                            records.append(self._normalize_comment_thread(item))
                    except ValueError:
                        continue
                elif threads.status == 429:
                    return self._fail(request, started, AcquisitionStatus.RATE_LIMITED, "rate_limited", "http 429")

        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS,
            provider_endpoint=f"{self.base_url}/videos",
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(records),
            provider_metadata={
                "quota_units": quota_units,
                "videos": len(video_ids),
            },
            records=tuple(records),
        )

    def _fail(self, request, started, status, category, detail=None) -> AcquisitionResult:
        return self._result(
            request,
            status=status,
            provider_endpoint=self.base_url,
            started_at=started,
            error_category=category,
            provider_metadata={"detail": detail} if detail else {},
        )

    # -- normalization ------------------------------------------------------ #
    @staticmethod
    def _normalize_video(item: dict) -> dict:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        published = snippet.get("publishedAt")
        return {
            "platform": "youtube",
            "object_type": "video",
            "platform_object_id": item.get("id"),
            "parent_object_id": None,
            "author_public_id": snippet.get("channelId"),
            "author_name": snippet.get("channelTitle"),
            "text": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "language": snippet.get("defaultAudioLanguage"),
            "published_at": _parse_iso(published),
            "media_type": "video",
            "canonical_url": f"https://www.youtube.com/watch?v={item.get('id')}",
            # A video title/description is the channel's own content, never a
            # fan comment; it must not enter fan sentiment aggregation.
            "content_role": None,
            "content_role_method": None,
            "resolution_method": "EXACT_PLATFORM_ID",
            "resolution_evidence": "YouTube video id",
            "engagement": {
                "views": _int(stats.get("viewCount")),
                "likes": _int(stats.get("likeCount")),
                "comments": _int(stats.get("commentCount")),
            },
        }

    @staticmethod
    def _normalize_comment_thread(item: dict) -> dict:
        snippet = item.get("snippet", {})
        top = snippet.get("topLevelComment", {}).get("snippet", {})
        published = top.get("publishedAt")
        return {
            "platform": "youtube",
            "object_type": "comment",
            "platform_object_id": item.get("id"),
            "parent_object_id": snippet.get("videoId"),
            "author_public_id": top.get("authorChannelId", {}).get("value"),
            "author_name": top.get("authorDisplayName"),
            "text": top.get("textOriginal", ""),
            "language": top.get("textDisplay") and None,
            "published_at": _parse_iso(published),
            "media_type": "text",
            "canonical_url": f"https://www.youtube.com/watch?v={snippet.get('videoId')}&lc={item.get('id')}",
            # Top-level viewer comments are fan-generated discourse.
            "content_role": "FAN_GENERATED",
            "content_role_method": "source_type",
            "resolution_method": "EXACT_PLATFORM_ID",
            "resolution_evidence": "YouTube comment thread id",
            "engagement": {
                "likes": _int(top.get("likeCount")),
            },
        }


def _parse_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
