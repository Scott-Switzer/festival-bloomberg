"""P3 — MusicBrainz Silver graph: stream local JSONL dumps into Silver Parquet.

Inputs (local extracted MusicBrainz mbdump JSONL, one JSON object per line):
    /tmp/mb_dumps/extract/mbdump/place    (82,789 places)
    /tmp/mb_dumps/extract/mbdump/series   (37,468 series)
    /tmp/mb_event_dump/mbdump/event       (124,897 events)

Outputs → festival-intelligence-lake/silver/:
    silver/events/events.parquet            (canonical events, MBID identity)
    silver/events/event_artist_edges.parquet (performer relations, MBID-joined)
    silver/events/event_place_edges.parquet  (venue relations)
    silver/events/event_series_edges.parquet (series membership)
    silver/venues/venues.parquet            (places with VENUE/OTHER_PLACE/UNKNOWN classification)
    silver/venues/place_area_edges.parquet
    silver/series/series.parquet            (FESTIVAL/TOUR/RESIDENCY/RUN/OTHER semantics preserved)
    silver/areas/areas.parquet              (from place.area references)

All rows carry source_system='musicbrainz', knowledge_time, ingested_at.
No fuzzy matching: MBIDs are the join keys.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/build_mb_silver_graph.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from festival_bloomberg.lake.r2 import r2_client  # noqa: E402

LAKE_BUCKET = "festival-intelligence-lake"
SOURCE_SYSTEM = "musicbrainz"
DUMP_VERSION = "20260826-001001"

PLACE_INPUT = Path("/tmp/mb_dumps/extract/mbdump/place")
SERIES_INPUT = Path("/tmp/mb_dumps/extract/mbdump/series")
EVENT_INPUT = Path("/tmp/mb_event_dump/mbdump/event")

# Venue-like place types (MusicBrainz place type semantics).
VENUE_TYPES = {
    "Venue", "Club", "Concert hall", "Amphitheater", "Stadium", "Arena",
    "Theatre", "Festival site", "Hall", "Opera house", "Music venue",
    "Outdoor venue", "Indoor venue", "Bar", "Cafe", "Nightclub",
}

# Series type → product classification (preserve source semantics; classify
# only where the source type is unambiguous).
SERIES_TYPE_MAP = {
    "Festival": "FESTIVAL",
    "Concert tour": "TOUR",
    "Residency": "RESIDENCY",
    "Concert residency": "RESIDENCY",
    "Concert series": "RUN",
    "Tour": "TOUR",
}


def sha_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_parquet_r2(table: pa.Table, key: str) -> tuple[int, int, str]:
    """Write a parquet buffer to R2; return (rows, bytes, sha256)."""
    import io

    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    data = buf.getvalue()
    checksum = hashlib.sha256(data).hexdigest()
    s3 = r2_client()
    s3.put_object(Bucket=LAKE_BUCKET, Key=key, Body=data)
    stored = s3.head_object(Bucket=LAKE_BUCKET, Key=key).get("ContentLength")
    if stored != len(data):
        raise RuntimeError(f"upload size mismatch for {key}: {stored} != {len(data)}")
    print(f"  → r2://{LAKE_BUCKET}/{key}  {table.num_rows:,} rows, {len(data)/1048576:.1f} MB")
    return table.num_rows, len(data), checksum


def build_places() -> dict[str, dict]:
    """Parse places → venues.parquet + place_area_edges.parquet. Returns place index."""
    venues: list[dict] = []
    place_area_edges: list[dict] = []
    areas: dict[str, dict] = {}
    place_index: dict[str, dict] = {}
    stats = Counter()

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for rec in iter_jsonl(PLACE_INPUT):
        stats["places_parsed"] += 1
        pid = rec.get("id")
        if not pid:
            stats["invalid"] += 1
            continue
        ptype = rec.get("type") or "UNKNOWN"
        coords = rec.get("coordinates") or {}
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        lifespan = rec.get("life-span") or {}
        area = rec.get("area") or {}

        classification = "UNKNOWN"
        if ptype in VENUE_TYPES:
            classification = "VENUE"
        elif ptype in ("Studio", "Religious building", "Educational facility",
                       "Museum", "Government building", "Commercial building",
                       "Residence", "Park", "Other"):
            classification = "OTHER_PLACE"

        venues.append({
            "venue_key": f"mbid::{pid}",
            "place_mbid": pid,
            "name": rec.get("name"),
            "place_type": ptype,
            "classification": classification,
            "address": rec.get("address"),
            "area_mbid": area.get("id"),
            "area_name": area.get("name"),
            "latitude": lat,
            "longitude": lon,
            "begin": lifespan.get("begin"),
            "end": lifespan.get("end"),
            "ended": lifespan.get("ended"),
            "disambiguation": rec.get("disambiguation"),
            "source_system": SOURCE_SYSTEM,
            "source_version": DUMP_VERSION,
            "knowledge_time": now,
            "ingested_at": now,
        })
        place_index[pid] = {"name": rec.get("name"), "area_mbid": area.get("id"),
                            "classification": classification}
        if area.get("id"):
            place_area_edges.append({
                "edge_key": sha_key("PLACE_IN_AREA", pid, area["id"]),
                "place_mbid": pid,
                "area_mbid": area["id"],
                "area_name": area.get("name"),
                "source_system": SOURCE_SYSTEM,
                "knowledge_time": now,
            })
            aid = area["id"]
            if aid not in areas:
                areas[aid] = {
                    "area_mbid": aid,
                    "name": area.get("name"),
                    "area_type": area.get("type"),
                    "iso_3166_2_codes": json.dumps(area.get("iso-3166-2-codes") or []),
                    "source_system": SOURCE_SYSTEM,
                    "knowledge_time": now,
                }
        stats["venues_emitted"] += 1

    print(f"places: {dict(stats)}")
    return {"venues": venues, "edges": place_area_edges, "areas": list(areas.values()),
            "index": place_index, "stats": stats}


def build_series() -> list[dict]:
    rows: list[dict] = []
    stats = Counter()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for rec in iter_jsonl(SERIES_INPUT):
        stats["series_parsed"] += 1
        sid = rec.get("id")
        if not sid:
            stats["invalid"] += 1
            continue
        stype = rec.get("type") or "Other"
        classification = SERIES_TYPE_MAP.get(stype, "OTHER")
        # Event-membership evidence from relations
        event_count = 0
        artist_rel_count = 0
        for rel in rec.get("relations") or []:
            if rel.get("event"):
                event_count += 1
            if rel.get("artist"):
                artist_rel_count += 1
        rows.append({
            "series_mbid": sid,
            "name": rec.get("name"),
            "series_type": stype,
            "classification": classification,
            "disambiguation": rec.get("disambiguation"),
            "event_member_count": event_count,
            "artist_relation_count": artist_rel_count,
            "source_system": SOURCE_SYSTEM,
            "source_version": DUMP_VERSION,
            "knowledge_time": now,
        })
        stats["classified_" + classification] += 1
    print(f"series: {dict(stats)}")
    return rows


def build_events(place_index: dict[str, dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    events: list[dict] = []
    event_artist: list[dict] = []
    event_place: list[dict] = []
    event_series: list[dict] = []
    stats = Counter()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for rec in iter_jsonl(EVENT_INPUT):
        stats["events_parsed"] += 1
        eid = rec.get("id")
        if not eid:
            stats["invalid"] += 1
            continue
        lifespan = rec.get("life-span") or {}
        events.append({
            "event_mbid": eid,
            "name": rec.get("name"),
            "event_type": rec.get("type"),
            "begin_date": lifespan.get("begin"),
            "end_date": lifespan.get("end"),
            "event_time": rec.get("time"),
            "cancelled": bool(rec.get("cancelled")),
            "setlist_present": bool(rec.get("setlist")),
            "disambiguation": rec.get("disambiguation"),
            "source_system": SOURCE_SYSTEM,
            "source_version": DUMP_VERSION,
            "knowledge_time": now,
        })

        for rel in rec.get("relations") or []:
            rtype = rel.get("type")
            target_type = rel.get("target-type")
            if target_type == "artist" and rel.get("artist"):
                amb = rel["artist"].get("id")
                if amb:
                    event_artist.append({
                        "edge_key": sha_key("EVENT_ARTIST", eid, amb, rtype or ""),
                        "event_mbid": eid,
                        "artist_mbid": amb,
                        "artist_name": rel["artist"].get("name"),
                        "performer_role": rtype,
                        "source_system": SOURCE_SYSTEM,
                        "knowledge_time": now,
                    })
                    stats["event_artist_edges"] += 1
            elif target_type == "place" and rel.get("place"):
                pmb = rel["place"].get("id")
                if pmb:
                    event_place.append({
                        "edge_key": sha_key("EVENT_PLACE", eid, pmb, rtype or ""),
                        "event_mbid": eid,
                        "place_mbid": pmb,
                        "place_name": rel["place"].get("name"),
                        "relation_type": rtype,
                        "source_system": SOURCE_SYSTEM,
                        "knowledge_time": now,
                    })
                    stats["event_place_edges"] += 1
            elif target_type == "series" and rel.get("series"):
                smb = rel["series"].get("id")
                if smb:
                    event_series.append({
                        "edge_key": sha_key("EVENT_SERIES", eid, smb, rtype or ""),
                        "event_mbid": eid,
                        "series_mbid": smb,
                        "relation_type": rtype,
                        "source_system": SOURCE_SYSTEM,
                        "knowledge_time": now,
                    })
                    stats["event_series_edges"] += 1

    print(f"events: {dict(stats)}")
    return events, event_artist, event_place, event_series


def to_table(rows: list[dict]) -> pa.Table:
    if not rows:
        return pa.table({})
    cols = {k: [r.get(k) for r in rows] for k in rows[0].keys()}
    return pa.table(cols)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="parse only, no R2 writes")
    args = ap.parse_args()

    t0 = time.time()
    print("=== building places/venues ===")
    places = build_places()
    print("=== building series ===")
    series = build_series()
    print("=== building events ===")
    events, ev_artist, ev_place, ev_series = build_events(places["index"])

    outputs = [
        ("silver/venues/venues.parquet", places["venues"]),
        ("silver/venues/place_area_edges.parquet", places["edges"]),
        ("silver/areas/areas.parquet", places["areas"]),
        ("silver/series/series.parquet", series),
        ("silver/events/events.parquet", events),
        ("silver/events/event_artist_edges.parquet", ev_artist),
        ("silver/events/event_place_edges.parquet", ev_place),
        ("silver/events/event_series_edges.parquet", ev_series),
    ]

    registered = []
    if args.dry_run:
        for key, rows in outputs:
            print(f"  DRY-RUN {key}: {len(rows):,} rows")
    else:
        for key, rows in outputs:
            table = to_table(rows)
            n, b, checksum = write_parquet_r2(table, key)
            registered.append((key, n, b, checksum))

    runtime = time.time() - t0
    print(f"\nruntime: {runtime:.1f}s")

    # Register in catalog
    if not args.dry_run:
        from festival_bloomberg.lake.catalog import register_dataset
        dataset_ids = {
            "silver/venues/venues.parquet": "silver.venues",
            "silver/venues/place_area_edges.parquet": "silver.place_area_edges",
            "silver/areas/areas.parquet": "silver.areas",
            "silver/series/series.parquet": "silver.series",
            "silver/events/events.parquet": "silver.events",
            "silver/events/event_artist_edges.parquet": "silver.event_artist_edges",
            "silver/events/event_place_edges.parquet": "silver.event_place_edges",
            "silver/events/event_series_edges.parquet": "silver.event_series_edges",
        }
        for key, n, b, checksum in registered:
            register_dataset(
                dataset_id=dataset_ids[key],
                dataset_version=DUMP_VERSION,
                layer="SILVER",
                source="musicbrainz",
                source_version=DUMP_VERSION,
                r2_bucket=LAKE_BUCKET,
                r2_prefix=key,
                fmt="parquet",
                schema_version="silver-v1",
                row_count=n,
                byte_count=b,
                artifact_checksum=checksum,
                verification_status="BUILD_COMPLETE",
                license="CC0-1.0",
                rights_status="DERIVED_FROM_PUBLIC_DOMAIN",
                commercial_use_status="ALLOWED",
                upstream_dataset_ids=["raw.musicbrainz_relational_mbdump"],
            )
        print("catalog registered:", len(registered), "silver datasets")


if __name__ == "__main__":
    main()
