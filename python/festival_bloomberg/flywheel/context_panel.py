"""CONTEXT_PANEL — attention, market and weather series with vintages.

Attention is a time-series panel, not one current score. This module fetches
daily Wikimedia pageviews (key-free, CC-licensed, historical back to 2015) and
derives the velocity/shock statistics that a promoter could actually have
computed at a decision cutoff. Market/weather adapters (Census, BLS, BEA,
NOAA/ERA5) are declared here as *scaffolding*: their URLs and vintage
semantics are fixed, but they require keys and are reported
KEY_REQUIRED / REGISTRATION_REQUIRED until configured — never scraped.

Two kinds of weather must never be mixed:
    actual weather (explains the final outcome)
    forecast weather known at cutoff (usable prospectively)
``vintage`` and ``knowledge_time`` on every row keep them distinct.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.transport import TransportError, UrllibTransport

PAGEVIEWS_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
DEFAULT_PROJECT = "en.wikipedia"
DEFAULT_ACCESS = "all-access"
DEFAULT_AGENT = "user"

#: Wikimedia pageviews are available from 2015-07-01 onward.
PAGEVIEWS_FLOOR = date(2015, 7, 1)


def pageviews_url(
    article: str,
    start: date,
    end: date,
    *,
    project: str = DEFAULT_PROJECT,
    access: str = DEFAULT_ACCESS,
    agent: str = DEFAULT_AGENT,
    granularity: str = "daily",
) -> str:
    """Wikimedia REST pageview URL for one article over [start, end]."""
    slug = article.strip().replace(" ", "_")
    fmt = lambda d: d.strftime("%Y%m%d")  # noqa: E731
    return (
        f"{PAGEVIEWS_BASE}/{project}/{access}/{agent}/{slug}/"
        f"{granularity}/{fmt(start)}/{fmt(end)}"
    )


def parse_pageviews_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the ``items`` list into [{observed_date, views}, ...]."""
    items = (payload or {}).get("items") or []
    parsed: list[dict[str, Any]] = []
    for item in items:
        raw = item.get("timestamp")
        views = item.get("views")
        if not raw or views is None:
            continue
        try:
            observed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except (ValueError, IndexError):
            continue
        parsed.append({"observed_date": observed, "views": int(views)})
    parsed.sort(key=lambda row: row["observed_date"])
    return parsed


def collect_artist_pageviews(
    transport: UrllibTransport,
    article: str,
    start: date,
    end: date,
    *,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Fetch a daily pageview series for one article (key-free)."""
    url = pageviews_url(article, start, end)
    try:
        response = transport.request("GET", url, timeout_seconds=timeout_seconds)
    except TransportError as exc:
        raise PageviewsError(f"network failure: {exc}") from None
    if response.status == 429:
        raise PageviewsRateLimited("Wikimedia rate limit (429); retry later")
    if response.status != 200:
        raise PageviewsError(f"Wikimedia http {response.status}")
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise PageviewsError("response not JSON") from exc
    return parse_pageviews_items(payload if isinstance(payload, dict) else {})


# ---------------------------------------------------------------------------
# Attention derivation (pure, deterministic)
# ---------------------------------------------------------------------------
def _window_sum(daily: dict[date, int], as_of: date, days: int) -> tuple[float, int]:
    """Sum of views over the trailing ``days`` window and count of populated days."""
    total = 0.0
    count = 0
    start = as_of - timedelta(days=days - 1)
    for offset in range(days):
        key = start + timedelta(days=offset)
        value = daily.get(key)
        if value is not None:
            total += float(value)
            count += 1
    return total, count


def derive_attention_stats(
    daily: dict[date, int], as_of: date | None = None
) -> dict[str, Any]:
    """Velocity / shock statistics from a daily pageview series.

    Returns None-valued fields (never fabricated zeros) when the window lacks
    data. ``as_of`` defaults to the last observed date.
    """
    if not daily:
        return {key: None for key in _STAT_KEYS}
    anchor = as_of or max(daily)
    stats: dict[str, Any] = {"as_of": anchor.isoformat()}

    total = sum(float(v) for v in daily.values() if v is not None)
    stats["total_views"] = total
    stats["observed_days"] = len(daily)

    def avg(days: int) -> float | None:
        summed, count = _window_sum(daily, anchor, days)
        return summed / count if count else None

    stats["views_7d"] = _window_sum(daily, anchor, 7)[0]
    stats["views_30d"] = _window_sum(daily, anchor, 30)[0]
    stats["views_90d"] = _window_sum(daily, anchor, 90)[0]
    stats["velocity_7d"] = avg(7)
    stats["velocity_30d"] = avg(30)
    stats["velocity_90d"] = avg(90)

    v7 = stats["velocity_7d"]
    v30 = stats["velocity_30d"]
    stats["acceleration"] = (
        (v7 - v30) if (v7 is not None and v30 is not None) else None
    )

    values = [float(v) for v in daily.values() if v is not None]
    if len(values) >= 2:
        mean = statistics.fmean(values)
        stdev = statistics.pstdev(values)
        stats["volatility_cv"] = stdev / mean if mean else None
        if stdev and v7 is not None:
            stats["zscore_7d"] = (v7 - mean) / stdev
        else:
            stats["zscore_7d"] = None
    else:
        stats["volatility_cv"] = None
        stats["zscore_7d"] = None

    # Year-over-year: same 30-day window one year earlier.
    year_ago = anchor - timedelta(days=365)
    year_ago_sum, year_ago_count = _window_sum(daily, year_ago, 30)
    current_30, current_count = _window_sum(daily, anchor, 30)
    if year_ago_count and current_count:
        prior_avg = year_ago_sum / year_ago_count
        current_avg = current_30 / current_count
        if prior_avg > 0:
            stats["yoy_change"] = (current_avg - prior_avg) / prior_avg
        else:
            stats["yoy_change"] = None
    else:
        stats["yoy_change"] = None

    return stats


_STAT_KEYS = (
    "as_of",
    "total_views",
    "observed_days",
    "views_7d",
    "views_30d",
    "views_90d",
    "velocity_7d",
    "velocity_30d",
    "velocity_90d",
    "acceleration",
    "volatility_cv",
    "zscore_7d",
    "yoy_change",
)


def build_pageview_series_rows(
    *,
    entity_name: str,
    series: list[dict[str, Any]],
    provider: str = "wikimedia",
    retrieved_at: datetime | None = None,
    knowledge_time: datetime | None = None,
    source_url: str | None = None,
    parser_version: str = "wikimedia_pageviews_v1",
    software_version: str = "data_flywheel_and_coverage_v1",
    rights_status: str = "OPEN_WITH_ATTRIBUTION",
    commercial_use_status: str = "OPEN_WITH_ATTRIBUTION",
    license: str = "CC-BY-SA / CC0 (Wikimedia pageview aggregate data)",
) -> list[dict[str, Any]]:
    """Build ``flywheel.context_panel_series`` rows from a pageview series."""
    now = retrieved_at or utc_now()
    knowledge = knowledge_time or now
    rows: list[dict[str, Any]] = []
    for point in series:
        observed = point["observed_date"]
        views = point["views"]
        row_id = f"ctx_{content_hash_of({
            'entity': entity_name,
            'type': 'ATTENTION_PAGEVIEWS',
            'date': observed.isoformat(),
            'value': views,
            'retrieved': now.isoformat(),
        })[:20]}"
        rows.append(
            {
                "series_id": row_id,
                "entity_type": "ARTIST",
                "entity_key": f"name::{entity_name.lower()}",
                "entity_name": entity_name,
                "series_type": "ATTENTION_PAGEVIEWS",
                "provider": provider,
                "observed_date": observed.isoformat(),
                "value": float(views),
                "unit": "views/day",
                "metric_name": "daily_pageviews",
                "vintage": "daily",
                "source_publication_time": None,
                "source_as_of": observed.isoformat(),
                "retrieved_at": now.isoformat(),
                "knowledge_time": knowledge.isoformat(),
                "source_url": source_url,
                "raw_payload_hash": content_hash_of(
                    {"article": entity_name, "date": observed.isoformat(), "views": views}
                ),
                "license": license,
                "rights_status": rights_status,
                "commercial_use_status": commercial_use_status,
                "parser_version": parser_version,
                "software_version": software_version,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Market/weather vintage semantics (adapter scaffolding, no scraping)
# ---------------------------------------------------------------------------
def census_acs_5year_label(end_year: int) -> str:
    return f"ACS 5-Year {end_year - 4}-{end_year}"


def census_acs_publication_time(end_year: int) -> date:
    """Approximate ACS 5-year release date (mid-year after the end year).

    Documented as an estimate so a booking model never consumes a revised
    statistic as if it were contemporaneous.
    """
    return date(end_year + 1, 7, 1)


def census_api_url(
    *, series: str, vintage: int, key: str, get: str = "NAME,POP_ESTIMATE", for_geo: str = "us:1"
) -> str:
    """Census Data API URL builder (ACS / population estimates)."""
    if not key:
        raise ValueError("Census Data API requires an API key (register at census.gov)")
    from urllib.parse import urlencode

    params = urlencode({"get": get, "for": for_geo, "key": key})
    return f"https://api.census.gov/data/{vintage}/{series}?{params}"


def bls_series_url(*, series_id: str, start_year: int, end_year: int, key: str) -> str:
    if not key:
        raise ValueError("BLS public data API requires a registration key")
    return "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def noaa_cdo_url(*, dataset: str, station: str, start: str, end: str, token: str) -> str:
    if not token:
        raise ValueError("NOAA CDO requires a token (register at ncei.noaa.gov)")
    from urllib.parse import urlencode

    params = urlencode(
        {
            "datasetid": dataset,
            "stationid": station,
            "startdate": start,
            "enddate": end,
            "limit": "1000",
            "token": token,
        }
    )
    return f"https://www.ncei.noaa.gov/cdo-web/api/v2/data?{params}"


class PageviewsError(RuntimeError):
    pass


class PageviewsRateLimited(PageviewsError):
    pass
