"""P4 — Wikidata music subgraph: stream the truthy NT.bz2 from R2 and extract
music-relevant identity facts into Silver Parquet.

Input (RAW, never downloaded locally — streamed from R2):
    festival-intelligence-raw/bulk/wikidata/dump=latest-truthy/latest-truthy.nt.bz2
    (~40.35 GB bzip2, N-Triples, subject-grouped in dump order)

Extraction strategy:
    The dump is huge but each subject's triples are contiguous. We stream
    triple-by-triple, buffer per-subject blocks, and emit a subject only when it
    carries at least one whitelisted music-identity property. Memory is bounded
    to one subject block at a time.

Whitelisted properties (identity + enrichment only, no generic crawl):
    P434  MusicBrainz artist ID      P1566 GeoNames ID
    P435  MusicBrainz label ID       P625  coordinates
    P1650 MusicBrainz place ID       P17   country
    P1902 Spotify artist ID         P131  located in admin unit
    P2397 YouTube channel ID        P571  inception (founding)
    P1953 Discogs artist ID         P495  country of origin
    P213  ISNI                       P31   instance-of (kept only for
    P214  VIAF ID                          entity typing of kept subjects)
    P856  official website

Outputs → immutable generation under
``silver/wikidata/generations/<run_id>/``. ``silver/wikidata/CURRENT.json``
points to the complete published generation. Products include music entities,
types, external IDs, coordinates, locations, websites, inception dates,
genres, and typed relationships.

All rows carry source_system='wikidata', knowledge_time, and ingested_at.
No inference beyond what the dump states.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/build_wikidata_music_graph.py
    PYTHONPATH=python .venv/bin/python scripts/build_wikidata_music_graph.py --limit 500000
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import io
import json
import os
import re
import resource
import shutil
import subprocess
import threading
import time
from collections import Counter
from queue import Queue

import pyarrow as pa
import pyarrow.parquet as pq

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from festival_bloomberg.lake.r2 import r2_client  # noqa: E402

RAW_BUCKET = "festival-intelligence-raw"
LAKE_BUCKET = "festival-intelligence-lake"
RAW_KEY = "bulk/wikidata/dump=latest-truthy/latest-truthy.nt.bz2"
DUMP_VERSION = "latest-truthy-20260828"
RAW_BYTES = 43_329_477_419
RAW_ETAG = "7240fc164e418c27eac9e3ade4ad71b2-646"
SOURCE_SYSTEM = "wikidata"

# Local spill dir for incremental parquet (bounds RAM on the full ~40 GB scan).
SPILL_DIR = "/tmp/wd_spill"
SPILL_EVERY = int(os.environ.get("WD_SPILL_EVERY", "1000000"))
MAX_ATTEMPTS = 300           # connection-break retries before giving up
MAX_FINAL_BUFFER_BYTES = int(os.environ.get("WD_MAX_FINAL_BUFFER_BYTES", str(512 * 1024 * 1024)))

# subject predicate object .
NT_LINE = re.compile(r"^\s*<([^>]+)>\s+<([^>]+)>\s+(.+?)\s*\.\s*$")

# property id (without ns prefix) → our name
PROP_NS = "http://www.wikidata.org/prop/direct/"
ENTITY_NS = "http://www.wikidata.org/entity/"

KEEP_PROPS = {
    "P434": "musicbrainz_artist_id",
    "P435": "musicbrainz_label_id",
    "P1650": "musicbrainz_place_id",
    "P1902": "spotify_artist_id",
    "P2397": "youtube_channel_id",
    "P1953": "discogs_artist_id",
    "P1566": "geonames_id",
    "P213": "isni",
    "P214": "viaf_id",
    "P856": "official_website",
    "P625": "coordinates",
    "P17": "country",
    "P27": "citizenship",
    "P131": "located_in",
    "P571": "inception",
    "P31": "instance_of",
    "P495": "country_of_origin",
    # Typed graph enrichment.  These are deliberately normalized into their
    # own products; they are not external identifiers.
    "P136": "genre",
    "P527": "has_part",
    "P361": "part_of",
    "P175": "performer",
    "P463": "member_of",
}

EXTERNAL_ID_PROPS = {
    "P434", "P435", "P1650", "P1902", "P2397", "P1953", "P1566",
    "P213", "P214",
}

# Properties that establish that a subject belongs in this bounded music
# identity graph.  P31 and enrichment fields are retained for an anchored
# subject, but they are not anchors themselves; treating P31 as an anchor
# would keep nearly every typed Wikidata entity and defeat the bounded scope.
IDENTITY_ANCHOR_PROPS = {
    "P434",   # MusicBrainz artist
    "P435",   # MusicBrainz label
    "P1650",  # MusicBrainz place
    "P1902",  # Spotify artist
    "P2397",  # YouTube channel
    "P1953",  # Discogs artist
}
GENERIC_ID_PROPS = {"P213", "P214", "P1566"}  # require a music/place class

# Conservative artist-like classes. Generic person classes are intentionally
# excluded so an ISNI/VIAF does not pull every identified human into the graph.
ARTIST_CLASSES = {
    "Q215380",   # musical group / musical ensemble
    "Q639669",   # musician
    "Q488205",   # singer-songwriter
    "Q177220",   # singer
    "Q2865819",  # lyricist
    "Q1278468",  # choir
    "Q18223738", # group of musicians
    "Q4438121",  # orchestra
}
# Venue/place-like classes
VENUE_CLASSES = {
    "Q173432",   # venue? (building)
    "Q811430",   # concert hall
    "Q588140",   # nightclub
    "Q153562",   # opera house
    "Q483110",   # stadium
    "Q641226",   # arena
    "Q24354",    # theatre
    "Q878304",   # amphitheatre
}
# Generic places remain in the entity/type product, but are never promoted to
# LIVE_MUSIC_VENUE.  A building is not evidence that live music occurs there.
PLACE_CLASSES = {
    "Q41176",    # building
    "Q847017",   # broad place class retained only as PLACE
    "Q187456",   # broad place class retained only as PLACE
    "Q1215720",  # cultural center / broad place class
}
FESTIVAL_CLASSES = {
    "Q132241",   # music festival
    "Q40056",    # festival? (Q40056 = festival)
    "Q40056",
}

KEEP_CLASSES = ARTIST_CLASSES | VENUE_CLASSES | PLACE_CLASSES | FESTIVAL_CLASSES
# bytes variants so the hot consumer loop never decodes the raw NT lines
_ARTIST_CLASSES_B = {c.encode() for c in ARTIST_CLASSES}
_VENUE_CLASSES_B = {c.encode() for c in VENUE_CLASSES}
_PLACE_CLASSES_B = {c.encode() for c in PLACE_CLASSES}
_FESTIVAL_CLASSES_B = {c.encode() for c in FESTIVAL_CLASSES}
KEEP_CLASSES_B = _ARTIST_CLASSES_B | _VENUE_CLASSES_B | _PLACE_CLASSES_B | _FESTIVAL_CLASSES_B
KEEP_PROPS_B = {p.encode() for p in KEEP_PROPS}
NT_LINE_B = re.compile(rb"^\s*<([^>]+)>\s+<([^>]+)>\s+(.+?)\s*\.\s*$")

_UUID_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def normalize_external_id(prop: str, raw: bytes | str) -> str | None:
    """Normalize only identifiers that satisfy the provider's stable shape."""
    value = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    value = value.strip()
    if prop in {"P434", "P435", "P1650"}:
        value = value.lower()
        return value if _UUID_ID.fullmatch(value) else None
    if prop == "P1902":
        return value if _SPOTIFY_ID.fullmatch(value) else None
    if prop == "P2397":
        return value if _YOUTUBE_CHANNEL_ID.fullmatch(value) else None
    if prop in {"P1953", "P1566", "P214"}:
        return value if value.isdigit() else None
    if prop == "P213":
        compact = value.replace(" ", "").replace("-", "").upper()
        return compact if re.fullmatch(r"\d{15}[\dX]", compact) else None
    return None

_COMMON_FIELDS = [
    pa.field("source_system", pa.string()),
    pa.field("knowledge_time", pa.string()),
    pa.field("ingested_at", pa.string()),
]
# Explicit schemas keep spill shards + final tables identical across batches.
OUTPUT_SCHEMAS = {
    "music_entities": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("classification", pa.string()),
    ] + _COMMON_FIELDS),
    "entity_types": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("type_qid", pa.string()),
    ] + _COMMON_FIELDS),
    "entity_ids": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "artist_ids": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "venue_ids": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "place_ids": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("classification", pa.string()),
        pa.field("external_id_property", pa.string()),
        pa.field("external_id_name", pa.string()),
        pa.field("external_id_value", pa.string()),
    ] + _COMMON_FIELDS),
    "coordinates": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("longitude", pa.float64()),
        pa.field("latitude", pa.float64()),
    ] + _COMMON_FIELDS),
    "locations": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("location_property", pa.string()),
        pa.field("location_qid", pa.string()),
    ] + _COMMON_FIELDS),
    "websites": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("url", pa.string()),
    ] + _COMMON_FIELDS),
    "inceptions": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("inception", pa.string()),
    ] + _COMMON_FIELDS),
    "genres": pa.schema([
        pa.field("qid", pa.string()),
        pa.field("genre_qid", pa.string()),
    ] + _COMMON_FIELDS),
    "relationships": pa.schema([
        pa.field("subject_qid", pa.string()),
        pa.field("relationship_property", pa.string()),
        pa.field("object_qid", pa.string()),
    ] + _COMMON_FIELDS),
}


ENTITY_NS_B = ENTITY_NS.encode()
PROP_NS_B = PROP_NS.encode()

# Match only direct-property triples that can affect an output. The full
# Wikidata dump is overwhelmingly unrelated to this bounded music subgraph;
# parsing every N-Triples line with the generic matcher made the Python regex
# the wall-clock bottleneck. Folding the namespace and property allow-list
# into one compiled expression preserves the exact retained triples while
# rejecting unrelated lines in the regex engine's fast path.
_KEEP_PROP_PATTERN_B = b"|".join(
    re.escape(prop)
    for prop in sorted(KEEP_PROPS_B, key=lambda value: (len(value), value))
)
KEEP_NT_LINE_B = re.compile(
    rb"^\s*<([^>]+)>\s+<" + re.escape(PROP_NS_B) + rb"("
    + _KEEP_PROP_PATTERN_B + rb")>\s+(.+?)\s*\.\s*$"
)


def qid(uri):
    """Strip the entity namespace; accepts bytes or str."""
    prefix = ENTITY_NS_B if isinstance(uri, bytes) else ENTITY_NS
    return uri[len(prefix):] if uri.startswith(prefix) else uri


def object_value(raw: bytes) -> bytes:
    """Extract the value from an N-Triples object token (bytes in/out)."""
    if raw.startswith(b"<"):
        return raw[1:raw.index(b">")]
    if raw.startswith(b'"'):
        # "literal" or "literal"@lang / ^^^datatype — keep lexical form
        end = raw.rindex(b'"')
        return raw[1:end]
    return raw


def is_uri(raw) -> bool:
    """True for IRI tokens; accepts bytes or str."""
    return raw.startswith(b"<") if isinstance(raw, bytes) else raw.startswith("<")


def should_keep_subject(props: dict[str, list[bytes]], p31: set[bytes]) -> bool:
    """Return whether a subject is anchored to the bounded music graph."""
    if any(
        normalize_external_id(prop, value) is not None
        for prop, values in props.items()
        if prop in IDENTITY_ANCHOR_PROPS
        for value in values
    ):
        return True
    return bool(p31 & KEEP_CLASSES_B) and any(
        normalize_external_id(prop, value) is not None
        for prop, values in props.items()
        if prop in GENERIC_ID_PROPS
        for value in values
    )


def stream_nt_lines(bucket: str, key: str, queue: Queue, chunk: int = 1 << 20,
                    start_consumed: int = 0):
    """Producer: stream bzip2 from R2, yield decompressed lines.

    Connection breaks are retried with an open-ended Range GET resumed from the
    last *compressed* byte successfully fed to the decompressor, so the bz2
    stream stays byte-contiguous (same decompressor instance across retries).
    The stream is intentionally restart-from-zero.  A compressed-byte
    checkpoint cannot safely resume the subject reducer without a durable
    reducer checkpoint, and replaying a block would duplicate rows.
    """
    s3 = r2_client()
    dec = bz2.BZ2Decompressor()
    tail = b""
    consumed = 0
    attempts = 0
    # Lines are queued in batches: per-line queue.put on a threading.Queue is
    # the pipeline bottleneck (~5µs each → ~1 MB/s); batching makes it ~100x
    # cheaper and the pipeline becomes regex/decompress-bound instead.
    batch: list[bytes] = []
    if start_consumed:
        raise RuntimeError(
            "compressed checkpoint resume is disabled; restart the reducer from byte zero"
        )
    try:
        while True:
            try:
                if consumed:
                    resp = s3.get_object(Bucket=bucket, Key=key,
                                         Range=f"bytes={consumed}-")
                else:
                    resp = s3.get_object(Bucket=bucket, Key=key)
            except Exception as e:  # noqa: BLE001 — request failed before streaming
                attempts += 1
                if attempts > MAX_ATTEMPTS:
                    queue.put(RuntimeError(f"get_object retries exhausted: {e!r}"))
                    return
                time.sleep(min(30, 2 * attempts))
                continue
            body = resp["Body"]
            broken = False
            while True:
                try:
                    compressed = body.read(chunk)
                except Exception as e:  # noqa: BLE001 — mid-stream connection break
                    broken = True
                    attempts += 1
                    if attempts > MAX_ATTEMPTS:
                        queue.put(e)
                        return
                    time.sleep(min(30, 2 * attempts))
                    break
                if not compressed:
                    break  # true end of stream
                data = dec.decompress(compressed)
                consumed += len(compressed)
                if data:
                    lines = (tail + data).split(b"\n")
                    tail = lines.pop()
                    batch.extend(lines)
                    if len(batch) >= 4096:
                        queue.put(batch)
                        batch = []
            if not broken:
                break  # clean EOF of the whole object
    except Exception as e:  # noqa: BLE001
        queue.put(e)
    if tail:
        batch.append(tail)
    if batch:
        queue.put(batch)
    queue.put(None)  # sentinel


def read_lines(queue: Queue):
    while True:
        item = queue.get()
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def parallel_decompressed_batches(batch_size: int = 4096):
    """Stream R2 through rclone + pbzip2 and yield decompressed line batches.

    Python's single-core ``bz2`` decoder is the production bottleneck for the
    43 GB truthy dump.  The external pipeline keeps the source remote (no local
    43 GB copy), uses parallel bzip2 blocks, and is supervised here so an
    upstream failure cannot be mistaken for a clean EOF and publish a partial
    Silver build.
    """
    rclone_bin = os.environ.get("RCLONE_BIN") or shutil.which("rclone")
    pbzip2_bin = os.environ.get("PBZIP2_BIN") or shutil.which("pbzip2")
    if not rclone_bin or not pbzip2_bin:
        raise RuntimeError("parallel decompression requires rclone and pbzip2")
    workers = max(1, int(os.environ.get("WD_PBZIP2_WORKERS", "6")))
    source = f"r2:{RAW_BUCKET}/{RAW_KEY}"
    rclone = subprocess.Popen(
        [rclone_bin, "cat", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert rclone.stdout is not None
    decompressor = subprocess.Popen(
        [pbzip2_bin, "-dc", f"-p{workers}"],
        stdin=rclone.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rclone.stdout.close()
    assert decompressor.stdout is not None
    batch: list[bytes] = []
    try:
        for raw_line in decompressor.stdout:
            batch.append(raw_line.rstrip(b"\n"))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
        decompressor.stdout.close()
        decompressor_error = (decompressor.stderr.read() if decompressor.stderr else b"")
        decompressor_rc = decompressor.wait()
        rclone_error = (rclone.stderr.read() if rclone.stderr else b"")
        rclone_rc = rclone.wait()
        if decompressor_rc or rclone_rc:
            raise RuntimeError(
                "parallel Wikidata stream failed: "
                f"rclone_rc={rclone_rc} pbzip2_rc={decompressor_rc} "
                f"rclone={rclone_error[-500:].decode('utf-8', 'replace')!r} "
                f"pbzip2={decompressor_error[-500:].decode('utf-8', 'replace')!r}"
            )
    finally:
        for process in (decompressor, rclone):
            if process.poll() is None:
                process.terminate()
        for process in (decompressor, rclone):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


class SubgraphBuilder:
    def __init__(self, run_id: str | None = None) -> None:
        self.stats: Counter = Counter()
        self.run_id = run_id or os.environ.get("WD_RUN_ID") or (
            time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}"
        )
        self.music_entities: list[dict] = []
        self.entity_types: list[dict] = []
        self.entity_ids: list[dict] = []
        self.artist_ids: list[dict] = []
        self.venue_ids: list[dict] = []
        self.place_ids: list[dict] = []
        self.coordinates: list[dict] = []
        self.locations: list[dict] = []
        self.websites: list[dict] = []
        self.inceptions: list[dict] = []
        self.genres: list[dict] = []
        self.relationships: list[dict] = []
        self.now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Incremental spill state — flushes accumulated rows to parquet so the
        # full ~40 GB scan stays far below the 8 GB RAM ceiling. Spill shards
        # are uploaded to R2 as they are produced (the run's row volume ~5 GB
        # exceeds local free disk), so local disk stays near-zero.
        self._s3 = r2_client()
        self.spill_dir = Path(SPILL_DIR) / self.run_id
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        # Never delete another run's shards at startup.  A run-scoped prefix
        # prevents concurrent launchd/manual invocations from corrupting one
        # another; only this run's verified shards are removed after publish.
        self.spill_prefix = f"silver/wikidata/_spill/{self.run_id}/"
        self._pending = 0
        self.spill_paths: dict[str, list[str]] = {
            n: [] for n in ("music_entities", "entity_types", "entity_ids",
                            "artist_ids", "venue_ids", "place_ids", "coordinates",
                            "locations", "websites", "inceptions", "genres",
                            "relationships")}

    def classify(self, p31_values: set) -> str:
        if p31_values & _ARTIST_CLASSES_B:
            return "ARTIST"
        if p31_values & _VENUE_CLASSES_B:
            return "LIVE_MUSIC_VENUE"
        if p31_values & _PLACE_CLASSES_B:
            return "PLACE"
        if p31_values & _FESTIVAL_CLASSES_B:
            return "FESTIVAL"
        return "OTHER"

    def emit(self, subject_qid_b: bytes, props: dict[str, list[bytes]],
             p31: set) -> None:
        kind = self.classify(p31)
        self.stats[f"kept_{kind}"] += 1
        subject_qid = qid(subject_qid_b).decode("utf-8", "replace")
        self.music_entities.append({
            "qid": subject_qid, "classification": kind,
            "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
            "ingested_at": self.now,
        })
        for type_qid_b in sorted(p31):
            type_qid = qid(type_qid_b).decode("utf-8", "replace")
            self.entity_types.append({
                "qid": subject_qid, "type_qid": type_qid,
                "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                "ingested_at": self.now,
            })
        row = {"qid": subject_qid, "classification": kind,
               "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
               "ingested_at": self.now}
        for prop, name in KEEP_PROPS.items():
            if prop not in props:
                continue
            for val_b in props[prop]:
                val = val_b.decode("utf-8", "replace")
                r = dict(row)
                if prop == "P625":
                    # coordinates come as Point(lon lat)
                    m = re.match(r"Point\((-?[\d.]+) (-?[\d.]+)\)", val)
                    if not m:
                        continue
                    self.coordinates.append({
                        "qid": subject_qid, "longitude": float(m.group(1)),
                        "latitude": float(m.group(2)),
                        "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                        "ingested_at": self.now,
                    })
                elif prop in ("P17", "P27", "P131", "P495"):
                    # object_value() has already removed angle brackets, so
                    # test the retained Wikidata namespace rather than calling
                    # is_uri() on the lexical value.
                    if val_b.startswith(ENTITY_NS_B):
                        self.locations.append({
                            "qid": subject_qid, "location_property": name,
                            "location_qid": qid(val_b).decode("utf-8", "replace"),
                            "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                            "ingested_at": self.now,
                        })
                elif prop == "P856":
                    self.websites.append({
                        "qid": subject_qid, "url": val,
                        "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                        "ingested_at": self.now,
                    })
                elif prop == "P571":
                    self.inceptions.append({
                        "qid": subject_qid, "inception": val,
                        "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                        "ingested_at": self.now,
                    })
                elif prop == "P136":
                    if val_b.startswith(ENTITY_NS_B):
                        self.genres.append({
                            "qid": subject_qid,
                            "genre_qid": qid(val_b).decode("utf-8", "replace"),
                            "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                            "ingested_at": self.now,
                        })
                elif prop in ("P527", "P361", "P175", "P463"):
                    if val_b.startswith(ENTITY_NS_B):
                        self.relationships.append({
                            "subject_qid": subject_qid,
                            "relationship_property": name,
                            "object_qid": qid(val_b).decode("utf-8", "replace"),
                            "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                            "ingested_at": self.now,
                        })
                elif prop in EXTERNAL_ID_PROPS:
                    normalized_id = normalize_external_id(prop, val)
                    if normalized_id is None:
                        self.stats[f"invalid_external_id_{prop}"] += 1
                        continue
                    r["external_id_property"] = prop
                    r["external_id_name"] = name
                    r["external_id_value"] = normalized_id
                    self.entity_ids.append(r)
                    if kind == "LIVE_MUSIC_VENUE":
                        self.venue_ids.append(r)
                    elif kind == "PLACE":
                        self.place_ids.append(r)
                    elif kind == "ARTIST":
                        self.artist_ids.append(r)
        if kind == "LIVE_MUSIC_VENUE":
            self.stats["venue_rows"] += 1
        elif kind == "ARTIST":
            self.stats["artist_rows"] += 1
        self._pending += 1
        if self._pending >= SPILL_EVERY:
            self._spill()

    def _spill(self) -> None:
        """Flush accumulated rows to parquet, upload the shard to R2, clear."""
        for name in self.spill_paths:
            rows = getattr(self, name)
            if not rows:
                continue
            seq = len(self.spill_paths[name])
            local = self.spill_dir / f"{name}.{seq:04d}.parquet"
            pq.write_table(
                pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMAS[name]),
                local, compression="zstd")
            r2_key = f"{self.spill_prefix}{name}.{seq:04d}.parquet"
            digest = hashlib.sha256()
            with open(local, "rb") as f:
                for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
                f.seek(0)
                self._s3.upload_fileobj(
                    f,
                    LAKE_BUCKET,
                    r2_key,
                    ExtraArgs={"Metadata": {"sha256": digest.hexdigest()}},
                )
            head = self._s3.head_object(Bucket=LAKE_BUCKET, Key=r2_key)
            if int(head.get("ContentLength", -1)) != local.stat().st_size:
                raise RuntimeError(f"spill upload size verification failed for {r2_key}")
            if head.get("Metadata", {}).get("sha256") != digest.hexdigest():
                raise RuntimeError(f"spill upload hash verification failed for {r2_key}")
            local.unlink(missing_ok=True)
            self.spill_paths[name].append(r2_key)
            getattr(self, name).clear()
        self._pending = 0


def verified_source_identity(s3) -> tuple[int, str]:
    """Return the exact approved raw object identity or fail closed."""
    head = s3.head_object(Bucket=RAW_BUCKET, Key=RAW_KEY)
    source_bytes = int(head.get("ContentLength", -1))
    source_etag = str(head.get("ETag", "")).strip('"')
    if source_bytes != RAW_BYTES or source_etag != RAW_ETAG:
        raise RuntimeError(
            "Wikidata raw source identity changed; refusing to mix dump "
            f"versions (bytes={source_bytes}, etag={source_etag!r})"
        )
    return source_bytes, source_etag


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N subjects (for bounded testing)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--parallel-decompress",
        action="store_true",
        help="stream via rclone + pbzip2 instead of Python's single-core bz2",
    )
    args = ap.parse_args()
    if args.limit and not args.dry_run:
        ap.error("--limit is incomplete by definition and requires --dry-run")

    t0 = time.time()
    builder = SubgraphBuilder()
    source_bytes, source_etag = verified_source_identity(builder._s3)
    source_meta = {
        "source_bytes_expected": source_bytes,
        "source_etag": source_etag,
    }
    if args.parallel_decompress:
        print("[stream] rclone + pbzip2 parallel decompression", flush=True)
        source_batches = parallel_decompressed_batches()
    else:
        queue: Queue = Queue(maxsize=5000)
        producer = threading.Thread(
            target=stream_nt_lines, args=(RAW_BUCKET, RAW_KEY, queue),
            kwargs={"start_consumed": 0}, daemon=True)
        producer.start()
        source_batches = read_lines(queue)

    subject: bytes | None = None
    props: dict[str, list[bytes]] = {}
    p31: set = set()

    def flush():
        nonlocal subject, props, p31
        if subject is not None and props:
            builder.stats["subjects_seen"] += 1
            # keep only subjects with a music-identity anchor OR a class we care about
            if should_keep_subject(props, p31):
                builder.emit(subject, props, p31)
            else:
                builder.stats["skipped_no_anchor"] += 1
        subject, props, p31 = None, {}, set()

    n_lines = 0
    stop = False
    for item in source_batches:
        for raw_line in item:
            n_lines += 1
            if n_lines % 2_000_000 == 0:
                kept = sum(builder.stats[k] for k in (
                    "kept_ARTIST", "kept_LIVE_MUSIC_VENUE", "kept_PLACE",
                    "kept_FESTIVAL", "kept_OTHER"))
                print(f"  ... {n_lines:,} lines, kept={kept:,}",
                      flush=True)
            if args.limit and builder.stats["subjects_seen"] >= args.limit:
                stop = True
                break
            if not raw_line.strip() or raw_line.lstrip().startswith(b"#"):
                continue
            m = KEEP_NT_LINE_B.match(raw_line)
            if not m:
                continue
            builder.stats["triples_matched"] += 1
            s_uri_b, prop_b, obj_raw_b = m.group(1), m.group(2), m.group(3)
            prop = prop_b.decode()
            if s_uri_b != subject:
                flush()
                subject = s_uri_b
            props.setdefault(prop, []).append(object_value(obj_raw_b))
            if prop == "P31" and is_uri(obj_raw_b):
                p31.add(qid(object_value(obj_raw_b)))
        if stop:
            break
    flush()

    # rclone's S3 transport does not expose an If-Match option for `cat`.
    # Re-verify the object after EOF so a replacement during the stream can
    # never advance publication. The approved object is immutable by policy;
    # this is the fail-closed enforcement boundary.
    end_source_bytes, end_source_etag = verified_source_identity(builder._s3)
    if (end_source_bytes, end_source_etag) != (source_bytes, source_etag):
        raise RuntimeError("Wikidata raw source identity changed during the scan")

    runtime = time.time() - t0
    print(f"lines: {n_lines:,}")
    print(f"stats: {dict(builder.stats)}")
    print(f"artist_external_ids: {len(builder.artist_ids):,}")
    print(f"venue_external_ids: {len(builder.venue_ids):,}")
    print(f"coordinates: {len(builder.coordinates):,}")
    print(f"locations: {len(builder.locations):,}")
    print(f"websites: {len(builder.websites):,}")
    print(f"inceptions: {len(builder.inceptions):,}")
    print(f"runtime: {runtime:.1f}s")

    if args.dry_run:
        return

    from festival_bloomberg.lake.catalog import register_dataset_batch

    s3 = r2_client()
    dataset_ids = {
        "music_entities": "silver.wikidata_music_entities",
        "entity_types": "silver.wikidata_entity_types",
        "entity_ids": "silver.wikidata_entity_external_ids",
        "artist_ids": "silver.wikidata_artist_external_ids",
        "venue_ids": "silver.wikidata_venue_external_ids",
        "place_ids": "silver.wikidata_place_external_ids",
        "coordinates": "silver.wikidata_entity_coordinates",
        "locations": "silver.wikidata_entity_locations",
        "websites": "silver.wikidata_entity_websites",
        "inceptions": "silver.wikidata_entity_inception",
        "genres": "silver.wikidata_genres",
        "relationships": "silver.wikidata_relationships",
    }
    file_names = {
        "music_entities": "music_entities.parquet",
        "entity_types": "entity_types.parquet",
        "entity_ids": "entity_external_ids.parquet",
        "artist_ids": "artist_external_ids.parquet",
        "venue_ids": "venue_external_ids.parquet",
        "place_ids": "place_external_ids.parquet",
        "coordinates": "entity_coordinates.parquet",
        "locations": "entity_locations.parquet",
        "websites": "entity_websites.parquet",
        "inceptions": "entity_inception.parquet",
        "genres": "genres.parquet",
        "relationships": "relationships.parquet",
    }
    generation_prefix = f"silver/wikidata/generations/{builder.run_id}/"
    r2_keys = {
        name: f"{generation_prefix}{file_name}"
        for name, file_name in file_names.items()
    }

    def chunk_rows(rows, n=500_000):
        for i in range(0, len(rows), n):
            yield rows[i:i + n]

    def dedupe_rows(rows, key_fields):
        # Dedup within one batch: resume re-emission can repeat the final bz2
        # block's rows (bounded to one block per restart).
        seen: set = set()
        out = []
        for r in rows:
            k = tuple(r[f] for f in key_fields)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out

    def write_output(name: str):
        rows = getattr(builder, name)
        spills = builder.spill_paths[name]
        r2_key = r2_keys[name]
        key_fields = [f.name for f in OUTPUT_SCHEMAS[name]
                      if f.name not in ("source_system", "knowledge_time", "ingested_at")]
        buf = io.BytesIO()
        writer = None
        n_total = 0

        def emit_table(t):
            nonlocal writer, n_total
            if writer is None:
                writer = pq.ParquetWriter(buf, t.schema, compression="zstd")
            writer.write_table(t)
            n_total += t.num_rows
            if buf.tell() > MAX_FINAL_BUFFER_BYTES:
                raise RuntimeError(
                    f"final {name} parquet exceeds bounded buffer "
                    f"({MAX_FINAL_BUFFER_BYTES} bytes); refusing to publish"
                )

        for sp in spills:
            resp = s3.get_object(Bucket=LAKE_BUCKET, Key=sp)
            parquet_file = pq.ParquetFile(io.BytesIO(resp["Body"].read()))
            for batch in parquet_file.iter_batches(batch_size=100_000):
                emit_table(pa.Table.from_pylist(
                    dedupe_rows(batch.to_pylist(), key_fields),
                    schema=OUTPUT_SCHEMAS[name],
                ))
        for chunk in chunk_rows(rows):
            emit_table(pa.Table.from_pylist(
                dedupe_rows(chunk, key_fields), schema=OUTPUT_SCHEMAS[name]))
        if writer is None:
            emit_table(pa.Table.from_pylist([], schema=OUTPUT_SCHEMAS[name]))
        writer.close()
        if buf.tell() > MAX_FINAL_BUFFER_BYTES:
            raise RuntimeError(
                f"final {name} parquet exceeds bounded buffer after close "
                f"({MAX_FINAL_BUFFER_BYTES} bytes); refusing to publish"
            )
        data = buf.getvalue()
        # Validate the complete artifact before replacing the canonical key.
        # Spills remain intact until both this check and catalog registration
        # succeed, so a failed publish is recoverable without a rescan.
        verified = pq.read_table(io.BytesIO(data))
        if verified.num_rows != n_total or verified.schema != OUTPUT_SCHEMAS[name]:
            raise RuntimeError(f"final {name} parquet verification failed")
        artifact_sha256 = hashlib.sha256(data).hexdigest()
        s3.put_object(
            Bucket=LAKE_BUCKET,
            Key=r2_key,
            Body=data,
            Metadata={"sha256": artifact_sha256},
        )
        head = s3.head_object(Bucket=LAKE_BUCKET, Key=r2_key)
        if int(head.get("ContentLength", -1)) != len(data):
            raise RuntimeError(f"published {r2_key} size verification failed")
        if head.get("Metadata", {}).get("sha256") != artifact_sha256:
            raise RuntimeError(f"published {r2_key} checksum verification failed")
        print(f"  → r2://{LAKE_BUCKET}/{r2_key}  {n_total:,} rows, "
              f"{len(data) / 1048576:.1f} MB", flush=True)
        return {
            "name": name,
            "dataset_id": dataset_ids[name],
            "r2_key": r2_key,
            "row_count": n_total,
            "byte_count": len(data),
            "sha256": artifact_sha256,
            "schema": str(OUTPUT_SCHEMAS[name]),
        }

    artifacts = [write_output(name) for name in builder.spill_paths]
    row_counts = {
        artifact["name"]: artifact["row_count"] for artifact in artifacts
    }

    def put_verified_json(key: str, value: dict) -> str:
        data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        sha256 = hashlib.sha256(data).hexdigest()
        s3.put_object(
            Bucket=LAKE_BUCKET,
            Key=key,
            Body=data,
            ContentType="application/json",
            Metadata={"sha256": sha256},
        )
        head = s3.head_object(Bucket=LAKE_BUCKET, Key=key)
        if int(head.get("ContentLength", -1)) != len(data):
            raise RuntimeError(f"published {key} size verification failed")
        if head.get("Metadata", {}).get("sha256") != sha256:
            raise RuntimeError(f"published {key} checksum verification failed")
        return sha256

    artifacts_manifest_key = f"{generation_prefix}artifacts.json"
    artifacts_manifest = {
        "schema_version": "wikidata-silver-artifacts-v1",
        "run_id": builder.run_id,
        "dump_version": DUMP_VERSION,
        "source": {
            "bucket": RAW_BUCKET,
            "key": RAW_KEY,
            "bytes": source_bytes,
            "etag": source_etag,
        },
        "knowledge_time": builder.now,
        "status": "ARTIFACTS_VERIFIED",
        "artifacts": artifacts,
    }
    artifacts_manifest_sha256 = put_verified_json(
        artifacts_manifest_key, artifacts_manifest
    )

    # The catalog is an artifact index, not the publication authority. Register
    # only verified immutable artifacts and mark that explicitly. R2 CURRENT is
    # replaced last and is the sole authority for choosing a complete serving
    # generation, so a catalog/CURRENT failure gap cannot activate mixed data.
    registrations = []
    for artifact in artifacts:
        registrations.append({
            "dataset_id": artifact["dataset_id"],
            "dataset_version": DUMP_VERSION,
            "layer": "SILVER",
            "source": "wikidata",
            "source_version": DUMP_VERSION,
            "r2_bucket": LAKE_BUCKET,
            "r2_prefix": artifact["r2_key"],
            "fmt": "parquet",
            "schema_version": "silver-v1",
            "row_count": artifact["row_count"],
            "byte_count": artifact["byte_count"],
            "source_checksum": source_etag,
            "artifact_checksum": artifact["sha256"],
            "verification_status": "ARTIFACTS_VERIFIED_NOT_PUBLICATION_AUTHORITY",
            "license": "CC0-1.0",
            "rights_status": "DERIVED_FROM_PUBLIC_DOMAIN",
            "commercial_use_status": "ALLOWED",
            "serving_eligible": False,
            "access_classification": "PUBLIC",
            "upstream_dataset_ids": ["raw.wikidata_truthy_rdf"],
            "notes": (
                "Verified artifacts manifest: "
                f"r2://{LAKE_BUCKET}/{artifacts_manifest_key}; "
                "publication authority is silver/wikidata/CURRENT.json"
            ),
        })
    register_dataset_batch(registrations)

    manifest_key = f"{generation_prefix}manifest.json"
    manifest_sha256 = put_verified_json(manifest_key, {
        "schema_version": "wikidata-silver-generation-v1",
        "run_id": builder.run_id,
        "dump_version": DUMP_VERSION,
        "source": artifacts_manifest["source"],
        "knowledge_time": builder.now,
        "status": "PUBLISHED",
        "artifacts_manifest_key": artifacts_manifest_key,
        "artifacts_manifest_sha256": artifacts_manifest_sha256,
        "artifact_count": len(artifacts),
    })

    current_key = "silver/wikidata/CURRENT.json"
    put_verified_json(current_key, {
        "schema_version": "wikidata-silver-current-v1",
        "run_id": builder.run_id,
        "dump_version": DUMP_VERSION,
        "manifest_key": manifest_key,
        "manifest_sha256": manifest_sha256,
        "published_at": builder.now,
        "publication_authority": True,
    })

    for spills in builder.spill_paths.values():
        for spill_key in spills:
            s3.delete_object(Bucket=LAKE_BUCKET, Key=spill_key)
    shutil.rmtree(builder.spill_dir, ignore_errors=True)
    print(f"catalog registered: {len(artifacts)} wikidata silver datasets")
    print(f"current generation → r2://{LAKE_BUCKET}/{current_key}")

    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        peak_rss *= 1024
    spill_object_count = sum(len(keys) for keys in builder.spill_paths.values())

    # Persist run stats for the checkpoint report. Neither transport exposes a
    # directly observed compressed-byte counter, so do not substitute expected
    # object size for observed consumption.
    stats_path = Path("control/lake/wikidata_music_graph_stats.json")
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps({
        "run_id": builder.run_id,
        "lines_read": n_lines,
        "stats": dict(builder.stats),
        "row_counts": row_counts,
        "runtime_seconds": round(runtime, 1),
        "dump_version": DUMP_VERSION,
        **source_meta,
        "source_bytes_consumed": None,
        "source_complete": not bool(args.limit),
        "source_completion_basis": (
            "EOF_PLUS_POST_STREAM_IDENTITY_CHECK" if not args.limit
            else "BOUNDED_SUBJECT_LIMIT"
        ),
        "retries_observed": None,
        "peak_rss_bytes": peak_rss,
        "spill_object_count": spill_object_count,
        "artifact_count": len(artifacts),
        "generation_manifest_key": manifest_key,
        "generation_manifest_sha256": manifest_sha256,
        "current_pointer_key": current_key,
        "disk_free_bytes_at_publish": shutil.disk_usage("/tmp").free,
        "status": "BUILD_COMPLETE",
    }, indent=2) + "\n")
    print(f"stats → {stats_path}")


if __name__ == "__main__":
    main()
