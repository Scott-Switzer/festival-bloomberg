"""LIVE_DATA_ACTIVATION_AND_INTELLIGENCE_SCALE_V1 — live operational acceptance.

Turns already-validated providers into persisted, source-backed data:

1. **Spotify identity resolution** (bounded): search the Web API for the
   festival-seed + box-office + forward artist names, classify each candidate
   deterministically (EXACT / HIGH_CONFIDENCE / AMBIGUOUS / NO_MATCH), and
   persist append-only resolution rows. Only EXACT normalized-name matches are
   additionally written to ``core.entity_external_ids``. Nothing is merged on
   string similarity alone.
2. **Ticketmaster** (bounded): validate auth and run a few US market
   partitions filtered to Music. Every event snapshot (status, public onsale,
   presales, price range, promoter, classification, venue coords) is appended
   to ``events.provider_event_snapshots``.
3. **NWS weather** (bounded): fetch a forecast for future US events that have
   coordinates and persist ``events.weather_forecast_snapshots`` (generation
   time kept separate from the validity window).
4. **Activity tape**: derive EVENT_DISCOVERED / ONSALE_DISCOVERED /
   PRESALE_DISCOVERED / PRICE_RANGE_DISCOVERED / PROMOTER_IDENTIFIED and
   cancellation/postponement/reschedule transitions from the snapshots.

No secret value is ever written to the report.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionRequest, content_hash_of, utc_now
from ..acquisition.providers.spotify import SpotifyProvider
from ..acquisition.providers.ticketmaster import TicketmasterProvider
from ..acquisition.providers.nws import NwsProvider
from ..acquisition.transport import UrllibTransport
from ..identity.spotify import (
    build_rows,
    normalize_name,
    persist_exact_external_ids,
    persist_resolutions,
)
from ..product.workflow import complete_acquisition_run, start_acquisition_run
from ..intelligence.tape import (
    derive_provider_event_tape_entries,
    insert_tape_entries,
)
from ..localenv import load_local_env
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "live_data_activation_v1"

#: Bounded US market partitions (city, state) for the Ticketmaster music sweep.
DEFAULT_MARKETS: tuple[tuple[str, str], ...] = (
    ("Chicago", "IL"),
    ("Los Angeles", "CA"),
    ("New York", "NY"),
    ("Austin", "TX"),
    ("Nashville", "TN"),
)


def _count(conn, sql: str, params: list[Any] | None = None) -> int:
    try:
        return int(conn.execute(sql, params or []).fetchone()[0])
    except Exception:
        return 0


def _table_rows(conn, table: str) -> int:
    try:
        return _count(conn, f"SELECT COUNT(*) FROM {table}")
    except Exception:
        return 0


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _collect_resolution_names(conn, limit: int) -> list[tuple[str, str]]:
    """Distinct artist names to resolve, ordered: festival seed, box-office, forward."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for table, col in (
        ("core.lineup_slots", "artist_name"),
        ("research.canonical_boxoffice_engagements", "artist"),
        ("flywheel.forward_watch_events", "artist_name"),
    ):
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL AND {col} <> '' ORDER BY 1"
            ).fetchall()
        except Exception:
            continue
        for (name,) in rows:
            norm = normalize_name(name)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append((table, name))
            if len(out) >= limit:
                return out
    return out


def _candidate_from_record(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec.get("spotify_id") or rec.get("platform_object_id"),
        "name": rec.get("name"),
        "uri": rec.get("uri"),
        "external_urls": rec.get("external_urls"),
    }


def _run_spotify_resolution(conn, limit: int, validate: bool) -> dict[str, Any]:
    provider = SpotifyProvider(transport=UrllibTransport())
    if not provider._has_credentials():
        return {"status": "NOT_CONFIGURED", "resolutions": 0}
    names = _collect_resolution_names(conn, limit)
    summary = {
        "status": "RUNNING",
        "names_attempted": 0,
        "names_resolved": 0,
        "searches": 0,
        "rate_limited": 0,
        "provider_errors": 0,
        "resolution_rows": 0,
        "exact": 0,
        "high_confidence": 0,
        "ambiguous": 0,
        "no_match": 0,
        "external_ids": 0,
    }
    if not validate:
        summary["status"] = "SKIPPED"
        summary["names_available"] = len(names)
        return summary

    for source_table, name in names:
        req = AcquisitionRequest.new(
            entity_id=normalize_name(name),
            entity_type="artist",
            platform="spotify",
            query=name,
            max_records=5,
            commercial_context="research",
        )
        result = provider.acquire(req)
        if result.status.value == "NOT_CONFIGURED":
            summary["status"] = "NOT_CONFIGURED"
            break
        if result.status.value == "RATE_LIMITED":
            summary["rate_limited"] += 1
            summary["status"] = "RATE_LIMITED_STOPPED"
            break
        if result.status.value in ("PROVIDER_ERROR", "SCHEMA_INVALID", "TIMEOUT"):
            summary["provider_errors"] += 1
            summary["status"] = f"STOPPED_{result.status.value}"
            break
        summary["searches"] += 1
        summary["names_attempted"] += 1
        candidates = [_candidate_from_record(r) for r in result.records]
        rows = build_rows(source_table, name, candidates, result.completed_at.isoformat())
        summary["resolution_rows"] += persist_resolutions(conn, rows)
        summary["external_ids"] += persist_exact_external_ids(conn, rows)
        for r in rows:
            s = r["resolution_status"]
            if s == "EXACT":
                summary["exact"] += 1
            elif s == "HIGH_CONFIDENCE":
                summary["high_confidence"] += 1
            elif s == "AMBIGUOUS":
                summary["ambiguous"] += 1
            else:
                summary["no_match"] += 1
        if any(r["resolution_status"] in ("EXACT", "HIGH_CONFIDENCE") for r in rows):
            summary["names_resolved"] += 1
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    return summary


def _persist_event_snapshot(conn, rec: dict[str, Any], retrieved_at: str,
                            acquisition_run_id: str | None = None) -> bool:
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
             software_version, acquisition_run_id, ingested_at)
        VALUES (?, 'ticketmaster',
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, 'RESEARCH_ONLY',
                'PROTOTYPE_ONLY', ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            snapshot_key,          # 1  snapshot_key
            platform_object_id,    # 2  platform_object_id
            rec.get("event_name"), # 3  event_name
            artist_name,           # 4  artist_name
            json.dumps(attractions, default=str),  # 5 attractions
            rec.get("ticketmaster_venue_id"),      # 6  venue_id
            rec.get("venue_name"), # 7  venue_name
            rec.get("city"),       # 8  city
            rec.get("state_code"), # 9  state_code
            rec.get("country_code"),  # 10 country_code
            rec.get("latitude"),   # 11 latitude
            rec.get("longitude"),  # 12 longitude
            rec.get("local_date"), # 13 local_date
            rec.get("local_time"), # 14 local_time
            rec.get("event_time"), # 15 event_time
            rec.get("timezone"),   # 16 timezone
            rec.get("event_status"),   # 17 event_status
            rec.get("onsale_start"),   # 18 onsale_start
            rec.get("onsale_end"),     # 19 onsale_end
            json.dumps(rec.get("presales") or [], default=str),  # 20 presales
            rec.get("price_min"),   # 21 price_min
            rec.get("price_max"),   # 22 price_max
            rec.get("price_currency"),  # 23 price_currency
            rec.get("price_type"),  # 24 price_type
            rec.get("promoter"),    # 25 promoter
            classifications.get("segment"),   # 26 segment
            classifications.get("genre"),     # 27 genre
            classifications.get("subgenre"),  # 28 subgenre
            rec.get("event_type"),  # 29 event_type
            rec.get("canonical_url"),  # 30 canonical_url
            retrieved_at,           # 31 retrieved_at
            retrieved_at,           # 32 knowledge_time
            rec.get("content_hash"),  # 33 content_hash
            rec.get("content_hash"),  # 34 raw_payload_hash
            SOFTWARE_VERSION,       # 35 software_version
            acquisition_run_id,     # 36 acquisition_run_id
        ],
    )
    return True


def _run_ticketmaster(
    conn,
    markets: tuple[tuple[str, str], ...],
    validate: bool,
    transport=None,
) -> dict[str, Any]:
    # Injectable transport: production uses UrllibTransport(), offline
    # parity/experiment runs inject a scripted FakeTransport. Providers never
    # talk to the network directly (see acquisition/transport.py).
    provider = TicketmasterProvider(transport=transport or UrllibTransport())
    summary = {
        "status": "RUNNING",
        "configured": provider.configured(),
        "partitions": 0,
        "requests": 0,
        "events_received": 0,
        "events_persisted": 0,
        "rate_limited": 0,
        "provider_errors": 0,
        "with_onsale": 0,
        "with_presale": 0,
        "with_price": 0,
        "with_promoter": 0,
        "distinct_events": 0,
    }
    if not provider.configured():
        summary["status"] = "NOT_CONFIGURED"
        return summary
    if not validate:
        summary["status"] = "SKIPPED"
        return summary

    # Every snapshot in this pass belongs to one explicit logical acquisition
    # run (migration 030) so the alert engine compares runs, not timestamps.
    run_id = start_acquisition_run(conn, provider="ticketmaster", operation="national_music_events_refresh")
    summary["acquisition_run_id"] = run_id

    for city, state in markets:
        req = AcquisitionRequest.new(
            entity_id=city.lower(),
            entity_type="market",
            platform="ticketmaster",
            query="",
            market_id=f"{city},{state},US",
            classification_name="Music",
            max_records=40,
            operation="SEARCH_EVENTS",
            commercial_context="research",
            start_time=utc_now(),
        )
        result = provider.acquire(req)
        meta = result.provider_metadata or {}
        summary["partitions"] += 1
        summary["requests"] += int(meta.get("pagination", {}).get("pages_fetched", 1) or 1)
        if result.status.value == "NOT_CONFIGURED":
            summary["status"] = "NOT_CONFIGURED"
            break
        if result.status.value == "RATE_LIMITED":
            summary["rate_limited"] += 1
            summary["status"] = "RATE_LIMITED_STOPPED"
            break
        if result.status.value == "PROVIDER_ERROR":
            summary["provider_errors"] += 1
            summary["status"] = f"STOPPED_{result.status.value}"
            break
        summary["events_received"] += result.record_count
        for rec in result.records:
            if _persist_event_snapshot(conn, rec, result.completed_at.isoformat(),
                                       acquisition_run_id=run_id):
                summary["events_persisted"] += 1
                if rec.get("onsale_start"):
                    summary["with_onsale"] += 1
                if rec.get("presales"):
                    summary["with_presale"] += 1
                if rec.get("price_min") is not None or rec.get("price_max") is not None:
                    summary["with_price"] += 1
                if rec.get("promoter"):
                    summary["with_promoter"] += 1
    summary["distinct_events"] = _count(
        conn, "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"
    )
    if summary["status"] == "RUNNING":
        summary["status"] = "COMPLETE"
    complete_acquisition_run(
        conn, run_id=run_id, status=summary["status"],
        request_count=summary["requests"], record_count=summary["events_received"],
        error_count=summary["provider_errors"],
        note=f"events_persisted={summary['events_persisted']}",
    )
    return summary


def _run_weather(conn, limit: int, validate: bool) -> dict[str, Any]:
    summary = {"status": "RUNNING", "events_attempted": 0, "events_with_forecast": 0, "periods_persisted": 0}
    if not validate:
        summary["status"] = "SKIPPED"
        return summary
    provider = NwsProvider(transport=UrllibTransport())
    # Future US events with coordinates, newest first, bounded.
    try:
        rows = conn.execute(
            """
            SELECT platform_object_id, latitude, longitude, local_date
            FROM events.provider_event_snapshots
            WHERE provider = 'ticketmaster'
              AND country_code = 'US'
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
              AND TRY_CAST(local_date AS DATE) >= CURRENT_DATE
            GROUP BY platform_object_id, latitude, longitude, local_date
            ORDER BY local_date ASC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
    except Exception:
        rows = []
    for (event_ref, lat, lon, _ldate) in rows:
        summary["events_attempted"] += 1
        try:
            req = AcquisitionRequest.new(
                entity_id=str(event_ref),
                entity_type="event",
                platform="nws",
                query=f"{lat},{lon}",
                commercial_context="research",
            )
            result = provider.acquire(req)
        except Exception:
            continue
        if not result.is_success:
            continue
        got = False
        for rec in result.records:
            gen = _parse_ts(rec.get("generation_time"))
            if gen is None:
                continue
            forecast_key = content_hash_of({"event": event_ref, "generation_time": gen.isoformat()})
            exists = conn.execute(
                "SELECT 1 FROM events.weather_forecast_snapshots WHERE forecast_key = ?",
                [forecast_key],
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO events.weather_forecast_snapshots
                    (forecast_key, event_ref, venue_latitude, venue_longitude,
                     generation_time, valid_start, valid_end, temperature,
                     temperature_unit, precipitation_probability, wind_speed,
                     short_forecast, source_url, retrieved_at, knowledge_time,
                     rights_status, commercial_use_status, software_version, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PUBLIC_DOMAIN',
                        'RESEARCH_ONLY', ?, CURRENT_TIMESTAMP)
                """,
                [
                    forecast_key, str(event_ref), lat, lon, gen,
                    _parse_ts(rec.get("valid_start")), _parse_ts(rec.get("valid_end")),
                    rec.get("temperature"), rec.get("temperature_unit"),
                    rec.get("precipitation_probability"), rec.get("wind_speed"),
                    rec.get("short_forecast"), rec.get("source_url"),
                    rec.get("retrieved_at"), rec.get("knowledge_time"),
                    SOFTWARE_VERSION,
                ],
            )
            summary["periods_persisted"] += 1
            got = True
        if got:
            summary["events_with_forecast"] += 1
    summary["status"] = "COMPLETE"
    return summary


def run_live_data_activation_oa(
    *,
    db_path: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/live_data_activation_v1.json",
    validate: bool = True,
    spotify_limit: int = 120,
    ticketmaster_markets: tuple[tuple[str, str], ...] = DEFAULT_MARKETS,
    weather_limit: int = 12,
) -> dict[str, Any]:
    load_local_env()
    started = utc_now()
    oa_run_id = f"live_activation_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(db_path)
    try:
        from ..events.repository import EventRepository

        EventRepository(repo.conn)  # applies pending migrations (incl. 024)
        conn = repo.conn

        def snapshot() -> dict[str, int]:
            return {
                "spotify_resolutions": _table_rows(conn, "identity.spotify_artist_resolutions"),
                "spotify_external_ids": _count(
                    conn, "SELECT COUNT(*) FROM core.entity_external_ids WHERE id_type='spotify'"
                ),
                "provider_event_snapshots": _table_rows(conn, "events.provider_event_snapshots"),
                "weather_forecasts": _table_rows(conn, "events.weather_forecast_snapshots"),
                "activity_tape": _table_rows(conn, "terminal.activity_tape"),
            }

        before = snapshot()

        spotify = _run_spotify_resolution(conn, spotify_limit, validate)
        conn.commit()
        ticketmaster = _run_ticketmaster(conn, ticketmaster_markets, validate)
        conn.commit()
        weather = _run_weather(conn, weather_limit, validate)
        conn.commit()

        tape_rows = derive_provider_event_tape_entries(conn)
        new_tape = insert_tape_entries(conn, tape_rows)
        conn.commit()

        after = snapshot()

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "before": before,
            "after": after,
            "spotify": spotify,
            "ticketmaster": ticketmaster,
            "weather": weather,
            "activity_tape": {
                "derived_rows": len(tape_rows),
                "new_rows": new_tape,
            },
            "artists_with_spotify_external_id": _count(
                conn, "SELECT COUNT(DISTINCT entity_key) FROM core.entity_external_ids WHERE id_type='spotify'"
            ),
            "artists_with_exact_spotify_resolution": _count(
                conn,
                "SELECT COUNT(DISTINCT normalized_local_name) FROM identity.spotify_artist_resolutions "
                "WHERE resolution_status='EXACT'",
            ),
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_live_data_activation_oa()
    print(json.dumps(result, indent=2, default=str))
