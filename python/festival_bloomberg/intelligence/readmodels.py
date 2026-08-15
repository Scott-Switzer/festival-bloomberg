"""Read-only terminal read models over the canonical warehouse.

Every function returns plain JSON-serializable dicts/lists. No function here
writes to the warehouse and no function invents facts: a missing table, a NULL
column, or an empty series is returned as-is (UNKNOWN is never encoded as 0).

Entities are keyed by normalized name (the entity master is a later milestone);
``entity_key`` is a stable lowercase slug so the terminal can link artist ->
event -> venue -> market without a full identity merge.
"""

from __future__ import annotations

import re
from typing import Any


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
    """Search artists, venues, markets, and festivals by name."""
    q = f"%{query.strip().lower()}%"
    results: list[dict[str, Any]] = []

    artists = _rows(conn, """
        SELECT DISTINCT artist AS name FROM research.canonical_boxoffice_engagements
        WHERE lower(artist) LIKE ? AND artist IS NOT NULL
        UNION
        SELECT DISTINCT artist_name AS name FROM flywheel.forward_watch_events
        WHERE lower(artist_name) LIKE ? AND artist_name IS NOT NULL
        LIMIT ?
    """, [q, q, limit])
    for r in artists:
        results.append({"entity_type": "ARTIST", "entity_id": entity_key(r["name"]),
                        "name": r["name"]})

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

    # Festivals: no canonical festival corpus yet; returned empty (honest).
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
def get_artist(conn, artist_key: str) -> dict[str, Any] | None:
    name = _artist_name_for_key(conn, artist_key)
    if name is None:
        return None
    history = _rows(conn, """
        SELECT canonical_engagement_id, artist, venue, city, market, start_date,
               number_of_shows, is_multi_show
        FROM research.canonical_boxoffice_engagements
        WHERE lower(artist) = ?
        ORDER BY start_date
    """, [artist_key])
    upcoming = _rows(conn, """
        SELECT watch_event_id, provider, provider_event_id, venue_name, market,
               event_date, event_status, first_seen_at, source_url
        FROM flywheel.forward_watch_events
        WHERE lower(artist_name) = ? AND event_date >= CURRENT_DATE
        ORDER BY event_date
    """, [artist_key])
    # Box-office outcomes are read from the raw boxscore corpus, which carries
    # headcount / gross / price directly and is keyed by artist name (the
    # economics.event_outcome_claims ledger uses a different id space).
    outcomes = _rows(conn, """
        SELECT engagement_id, start_date, venue, headcount_total, headcount_definition,
               ticket_gross_total, currency, price_min, price_max,
               reporting_source, source_url
        FROM research.boxoffice_engagements
        WHERE lower(artist) = ?
        ORDER BY start_date
    """, [artist_key])
    return {
        "entity_type": "ARTIST",
        "entity_id": artist_key,
        "name": name,
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
    # No canonical festival corpus yet. Return None so the terminal can render
    # an honest "no data" page rather than fabricate a festival.
    return None


# ---------------------------------------------------------------------------
# Attention / news / sources
# ---------------------------------------------------------------------------
def get_attention_series(conn, entity_name: str) -> list[dict[str, Any]]:
    return _rows(conn, """
        SELECT observed_date, value, unit, metric_name, provider, vintage
        FROM metrics.artist_attention_observations
        WHERE lower(entity_name) = ? ORDER BY observed_date
    """, [entity_name.lower()])


def get_news(conn, entity_name: str) -> list[dict[str, Any]]:
    # GDELT/YouTube/news providers not yet activated: honest empty list.
    return _rows(conn, """
        SELECT article_url, title, publication_time, domain, provider
        FROM terminal.news_mentions
        WHERE lower(entity_name) = ? ORDER BY publication_time DESC LIMIT 25
    """, [entity_name.lower()])


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
