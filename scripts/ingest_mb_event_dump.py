"""Ingest the MusicBrainz event dump into a dedicated staging warehouse.

The event dump (533MB NDJSON, ~470k events) provides begin_date/event_type for
the 107k distinct events in the lake's event_performers — the missing piece
for P5 live statistics (SHOWS_30D/90D/365D, festival appearances).

Runs against its OWN duckdb file (the Wikimedia backfill holds the write lock
on the main scale warehouse). After the backfill completes, the raw event rows
are merged into the main warehouse.

    PYTHONPATH=python .venv/bin/python scripts/ingest_mb_event_dump.py \
        --warehouse /tmp/mb_events.duckdb \
        --dump /tmp/mb_event_dump/mbdump/event \
        --snapshot 20260826-001001
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402
from festival_bloomberg.musicbrainz.dumps import (  # noqa: E402
    dump_source_id,
    dump_url,
    ingest_events_file,
    record_dump_source,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/mb_events.duckdb")
    parser.add_argument("--dump", default="/tmp/mb_event_dump/mbdump/event")
    parser.add_argument("--snapshot", default="20260826-001001")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--commit-every", type=int, default=5000)
    args = parser.parse_args()

    knowledge_time = datetime.now(timezone.utc).isoformat()
    conn = duckdb.connect(args.warehouse)
    try:
        # Bound DuckDB memory explicitly (8GB host; the dump ingest + payload
        # JSON ballooned the default 6.3GB buffer and OOM'd on the first run).
        conn.execute("SET memory_limit='3GB'")
        conn.execute("SET threads=4")
        conn.execute("SET preserve_insertion_order=false")
        apply_pending_migrations(conn)
        source_id = dump_source_id(args.snapshot, "event", dump_url(args.snapshot, "event"))
        record_dump_source(
            conn, dump_source_id_value=source_id, entity_type="event",
            snapshot_dir=args.snapshot, url=dump_url(args.snapshot, "event"),
            size_bytes=None, local_path=None, checksum=None,
        )
        summary = ingest_events_file(
            conn, args.dump,
            dump_source_id_value=source_id,
            knowledge_time=knowledge_time,
            limit=args.limit,
            commit_every=args.commit_every,
        )
        import json

        print(json.dumps(summary, indent=2, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
