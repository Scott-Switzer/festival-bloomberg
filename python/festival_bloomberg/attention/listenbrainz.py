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


def observation_key(*, artist_key: str, mbid: str, metric_kind: str, stats_range: str) -> str:
    material = "|".join(
        [artist_key, SOURCE_SYSTEM, metric_kind, mbid, stats_range or DEFAULT_RANGE, METRIC_VERSION]
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
    extra_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
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


def collect_artist_listen_counts(
    conn,
    transport,
    *,
    artists: list[tuple[str, str]],
    stats_range: str = DEFAULT_RANGE,
    min_interval_seconds: float = 0.5,
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
            query="",
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
                extra_provenance=extra,
            )
            summary["rows_persisted"] += persist_observation(conn, row)
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary
