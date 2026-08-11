#!/usr/bin/env python
"""
Festival Bloomberg — real, no-key ingestion pipeline.

Pulls REAL data from two free, key-less sources and writes it into the
DuckDB warehouse through ``warehouse.repository.FestivalRepository``:

  1. MusicBrainz  (https://musicbrainz.org/ws/2/)  — canonical artist identity,
     country, lifespan, genre tags. No API key; a descriptive User-Agent is
     REQUIRED by MusicBrainz policy.
  2. Wikipedia pageviews (Wikimedia REST) — attention/momentum signal per artist.
     No key; anonymous access with polite rate limiting.

No paid APIs, no credentials. Designed to be idempotent (upserts) and to
degrade gracefully if the network is unavailable.

Usage:
    python scripts/ingest_real_data.py                 # default seed set
    python scripts/ingest_real_data.py --limit 20      # cap artists ingested
    python scripts/ingest_real_data.py --dry-run       # no writes
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import requests

# Allow running as a script or as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warehouse.repository import FestivalRepository, DEFAULT_DB_PATH  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ingest_real_data")

# Polite, identifying User-Agent (MusicBrainz policy requirement).
USER_AGENT = "FestivalIntelligenceTerminal/1.0 (student portfolio project; contact: dev@example.com)"
MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2"
WIKI_PAGEVIEWS_BASE = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews"
    "/per-article/en.wikipedia/all-access/user"
)

# --------------------------------------------------------------------------- #
# Seed data — real, well-known artists and festivals (free, public knowledge)
# --------------------------------------------------------------------------- #
SEED_ARTISTS = [
    "Radiohead", "Kendrick Lamar", "Billie Eilish", "The Weeknd",
    "Tame Impala", "Tyler, The Creator", "Glass Animals", "Dua Lipa",
    "Arctic Monkeys", "Phoebe Bridgers", "Fred again..", "ODESZA",
    "Florence + The Machine", "The Strokes", "Lorde", "Bad Bunny",
    "Rosalia", "Grove", "Khruangbin", "Bon Iver",
    "Disclosure", "Jamie xx", "Charli XCX", "Mitski",
    "Twenty One Pilots", "Hozier", "Lana Del Rey", "Anderson .Paak",
]

SEED_FESTIVALS = [
    {"name": "Lollapalooza", "normalized_name": "lollapalooza",
     "location_city": "Chicago", "location_country": "US", "capacity": 400000,
     "genre_focus": ["rock", "hip-hop", "pop"], "festival_type": "music",
     "venue_type": "outdoor", "duration_days": 4, "typical_month": 8},
    {"name": "Coachella", "normalized_name": "coachella",
     "location_city": "Indio", "location_country": "US", "capacity": 250000,
     "genre_focus": ["rock", "electronic", "hip-hop"], "festival_type": "music",
     "venue_type": "outdoor", "duration_days": 6, "typical_month": 4},
    {"name": "Bonnaroo", "normalized_name": "bonnaroo",
     "location_city": "Manchester", "location_country": "US", "capacity": 90000,
     "genre_focus": ["rock", "electronic"], "festival_type": "music",
     "venue_type": "outdoor", "duration_days": 4, "typical_month": 6},
    {"name": "Outside Lands", "normalized_name": "outside_lands",
     "location_city": "San Francisco", "location_country": "US", "capacity": 220000,
     "genre_focus": ["rock", "hip-hop", "electronic"], "festival_type": "music",
     "venue_type": "outdoor", "duration_days": 3, "typical_month": 8},
    {"name": "Austin City Limits", "normalized_name": "austin_city_limits",
     "location_city": "Austin", "location_country": "US", "capacity": 450000,
     "genre_focus": ["rock", "country", "electronic"], "festival_type": "music",
     "venue_type": "outdoor", "duration_days": 6, "typical_month": 10},
]


# --------------------------------------------------------------------------- #
# MusicBrainz helpers
# --------------------------------------------------------------------------- #
def _mb_get(url: str, params: Dict[str, str], session: requests.Session,
           retries: int = 4) -> Optional[dict]:
    """GET with polite rate-limiting and retry on transient errors.

    Forces ``Connection: close`` to avoid stale pooled TLS connections that
    MusicBrainz occasionally resets (SSL EOF), and retries on 429/5xx and
    network errors with exponential backoff.
    """
    backoff = 2.0
    headers = {"User-Agent": USER_AGENT, "Connection": "close"}
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=20)
            if resp.status_code in (429, 503, 502, 500):
                logger.warning("MusicBrainz %s (attempt %d/%d); backing off %ss",
                               resp.status_code, attempt + 1, retries, backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            time.sleep(1.0)  # MusicBrainz rate limit: 1 req/sec
            return resp.json()
        except requests.RequestException as e:
            if attempt < retries - 1:
                logger.warning("MusicBrainz request error (%s); retrying in %ss", e, backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            logger.warning("MusicBrainz request failed after %d attempts: %s", retries, e)
            return None
    return None


def resolve_musicbrainz_artist(name: str, session: requests.Session) -> Optional[Dict[str, str]]:
    """Resolve an artist name to a MusicBrainz ID and core metadata."""
    data = _mb_get(
        f"{MUSICBRAINZ_BASE}/artist/",
        {"query": f'artist:"{name}"', "fmt": "json", "limit": 5},
        session,
    )
    if not data or not data.get("artists"):
        return None

    best = data["artists"][0]
    mbid = best.get("id")
    if not mbid:
        return None

    # Enrich with full record (tags -> genres, lifespan, country).
    full = _mb_get(
        f"{MUSICBRAINZ_BASE}/artist/{mbid}",
        {"fmt": "json", "inc": "aliases+tags+ratings"},
        session,
    )
    if full:
        best = full

    genres = [t["name"] for t in best.get("tags", [])][:10]
    life = best.get("life-span", {}) or {}
    return {
        "musicbrainz_id": mbid,
        "name": best.get("name", name),
        "normalized_name": best.get("name", name).lower().strip(),
        "disambiguation": best.get("disambiguation"),
        "country": best.get("country"),
        "genres": genres,
        "type": best.get("type"),
        "life_span_begin": life.get("begin"),
        "life_span_end": life.get("end"),
    }


# --------------------------------------------------------------------------- #
# Wikipedia pageviews (momentum signal)
# --------------------------------------------------------------------------- #
def fetch_wikipedia_pageviews(artist_name: str, session: requests.Session,
                              days: int = 30) -> Optional[int]:
    """Total Wikipedia pageviews for the artist over the last ``days`` days."""
    # Normalize article title: replace spaces with underscores; strip disambiguation.
    title = artist_name.replace(" ", "_")
    end = date.today()
    # Pageviews API requires YYYYMMDD granularity; use full recent days.
    start = end - timedelta(days=days)
    url = f"{WIKI_PAGEVIEWS_BASE}/{title}/daily/{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    try:
        resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        items = resp.json().get("items", [])
        total = sum(int(i.get("views", 0)) for i in items)
        time.sleep(0.1)  # be polite
        return total
    except requests.RequestException as e:
        logger.debug("Wikipedia pageviews failed for %s: %s", artist_name, e)
        return None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def ingest_artists(repo: Optional[FestivalRepository], names: List[str], dry_run: bool = False) -> int:
    session = requests.Session()
    ingested = 0
    if dry_run:
        for name in names:
            logger.info("[dry-run] would resolve + upsert artist: %s", name)
            ingested += 1
        return ingested
    assert repo is not None, "repo required when not dry_run"
    for name in names:
        logger.info("Resolving artist: %s", name)
        meta = resolve_musicbrainz_artist(name, session)
        if not meta:
            logger.warning("  -> no MusicBrainz match for %s; recording name-only stub", name)
            meta = {
                "name": name,
                "normalized_name": name.lower().strip(),
                "musicbrainz_id": None,
                "genres": [],
                "country": None,
                "type": None,
            }
        if dry_run:
            logger.info("  [dry-run] would upsert: %s (%s)", meta["name"], meta.get("musicbrainz_id"))
            ingested += 1
            continue

        key = repo.upsert_artist(meta)
        # Wikipedia pageviews as a momentum signal.
        views = fetch_wikipedia_pageviews(meta["name"], session)
        if views is not None:
            repo.insert_artist_metric(
                key, "wikipedia", "pageviews_30d", float(views),
                observed_date=date.today(),
                meta_data={"window_days": 30},
            )
            logger.info("  -> upserted %s | wiki views(30d)=%s", meta["name"], f"{views:,}")
        else:
            logger.info("  -> upserted %s (no wiki data)", meta["name"])
        ingested += 1
    return ingested


def ingest_festivals(repo: Optional[FestivalRepository], dry_run: bool = False) -> int:
    ingested = 0
    if dry_run:
        for f in SEED_FESTIVALS:
            logger.info("[dry-run] would upsert festival: %s", f["name"])
            ingested += 1
        return ingested
    assert repo is not None, "repo required when not dry_run"
    for f in SEED_FESTIVALS:
        repo.upsert_festival(f)
        logger.info("Upserted festival: %s", f["name"])
        ingested += 1
    return ingested


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest real festival/artist data (no keys).")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="DuckDB warehouse path")
    parser.add_argument("--limit", type=int, default=0, help="Cap number of artists ingested")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to the warehouse")
    parser.add_argument("--no-recover", action="store_true",
                        help="Skip the MusicBrainz recovery pass for unresolved artists")
    parser.add_argument("--artists", nargs="*", default=None, help="Override artist seed list")
    args = parser.parse_args()

    names = args.artists if args.artists else SEED_ARTISTS
    if args.limit:
        names = names[: args.limit]

    logger.info("Starting ingestion: %d artists; %d festivals (dry_run=%s)",
                len(names), len(SEED_FESTIVALS), args.dry_run)

    if args.dry_run:
        repo = None
    else:
        repo = FestivalRepository(args.db_path)

    try:
        n_fest = ingest_festivals(repo, args.dry_run) if repo else ingest_festivals(None, True)
        n_art = ingest_artists(repo, names, args.dry_run) if repo else ingest_artists(None, names, True)

        # Recovery pass: re-attempt MusicBrainz resolution for any artist that
        # landed without an MBID (best-effort, mitigates transient upstream TLS
        # resets). Only runs on a real repo with data already written.
        if repo and not args.dry_run and not args.no_recover:
            unresolved = repo.conn.execute(
                "SELECT name FROM core.artists WHERE musicbrainz_id IS NULL"
            ).fetchall()
            unresolved = [r[0] for r in unresolved]
            if unresolved:
                logger.info("Recovery pass: retrying %d unresolved artists after cooldown",
                            len(unresolved))
                time.sleep(5.0)  # brief cooldown so MusicBrainz TLS pool recovers
                recovered = ingest_artists(repo, unresolved)
                logger.info("Recovery pass complete: %d re-processed", recovered)
    finally:
        if repo:
            repo.close()

    logger.info("Ingestion complete: %d festivals, %d artists written.", n_fest, n_art)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
