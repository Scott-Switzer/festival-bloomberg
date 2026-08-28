#!/usr/bin/env python3
"""MusicBrainz Wave 1: download, verify, manifest, ingest.

Dumps: area (~33MB), event (~46MB), place (~141MB), series (~31MB), recording (~32MB)
Total compressed: ~283 MB

Pipeline:
  1. Download SHA256SUMS + each .tar.xz
  2. Verify SHA256 for every artifact
  3. Record in security.bulk_source_manifest
  4. Upload compressed archive to R2 raw bulk
  5. Stream-extract + normalize into warehouse tables
  6. Report counts

Usage:
  PYTHONPATH=python .venv/bin/python scripts/mb_wave1_acquire_and_ingest.py \\
    --warehouse /tmp/artist_security_1000.duckdb \\
    --dumps-dir /tmp/mb_dumps_25k
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402
from festival_bloomberg.musicbrainz.dumps import (  # noqa: E402
    dump_url,
    dump_source_id,
    extract_dump_member,
    ingest_area_file,
    ingest_events_file,
    ingest_place_file,
    ingest_series_file,
    record_dump_source,
    iter_json_objects,
    normalize_area,
    persist_reference_area,
)

SNAPSHOT = "20260826-001001"
BASE_URL = f"https://ftp.musicbrainz.org/pub/musicbrainz/data/json-dumps/{SNAPSHOT}"

# Wave 1: 5 high-ROI dumps (~283MB compressed total)
DUMPS = ["area", "event", "place", "series", "recording"]

JUSTIFICATIONS = {
    "area": "Geographical reference layer for venue/artist/place enrichment and market mapping. Enables Artist x Market expansion.",
    "event": "Refresh and complete the event graph: performer links, place links, series membership. 107K+ events expected.",
    "place": "Canonical venue/place identities with coordinates/area. 51K+ places expected.",
    "series": "Festival/tour/residency series spine for touring history and festival experience.",
    "recording": "Recording identity, artist catalog breadth, MBID linkage, ISRC relationships. ~32MB compressed.",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download_file(url: str, dest: Path, label: str) -> None:
    """Download a file with progress reporting."""
    import urllib.request

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  {label}: already exists ({dest.stat().st_size / 1048576:.1f}MB)", flush=True)
        return
    print(f"  {label}: downloading...", flush=True)
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "festival-bloomberg-research/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as out:
        total = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    elapsed = time.time() - t0
    print(f"  {label}: {total / 1048576:.1f}MB in {elapsed:.1f}s ({total / elapsed / 1048576:.1f}MB/s)", flush=True)


def r2_upload(bucket: str, key: str, path: Path) -> bool:
    """Upload to R2. Returns True on success."""
    try:
        subprocess.run(
            ["npx", "wrangler", "r2", "object", "put", f"{bucket}/{key}",
             "--remote", "--file", str(path)],
            check=True, capture_output=True, text=True, timeout=300,
        )
        return True
    except Exception as exc:
        print(f"  R2 upload failed: {exc}", flush=True)
        return False


def record_bulk_manifest(conn, *, entity: str, size_bytes: int, checksum: str,
                         r2_key: str | None, row_count: int) -> str:
    """Record in security.bulk_source_manifest."""
    source = f"musicbrainz_{entity}_dump"
    url = dump_url(SNAPSHOT, entity)
    key = hashlib.sha256(f"{source}|{SNAPSHOT}|{url}".encode()).hexdigest()[:32]
    conn.execute(
        """
        INSERT INTO security.bulk_source_manifest
            (source_manifest_key, source, source_version, source_url, retrieved_at,
             compressed_bytes, sha256, license, rights_status, commercial_use_status,
             product_use_justification, raw_r2_key, normalized_dataset, row_count, ingested_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, 'CC0',
                'SOURCE_LICENSE_REVIEWED', 'INTERNAL_ANALYTICS_ONLY',
                ?, ?, NULL, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (source, source_version, source_url) DO UPDATE SET
            compressed_bytes = excluded.compressed_bytes,
            sha256 = excluded.sha256,
            raw_r2_key = excluded.raw_r2_key,
            row_count = excluded.row_count
        """,
        [key, source, SNAPSHOT, url, size_bytes, checksum,
         JUSTIFICATIONS[entity], r2_key, row_count],
    )
    return key


def ingest_area_stream(conn, archive_path: Path, *, dump_source_id_value: str,
                       knowledge_time: str) -> dict:
    """Stream area dump from tar.xz and ingest into reference.musicbrainz_areas + raw."""
    summary = {"status": "RUNNING", "parsed": 0, "new_areas": 0, "skipped": 0, "invalid": 0}
    member_name = None
    conn.execute("BEGIN TRANSACTION")
    try:
        with tarfile.open(archive_path, "r:xz") as tf:
            for m in tf:
                if m.name.endswith("/area"):
                    member_name = m.name
                    break
            if member_name is None:
                summary["status"] = "MEMBER_NOT_FOUND"
                conn.execute("COMMIT")
                return summary
            fh = tf.extractfile(member_name)
            if fh is None:
                summary["status"] = "MEMBER_NOT_READABLE"
                conn.execute("COMMIT")
                return summary
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    summary["invalid"] += 1
                    continue
                try:
                    rec = normalize_area(obj)
                except (ValueError, KeyError):
                    summary["invalid"] += 1
                    continue
                summary["parsed"] += 1
                if persist_reference_area(conn, rec, dump_source_id_value=dump_source_id_value,
                                          knowledge_time=knowledge_time):
                    summary["new_areas"] += 1
                else:
                    summary["skipped"] += 1
                if summary["parsed"] % 50000 == 0:
                    conn.execute("COMMIT")
                    conn.execute("BEGIN TRANSACTION")
                    print(f"    areas: {summary['parsed']:,} parsed, {summary['new_areas']:,} new", flush=True)
    finally:
        conn.execute("COMMIT")
    summary["status"] = "COMPLETE"
    return summary


def ingest_recording_stream(conn, archive_path: Path, *, dump_source_id_value: str,
                            knowledge_time: str) -> dict:
    """Stream recording dump from tar.xz and ingest into core.recordings."""
    summary = {"status": "RUNNING", "parsed": 0, "new_recordings": 0,
               "skipped": 0, "invalid": 0, "isrc_rows": 0}
    member_name = None
    conn.execute("BEGIN TRANSACTION")
    try:
        with tarfile.open(archive_path, "r:xz") as tf:
            for m in tf:
                if m.name.endswith("/recording"):
                    member_name = m.name
                    break
            if member_name is None:
                summary["status"] = "MEMBER_NOT_FOUND"
                conn.execute("COMMIT")
                return summary
            fh = tf.extractfile(member_name)
            if fh is None:
                summary["status"] = "MEMBER_NOT_READABLE"
                conn.execute("COMMIT")
                return summary
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    summary["invalid"] += 1
                    continue
                summary["parsed"] += 1
                mbid = obj.get("id")
                if not mbid:
                    summary["invalid"] += 1
                    continue
                name = obj.get("name") or "[untitled]"
                length = obj.get("length")
                # Artist links from relations
                artist_mbids = []
                isrcs = []
                for rel in obj.get("relations") or []:
                    if not isinstance(rel, dict):
                        continue
                    tt = rel.get("target-type")
                    if tt == "artist":
                        artist = rel.get("artist") or {}
                        aid = artist.get("id") if isinstance(artist, dict) else None
                        if aid and aid not in artist_mbids:
                            artist_mbids.append(aid)
                    elif tt == "isrc":
                        isrc_obj = rel.get("isrc") or {}
                        isrc_val = isrc_obj.get("isrc") if isinstance(isrc_obj, dict) else None
                        if isrc_val:
                            isrcs.append(isrc_val)
                # first-release-date from the recording itself
                frd = obj.get("first-release-date")
                disc = obj.get("disambiguation")
                recording_key = f"mbid::{mbid}"
                # Upsert by MBID
                exists = conn.execute(
                    "SELECT 1 FROM core.recordings WHERE musicbrainz_id = ?", [mbid]
                ).fetchone()
                if exists:
                    summary["skipped"] += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO core.recordings
                            (recording_key, musicbrainz_id, name, artist_keys, isrc,
                             duration_ms, first_release_date, disambiguation,
                             source_system, source_url, knowledge_time, ingested_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'musicbrainz_dump', NULL, ?, CURRENT_TIMESTAMP)
                        """,
                        [recording_key, mbid, name,
                         json.dumps([f"mbid::{a}" for a in artist_mbids]) if artist_mbids else None,
                         isrcs[0] if isrcs else None,
                         length, frd, disc, knowledge_time],
                    )
                    summary["new_recordings"] += 1
                if summary["parsed"] % 100000 == 0:
                    conn.execute("COMMIT")
                    conn.execute("BEGIN TRANSACTION")
                    print(f"    recordings: {summary['parsed']:,} parsed, {summary['new_recordings']:,} new", flush=True)
    finally:
        conn.execute("COMMIT")
    summary["status"] = "COMPLETE"
    return summary


def count_table(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="MusicBrainz Wave 1 acquisition + ingestion")
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--dumps-dir", default="/tmp/mb_dumps_25k")
    parser.add_argument("--r2-bucket", default="festival-intelligence-raw")
    parser.add_argument("--r2-prefix", default="bulk/musicbrainz")
    parser.add_argument("--skip-r2", action="store_true", help="Skip R2 uploads")
    parser.add_argument("--skip-download", action="store_true", help="Skip downloads (use existing files)")
    parser.add_argument("--entity", choices=DUMPS, default=None, help="Process only one entity")
    args = parser.parse_args()

    dumps_dir = Path(args.dumps_dir)
    dumps_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = dumps_dir / "extract"

    conn = duckdb.connect(args.warehouse)
    apply_pending_migrations(conn)
    knowledge_time = datetime.now(timezone.utc).isoformat()
    entities = [args.entity] if args.entity else DUMPS

    # BEFORE snapshot
    print("=== BEFORE ===", flush=True)
    before = {}
    for label, q in [
        ("events", "SELECT COUNT(*) FROM raw.musicbrainz_event"),
        ("places", "SELECT COUNT(*) FROM raw.musicbrainz_place"),
        ("venues", "SELECT COUNT(*) FROM core.venues"),
        ("series_raw", "SELECT COUNT(*) FROM raw.musicbrainz_series"),
        ("event_series", "SELECT COUNT(*) FROM core.event_series"),
        ("performers", "SELECT COUNT(*) FROM core.event_performers"),
        ("entity_rels", "SELECT COUNT(*) FROM core.entity_relationships"),
        ("ref_areas", "SELECT COUNT(*) FROM reference.musicbrainz_areas"),
        ("ref_artists", "SELECT COUNT(*) FROM reference.musicbrainz_artists"),
        ("recordings", "SELECT COUNT(*) FROM core.recordings"),
    ]:
        before[label] = conn.execute(q).fetchone()[0]
        print(f"  {label:20s} {before[label]:>12,}", flush=True)

    t_start = time.time()
    results = {}

    # Step 1: Download SHA256SUMS
    if not args.skip_download:
        sha256sums_path = dumps_dir / "SHA256SUMS"
        download_file(f"{BASE_URL}/SHA256SUMS", sha256sums_path, "SHA256SUMS")
        # Parse expected checksums
        expected = {}
        for line in sha256sums_path.read_text().strip().splitlines():
            parts = line.split("  ", 1)
            if len(parts) == 2:
                expected[parts[1]] = parts[0]
    else:
        expected = {}

    for entity in entities:
        print(f"\n{'='*60}", flush=True)
        print(f"  {entity.upper()}", flush=True)
        print(f"{'='*60}", flush=True)
        t_entity = time.time()

        # Download
        archive = dumps_dir / f"{entity}.tar.xz"
        if not args.skip_download:
            download_file(f"{BASE_URL}/{entity}.tar.xz", archive, entity)

        if not archive.exists():
            print(f"  SKIP: {archive} not found", flush=True)
            continue

        size = archive.stat().st_size
        checksum = sha256_file(archive)

        # Verify SHA256
        if expected and entity + ".tar.xz" in expected:
            if checksum != expected[entity + ".tar.xz"]:
                print(f"  SHA256 MISMATCH! expected={expected[entity + '.tar.xz']} got={checksum}", flush=True)
                print(f"  FAILING CLOSED — skipping {entity}", flush=True)
                continue
            print(f"  SHA256 verified: {checksum[:16]}...", flush=True)
        else:
            print(f"  SHA256SUMS entry not found for {entity}.tar.xz — recording checksum only", flush=True)

        # R2 upload
        r2_key = None
        if not args.skip_r2:
            r2_key = f"{args.r2_prefix}/{SNAPSHOT}/{entity}.tar.xz"
            ok = r2_upload(args.r2_bucket, r2_key, archive)
            if not ok:
                r2_key = None

        # Record dump source in raw.musicbrainz_dump_source
        dsid = dump_source_id(SNAPSHOT, entity, dump_url(SNAPSHOT, entity))
        record_dump_source(conn, dump_source_id_value=dsid, entity_type=entity,
                           snapshot_dir=SNAPSHOT, url=dump_url(SNAPSHOT, entity),
                           size_bytes=size, local_path=str(archive), checksum=checksum)

        # Ingest based on entity type
        if entity == "area":
            summary = ingest_area_stream(conn, archive, dump_source_id_value=dsid,
                                         knowledge_time=knowledge_time)
            row_count = summary.get("new_areas", 0)
        elif entity == "event":
            # Events: extract to file then ingest (existing function expects file path)
            extracted = extract_dump_member(archive, extract_dir, f"mbdump/{entity}")
            summary = ingest_events_file(conn, extracted, dump_source_id_value=dsid,
                                         knowledge_time=knowledge_time, commit_every=5000)
            row_count = summary.get("new_events", 0)
        elif entity == "place":
            extracted = extract_dump_member(archive, extract_dir, f"mbdump/{entity}")
            summary = ingest_place_file(conn, extracted, dump_source_id_value=dsid,
                                        knowledge_time=knowledge_time, commit_every=5000)
            row_count = summary.get("new_places", 0)
        elif entity == "series":
            extracted = extract_dump_member(archive, extract_dir, f"mbdump/{entity}")
            summary = ingest_series_file(conn, extracted, dump_source_id_value=dsid,
                                         knowledge_time=knowledge_time)
            row_count = summary.get("persisted", 0)
        elif entity == "recording":
            summary = ingest_recording_stream(conn, archive, dump_source_id_value=dsid,
                                              knowledge_time=knowledge_time)
            row_count = summary.get("new_recordings", 0)
        else:
            summary = {"status": "SKIPPED"}
            row_count = 0

        # Record bulk manifest
        record_bulk_manifest(conn, entity=entity, size_bytes=size, checksum=checksum,
                             r2_key=r2_key, row_count=row_count)

        elapsed = time.time() - t_entity
        results[entity] = {
            "compressed_bytes": size,
            "sha256": checksum,
            "summary": summary,
            "row_count": row_count,
            "elapsed_s": round(elapsed, 1),
            "r2_key": r2_key,
        }

        print(f"  RESULT: {json.dumps({k: v for k, v in summary.items() if k != 'by_type'}, default=str)[:500]}", flush=True)
        print(f"  new rows: {row_count:,}  elapsed: {elapsed:.1f}s", flush=True)

    total_elapsed = time.time() - t_start

    # AFTER snapshot
    print(f"\n{'='*60}", flush=True)
    print("  AFTER", flush=True)
    print(f"{'='*60}", flush=True)
    after = {}
    for label, q in [
        ("events", "SELECT COUNT(*) FROM raw.musicbrainz_event"),
        ("places", "SELECT COUNT(*) FROM raw.musicbrainz_place"),
        ("venues", "SELECT COUNT(*) FROM core.venues"),
        ("series_raw", "SELECT COUNT(*) FROM raw.musicbrainz_series"),
        ("event_series", "SELECT COUNT(*) FROM core.event_series"),
        ("performers", "SELECT COUNT(*) FROM core.event_performers"),
        ("entity_rels", "SELECT COUNT(*) FROM core.entity_relationships"),
        ("ref_areas", "SELECT COUNT(*) FROM reference.musicbrainz_areas"),
        ("ref_artists", "SELECT COUNT(*) FROM reference.musicbrainz_artists"),
        ("recordings", "SELECT COUNT(*) FROM core.recordings"),
    ]:
        after[label] = conn.execute(q).fetchone()[0]
        delta = after[label] - before[label]
        sign = "+" if delta > 0 else ""
        print(f"  {label:20s} {before[label]:>12,} → {after[label]:>12,}  ({sign}{delta:,})", flush=True)

    # Per-entity event type breakdown
    print("\n=== Event types (top 10) ===", flush=True)
    for row in conn.execute(
        "SELECT event_type, COUNT(*) FROM raw.musicbrainz_event GROUP BY event_type ORDER BY 2 DESC LIMIT 10"
    ).fetchall():
        print(f"  {str(row[0]):30s} {row[1]:>8,}", flush=True)

    print("\n=== Venue types (top 10) ===", flush=True)
    for row in conn.execute(
        "SELECT venue_type, COUNT(*) FROM core.venues GROUP BY venue_type ORDER BY 2 DESC LIMIT 10"
    ).fetchall():
        print(f"  {str(row[0]):30s} {row[1]:>8,}", flush=True)

    print("\n=== Series types ===", flush=True)
    for row in conn.execute(
        "SELECT series_type, COUNT(*) FROM raw.musicbrainz_series GROUP BY series_type ORDER BY 2 DESC"
    ).fetchall():
        print(f"  {str(row[0]):30s} {row[1]:>8,}", flush=True)

    # Universe coverage: how many 25K artists have event performer links?
    print("\n=== Universe coverage ===", flush=True)
    r = conn.execute("""
        SELECT COUNT(DISTINCT u.artist_key)
        FROM security.artist_security_universe_25000 u
        JOIN core.artists a ON a.artist_key = u.artist_key
        JOIN core.event_performers ep ON ep.artist_mbid = a.musicbrainz_id
    """).fetchone()
    print(f"  25K artists with MB event performances: {r[0]:,}", flush=True)

    conn.close()

    # Final report
    print(f"\n{'='*60}", flush=True)
    print("  WAVE 1 COMPLETE", flush=True)
    print(f"{'='*60}", flush=True)
    total_compressed = sum(r.get("compressed_bytes", 0) for r in results.values())
    total_new = sum(r.get("row_count", 0) for r in results.values())
    print(f"  Total compressed: {total_compressed / 1048576:.1f}MB", flush=True)
    print(f"  Total new rows:   {total_new:,}", flush=True)
    print(f"  Total runtime:    {total_elapsed:.1f}s", flush=True)
    print(f"  Entities:         {', '.join(results.keys())}", flush=True)

    out_path = Path("reports/mb_wave1_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "snapshot": SNAPSHOT,
        "entities": results,
        "before": before,
        "after": after,
        "total_compressed_bytes": total_compressed,
        "total_new_rows": total_new,
        "total_runtime_s": round(total_elapsed, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n  Report: {out_path}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
