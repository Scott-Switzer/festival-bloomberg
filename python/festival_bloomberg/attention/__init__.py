"""Attention time-series acquisition (Wikimedia pageviews first)."""

from .wikimedia_pageviews import (  # noqa: F401
    WIKIMEDIA_PAGEVIEWS_BASE,
    build_pageviews_url,
    build_pageviews_observation,
    collect_artist_pageviews,
    pageviews_observation_key,
)
