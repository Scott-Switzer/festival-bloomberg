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

Outputs → festival-intelligence-lake/silver/:
    silver/wikidata/artist_external_ids.parquet   (QID ↔ MBID/Spotify/YouTube/...)
    silver/wikidata/venue_external_ids.parquet    (place-typed subjects)
    silver/wikidata/entity_coordinates.parquet    (QID, lat, lon)
    silver/wikidata/entity_locations.parquet      (QID → country/admin-unit QID)
    silver/wikidata/entity_websites.parquet       (QID, official website)
    silver/wikidata/entity_inception.parquet      (QID, founding date)

All rows carry source_system='wikidata', knowledge_time, ingested_at.
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
SOURCE_SYSTEM = "wikidata"

# Local spill dir for incremental parquet (bounds RAM on the full ~40 GB scan).
SPILL_DIR = "/tmp/wd_spill"
SPILL_EVERY = int(os.environ.get("WD_SPILL_EVERY", "1000000"))
MAX_ATTEMPTS = 300           # connection-break retries before giving up

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
    "Q1370242",  # arena? — 'venue'? actual 'venue' is Q173432
    "Q173432",   # venue? (building)
    "Q811430",   # concert hall
    "Q588140",   # nightclub
    "Q2748254",  # opera house? actual Q153562
    "Q153562",   # opera house
    "Q41176",    # building
    "Q847017",   # science museum? no — 'stadium' Q483110
    "Q483110",   # stadium
    "Q641226",   # arena
    "Q187456",   # pavilion? — no; theatre Q24354
    "Q24354",    # theatre
    "Q1320047",  # amphitheatre? actual Q878304
    "Q878304",   # amphitheatre
    "Q1215720",  # cultural center? — keep broad
}
FESTIVAL_CLASSES = {
    "Q132241",   # music festival
    "Q40056",    # festival? (Q40056 = festival)
    "Q40056",
}

KEEP_CLASSES = ARTIST_CLASSES | VENUE_CLASSES | FESTIVAL_CLASSES
# bytes variants so the hot consumer loop never decodes the raw NT lines
_ARTIST_CLASSES_B = {c.encode() for c in ARTIST_CLASSES}
_VENUE_CLASSES_B = {c.encode() for c in VENUE_CLASSES}
_FESTIVAL_CLASSES_B = {c.encode() for c in FESTIVAL_CLASSES}
KEEP_CLASSES_B = _ARTIST_CLASSES_B | _VENUE_CLASSES_B | _FESTIVAL_CLASSES_B
KEEP_PROPS_B = {p.encode() for p in KEEP_PROPS}
NT_LINE_B = re.compile(rb"^\s*<([^>]+)>\s+<([^>]+)>\s+(.+?)\s*\.\s*$")

_COMMON_FIELDS = [
    pa.field("source_system", pa.string()),
    pa.field("knowledge_time", pa.string()),
]
# Explicit schemas keep spill shards + final tables identical across batches.
OUTPUT_SCHEMAS = {
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
    if any(prop in IDENTITY_ANCHOR_PROPS for prop in props):
        return True
    return any(prop in GENERIC_ID_PROPS for prop in props) and bool(p31 & KEEP_CLASSES_B)


PROGRESS_FILE = Path("/tmp/r2_checkpoints/wd_progress.json")


def find_bz2_block_start(s3, bucket: str, key: str, consumed: int):
    """Locate the last valid bz2 block boundary at or before `consumed`.

    A fresh BZ2Decompressor cannot start mid-block, so a restarted process must
    re-align to a stream/block start. bz2 block boundaries are the stream
    header 'BZh'+digit or the block-end magic 0x177245385090 + 4-byte CRC.
    Candidates are validated by actually decompressing (newest first). If the
    object has no usable boundary near the checkpoint (e.g. a single giant
    block), this falls back to (0, b"") — a full re-read.
    """
    window_bytes = 64 * 1024 * 1024
    start = max(0, consumed - window_bytes)
    resp = s3.get_object(Bucket=bucket, Key=key,
                         Range=f"bytes={start}-{consumed - 1}")
    win = resp["Body"].read()
    cands: set[int] = set()
    i = 0
    while True:
        j = win.find(b"BZh", i)
        if j < 0:
            break
        if j + 3 < len(win) and 49 <= win[j + 3] <= 57:  # b'1'..b'9'
            cands.add(start + j)
        i = j + 1
    i = 0
    while True:
        j = win.find(b"\x17\x72\x45\x38\x50\x90", i)  # block-end magic
        if j < 0:
            break
        cands.add(start + j + 10)  # magic(6) + CRC(4) → next block start
        i = j + 1
    for off in sorted(cands, reverse=True):
        dec = bz2.BZ2Decompressor()
        out = bytearray()
        data = win[off - start:]
        try:
            while data:
                out += dec.decompress(data[: 1 << 20])
                data = data[1 << 20:]
        except EOFError:
            return off, bytes(out)   # valid boundary, tail truncated by window
        except OSError:
            continue                 # false positive — try the next older one
        return off, bytes(out)
    return 0, b""


def stream_nt_lines(bucket: str, key: str, queue: Queue, chunk: int = 1 << 20,
                    start_consumed: int = 0):
    """Producer: stream bzip2 from R2, yield decompressed lines.

    Connection breaks are retried with an open-ended Range GET resumed from the
    last *compressed* byte successfully fed to the decompressor, so the bz2
    stream stays byte-contiguous (same decompressor instance across retries).
    A progress checkpoint is written every ~30s; on process restart the
    checkpoint is used to block-align and resume instead of re-reading.
    """
    s3 = r2_client()
    dec = bz2.BZ2Decompressor()
    tail = b""
    consumed = 0
    attempts = 0
    last_progress = time.time()
    # Lines are queued in batches: per-line queue.put on a threading.Queue is
    # the pipeline bottleneck (~5µs each → ~1 MB/s); batching makes it ~100x
    # cheaper and the pipeline becomes regex/decompress-bound instead.
    batch: list[bytes] = []
    if start_consumed:
        off, initial_out = find_bz2_block_start(s3, bucket, key, start_consumed)
        consumed = off
        if initial_out:
            lines = initial_out.split(b"\n")
            tail = lines.pop()
            batch.extend(lines)
        print(f"[resume] block-aligned at byte {off:,} "
              f"(checkpoint was {start_consumed:,})", flush=True)
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
                if time.time() - last_progress > 30:
                    try:
                        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
                        PROGRESS_FILE.write_text(json.dumps(
                            {"compressed_consumed": consumed,
                             "attempts": attempts,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
                    except Exception:  # noqa: BLE001
                        pass
                    last_progress = time.time()
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
    def __init__(self) -> None:
        self.stats: Counter = Counter()
        self.artist_ids: list[dict] = []
        self.venue_ids: list[dict] = []
        self.coordinates: list[dict] = []
        self.locations: list[dict] = []
        self.websites: list[dict] = []
        self.inceptions: list[dict] = []
        self.now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Incremental spill state — flushes accumulated rows to parquet so the
        # full ~40 GB scan stays far below the 8 GB RAM ceiling. Spill shards
        # are uploaded to R2 as they are produced (the run's row volume ~5 GB
        # exceeds local free disk), so local disk stays near-zero.
        self._s3 = r2_client()
        self.spill_dir = Path(SPILL_DIR)
        shutil.rmtree(self.spill_dir, ignore_errors=True)  # stale local shards
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        # stale R2 shards from a dead run (spills are ephemeral by design).
        # Paginate: a list_objects_v2 page holds at most 1000 keys, and a run
        # that died before finalizing could have left more spills than that.
        # Spill keys are deterministic, so wiping all of them at init is what
        # guarantees restart-idempotency (re-emission overwrites, never appends).
        try:
            while True:
                page = self._s3.list_objects_v2(
                    Bucket=LAKE_BUCKET,
                    Prefix="silver/wikidata/_spill/",
                    MaxKeys=1000)
                keys = [o["Key"] for o in page.get("Contents", [])]
                if not keys:
                    break
                for k in keys:
                    self._s3.delete_object(Bucket=LAKE_BUCKET, Key=k)
                if not page.get("IsTruncated"):
                    break
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        self._pending = 0
        self.spill_paths: dict[str, list[str]] = {
            n: [] for n in ("artist_ids", "venue_ids", "coordinates",
                            "locations", "websites", "inceptions")}

    def classify(self, p31_values: set) -> str:
        if p31_values & _ARTIST_CLASSES_B:
            return "ARTIST"
        if p31_values & _VENUE_CLASSES_B:
            return "VENUE"
        if p31_values & _FESTIVAL_CLASSES_B:
            return "FESTIVAL"
        return "OTHER"

    def emit(self, subject_qid_b: bytes, props: dict[str, list[bytes]],
             p31: set) -> None:
        kind = self.classify(p31)
        self.stats[f"kept_{kind}"] += 1
        subject_qid = subject_qid_b.decode("utf-8", "replace")
        row = {"qid": subject_qid, "classification": kind,
               "source_system": SOURCE_SYSTEM, "knowledge_time": self.now}
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
                    })
                elif prop in ("P17", "P27", "P131", "P495"):
                    if is_uri(val):
                        self.locations.append({
                            "qid": subject_qid, "location_property": name,
                            "location_qid": qid(val),
                            "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                        })
                elif prop == "P856":
                    self.websites.append({
                        "qid": subject_qid, "url": val,
                        "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                    })
                elif prop == "P571":
                    self.inceptions.append({
                        "qid": subject_qid, "inception": val,
                        "source_system": SOURCE_SYSTEM, "knowledge_time": self.now,
                    })
                else:
                    r["external_id_property"] = prop
                    r["external_id_name"] = name
                    r["external_id_value"] = val
                    if kind == "VENUE":
                        self.venue_ids.append(r)
                    else:
                        self.artist_ids.append(r)
        if kind == "VENUE":
            self.stats["venue_rows"] += 1
        else:
            self.stats["artist_rows"] += 1
        self._pending += 1
        if self._pending >= SPILL_EVERY:
            self._spill()

    def _spill(self) -> None:
        """Flush accumulated rows to parquet, upload the shard to R2, clear."""
        for name in ("artist_ids", "venue_ids", "coordinates",
                     "locations", "websites", "inceptions"):
            rows = getattr(self, name)
            if not rows:
                continue
            seq = len(self.spill_paths[name])
            local = self.spill_dir / f"{name}.{seq:04d}.parquet"
            pq.write_table(
                pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMAS[name]),
                local, compression="zstd")
            r2_key = f"silver/wikidata/_spill/{name}.{seq:04d}.parquet"
            with open(local, "rb") as f:
                self._s3.put_object(Bucket=LAKE_BUCKET, Key=r2_key, Body=f.read())
            local.unlink(missing_ok=True)
            self.spill_paths[name].append(r2_key)
            getattr(self, name).clear()
        self._pending = 0


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

    # Restart-resume: if a previous run checkpointed compressed progress, pick
    # up from there (block-aligned) instead of re-reading ~21 GB from byte 0.
    start_consumed = 0
    try:
        ck = json.loads(PROGRESS_FILE.read_text())
        start_consumed = int(ck.get("compressed_consumed", 0))
    except Exception:  # noqa: BLE001 — no checkpoint yet
        pass
    if start_consumed and not args.parallel_decompress:
        print(f"[resume] checkpoint found at compressed byte {start_consumed:,}",
              flush=True)

    t0 = time.time()
    builder = SubgraphBuilder()
    if args.parallel_decompress:
        print("[stream] rclone + pbzip2 parallel decompression", flush=True)
        source_batches = parallel_decompressed_batches()
    else:
        queue: Queue = Queue(maxsize=5000)
        producer = threading.Thread(
            target=stream_nt_lines, args=(RAW_BUCKET, RAW_KEY, queue),
            kwargs={"start_consumed": start_consumed}, daemon=True)
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
                print(f"  ... {n_lines:,} lines, kept={builder.stats['kept_ARTIST'] + builder.stats['kept_VENUE'] + builder.stats['kept_FESTIVAL'] + builder.stats['kept_OTHER']:,}",
                      flush=True)
            if args.limit and builder.stats["subjects_seen"] >= args.limit:
                stop = True
                break
            if not raw_line.strip() or raw_line.lstrip().startswith(b"#"):
                continue
            m = KEEP_NT_LINE_B.match(raw_line)
            if not m:
                continue
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

    from festival_bloomberg.lake.catalog import register_dataset

    s3 = r2_client()
    dataset_ids = {
        "artist_ids": "silver.wikidata_artist_external_ids",
        "venue_ids": "silver.wikidata_venue_external_ids",
        "coordinates": "silver.wikidata_entity_coordinates",
        "locations": "silver.wikidata_entity_locations",
        "websites": "silver.wikidata_entity_websites",
        "inceptions": "silver.wikidata_entity_inception",
    }
    r2_keys = {
        "artist_ids": "silver/wikidata/artist_external_ids.parquet",
        "venue_ids": "silver/wikidata/venue_external_ids.parquet",
        "coordinates": "silver/wikidata/entity_coordinates.parquet",
        "locations": "silver/wikidata/entity_locations.parquet",
        "websites": "silver/wikidata/entity_websites.parquet",
        "inceptions": "silver/wikidata/entity_inception.parquet",
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
        if not rows and not spills:
            print(f"  SKIP {r2_key} (0 rows)")
            return 0
        key_fields = [f.name for f in OUTPUT_SCHEMAS[name]
                      if f.name not in ("source_system", "knowledge_time")]
        buf = io.BytesIO()
        writer = None
        n_total = 0

        def emit_table(t):
            nonlocal writer, n_total
            if writer is None:
                writer = pq.ParquetWriter(buf, t.schema, compression="zstd")
            writer.write_table(t)
            n_total += t.num_rows

        for sp in spills:
            resp = s3.get_object(Bucket=LAKE_BUCKET, Key=sp)
            t = pq.read_table(io.BytesIO(resp["Body"].read()))
            emit_table(pa.Table.from_pylist(
                dedupe_rows(t.to_pylist(), key_fields), schema=OUTPUT_SCHEMAS[name]))
            s3.delete_object(Bucket=LAKE_BUCKET, Key=sp)   # free R2 as we consume
        for chunk in chunk_rows(rows):
            emit_table(pa.Table.from_pylist(
                dedupe_rows(chunk, key_fields), schema=OUTPUT_SCHEMAS[name]))
        if writer is None:
            return 0
        writer.close()
        data = buf.getvalue()
        s3.put_object(Bucket=LAKE_BUCKET, Key=r2_key, Body=data)
        print(f"  → r2://{LAKE_BUCKET}/{r2_key}  {n_total:,} rows, "
              f"{len(data) / 1048576:.1f} MB", flush=True)
        register_dataset(
            dataset_id=dataset_ids[name],
            dataset_version=DUMP_VERSION,
            layer="SILVER",
            source="wikidata",
            source_version=DUMP_VERSION,
            r2_bucket=LAKE_BUCKET,
            r2_prefix=r2_key,
            fmt="parquet",
            schema_version="silver-v1",
            row_count=n_total,
            byte_count=len(data),
            verification_status="BUILD_COMPLETE",
            license="CC0-1.0",
            rights_status="DERIVED_FROM_PUBLIC_DOMAIN",
            commercial_use_status="ALLOWED",
            upstream_dataset_ids=["raw.wikidata_truthy_rdf"],
        )
        return n_total

    row_counts: dict[str, int] = {}
    written = 0
    for name in ("artist_ids", "venue_ids", "coordinates",
                 "locations", "websites", "inceptions"):
        n = write_output(name)
        if n:
            written += 1
        row_counts[name] = n
    shutil.rmtree(SPILL_DIR, ignore_errors=True)
    print(f"catalog registered: {written} wikidata silver datasets")

    # persist run stats for the checkpoint report
    stats_path = Path("control/lake/wikidata_music_graph_stats.json")
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps({
        "lines_read": n_lines,
        "stats": dict(builder.stats),
        "row_counts": {k: v for k, v in [
            ("artist_external_ids", row_counts["artist_ids"]),
            ("venue_external_ids", row_counts["venue_ids"]),
            ("entity_coordinates", row_counts["coordinates"]),
            ("entity_locations", row_counts["locations"]),
            ("entity_websites", row_counts["websites"]),
            ("entity_inception", row_counts["inceptions"]),
        ]},
        "runtime_seconds": round(runtime, 1),
        "dump_version": DUMP_VERSION,
    }, indent=2) + "\n")
    print(f"stats → {stats_path}")


if __name__ == "__main__":
    main()
