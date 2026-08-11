"""Data contracts for the Festival Bloomberg scraper ensemble.

These are the shared, typed shapes that every scraper adapter returns and that
the ensemble orchestrator fuses into a sellable per-artist insight.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime, date


class SourceType(str, Enum):
    WIKIPEDIA = "wikipedia"
    MUSICBRAINZ = "musicbrainz"
    WIKIDATA = "wikidata"
    HACKERNEWS = "hackernews"
    GDELT = "gdelt"
    RSS = "rss"
    LASTFM = "lastfm"          # optional (free key)
    DISCOGS = "discogs"        # optional (free key)


class ScrapeStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    SKIPPED = "skipped"


@dataclass
class ScrapeResult:
    """Normalized result from a single scraper adapter."""
    source: SourceType
    status: ScrapeStatus
    artist_name: str
    # Free-text "what people are saying" snippets (posts, headlines, comments)
    texts: List[str] = field(default_factory=list)
    # Structured metadata harvested for this artist
    metadata: Dict[str, object] = field(default_factory=dict)
    # Raw counts / signals
    metrics: Dict[str, float] = field(default_factory=dict)
    # Engineered signals specific to this source
    mentions: int = 0
    error: Optional[str] = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SentimentBreakdown:
    """Aggregated sentiment across all collected texts for an artist."""
    positive: float = 0.0
    neutral: float = 0.0
    negative: float = 0.0
    compound: float = 0.0          # VADER compound mean
    sample_size: int = 0           # number of texts scored
    top_positive: List[str] = field(default_factory=list)
    top_negative: List[str] = field(default_factory=list)


@dataclass
class ArtistInsight:
    """Fused, sellable insight for a single artist.

    This is the product: what people say, how they feel, who the fans likely
    are, and how the artist fits a given lineup.
    """
    artist_name: str
    artist_key: Optional[str] = None
    musicbrainz_id: Optional[str] = None

    # --- Volume / attention ------------------------------------------------- #
    mention_volume: int = 0                 # total texts/mentions across sources
    attention_score: float = 0.0            # normalized 0-100 attention index

    # --- Sentiment ---------------------------------------------------------- #
    sentiment: SentimentBreakdown = field(default_factory=SentimentBreakdown)
    sentiment_label: str = "unknown"        # positive | neutral | negative | mixed

    # --- Topics / themes ---------------------------------------------------- #
    top_topics: List[str] = field(default_factory=list)
    raw_texts: List[str] = field(default_factory=list)

    # --- Demographic / audience proxies ------------------------------------- #
    # These are PROXIES derived from open metadata, never claimed as ground truth.
    origin_country: Optional[str] = None
    active_since: Optional[int] = None
    era: Optional[str] = None               # e.g. "1990s", "2010s"
    genres: List[str] = field(default_factory=list)
    audience_age_proxy: Optional[str] = None  # e.g. "millennial", "gen-z", "mixed"
    platform_affinity: Dict[str, float] = field(default_factory=dict)  # subreddit/feed affinity

    # --- Lineup fit --------------------------------------------------------- #
    lineup_fit_score: Optional[float] = None   # 0-100 vs a target festival
    lineup_fit_rationale: List[str] = field(default_factory=list)

    # --- Provenance --------------------------------------------------------- #
    sources_used: List[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)
    llm_summary: Optional[str] = None        # optional NVIDIA-generated prose
