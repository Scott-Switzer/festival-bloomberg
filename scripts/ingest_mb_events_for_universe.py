"""Targeted MusicBrainz event ingest for the ARTIST_SECURITY_1000 scale pass.

The full event dump (~470k events with heavy JSON payloads) OOMs on the 8GB
host when ingested wholesale. The scale pass only needs the events referenced
by the lake's ``core.event_performers`` (107,599 distinct MBIDs) — begin_date,
event_type, cancelled — plus the series/performer edges already present in
the lake. This script:

1. Reads the needed event MBIDs from the lake parquet (read-only, no lock).
2. Streams the dump NDJSON line-by-line, keeping ONLY matching events.
3. Persists compact raw.musicbrainz_event rows (payload trimmed to the small
   fields; full payload NOT retained — the source lineage is in
   raw.musicbrainz_dump_source).
4. Also records series (festival/tour) membership edges into
   core.series_events when the event is part of a series.

Idempotent by MBID. Bounded transactions + a hard DuckDB memory limit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402
from festival_bloomberg.musicbrainz.dumps import (  # noqa: E402
    dump_source_id,
    dump_url,
    record_dump_source,
    series_key_for,
)


def _compact_event(obj: dict) -> dict | None:
    mbid = obj.get("id")
    if not mbid:
        return None
    lifespan = obj.get("life-span") or {}
    return {
        "mbid": mbid,
        "name": obj.get("name"),
        "event_type": obj.get("type"),
        "begin_date": lifespan.get("begin") if isinstance(lifespan, dict) else None,
        "end_date": lifespan.get("end") if isinstance(lifespan, dict) else None,
        "event_time": obj.get("time"),
        "cancelled": obj.get("cancelled"),
        "disambiguation": obj.get("disambiguation"),
        "relations": obj.get("relations") or [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/mb_events.duckdb")
    parser.add_argument("--dump", default="/tmp/mb_event_dump/mbdump/event")
    parser.add_argument("--lake", default="/tmp/fi_lake/event_performers.parquet")
    parser.add_argument("--snapshot", default="20260826-001001")
    parser.add_argument("--commit-every", type=int, default=2000)
    args = parser.parse_args()

    knowledge_time = datetime.now(timezone.utc).isoformat()
    conn = duckdb.connect(args.warehouse)
    try:
        conn.execute("SET memory_limit='3GB'")
        conn.execute("SET threads=4")
        conn.execute("SET preserve_insertion_order=false")
        apply_pending_migrations(conn)

        needed = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT event_mbid FROM read_parquet(?) WHERE event_mbid IS NOT NULL",
                [args.lake],
            ).fetchall()
        }
        print(f"needed event mbids from lake: {len(needed)}")

        source_id = dump_source_id(args.snapshot, "event", dump_url(args.snapshot, "event"))
        record_dump_source(
            conn, dump_source_id_value=source_id, entity_type="event",
            snapshot_dir=args.snapshot, url=dump_url(args.snapshot, "event"),
            size_bytes=None, local_path=None, checksum=None,
        )

        summary = {
            "status": "RUNNING", "scanned": 0, "kept": 0, "inserted": 0,
            "skipped_existing": 0, "series_events": 0, "invalid": 0,
        }
        conn.execute("BEGIN TRANSACTION")
        try:
            with open(args.dump, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    summary["scanned"] += 1
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        summary["invalid"] += 1
                        continue
                    rec = _compact_event(obj)
                    if rec is None or rec["mbid"] not in needed:
                        continue
                    summary["kept"] += 1
                    exists = conn.execute(
                        "SELECT 1 FROM raw.musicbrainz_event WHERE mbid = ?", [rec["mbid"]]
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            """
                            INSERT INTO raw.musicbrainz_event
                                (mbid, name, event_type, begin_date, end_date, event_time,
                                 cancelled, disambiguation, payload, dump_source_id,
                                 knowledge_time, ingested_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """,
                            [
                                rec["mbid"], rec["name"], rec["event_type"], rec["begin_date"],
                                rec["end_date"], rec["event_time"], rec["cancelled"],
                                rec["disambiguation"],
                                json.dumps({
                                    "name": rec["name"], "event_type": rec["event_type"],
                                    "begin_date": rec["begin_date"], "cancelled": rec["cancelled"],
                                }, default=str),
                                source_id, knowledge_time,
                            ],
                        )
                        summary["inserted"] += 1
                        for rel in rec["relations"]:
                            if not isinstance(rel, dict):
                                continue
                            if rel.get("target-type") == "series" and rel.get("type") == "part of":
                                nested = rel.get("series") or {}
                                tid = nested.get("id") if isinstance(nested, dict) else None
                                if tid:
                                    conn.execute(
                                        """
                                        INSERT OR IGNORE INTO core.series_events
                                            (series_event_key, series_key, series_mbid, event_mbid,
                                             event_name, event_type, event_begin_date, event_end_date,
                                             relationship_type, source_system, knowledge_time, ingested_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'part of', 'musicbrainz',
                                                ?, CURRENT_TIMESTAMP)
                                        """,
                                        [
                                            json.dumps([tid, rec["mbid"]]),
                                            series_key_for(tid), tid, rec["mbid"], rec["name"],
                                            rec["event_type"], rec["begin_date"], rec["end_date"],
                                            knowledge_time,
                                        ],
                                    )
                                    summary["series_events"] += 1
                    else:
                        summary["skipped_existing"] += 1
                    if summary["inserted"] % args.commit_every == 0:
                        conn.execute("COMMIT")
                        conn.execute("BEGIN TRANSACTION")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        summary["status"] = "COMPLETE"
        print(json.dumps(summary, indent=2, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
