"""YouTube Data API provider — official social source for fan comments.

Collects videos and top-level comment threads through the official API.
Mutable statistics and comment bodies are snapshots at retrieval time:
``knowledge_time`` is always retrieval time unless an immutable historical
snapshot/version is independently proven.

Quota is tracked as method-call counts (see ``youtube_quota``). Estimated
USD cost is ``0.0`` (free tier), never fabricated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...markets.registry import assign_source_object_market
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
from ..youtube_errors import (
    AUTH_NOT_CONFIGURED,
    AUTH_VALID,
    CAT_COMMENTS_DISABLED,
    CAT_QUOTA_EXCEEDED,
    auth_from_http,
    classify_youtube_error,
)
from ..youtube_quota import YouTubeQuotaBudget, YouTubeQuotaBudgetExceeded

DEFAULT_BASE_URL = "https://www.googleapis.com/youtube/v3"
PROVIDER_VERSION = "youtube_official_api-v2"
PARSER_VERSION = "youtube_v3_comment_thread-v2"

#: Stable public video used only for credential validation (Me at the zoo).
VALIDATION_VIDEO_ID = "jNQXAC9IVRw"

_METHOD_PATHS = {
    "search": "search.list",
    "videos": "videos.list",
    "commentThreads": "commentThreads.list",
    "comments": "comments.list",
    "channels": "channels.list",
}


class YouTubeProvider(BaseProvider):
    name = "youtube"

    def __init__(
        self,
        transport=None,
        env=None,
        *,
        base_url: str | None = None,
        quota_budget: YouTubeQuotaBudget | None = None,
    ) -> None:
        super().__init__(transport=transport, env=env)
        self.base_url = (base_url or self.env.get("YOUTUBE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.quota = quota_budget or YouTubeQuotaBudget()

    def health(self) -> ProviderHealth:
        if self.secret("YOUTUBE_API_KEY") is None:
            return ProviderHealth(provider=self.name, healthy=False, last_error="no YOUTUBE_API_KEY")
        return ProviderHealth(provider=self.name, healthy=True)

    def estimate(self, request: AcquisitionRequest) -> CostEstimate:
        return CostEstimate(provider=self.name, estimated_cost_usd=0.0, free_quota=True, source="free_tier")

    def configured(self) -> bool:
        return self.secret("YOUTUBE_API_KEY") is not None

    def validate_auth(self) -> dict[str, str]:
        """Live, low-cost credential check. Never returns the key.

        Presence is reported separately from AUTH_VALID. A key that is
        CONFIGURED but fails videos.list is not AUTH_VALID.
        """
        if not self.configured():
            return {
                "configured": "NOT_CONFIGURED",
                "auth": AUTH_NOT_CONFIGURED,
                "error_category": "credentials_missing",
            }
        started = utc_now()
        response, error = self._api_get(
            "videos",
            {
                "part": "id",
                "id": VALIDATION_VIDEO_ID,
                "maxResults": "1",
            },
        )
        del started
        if error is not None:
            return {
                "configured": "CONFIGURED",
                "auth": error["auth"],
                "error_category": error["category"],
            }
        assert response is not None
        auth = auth_from_http(response.status, _safe_json(response))
        return {
            "configured": "CONFIGURED",
            "auth": auth,
            "error_category": "ok" if auth == AUTH_VALID else "validation_failed",
        }

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        key = self.secret("YOUTUBE_API_KEY")
        if key is None:
            return self._not_configured(request, "YOUTUBE_API_KEY not set")

        started = utc_now()
        retrieved_at = started.isoformat()
        max_videos = request.max_videos if request.max_videos is not None else min(request.max_records, 50)
        max_comments = request.max_records
        order = request.order or "date"
        try:
            video_ids, search_meta, search_error = self._search_video_ids(
                request.query,
                published_after=request.start_time,
                max_videos=max_videos,
                order=order,
            )
        except YouTubeQuotaBudgetExceeded:
            return self._quota_stop(request, started, "search.list")
        if search_error is not None:
            return self._classified_fail(request, started, search_error)

        if not video_ids:
            return self._result(
                request,
                status=AcquisitionStatus.NO_RESULTS,
                provider_endpoint=f"{self.base_url}/search",
                started_at=started,
                cost_usd=0.0,
                provider_metadata={
                    "quota_usage": self.quota.as_dict(),
                    "search": search_meta,
                    "provider_version": PROVIDER_VERSION,
                },
            )

        try:
            videos, video_error, missing = self._list_videos(video_ids)
        except YouTubeQuotaBudgetExceeded:
            return self._quota_stop(request, started, "videos.list")
        if video_error is not None:
            return self._classified_fail(request, started, video_error)

        records: list[dict] = []
        comments_disabled: list[str] = []
        comment_count = 0
        comment_pages = 0
        comment_page_cap_hit = False
        comment_count_cap_hit = False
        comments_reported = 0
        for video in videos:
            video_record = self._normalize_video(
                video,
                retrieved_at=retrieved_at,
                search_query=request.query,
                search_cohort=request.search_cohort,
                selection_reason=search_meta.get("selection_rule"),
            )
            records.append(video_record)
            reported = (video_record.get("engagement") or {}).get("comments")
            if isinstance(reported, int):
                comments_reported += reported
            remaining = max_comments - comment_count
            if remaining <= 0:
                comment_count_cap_hit = True
                continue
            try:
                comments, disabled, comment_error, comment_cov = self._list_comment_threads(
                    video_record["platform_object_id"],
                    max_comments=remaining,
                    retrieved_at=retrieved_at,
                    search_cohort=request.search_cohort,
                    search_query=request.query,
                )
            except YouTubeQuotaBudgetExceeded:
                # Keep videos already collected; stop comments cleanly.
                self.quota.stopped_reason = "QUOTA_STOP"
                break
            if comment_error is not None and comment_error["category"] not in {CAT_COMMENTS_DISABLED}:
                return self._classified_fail(request, started, comment_error, records=records)
            if disabled:
                comments_disabled.append(video_record["platform_object_id"])
                continue
            comment_pages += comment_cov.get("pages", 0)
            comment_page_cap_hit = comment_page_cap_hit or comment_cov.get("page_cap_hit", False)
            comment_count_cap_hit = comment_count_cap_hit or comment_cov.get("count_cap_hit", False)
            for comment in comments:
                # Source-object market context only — never commenter location.
                comment["market_id"] = video_record.get("market_id")
                comment["market_context_method"] = video_record.get("market_context_method")
                comment["commenter_location"] = None
            records.extend(comments)
            comment_count += len(comments)

        from ...social.sampling import annotate_coverage

        enabled = len(videos) - len(comments_disabled)
        coverage = annotate_coverage(
            videos_discovered=len(video_ids),
            videos_selected=len(videos),
            videos_with_comments_enabled=max(0, enabled),
            comments_reported=comments_reported or None,
            comments_requested=max_comments,
            comments_retrieved=comment_count,
            comment_pages_fetched=comment_pages,
            comment_page_cap_hit=comment_page_cap_hit,
            comment_count_cap_hit=comment_count_cap_hit,
            comments_disabled=bool(videos) and enabled == 0,
        )

        return self._result(
            request,
            status=AcquisitionStatus.SUCCESS,
            provider_endpoint=f"{self.base_url}/videos",
            started_at=started,
            record_count=len(records),
            cost_usd=0.0,
            raw_payload_hash=content_hash_of(
                [r.get("platform_object_id") for r in records]
            ),
            provider_metadata={
                "quota_usage": self.quota.as_dict(),
                "quota_units": self.quota.total_read_calls,
                "videos_discovered": len(video_ids),
                "videos_selected": len(videos),
                "videos_missing": missing,
                "videos_comments_disabled": comments_disabled,
                "search": search_meta,
                "provider_version": PROVIDER_VERSION,
                "parser_version": PARSER_VERSION,
                "sampling": coverage,
            },
            records=tuple(records),
        )

    # -- HTTP --------------------------------------------------------------- #
    def _api_get(self, resource: str, params: dict[str, Any]) -> tuple[Any, dict | None]:
        method = _METHOD_PATHS[resource]
        if self.quota.would_exceed(method):
            raise YouTubeQuotaBudgetExceeded(method, self.quota.as_dict())
        key = self.secret("YOUTUBE_API_KEY")
        request_params = dict(params)
        request_params["key"] = key
        try:
            response = self.transport.request(
                "GET",
                f"{self.base_url}/{resource}",
                params=request_params,
                timeout_seconds=30.0,
            )
        except TransportError as exc:
            return None, {
                "auth": "UNKNOWN",
                "category": "network",
                "status": AcquisitionStatus.PROVIDER_ERROR,
                "detail": str(exc),
            }
        self.quota.consume(method)
        if response.status == 200:
            try:
                response.json()
            except ValueError:
                return None, {
                    "auth": "UNKNOWN",
                    "category": "schema_invalid",
                    "status": AcquisitionStatus.SCHEMA_INVALID,
                    "detail": f"{resource}_response",
                }
            return response, None
        payload = _safe_json(response)
        auth, category = classify_youtube_error(response.status, payload)
        if category == CAT_QUOTA_EXCEEDED or response.status == 429:
            status = AcquisitionStatus.RATE_LIMITED
        elif category == CAT_COMMENTS_DISABLED:
            status = AcquisitionStatus.PARTIAL_SUCCESS
        else:
            status = AcquisitionStatus.PROVIDER_ERROR
        return None, {
            "auth": auth,
            "category": category,
            "status": status,
            "detail": f"http {response.status}",
            "resource": resource,
        }

    def _search_video_ids(
        self,
        query: str,
        *,
        published_after: datetime | None,
        max_videos: int,
        order: str,
    ) -> tuple[list[str], dict, dict | None]:
        ids: list[str] = []
        page_token = None
        pages = 0
        while len(ids) < max_videos:
            params: dict[str, Any] = {
                "part": "snippet",
                "q": query,
                "type": "video",
                "order": order,
                "maxResults": str(min(50, max_videos - len(ids))),
            }
            if published_after is not None:
                params["publishedAfter"] = _rfc3339(published_after)
            if page_token:
                params["pageToken"] = page_token
            response, error = self._api_get("search", params)
            if error is not None:
                return ids, _search_meta(query, published_after, order, ids, pages), error
            payload = response.json()
            pages += 1
            for item in payload.get("items") or []:
                video_id = (item.get("id") or {}).get("videoId")
                if video_id and video_id not in ids:
                    ids.append(video_id)
                if len(ids) >= max_videos:
                    break
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
            if pages >= 5:
                break
        meta = _search_meta(query, published_after, order, ids, pages)
        return ids[:max_videos], meta, None

    def _list_videos(self, video_ids: list[str]) -> tuple[list[dict], dict | None, list[str]]:
        found: list[dict] = []
        missing: list[str] = []
        for chunk_start in range(0, len(video_ids), 50):
            chunk = video_ids[chunk_start : chunk_start + 50]
            response, error = self._api_get(
                "videos",
                {
                    "part": "snippet,statistics,status",
                    "id": ",".join(chunk),
                    "maxResults": "50",
                },
            )
            if error is not None:
                return found, error, missing
            payload = response.json()
            returned = {item.get("id"): item for item in payload.get("items") or [] if item.get("id")}
            for video_id in chunk:
                if video_id in returned:
                    found.append(returned[video_id])
                else:
                    missing.append(video_id)
        return found, None, missing

    def _list_comment_threads(
        self,
        video_id: str,
        *,
        max_comments: int,
        retrieved_at: str,
        search_cohort: str | None,
        search_query: str | None,
    ) -> tuple[list[dict], bool, dict | None, dict]:
        records: list[dict] = []
        page_token = None
        pages = 0
        while len(records) < max_comments:
            params: dict[str, Any] = {
                "part": "snippet,replies",
                "videoId": video_id,
                "maxResults": str(min(100, max_comments - len(records))),
                "textFormat": "plainText",
                "order": "time",
            }
            if page_token:
                params["pageToken"] = page_token
            response, error = self._api_get("commentThreads", params)
            if error is not None:
                if error["category"] == CAT_COMMENTS_DISABLED:
                    return [], True, None, {"pages": 0, "page_cap_hit": False, "count_cap_hit": False}
                return records, False, error, {"pages": pages, "page_cap_hit": False, "count_cap_hit": False}
            payload = response.json()
            pages += 1
            for item in payload.get("items") or []:
                records.append(
                    self._normalize_comment_thread(
                        item,
                        retrieved_at=retrieved_at,
                        search_cohort=search_cohort,
                        search_query=search_query,
                    )
                )
                if len(records) >= max_comments:
                    break
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
            if pages >= 10:
                return records, False, None, {
                    "pages": pages,
                    "page_cap_hit": True,
                    "count_cap_hit": len(records) >= max_comments,
                }
        return records, False, None, {
            "pages": pages,
            "page_cap_hit": False,
            "count_cap_hit": bool(page_token) and len(records) >= max_comments,
        }

    # -- normalization ------------------------------------------------------ #
    def _normalize_video(
        self,
        item: dict,
        *,
        retrieved_at: str,
        search_query: str,
        search_cohort: str | None,
        selection_reason: str | None,
    ) -> dict:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        published = snippet.get("publishedAt")
        title = snippet.get("title") or ""
        description = snippet.get("description") or ""
        tags = snippet.get("tags") if isinstance(snippet.get("tags"), list) else None
        assignment = assign_source_object_market(
            title=title,
            description=description,
            tags=tags,
            search_query=search_query,
        )
        video_id = item.get("id")
        return {
            "platform": "youtube",
            "provider": "youtube_official_api",
            "object_type": "video",
            "platform_object_id": video_id,
            "parent_object_id": None,
            "author_public_id": snippet.get("channelId"),
            "author_name": snippet.get("channelTitle"),
            "channel_id": snippet.get("channelId"),
            "channel_title": snippet.get("channelTitle"),
            "text": title,
            "description": description,
            "language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
            "published_at": _parse_iso(published),
            "source_publication_time": _parse_iso(published),
            "source_updated_at": None,
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "media_type": "video",
            "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
            "tags": tags,
            "category_id": snippet.get("categoryId"),
            "search_cohort": search_cohort,
            "search_query": search_query,
            "selection_reason": selection_reason,
            "market_id": assignment.market_id,
            "market_context_method": assignment.method,
            "commenter_location": None,
            "content_role": "UNKNOWN",
            "content_role_method": "unresolved_channel_identity",
            "resolution_method": "EXACT_PLATFORM_ID",
            "resolution_evidence": "YouTube video id",
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "content_hash": content_hash_of(title + "\n" + description),
            "engagement": {
                "views": _int(stats.get("viewCount")),
                "likes": _int(stats.get("likeCount")),
                "comments": _int(stats.get("commentCount")),
                "snapshot_at": retrieved_at,
            },
        }

    @staticmethod
    def _normalize_comment_thread(
        item: dict,
        *,
        retrieved_at: str,
        search_cohort: str | None,
        search_query: str | None,
    ) -> dict:
        snippet = item.get("snippet") or {}
        top_wrapper = snippet.get("topLevelComment") or {}
        top = top_wrapper.get("snippet") or {}
        comment_id = top_wrapper.get("id") or item.get("id")
        video_id = snippet.get("videoId")
        total_replies = _int(snippet.get("totalReplyCount")) or 0
        nested = ((item.get("replies") or {}).get("comments")) or []
        replies_complete = total_replies <= len(nested)
        author_channel = top.get("authorChannelId")
        author_id = None
        if isinstance(author_channel, dict):
            author_id = author_channel.get("value")
        text = top.get("textOriginal")
        published = top.get("publishedAt")
        updated = top.get("updatedAt")
        return {
            "platform": "youtube",
            "provider": "youtube_official_api",
            "object_type": "comment",
            "platform_object_id": comment_id,
            "parent_object_id": video_id,
            "video_id": video_id,
            "author_public_id": author_id,
            "author_name": top.get("authorDisplayName"),
            "text": text,
            "language": None,
            "published_at": _parse_iso(published),
            "source_publication_time": _parse_iso(published),
            "source_updated_at": _parse_iso(updated),
            "retrieved_at": retrieved_at,
            "knowledge_time": retrieved_at,
            "knowledge_time_source": "retrieval",
            "media_type": "text",
            "canonical_url": (
                f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
                if video_id and comment_id
                else None
            ),
            "search_cohort": search_cohort,
            "search_query": search_query,
            "market_id": None,
            "market_context_method": "UNKNOWN",
            "commenter_location": None,
            "content_role": "FAN_GENERATED",
            "content_role_method": "source_type",
            "resolution_method": "EXACT_PLATFORM_ID",
            "resolution_evidence": "YouTube comment id",
            "parser_version": PARSER_VERSION,
            "provider_version": PROVIDER_VERSION,
            "replies_complete": replies_complete,
            "total_reply_count": total_replies,
            "content_hash": content_hash_of(text or ""),
            "engagement": {
                "likes": _int(top.get("likeCount")),
            },
        }

    def _classified_fail(
        self,
        request: AcquisitionRequest,
        started: datetime,
        error: dict,
        records: list[dict] | None = None,
    ) -> AcquisitionResult:
        status = error.get("status") or AcquisitionStatus.PROVIDER_ERROR
        if records:
            status = AcquisitionStatus.PARTIAL_SUCCESS
        return self._result(
            request,
            status=status,
            provider_endpoint=self.base_url,
            started_at=started,
            record_count=len(records or []),
            cost_usd=0.0,
            error_category=error.get("category"),
            provider_metadata={
                "detail": error.get("detail"),
                "auth": error.get("auth"),
                "quota_usage": self.quota.as_dict(),
            },
            records=tuple(records or ()),
        )

    def _quota_stop(self, request: AcquisitionRequest, started: datetime, method: str) -> AcquisitionResult:
        return self._result(
            request,
            status=AcquisitionStatus.BUDGET_EXCEEDED,
            provider_endpoint=self.base_url,
            started_at=started,
            cost_usd=0.0,
            error_category="quota_budget",
            provider_metadata={
                "detail": f"session cap would be exceeded by {method}",
                "quota_usage": self.quota.as_dict(),
                "stopped_reason": "QUOTA_STOP",
            },
        )


def _search_meta(
    query: str,
    published_after: datetime | None,
    order: str,
    video_ids: list[str],
    pages: int,
) -> dict:
    return {
        "query": query,
        "order": order,
        "published_after": _rfc3339(published_after) if published_after else None,
        "video_ids": list(video_ids),
        "pages": pages,
        "selection_rule": f"search.list q={query!r} type=video order={order} first {len(video_ids)} ids",
    }


def _safe_json(response) -> Any:
    try:
        return response.json()
    except (ValueError, AttributeError):
        return {}


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat().replace("+00:00", "Z")


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
