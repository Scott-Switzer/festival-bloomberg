#!/usr/bin/env python
"""
Festival Bloomberg — scraper ensemble sentiment ingestion.

Runs the key-less, ToS-friendly scraper ensemble (Wikipedia, MusicBrainz,
Wikidata, Hacker News, GDELT, RSS) for every artist in the warehouse, scores
sentiment with VADER, and persists per-artist insight into:
    metrics.artist_sentiment
    metrics.social_signals

Usage:
    python scripts/ingest_sentiment.py                 # all artists
    python scripts/ingest_sentiment.py --limit 5       # first N
    python scripts/ingest_sentiment.py --artist "Radiohead"
    python scripts/ingest_sentiment.py --no-llm       # skip optional LLM prose
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import List

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ingest_sentiment")

# Ensure project root on path when run as a script.
sys.path.insert(0, ".")

from scrapers.ensemble import ScraperEnsemble  # noqa: E402
from warehouse.repository import FestivalRepository, DEFAULT_DB_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensemble sentiment ingestion (no keys).")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=0, help="Cap number of artists")
    parser.add_argument("--artist", default=None, help="Process a single artist by name")
    parser.add_argument("--no-llm", action="store_true", help="Skip optional LLM summary")
    args = parser.parse_args()

    repo = FestivalRepository(args.db_path)
    ensemble = ScraperEnsemble(use_llm=not args.no_llm)
    session = requests.Session()

    try:
        if args.artist:
            artists = [{"name": args.artist, "artist_key": f"name::{args.artist.lower()}"}]
        else:
            rows = repo.conn.execute(
                "SELECT artist_key, name FROM core.artists ORDER BY name"
            ).fetchall()
            artists = [{"artist_key": r[0], "name": r[1]} for r in rows]

        if args.limit:
            artists = artists[: args.limit]

        logger.info("Ensemble sentiment ingestion: %d artists", len(artists))
        processed = 0
        for art in artists:
            name = art["name"]
            key = art["artist_key"]
            try:
                insight = ensemble.analyze_artist(name, session)
                repo.upsert_sentiment(key, insight)
                # Per-source social signals
                for src in insight.sources_used:
                    # approximate per-source mention counts from raw texts is not
                    # granular here; record the dominant signal counts instead.
                    pass
                # Record social signal rows from each scrape source's metrics.
                # (We re-derive lightweight signals from the insight for storage.)
                repo.insert_social_signal(
                    key, "ensemble", mention_count=insight.mention_volume,
                    points=insight.attention_score, comments=0.0,
                    pageviews_30d=0.0, news_mentions=0.0,
                )
                processed += 1
                logger.info(
                    "  %s -> sentiment=%s compound=%.3f mentions=%d topics=%s",
                    name, insight.sentiment_label, insight.sentiment.compound,
                    insight.mention_volume, insight.top_topics,
                )
            except Exception as e:  # one artist must not abort the batch
                logger.warning("Failed artist %s: %s", name, e)

        logger.info("Ensemble ingestion complete: %d/%d artists processed.",
                     processed, len(artists))
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
