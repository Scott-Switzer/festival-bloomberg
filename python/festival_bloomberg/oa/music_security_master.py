"""MUSIC_SECURITY_MASTER_AND_MONITORING_V1 — bounded operational acceptance.

Ingests the MusicBrainz CC0 ``series`` dump (the festival/tour/residency
spine) into ``raw.musicbrainz_series`` + ``core.event_series`` with full
source lineage (snapshot date, URL, compressed size, SHA-256, CC0). Dump files
live under a gitignored data directory and are never committed.

This is the first, smallest, highest-value slice of the bulk ingest: series
(31 MB) before event/place/artist/label. The artist dump (~1.7 GB) is
deliberately NOT downloaded here.
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
    ingest_series_file,
    record_dump_source,
)
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "music_security_master_v1"
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


def run_music_security_master_oa(
    *,
    db_path: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/music_security_master_v1.json",
    dump_dir: str = DEFAULT_DUMP_DIR,
    validate: bool = True,
) -> dict[str, Any]:
    load_local_env()
    repo = FestivalRepository(db_path)
    try:
        from ..events.repository import EventRepository

        EventRepository(repo.conn)  # applies pending migrations (incl. 027)
        conn = repo.conn

        before = {
            "series": _count(conn, "SELECT COUNT(*) FROM core.event_series"),
            "dump_sources": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_dump_source"),
        }

        ingest: dict[str, Any] = {"status": "SKIPPED"}
        source: dict[str, Any] = {}
        snapshot: str | None = None
        if validate:
            index = _fetch(JSON_DUMPS_INDEX)
            snapshot = discover_latest_snapshot(index)
            if snapshot is None:
                ingest = {"status": "NO_SNAPSHOT_DISCOVERED"}
            else:
                meta = download_dump("series", snapshot, dump_dir)
                source = {
                    "snapshot_dir": snapshot,
                    "url": meta["url"],
                    "compressed_size_bytes": meta["compressed_size_bytes"],
                    "checksum_sha256": meta["checksum_sha256"],
                    "license": meta["license"],
                    "extracted_path": meta["extracted_path"],
                }
                dsid = dump_source_id(snapshot, "series", meta["url"])
                record_dump_source(
                    conn,
                    dump_source_id_value=dsid,
                    entity_type="series",
                    snapshot_dir=snapshot,
                    url=meta["url"],
                    size_bytes=meta["compressed_size_bytes"],
                    local_path=meta["extracted_path"],
                    checksum=meta["checksum_sha256"],
                )
                ingest = ingest_series_file(conn, meta["extracted_path"], dump_source_id_value=dsid)
                conn.commit()

        after = {
            "series": _count(conn, "SELECT COUNT(*) FROM core.event_series"),
            "dump_sources": _count(conn, "SELECT COUNT(*) FROM raw.musicbrainz_dump_source"),
        }
        series_by_type = {}
        try:
            rows = conn.execute(
                "SELECT series_type, COUNT(*) FROM core.event_series GROUP BY series_type ORDER BY 2 DESC"
            ).fetchall()
            series_by_type = {r[0]: r[1] for r in rows}
        except Exception:
            pass

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "snapshot": snapshot,
            "source": source,
            "ingest": ingest,
            "before": before,
            "after": after,
            "series_by_type": series_by_type,
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    print(json.dumps(run_music_security_master_oa(), indent=2, default=str))
