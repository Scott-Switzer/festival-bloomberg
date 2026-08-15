"""LIVE_ENTERTAINMENT_DATA_FABRIC_V1 — operational acceptance driver.

Turns the working ingestion proof into a denser, cross-linked information
estate with three real, key-free / already-credentialed acquisitions:

1. **GDELT news discovery** (key-free, metadata-only): bounded per-entity
   keyword queries against the DOC 2.0 artlist endpoint. Persists article
   metadata (URL, title, domain, publication time, language) into
   ``terminal.news_mentions`` and derives NEWS_MENTION tape rows. Full
   article text is never fetched or stored. The provider's documented
   >=5s/request spacing is honored (the provider enforces it; we do not
   burst to defeat it).

2. **Wikimedia pageviews** (key-free attention series): a bounded window of
   daily pageviews for resolved artist names, persisted into
   ``metrics.artist_attention_observations``. A missing article is persisted
   as ``missing``, never as a fabricated zero.

3. **Ticketmaster US music sweep** (already-credentialed): extended
   market partitions with a per-partition manifest in
   ``terminal.acquisition_partitions`` so COMPLETE vs TRUNCATED-by-cap vs
   RATE_LIMITED is reported honestly rather than as a raw event count.

No secret value is ever written to the report.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionRequest, content_hash_of, utc_now
from ..acquisition.providers.gdelt import GdeltProvider
from ..acquisition.providers.ticketmaster import TicketmasterProvider
from ..acquisition.transport import UrllibTransport
from ..attention.wikimedia_pageviews import collect_artist_pageviews
from ..identity.spotify import normalize_name
from ..intelligence.tape import (
    derive_news_tape_entries,
    derive_provider_event_tape_entries,
    insert_tape_entries,
)
from ..localenv import load_local_env
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "live_entertainment_data_fabric_v1"

#: Extended US market partitions (city, state). Washington, DC uses "DC".
DEFAULT_MARKETS: tuple[tuple[str, str], ...] = (
    ("Chicago", "IL"),
    ("Los Angeles", "CA"),
    ("New York", "NY"),
    ("Austin", "TX"),
    ("Nashville", "TN"),
    ("Atlanta", "GA"),
    ("Boston", "MA"),
    ("Dallas", "TX"),
    ("Denver", "CO"),
    ("Houston", "TX"),
    ("Las Vegas", "NV"),
    ("Miami", "FL"),
    ("Philadelphia", "PA"),
    ("Phoenix", "AZ"),
    ("San Francisco", "CA"),
    ("Seattle", "WA"),
    ("Washington", "DC"),
)


def _count(conn, sql: str, params: list[Any] | None = None) -> int:
    try:
        return int(conn.execute(sql, params or []).fetchone()[0])
    except Exception:
        return 0


def _table_rows(conn, table: str) -> int:
    return _count(conn, f"SELECT COUNT(*) FROM {table}")


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _collect_news_entities(conn, limit: int) -> list[tuple[str, str, str]]:
    """Distinct (entity_type, entity_name, entity_id) to query GDELT for.

    Order: festival-seed artists, box-office artists, then festival names.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for entity_type, table, col in (
        ("ARTIST", "core.lineup_slots", "artist_name"),
        ("ARTIST", "research.canonical_boxoffice_engagements", "artist"),
        ("ARTIST", "flywheel.forward_watch_events", "artist_name"),
        ("FESTIVAL", "core.festivals", "name"),
    ):
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL AND {col} <> '' ORDER BY 1"
            ).fetchall()
        except Exception:
            continue
        for (name,) in rows:
            key = f"{entity_type}|{normalize_name(name)}"
            if key in seen:
                continue
            seen.add(key)
            if entity_type == "FESTIVAL":
                entity_id = f"name::{normalize_name(name)}"
            else:
                entity_id = f"name::{normalize_name(name)}"
            out.append((entity_type, name, entity_id))
            if len(out) >= limit:
                return out
    return out


def _persist_news_mention(conn, rec: dict[str, Any], *, entity_type: str, entity_name: str,
                          entity_id: str, query: str, retrieved_at: str) -> bool:
    article_url = rec.get("article_url")
    if not article_url:
        return False
    mention_id = content_hash_of(f"{entity_type}|{entity_id}|{article_url}")
    dedupe = content_hash_of(f"news|{entity_type}|{entity_id}|{article_url}")
    exists = conn.execute(
        "SELECT 1 FROM terminal.news_mentions WHERE dedupe_key = ?", [dedupe]
    ).fetchone()
    if exists:
        return False
    pub = _parse_ts(rec.get("published_at"))
    retrieved = _parse_ts(retrieved_at) or utc_now()
    conn.execute(
        """
        INSERT INTO terminal.news_mentions
            (mention_id, entity_type, entity_name, entity_id, article_url, domain,
             title, publication_time, query_or_match, provider, retrieved_at,
             knowledge_time, rights_status, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'gdelt', ?, ?, 'OPEN_COMMERCIAL_OK', ?)
        """,
        [
            mention_id, entity_type, entity_name, entity_id, article_url,
            rec.get("domain"), rec.get("title"), pub, query,
            retrieved, retrieved, dedupe,
        ],
    )
    return True


def _run_gdelt_news(conn, limit: int, validate: bool) -> dict[str, Any]:
    summary = {
        "status": "RUNNING", "entities_attempted": 0, "queries": 0,
        "articles_returned": 0, "mentions_persisted": 0, "rate_limited": 0,
        "provider_errors": 0, "no_results": 0,
    }
    if not validate:
        summary["status"] = "SKIPPED"
        return summary
    if limit <= 0:
        summary["status"] = "SKIPPED"
        return summary
    provider = GdeltProvider(transport=UrllibTransport())
    entities = _collect_news_entities(conn, limit)
    for entity_type, name, entity_id in entities:
        req = AcquisitionRequest.new(
            entity_id=entity_id,
            entity_type="artist" if entity_type == "ARTIST" else "festival",
            platform="gdelt",
            query=f'"{name}"',
            max_records=25,
            commercial_context="research",
        )
        result = provider.acquire(req)
        summary["queries"] += 1
        summary["entities_attempted"] += 1
        if result.status.value == "RATE_LIMITED":
            summary["rate_limited"] += 1
            summary["status"] = "RATE_LIMITED_STOPPED"
            break
        if result.status.value == "PROVIDER_ERROR":
            summary["provider_errors"] += 1
            summary["status"] = "STOPPED_PROVIDER_ERROR"
            break
        if result.status.value == "NO_RESULTS":
            summary["no_results"] += 1
            continue
        summary["articles_returned"] += result.record_count
        for rec in result.records:
            if _persist_news_mention(
                conn, rec, entity_type=entity_type, entity_name=name,
                entity_id=entity_id, query=f'"{name}"',
                retrieved_at=result.completed_at.isoformat(),
            ):
                summary["mentions_persisted"] += 1
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary


def _run_wikimedia_attention(conn, names: list[str], days: int, validate: bool) -> dict[str, Any]:
    if not validate:
        return {"status": "SKIPPED", "names_attempted": 0}
    return collect_artist_pageviews(conn, UrllibTransport(), names=names, days=days)


def _run_ticketmaster(conn, markets: tuple[tuple[str, str], ...], validate: bool) -> dict[str, Any]:
    summary = {
        "status": "RUNNING", "configured": False, "partitions": 0,
        "partitions_complete": 0, "partitions_truncated": 0,
        "events_persisted": 0, "requests": 0, "rate_limited": 0,
        "provider_errors": 0, "distinct_events": 0,
    }
    provider = TicketmasterProvider(transport=UrllibTransport())
    summary["configured"] = provider.configured()
    if not provider.configured():
        summary["status"] = "NOT_CONFIGURED"
        return summary
    if not validate:
        summary["status"] = "SKIPPED"
        return summary

    run_retrieved = utc_now().isoformat()
    for city, state in markets:
        req = AcquisitionRequest.new(
            entity_id=city.lower(),
            entity_type="market",
            platform="ticketmaster",
            query="",
            market_id=f"{city},{state},US",
            classification_name="Music",
            max_records=100,  # full allowed depth (5 pages x 20); deep-paging cap is never exceeded
            operation="SEARCH_EVENTS",
            commercial_context="research",
            start_time=utc_now(),
        )
        result = provider.acquire(req)
        pagination = (result.provider_metadata or {}).get("pagination") or {}
        summary["partitions"] += 1
        summary["requests"] += int(pagination.get("pages_fetched", 1) or 1)
        if result.status.value == "NOT_CONFIGURED":
            summary["status"] = "NOT_CONFIGURED"
            _persist_partition(conn, city, state, "NOT_CONFIGURED", None, False, None, run_retrieved)
            break
        if result.status.value == "RATE_LIMITED":
            summary["rate_limited"] += 1
            summary["status"] = "RATE_LIMITED_STOPPED"
            _persist_partition(conn, city, state, "RATE_LIMITED", None, False, "rate_limited", run_retrieved)
            break
        if result.status.value == "PROVIDER_ERROR":
            summary["provider_errors"] += 1
            summary["status"] = "STOPPED_PROVIDER_ERROR"
            _persist_partition(conn, city, state, "ERROR", None, False, "provider_error", run_retrieved)
            break

        coverage = pagination.get("coverage_status")
        truncated = coverage == "TRUNCATED_BY_CAP"
        complete = bool(pagination.get("complete"))
        if result.is_success and complete and not truncated:
            pstatus = "COMPLETE"
            summary["partitions_complete"] += 1
        else:
            pstatus = "PARTIAL"
            if truncated:
                summary["partitions_truncated"] += 1
        total = pagination.get("reported_total")
        received = pagination.get("items_fetched")
        _persist_partition(conn, city, state, pstatus, total, truncated, None, run_retrieved,
                           received=received)
        for rec in result.records:
            if _persist_event_snapshot(conn, rec, result.completed_at.isoformat()):
                summary["events_persisted"] += 1
    summary["distinct_events"] = _count(
        conn, "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"
    )
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary


def _persist_partition(conn, city: str, state: str, status: str, total_expected,
                       truncated: bool, error_category, retrieved_at: str,
                       received: int | None = None) -> None:
    partition_id = f"{city.lower()},{state.lower()},US:music"
    partition_key = content_hash_of(f"ticketmaster|{partition_id}|{retrieved_at}")
    exists = conn.execute(
        "SELECT 1 FROM terminal.acquisition_partitions WHERE partition_key = ?", [partition_key]
    ).fetchone()
    if exists:
        return
    conn.execute(
        """
        INSERT INTO terminal.acquisition_partitions
            (partition_key, provider, partition_id, market_id, classification_name,
             window_start, window_end, total_expected, records_received,
             records_persisted, truncated, status, error_category, started_at,
             finished_at, retrieved_at, knowledge_time, software_version, ingested_at)
        VALUES (?, 'ticketmaster', ?, ?, 'Music', NULL, NULL, ?, ?, 0, ?, ?, ?, NULL,
                NULL, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            partition_key, partition_id, f"{city},{state},US", total_expected,
            received if received is not None else 0, truncated, status,
            error_category, retrieved_at, retrieved_at, SOFTWARE_VERSION,
        ],
    )


def _persist_event_snapshot(conn, rec: dict[str, Any], retrieved_at: str) -> bool:
    platform_object_id = rec.get("platform_object_id")
    if not platform_object_id:
        return False
    snapshot_key = content_hash_of(
        {"provider": "ticketmaster", "id": platform_object_id, "retrieved_at": retrieved_at}
    )
    exists = conn.execute(
        "SELECT 1 FROM events.provider_event_snapshots WHERE snapshot_key = ?", [snapshot_key]
    ).fetchone()
    if exists:
        return False
    attractions = rec.get("attractions") or []
    artist_name = (attractions[0].get("attraction_name") if attractions else None)
    classifications = rec.get("classifications") or {}
    conn.execute(
        """
        INSERT INTO events.provider_event_snapshots
            (snapshot_key, provider, platform_object_id, event_name, artist_name,
             attractions, venue_id, venue_name, city, state_code, country_code,
             latitude, longitude, local_date, local_time, event_time, timezone,
             event_status, onsale_start, onsale_end, presales, price_min,
             price_max, price_currency, price_type, promoter, segment, genre,
             subgenre, event_type, canonical_url, retrieved_at, knowledge_time,
             content_hash, raw_payload_hash, rights_status, commercial_use_status,
             software_version, ingested_at)
        VALUES (?, 'ticketmaster',
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, 'RESEARCH_ONLY',
                'PROTOTYPE_ONLY', ?, CURRENT_TIMESTAMP)
        """,
        [
            snapshot_key,
            platform_object_id,
            rec.get("event_name"),
            artist_name,
            json.dumps(attractions, default=str),
            rec.get("ticketmaster_venue_id"),
            rec.get("venue_name"),
            rec.get("city"),
            rec.get("state_code"),
            rec.get("country_code"),
            rec.get("latitude"),
            rec.get("longitude"),
            rec.get("local_date"),
            rec.get("local_time"),
            rec.get("event_time"),
            rec.get("timezone"),
            rec.get("event_status"),
            rec.get("onsale_start"),
            rec.get("onsale_end"),
            json.dumps(rec.get("presales") or [], default=str),
            rec.get("price_min"),
            rec.get("price_max"),
            rec.get("price_currency"),
            rec.get("price_type"),
            rec.get("promoter"),
            classifications.get("segment"),
            classifications.get("genre"),
            classifications.get("subgenre"),
            rec.get("event_type"),
            rec.get("canonical_url"),
            retrieved_at,
            retrieved_at,
            rec.get("content_hash"),
            rec.get("content_hash"),
            SOFTWARE_VERSION,
        ],
    )
    return True


def run_data_fabric_oa(
    *,
    db_path: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/live_entertainment_data_fabric_v1.json",
    validate: bool = True,
    gdelt_limit: int = 12,
    attention_names: list[str] | None = None,
    attention_days: int = 30,
    ticketmaster_markets: tuple[tuple[str, str], ...] = DEFAULT_MARKETS,
) -> dict[str, Any]:
    load_local_env()
    started = utc_now()
    oa_run_id = f"data_fabric_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(db_path)
    try:
        from ..events.repository import EventRepository

        EventRepository(repo.conn)  # applies pending migrations (incl. 025)
        conn = repo.conn

        def snapshot() -> dict[str, int]:
            return {
                "news_mentions": _table_rows(conn, "terminal.news_mentions"),
                "attention_observations": _table_rows(conn, "metrics.artist_attention_observations"),
                "provider_event_snapshots": _table_rows(conn, "events.provider_event_snapshots"),
                "partitions": _table_rows(conn, "terminal.acquisition_partitions"),
                "activity_tape": _table_rows(conn, "terminal.activity_tape"),
            }

        before = snapshot()

        gdelt = _run_gdelt_news(conn, gdelt_limit, validate)
        conn.commit()

        if attention_names is None:
            attention_names = _default_attention_names(conn)
        attention = _run_wikimedia_attention(conn, attention_names, attention_days, validate)
        conn.commit()

        ticketmaster = _run_ticketmaster(conn, ticketmaster_markets, validate)
        conn.commit()

        news_tape = derive_news_tape_entries(conn)
        new_news_tape = insert_tape_entries(conn, news_tape)
        event_tape = derive_provider_event_tape_entries(conn)
        new_event_tape = insert_tape_entries(conn, event_tape)
        conn.commit()

        after = snapshot()

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "before": before,
            "after": after,
            "gdelt": gdelt,
            "attention": attention,
            "ticketmaster": ticketmaster,
            "activity_tape": {
                "news_derived": len(news_tape),
                "news_new_rows": new_news_tape,
                "event_derived": len(event_tape),
                "event_new_rows": new_event_tape,
            },
            "news_entities_covered": _count(
                conn, "SELECT COUNT(DISTINCT entity_id) FROM terminal.news_mentions"
            ),
            "artists_with_attention": _count(
                conn,
                "SELECT COUNT(DISTINCT artist_key) FROM metrics.artist_attention_observations WHERE status='ok'",
            ),
            "partition_completeness": _partition_completeness(conn),
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


def _default_attention_names(conn, limit: int = 30) -> list[str]:
    """Resolved EXACT Spotify artists first, then box-office artists (bounded)."""
    names: list[str] = []
    seen: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT DISTINCT local_artist_name FROM identity.spotify_artist_resolutions "
            "WHERE resolution_status = 'EXACT' ORDER BY local_artist_name"
        ).fetchall()
        for (name,) in rows:
            key = normalize_name(name)
            if key and key not in seen:
                seen.add(key)
                names.append(name)
                if len(names) >= limit:
                    return names
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT DISTINCT artist FROM research.canonical_boxoffice_engagements "
            "WHERE artist IS NOT NULL AND artist <> '' ORDER BY artist"
        ).fetchall()
        for (name,) in rows:
            key = normalize_name(name)
            if key and key not in seen:
                seen.add(key)
                names.append(name)
                if len(names) >= limit:
                    return names
    except Exception:
        pass
    return names


def _partition_completeness(conn) -> dict[str, Any]:
    total = _count(conn, "SELECT COUNT(*) FROM terminal.acquisition_partitions WHERE provider='ticketmaster'")
    complete = _count(
        conn,
        "SELECT COUNT(*) FROM terminal.acquisition_partitions "
        "WHERE provider='ticketmaster' AND status='COMPLETE'",
    )
    truncated = _count(
        conn,
        "SELECT COUNT(*) FROM terminal.acquisition_partitions "
        "WHERE provider='ticketmaster' AND truncated = TRUE",
    )
    return {
        "total": total,
        "complete": complete,
        "truncated": truncated,
        "complete_pct": (100.0 * complete / total) if total else None,
    }


if __name__ == "__main__":
    result = run_data_fabric_oa()
    print(json.dumps(result, indent=2, default=str))
