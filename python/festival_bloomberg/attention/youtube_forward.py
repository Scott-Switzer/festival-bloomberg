"""YouTube FORWARD tape — daily channel snapshots (attention time series).

Milestone OPEN_ARTIST_MARKET_DATA_V1 — SOURCE 3.

The point of this rail is to START a daily tape NOW: today's channel state
(subscribers, cumulative views, video count) cannot necessarily be recovered
later. It never reconstructs historical values from current state.

What is collected per artist (when a channel is resolvable):

* ``YT_SUBSCRIBERS``     — subscriberCount (channels.list statistics)
* ``YT_CHANNEL_VIEWS``   — cumulative viewCount
* ``YT_VIDEO_COUNT``     — videoCount
* ``YT_RECENT_VIDEO_VIEWS`` / ``YT_RECENT_VIDEO_LIKES`` / ``YT_RECENT_VIDEO_COMMENTS``
  — engagement of the most recent published video (videos.list statistics)

Each snapshot is persisted as ONE ``metrics.artist_attention_observations``
row with ``period_start = period_end = retrieval day`` (a mutable-statistics
snapshot — the observation IS the retrieval moment). ``value`` is never
fabricated: a missing channel is ``status='missing'``; a rate limit is
``RATE_LIMITED`` and stops the batch.

Quota: channels.list=1 unit, videos.list=1 unit per artist per day.
YOUTUBE_API_KEY is required; without it the collector reports
NOT_CONFIGURED and fails closed (never a fabricated zero).

Deltas (YT_SUBSCRIBER_DELTA etc.) are computed ONLY by the security master
from two real snapshots — never here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from ..acquisition.contracts import utc_now
from ..identity.spotify import normalize_name

SOURCE_SYSTEM = "youtube"
METRIC_VERSION = "youtube_channel_snapshot_v1"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

#: Channels.list statistics cost 1 quota unit; videos.list also 1. Kept
#: explicit so the collector can enforce a bounded per-run quota.
QUOTA_UNITS_CHANNELS = 1
QUOTA_UNITS_VIDEOS = 1


def artist_key_for(name: str) -> str:
    return f"name::{normalize_name(name)}"


def snapshot_key(*, artist_key: str, day: str, channel_id: str) -> str:
    material = "|".join([artist_key, SOURCE_SYSTEM, "channel_snapshot", channel_id, day, METRIC_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def resolve_channel_id(value: str | None) -> str | None:
    """Extract a YouTube channel ID from a stored identifier.

    Accepts ``UC...`` channel IDs, ``channel/<id>`` URLs, or legacy
    ``user/<handle>`` handles. Returns None when not resolvable.
    """
    if not value:
        return None
    v = (value or "").strip()
    if v.startswith("https://www.youtube.com/channel/"):
        return v.rsplit("/", 1)[-1].strip()
    if v.startswith("https://www.youtube.com/user/"):
        return v.rsplit("/", 1)[-1].strip()
    if v.startswith("channel/") or v.startswith("user/"):
        return v.rsplit("/", 1)[-1].strip()
    if v.startswith("UC"):
        return v
    return None


def _api_get(transport, resource: str, params: dict[str, Any], api_key: str) -> Any:
    from ..acquisition.transport import TransportError

    request_params = dict(params)
    request_params["key"] = api_key
    try:
        response = transport.request("GET", f"{YOUTUBE_API_BASE}/{resource}", params=request_params)
    except TransportError as exc:
        return {"status": "error", "error_category": "network", "detail": str(exc)}
    if response.status == 400:
        # An invalid API key surfaces as HTTP 400 with "API key not valid" —
        # this is a PROVISIONING failure (the key is configured but dead), not
        # an application error. It must fail closed and be reported as
        # NOT_CONFIGURED so the milestone can surface key-provisioning status
        # explicitly instead of silently collecting nothing.
        body = response.json() if response.body else {}
        message = ((body.get("error") or {}).get("message") or "").lower()
        if "api key not valid" in message or "badrequest" in str(body.get("error") or {}).lower():
            return {"status": "not_configured", "error_category": "invalid_api_key"}
        return {"status": "error", "error_category": "http_400", "detail": message[:200]}
    if response.status == 403:
        body = response.json() if response.body else {}
        error = (body.get("error") or {}).get("errors") or [{}]
        reason = (error[0] or {}).get("reason", "")
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            return {"status": "rate_limited", "error_category": "quota_exceeded"}
        if reason == "forbidden":
            return {"status": "not_configured", "error_category": "api_not_enabled"}
        return {"status": "error", "error_category": "forbidden", "detail": reason}
    if response.status == 429:
        return {"status": "rate_limited", "error_category": "rate_limited"}
    if response.status != 200:
        return {"status": "error", "error_category": f"http_{response.status}", "detail": response.body.decode("utf-8", "replace")[:300]}
    try:
        return response.json()
    except ValueError:
        return {"status": "error", "error_category": "schema_invalid"}


def fetch_channel_snapshot(transport, *, channel_id: str, api_key: str) -> dict[str, Any]:
    """One channels.list call → {status, subscriber_count, view_count, video_count}."""
    payload = _api_get(
        transport, "channels",
        {"part": "statistics", "id": channel_id, "maxResults": "1"},
        api_key,
    )
    if payload.get("status") is not None:
        return payload
    items = payload.get("items") or []
    if not items:
        return {"status": "missing", "error_category": "channel_not_found"}
    stats = (items[0].get("statistics") or {})
    return {
        "status": "ok",
        "subscriber_count": _int_or_none(stats.get("subscriberCount")),
        "view_count": _int_or_none(stats.get("viewCount")),
        "video_count": _int_or_none(stats.get("videoCount")),
        "channel_id": channel_id,
    }


def fetch_recent_video_stats(transport, *, channel_id: str, api_key: str) -> dict[str, Any]:
    """Most recent published video's statistics (videos.list, 1 unit)."""
    search = _api_get(
        transport, "search",
        {"part": "id", "channelId": channel_id, "order": "date", "type": "video", "maxResults": "1"},
        api_key,
    )
    if search.get("status") is not None:
        return search
    items = search.get("items") or []
    if not items:
        return {"status": "missing", "error_category": "no_videos"}
    video_id = ((items[0].get("id") or {}).get("videoId"))
    if not video_id:
        return {"status": "missing", "error_category": "no_video_id"}
    videos = _api_get(
        transport, "videos",
        {"part": "statistics", "id": video_id, "maxResults": "1"},
        api_key,
    )
    if videos.get("status") is not None:
        return videos
    vitems = videos.get("items") or []
    if not vitems:
        return {"status": "missing", "error_category": "video_not_found"}
    stats = (vitems[0].get("statistics") or {})
    return {
        "status": "ok",
        "video_id": video_id,
        "views": _int_or_none(stats.get("viewCount")),
        "likes": _int_or_none(stats.get("likeCount")),
        "comments": _int_or_none(stats.get("commentCount")),
    }


def build_snapshot_observation(
    *,
    artist_name: str,
    artist_key: str,
    channel_id: str,
    day: str,
    retrieved_at: str,
    metric_kind: str,
    value: int | None,
    value_unit: str,
    status: str,
    source_url: str,
    extra_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provenance = {
        "source_system": SOURCE_SYSTEM,
        "endpoint": "channels.list statistics snapshot",
        "channel_id": channel_id,
        "semantics": "MUTABLE_PLATFORM_SNAPSHOT; snapshot at retrieval moment; "
                     "never reconstructed historically",
    }
    if extra_provenance:
        provenance.update(extra_provenance)
    return {
        "observation_key": snapshot_key(artist_key=artist_key, day=day, channel_id=channel_id)
        + hashlib.sha256(f"|{metric_kind}".encode("utf-8")).hexdigest()[:16],
        "artist_key": artist_key,
        "festival_key": None,
        "edition_key": None,
        "edition_year": None,
        "source_system": SOURCE_SYSTEM,
        "metric_kind": metric_kind,
        "project": None,
        "access_method": "public_api",
        "agent": "user",
        "article_title": artist_name.strip() or None,
        "granularity": "daily",
        "period_start": day,
        "period_end": day,
        "value": value,
        "value_sum": value,
        "value_unit": value_unit,
        "status": status,
        "error_code": None if status == "ok" else status,
        "error_message": None,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "raw_response_json": None,
        "provenance_json": json.dumps(provenance, default=str),
        "metric_version": METRIC_VERSION,
    }


def persist_snapshot(conn, row: dict[str, Any]) -> int:
    exists = conn.execute(
        "SELECT 1 FROM metrics.artist_attention_observations WHERE observation_key = ?",
        [row["observation_key"]],
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO metrics.artist_attention_observations
            (observation_key, artist_key, festival_key, edition_key, edition_year,
             source_system, metric_kind, project, access_method, agent, article_title,
             granularity, period_start, period_end, value, value_sum, value_unit,
             status, error_code, error_message, source_url, retrieved_at,
             raw_response_json, provenance_json, metric_version, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            row["observation_key"], row["artist_key"], row["festival_key"], row["edition_key"],
            row["edition_year"], row["source_system"], row["metric_kind"], row["project"],
            row["access_method"], row["agent"], row["article_title"], row["granularity"],
            row["period_start"], row["period_end"], row["value"], row["value_sum"],
            row["value_unit"], row["status"], row["error_code"], row["error_message"],
            row["source_url"], row["retrieved_at"], row["raw_response_json"],
            row["provenance_json"], row["metric_version"],
        ],
    )
    return 1


def collect_channel_snapshots(
    conn,
    transport,
    *,
    artists: list[dict[str, Any]],
    api_key: str | None,
    snapshot_date: str | None = None,
    include_recent_video: bool = True,
) -> dict[str, Any]:
    """Daily channel snapshot tape for a list of artists.

    ``artists`` are dicts with ``artist_name`` (display name), ``artist_key``
    (canonical) and ``channel_id`` (resolved from entity_external_ids; may be
    None → missing, never fabricated).

    Returns a summary; a quota hit stops the batch (RATE_LIMITED_STOPPED).
    """
    if not api_key:
        return {
            "status": "NOT_CONFIGURED",
            "detail": "YOUTUBE_API_KEY not set — no snapshot collected",
            "artists_eligible": len(artists),
            "rows_persisted": 0,
        }
    day = snapshot_date or date.today().isoformat()
    retrieved_at = utc_now().isoformat()
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "artists_eligible": len(artists),
        "artists_resolved": 0,
        "artists_missing": 0,
        "artists_error": 0,
        "rows_persisted": 0,
        "quota_units_used": 0,
        "snapshot_date": day,
    }
    for artist in artists:
        name = (artist.get("artist_name") or "").strip()
        artist_key = artist.get("artist_key") or artist_key_for(name)
        channel_id = resolve_channel_id(artist.get("channel_id"))
        if not channel_id:
            summary["artists_missing"] += 1
            row = build_snapshot_observation(
                artist_name=name, artist_key=artist_key, channel_id="",
                day=day, retrieved_at=retrieved_at, metric_kind="YT_CHANNEL_RESOLVED",
                value=None, value_unit="count", status="missing",
                source_url="", extra_provenance={"detail": "no resolvable channel id"},
            )
            summary["rows_persisted"] += persist_snapshot(conn, row)
            continue
        snap = fetch_channel_snapshot(transport, channel_id=channel_id, api_key=api_key)
        summary["quota_units_used"] += QUOTA_UNITS_CHANNELS
        if snap.get("status") == "rate_limited":
            summary["status"] = "RATE_LIMITED_STOPPED"
            summary["detail"] = "quota exceeded at channels.list"
            break
        if snap.get("status") == "not_configured":
            summary["status"] = "NOT_CONFIGURED"
            category = snap.get("error_category")
            summary["detail"] = (
                "YOUTUBE_API_KEY present but INVALID (API key not valid) — "
                "provisioning required; no snapshot collected"
                if category == "invalid_api_key"
                else "YouTube Data API not enabled for this key"
            )
            summary["key_provisioning_status"] = "INVALID_KEY" if category == "invalid_api_key" else "API_NOT_ENABLED"
            break
        if snap.get("status") != "ok":
            summary["artists_error"] += 1
            continue
        summary["artists_resolved"] += 1
        for metric_kind, value, unit in (
            ("YT_SUBSCRIBERS", snap["subscriber_count"], "subscribers"),
            ("YT_CHANNEL_VIEWS", snap["view_count"], "views"),
            ("YT_VIDEO_COUNT", snap["video_count"], "videos"),
        ):
            row = build_snapshot_observation(
                artist_name=name, artist_key=artist_key, channel_id=channel_id,
                day=day, retrieved_at=retrieved_at, metric_kind=metric_kind,
                value=value, value_unit=unit, status="ok" if value is not None else "missing",
                source_url=f"https://www.youtube.com/channel/{channel_id}",
            )
            summary["rows_persisted"] += persist_snapshot(conn, row)
        if include_recent_video:
            vid = fetch_recent_video_stats(transport, channel_id=channel_id, api_key=api_key)
            summary["quota_units_used"] += QUOTA_UNITS_VIDEOS
            if vid.get("status") == "rate_limited":
                summary["status"] = "RATE_LIMITED_STOPPED"
                summary["detail"] = "quota exceeded at videos.list"
                break
            if vid.get("status") == "ok":
                for metric_kind, value, unit in (
                    ("YT_RECENT_VIDEO_VIEWS", vid["views"], "views"),
                    ("YT_RECENT_VIDEO_LIKES", vid["likes"], "likes"),
                    ("YT_RECENT_VIDEO_COMMENTS", vid["comments"], "comments"),
                ):
                    row = build_snapshot_observation(
                        artist_name=name, artist_key=artist_key, channel_id=channel_id,
                        day=day, retrieved_at=retrieved_at, metric_kind=metric_kind,
                        value=value, value_unit=unit,
                        status="ok" if value is not None else "missing",
                        source_url=f"https://www.youtube.com/watch?v={vid['video_id']}",
                        extra_provenance={"video_id": vid["video_id"], "endpoint": "videos.list"},
                    )
                    summary["rows_persisted"] += persist_snapshot(conn, row)
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
