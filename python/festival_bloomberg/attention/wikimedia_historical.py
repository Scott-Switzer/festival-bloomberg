"""Wikimedia historical daily-pageviews backfill (PIT-correct, per-day rows).

Milestone OPEN_ARTIST_MARKET_DATA_V1 — SOURCE 2.

The existing ``wikimedia_pageviews.collect_artist_pageviews`` persists ONE row
per artist per requested window (the 30d sum). That cannot power a historical
factor tape. This module fetches the FULL per-article range (the Analytics API
returns one item per day for the requested range in a single call) and persists
ONE observation per (artist, day) — ``period_start = period_end = day`` — so the
security master can derive WIKI_VIEWS_1D/7D/28D/90D + zscore + momentum + shock
from a real daily series.

PIT semantics (mirrors ``attention.historical_pit`` / the pilot script):

* ``period_start = period_end`` is the observation DAY (from the API's
  per-day timestamp), never the retrieval time;
* ``available_at`` (provenance) = observation_day + 1 — Wikimedia loads a
  day's aggregate at the end of that day (00:00 UTC next day);
* ``retrieved_at`` = when we fetched it — provenance only, NEVER an
  admissibility gate;
* Days before 2015-07-01 are UNAVAILABLE (series did not exist) — never
  missing, never zero.

Chunking: the API serves multi-year ranges, but very long ranges can be
truncated by intermediaries, so requests are chunked into bounded windows
(default 400 days) with no overlap. Idempotency: the observation key is a
stable hash of (artist_key, project, day) — re-running is a no-op.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..identity.spotify import normalize_name
from .wikimedia_pageviews import (
    WIKIMEDIA_SERIES_START,
    build_pageviews_observation,
    fetch_pageviews,
    persist_pageviews,
    wikimedia_available_at,
)

SOURCE_SYSTEM = "wikimedia"
METRIC_VERSION = "wikimedia_pageviews_daily_v1"
DEFAULT_CHUNK_DAYS = 400
DEFAULT_MIN_INTERVAL_SECONDS = 0.3


def artist_key_for(name: str) -> str:
    return f"name::{normalize_name(name)}"


def daily_observation_key(*, artist_key: str, day: str) -> str:
    import hashlib

    material = "|".join([artist_key, SOURCE_SYSTEM, "pageviews", "daily", day, METRIC_VERSION])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _day_from_timestamp(raw: str | None) -> date | None:
    ts = str(raw or "")
    digits = "".join(ch for ch in ts if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def split_windows(
    start: date,
    end: date,
    *,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
) -> list[tuple[date, date]]:
    """Non-overlapping [start, end] windows covering the range (inclusive)."""
    if end < start:
        return []
    out: list[tuple[date, date]] = []
    lo = start
    while lo <= end:
        hi = min(lo + timedelta(days=chunk_days - 1), end)
        out.append((lo, hi))
        lo = hi + timedelta(days=1)
    return out


def collect_artist_daily_pageviews(
    conn,
    transport,
    *,
    names: list[str],
    start: str | None = None,
    end: str | None = None,
    project: str = "en.wikipedia",
    access: str = "all-access",
    agent: str = "user",
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    artist_keys_by_name: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Backfill daily pageviews for a list of artist names (one row per day).

    ``start``/``end`` are YYYY-MM-DD or YYYYMMDD; default start is
    WIKIMEDIA_SERIES_START (2015-07-01), default end is yesterday UTC. Every
    day on/after the series start that the API returns becomes one
    ``artist_attention_observations`` row with period_start = period_end = day.

    Returns a summary with per-artist coverage and the overall daily row count.
    """
    end_dt = _parse_end(end)
    start_dt = _parse_start(start, end_dt)
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "names_attempted": 0,
        "names_ok": 0,
        "names_missing": 0,
        "names_error": 0,
        "daily_rows_persisted": 0,
        "chunks_requested": 0,
        "window_start": start_dt.isoformat(),
        "window_end": end_dt.isoformat(),
        "series_start": WIKIMEDIA_SERIES_START.isoformat(),
        "per_artist": {},
    }
    for index, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        if index and min_interval_seconds > 0:
            time.sleep(min_interval_seconds)
        summary["names_attempted"] += 1
        artist_rows = 0
        status = "ok"
        for lo, hi in split_windows(start_dt, end_dt, chunk_days=chunk_days):
            summary["chunks_requested"] += 1
            result = fetch_pageviews(
                transport,
                title=name,
                start=lo.strftime("%Y%m%d"),
                end=hi.strftime("%Y%m%d"),
                project=project,
                access=access,
                agent=agent,
            )
            if result["status"] == "missing":
                if status == "ok":
                    status = "missing"
                continue
            if result["status"] != "ok":
                status = "error"
                summary["names_error"] += 1
                break
            for item in result["items"]:
                day = _day_from_timestamp(item.get("timestamp"))
                if day is None or day < WIKIMEDIA_SERIES_START:
                    continue
                day_iso = day.isoformat()
                row = build_pageviews_observation(
                    artist_name=name,
                    title=name,
                    project=result["project"],
                    access=result["access"],
                    agent=result["agent"],
                    granularity="daily",
                    start=day_iso.replace("-", ""),
                    end=day_iso.replace("-", ""),
                    items=[item],
                    status="ok",
                    error_code=None,
                    error_message=None,
                    source_url=result["source_url"],
                    retrieved_at=result["retrieved_at"],
                    raw_response=item,
                )
                if artist_keys_by_name:
                    canonical = artist_keys_by_name.get(name) or artist_keys_by_name.get(name.lower())
                    if canonical:
                        row["artist_key"] = canonical
                row["observation_key"] = daily_observation_key(
                    artist_key=row["artist_key"], day=day_iso,
                )
                row["period_start"] = day_iso
                row["period_end"] = day_iso
                row["metric_version"] = METRIC_VERSION
                prov = json.loads(row["provenance_json"] or "{}")
                prov["granularity"] = "daily"
                prov["available_at"] = wikimedia_available_at(day).isoformat()
                prov["observation_day"] = day_iso
                prov["backfill"] = True
                prov["chunk_days"] = chunk_days
                row["provenance_json"] = json.dumps(prov, default=str)
                summary["daily_rows_persisted"] += persist_pageviews(conn, row)
                artist_rows += 1
        if status == "ok" and artist_rows:
            summary["names_ok"] += 1
        elif status == "ok":
            summary["names_missing"] += 1
        summary["per_artist"][name] = {
            "status": status,
            "daily_rows": artist_rows,
            "first_day": None,
            "last_day": None,
        }
        if artist_rows:
            days = conn.execute(
                "SELECT MIN(period_start), MAX(period_end) FROM metrics.artist_attention_observations "
                "WHERE artist_key = ? AND source_system = 'wikimedia' AND metric_version = ?",
                [artist_key_for(name), METRIC_VERSION],
            ).fetchone()
            summary["per_artist"][name]["first_day"] = days[0]
            summary["per_artist"][name]["last_day"] = days[1]
    summary["status"] = "COMPLETE"
    return summary


def _parse_start(start: str | None, end_dt: date) -> date:
    if start:
        d = _parse_date(start)
        if d is not None:
            return max(d, WIKIMEDIA_SERIES_START)
    return WIKIMEDIA_SERIES_START


def _parse_end(end: str | None) -> date:
    if end:
        d = _parse_date(end)
        if d is not None:
            return min(d, date.today() - timedelta(days=1))
    return date.today() - timedelta(days=1)


def _parse_date(value: str) -> date | None:
    digits = "".join(ch for ch in str(value) if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def batch_persist_daily_rows(conn, rows: list[dict[str, Any]]) -> int:
    """Bulk-persist daily pageview rows with a single existence gate.

    The per-row ``persist_pageviews`` path (SELECT + INSERT per day) does not
    scale to the multi-million-row backfill; this batches one existence query
    for the whole chunk and one ``executemany`` INSERT. Idempotency is kept:
    a day already present is skipped, never rewritten.
    """
    if not rows:
        return 0
    keys = [r["observation_key"] for r in rows]
    existing = {
        r[0] for r in conn.execute(
            "SELECT observation_key FROM metrics.artist_attention_observations "
            "WHERE observation_key IN (SELECT UNNEST(?))",
            [keys],
        ).fetchall()
    }
    fresh = [r for r in rows if r["observation_key"] not in existing]
    if not fresh:
        return 0
    cols = (
        "observation_key, artist_key, festival_key, edition_key, edition_year, "
        "source_system, metric_kind, project, access_method, agent, article_title, "
        "granularity, period_start, period_end, value, value_sum, value_unit, "
        "status, error_code, error_message, source_url, retrieved_at, "
        "raw_response_json, provenance_json, metric_version"
    )
    placeholders = ", ".join("?" for _ in range(25))
    conn.executemany(
        f"""
        INSERT INTO metrics.artist_attention_observations ({cols}, ingested_at)
        VALUES ({placeholders}, CURRENT_TIMESTAMP)
        """,
        [
            [
                r["observation_key"], r["artist_key"], r["festival_key"], r["edition_key"],
                r["edition_year"], r["source_system"], r["metric_kind"], r["project"],
                r["access_method"], r["agent"], r["article_title"], r["granularity"],
                r["period_start"], r["period_end"], r["value"], r["value_sum"],
                r["value_unit"], r["status"], r["error_code"], r["error_message"],
                r["source_url"], r["retrieved_at"], r["raw_response_json"],
                r["provenance_json"], r["metric_version"],
            ]
            for r in fresh
        ],
    )
    return len(fresh)


def collect_artist_daily_pageviews_batched(
    conn,
    transport,
    *,
    names: list[str],
    start: str | None = None,
    end: str | None = None,
    project: str = "en.wikipedia",
    access: str = "all-access",
    agent: str = "user",
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    artist_keys_by_name: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Full-scale daily backfill with batched persistence.

    Same PIT semantics and per-day rows as ``collect_artist_daily_pageviews``,
    but rows are accumulated per chunk and bulk-inserted (one existence gate
    + one executemany per chunk) — the ~4M-row path. Fetching is unchanged
    (one request per chunk window).
    """
    end_dt = _parse_end(end)
    start_dt = _parse_start(start, end_dt)
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "names_attempted": 0,
        "names_ok": 0,
        "names_missing": 0,
        "names_error": 0,
        "daily_rows_persisted": 0,
        "daily_rows_skipped_existing": 0,
        "chunks_requested": 0,
        "window_start": start_dt.isoformat(),
        "window_end": end_dt.isoformat(),
        "series_start": WIKIMEDIA_SERIES_START.isoformat(),
        "per_artist": {},
    }
    for index, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        if index and min_interval_seconds > 0:
            time.sleep(min_interval_seconds)
        summary["names_attempted"] += 1
        pending: list[dict[str, Any]] = []
        status = "ok"
        artist_new = 0
        artist_skipped = 0
        for lo, hi in split_windows(start_dt, end_dt, chunk_days=chunk_days):
            summary["chunks_requested"] += 1
            result = fetch_pageviews(
                transport,
                title=name,
                start=lo.strftime("%Y%m%d"),
                end=hi.strftime("%Y%m%d"),
                project=project,
                access=access,
                agent=agent,
            )
            if result["status"] == "missing":
                if status == "ok":
                    status = "missing"
                continue
            if result["status"] != "ok":
                status = "error"
                summary["names_error"] += 1
                break
            for item in result["items"]:
                day = _day_from_timestamp(item.get("timestamp"))
                if day is None or day < WIKIMEDIA_SERIES_START:
                    continue
                day_iso = day.isoformat()
                row = build_pageviews_observation(
                    artist_name=name, title=name, project=result["project"],
                    access=result["access"], agent=result["agent"], granularity="daily",
                    start=day_iso.replace("-", ""), end=day_iso.replace("-", ""),
                    items=[item], status="ok", error_code=None, error_message=None,
                    source_url=result["source_url"], retrieved_at=result["retrieved_at"],
                    raw_response=item,
                )
                if artist_keys_by_name:
                    canonical = artist_keys_by_name.get(name) or artist_keys_by_name.get(name.lower())
                    if canonical:
                        row["artist_key"] = canonical
                row["observation_key"] = daily_observation_key(artist_key=row["artist_key"], day=day_iso)
                row["period_start"] = day_iso
                row["period_end"] = day_iso
                row["metric_version"] = METRIC_VERSION
                prov = json.loads(row["provenance_json"] or "{}")
                prov["granularity"] = "daily"
                prov["available_at"] = wikimedia_available_at(day).isoformat()
                prov["observation_day"] = day_iso
                prov["backfill"] = True
                prov["chunk_days"] = chunk_days
                row["provenance_json"] = json.dumps(prov, default=str)
                pending.append(row)
            # flush per chunk to bound memory
            written = batch_persist_daily_rows(conn, pending)
            summary["daily_rows_persisted"] += written
            summary["daily_rows_skipped_existing"] += len(pending) - written
            artist_new += written
            artist_skipped += len(pending) - written
            pending = []
        if pending:
            written = batch_persist_daily_rows(conn, pending)
            summary["daily_rows_persisted"] += written
            summary["daily_rows_skipped_existing"] += len(pending) - written
            artist_new += written
            artist_skipped += len(pending) - written
        if status == "ok" and artist_new:
            summary["names_ok"] += 1
        elif status == "ok":
            summary["names_missing"] += 1
        summary["per_artist"][name] = {
            "status": status,
            "daily_rows_new": artist_new,
            "daily_rows_existing": artist_skipped,
            "first_day": None,
            "last_day": None,
        }
        if artist_new or artist_skipped:
            days = conn.execute(
                "SELECT MIN(period_start), MAX(period_end) FROM metrics.artist_attention_observations "
                "WHERE artist_key = ? AND source_system = 'wikimedia' AND metric_version = ?",
                [artist_keys_by_name.get(name) or artist_keys_by_name.get(name.lower()) or artist_key_for(name),
                 METRIC_VERSION],
            ).fetchone()
            summary["per_artist"][name]["first_day"] = days[0]
            summary["per_artist"][name]["last_day"] = days[1]
    summary["status"] = "COMPLETE"
    return summary


def collect_artist_daily_pageviews_bounded(
    conn,
    transport,
    *,
    names: list[str],
    lookback_days: int = 90,
    **kwargs: Any,
) -> dict[str, Any]:
    """Bounded daily backfill (last ``lookback_days`` only) — for smoke tests and
    incremental maintenance where the full history has already been acquired."""
    end_dt = date.today() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=lookback_days)
    return collect_artist_daily_pageviews(
        conn, transport, names=names,
        start=start_dt.isoformat(), end=end_dt.isoformat(), **kwargs,
    )


def artist_keys_by_name_for(conn, names: list[str]) -> dict[str, str]:
    """Map display names to canonical artist_key (mbid:: / name::) from core.artists."""
    out: dict[str, str] = {}
    if not names:
        return out
    rows = conn.execute(
        "SELECT name, artist_key FROM core.artists WHERE name IN (SELECT UNNEST(?))",
        [names],
    ).fetchall()
    for name, key in rows:
        out[name] = key
        out[name.lower()] = key
    return out
