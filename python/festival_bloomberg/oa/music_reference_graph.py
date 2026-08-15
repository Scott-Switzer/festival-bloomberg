"""MUSIC_REFERENCE_GRAPH_AND_PRO_WORKFLOW_V1 — bounded operational acceptance.

Turns the 6,228-series spine from migration 027 into a connected graph by
ingesting the MusicBrainz CC0 ``event`` and ``place`` dumps:

- raw.musicbrainz_event  + typed relations  (event->series "part of",
  event->artist performer roles, event->place, event->subevent, event->url)
- core.series_events     (festival/tour membership)
- core.event_performers  (role semantics preserved)
- raw.musicbrainz_place  + canonical core.venues (MBID-keyed, no fabricated
  capacity/country)
- core.entity_relationships (typed, source-backed edges)

Dump files live under the gitignored ``data/musicbrainz_dumps/`` directory and
are never committed. The artist dump (~1.7 GB) is deliberately NOT downloaded
here; it is the next milestone's identity-resolution work.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from ..localenv import load_local_env
from ..musicbrainz.dumps import (
    JSON_DUMPS_INDEX,
    discover_latest_snapshot,
    download_dump,
    dump_source_id,
    ingest_events_file,
    ingest_place_file,
    record_dump_source,
)
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "music_reference_graph_v1"
DEFAULT_DUMP_DIR = "data/musicbrainz_dumps"


def _fetch(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "festival-bloomberg-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _count(conn, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0])
    except Exception:
        return 0


def _download_and_record(conn, entity_type: str, snapshot: str, dump_dir: str) -> tuple[str | None, str | None]:
    """Download one dump, record its lineage, and return (dsid, extracted_path)."""
    meta = download_dump(entity_type, snapshot, dump_dir)
    dsid = dump_source_id(snapshot, entity_type, meta["url"])
    record_dump_source(
        conn,
        dump_source_id_value=dsid,
        entity_type=entity_type,
        snapshot_dir=snapshot,
        url=meta["url"],
        size_bytes=meta["compressed_size_bytes"],
        local_path=meta["extracted_path"],
        checksum=meta["checksum_sha256"],
    )
    return dsid, meta["extracted_path"]


def run_music_reference_graph_oa(
    *,
    db_path: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/music_reference_graph_v1.json",
    dump_dir: str = DEFAULT_DUMP_DIR,
    validate: bool = True,
) -> dict[str, Any]:
    load_local_env()
    repo = FestivalRepository(db_path)
    try:
        from ..events.repository import EventRepository

        EventRepository(repo.conn)  # applies pending migrations (incl. 028)
        conn = repo.conn

        before = {
            "events": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_event"),
            "places": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_place"),
            "series_events": _count(conn, "SELECT COUNT(*) FROM core.series_events"),
            "performers": _count(conn, "SELECT COUNT(*) FROM core.event_performers"),
        }

        sources: dict[str, Any] = {}
        event_ingest: dict[str, Any] = {"status": "SKIPPED"}
        place_ingest: dict[str, Any] = {"status": "SKIPPED"}
        snapshot: str | None = None
        if validate:
            snapshot = discover_latest_snapshot(_fetch(JSON_DUMPS_INDEX))
            if snapshot is None:
                event_ingest = {"status": "NO_SNAPSHOT_DISCOVERED"}
            else:
                event_dsid, event_path = _download_and_record(conn, "event", snapshot, dump_dir)
                sources["event"] = {"snapshot_dir": snapshot, "dump_source_id": event_dsid}
                event_ingest = ingest_events_file(
                    conn, event_path, dump_source_id_value=event_dsid, commit_every=2000
                )

                place_dsid, place_path = _download_and_record(conn, "place", snapshot, dump_dir)
                sources["place"] = {"snapshot_dir": snapshot, "dump_source_id": place_dsid}
                place_ingest = ingest_place_file(
                    conn, place_path, dump_source_id_value=place_dsid, commit_every=2000
                )

        after = {
            "events": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_event"),
            "places": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_place"),
            "series_events": _count(conn, "SELECT COUNT(*) FROM core.series_events"),
            "performers": _count(conn, "SELECT COUNT(*) FROM core.event_performers"),
            "venues": _count(conn, "SELECT COUNT(*) FROM core.venues WHERE musicbrainz_id IS NOT NULL"),
        }

        # Performer role distribution + festival/tour series linkage.
        performer_roles: dict[str, int] = {}
        try:
            rows = conn.execute(
                "SELECT performer_role, COUNT(*) FROM core.event_performers GROUP BY performer_role ORDER BY 2 DESC"
            ).fetchall()
            performer_roles = {r[0] or "?" : r[1] for r in rows}
        except Exception:
            pass

        linked_series: dict[str, int] = {}
        try:
            rows = conn.execute(
                """
                SELECT s.series_type, COUNT(DISTINCT se.series_key)
                FROM core.series_events se
                JOIN core.event_series s ON s.series_key = se.series_key
                GROUP BY s.series_type ORDER BY 2 DESC
                """
            ).fetchall()
            linked_series = {r[0] or "?" : r[1] for r in rows}
        except Exception:
            pass

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "snapshot": snapshot,
            "sources": sources,
            "event_ingest": event_ingest,
            "place_ingest": place_ingest,
            "before": before,
            "after": after,
            "performer_roles": performer_roles,
            "linked_series_by_type": linked_series,
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    print(json.dumps(run_music_reference_graph_oa(), indent=2, default=str))
