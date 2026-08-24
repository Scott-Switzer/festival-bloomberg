"""Key-free Wikimedia REST Pageviews collector (attention time series).

Endpoint:
    GET /metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}

No API key is required. Titles are space -> underscore then URI-encoded. Each
per-article response is persisted as one ``metrics.artist_attention_observations``
row keyed by a stable hash, so re-running a window is idempotent and never
rewrites history.

Semantics preserved:

- Pageviews are an ATTENTION channel (``ATTENTION_CONSUMPTION_SAMPLE``), never
  LOCAL_DEMAND and never a ticket-demand proxy.
- The article title is stored verbatim (``article_title``); a 404 title is
  persisted with ``status = 'missing'`` — UNKNOWN is never encoded as a zero
  value.
- ``period_start`` / ``period_end`` are the *observation window*; ``retrieved_at``
  is when we fetched it. They are never collapsed.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..identity.spotify import normalize_name

WIKIMEDIA_PAGEVIEWS_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
METRIC_VERSION = "wikimedia_pageviews_v1"
SOURCE_SYSTEM = "wikimedia"

#: The Analytics API serves pageview data starting on this day. Days before
#: this are OUTSIDE the source's existence and are therefore UNAVAILABLE —
#: never MISSING, never ZERO. See the Wikimedia Analytics API reference:
#: "These endpoints serve data starting on July 1, 2015."
WIKIMEDIA_SERIES_START = date(2015, 7, 1)

#: Availability policy version for the daily pageview aggregate. Bump when the
#: availability semantics below change.
WIKIMEDIA_AVAILABILITY_POLICY_VERSION = "wikimedia_pageviews_availability_v1"


def wikimedia_available_at(observation_day: date) -> date:
    """Day-level publication bound for a Wikimedia daily pageview aggregate.

    Wikimedia loads a day's pageview data at the end of the relevant period:
    the aggregate for ``observation_day`` D becomes knowable from the source at
    D+1 (00:00 UTC). This is a conservative availability bound derived from the
    source's documented load semantics — it is NOT the retrieval time, and it
    is NOT the observation day itself.

    PIT admissibility for a window ending at cutoff therefore requires
    ``observation_day < cutoff AND wikimedia_available_at(observation_day) <
    cutoff``. ``retrieved_at`` (when Festival Bloomberg happened to download
    the value) is provenance and is NEVER an admissibility gate.
    """
    return observation_day + timedelta(days=1)


def artist_key_for(name: str) -> str:
    """Documented fallback artist_key: ``name::<normalized_name>``."""
    return f"name::{normalize_name(name)}"


def encode_title(title: str) -> str:
    normalized = (title or "").strip().replace(" ", "_")
    if not normalized:
        raise ValueError("article_title_empty")
    return urllib.parse.quote(normalized)


def build_pageviews_url(
    title: str,
    *,
    project: str = "en.wikipedia",
    access: str = "all-access",
    agent: str = "user",
    granularity: str = "daily",
    start: str,
    end: str,
    base_url: str = WIKIMEDIA_PAGEVIEWS_BASE,
) -> str:
    encoded = encode_title(title)
    parts = [base_url.rstrip("/"), project, access, agent, encoded, granularity, start, end]
    return "/".join(parts)


def pageviews_observation_key(
    *,
    artist_key: str,
    project: str,
    period_start: str | None,
    period_end: str | None,
) -> str:
    material = "|".join(
        [artist_key, SOURCE_SYSTEM, "pageviews", project, period_start or "", period_end or "", METRIC_VERSION]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _yyyymmdd_to_iso(raw: str) -> str | None:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def build_pageviews_observation(
    *,
    artist_name: str,
    title: str,
    project: str,
    access: str,
    agent: str,
    granularity: str,
    start: str,
    end: str,
    items: list[dict[str, Any]],
    status: str,
    error_code: str | None,
    error_message: str | None,
    source_url: str,
    retrieved_at: str,
    raw_response: Any = None,
) -> dict[str, Any]:
    artist_key = artist_key_for(artist_name)
    period_start = _yyyymmdd_to_iso(start)
    period_end = _yyyymmdd_to_iso(end)
    value_sum = None
    if status == "ok":
        value_sum = sum(int(i.get("views") or 0) for i in items)
    return {
        "observation_key": pageviews_observation_key(
            artist_key=artist_key, project=project,
            period_start=period_start, period_end=period_end,
        ),
        "artist_key": artist_key,
        "festival_key": None,
        "edition_key": None,
        "edition_year": None,
        "source_system": SOURCE_SYSTEM,
        "metric_kind": "pageviews",
        "project": project,
        "access_method": access,
        "agent": agent,
        "article_title": title.strip(),
        "granularity": granularity,
        "period_start": period_start,
        "period_end": period_end,
        "value": value_sum,
        "value_sum": value_sum,
        "value_unit": "pageviews",
        "status": status,
        "error_code": error_code,
        "error_message": error_message,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "raw_response_json": json.dumps(raw_response, default=str) if raw_response is not None else None,
        "provenance_json": json.dumps(
            {
                "source_system": SOURCE_SYSTEM,
                "endpoint": "per-article",
                "project": project,
                "access": access,
                "agent": agent,
                "granularity": granularity,
                "start": start,
                "end": end,
                "artist_name": artist_name,
                "article_title": title.strip(),
            },
            default=str,
        ),
        "metric_version": METRIC_VERSION,
    }


def persist_pageviews(conn, row: dict[str, Any]) -> int:
    """Insert one pageviews observation (idempotent by observation_key)."""
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


def fetch_pageviews(
    transport,
    *,
    title: str,
    start: str,
    end: str,
    project: str = "en.wikipedia",
    access: str = "all-access",
    agent: str = "user",
    granularity: str = "daily",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Fetch a per-article pageviews window and normalize it (no persistence)."""
    from ..acquisition.transport import TransportError

    url = build_pageviews_url(
        title, project=project, access=access, agent=agent,
        granularity=granularity, start=start, end=end,
    )
    retrieved_at = datetime.now(timezone.utc).isoformat()
    base = dict(
        project=project, access=access, agent=agent, granularity=granularity,
        start=start, end=end, source_url=url, retrieved_at=retrieved_at,
        items=[], raw_response=None,
    )
    try:
        response = transport.request("GET", url, headers={"Accept": "application/json"}, timeout_seconds=timeout_seconds)
    except TransportError as exc:
        return {**base, "status": "error", "error_code": "network", "error_message": str(exc)}

    if response.status == 404:
        return {**base, "status": "missing", "error_code": "pageviews_not_found",
                "error_message": "Article or date range not found", "raw_response": response.body.decode("utf-8", "replace")}
    if response.status != 200:
        return {**base, "status": "error", "error_code": f"http_{response.status}",
                "error_message": f"HTTP {response.status}", "raw_response": response.body.decode("utf-8", "replace")}
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {**base, "status": "error", "error_code": "response_parse_error",
                "error_message": "non-JSON response", "raw_response": response.body.decode("utf-8", "replace")}
    items = (payload or {}).get("items") or []
    if not items:
        return {**base, "status": "missing", "error_code": "pageviews_empty",
                "error_message": "No pageview items returned", "raw_response": payload}
    return {**base, "status": "ok", "items": items, "raw_response": payload}


def collect_artist_pageviews(
    conn,
    transport,
    *,
    names: list[str],
    days: int = 30,
    project: str = "en.wikipedia",
    access: str = "all-access",
    agent: str = "user",
    min_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    """Bounded pageviews collection over a list of artist names.

    ``names`` are raw display names; the article title defaults to the name
    (the same conservative fallback as the TypeScript scraper). A 404 title
    is persisted as ``missing``, never as a fabricated zero.
    """
    end_dt = datetime.now(timezone.utc).date()
    start_dt = end_dt - timedelta(days=max(1, days))
    start = start_dt.strftime("%Y%m%d")
    end = end_dt.strftime("%Y%m%d")
    summary = {
        "status": "RUNNING",
        "names_attempted": 0,
        "ok": 0,
        "missing": 0,
        "error": 0,
        "rows_persisted": 0,
        "window_start": start,
        "window_end": end,
    }
    for index, name in enumerate(names):
        if not name or not name.strip():
            continue
        if index and min_interval_seconds > 0:
            time.sleep(min_interval_seconds)
        summary["names_attempted"] += 1
        result = fetch_pageviews(
            transport, title=name, start=start, end=end,
            project=project, access=access, agent=agent,
        )
        row = build_pageviews_observation(
            artist_name=name,
            title=name,
            project=result["project"],
            access=result["access"],
            agent=result["agent"],
            granularity=result["granularity"],
            start=result["start"],
            end=result["end"],
            items=result["items"],
            status=result["status"],
            error_code=result.get("error_code"),
            error_message=result.get("error_message"),
            source_url=result["source_url"],
            retrieved_at=result["retrieved_at"],
            raw_response=result.get("raw_response"),
        )
        summary[result["status"]] += 1
        summary["rows_persisted"] += persist_pageviews(conn, row)
    summary["status"] = "COMPLETE"
    return summary
