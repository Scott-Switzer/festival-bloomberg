"""Ensemble orchestrator: fuse multiple scraper adapters into one insight.

The orchestrator runs every enabled adapter (key-less, ToS-friendly), collects
their :class:`ScrapeResult` payloads, and fuses them into a single sellable
:class:`ArtistInsight`: attention volume, VADER sentiment, topic tags,
audience/demographic *proxies*, and an optional lineup-fit score.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import requests

from scrapers.adapters import (
    scrape_gdelt,
    scrape_hackernews,
    scrape_musicbrainz,
    scrape_rss,
    scrape_wikidata,
    scrape_wikipedia,
)
from scrapers.contracts import (
    ArtistInsight,
    ScrapeResult,
    ScrapeStatus,
    SentimentBreakdown,
    SourceType,
)
from scrapers.sentiment import LLMSentimentSummarizer, SentimentAnalyzer

logger = logging.getLogger(__name__)


# Map an artist's active-since year to a coarse audience-age proxy.
def _era_proxy(begin_year: Optional[int]) -> Optional[str]:
    if not begin_year:
        return None
    if begin_year < 1980:
        return "boomer/older"
    if begin_year < 1995:
        return "gen-x"
    if begin_year < 2005:
        return "millennial"
    if begin_year < 2015:
        return "gen-z"
    return "gen-z / newest"


# Subreddit-style community affinity proxy (used as a lightweight signal of
# where an artist's audience congregates online). Tuned from genre keywords.
_COMMUNITY_MAP = {
    "hip hop": "r/hiphopheads",
    "rap": "r/hiphopheads",
    "pop": "r/popheads",
    "indie": "r/indieheads",
    "rock": "r/rock",
    "metal": "r/metal",
    "electronic": "r/electronicmusic",
    "edm": "r/EDM",
    "country": "r/country",
    "folk": "r/folk",
    "jazz": "r/jazz",
    "r&b": "r/rnb",
    "k-pop": "r/kpop",
}


class ScraperEnsemble:
    """Runs the scraper ensemble and fuses results into artist insight."""

    def __init__(self, use_llm: bool = True) -> None:
        self.sentiment = SentimentAnalyzer()
        self.llm = LLMSentimentSummarizer() if use_llm else None

    # -- public API --------------------------------------------------------- #
    def analyze_artist(self, artist: str, session: Optional[requests.Session] = None,
                       enable: Optional[List[SourceType]] = None) -> ArtistInsight:
        session = session or requests.Session()
        enable = enable or list(SourceType)

        # Order matters: metadata sources first, then text/sentiment sources.
        runners = {
            SourceType.WIKIPEDIA: lambda: scrape_wikipedia(session, artist),
            SourceType.MUSICBRAINZ: lambda: scrape_musicbrainz(session, artist),
            SourceType.WIKIDATA: lambda: scrape_wikidata(session, artist),
            SourceType.HACKERNEWS: lambda: scrape_hackernews(session, artist),
            SourceType.GDELT: lambda: scrape_gdelt(session, artist),
            SourceType.RSS: lambda: scrape_rss(session, artist),
        }

        results: Dict[SourceType, ScrapeResult] = {}
        for src, run in runners.items():
            if src not in enable:
                continue
            try:
                res = run()
                results[src] = res
                logger.info("source=%s status=%s texts=%d", src.value, res.status.value, len(res.texts))
            except Exception as e:  # a single source must never break the ensemble
                logger.warning("source=%s crashed: %s", src.value, e)
                results[src] = ScrapeResult(src, ScrapeStatus.FAILED, artist, error=str(e))

        return self._fuse(artist, results)

    # -- fusion -------------------------------------------------------------- #
    def _fuse(self, artist: str, results: Dict[SourceType, ScrapeResult]) -> ArtistInsight:
        all_texts: List[str] = []
        sources_used: List[str] = []
        insight = ArtistInsight(artist_name=artist)

        # Metadata fusion (MusicBrainz / Wikidata / Wikipedia)
        mb = results.get(SourceType.MUSICBRAINZ)
        wd = results.get(SourceType.WIKIDATA)
        wp = results.get(SourceType.WIKIPEDIA)
        if mb and mb.status != ScrapeStatus.FAILED:
            sources_used.append("musicbrainz")
            insight.musicbrainz_id = mb.metadata.get("musicbrainz_id")
            insight.origin_country = mb.metadata.get("country")
            if mb.metadata.get("begin_year"):
                insight.active_since = mb.metadata["begin_year"]
            insight.genres = list(mb.metadata.get("tags", []))
        if wd and wd.status != ScrapeStatus.FAILED:
            sources_used.append("wikidata")
            if not insight.origin_country:
                insight.origin_country = wd.metadata.get("country")
            if not insight.active_since and wd.metadata.get("inception_year"):
                try:
                    insight.active_since = int(wd.metadata["inception_year"])
                except (TypeError, ValueError):
                    pass
            if wd.metadata.get("genre") and wd.metadata["genre"] not in insight.genres:
                insight.genres.append(wd.metadata["genre"])
        if wp and wp.status != ScrapeStatus.FAILED:
            sources_used.append("wikipedia")
            insight.attention_score = min(100.0, (wp.metrics.get("pageviews_30d", 0.0) or 0) / 5000.0)

        insight.era = _era_proxy(insight.active_since)
        insight.audience_age_proxy = insight.era

        # Community affinity proxy from genres
        for g in insight.genres:
            gl = g.lower()
            for key, sub in _COMMUNITY_MAP.items():
                if key in gl and sub not in insight.platform_affinity:
                    insight.platform_affinity[sub] = 1.0

        # Text fusion (HN, GDELT, RSS, Wikipedia extract)
        for src in (SourceType.HACKERNEWS, SourceType.GDELT, SourceType.RSS, SourceType.WIKIPEDIA):
            r = results.get(src)
            if r and r.status != ScrapeStatus.FAILED:
                all_texts.extend(r.texts)
                insight.mention_volume += r.mentions
                if src.value not in sources_used:
                    sources_used.append(src.value)

        insight.raw_texts = all_texts[:200]
        insight.sources_used = sources_used

        # Sentiment
        if all_texts and self.sentiment.available:
            sb = self.sentiment.analyze(all_texts)
            insight.sentiment = sb
            insight.sentiment_label = self.sentiment.label(sb.compound, sb.negative)
        else:
            insight.sentiment = SentimentBreakdown()
            insight.sentiment_label = "unknown"

        # Topics
        insight.top_topics = self.sentiment.extract_topics(all_texts)

        # Normalize attention score if we still have none but have mentions
        if insight.attention_score == 0.0 and insight.mention_volume:
            insight.attention_score = min(100.0, float(insight.mention_volume) * 2.0)

        # Optional LLM prose
        if self.llm and self.llm.available and all_texts:
            try:
                insight.llm_summary = self.llm.summarize(artist, all_texts)
            except Exception as e:  # pragma: no cover
                logger.warning("LLM summary failed: %s", e)

        return insight

    # -- lineup fit ---------------------------------------------------------- #
    def lineup_fit(self, insight: ArtistInsight, festival_genres: List[str],
                   festival_era_mix: Optional[List[Optional[str]]] = None) -> ArtistInsight:
        """Score how well an artist fits a festival's existing genre/era mix.

        This is a transparent, explainable heuristic (no black box): it rewards
        genre overlap and era compatibility, and penalizes nothing arbitrarily.
        """
        score = 50.0  # neutral baseline
        rationale: List[str] = []
        artist_genres = [g.lower() for g in insight.genres]
        fest = [g.lower() for g in festival_genres]

        overlap = set()
        for ag in artist_genres:
            for fg in fest:
                if ag in fg or fg in ag:
                    overlap.add(ag)
        if overlap:
            score += min(30.0, 10.0 * len(overlap))
            rationale.append(f"Genre overlap: {', '.join(sorted(overlap))}")
        else:
            score -= 10.0
            rationale.append("No direct genre overlap with current lineup")

        # Era compatibility
        if insight.era and festival_era_mix:
            if insight.era in festival_era_mix or None in festival_era_mix:
                score += 10.0
                rationale.append(f"Era ({insight.era}) aligns with festival history")
            else:
                score -= 5.0
                rationale.append(f"Era ({insight.era}) differs from festival norm")

        # Sentiment tailwind
        if insight.sentiment.compound >= 0.35:
            score += 10.0
            rationale.append("Strong positive public sentiment")
        elif insight.sentiment.compound <= -0.35:
            score -= 10.0
            rationale.append("Negative public sentiment — reputational risk")

        insight.lineup_fit_score = max(0.0, min(100.0, round(score, 1)))
        insight.lineup_fit_rationale = rationale
        return insight
