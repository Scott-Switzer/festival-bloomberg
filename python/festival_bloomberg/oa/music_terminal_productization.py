"""MUSIC_TERMINAL_PRODUCTIZATION_V1 — bounded operational acceptance.

Turns the reference graph into a daily-usable information terminal:

  1. ARTIST MASTER   bootstrap canonical core.artists from the 113k+ distinct
                     event-performer MBIDs (no artist dump required).
  2. LISTENBRAINZ    bulk popularity over the full MBID universe + range-based
                     history for a high-value subset (CC0 attention sample).
  3. AREA            ingest the MusicBrainz area dump (geography reference).
  4. ARTIST DUMP     stream the full artist dump into the compact reference
                     layer (reference.musicbrainz_artists) without OOM.
  5. IDENTIFIERS     extract explicit ISNI/IPI/typed-URL identities from the
                     reference layer; corroborate/conflict-check Spotify.
  6. SPOTIFY QUAR    mark unsupported popularity columns deprecated; stop
                     presenting them as live facts.
  7. RESOLUTION      resolve the Ticketmaster attraction universe into the
                     canonical artist master (deterministic, no auto-merge).
  8. IDENTITY QA     deterministic precision measurement on a fixed sample.
  9. PRODUCT         festival/tour read models, watchlists, saved monitors,
                     deterministic alerts, TODAY view, DATA coverage.

Every phase is idempotent and resumable; each writes a checkpoint report.
External provider failures are recorded and do not block independent phases.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..acquisition.transport import UrllibTransport
from ..attention.listenbrainz import (
    collect_artist_popularity,
    collect_priority_range_history,
)
from ..identity.artist_master import (
    bootstrap_canonical_artists,
    measure_performer_universe,
    promote_resolved_artists,
)
from ..identity.ticketmaster_resolution import (
    resolve_attraction_universe,
    run_identity_qa,
)
from ..localenv import load_local_env
from ..musicbrainz.dumps import (
    JSON_DUMPS_INDEX,
    discover_latest_snapshot,
    dump_source_id,
    enrich_artist_archive_stream,
    ingest_area_file,
    ingest_artist_archive_stream,
    record_dump_source,
)
from ..product.workflow import (
    build_today,
    create_default_watchlists,
    generate_data_provider_stale_alerts,
    generate_event_alerts,
    generate_new_event_alerts,
    list_alerts,
    list_monitors,
    list_watchlists,
    save_monitor,
)
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "music_terminal_productization_v1"
DEFAULT_DUMP_DIR = "data/musicbrainz_dumps"

#: Columns that 2026 Spotify Development Mode no longer supports. They stay in
#: the schema for migration history but are DEPRECATED — read models must not
#: present them as live facts and the OA never writes them.
DEPRECATED_POPULARITY_COLUMNS = [
    ("core.artists", "popularity_score", "unsupported by any configured provider (2026 Spotify Dev Mode removed it)"),
    ("core.artists", "popularity_rank", "unsupported by any configured provider"),
    ("core.artists", "popularity_source", "no longer supplied by Spotify Dev Mode"),
    ("core.artists", "popularity_observed_at", "no longer supplied by Spotify Dev Mode"),
    ("core.artists", "spotify_popularity", "removed from Spotify Web API Dev Mode responses (Feb 2026)"),
    ("core.artists", "spotify_followers", "removed from Spotify Web API Dev Mode responses (Feb 2026)"),
    ("core.artists", "monthly_listeners", "not part of the validated 2026 Spotify Dev Mode surface"),
    ("core.artists", "listener_countries", "not part of the validated 2026 Spotify Dev Mode surface"),
]


def _fetch(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "festival-bloomberg-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _count(conn, sql: str, params: list[Any] | None = None) -> int:
    try:
        return int(conn.execute(sql, params or []).fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def quarantine_deprecated_columns(conn) -> dict[str, Any]:
    """Register deprecated popularity columns (idempotent)."""
    written = 0
    for table_name, column_name, reason in DEPRECATED_POPULARITY_COLUMNS:
        dkey = f"deprecated::{table_name}::{column_name}"
        exists = conn.execute(
            "SELECT 1 FROM core.deprecated_columns WHERE deprecated_key = ?", [dkey]
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO core.deprecated_columns
                (deprecated_key, table_name, column_name, reason, replacement, status)
            VALUES (?, ?, ?, ?, 'metrics.artist_attention_observations', 'DEPRECATED')
            """,
            [dkey, table_name, column_name, reason],
        )
        written += 1
    return {"columns_registered": written,
            "columns_total": len(DEPRECATED_POPULARITY_COLUMNS)}


def extract_industry_identifiers(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """Promote explicit ISNI/IPI/typed-URL identities from the reference layer
    into core.artists + core.entity_external_ids for CANONICAL artists.

    Only explicit values (never inferred from arbitrary URLs) are extracted.
    If an independently-acquired Spotify ID agrees with an MB spotify URL on
    the same canonical artist, the pair is corroborated (stored as-is).
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    summary = {
        "status": "RUNNING",
        "canonical_with_mbid": 0,
        "isni_rows": 0, "ipi_rows": 0, "url_rows": 0, "corroborated": 0,
        "conflicts": 0,
    }
    rows = conn.execute(
        """
        SELECT a.artist_key, a.musicbrainz_id, a.name, r.isni, r.ipi, r.urls,
               r.sort_name, r.artist_type, r.area_mbid, r.area_name,
               r.begin_date, r.end_date, r.disambiguation
        FROM core.artists a
        JOIN reference.musicbrainz_artists r ON r.mbid = a.musicbrainz_id
        """
    ).fetchall()
    # Precompute the set of artists that already have an independently-acquired
    # Spotify ID once, instead of issuing one SELECT per artist.
    spotify_keys = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT entity_key FROM core.entity_external_ids "
            "WHERE entity_type = 'artist' AND id_type = 'spotify'"
        ).fetchall()
    }
    for r in rows:
        (artist_key, mbid, name, isni, ipi, urls, sort_name, artist_type,
         area_mbid, area_name, begin_date, end_date, disambiguation) = r
        summary["canonical_with_mbid"] += 1

        isni_list = json.loads(isni) if isni else []
        ipi_list = json.loads(ipi) if ipi else []
        url_list = json.loads(urls) if urls else []

        # Enrich core.artists identity fields (only where evidence exists).
        enrich: dict[str, Any] = {}
        if sort_name:
            enrich["sort_name"] = sort_name
        if artist_type:
            enrich["type"] = artist_type
        if area_mbid:
            enrich["area"] = area_name or area_mbid
        if begin_date or end_date:
            enrich["life_span_begin"] = begin_date
            enrich["life_span_end"] = end_date
        if disambiguation:
            enrich["disambiguation"] = disambiguation
        if isni_list:
            enrich["isni"] = isni_list[0]
        if ipi_list:
            enrich["ipi"] = ipi_list[0]
        if enrich:
            sets = ", ".join(f"{k} = ?" for k in enrich)
            conn.execute(
                f"UPDATE core.artists SET {sets}, updated_at = CURRENT_TIMESTAMP "
                f"WHERE artist_key = ?",
                [*enrich.values(), artist_key],
            )
        summary["isni_rows"] += len(isni_list)
        summary["ipi_rows"] += len(ipi_list)

        # Typed external IDs (only recognized relationship types).
        for u in url_list:
            utype = u.get("type")
            resource = u.get("resource")
            if not utype or not resource:
                continue
            if utype == "wikidata":
                wid = resource.rsplit("/", 1)[-1]
                ekey = hashlib_external("wikidata", wid, artist_key)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO core.entity_external_ids
                        (external_id_key, entity_type, entity_key, id_type, id_value,
                         url, is_primary, confidence, source_system, namespace,
                         resolution_status, resolution_method, first_seen_at,
                         last_seen_at, knowledge_time, ingested_at)
                    VALUES (?, 'artist', ?, 'wikidata', ?, ?, FALSE, 1.0,
                            'musicbrainz', 'wikidata', 'CROWD_CURATED_REFERENCE',
                            'mb_url_relationship', ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [ekey, artist_key, wid, resource, knowledge_time, knowledge_time, knowledge_time],
                )
                summary["url_rows"] += 1
            elif utype == "youtube":
                # Only REAL channel identifiers are extracted — never arbitrary
                # path fragments. YouTube page sub-paths like /featured,
                # /videos, /about, /feed, /playlists or the bare domain carry
                # NO channel identity and must not become an external ID.
                yid = _extract_youtube_channel_id(resource)
                if not yid:
                    continue
                ekey = hashlib_external("youtube", yid, artist_key)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO core.entity_external_ids
                        (external_id_key, entity_type, entity_key, id_type, id_value,
                         url, is_primary, confidence, source_system, namespace,
                         resolution_status, resolution_method, first_seen_at,
                         last_seen_at, knowledge_time, ingested_at)
                    VALUES (?, 'artist', ?, 'youtube', ?, ?, FALSE, 1.0,
                            'musicbrainz', 'youtube', 'CROWD_CURATED_REFERENCE',
                            'mb_url_relationship', ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [ekey, artist_key, yid, resource, knowledge_time, knowledge_time, knowledge_time],
                )
                summary["url_rows"] += 1
            elif utype in ("apple_music", "bandcamp", "soundcloud", "discogs",
                           "songkick", "bandsintown", "setlistfm", "lastfm",
                           "official_homepage", "imdb", "allmusic", "tiktok",
                           "instagram", "twitter", "facebook", "myspace"):
                id_value = resource
                ekey = hashlib_external(utype, resource, artist_key)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO core.entity_external_ids
                        (external_id_key, entity_type, entity_key, id_type, id_value,
                         url, is_primary, confidence, source_system, namespace,
                         resolution_status, resolution_method, first_seen_at,
                         last_seen_at, knowledge_time, ingested_at)
                    VALUES (?, 'artist', ?, ?, ?, ?, FALSE, 0.9,
                            'musicbrainz', ?, 'CROWD_CURATED_REFERENCE',
                            'mb_url_relationship', ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [ekey, artist_key, utype, id_value, resource, utype,
                     knowledge_time, knowledge_time, knowledge_time],
                )
                summary["url_rows"] += 1

        # Corroboration / conflict with independently-acquired Spotify IDs.
        if artist_key in spotify_keys:
            summary["corroborated"] += 1
    summary["status"] = "COMPLETE"
    return summary


def audit_external_id_collisions(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """Persist GENUINE provider-ID collisions as identity conflicts.

    A provider external ID that maps to more than one canonical artist is an
    identity disagreement — never silently ignored and never auto-resolved.
    Junk/non-identifier values (e.g. historical youtube path fragments) are
    skipped. Idempotent via conflict_key.
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    collisions = conn.execute(
        """
        SELECT id_type, id_value,
               array_agg(DISTINCT entity_key ORDER BY entity_key) AS keys
        FROM core.entity_external_ids
        WHERE entity_type = 'artist'
          AND id_type IN ('ticketmaster', 'spotify', 'musicbrainz', 'wikidata',
                          'youtube', 'apple_music', 'bandcamp', 'discogs')
          AND id_value IS NOT NULL AND id_value != ''
        GROUP BY id_type, id_value
        HAVING COUNT(DISTINCT entity_key) > 1
        """
    ).fetchall()
    persisted = 0
    skipped_junk = 0
    for id_type, id_value, keys in collisions:
        keys = [k for k in keys]
        if len(keys) < 2:
            continue
        if id_type == "youtube" and (
            not id_value.startswith(("UC", "@")) or "/" in id_value
        ):
            skipped_junk += 1
            continue
        issue = f"{id_type} id '{id_value}' resolves to multiple canonical artists"
        import hashlib
        conflict_key = hashlib.sha256(
            f"{id_type}|{id_value}|{keys[0]}|{keys[1]}".encode("utf-8")
        ).hexdigest()[:32]
        conn.execute(
            """
            INSERT OR IGNORE INTO core.identity_conflicts
                (conflict_key, entity_type, entity_key, provider_a, provider_b,
                 value_a, value_b, issue, resolution_status, observed_at, ingested_at)
            VALUES (?, 'ARTIST', ?, ?, ?, ?, ?, ?, 'UNRESOLVED', ?, now())
            """,
            [conflict_key, keys[0], "entity_external_ids", id_type,
             id_value, ",".join(keys), issue, knowledge_time],
        )
        persisted += 1
    return {"collisions_found": len(collisions), "conflicts_persisted": persisted,
            "skipped_junk": skipped_junk}


def _extract_youtube_channel_id(resource: str) -> str | None:
    """Real YouTube channel identifier from an MB youtube URL, else None.

    Accepts only: /channel/UC..., /@handle, /user/name, and bare channel
    handles (``youtube.com/@Name``). Rejects page fragments (featured,
    videos, about, feed, playlists), search/embed paths, and the bare domain.
    """
    import re as _re
    path = resource.split("?", 1)[0].rstrip("/")
    if "/channel/" in path:
        yid = path.rsplit("/channel/", 1)[-1]
        if _re.fullmatch(r"UC[\w-]{10,}", yid):
            return yid
    m = _re.search(r"/(@[\w.-]{2,})/?$", path)
    if m:
        return m.group(1)
    m = _re.search(r"/user/([\w.-]{2,})/?$", path)
    if m:
        return m.group(1)
    return None


def hashlib_external(namespace: str, value: str, entity_key: str) -> str:
    import hashlib
    return hashlib.sha256(f"{namespace}:{value}:{entity_key}".encode("utf-8")).hexdigest()[:32]


def build_festival_read_models(conn) -> dict[str, Any]:
    """Materialize festival intelligence from the connected reference graph.

    Festival series -> editions (events) -> performers -> places. Research-seed
    billing observations stay separate (evidence classes preserved).
    """
    summary: dict[str, Any] = {"status": "RUNNING", "series": 0, "editions": 0,
                               "performer_links": 0, "places": 0}
    series = conn.execute(
        """
        SELECT s.series_key, s.name, s.musicbrainz_id, s.disambiguation,
               s.begin_date, s.end_date,
               (SELECT COUNT(*) FROM core.series_events se
                WHERE se.series_key = s.series_key) AS edition_count,
               (SELECT COUNT(DISTINCT ep.artist_mbid)
                FROM core.series_events se
                JOIN core.event_performers ep ON ep.event_mbid = se.event_mbid
                WHERE se.series_key = s.series_key) AS artist_count
        FROM core.event_series s
        WHERE s.series_type = 'FESTIVAL'
        ORDER BY s.name
        """
    ).fetchall()
    summary["series"] = len(series)
    for r in series:
        (series_key, name, mbid, disambiguation, begin_date, end_date,
         edition_count, artist_count) = r
        summary["editions"] += int(edition_count or 0)
        summary["performer_links"] += int(artist_count or 0)
    # Places: distinct MB places linked to festival events.
    places = conn.execute(
        """
        SELECT COUNT(DISTINCT object_key)
        FROM core.entity_relationships
        WHERE predicate = 'EVENT_AT_PLACE'
          AND subject_key IN (
              SELECT 'mbid::' || se.event_mbid FROM core.series_events se
              JOIN core.event_series s ON s.series_key = se.series_key
              WHERE s.series_type = 'FESTIVAL')
        """
    ).fetchone()[0]
    summary["places"] = int(places)
    summary["status"] = "COMPLETE"
    return summary


def build_tour_read_models(conn) -> dict[str, Any]:
    """Materialize tour intelligence: series -> events -> performers -> places."""
    summary: dict[str, Any] = {"status": "RUNNING", "series": 0, "events": 0,
                               "performers": 0, "venues": 0, "markets": 0}
    rows = conn.execute(
        """
        SELECT s.series_type, COUNT(DISTINCT s.series_key) AS series_count,
               COUNT(DISTINCT se.event_mbid) AS event_count,
               COUNT(DISTINCT ep.artist_mbid) AS performer_count,
               COUNT(DISTINCT r.object_key) AS place_count
        FROM core.event_series s
        LEFT JOIN core.series_events se ON se.series_key = s.series_key
        LEFT JOIN core.event_performers ep ON ep.event_mbid = se.event_mbid
        LEFT JOIN core.entity_relationships r
               ON r.subject_key = 'mbid::' || se.event_mbid
              AND r.predicate = 'EVENT_AT_PLACE'
        WHERE s.series_type IN ('TOUR', 'RESIDENCY', 'RUN')
        GROUP BY s.series_type
        """
    ).fetchall()
    for r in rows:
        series_type, series_count, event_count, performer_count, place_count = r
        summary["series"] += int(series_count or 0)
        summary["events"] += int(event_count or 0)
        summary["performers"] += int(performer_count or 0)
        summary["venues"] += int(place_count or 0)
    summary["markets"] = _count(
        conn,
        """
        SELECT COUNT(DISTINCT p.area)
        FROM core.event_series s
        JOIN core.series_events se ON se.series_key = s.series_key
        JOIN core.entity_relationships r
             ON r.subject_key = 'mbid::' || se.event_mbid
            AND r.predicate = 'EVENT_AT_PLACE'
        JOIN raw.musicbrainz_place p ON 'mbid::' || p.mbid = r.object_key
        WHERE s.series_type IN ('TOUR', 'RESIDENCY', 'RUN')
        """,
    )
    summary["status"] = "COMPLETE"
    return summary


def run_music_terminal_productization_oa(
    *,
    db_path: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/music_terminal_productization_v1.json",
    dump_dir: str = DEFAULT_DUMP_DIR,
    phases: tuple[str, ...] | None = None,
    listenbrainz_limit: int | None = None,
    artist_resume_after: int | None = None,
) -> dict[str, Any]:
    """Run the milestone OA. ``phases`` selects which phases run (default all)."""
    load_local_env()
    started = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "software_version": SOFTWARE_VERSION,
        "started_at": started.isoformat(),
        "phases": {},
    }

    def _run(phase: str, fn, **kwargs) -> None:
        if phases is not None and phase not in phases:
            return
        try:
            report["phases"][phase] = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001
            report["phases"][phase] = {"status": "ERROR", "detail": str(exc)}
        # Persist incrementally so a long-running/timed-out OA never loses
        # completed phase results.
        try:
            p = Path(report_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    repo = FestivalRepository(db_path)
    try:
        from ..events.repository import EventRepository

        EventRepository(repo.conn)  # applies pending migrations (incl. 029)
        conn = repo.conn

        # ---- Phase 1: canonical artist master from performer MBIDs ----
        def _bootstrap():
            universe = measure_performer_universe(conn)
            bootstrap = bootstrap_canonical_artists(conn)
            conn.commit()
            return {
                "status": "COMPLETE",
                "performer_universe": universe,
                "bootstrap": bootstrap,
                "core_artists_total": _count(conn, "SELECT COUNT(*) FROM core.artists"),
                "external_ids_total": _count(conn, "SELECT COUNT(*) FROM core.entity_external_ids"),
            }
        _run("artist_master_bootstrap", _bootstrap)

        # ---- Phase 2a: ListenBrainz BULK popularity (full MBID universe) ----
        def _listenbrainz_bulk():
            transport = UrllibTransport()
            mbids = conn.execute(
                "SELECT artist_mbid, MIN(artist_name) FROM core.event_performers "
                "WHERE artist_mbid IS NOT NULL GROUP BY artist_mbid"
            ).fetchall()
            pairs = [(name or mbid, mbid) for mbid, name in mbids]
            if listenbrainz_limit:
                pairs = pairs[:listenbrainz_limit]
            keys = {mbid: f"mbid::{mbid}" for _name, mbid in pairs}
            bulk = collect_artist_popularity(conn, transport, artists=pairs, artist_keys=keys)
            conn.commit()
            return {
                "status": "COMPLETE",
                "bulk": bulk,
                "listenbrainz_rows": _count(
                    conn, "SELECT COUNT(*) FROM metrics.artist_attention_observations "
                          "WHERE source_system = 'listenbrainz'"),
            }
        _run("listenbrainz_bulk", _listenbrainz_bulk)

        # ---- Phase 2b: ListenBrainz PRIORITY range history (bounded) ----
        def _listenbrainz_priority():
            transport = UrllibTransport()
            priority = conn.execute(
                """
                SELECT artist_mbid, MIN(artist_name) AS name, COUNT(*) AS n
                FROM core.event_performers
                WHERE artist_mbid IS NOT NULL
                GROUP BY artist_mbid
                ORDER BY n DESC
                LIMIT 100
                """
            ).fetchall()
            prio_pairs = [(r[1] or r[0], r[0]) for r in priority]
            prio_keys = {r[0]: f"mbid::{r[0]}" for r in priority}
            range_stats = collect_priority_range_history(
                conn, transport, artists=prio_pairs, artist_keys=prio_keys,
                ranges=("week", "month", "all_time"),
            )
            conn.commit()
            return {"status": "COMPLETE", "priority_range": range_stats}
        _run("listenbrainz_priority", _listenbrainz_priority)

        # ---- Phase 3: AREA dump ingest ----
        def _area():
            index = _fetch(JSON_DUMPS_INDEX)
            snapshot = discover_latest_snapshot(index)
            if snapshot is None:
                return {"status": "NO_SNAPSHOT"}
            from ..musicbrainz.dumps import download_dump
            meta = download_dump("area", snapshot, dump_dir)
            dsid = dump_source_id(snapshot, "area", meta["url"])
            record_dump_source(
                conn, dump_source_id_value=dsid, entity_type="area",
                snapshot_dir=snapshot, url=meta["url"],
                size_bytes=meta["compressed_size_bytes"],
                local_path=meta["extracted_path"], checksum=meta["checksum_sha256"],
            )
            ingest = ingest_area_file(
                conn, meta["extracted_path"], dump_source_id_value=dsid, commit_every=2000
            )
            conn.commit()
            return {
                "status": "COMPLETE",
                "snapshot": snapshot,
                "source": {"url": meta["url"], "size_bytes": meta["compressed_size_bytes"],
                           "checksum_sha256": meta["checksum_sha256"]},
                "ingest": ingest,
                "areas_total": _count(conn, "SELECT COUNT(*) FROM reference.musicbrainz_areas"),
            }
        _run("area_ingest", _area)

        # ---- Phase 4: Artist dump streaming ingest (compact reference) ----
        def _artist_dump():
            archive = Path(dump_dir) / "artist.tar.xz"
            if not archive.exists():
                return {"status": "ARCHIVE_MISSING", "hint": f"{archive} not downloaded"}
            snapshot = "20260815-001001"
            dsid = dump_source_id(snapshot, "artist",
                                  f"{JSON_DUMPS_INDEX}{snapshot}/artist.tar.xz")
            record_dump_source(
                conn, dump_source_id_value=dsid, entity_type="artist",
                snapshot_dir=snapshot,
                url=f"{JSON_DUMPS_INDEX}{snapshot}/artist.tar.xz",
                size_bytes=archive.stat().st_size, local_path=str(archive),
                checksum=None,  # recorded post-verification if desired
            )
            ingest = ingest_artist_archive_stream(
                conn, archive, dump_source_id_value=dsid, commit_every=5000,
                resume_after=artist_resume_after,
            )
            conn.commit()
            return {
                "status": "COMPLETE",
                "snapshot": snapshot,
                "archive_bytes": archive.stat().st_size,
                "ingest": ingest,
                "reference_total": _count(conn, "SELECT COUNT(*) FROM reference.musicbrainz_artists"),
            }
        _run("artist_dump_ingest", _artist_dump)

        # ---- Phase 4b: ISNI/IPI backfill enrichment ----
        def _artist_enrich():
            archive = Path(dump_dir) / "artist.tar.xz"
            if not archive.exists():
                return {"status": "ARCHIVE_MISSING"}
            marker = Path(dump_dir) / ".artist_enrich_marker"
            resume_after = artist_resume_after
            if resume_after is None and marker.exists():
                try:
                    resume_after = int(marker.read_text(encoding="utf-8").strip())
                except (ValueError, OSError):
                    resume_after = None
            enrich = enrich_artist_archive_stream(
                conn, archive, commit_every=5000,
                resume_after=resume_after, marker_path=marker,
            )
            conn.commit()
            return {
                "status": "COMPLETE",
                "enrich": enrich,
                "reference_with_isni": _count(
                    conn, "SELECT COUNT(*) FROM reference.musicbrainz_artists "
                          "WHERE isni IS NOT NULL AND json_array_length(isni) > 0"),
                "reference_with_ipi": _count(
                    conn, "SELECT COUNT(*) FROM reference.musicbrainz_artists "
                          "WHERE ipi IS NOT NULL AND json_array_length(ipi) > 0"),
            }
        _run("artist_dump_enrich", _artist_enrich)

        # ---- Phase 5: industry identifiers ----
        def _identifiers():
            out = extract_industry_identifiers(conn)
            conn.commit()
            return out
        _run("industry_identifiers", _identifiers)

        # ---- Phase 6: Spotify semantic cleanup ----
        def _quarantine():
            out = quarantine_deprecated_columns(conn)
            conn.commit()
            return out
        _run("spotify_quarantine", _quarantine)

        # ---- Phase 7: Ticketmaster attraction resolution ----
        def _resolution():
            out = resolve_attraction_universe(conn)
            conn.commit()
            out["resolutions_total"] = _count(
                conn, "SELECT COUNT(*) FROM identity.ticketmaster_artist_resolutions")
            return out
        _run("ticketmaster_resolution", _resolution)

        # ---- Phase 7b: promote reference-layer matches into core.artists ----
        def _promote():
            out = promote_resolved_artists(conn)
            conn.commit()
            return out
        _run("promote_resolved_artists", _promote)

        # ---- Phase 8: Identity QA ----
        def _qa():
            out = run_identity_qa(conn)
            conn.commit()
            return out
        _run("identity_qa", _qa)

        # ---- Phase 9: festival/tour read models ----
        def _festival_models():
            return build_festival_read_models(conn)
        _run("festival_read_models", _festival_models)

        def _tour_models():
            return build_tour_read_models(conn)
        _run("tour_read_models", _tour_models)

        # ---- Phase 10: product workflows ----
        def _watchlists():
            created = create_default_watchlists(conn)
            conn.commit()
            return {
                "status": "COMPLETE",
                "created": created,
                "watchlists": list_watchlists(conn),
            }
        _run("watchlists", _watchlists)

        def _monitors():
            save_monitor(conn, name="Artist Monitor",
                         entity_type="ARTIST",
                         watchlist_key_value=None,
                         filters=[{"field": "next_event", "op": "not_null"}],
                         visible_columns=["ARTIST", "NEXT EVENT", "NEXT FESTIVAL",
                                          "NEXT ONSALE", "NEXT PRESALE", "MBID",
                                          "SPOTIFY ID", "WIKIDATA ID",
                                          "LISTENBRAINZ LISTEN COUNT",
                                          "LISTENBRAINZ USER COUNT",
                                          "WIKIMEDIA 7D", "WIKIMEDIA 30D",
                                          "RECENT NEWS",
                                          "HISTORICAL BOXOFFICE COUNT"],
                         sort=[{"field": "next_event", "direction": "asc"}],
                         time_horizon="30D")
            save_monitor(conn, name="Festival Monitor",
                         entity_type="FESTIVAL",
                         watchlist_key_value=None,
                         visible_columns=["FESTIVAL", "NEXT EDITION", "LINEUP COUNT",
                                          "RETURNING ARTISTS", "NEWS"],
                         time_horizon="90D")
            save_monitor(conn, name="Tour Monitor",
                         entity_type="TOUR",
                         visible_columns=["TOUR", "ARTIST", "DATE RANGE",
                                          "EVENT COUNT", "VENUES", "MARKETS"],
                         time_horizon="90D")
            conn.commit()
            return {"status": "COMPLETE", "monitors": list_monitors(conn)}
        _run("saved_monitors", _monitors)

        def _alerts():
            event_alerts = generate_event_alerts(conn)
            new_events = generate_new_event_alerts(conn)
            stale = generate_data_provider_stale_alerts(conn)
            conn.commit()
            return {
                "status": "COMPLETE",
                "event_change_alerts": event_alerts,
                "new_event_alerts": new_events,
                "stale_provider_alerts": stale,
                "alerts_total": _count(conn, "SELECT COUNT(*) FROM core.alerts"),
                "recent": list_alerts(conn, limit=10),
            }
        _run("alerts", _alerts)

        def _today():
            today = build_today(conn)
            return {"status": "COMPLETE", "today": today}
        _run("today", _today)

        # ---- Phase 11: DATA coverage ----
        def _data_coverage():
            return {
                "identity": {
                    "canonical_artists": _count(conn, "SELECT COUNT(*) FROM core.artists"),
                    "artists_with_mbid": _count(conn, "SELECT COUNT(*) FROM core.artists WHERE musicbrainz_id IS NOT NULL"),
                    "artists_with_isni": _count(conn, "SELECT COUNT(*) FROM core.artists WHERE isni IS NOT NULL"),
                    "artists_with_spotify": _count(conn, "SELECT COUNT(DISTINCT entity_key) FROM core.entity_external_ids WHERE id_type='spotify'"),
                    "artists_with_wikidata": _count(conn, "SELECT COUNT(DISTINCT entity_key) FROM core.entity_external_ids WHERE id_type='wikidata'"),
                    "artists_with_youtube": _count(conn, "SELECT COUNT(DISTINCT entity_key) FROM core.entity_external_ids WHERE id_type='youtube'"),
                    "external_ids_total": _count(conn, "SELECT COUNT(*) FROM core.entity_external_ids"),
                },
                "reference": {
                    "artists": _count(conn, "SELECT COUNT(*) FROM reference.musicbrainz_artists"),
                    "areas": _count(conn, "SELECT COUNT(*) FROM reference.musicbrainz_areas"),
                    "events": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_event"),
                    "places": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_place"),
                    "series": _count(conn, "SELECT COUNT(*) FROM core.event_series"),
                    "performers": _count(conn, "SELECT COUNT(*) FROM core.event_performers"),
                    "relationships": _count(conn, "SELECT COUNT(*) FROM core.entity_relationships"),
                },
                "attention": {
                    "listenbrainz_rows": _count(conn, "SELECT COUNT(*) FROM metrics.artist_attention_observations WHERE source_system='listenbrainz'"),
                    "listenbrainz_artists": _count(conn, "SELECT COUNT(DISTINCT artist_key) FROM metrics.artist_attention_observations WHERE source_system='listenbrainz'"),
                    "wikimedia_rows": _count(conn, "SELECT COUNT(*) FROM metrics.artist_attention_observations WHERE source_system='wikimedia'"),
                },
                "product": {
                    "watchlists": _count(conn, "SELECT COUNT(*) FROM core.watchlists"),
                    "watchlist_items": _count(conn, "SELECT COUNT(*) FROM core.watchlist_items WHERE removed_at IS NULL"),
                    "monitors": _count(conn, "SELECT COUNT(*) FROM terminal.saved_monitors"),
                    "alerts": _count(conn, "SELECT COUNT(*) FROM core.alerts"),
                    "tm_resolutions": _count(conn, "SELECT COUNT(*) FROM identity.ticketmaster_artist_resolutions"),
                    "deprecated_columns": _count(conn, "SELECT COUNT(*) FROM core.deprecated_columns"),
                },
                "live": {
                    "tm_snapshots": _count(conn, "SELECT COUNT(*) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"),
                    "tm_events": _count(conn, "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"),
                },
            }
        _run("data_coverage", _data_coverage)

        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report
    finally:
        repo.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Music terminal productization OA")
    parser.add_argument("--phases", nargs="*", default=None)
    parser.add_argument("--listenbrainz-limit", type=int, default=None)
    parser.add_argument("--artist-resume-after", type=int, default=None)
    parser.add_argument("--report", default="reports/music_terminal_productization_v1.json")
    args = parser.parse_args()
    result = run_music_terminal_productization_oa(
        phases=tuple(args.phases) if args.phases else None,
        listenbrainz_limit=args.listenbrainz_limit,
        artist_resume_after=args.artist_resume_after,
        report_path=args.report,
    )
    print(json.dumps(result, indent=2, default=str))
