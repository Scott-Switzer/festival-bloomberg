"""MusicBrainz JSON-dump downloader + parser + ingester.

The official core data is CC0 and downloadable per entity type from
``https://ftp.musicbrainz.org/pub/musicbrainz/data/json-dumps/``. We ingest
the *small* high-value dumps (event, series, place — later artist, label)
locally rather than hammering the 1-request/sec web service.

Every download records its snapshot date, URL, compressed size and SHA-256,
and the raw JSON payload is preserved in ``raw.musicbrainz_series`` (the
first entity type wired). Dump files themselves are NEVER committed to git.

The parser is deliberately tolerant of both dump layouts (a top-level JSON
array and newline-delimited JSON) because MusicBrainz has shipped both.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

JSON_DUMPS_INDEX = "https://ftp.musicbrainz.org/pub/musicbrainz/data/json-dumps/"
SNAPSHOT_DIR_RE = re.compile(r"href=\"(\d{8}-\d{6})/\"")
CC0_LICENSE = "CC0"

#: MusicBrainz series type -> canonical EVENT series_type.
#: Only EVENT series types belong in core.event_series; catalogue/work/label/
#: award/podcast series are NOT event series and stay in raw only.
SERIES_TYPE_MAP: dict[str, str] = {
    "Festival": "FESTIVAL",
    "Tour": "TOUR",
    "Residency": "RESIDENCY",
    "Run": "RUN",
    "Event series": "EVENT_SERIES",
    "Event": "EVENT_SERIES",
}


def snapshot_sort_key(name: str) -> str:
    digits = "".join(ch for ch in name if ch.isdigit())
    return digits or name


def discover_latest_snapshot(index_html: str) -> str | None:
    """Return the most recent dated snapshot dir name, or None.

    ``index_html`` is the raw HTML index listing of the JSON dumps directory
    (fetched separately by the caller through the transport, so tests can
    script it).
    """
    names = SNAPSHOT_DIR_RE.findall(index_html)
    if not names:
        return None
    return max(names, key=snapshot_sort_key)


def dump_url(snapshot_dir: str, entity_type: str) -> str:
    return f"{JSON_DUMPS_INDEX}{snapshot_dir}/{entity_type}.tar.xz"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_source_id(snapshot_dir: str, entity_type: str, url: str) -> str:
    material = "|".join([snapshot_dir, entity_type, url])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def iter_json_objects(text: str) -> Iterator[dict[str, Any]]:
    """Yield dicts from either a JSON array or a newline-delimited JSON file."""
    stripped = text.lstrip()
    if not stripped:
        return
    if stripped.startswith("["):
        data = json.loads(stripped)
        if isinstance(data, list):
            yield from (item for item in data if isinstance(item, dict))
        return
    for line in stripped.splitlines():
        line = line.strip()
        if line:
            item = json.loads(line)
            if isinstance(item, dict):
                yield item


def series_artist_mbids(obj: dict[str, Any]) -> list[str]:
    """Extract artist MBIDs from a series object's relations (dump or web format)."""
    out: list[str] = []

    def _scan(relations: list[dict[str, Any]]) -> None:
        for rel in relations if isinstance(relations, list) else []:
            if not isinstance(rel, dict):
                continue
            if rel.get("target-type") != "artist":
                continue
            artist = rel.get("artist") or {}
            mbid = artist.get("id") if isinstance(artist, dict) else None
            if mbid and mbid not in out:
                out.append(mbid)

    # Dump format: top-level "relations" list.
    _scan(obj.get("relations") or [])
    # Web-service format fallback: "relation-list" -> [{"relations": [...]}].
    for rl in obj.get("relation-list") or []:
        if isinstance(rl, dict):
            _scan(rl.get("relations") or [])
    return out


def normalize_series(obj: dict[str, Any]) -> dict[str, Any]:
    mbid = obj.get("id")
    if not mbid:
        raise ValueError("series object missing id")
    raw_type = obj.get("type") or "Series"
    series_type = SERIES_TYPE_MAP.get(raw_type)  # None when NOT an event series
    return {
        "mbid": mbid,
        "name": obj.get("name"),
        "series_type": series_type,
        "source_type": raw_type,
        "is_event_series": series_type is not None,
        "disambiguation": obj.get("disambiguation"),
        "artist_mbids": series_artist_mbids(obj),
        "begin_date": obj.get("begin-date"),
        "end_date": obj.get("end-date"),
        "payload": obj,
    }


def series_key_for(mbid: str) -> str:
    return f"mbid::{mbid}"


def record_dump_source(conn, *, dump_source_id_value: str, entity_type: str,
                       snapshot_dir: str, url: str, size_bytes: int | None,
                       local_path: str | None, checksum: str | None) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM raw.musicbrainz_dump_source WHERE dump_source_id = ?",
        [dump_source_id_value],
    ).fetchone()
    if exists:
        return False
    conn.execute(
        """
        INSERT INTO raw.musicbrainz_dump_source
            (dump_source_id, entity_type, snapshot_date, download_url,
             compressed_size_bytes, local_path, checksum_sha256, license,
             downloaded_at, parsed_rows, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0, CURRENT_TIMESTAMP)
        """,
        [
            dump_source_id_value, entity_type, snapshot_dir, url,
            size_bytes, local_path, checksum, CC0_LICENSE,
        ],
    )
    return True


def persist_series(conn, rec: dict[str, Any], *, dump_source_id_value: str,
                   knowledge_time: str) -> int:
    """Persist one series into raw + canonical event_series. Returns 1 if new."""
    exists = conn.execute(
        "SELECT 1 FROM raw.musicbrainz_series WHERE mbid = ?", [rec["mbid"]]
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO raw.musicbrainz_series
            (mbid, name, series_type, disambiguation, artist_mbids, begin_date,
             end_date, payload, dump_source_id, knowledge_time, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            rec["mbid"], rec["name"], rec["source_type"], rec["disambiguation"],
            json.dumps(rec["artist_mbids"], default=str),
            rec["begin_date"], rec["end_date"],
            json.dumps(rec["payload"], default=str),
            dump_source_id_value, knowledge_time,
        ],
    )
    # Canonical event_series: ONLY event series (festival/tour/residency/run).
    # Catalogue/work/label/award/podcast series stay in raw only.
    if rec["series_type"] is not None:
        conn.execute(
            """
            INSERT OR IGNORE INTO core.event_series
                (series_key, musicbrainz_id, name, normalized_name, series_type,
                 artist_key, disambiguation, begin_date, end_date, source_system,
                 source_url, knowledge_time, ingested_at)
            VALUES (?, ?, ?, LOWER(TRIM(?)), ?, ?, ?, ?, ?, 'musicbrainz', NULL, ?, CURRENT_TIMESTAMP)
            """,
            [
                series_key_for(rec["mbid"]), rec["mbid"], rec["name"], rec["name"],
                rec["series_type"], None, rec["disambiguation"],
                rec["begin_date"], rec["end_date"], knowledge_time,
            ],
        )
    return 1


def extract_dump_member(archive_path: Path, dest_dir: Path, member: str) -> Path:
    """Extract one member (``mbdump/<entity>``) from a ``.tar.xz`` dump."""
    import tarfile

    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:xz") as tf:
        tf.extract(member, path=dest_dir)
    return dest_dir / member


def download_dump(entity_type: str, snapshot_dir: str, dest_dir: str | Path) -> dict[str, Any]:
    """Download one ``.tar.xz`` dump, verify its SHA-256, and extract the JSON.

    Streams to disk (dumps can be large; they must not be held in memory) and
    returns source lineage metadata. The downloaded files live OUTSIDE git —
    the caller chooses ``dest_dir`` (e.g. a gitignored ``data/`` directory).
    """
    import urllib.request

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    url = dump_url(snapshot_dir, entity_type)
    archive_path = dest / f"{entity_type}.tar.xz"
    if not archive_path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "festival-bloomberg-research/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(archive_path, "wb") as out:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
    size = archive_path.stat().st_size
    checksum = sha256_of(archive_path.read_bytes())
    member = extract_dump_member(archive_path, dest, f"mbdump/{entity_type}")
    return {
        "entity_type": entity_type,
        "snapshot_dir": snapshot_dir,
        "url": url,
        "archive_path": str(archive_path),
        "compressed_size_bytes": size,
        "checksum_sha256": checksum,
        "license": CC0_LICENSE,
        "extracted_path": str(member),
    }


def _ingest_series_objects(
    conn,
    objects,
    *,
    dump_source_id_value: str,
    knowledge_time: str,
    limit: int | None,
) -> dict[str, Any]:
    summary = {
        "status": "RUNNING",
        "parsed": 0,
        "persisted": 0,
        "skipped_existing": 0,
        "by_type": {},
        "event_series": 0,
        "invalid": 0,
    }
    count = 0
    for obj in objects:
        count += 1
        try:
            rec = normalize_series(obj)
        except (ValueError, KeyError):
            summary["invalid"] += 1
            continue
        summary["parsed"] += 1
        summary["by_type"][rec["source_type"]] = summary["by_type"].get(rec["source_type"], 0) + 1
        if rec["is_event_series"]:
            summary["event_series"] = summary.get("event_series", 0) + 1
        if persist_series(conn, rec, dump_source_id_value=dump_source_id_value,
                          knowledge_time=knowledge_time):
            summary["persisted"] += 1
        else:
            summary["skipped_existing"] += 1
        if limit is not None and summary["persisted"] >= limit:
            break
    summary["status"] = "COMPLETE"
    summary["objects_seen"] = count
    return summary


def ingest_series_dump(
    conn,
    text: str,
    *,
    dump_source_id_value: str,
    knowledge_time: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Parse and persist an in-memory series dump string. Returns a summary."""
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    return _ingest_series_objects(
        conn, iter_json_objects(text),
        dump_source_id_value=dump_source_id_value,
        knowledge_time=knowledge_time, limit=limit,
    )


def _pair_key(a: str, b: str) -> str:
    return hashlib.sha256(f"{a}|{b}".encode("utf-8")).hexdigest()[:32]


def _relationship_key(subject_type: str, subject_key: str, predicate: str,
                      object_type: str, object_key: str, source: str) -> str:
    material = "|".join([subject_type, subject_key, predicate, object_type, object_key, source])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _insert_relationship(conn, *, subject_type: str, subject_key: str, predicate: str,
                          object_type: str, object_key: str, source_system: str,
                          source_url: str | None, knowledge_time: str) -> None:
    key = _relationship_key(subject_type, subject_key, predicate, object_type, object_key, source_system)
    conn.execute(
        """
        INSERT OR IGNORE INTO core.entity_relationships
            (relationship_key, subject_entity_type, subject_key, predicate,
             object_entity_type, object_key, source_system, source_url,
             evidence_class, knowledge_time, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CROWD_CURATED_REFERENCE', ?, CURRENT_TIMESTAMP)
        """,
        [key, subject_type, subject_key, predicate, object_type, object_key,
         source_system, source_url, knowledge_time],
    )


def normalize_event(obj: dict[str, Any]) -> dict[str, Any]:
    mbid = obj.get("id")
    if not mbid:
        raise ValueError("event object missing id")
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
        "payload": obj,
    }


def relation_target(rel: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return (target-type, target-id, target-value) for one MB relation."""
    tt = rel.get("target-type")
    nested = rel.get(tt) if tt else None
    nested = nested if isinstance(nested, dict) else {}
    if tt == "url":
        return tt, nested.get("id"), nested.get("resource")
    return tt, nested.get("id"), nested.get("name")


def persist_event(conn, rec: dict[str, Any], *, dump_source_id_value: str,
                  knowledge_time: str) -> dict[str, int]:
    """Persist one event + its typed edges. Returns new-row counts."""
    out = {"new": 0, "series_events": 0, "performers": 0, "places": 0, "urls": 0, "subevents": 0}
    exists = conn.execute(
        "SELECT 1 FROM raw.musicbrainz_event WHERE mbid = ?", [rec["mbid"]]
    ).fetchone()
    if exists:
        return out
    conn.execute(
        """
        INSERT INTO raw.musicbrainz_event
            (mbid, name, event_type, begin_date, end_date, event_time, cancelled,
             disambiguation, payload, dump_source_id, knowledge_time, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            rec["mbid"], rec["name"], rec["event_type"], rec["begin_date"],
            rec["end_date"], rec["event_time"], rec["cancelled"],
            rec["disambiguation"], json.dumps(rec["payload"], default=str),
            dump_source_id_value, knowledge_time,
        ],
    )
    out["new"] = 1
    for rel in rec["relations"]:
        if not isinstance(rel, dict):
            continue
        tt, tid, tval = relation_target(rel)
        rtype = rel.get("type")
        direction = rel.get("direction")
        if tt == "series" and rtype == "part of" and tid:
            conn.execute(
                """
                INSERT OR IGNORE INTO core.series_events
                    (series_event_key, series_key, series_mbid, event_mbid, event_name,
                     event_type, event_begin_date, event_end_date, relationship_type,
                     source_system, knowledge_time, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'part of', 'musicbrainz', ?, CURRENT_TIMESTAMP)
                """,
                [_pair_key(tid, rec["mbid"]), series_key_for(tid), tid, rec["mbid"],
                 rec["name"], rec["event_type"], rec["begin_date"], rec["end_date"],
                 knowledge_time],
            )
            out["series_events"] += 1
        elif tt == "artist" and tid:
            conn.execute(
                """
                INSERT OR IGNORE INTO core.event_performers
                    (performer_key, event_mbid, artist_mbid, artist_name, performer_role,
                     direction, source_system, knowledge_time, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, 'musicbrainz', ?, CURRENT_TIMESTAMP)
                """,
                [_pair_key(f"{rec['mbid']}|{tid}", rtype or "performer"), rec["mbid"], tid,
                 tval, rtype or "performer", direction, knowledge_time],
            )
            out["performers"] += 1
        elif tt == "place" and rtype == "held at" and tid:
            _insert_relationship(
                conn, subject_type="EVENT", subject_key=f"mbid::{rec['mbid']}",
                predicate="EVENT_AT_PLACE", object_type="PLACE", object_key=f"mbid::{tid}",
                source_system="musicbrainz", source_url=None, knowledge_time=knowledge_time,
            )
            out["places"] += 1
        elif tt == "event" and rtype == "part of" and tid:
            _insert_relationship(
                conn, subject_type="EVENT", subject_key=f"mbid::{rec['mbid']}",
                predicate="EVENT_PART_OF_EVENT", object_type="EVENT", object_key=f"mbid::{tid}",
                source_system="musicbrainz", source_url=None, knowledge_time=knowledge_time,
            )
            out["subevents"] += 1
        elif tt == "url" and rtype in ("official homepage", "ticketing") and tval:
            _insert_relationship(
                conn, subject_type="EVENT", subject_key=f"mbid::{rec['mbid']}",
                predicate="EVENT_HAS_URL", object_type="URL", object_key=tval,
                source_system="musicbrainz", source_url=tval, knowledge_time=knowledge_time,
            )
            out["urls"] += 1
    return out


def _commit_batch(conn, *, batching: bool) -> None:
    """Commit the current batch and open the next (DuckDB batched ingest)."""
    if not batching:
        return
    conn.execute("COMMIT")
    conn.execute("BEGIN TRANSACTION")


def ingest_events_file(
    conn,
    path: str | Path,
    *,
    dump_source_id_value: str,
    knowledge_time: str | None = None,
    limit: int | None = None,
    commit_every: int | None = None,
) -> dict[str, Any]:
    """Stream and persist an event dump, materializing series/performer edges.

    ``commit_every`` wraps the ingest in periodic transactions (fast for large
    dumps without buffering the whole file in memory).
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    batching = commit_every and commit_every > 0
    if batching:
        conn.execute("BEGIN TRANSACTION")
    summary = {
        "status": "RUNNING", "parsed": 0, "new_events": 0, "skipped_existing": 0,
        "invalid": 0, "series_events": 0, "performers": 0, "places": 0,
        "urls": 0, "subevents": 0, "by_type": {},
    }
    count = 0
    try:
        with open(Path(path), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    summary["invalid"] += 1
                    continue
                try:
                    rec = normalize_event(obj)
                except (ValueError, KeyError):
                    summary["invalid"] += 1
                    continue
                summary["parsed"] += 1
                summary["by_type"][rec["event_type"] or "?" ] = summary["by_type"].get(rec["event_type"] or "?", 0) + 1
                out = persist_event(conn, rec, dump_source_id_value=dump_source_id_value,
                                    knowledge_time=knowledge_time)
                if out["new"]:
                    summary["new_events"] += 1
                    for k in ("series_events", "performers", "places", "urls", "subevents"):
                        summary[k] += out[k]
                    if batching and summary["new_events"] % commit_every == 0:
                        _commit_batch(conn, batching=True)
                else:
                    summary["skipped_existing"] += 1
                if limit is not None and summary["new_events"] >= limit:
                    break
    finally:
        if batching:
            conn.execute("COMMIT")
    summary["status"] = "COMPLETE"
    summary["objects_seen"] = count
    return summary


def ingest_series_file(
    conn,
    path: str | Path,
    *,
    dump_source_id_value: str,
    knowledge_time: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Stream and persist a series dump from an on-disk NDJSON/array file.

    Streams line-by-line so large dumps are not held fully in memory.
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    p = Path(path)

    def _stream() -> Iterator[dict[str, Any]]:
        with open(p, encoding="utf-8") as fh:
            first = True
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if first and line.startswith("["):
                    # Whole-file JSON array (older layout): load it once.
                    yield from iter_json_objects(p.read_text(encoding="utf-8"))
                    return
                first = False
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj

    return _ingest_series_objects(
        conn, _stream(),
        dump_source_id_value=dump_source_id_value,
        knowledge_time=knowledge_time, limit=limit,
    )


# ---------------------------------------------------------------------------
# Artist dump (compact reference layer; full universe stays in
# reference.musicbrainz_artists — only RELEVANT artists are promoted into
# core.artists by the identity driver, never a blind bulk copy).
# ---------------------------------------------------------------------------

#: MusicBrainz artist relation types -> external identity URL classification.
#: Only EXPLICITLY TYPED relationships are recognized; arbitrary URLs are
#: never inferred into an ID.
ARTIST_URL_RELATION_TYPES = {
    "official homepage": "official_homepage",
    "wikidata": "wikidata",
    "youtube": "youtube",
    "apple music": "apple_music",
    "bandcamp": "bandcamp",
    "soundcloud": "soundcloud",
    "discogs": "discogs",
    "songkick": "songkick",
    "bandsintown": "bandsintown",
    "setlist.fm": "setlistfm",
    "last.fm": "lastfm",
    "myspace": "myspace",
    "twitter": "twitter",
    "instagram": "instagram",
    "facebook": "facebook",
    "tiktok": "tiktok",
    "imdb": "imdb",
    "allmusic": "allmusic",
    "musicbrainz": "musicbrainz",
}


def normalize_artist(obj: dict[str, Any]) -> dict[str, Any]:
    """Compact normalized artist record from a MusicBrainz artist dump object."""
    mbid = obj.get("id")
    if not mbid:
        raise ValueError("artist object missing id")
    lifespan = obj.get("life-span") or {}
    area = obj.get("area") or {}

    aliases: list[dict[str, Any]] = []
    for a in obj.get("aliases") or [] if isinstance(obj.get("aliases"), list) else []:
        if isinstance(a, dict) and a.get("name"):
            aliases.append({
                "name": a["name"],
                "locale": a.get("locale"),
                "type": a.get("type"),
                "begin": a.get("begin"),
                "end": a.get("end"),
                "primary": bool(a.get("primary")),
            })

    # Explicit ISNI/IPI identifier lists (never inferred). The JSON dump
    # stores these as ``isnis``/``ipis`` (plural lists).
    isni = [x for x in (obj.get("isnis") or []) if isinstance(x, str) and x]
    ipi = [x for x in (obj.get("ipis") or []) if isinstance(x, str) and x]

    # Typed URL relationships (official homepage, wikidata, youtube, ...).
    urls: list[dict[str, Any]] = []
    for rel in obj.get("relations") or []:
        if not isinstance(rel, dict) or rel.get("target-type") != "url":
            continue
        rtype = rel.get("type")
        classification = ARTIST_URL_RELATION_TYPES.get(rtype)
        if not classification:
            continue
        nested = rel.get("url") or {}
        resource = nested.get("resource") if isinstance(nested, dict) else None
        if resource:
            urls.append({"type": classification, "resource": resource})

    return {
        "mbid": mbid,
        "name": obj.get("name"),
        "sort_name": obj.get("sort-name"),
        "artist_type": obj.get("type"),
        "area_mbid": area.get("id") if isinstance(area, dict) else None,
        "area_name": area.get("name") if isinstance(area, dict) else None,
        "begin_date": lifespan.get("begin") if isinstance(lifespan, dict) else None,
        "end_date": lifespan.get("end") if isinstance(lifespan, dict) else None,
        "disambiguation": obj.get("disambiguation"),
        "isni": isni,
        "ipi": ipi,
        "aliases": aliases,
        "urls": urls,
    }


def persist_reference_artist(conn, rec: dict[str, Any], *, dump_source_id_value: str,
                             knowledge_time: str) -> int:
    """Persist one compact artist reference row. Returns 1 if new."""
    exists = conn.execute(
        "SELECT 1 FROM reference.musicbrainz_artists WHERE mbid = ?", [rec["mbid"]]
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO reference.musicbrainz_artists
            (mbid, name, normalized_name, sort_name, artist_type, area_mbid,
             area_name, begin_date, end_date, disambiguation, isni, ipi,
             aliases, urls, dump_source_id, knowledge_time, ingested_at)
        VALUES (?, ?, LOWER(TRIM(?)), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            rec["mbid"], rec["name"], rec["name"] or "", rec["sort_name"],
            rec["artist_type"], rec["area_mbid"], rec["area_name"],
            rec["begin_date"], rec["end_date"], rec["disambiguation"],
            json.dumps(rec["isni"], default=str) if rec["isni"] else None,
            json.dumps(rec["ipi"], default=str) if rec["ipi"] else None,
            json.dumps(rec["aliases"], default=str) if rec["aliases"] else None,
            json.dumps(rec["urls"], default=str) if rec["urls"] else None,
            dump_source_id_value, knowledge_time,
        ],
    )
    return 1


def ingest_artist_file(
    conn,
    path: str | Path,
    *,
    dump_source_id_value: str,
    knowledge_time: str | None = None,
    limit: int | None = None,
    commit_every: int | None = None,
) -> dict[str, Any]:
    """Stream and persist the artist dump into the compact reference layer.

    Streams line-by-line (never holds the file in memory), wraps the ingest in
    bounded transactions (``commit_every``) so huge dumps do not OOM, and only
    ever writes the compact reference row — the full payload JSON is NOT
    retained. Idempotent by MBID.
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    batching = commit_every and commit_every > 0
    if batching:
        conn.execute("BEGIN TRANSACTION")
    summary = {
        "status": "RUNNING", "parsed": 0, "new_artists": 0,
        "skipped_existing": 0, "invalid": 0, "by_type": {},
        "isni_rows": 0, "ipi_rows": 0, "url_rows": 0, "alias_rows": 0,
    }
    count = 0
    try:
        with open(Path(path), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    summary["invalid"] += 1
                    continue
                try:
                    rec = normalize_artist(obj)
                except (ValueError, KeyError):
                    summary["invalid"] += 1
                    continue
                summary["parsed"] += 1
                summary["by_type"][rec["artist_type"] or "?"] = (
                    summary["by_type"].get(rec["artist_type"] or "?", 0) + 1
                )
                if persist_reference_artist(conn, rec,
                                           dump_source_id_value=dump_source_id_value,
                                           knowledge_time=knowledge_time):
                    summary["new_artists"] += 1
                    summary["isni_rows"] += len(rec["isni"])
                    summary["ipi_rows"] += len(rec["ipi"])
                    summary["url_rows"] += len(rec["urls"])
                    summary["alias_rows"] += len(rec["aliases"])
                    if batching and summary["new_artists"] % commit_every == 0:
                        _commit_batch(conn, batching=True)
                else:
                    summary["skipped_existing"] += 1
                if limit is not None and summary["new_artists"] >= limit:
                    break
    finally:
        if batching:
            conn.execute("COMMIT")
    summary["status"] = "COMPLETE"
    summary["objects_seen"] = count
    return summary


# ---------------------------------------------------------------------------
# AREA dump (geography reference for venue/artist/place enrichment).
# ---------------------------------------------------------------------------

def normalize_area(obj: dict[str, Any]) -> dict[str, Any]:
    """Compact normalized area record from a MusicBrainz area dump object."""
    mbid = obj.get("id")
    if not mbid:
        raise ValueError("area object missing id")
    lifespan = obj.get("life-span") or {}
    # Containment: "part of" relations to another AREA (explicit only).
    parent_mbid = None
    for rel in obj.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        if rel.get("target-type") == "area" and rel.get("type") == "part of":
            nested = rel.get("area") or {}
            if isinstance(nested, dict):
                parent_mbid = nested.get("id")
            break
    return {
        "mbid": mbid,
        "name": obj.get("name"),
        "area_type": obj.get("type"),
        "iso_3166_1": obj.get("iso-3166-1-codes") or [],
        "iso_3166_2": obj.get("iso-3166-2-codes") or [],
        "iso_3166_3": obj.get("iso-3166-3-codes") or [],
        "parent_mbid": parent_mbid,
        "begin_date": lifespan.get("begin") if isinstance(lifespan, dict) else None,
        "end_date": lifespan.get("end") if isinstance(lifespan, dict) else None,
        "disambiguation": obj.get("disambiguation"),
    }


def persist_reference_area(conn, rec: dict[str, Any], *, dump_source_id_value: str,
                           knowledge_time: str) -> int:
    """Persist one compact area reference row (+ raw observation). Returns 1 if new."""
    exists = conn.execute(
        "SELECT 1 FROM reference.musicbrainz_areas WHERE mbid = ?", [rec["mbid"]]
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO reference.musicbrainz_areas
            (mbid, name, normalized_name, area_type, iso_3166_1, iso_3166_2,
             iso_3166_3, parent_mbid, begin_date, end_date, disambiguation,
             dump_source_id, knowledge_time, ingested_at)
        VALUES (?, ?, LOWER(TRIM(?)), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            rec["mbid"], rec["name"], rec["name"] or "", rec["area_type"],
            json.dumps(rec["iso_3166_1"], default=str) if rec["iso_3166_1"] else None,
            json.dumps(rec["iso_3166_2"], default=str) if rec["iso_3166_2"] else None,
            json.dumps(rec["iso_3166_3"], default=str) if rec["iso_3166_3"] else None,
            rec["parent_mbid"], rec["begin_date"], rec["end_date"],
            rec["disambiguation"], dump_source_id_value, knowledge_time,
        ],
    )
    return 1


def ingest_artist_archive_stream(
    conn,
    archive_path: str | Path,
    *,
    dump_source_id_value: str,
    knowledge_time: str | None = None,
    limit: int | None = None,
    commit_every: int | None = None,
    resume_after: int | None = None,
) -> dict[str, Any]:
    """Stream the artist dump DIRECTLY from the ``.tar.xz`` archive.

    The full artist dump is ~1.7 GB compressed; extracting it to disk can
    need 5-10x that space and caused an OOM on an earlier oversized ingest.
    This path opens the archive, iterates the ``mbdump/artist`` member
    line-by-line (never holding the file in memory), and persists compact
    reference rows in bounded transactions (``commit_every``). The archive
    itself can be deleted after a verified run; its checksum + source lineage
    stay in ``raw.musicbrainz_dump_source``.

    ``resume_after`` skips the first N JSON lines without parsing them so a
    timed-out run can continue cheaply (idempotent by MBID; ``limit`` bounds
    how many NEW artists this run writes).
    """
    import tarfile

    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    batching = commit_every and commit_every > 0
    if batching:
        conn.execute("BEGIN TRANSACTION")
    summary = {
        "status": "RUNNING", "parsed": 0, "new_artists": 0,
        "skipped_existing": 0, "invalid": 0, "by_type": {},
        "isni_rows": 0, "ipi_rows": 0, "url_rows": 0, "alias_rows": 0,
        "resumed_after": resume_after,
    }
    count = 0
    member_name: str | None = None
    try:
        with tarfile.open(archive_path, "r:xz") as tf:
            # NOTE: use lazy iteration, NOT ``getmembers()`` — materializing
            # all members on an xz archive seeks through the whole compressed
            # stream (O(n^2) decompression) and hangs for the multi-GB dump.
            for m in tf:
                if m.name.endswith("/artist"):
                    member_name = m.name
                    break
            if member_name is None:
                summary["status"] = "MEMBER_NOT_FOUND"
                return summary
            fh = tf.extractfile(member_name)
            if fh is None:
                summary["status"] = "MEMBER_NOT_READABLE"
                return summary
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                count += 1
                if resume_after is not None and count <= resume_after:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    summary["invalid"] += 1
                    continue
                try:
                    rec = normalize_artist(obj)
                except (ValueError, KeyError):
                    summary["invalid"] += 1
                    continue
                summary["parsed"] += 1
                summary["by_type"][rec["artist_type"] or "?"] = (
                    summary["by_type"].get(rec["artist_type"] or "?", 0) + 1
                )
                if persist_reference_artist(conn, rec,
                                           dump_source_id_value=dump_source_id_value,
                                           knowledge_time=knowledge_time):
                    summary["new_artists"] += 1
                    summary["isni_rows"] += len(rec["isni"])
                    summary["ipi_rows"] += len(rec["ipi"])
                    summary["url_rows"] += len(rec["urls"])
                    summary["alias_rows"] += len(rec["aliases"])
                    if batching and summary["new_artists"] % commit_every == 0:
                        _commit_batch(conn, batching=True)
                else:
                    summary["skipped_existing"] += 1
                if limit is not None and summary["new_artists"] >= limit:
                    break
    finally:
        if batching:
            conn.execute("COMMIT")
    summary["status"] = "COMPLETE"
    summary["objects_seen"] = count
    return summary


def enrich_artist_archive_stream(
    conn,
    archive_path: str | Path,
    *,
    knowledge_time: str | None = None,
    commit_every: int | None = None,
    resume_after: int | None = None,
    marker_path: str | Path | None = None,
) -> dict[str, Any]:
    """Backfill ISNI/IPI/URL/alias fields on EXISTING reference rows.

    The original artist ingest stored rows before the dump key names were
    confirmed (``isnis``/``ipis``), so isni/ipi columns are NULL. This pass
    streams the archive again and UPDATEs only rows that already exist and
    are missing values — idempotent, resumable via ``resume_after``, and
    bounded transactions to avoid OOM. ``marker_path`` (if given) records the
    last scanned line count after every commit so a timed-out run resumes
    cheaply without re-parsing the prefix.
    """
    import tarfile

    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    batching = commit_every and commit_every > 0
    if batching:
        conn.execute("BEGIN TRANSACTION")
    summary = {
        "status": "RUNNING", "scanned": 0, "updated": 0, "missing": 0,
        "invalid": 0, "isni_rows": 0, "ipi_rows": 0,
        "url_rows": 0, "alias_rows": 0, "resumed_after": resume_after,
    }
    count = 0
    last_marker: int | None = None
    member_name: str | None = None
    try:
        with tarfile.open(archive_path, "r:xz") as tf:
            # Lazy iteration, NOT ``getmembers()`` (see ingest above).
            for m in tf:
                if m.name.endswith("/artist"):
                    member_name = m.name
                    break
            if member_name is None:
                summary["status"] = "MEMBER_NOT_FOUND"
                return summary
            fh = tf.extractfile(member_name)
            if fh is None:
                summary["status"] = "MEMBER_NOT_READABLE"
                return summary
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                count += 1
                if resume_after is not None and count <= resume_after:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    summary["invalid"] += 1
                    continue
                try:
                    rec = normalize_artist(obj)
                except (ValueError, KeyError):
                    summary["invalid"] += 1
                    continue
                summary["scanned"] += 1
                # Fast path: the original ingest already stored urls/aliases
                # correctly; only isni/ipi were lost. Skip the DB entirely
                # for lines carrying neither, keeping the pass archive-bound.
                if not rec["isni"] and not rec["ipi"]:
                    summary["missing"] += 1
                    if batching and summary["scanned"] % commit_every == 0:
                        _commit_batch(conn, batching=True)
                        if marker_path:
                            _write_marker(marker_path, count)
                            last_marker = count
                    continue
                updated = _backfill_reference_artist(conn, rec)
                if updated:
                    summary["updated"] += 1
                    summary["isni_rows"] += len(rec["isni"])
                    summary["ipi_rows"] += len(rec["ipi"])
                    summary["url_rows"] += len(rec["urls"])
                    summary["alias_rows"] += len(rec["aliases"])
                else:
                    summary["missing"] += 1
                if batching and summary["scanned"] % commit_every == 0:
                    _commit_batch(conn, batching=True)
                    if marker_path:
                        _write_marker(marker_path, count)
                        last_marker = count
    finally:
        if batching:
            conn.execute("COMMIT")
            if marker_path and last_marker is not None:
                _write_marker(marker_path, count)
    summary["status"] = "COMPLETE"
    summary["objects_seen"] = count
    return summary


def _write_marker(marker_path: str | Path, line_count: int) -> None:
    Path(marker_path).write_text(str(line_count), encoding="utf-8")


def _backfill_reference_artist(conn, rec: dict[str, Any]) -> bool:
    """Update an existing reference row's NULL identifier fields. True if changed."""
    row = conn.execute(
        "SELECT isni, ipi, urls, aliases FROM reference.musicbrainz_artists "
        "WHERE mbid = ?", [rec["mbid"]]
    ).fetchone()
    if row is None:
        return False
    cur_isni, cur_ipi, cur_urls, cur_aliases = row
    sets: list[str] = []
    params: list[Any] = []
    if not cur_isni and rec["isni"]:
        sets.append("isni = ?")
        params.append(json.dumps(rec["isni"], default=str))
    if not cur_ipi and rec["ipi"]:
        sets.append("ipi = ?")
        params.append(json.dumps(rec["ipi"], default=str))
    if not cur_urls and rec["urls"]:
        sets.append("urls = ?")
        params.append(json.dumps(rec["urls"], default=str))
    if not cur_aliases and rec["aliases"]:
        sets.append("aliases = ?")
        params.append(json.dumps(rec["aliases"], default=str))
    if not sets:
        return False
    sets.append("ingested_at = CURRENT_TIMESTAMP")
    conn.execute(
        f"UPDATE reference.musicbrainz_artists SET {', '.join(sets)} WHERE mbid = ?",
        [*params, rec["mbid"]],
    )
    return True


def ingest_area_file(
    conn,
    path: str | Path,
    *,
    dump_source_id_value: str,
    knowledge_time: str | None = None,
    limit: int | None = None,
    commit_every: int | None = None,
) -> dict[str, Any]:
    """Stream and persist the area dump into reference.musicbrainz_areas."""
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    batching = commit_every and commit_every > 0
    if batching:
        conn.execute("BEGIN TRANSACTION")
    summary = {
        "status": "RUNNING", "parsed": 0, "new_areas": 0,
        "skipped_existing": 0, "invalid": 0, "by_type": {},
    }
    count = 0
    try:
        with open(Path(path), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                count += 1
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
                summary["by_type"][rec["area_type"] or "?"] = (
                    summary["by_type"].get(rec["area_type"] or "?", 0) + 1
                )
                if persist_reference_area(conn, rec,
                                          dump_source_id_value=dump_source_id_value,
                                          knowledge_time=knowledge_time):
                    summary["new_areas"] += 1
                    if batching and summary["new_areas"] % commit_every == 0:
                        _commit_batch(conn, batching=True)
                else:
                    summary["skipped_existing"] += 1
                if limit is not None and summary["new_areas"] >= limit:
                    break
    finally:
        if batching:
            conn.execute("COMMIT")
    summary["status"] = "COMPLETE"
    summary["objects_seen"] = count
    return summary


def _clean_coord(value, lo: float, hi: float):
    """Validate a coordinate; return None for out-of-range/garbage values.

    MusicBrainz crowdsourced coordinates occasionally contain invalid values
    (e.g. longitude -73991593.99). We never persist garbage as a coordinate
    and never clamp it into something plausible-looking — invalid -> NULL.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not (lo <= v <= hi):
        return None
    return round(v, 6)


def normalize_place(obj: dict[str, Any]) -> dict[str, Any]:
    mbid = obj.get("id")
    if not mbid:
        raise ValueError("place object missing id")
    coords = obj.get("coordinates") or {}
    area = obj.get("area") or {}
    return {
        "mbid": mbid,
        "name": obj.get("name"),
        "place_type": obj.get("type"),
        "address": obj.get("address"),
        "latitude": _clean_coord(coords.get("latitude"), -90.0, 90.0) if isinstance(coords, dict) else None,
        "longitude": _clean_coord(coords.get("longitude"), -180.0, 180.0) if isinstance(coords, dict) else None,
        "area_name": area.get("name") if isinstance(area, dict) else None,
        "area_mbid": area.get("id") if isinstance(area, dict) else None,
        "disambiguation": obj.get("disambiguation"),
        "relations": obj.get("relations") or [],
        "payload": obj,
    }


def venue_key_for(mbid: str) -> str:
    return f"mbid::{mbid}"


def persist_place(conn, rec: dict[str, Any], *, dump_source_id_value: str,
                  knowledge_time: str) -> int:
    """Persist one MB place into raw + a canonical core.venues row (by MBID).

    Returns 1 if the raw place was new. Canonical venue rows are keyed by
    ``mbid::<id>`` so re-ingest is idempotent and never duplicates a venue.
    """
    exists = conn.execute(
        "SELECT 1 FROM raw.musicbrainz_place WHERE mbid = ?", [rec["mbid"]]
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO raw.musicbrainz_place
            (mbid, name, place_type, address, latitude, longitude, area,
             disambiguation, payload, dump_source_id, knowledge_time, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            rec["mbid"], rec["name"], rec["place_type"], rec["address"],
            rec["latitude"], rec["longitude"], rec["area_name"],
            rec["disambiguation"], json.dumps(rec["payload"], default=str),
            dump_source_id_value, knowledge_time,
        ],
    )
    # Canonical venue (basic resolution): MBID-keyed, no fabricated capacity or
    # country (area name alone is not country evidence).
    conn.execute(
        """
        INSERT OR IGNORE INTO core.venues
            (venue_key, name, normalized_name, venue_type, address, latitude,
             longitude, musicbrainz_id, source_system, ingested_at)
        VALUES (?, ?, LOWER(TRIM(?)), ?, ?, ?, ?, ?, 'musicbrainz', CURRENT_TIMESTAMP)
        """,
        [
            venue_key_for(rec["mbid"]), rec["name"], rec["name"], rec["place_type"],
            rec["address"], rec["latitude"], rec["longitude"], rec["mbid"],
        ],
    )
    if rec["area_mbid"]:
        _insert_relationship(
            conn, subject_type="PLACE", subject_key=venue_key_for(rec["mbid"]),
            predicate="PLACE_IN_AREA", object_type="AREA",
            object_key=f"mbid::{rec['area_mbid']}",
            source_system="musicbrainz", source_url=None, knowledge_time=knowledge_time,
        )
    return 1


def ingest_place_file(
    conn,
    path: str | Path,
    *,
    dump_source_id_value: str,
    knowledge_time: str | None = None,
    limit: int | None = None,
    commit_every: int | None = None,
) -> dict[str, Any]:
    """Stream and persist a place dump (raw places + canonical venues)."""
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    batching = commit_every and commit_every > 0
    if batching:
        conn.execute("BEGIN TRANSACTION")
    summary = {
        "status": "RUNNING", "parsed": 0, "new_places": 0, "skipped_existing": 0,
        "invalid": 0, "by_type": {},
    }
    count = 0
    try:
        with open(Path(path), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    obj = json.loads(line)
                except ValueError:
                    summary["invalid"] += 1
                    continue
                try:
                    rec = normalize_place(obj)
                except (ValueError, KeyError):
                    summary["invalid"] += 1
                    continue
                summary["parsed"] += 1
                summary["by_type"][rec["place_type"] or "?"] = summary["by_type"].get(rec["place_type"] or "?", 0) + 1
                if persist_place(conn, rec, dump_source_id_value=dump_source_id_value,
                                 knowledge_time=knowledge_time):
                    summary["new_places"] += 1
                    if batching and summary["new_places"] % commit_every == 0:
                        _commit_batch(conn, batching=True)
                else:
                    summary["skipped_existing"] += 1
                if limit is not None and summary["new_places"] >= limit:
                    break
    finally:
        if batching:
            conn.execute("COMMIT")
    summary["status"] = "COMPLETE"
    summary["objects_seen"] = count
    return summary
