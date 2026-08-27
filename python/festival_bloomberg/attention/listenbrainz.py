"""Key-free ListenBrainz artist-statistics collector (attention time series).

Fetches ``GET /1/stats/artist/{mbid}/listeners`` for each resolved artist MBID
and persists the provider's aggregate into ``metrics.artist_attention_observations``
as two metrics:

- ``LISTENBRAINZ_LISTEN_COUNT``   — ``total_listen_count`` (sitewide aggregate).
- ``LISTENBRAINZ_LISTENER_COUNT`` — size of the top-N listener list (a *sample*,
  recorded as such in provenance, never presented as a census).

Both are labeled ``ATTENTION_CONSUMPTION_SAMPLE`` and are never treated as
local demand. An artist with no MBID is skipped (NULL stays NULL); a 204/404
is persisted with ``status = 'missing'``, never as a zero.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from ..acquisition.contracts import AcquisitionRequest
from ..acquisition.providers.listenbrainz import ListenBrainzProvider
from ..identity.spotify import normalize_name

SOURCE_SYSTEM = "listenbrainz"
METRIC_VERSION = "listenbrainz_stats_v1"
DEFAULT_RANGE = "all_time"


def artist_key_for(name: str) -> str:
    """Documented fallback artist_key: ``name::<normalized_name>``."""
    return f"name::{normalize_name(name)}"


def observation_key(
    *,
    artist_key: str,
    mbid: str,
    metric_kind: str,
    stats_range: str,
    retrieved_at: str | None = None,
    provider_last_updated: str | None = None,
) -> str:
    """Temporal observation key for CUMULATIVE provider aggregates.

    Listen counts / listener counts are cumulative sitewide totals that change
    over time. Keying by retrieval day (and provider ``last_updated`` when the
    API supplies it) means each distinct observation accumulates instead of
    being silently dropped when the identical (artist, metric, range) tuple is
    re-fetched. A re-run within the same day stays idempotent; a later
    retrieval creates a new row.
    """
    material = "|".join(
        [
            artist_key, SOURCE_SYSTEM, metric_kind, mbid,
            stats_range or DEFAULT_RANGE, METRIC_VERSION,
            str(provider_last_updated) if provider_last_updated is not None else "",
            (retrieved_at or "")[:10],  # YYYY-MM-DD bucket
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def build_listenbrainz_observation(
    *,
    artist_name: str,
    artist_mbid: str,
    metric_kind: str,
    value,
    value_unit: str,
    stats_range: str,
    period_start: str | None,
    period_end: str | None,
    status: str,
    source_url: str,
    retrieved_at: str,
    artist_key: str | None = None,
    extra_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Prefer the canonical artist_key (``mbid::<mbid>``) so attention rows
    # join to core.artists; fall back to the documented name-based key.
    if not artist_key:
        artist_key = artist_key_for(artist_name)
    provenance = {
        "source_system": SOURCE_SYSTEM,
        "endpoint": "artist_listeners",
        "artist_mbid": artist_mbid,
        "stats_range": stats_range,
        "metric_kind": metric_kind,
        "semantics": "ATTENTION_CONSUMPTION_SAMPLE; never local demand",
    }
    if extra_provenance:
        provenance.update(extra_provenance)
    return {
        "observation_key": observation_key(
            artist_key=artist_key, mbid=artist_mbid,
            metric_kind=metric_kind, stats_range=stats_range,
            retrieved_at=retrieved_at,
            provider_last_updated=extra_provenance.get("provider_last_updated")
            if extra_provenance else None,
        ),
        "artist_key": artist_key,
        "festival_key": None,
        "edition_key": None,
        "edition_year": None,
        "source_system": SOURCE_SYSTEM,
        "metric_kind": metric_kind,
        "project": None,
        "access_method": "public_api",
        "agent": "user",
        "article_title": (artist_name or "").strip() or None,
        "granularity": stats_range or DEFAULT_RANGE,
        "period_start": period_start,
        "period_end": period_end,
        "value": value,
        "value_sum": value,
        "value_unit": value_unit,
        "status": status,
        "error_code": None,
        "error_message": None,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "raw_response_json": None,
        "provenance_json": json.dumps(provenance, default=str),
        "metric_version": METRIC_VERSION,
    }


def persist_observation(conn, row: dict[str, Any]) -> int:
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


POPULARITY_URL = "https://api.listenbrainz.org/1/popularity/artist"
BATCH_SIZE = 1000


def fetch_artist_popularity(transport, mbids: list[str]) -> dict[str, Any]:
    """POST a batch of MBIDs to the bulk popularity endpoint.

    Returns a summary with ``rows`` = list of
    ``{artist_mbid, total_listen_count, total_user_count}`` (missing -> None,
    never zero-filled).
    """
    from ..acquisition.transport import TransportError

    mbids = [m for m in mbids if m and m.strip()]
    if not mbids:
        return {"status": "ok", "rows": [], "requests": 0}
    rows: list[dict[str, Any]] = []
    requests = 0
    for i in range(0, len(mbids), BATCH_SIZE):
        chunk = mbids[i:i + BATCH_SIZE]
        requests += 1
        try:
            response = transport.request(
                "POST", POPULARITY_URL,
                headers={"Accept": "application/json"},
                body={"artist_mbids": chunk},
                timeout_seconds=30.0,
            )
        except TransportError as exc:
            return {"status": "error", "rows": rows, "requests": requests, "detail": str(exc)}
        if response.status == 429:
            return {"status": "rate_limited", "rows": rows, "requests": requests}
        if response.status != 200:
            return {"status": "error", "rows": rows, "requests": requests,
                    "detail": f"http {response.status}"}
        payload = json.loads(response.body.decode("utf-8"))
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, dict) and item.get("artist_mbid"):
                rows.append({
                    "artist_mbid": item.get("artist_mbid"),
                    "total_listen_count": item.get("total_listen_count"),
                    "total_user_count": item.get("total_user_count"),
                })
    return {"status": "ok", "rows": rows, "requests": requests}


def collect_artist_popularity(
    conn,
    transport,
    *,
    artists: list[tuple[str, str]],
    artist_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bulk ListenBrainz popularity -> attention observations (2 metrics).

    ``artists`` are ``(artist_name, artist_mbid)`` pairs. Missing counts stay
    NULL (never zero-filled). Labels: LISTENBRAINZ_TOTAL_LISTEN_COUNT /
    LISTENBRAINZ_TOTAL_USER_COUNT, ATTENTION_CONSUMPTION_SAMPLE.

    ``artist_keys`` optionally maps MBID -> canonical artist_key so rows join
    to core.artists; without it the name-based fallback key is used.
    """
    mbids = [mbid for _name, mbid in artists if mbid and mbid.strip()]
    name_by_mbid = {mbid: name for name, mbid in artists if mbid}
    result = fetch_artist_popularity(transport, mbids)
    summary = {
        "status": result["status"], "artists_eligible": len(mbids),
        "artists_returned": 0, "rows_persisted": 0, "requests": result["requests"],
        "error": 0,
    }
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for row in result["rows"]:
        mbid = row["artist_mbid"]
        name = name_by_mbid.get(mbid, mbid)
        summary["artists_returned"] += 1
        for metric_kind, value, unit in (
            ("LISTENBRAINZ_TOTAL_LISTEN_COUNT", row["total_listen_count"], "listens"),
            ("LISTENBRAINZ_TOTAL_USER_COUNT", row["total_user_count"], "listeners"),
        ):
            obs = build_listenbrainz_observation(
                artist_name=name, artist_mbid=mbid, metric_kind=metric_kind,
                value=value, value_unit=unit, stats_range="all_time",
                period_start=None, period_end=None, status="ok" if value is not None else "missing",
                source_url=POPULARITY_URL, retrieved_at=retrieved_at,
                artist_key=artist_keys.get(mbid) if artist_keys else None,
                extra_provenance={"endpoint": "bulk_popularity"},
            )
            summary["rows_persisted"] += persist_observation(conn, obs)
    if result["status"] != "ok":
        summary["error"] = 1
    return summary


def collect_artist_listen_counts(
    conn,
    transport,
    *,
    artists: list[tuple[str, str]],
    stats_range: str = DEFAULT_RANGE,
    min_interval_seconds: float = 0.5,
    artist_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bounded ListenBrainz collection over ``(artist_name, artist_mbid)`` pairs.

    Artists without an MBID are skipped (counted, never fabricated). Each
    resolved artist yields up to two rows (listen count + listener sample).
    """
    provider = ListenBrainzProvider(transport=transport)
    summary = {
        "status": "RUNNING",
        "artists_attempted": 0,
        "artists_resolved": 0,
        "missing": 0,
        "error": 0,
        "rate_limited": 0,
        "rows_persisted": 0,
        "stats_range": stats_range,
    }
    for index, (name, mbid) in enumerate(artists):
        mbid = (mbid or "").strip()
        if not mbid:
            continue
        if index and min_interval_seconds > 0:
            time.sleep(min_interval_seconds)
        summary["artists_attempted"] += 1
        req = AcquisitionRequest.new(
            entity_id=mbid,
            entity_type="artist",
            platform="listenbrainz",
            query=stats_range,
            operation="ARTIST_LISTENERS",
            external_id=mbid,
            commercial_context="research",
        )
        result = provider.acquire(req)
        if result.status.value == "RATE_LIMITED":
            summary["rate_limited"] += 1
            summary["status"] = "RATE_LIMITED_STOPPED"
            break
        if result.status.value in ("NO_RESULTS", "SCHEMA_INVALID"):
            summary["missing"] += 1
            continue
        if result.status.value != "SUCCESS":
            summary["error"] += 1
            continue
        summary["artists_resolved"] += 1
        rec = result.records[0]
        retrieved_at = result.completed_at.isoformat()
        period_start = None
        period_end = None
        from ..acquisition.providers.listenbrainz import ts_to_date

        period_start = ts_to_date(rec.get("from_ts"))
        period_end = ts_to_date(rec.get("to_ts"))

        listen_count = rec.get("total_listen_count")
        listener_sample = rec.get("listener_count_sample")

        for metric_kind, value, unit, extra in (
            ("LISTENBRAINZ_LISTEN_COUNT", listen_count, "listens", {}),
            (
                "LISTENBRAINZ_LISTENER_COUNT",
                listener_sample,
                "listeners",
                {"listener_count_scope": "top_n_sample"},
            ),
        ):
            row = build_listenbrainz_observation(
                artist_name=name or rec.get("artist_name") or mbid,
                artist_mbid=mbid,
                metric_kind=metric_kind,
                value=value,
                value_unit=unit,
                stats_range=rec.get("stats_range") or stats_range,
                period_start=period_start,
                period_end=period_end,
                status="ok",
                source_url=rec.get("source_url") or "",
                retrieved_at=retrieved_at,
                artist_key=artist_keys.get(mbid) if artist_keys else None,
                extra_provenance=extra,
            )
            summary["rows_persisted"] += persist_observation(conn, row)
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary


def collect_priority_range_history(
    conn,
    transport,
    *,
    artists: list[tuple[str, str]],
    ranges: tuple[str, ...] = ("week", "month", "all_time"),
    min_interval_seconds: float = 0.6,
    artist_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Range-based artist statistics for a SMALL high-value universe.

    For each artist, capture the supported ranges (week/month/all_time) so a
    trend line exists without pretending cumulative popularity is daily
    consumption. ``from_ts``/``to_ts``/``last_updated`` are preserved in
    provenance. Bounded + rate-limit-aware.
    """
    provider = ListenBrainzProvider(transport=transport)
    summary = {
        "status": "RUNNING",
        "artists_eligible": len(artists),
        "artists_completed": 0,
        "ranges_requested": 0,
        "missing": 0,
        "error": 0,
        "rate_limited": 0,
        "rows_persisted": 0,
        "ranges": list(ranges),
    }
    for index, (name, mbid) in enumerate(artists):
        mbid = (mbid or "").strip()
        if not mbid:
            continue
        if index and min_interval_seconds > 0:
            time.sleep(min_interval_seconds)
        for stats_range in ranges:
            summary["ranges_requested"] += 1
            req = AcquisitionRequest.new(
                entity_id=mbid,
                entity_type="artist",
                platform="listenbrainz",
                query=stats_range,
                operation="ARTIST_LISTENERS",
                external_id=mbid,
                commercial_context="research",
            )
            result = provider.acquire(req)
            if result.status.value == "RATE_LIMITED":
                summary["rate_limited"] += 1
                summary["status"] = "RATE_LIMITED_STOPPED"
                return summary
            if result.status.value in ("NO_RESULTS", "SCHEMA_INVALID"):
                summary["missing"] += 1
                continue
            if result.status.value != "SUCCESS":
                summary["error"] += 1
                continue
            rec = result.records[0]
            from ..acquisition.providers.listenbrainz import ts_to_date

            period_start = ts_to_date(rec.get("from_ts"))
            period_end = ts_to_date(rec.get("to_ts"))
            listen_count = rec.get("total_listen_count")
            listener_sample = rec.get("listener_count_sample")
            for metric_kind, value, unit, extra in (
                ("LISTENBRAINZ_LISTEN_COUNT", listen_count, "listens", {}),
                (
                    "LISTENBRAINZ_LISTENER_COUNT",
                    listener_sample,
                    "listeners",
                    {"listener_count_scope": "top_n_sample"},
                ),
            ):
                row = build_listenbrainz_observation(
                    artist_name=name or rec.get("artist_name") or mbid,
                    artist_mbid=mbid,
                    metric_kind=metric_kind,
                    value=value,
                    value_unit=unit,
                    stats_range=rec.get("stats_range") or stats_range,
                    period_start=period_start,
                    period_end=period_end,
                    status="ok",
                    source_url=rec.get("source_url") or "",
                    retrieved_at=result.completed_at.isoformat(),
                    artist_key=artist_keys.get(mbid) if artist_keys else None,
                    extra_provenance={
                        **extra,
                        "provider_last_updated": rec.get("last_updated"),
                        "range_collection": True,
                    },
                )
                summary["rows_persisted"] += persist_observation(conn, row)
        summary["artists_completed"] += 1
    summary["status"] = "COMPLETE"
    return summary
