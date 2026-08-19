"""Read-only terminal read models over the canonical warehouse.

Every function returns plain JSON-serializable dicts/lists. No function here
writes to the warehouse and no function invents facts: a missing table, a NULL
column, or an empty series is returned as-is (UNKNOWN is never encoded as 0).

Entities are keyed by normalized name (the entity master is a later milestone);
``entity_key`` is a stable lowercase slug so the terminal can link artist ->
event -> venue -> market without a full identity merge.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..festivals.repository import (
    FestivalSpineRepository,
    billing_trajectory,
    co_occurrence,
    relationship_graph,
)
from ..identity.spotify import normalize_name as _normalize_name


def entity_key(name: str | None) -> str | None:
    """Stable, lowercase, trimmed entity key (never used for identity claims)."""
    if name is None:
        return None
    return re.sub(r"\s+", " ", name.strip()).lower()


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    try:
        cur = conn.execute(sql, params or [])
    except Exception:
        return []
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _market_of(row: dict[str, Any]) -> str | None:
    """Prefer explicit market; fall back to the city portion of ``city``."""
    if row.get("market"):
        return row["market"]
    city = row.get("city")
    if city:
        return city.split(",")[0].strip() or None
    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search_entities(conn, query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search artists, venues, markets, and festivals by name.

    Artist search hierarchy (identity never defined by fuzzy similarity):
      1. exact canonical name (core.artists)
      2. exact alias/sort name (reference.artist_search_terms, normalized)
      3. prefix/substring over the indexed search terms
      4. FTS candidate retrieval when the fts extension is available
    """
    q_raw = query.strip()
    q = f"%{q_raw.lower()}%"
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add_artist(name: str | None, artist_key: str | None, mbid: str | None = None,
                    priority: int = 0) -> None:
        if not name:
            return
        eid = artist_key or (f"mbid::{mbid}" if mbid else entity_key(name))
        if eid in seen_ids:
            return
        seen_ids.add(eid)
        results.append({"entity_type": "ARTIST", "entity_id": eid, "name": name,
                        "_priority": priority})

    # 1. exact canonical name first (identity-backed).
    exact = _rows(conn, """
        SELECT name, artist_key FROM core.artists
        WHERE lower(name) = ? AND name IS NOT NULL
        LIMIT ?
    """, [q_raw.lower(), limit])
    for r in exact:
        _add_artist(r["name"], r["artist_key"], priority=0)

    # 2. exact alias / sort name from the indexed search terms.
    alias_exact = _rows(conn, """
        SELECT DISTINCT st.artist_mbid, r.name
        FROM reference.artist_search_terms st
        JOIN reference.musicbrainz_artists r ON r.mbid = st.artist_mbid
        WHERE st.normalized_term = ? AND st.term_type IN ('ALIAS', 'SORT_NAME')
        LIMIT ?
    """, [q_raw.lower(), limit])
    for r in alias_exact:
        _add_artist(r["name"], None, r["artist_mbid"], priority=1)

    # 3. substring over the INDEXED terms (not the 2.2M JSON column).
    sub_hits = _rows(conn, """
        SELECT DISTINCT st.artist_mbid, r.name
        FROM reference.artist_search_terms st
        JOIN reference.musicbrainz_artists r ON r.mbid = st.artist_mbid
        WHERE st.normalized_term LIKE ?
        ORDER BY length(st.normalized_term) ASC
        LIMIT ?
    """, [q, limit])
    for r in sub_hits:
        _add_artist(r["name"], None, r["artist_mbid"], priority=2)

    # Also surface canonical-artist substring hits (core.artists first).
    canon_sub = _rows(conn, """
        SELECT name, artist_key FROM core.artists
        WHERE lower(name) LIKE ? AND name IS NOT NULL
        LIMIT ?
    """, [q, limit])
    for r in canon_sub:
        _add_artist(r["name"], r["artist_key"], priority=3)

    # 4. FTS candidate retrieval (BM25) — candidates only, never identity.
    #    Deduplicated per artist: the terms table has one row per (artist,
    #    term), and the FTS score function needs a unique key per row.
    fts_hits: list[tuple[Any, ...]] = []
    try:
        fts_hits = _rows(conn, """
            SELECT MAX(fts_reference_artist_search_terms.match_bm25(artist_mbid, ?)) AS score,
                   artist_mbid
            FROM reference.artist_search_terms
            WHERE score IS NOT NULL
            GROUP BY artist_mbid
            ORDER BY score DESC
            LIMIT ?
        """, [q_raw, limit])
    except Exception:
        pass  # fts extension unavailable: deterministic layers still work
    for r in fts_hits:
        _add_artist(str(r[1]), None, str(r[1]), priority=4)

    # Box-office / forward-watch names as fallback artist candidates.
    legacy = _rows(conn, """
        SELECT DISTINCT artist AS name FROM research.canonical_boxoffice_engagements
        WHERE lower(artist) LIKE ? AND artist IS NOT NULL
        UNION
        SELECT DISTINCT artist_name AS name FROM flywheel.forward_watch_events
        WHERE lower(artist_name) LIKE ? AND artist_name IS NOT NULL
        LIMIT ?
    """, [q, q, limit])
    for r in legacy:
        _add_artist(r["name"], None, priority=5)

    # Priority-stable ordering (exact canonical beats alias beats FTS).
    artists_sorted = sorted(results, key=lambda x: (x.pop("_priority", 99), x["name"].lower()))
    results = artists_sorted[:limit]
    results = [{k: v for k, v in r.items() if k != "_priority"} for r in results]

    venues = _rows(conn, """
        SELECT DISTINCT venue AS name FROM research.canonical_boxoffice_engagements
        WHERE lower(venue) LIKE ? AND venue IS NOT NULL
        UNION
        SELECT DISTINCT venue_name AS name FROM flywheel.forward_watch_events
        WHERE lower(venue_name) LIKE ? AND venue_name IS NOT NULL
        LIMIT ?
    """, [q, q, limit])
    for r in venues:
        results.append({"entity_type": "VENUE", "entity_id": entity_key(r["name"]),
                        "name": r["name"]})

    markets = _rows(conn, """
        SELECT DISTINCT city AS name FROM research.canonical_boxoffice_engagements
        WHERE lower(city) LIKE ? AND city IS NOT NULL
        LIMIT ?
    """, [q, limit])
    for r in markets:
        mkt = r["name"].split(",")[0].strip()
        results.append({"entity_type": "MARKET", "entity_id": entity_key(mkt),
                        "name": mkt})

    festivals = _rows(conn, """
        SELECT festival_key AS id, name, location_city, location_country
        FROM core.festivals
        WHERE lower(name) LIKE ? AND name IS NOT NULL
        ORDER BY first_edition_year, name
        LIMIT ?
    """, [q, limit])
    for r in festivals:
        results.append({"entity_type": "FESTIVAL", "entity_id": r["id"],
                        "name": r["name"],
                        "location": ", ".join(x for x in (r["location_city"], r["location_country"]) if x)})

    return results[:limit]


# ---------------------------------------------------------------------------
# Tape
# ---------------------------------------------------------------------------
def query_tape(
    conn,
    *,
    entity_type: str | None = None,
    market_id: str | None = None,
    activity_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = """
        SELECT activity_id, observed_at, effective_at, entity_type, entity_id,
               activity_type, artist_id, event_id, venue_id, market_id,
               source_provider, source_record_id, old_value_json, new_value_json,
               evidence_class, rights_status, source_url, knowledge_time
        FROM terminal.activity_tape WHERE 1=1
    """
    params: list[Any] = []
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    if market_id:
        sql += " AND lower(COALESCE(market_id, '')) = ?"
        params.append(market_id.lower())
    if activity_type:
        sql += " AND activity_type = ?"
        params.append(activity_type)
    sql += " ORDER BY observed_at DESC LIMIT ?"
    params.append(limit)
    return _rows(conn, sql, params)


# ---------------------------------------------------------------------------
# Artist
# ---------------------------------------------------------------------------
def _artist_name_keys(conn, artist_key: str) -> list[str]:
    """All lowercase name forms usable for cross-table joins.

    Canonical name + every alias/credit for the artist (core.artist_aliases,
    reference aliases). Used to link box-office/forward tables that are keyed
    by artist NAME, never by the canonical key. Returns [] when nothing is
    known.
    """
    name = _artist_name_for_key(conn, artist_key)
    if not name:
        return []
    forms = {_normalize_name(name)}
    for row in conn.execute(
        "SELECT alias FROM core.artist_aliases WHERE artist_key = ?",
        [artist_key],
    ).fetchall():
        if row[0]:
            forms.add(_normalize_name(row[0]))
    mbid = artist_key.removeprefix("mbid::")
    if len(mbid) > 8:
        row = conn.execute(
            "SELECT aliases FROM reference.musicbrainz_artists WHERE mbid = ?", [mbid],
        ).fetchone()
        if row and row[0]:
            try:
                for alias in json.loads(row[0]):
                    aname = (alias or {}).get("name")
                    if aname:
                        forms.add(_normalize_name(aname))
            except (ValueError, TypeError):
                pass
    return sorted(forms)


def get_artist(conn, artist_key: str) -> dict[str, Any] | None:
    name = _artist_name_for_key(conn, artist_key)
    if name is None:
        return None
    name_forms = _artist_name_keys(conn, artist_key)
    if not name_forms:
        name_forms = [_normalize_name(name)]
    placeholders = ",".join("?" for _ in name_forms)
    history = _rows(conn, f"""
        SELECT canonical_engagement_id, artist, venue, city, market, start_date,
               number_of_shows, is_multi_show
        FROM research.canonical_boxoffice_engagements
        WHERE lower(artist) IN ({placeholders})
        ORDER BY start_date
    """, name_forms)
    upcoming = _rows(conn, f"""
        SELECT watch_event_id, provider, provider_event_id, venue_name, market,
               event_date, event_status, first_seen_at, source_url
        FROM flywheel.forward_watch_events
        WHERE lower(artist_name) IN ({placeholders}) AND event_date >= CURRENT_DATE
        ORDER BY event_date
    """, name_forms)
    # Box-office outcomes are read from the raw boxscore corpus, which carries
    # headcount / gross / price directly and is keyed by artist name (the
    # economics.event_outcome_claims ledger uses a different id space).
    outcomes = _rows(conn, f"""
        SELECT engagement_id, start_date, venue, headcount_total, headcount_definition,
               ticket_gross_total, currency, price_min, price_max,
               reporting_source, source_url
        FROM research.boxoffice_engagements
        WHERE lower(artist) IN ({placeholders})
        ORDER BY start_date
    """, name_forms)
    identity = _rows(conn, """
        SELECT DISTINCT spotify_id, spotify_name, spotify_url, resolution_status
        FROM identity.spotify_artist_resolutions
        WHERE normalized_local_name = ? AND resolution_status = 'EXACT'
    """, [_normalize_name(name)])
    canonical = _rows(conn, """
        SELECT artist_key, name, musicbrainz_id, type, area, isni, ipi,
               sort_name, disambiguation, life_span_begin, life_span_end
        FROM core.artists
        WHERE artist_key = ? OR musicbrainz_id = ?
        LIMIT 1
    """, [artist_key, artist_key.removeprefix("mbid::")])
    external = _rows(conn, """
        SELECT id_type, id_value, url, namespace, confidence, source_system
        FROM core.entity_external_ids
        WHERE entity_type = 'artist' AND entity_key = ?
        ORDER BY id_type
    """, [artist_key]) if canonical else []
    return {
        "entity_type": "ARTIST",
        "entity_id": artist_key,
        "name": name,
        "spotify_id": identity[0]["spotify_id"] if identity else None,
        "identity": identity,
        "canonical": canonical[0] if canonical else None,
        "external_ids": external,
        "history_count": len(history),
        "upcoming_count": len(upcoming),
        "history": history,
        "upcoming": upcoming,
        "outcomes": outcomes,
        "attention": get_attention_series(conn, name),
        "news": get_news(conn, name),
        "tape": query_tape(conn, entity_type="EVENT", limit=50),
    }


def _artist_name_for_key(conn, artist_key: str) -> str | None:
    # Canonical identity master first: artist_key (mbid::… / name::… / hash)
    # or the bare MusicBrainz ID.
    row = _rows(conn, """
        SELECT name FROM core.artists
        WHERE artist_key = ? OR musicbrainz_id = ? OR musicbrainz_id = ?
        LIMIT 1
    """, [artist_key, artist_key, artist_key.removeprefix("mbid::")])
    if row:
        return row[0]["name"]
    row = _rows(conn, """
        SELECT artist AS name FROM research.canonical_boxoffice_engagements
        WHERE lower(artist) = ? LIMIT 1
    """, [artist_key])
    if row:
        return row[0]["name"]
    row = _rows(conn, """
        SELECT artist_name AS name FROM flywheel.forward_watch_events
        WHERE lower(artist_name) = ? LIMIT 1
    """, [artist_key])
    return row[0]["name"] if row else None


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------
def get_event(conn, event_id: str) -> dict[str, Any] | None:
    row = _rows(conn, """
        SELECT watch_event_id, provider, provider_event_id, artist_name,
               venue_name, market, event_date, event_time, event_status,
               first_seen_at, tracking_status, source_url, rights_status,
               commercial_use_status
        FROM flywheel.forward_watch_events WHERE watch_event_id = ?
    """, [event_id])
    if row:
        e = dict(row[0])
        e["kind"] = "FORWARD"
        e["observations"] = _rows(conn, """
            SELECT milestone, event_status, price_min, price_max, currency,
                   observed_at, knowledge_time, source_provider, source_url
            FROM flywheel.forward_watch_observations
            WHERE watch_event_id = ? ORDER BY knowledge_time
        """, [event_id])
        e["timeline"] = _rows(conn, """
            SELECT cutoff_type, cutoff_kind, cutoff_timestamp, upper_bound,
                   evidence_class, source_provider, source_url
            FROM flywheel.pre_event_cutoff_evidence
            WHERE source_event_id = ? OR canonical_event_id = ?
            ORDER BY knowledge_time
        """, [event_id, event_id])
        e["competition"] = get_competing_events(
            conn, e.get("market"), e.get("event_date"), days=7
        )
        e["evidence"] = _rows(conn, """
            SELECT source_provider, source_url, rights_status, knowledge_time
            FROM flywheel.forward_watch_observations WHERE watch_event_id = ?
            UNION ALL
            SELECT source_provider, source_url, rights_status, knowledge_time
            FROM flywheel.pre_event_cutoff_evidence
            WHERE source_event_id = ? OR canonical_event_id = ?
        """, [event_id, event_id, event_id])
        return e
    # Historical engagement keyed by canonical_engagement_id.
    row = _rows(conn, """
        SELECT canonical_engagement_id, artist, venue, city, market, tour,
               start_date, end_date, number_of_shows, is_multi_show,
               source_count, resolution_confidence
        FROM research.canonical_boxoffice_engagements
        WHERE canonical_engagement_id = ?
    """, [event_id])
    if not row:
        return None
    e = dict(row[0])
    e["kind"] = "HISTORICAL"
    e["outcomes"] = _rows(conn, """
        SELECT b.engagement_id, b.start_date, b.headcount_total, b.headcount_definition,
               b.ticket_gross_total, b.currency, b.price_min, b.price_max,
               b.reporting_source, b.source_url
        FROM research.boxoffice_engagements b
        JOIN research.boxoffice_engagement_resolutions r ON r.raw_engagement_id = b.engagement_id
        WHERE r.canonical_engagement_id = ?
    """, [event_id])
    e["timeline"] = _rows(conn, """
        SELECT cutoff_type, cutoff_kind, cutoff_timestamp, upper_bound,
               evidence_class, source_provider, source_url
        FROM flywheel.pre_event_cutoff_evidence
        WHERE canonical_event_id = ? ORDER BY knowledge_time
    """, [event_id])
    e["competition"] = get_competing_events(
        conn, _market_of(e), e.get("start_date"), days=7
    )
    return e


def get_competing_events(
    conn, market: str | None, event_date, days: int = 7
) -> list[dict[str, Any]]:
    if market is None or event_date is None:
        return []
    mkt = market.lower()
    return _rows(conn, """
        SELECT canonical_engagement_id, artist, venue, start_date
        FROM research.canonical_boxoffice_engagements
        WHERE lower(city) LIKE ? AND ABS(CAST(start_date AS DATE) - CAST(? AS DATE)) <= ?
        ORDER BY start_date
        LIMIT 25
    """, [f"%{mkt}%", str(event_date), days])


# ---------------------------------------------------------------------------
# Venue
# ---------------------------------------------------------------------------
def get_venue(conn, venue_key: str) -> dict[str, Any] | None:
    name = _venue_name_for_key(conn, venue_key)
    if name is None:
        return None
    history = _rows(conn, """
        SELECT canonical_engagement_id, artist, city, start_date, number_of_shows
        FROM research.canonical_boxoffice_engagements
        WHERE lower(venue) = ? ORDER BY start_date
    """, [venue_key])
    upcoming = _rows(conn, """
        SELECT watch_event_id, artist_name, event_date, event_status
        FROM flywheel.forward_watch_events
        WHERE lower(venue_name) = ? AND event_date >= CURRENT_DATE ORDER BY event_date
    """, [venue_key])
    capacity = _rows(conn, """
        SELECT s.venue_name, c.capacity_value AS capacity, c.capacity_kind AS capacity_type,
               c.provider AS source_provider, c.source_url, c.knowledge_time
        FROM economics.venue_capacity_claims c
        LEFT JOIN economics.venue_source_ids s ON s.canonical_venue_id = c.canonical_venue_id
        WHERE lower(s.venue_name) = ?
    """, [venue_key])
    return {
        "entity_type": "VENUE",
        "entity_id": venue_key,
        "name": name,
        "history_count": len(history),
        "upcoming_count": len(upcoming),
        "history": history,
        "upcoming": upcoming,
        # capacity claims are CLAIMS: never collapsed to one exact number.
        "capacity_claims": capacity,
    }


def _venue_name_for_key(conn, venue_key: str) -> str | None:
    for sql in (
        "SELECT venue AS name FROM research.canonical_boxoffice_engagements WHERE lower(venue) = ? LIMIT 1",
        "SELECT venue_name AS name FROM flywheel.forward_watch_events WHERE lower(venue_name) = ? LIMIT 1",
    ):
        row = _rows(conn, sql, [venue_key])
        if row:
            return row[0]["name"]
    return None


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------
def get_market(conn, market_key: str) -> dict[str, Any] | None:
    upcoming = _rows(conn, """
        SELECT watch_event_id, artist_name, venue_name, event_date, event_status
        FROM flywheel.forward_watch_events
        WHERE lower(market) = ? AND event_date >= CURRENT_DATE ORDER BY event_date
    """, [market_key])
    history = _rows(conn, """
        SELECT canonical_engagement_id, artist, venue, start_date
        FROM research.canonical_boxoffice_engagements
        WHERE lower(city) LIKE ? ORDER BY start_date DESC LIMIT 100
    """, [f"{market_key}%"])
    venues = _rows(conn, """
        SELECT DISTINCT venue AS name FROM research.canonical_boxoffice_engagements
        WHERE lower(city) LIKE ? AND venue IS NOT NULL
    """, [f"{market_key}%"])
    if not upcoming and not history and not venues:
        return None
    return {
        "entity_type": "MARKET",
        "entity_id": market_key,
        "name": market_key.title(),
        "upcoming_count": len(upcoming),
        "history_count": len(history),
        "upcoming": upcoming,
        "history": history,
        "venues": [v["name"] for v in venues],
        # Demographic/weather context is provider-gated; empty until acquired.
        "profile": {},
        "context": _rows(conn, """
            SELECT series_type, observed_date, value, unit, vintage, provider
            FROM flywheel.context_panel_series
            WHERE lower(entity_name) = ? ORDER BY observed_date
        """, [market_key]),
    }


# ---------------------------------------------------------------------------
# Festival
# ---------------------------------------------------------------------------
def get_festival(conn, festival_key: str) -> dict[str, Any] | None:
    repo = FestivalSpineRepository(conn)
    fest = repo.get_festival(festival_key)
    if not fest:
        return None
    fest["entity_type"] = "FESTIVAL"
    fest["entity_id"] = festival_key
    for ed in fest["editions"]:
        ed["lineup"] = repo.get_lineup(ed["edition_key"])
        ed["billing"] = repo.get_billing(ed["edition_key"])
    return fest


def get_festival_edition(conn, edition_key: str) -> dict[str, Any] | None:
    return FestivalSpineRepository(conn).get_edition(edition_key)


def get_tour(conn, tour_key: str) -> dict[str, Any] | None:
    """TOUR page: series identity + events + performers + venues/markets."""
    row = conn.execute(
        """
        SELECT s.series_key, s.name, s.musicbrainz_id, s.disambiguation,
               s.series_type, s.begin_date, s.end_date,
               (SELECT COUNT(*) FROM core.series_events se
                WHERE se.series_key = s.series_key) AS event_count
        FROM core.event_series s WHERE s.series_key = ?
        """,
        [tour_key],
    ).fetchone()
    if row is None:
        # allow bare MBID lookup
        row = conn.execute(
            """
            SELECT s.series_key, s.name, s.musicbrainz_id, s.disambiguation,
                   s.series_type, s.begin_date, s.end_date,
                   (SELECT COUNT(*) FROM core.series_events se
                    WHERE se.series_key = s.series_key) AS event_count
            FROM core.event_series s WHERE s.musicbrainz_id = ?
            """,
            [tour_key],
        ).fetchone()
    if row is None:
        return None
    (series_key, name, mbid, disambiguation, series_type, begin_date,
     end_date, event_count) = row
    events = _rows(conn, """
        SELECT se.event_mbid AS event_key, se.event_name, se.event_begin_date AS local_date,
               se.event_type, r.object_key AS venue_key,
               (SELECT p.name FROM raw.musicbrainz_place p
                WHERE 'mbid::' || p.mbid = r.object_key) AS venue_name,
               (SELECT p.area FROM raw.musicbrainz_place p
                WHERE 'mbid::' || p.mbid = r.object_key) AS market
        FROM core.series_events se
        LEFT JOIN core.entity_relationships r
               ON r.subject_key = 'mbid::' || se.event_mbid AND r.predicate = 'EVENT_AT_PLACE'
        WHERE se.series_key = ?
        ORDER BY se.event_begin_date NULLS LAST, se.event_name
        """, [series_key])
    performers = _rows(conn, """
        SELECT DISTINCT ep.artist_mbid, ep.artist_name, ep.performer_role,
               a.artist_key
        FROM core.series_events se
        JOIN core.event_performers ep ON ep.event_mbid = se.event_mbid
        LEFT JOIN core.artists a ON a.musicbrainz_id = ep.artist_mbid
        WHERE se.series_key = ?
        ORDER BY ep.artist_name
        """, [series_key])
    markets = sorted({e["market"] for e in events if e.get("market")})
    venues = sorted({e["venue_name"] for e in events if e.get("venue_name")})
    return {
        "entity_type": "TOUR",
        "entity_id": series_key,
        "series_key": series_key,
        "name": name,
        "musicbrainz_id": mbid,
        "disambiguation": disambiguation,
        "series_type": series_type,
        "date_range": [begin_date, end_date],
        "event_count": int(event_count or 0),
        "events": events,
        "performers": performers,
        "markets": markets,
        "venues": venues,
    }


def list_tours(conn, *, market: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """TOUR/RESIDENCY/RUN series list with event/market/artist counts."""
    sql = """
        SELECT s.series_key, s.name, s.musicbrainz_id, s.series_type,
               s.begin_date, s.end_date, s.disambiguation,
               (SELECT COUNT(*) FROM core.series_events se
                WHERE se.series_key = s.series_key) AS event_count,
               (SELECT COUNT(DISTINCT ep.artist_mbid)
                FROM core.series_events se
                JOIN core.event_performers ep ON ep.event_mbid = se.event_mbid
                WHERE se.series_key = s.series_key) AS artist_count
        FROM core.event_series s
        WHERE s.series_type IN ('TOUR', 'RESIDENCY', 'RUN')
    """
    params: list[Any] = []
    if market:
        sql += """
          AND s.series_key IN (
              SELECT se.series_key FROM core.series_events se
              JOIN core.entity_relationships r
                   ON r.subject_key = 'mbid::' || se.event_mbid
                  AND r.predicate = 'EVENT_AT_PLACE'
              JOIN raw.musicbrainz_place p ON 'mbid::' || p.mbid = r.object_key
              WHERE lower(p.area) = lower(?))
        """
        params.append(market)
    sql += " ORDER BY event_count DESC LIMIT ?"
    params.append(limit)
    rows = _rows(conn, sql, params)
    for r in rows:
        r["entity_type"] = "TOUR"
        r["entity_id"] = r["series_key"]
    return rows


def get_artist_billing_trajectory(conn, artist_name: str) -> list[dict[str, Any]]:
    return billing_trajectory(conn, artist_name)


def get_artist_co_occurrence(conn, artist_name: str) -> list[dict[str, Any]]:
    return co_occurrence(conn, artist_name)


def get_artist_relationship_graph(conn, artist_name: str) -> dict[str, Any]:
    return relationship_graph(conn, artist_name)


# ---------------------------------------------------------------------------
# Attention / news / sources
# ---------------------------------------------------------------------------
def get_attention_series(conn, entity_name: str) -> list[dict[str, Any]]:
    # ``artist_key`` is either ``name::<normalized>`` (fallback) or a bare
    # normalized name; match both. UNKNOWN/error rows are excluded here and
    # surfaced separately so a missing article never reads as zero.
    key = _normalize_name(entity_name)
    # artist_key is either mbid::<uuid>, name::<normalized>, or a bare name.
    # Resolve the canonical MBID for this artist when present, plus the name keys.
    mbid_rows = _rows(conn, """
        SELECT musicbrainz_id FROM core.artists
        WHERE lower(name) = ? OR artist_key IN (?, ?) LIMIT 1
    """, [entity_name.lower(), key, f"name::{key}"])
    keys = [key, f"name::{key}"]
    if mbid_rows and mbid_rows[0]["musicbrainz_id"]:
        keys.append(f"mbid::{mbid_rows[0]['musicbrainz_id']}")
        keys.append(mbid_rows[0]["musicbrainz_id"])
    placeholders = ",".join("?" for _ in keys)
    return _rows(conn, f"""
        SELECT period_start AS observed_date, value, value_sum, value_unit AS unit,
               metric_kind AS metric_name, source_system AS provider, article_title,
               granularity, status, source_url, retrieved_at
        FROM metrics.artist_attention_observations
        WHERE lower(artist_key) IN ({placeholders}) AND status = 'ok'
        ORDER BY period_start, retrieved_at
    """, keys)


def get_news(conn, entity_name: str) -> list[dict[str, Any]]:
    # GDELT news mentions (metadata only); full article text is never stored.
    key = _normalize_name(entity_name)
    return _rows(conn, """
        SELECT mention_id, entity_type, entity_name, article_url, title,
               publication_time, domain, provider, query_or_match, retrieved_at
        FROM terminal.news_mentions
        WHERE lower(entity_name) = ? OR lower(entity_id) IN (?, ?)
        ORDER BY publication_time DESC LIMIT 25
    """, [entity_name.lower(), key, f"name::{key}"])


def get_recent_news(conn, limit: int = 100) -> list[dict[str, Any]]:
    """Most recent news mentions across all entities (the NEWS view)."""
    return _rows(conn, """
        SELECT mention_id, entity_type, entity_name, entity_id, article_url,
               title, publication_time, domain, provider, retrieved_at
        FROM terminal.news_mentions
        ORDER BY publication_time DESC, retrieved_at DESC LIMIT ?
    """, [limit])


def get_attention_coverage(conn, limit: int = 100) -> list[dict[str, Any]]:
    """Attention-series coverage across entities (the ATTN view).

    Returns one row per (artist, metric) with total views and the latest
    observation window. Only 'ok' observations are counted; missing/error
    articles are surfaced separately, never as zero.
    """
    return _rows(conn, """
        SELECT artist_key, article_title, metric_kind, source_system, project,
               COUNT(*) AS observations, MAX(period_end) AS latest_window,
               SUM(value_sum) AS total_value, value_unit
        FROM metrics.artist_attention_observations
        WHERE status = 'ok'
        GROUP BY artist_key, article_title, metric_kind, source_system, project, value_unit
        ORDER BY total_value DESC LIMIT ?
    """, [limit])


def get_sources(conn) -> list[dict[str, Any]]:
    registry = _rows(conn, """
        SELECT source_id, source_name, source_kind, pipeline, provider,
               access_status, documented_quota, rights_status,
               commercial_use_status, license, coverage_contribution, notes
        FROM flywheel.source_registry ORDER BY source_id
    """)
    health = {h["provider"]: h for h in _rows(conn, """
        SELECT provider, operational_status, last_success_at, last_attempt_at,
               latest_knowledge_time, records_total, entities_covered,
               failure_count, rate_limit_count, freshness_note
        FROM terminal.provider_health
    """)}
    for r in registry:
        h = health.get(r["source_id"])
        r["operational"] = h or {
            "operational_status": "NOT_MEASURED",
            "last_success_at": None, "last_attempt_at": None,
            "latest_knowledge_time": None, "records_total": None,
            "entities_covered": None, "failure_count": None,
            "rate_limit_count": None, "freshness_note": None,
        }
    return registry


def get_recent_changes(conn, limit: int = 100) -> list[dict[str, Any]]:
    """Recently changed live events (cancellations, onsales, prices, status)."""
    change_types = (
        "EVENT_CANCELLED", "EVENT_POSTPONED", "EVENT_RESCHEDULED",
        "EVENT_STATUS_CHANGED", "ONSALE_DISCOVERED", "PRESALE_DISCOVERED",
        "PRICE_RANGE_DISCOVERED", "PROMOTER_IDENTIFIED",
    )
    placeholders = ",".join("?" for _ in change_types)
    return _rows(conn, f"""
        SELECT activity_id, observed_at, entity_id, activity_type, artist_id,
               venue_id, market_id, source_provider, source_url, new_value_json
        FROM terminal.activity_tape
        WHERE activity_type IN ({placeholders})
        ORDER BY observed_at DESC LIMIT ?
    """, [*change_types, limit])


def get_live_events(conn, *, market: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Latest Ticketmaster event snapshots (distinct events, newest retrieval)."""
    sql = """
        SELECT s.platform_object_id, s.event_name, s.artist_name, s.venue_name,
               s.city, s.state_code, s.local_date, s.event_status, s.onsale_start,
               s.price_min, s.price_max, s.price_currency, s.promoter, s.canonical_url
        FROM events.provider_event_snapshots s
        JOIN (
            SELECT platform_object_id, MAX(retrieved_at) AS latest
            FROM events.provider_event_snapshots
            WHERE provider = 'ticketmaster'
            GROUP BY platform_object_id
        ) latest ON latest.platform_object_id = s.platform_object_id
                  AND latest.latest = s.retrieved_at
        WHERE 1=1
    """
    params: list[Any] = []
    if market:
        sql += " AND lower(s.city) = ?"
        params.append(market.lower())
    sql += " ORDER BY s.local_date, s.event_name LIMIT ?"
    params.append(limit)
    return _rows(conn, sql, params)


def get_source_evidence(conn, entity_id: str) -> list[dict[str, Any]]:
    """Return the source/evidence lineage backing an event or forward id."""
    return _rows(conn, """
        SELECT cutoff_type, cutoff_kind, evidence_class, source_provider,
               source_url, source_document_id, knowledge_time, rights_status
        FROM flywheel.pre_event_cutoff_evidence
        WHERE canonical_event_id = ? OR source_event_id = ?
        UNION ALL
        SELECT 'RESULT_PUBLICATION', evidence_class, evidence_class,
               source_provider, source_url, source_document_id, knowledge_time,
               rights_status
        FROM flywheel.pit_reconstruction_evidence
        WHERE canonical_event_id = ?
    """, [entity_id, entity_id, entity_id])
