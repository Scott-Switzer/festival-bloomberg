"""MusicBrainz bulk dump acquisition for ARTIST_SECURITY_25000_DATABASE_V1.

High-ROI first: series (31MB), place (141MB), artist (1.6GB). The full
release dump (22GB) and release-group (1GB) are deliberately deferred; the
22GB release dump is not downloaded in this milestone.

Pipeline per dump:
  1. verify local archive exists + size
  2. record immutable source manifest (security.bulk_source_manifest)
  3. upload compressed archive to R2 raw bulk (immutable)
  4. stream-extract + normalize only useful columns
  5. update manifest row counts

Explicit product-use justification for each dump is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402
from festival_bloomberg.musicbrainz.dumps import (  # noqa: E402
    dump_source_id,
    dump_url,
    extract_dump_member,
    ingest_artist_archive_stream,
    ingest_place_file,
    ingest_series_file,
    record_dump_source,
)

SNAPSHOT = "20260826-001001"

JUSTIFICATIONS = {
    "series": "Festival/tour/residency series spine for touring history, festival experience, routing, and artist-market evidence.",
    "place": "Canonical venue/place identities (>=5k venues) with coordinates/area for the venue security master and Artist x Market.",
    "artist": "Canonical artist identity + aliases/area/type/URL relations for the 100K identity layer and >=70% YouTube/Wikidata resolution.",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def r2_upload(bucket: str, key: str, path: Path) -> None:
    subprocess.run(
        ["npx", "wrangler", "r2", "object", "put", f"{bucket}/{key}", "--remote", "--file", str(path)],
        check=True, capture_output=True, text=True,
    )


def record_manifest(conn, *, entity_type: str, size_bytes: int, checksum: str, r2_key: str) -> str:
    source = f"musicbrainz_{entity_type}_dump"
    url = dump_url(SNAPSHOT, entity_type)
    key = hashlib.sha256(f"{source}|{SNAPSHOT}|{url}".encode()).hexdigest()[:32]
    conn.execute(
        """
        INSERT INTO security.bulk_source_manifest
            (source_manifest_key, source, source_version, source_url, retrieved_at,
             compressed_bytes, sha256, license, rights_status, commercial_use_status,
             product_use_justification, raw_r2_key, normalized_dataset, ingested_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, 'CC0',
                'SOURCE_LICENSE_REVIEWED', 'INTERNAL_ANALYTICS_ONLY', ?, ?, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT (source, source_version, source_url) DO UPDATE SET
            compressed_bytes = excluded.compressed_bytes,
            sha256 = excluded.sha256,
            raw_r2_key = excluded.raw_r2_key -- gitleaks:allow
        """,
        [key, source, SNAPSHOT, url, size_bytes, checksum, JUSTIFICATIONS[entity_type], r2_key],
    )
    return key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--dumps-dir", default="/tmp/mb_dumps")
    parser.add_argument("--r2-bucket", default="festival-intelligence-raw")
    parser.add_argument("--r2-prefix", default="bulk/musicbrainz")
    parser.add_argument("--entity", choices=["series", "place", "artist"], default=None, help="ingest only this entity")
    parser.add_argument("--skip-r2", action="store_true", help="do not upload raw artifact to R2")
    parser.add_argument("--artist-limit", type=int, default=None)
    args = parser.parse_args()

    conn = duckdb.connect(args.warehouse)
    apply_pending_migrations(conn)
    knowledge_time = datetime.now(timezone.utc).isoformat()
    entities = [args.entity] if args.entity else ["series", "place", "artist"]

    for entity in entities:
        archive = Path(args.dumps_dir) / f"{entity}.tar.xz"
        if not archive.exists():
            print(f"SKIP {entity}: {archive} missing (download first)", flush=True)
            continue
        size = archive.stat().st_size
        checksum = sha256_file(archive)
        r2_key = f"{args.r2_prefix}/{SNAPSHOT}/{entity}.tar.xz"
        if not args.skip_r2:
            try:
                r2_upload(args.r2_bucket, r2_key, archive)
                print(f"R2 {entity}: {r2_key} ({size/1048576:.1f}MB)", flush=True)
            except Exception as exc:
                print(f"R2 {entity} UPLOAD FAILED (continuing local ingest): {exc}", flush=True)
        manifest_key = record_manifest(conn, entity_type=entity, size_bytes=size, checksum=checksum, r2_key=r2_key)

        # Ingest local materialization.
        if entity == "series":
            extracted = extract_dump_member(archive, Path(args.dumps_dir) / "extract", "mbdump/series")
            summary = ingest_series_file(
                conn, extracted, dump_source_id_value=dump_source_id(SNAPSHOT, "series", dump_url(SNAPSHOT, "series")),
                knowledge_time=knowledge_time, limit=args.artist_limit,
            )
        elif entity == "place":
            extracted = extract_dump_member(archive, Path(args.dumps_dir) / "extract", "mbdump/place")
            summary = ingest_place_file(
                conn, extracted, dump_source_id_value=dump_source_id(SNAPSHOT, "place", dump_url(SNAPSHOT, "place")),
                knowledge_time=knowledge_time, limit=args.artist_limit,
            )
        else:  # artist
            summary = ingest_artist_archive_stream(
                conn, archive, dump_source_id_value=dump_source_id(SNAPSHOT, "artist", dump_url(SNAPSHOT, "artist")),
                knowledge_time=knowledge_time, limit=args.artist_limit, commit_every=5000,
            )
        conn.execute("UPDATE security.bulk_source_manifest SET row_count = ? WHERE source_manifest_key = ?",
                     [summary.get("persisted", summary.get("new_artists", summary.get("new_places", 0))) or 0, manifest_key])
        print(f"{entity}: {json.dumps(summary, default=str)[:1200]}", flush=True)
    conn.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
