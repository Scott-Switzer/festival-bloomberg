"""Key-less scraper adapters for the Festival Bloomberg ensemble.

Every adapter here works WITHOUT API keys and respects each source's rate
limits / robots. They return a normalized :class:`ScrapeResult` so the
orchestrator can fuse them uniformly.

Sources:
  * Wikipedia  - article summary + 30-day pageviews (attention/momentum)
  * MusicBrainz - artist metadata, genres, origin, lifespan
  * Wikidata   - structured claims (country, inception, members, genre)
  * HackerNews - real public discussions (points/comments) -> "what people say"
  * GDELT      - global news mentions (retry/backoff on 429)
  * RSS        - music/news blog feeds (Pitchfork et al.)

Reddit and Discogs are intentionally NOT used: both now block key-less access
(403 / require auth tokens), so they would produce empty results and break the
"robust, zero-maintenance" guarantee.
"""
from __future__ import annotations

import logging
import time
import re
from typing import Dict, List, Optional

import requests

from scrapers.contracts import ScrapeResult, ScrapeStatus, SourceType

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "festival-intelligence-research/1.0 (academic; contact: research@example.com)"
}


def _get(session: requests.Session, url: str, params: Optional[Dict] = None,
         retries: int = 3, backoff: float = 2.0, timeout: int = 20) -> Optional[requests.Response]:
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, headers=_HEADERS, timeout=timeout)
            if resp.status_code in (429, 503, 502):
                wait = backoff * (2 ** attempt)
                logger.warning("Source %s rate-limited (%s); backoff %ss", url, resp.status_code, wait)
                time.sleep(wait)
                continue
            if resp.status_code == 200:
                return resp
            logger.warning("Source %s returned %s", url, resp.status_code)
            return None
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            logger.warning("Request failed for %s: %s", url, e)
            return None
    return None


# --------------------------------------------------------------------------- #
# Wikipedia
# --------------------------------------------------------------------------- #
def scrape_wikipedia(session: requests.Session, artist: str, pageviews_days: int = 30) -> ScrapeResult:
    base = "https://en.wikipedia.org/api/rest_v1/page/summary/"
    resp = _get(session, base + requests.utils.quote(artist))
    texts: List[str] = []
    metadata: Dict = {}
    if resp is None:
        return ScrapeResult(SourceType.WIKIPEDIA, ScrapeStatus.FAILED, artist, error="no response")
    data = resp.json()
    extract = data.get("extract")
    if extract:
        texts.append(extract)
        metadata["description"] = data.get("description")
        metadata["extract"] = extract

    # 30-day pageviews (article attention signal)
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=pageviews_days)
    pv_url = (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/all-agents/{requests.utils.quote(artist.replace(' ', '_'))}/daily/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    pv = _get(session, pv_url)
    views = 0
    if pv and pv.ok:
        items = pv.json().get("items", [])
        views = sum(int(i.get("views", 0)) for i in items)
    if views:
        metadata["pageviews_30d"] = views
    return ScrapeResult(
        SourceType.WIKIPEDIA, ScrapeStatus.SUCCESS, artist,
        texts=texts, metadata=metadata,
        metrics={"pageviews_30d": float(views)},
        mentions=1 if texts else 0,
    )


# --------------------------------------------------------------------------- #
# MusicBrainz
# --------------------------------------------------------------------------- #
def scrape_musicbrainz(session: requests.Session, artist: str) -> ScrapeResult:
    search = _get(session, "https://musicbrainz.org/ws/2/artist/",
                  params={"query": f'artist:"{artist}"', "fmt": "json", "limit": 1})
    if search is None or not search.ok:
        return ScrapeResult(SourceType.MUSICBRAINZ, ScrapeStatus.FAILED, artist, error="search failed")
    artists = search.json().get("artists", [])
    if not artists:
        return ScrapeResult(SourceType.MUSICBRAINZ, ScrapeStatus.PARTIAL, artist, error="no match")
    mb = artists[0]
    mbid = mb.get("id")
    metadata = {
        "musicbrainz_id": mbid,
        "type": mb.get("type"),
        "country": mb.get("country"),
        "disambiguation": mb.get("disambiguation"),
        "tags": [t["name"] for t in mb.get("tags", [])][:8],
    }
    # Lifespan -> active years
    life = mb.get("life-span", {})
    if life.get("begin"):
        metadata["begin_year"] = int(life["begin"][:4])
    texts = [f"{artist} is a {mb.get('type', 'artist')} from {mb.get('country', 'unknown')}."
             for _ in [0] if mb.get("country")]
    return ScrapeResult(
        SourceType.MUSICBRAINZ, ScrapeStatus.SUCCESS, artist,
        texts=texts, metadata=metadata, mentions=1,
    )


# --------------------------------------------------------------------------- #
# Wikidata (structured metadata: genres, country, inception)
# --------------------------------------------------------------------------- #
def scrape_wikidata(session: requests.Session, artist: str) -> ScrapeResult:
    # Resolve to Wikidata QID via search, then pull claims.
    s = _get(session, "https://www.wikidata.org/w/api.php",
             params={"action": "wbsearchentities", "search": artist,
                     "language": "en", "format": "json", "limit": 1})
    if s is None or not s.ok:
        return ScrapeResult(SourceType.WIKIDATA, ScrapeStatus.FAILED, artist, error="search failed")
    ents = s.json().get("search", [])
    if not ents:
        return ScrapeResult(SourceType.WIKIDATA, ScrapeStatus.PARTIAL, artist, error="no entity")
    qid = ents[0]["id"]
    e = _get(session, f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    if e is None or not e.ok:
        return ScrapeResult(SourceType.WIKIDATA, ScrapeStatus.FAILED, artist, error="entity fetch failed")
    entity = e.json()["entities"][qid]
    claims = entity.get("claims", {})

    # Resolve a set of Wikidata Q-IDs to their English labels in one call.
    qids_to_resolve: set = set()

    def _time_year(val: dict) -> Optional[int]:
        t = val.get("time", "")
        m = re.match(r"[+\-]?(\d{4})", t)
        return int(m.group(1)) if m else None

    def _raw_val(prop: str):
        vals = claims.get(prop, [])
        if not vals:
            return None
        return vals[0]["mainsnak"].get("datavalue", {}).get("value")

    # Collect Q-IDs that need label resolution.
    country_val = _raw_val("P17")
    genre_val = _raw_val("P136")
    if isinstance(country_val, dict) and "id" in country_val:
        qids_to_resolve.add(country_val["id"])
    if isinstance(genre_val, dict) and "id" in genre_val:
        qids_to_resolve.add(genre_val["id"])

    label_map: Dict[str, str] = {}
    if qids_to_resolve:
        lr = _get(session, "https://www.wikidata.org/w/api.php",
                  params={"action": "wbgetentities", "ids": "|".join(qids_to_resolve),
                          "props": "labels", "languages": "en", "format": "json"})
        if lr and lr.ok:
            for qid, ent in lr.json().get("entities", {}).items():
                lab = ent.get("labels", {}).get("en", {}).get("value")
                if lab:
                    label_map[qid] = lab

    def _label_for(val) -> Optional[str]:
        if isinstance(val, dict) and "id" in val:
            return label_map.get(val["id"])
        if isinstance(val, dict) and "time" in val:
            yr = _time_year(val)
            return str(yr) if yr else None
        return str(val) if val else None

    inception = _raw_val("P571")
    inception_year = _time_year(inception) if isinstance(inception, dict) else None

    metadata = {
        "wikidata_id": qid,
        "country": _label_for(country_val),
        "inception_year": str(inception_year) if inception_year else None,
        "genre": _label_for(genre_val),
        "official_site": _label_for(_raw_val("P856")),
    }
    metadata = {k: v for k, v in metadata.items() if v}
    texts = [f"{artist} (Wikidata {qid})."]
    return ScrapeResult(
        SourceType.WIKIDATA, ScrapeStatus.SUCCESS, artist,
        texts=texts, metadata=metadata, mentions=1,
    )


# --------------------------------------------------------------------------- #
# Hacker News (Algolia) - real public discussions
# --------------------------------------------------------------------------- #
def scrape_hackernews(session: requests.Session, artist: str, limit: int = 20) -> ScrapeResult:
    url = "http://hn.algolia.com/api/v1/search"
    resp = _get(session, url, params={"query": artist, "tags": "story", "hitsPerPage": limit})
    if resp is None or not resp.ok:
        return ScrapeResult(SourceType.HACKERNEWS, ScrapeStatus.FAILED, artist, error="no response")
    hits = resp.json().get("hits", [])
    texts: List[str] = []
    total_points = 0
    total_comments = 0
    for h in hits:
        title = h.get("title") or ""
        if title:
            texts.append(title)
        total_points += int(h.get("points") or 0)
        total_comments += int(h.get("num_comments") or 0)
    return ScrapeResult(
        SourceType.HACKERNEWS, ScrapeStatus.SUCCESS, artist,
        texts=texts,
        metadata={"total_points": total_points, "total_comments": total_comments,
                  "stories": len(hits)},
        metrics={"discussion_points": float(total_points), "discussion_comments": float(total_comments)},
        mentions=len(texts),
    )


# --------------------------------------------------------------------------- #
# GDELT - global news mentions
# --------------------------------------------------------------------------- #
def scrape_gdelt(session: requests.Session, artist: str, maxrecords: int = 15) -> ScrapeResult:
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    resp = _get(session, url, params={"query": f'"{artist}"', "mode": "ArtList",
                                       "maxrecords": maxrecords, "format": "json"},
                retries=4, backoff=3.0)
    if resp is None or not resp.ok:
        return ScrapeResult(SourceType.GDELT, ScrapeStatus.FAILED, artist, error="no response")
    try:
        data = resp.json()
    except Exception:
        return ScrapeResult(SourceType.GDELT, ScrapeStatus.FAILED, artist, error="bad json")
    articles = data.get("articles", [])
    texts = []
    sources = set()
    for a in articles:
        title = a.get("title") or ""
        if title:
            texts.append(title)
        if a.get("domain"):
            sources.add(a["domain"])
    return ScrapeResult(
        SourceType.GDELT, ScrapeStatus.SUCCESS, artist,
        texts=texts,
        metadata={"news_sources": sorted(sources), "articles": len(articles)},
        metrics={"news_mentions": float(len(texts))},
        mentions=len(texts),
    )


# --------------------------------------------------------------------------- #
# RSS - music / news blog feeds
# --------------------------------------------------------------------------- #
_RSS_FEEDS = [
    ("pitchfork_news", "https://pitchfork.com/rss/news/"),
    ("rollingstone_music", "https://www.rollingstone.com/music/feed/"),
    ("stereogum", "https://www.stereogum.com/feed/"),
]


def scrape_rss(session: requests.Session, artist: str, feeds=None) -> ScrapeResult:
    feeds = feeds or _RSS_FEEDS
    texts: List[str] = []
    matched = 0
    for name, url in feeds:
        resp = _get(session, url, timeout=15)
        if resp is None or not resp.ok:
            continue
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            continue
        # RSS 2.0 and Atom both use <title> under items/entries
        for item in root.iter():
            if item.tag in ("item", "entry"):
                title_el = item.find("title")
                desc_el = item.find("description")
                title = (title_el.text or "").strip() if title_el is not None else ""
                desc = (desc_el.text or "").strip() if desc_el is not None else ""
                blob = f"{title} {desc}"
                if artist.lower() in blob.lower():
                    texts.append(title or desc[:200])
                    matched += 1
    status = ScrapeStatus.SUCCESS if texts else ScrapeStatus.PARTIAL
    return ScrapeResult(
        SourceType.RSS, status, artist,
        texts=texts,
        metadata={"feeds_checked": len(feeds), "matched": matched},
        metrics={"rss_mentions": float(matched)},
        mentions=matched,
    )
