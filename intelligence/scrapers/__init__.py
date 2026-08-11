"""Festival Bloomberg scraper ensemble.

A robust, key-less, ToS-friendly multi-source scraping + sentiment system
that fuses open-web signals into sellable per-artist insight:

  * what people are saying (Hacker News, GDELT news, RSS blogs, Wikipedia)
  * how they feel (VADER sentiment, topic/theme tags)
  * who the fans likely are (origin / era / genre demographic proxies)
  * how the artist fits a lineup (transparent genre+era+ sentiment heuristic)

No paid APIs, no authentication, no secrets required.
"""
from __future__ import annotations

from scrapers.contracts import (
    ArtistInsight,
    ScrapeResult,
    ScrapeStatus,
    SentimentBreakdown,
    SourceType,
)
from scrapers.sentiment import LLMSentimentSummarizer, SentimentAnalyzer
from scrapers.adapters import (
    scrape_gdelt,
    scrape_hackernews,
    scrape_musicbrainz,
    scrape_rss,
    scrape_wikidata,
    scrape_wikipedia,
)
from scrapers.ensemble import ScraperEnsemble

__all__ = [
    "ArtistInsight",
    "ScrapeResult",
    "ScrapeStatus",
    "SentimentBreakdown",
    "SourceType",
    "LLMSentimentSummarizer",
    "SentimentAnalyzer",
    "scrape_gdelt",
    "scrape_hackernews",
    "scrape_musicbrainz",
    "scrape_rss",
    "scrape_wikidata",
    "scrape_wikipedia",
    "ScraperEnsemble",
]
