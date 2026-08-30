"""P3-P13 — Production MAP/REDUCE full-scan for the 205 GB ListenBrainz corpus.

Why not the pilot architecture: the pilot staged ALL matched rows to one local
Parquet (1% -> 14.3M rows). A full run would approach ~1.4B matched rows / ~289M
listener_artist rows — impossible on this 8 GB RAM / ~14 GB free-disk Mac.

This pipeline is bounded, resumable, and applies the audience-affinity policy
GLOBALLY (never per source shard):

    MAP    per batch of 16 shards: stream -> filter to 25K -> aggregate ->
           write tiny partial parquets to R2 -> DELETE local -> checkpoint.

    REDUCE artist_day:   aggregate ALL artist_day partials -> silver, by year/mo.
    REDUCE affinity:     hash-partition listener_artist by listener_hash, then
           per partition aggregate per-listener globally, rank, apply top-K
           (from the P1/P2 sensitivity study: TOP_25), generate pairs, emit
           pair partials -> reduce to gold affinity edges.

Phases are independent commands so each is bounded and resumable:

    python scripts/lb_full_scan.py map --max-shards N [--batch 16]
    python scripts/lb_full_scan.py reduce-artist-day
    python scripts/lb_full_scan.py reduce-affinity --partitions H
    python scripts/lb_full_scan.py reduce-pairs

Checkpoint manifest written to
    control/lake/listenbrainz_full_scan/current.json
Skips completed batches on restart.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from festival_bloomberg.lake.r2 import r2_client  # noqa: E402

RAW_BUCKET = "festival-intelligence-raw"
LAKE_BUCKET = "festival-intelligence-lake"
RAW_KEY = ("bulk/listenbrainz/dump=2593-20260712-000004/"
           "listenbrainz-spark-dump-2593-20260712-000004-full.tar")
DUMP_VERSION = "2593-20260712-000004"

# Local plumbing (survives per-session; contents are transient, uploaded + deleted)
INDEX_CACHE = Path("control/lake/lb_tar_index.json")
ESTATE_JSON = Path("data/control/artist_security_25000/v1/"
                   "estate_20260828T013314Z_f87e5d1d073e.json")
CHECKPOINT = Path("control/lake/listenbrainz_full_scan/current.json")
SPILL = Path("/tmp/lb_full_spill")
LOCAL = Path("/tmp/lb_full_local")

# Policy (from P1/P2 sensitivity study — see control/lake/listenbrainz_sensitivity_summary.json)
TOP_K = 25                      # per-listener global artist cap
MIN_SHARED_LISTENERS = 3        # minimum shared listeners to persist an edge

# Sizes
BATCH_SHARDS = 16
TOTAL_SOURCE_BYTES = 205_073_162_240
SOURCE_DATASET = "raw.listenbrainz_full_dump"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            return json.loads(CHECKPOINT.read_text())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"checkpoint is not valid JSON: {CHECKPOINT}") from exc
    return {
        "pipeline": "listenbrainz_full_scan",
        "pipeline_version": 1,
        "source_dataset": SOURCE_DATASET,
        "dump_version": DUMP_VERSION,
        "batch_size_shards": BATCH_SHARDS,
        "listener_hash_partitions": None,
        "source_shard_count": 0,
        "completed_batches": [],   # list of [first_idx, last_idx]
        "completed_shards": [],
        "completed_artist_day": False,
        "completed_affinity_partitions": [],
        "completed_pairs": False,
        "listens_scanned": 0,
        "matched_listens": 0,
        "unresolved": 0,
        "no_credit": 0,
        "artists_seen": 0,
        "bytes_read": 0,
        "runtime_seconds": 0.0,
        "started_at": None,
        "updated_at": None,
    }


def validate_checkpoint(ckpt: dict, *, partitions: int | None = None) -> None:
    """Fail closed when a checkpoint belongs to a different scan geometry."""
    if ckpt.get("source_dataset") != SOURCE_DATASET:
        raise RuntimeError(
            "checkpoint source_dataset is incompatible; archive it and start a fresh run"
        )
    if ckpt.get("dump_version") not in (None, DUMP_VERSION):
        raise RuntimeError("checkpoint dump_version does not match the configured raw dump")
    completed = bool(ckpt.get("completed_batches") or ckpt.get("completed_affinity_partitions"))
    saved = ckpt.get("listener_hash_partitions")
    if completed and saved is None:
        raise RuntimeError(
            "legacy checkpoint has no listener_hash_partitions; do not resume it implicitly"
        )
    if partitions is not None and saved is not None and int(saved) != partitions:
        raise RuntimeError(
            f"checkpoint uses {saved} listener partitions, command requests {partitions}; "
            "use the same value or start a fresh run"
        )
    if ckpt.get("batch_size_shards") not in (None, BATCH_SHARDS):
        raise RuntimeError("checkpoint batch size does not match the fixed map geometry")


def ensure_map_complete(ckpt: dict) -> None:
    """Reducers must never publish a partial scan as a complete dataset."""
    expected = int(ckpt.get("source_shard_count") or 0)
    completed = {int(i) for i in (ckpt.get("completed_shards") or [])}
    if expected <= 0 or len(completed) < expected:
        raise RuntimeError(
            f"map is incomplete ({len(completed)}/{expected} shards); reducers are blocked"
        )


def save_checkpoint(s3, ckpt: dict) -> None:
    ckpt["updated_at"] = now_iso()
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ckpt, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".checkpoint.", suffix=".json", dir=CHECKPOINT.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, CHECKPOINT)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    try:
        s3.put_object(Bucket=LAKE_BUCKET,
                      Key="control/listenbrainz_full_scan/current.json",
                      Body=payload.encode())
    except Exception as exc:  # noqa: BLE001
        print(f"  (checkpoint R2 copy failed: {exc.__class__.__name__})")


def build_tar_index(s3) -> list[dict]:
    """Reuse cached index or walk tar headers via 512-byte range GETs (resumable)."""
    if INDEX_CACHE.exists():
        return json.loads(INDEX_CACHE.read_text())
    raise RuntimeError("tar index missing; run scripts/lb_pilot.py or lb_format_inventory first")


def data_shards(members: list[dict]) -> list[dict]:
    return [m for m in members
            if (m["name"].split("/")[-1].endswith(".parquet")
                and m["name"].split("/")[-1][:-8].isdigit())]


def load_universe() -> dict[str, dict]:
    """mbid -> {key, tier} for the 25K universe (from estate JSON)."""
    data = json.loads(ESTATE_JSON.read_text())
    out = {}
    for a in data.get("artists", []):
        mbid = a.get("mbid")
        if mbid:
            out[mbid] = {"key": a.get("key"), "tier": a.get("tier")}
    return out


def fetch_shard(s3, m: dict) -> bytes:
    end = m["offset"] + m["size"] - 1
    attempts = 0
    while True:
        try:
            resp = s3.get_object(Bucket=RAW_BUCKET, Key=RAW_KEY,
                                 Range=f"bytes={m['offset']}-{end}")
            return resp["Body"].read()
        except Exception as e:  # noqa: BLE001
            attempts += 1
            if attempts > 8:
                raise
            print(f"  fetch retry {attempts} ({e.__class__.__name__})", flush=True)
            time.sleep(min(60, 5 * attempts))


def upload(s3, local: Path, key: str) -> None:
    blob = local.read_bytes()
    s3.put_object(Bucket=LAKE_BUCKET, Key=key, Body=blob)
    # verify exists
    sz = s3.head_object(Bucket=LAKE_BUCKET, Key=key)["ContentLength"]
    if sz != len(blob):
        raise RuntimeError(f"upload size mismatch for {key}")
    local.unlink()


def _r2_list(s3, prefix: str):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=LAKE_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def cmd_map(args) -> None:
    s3 = r2_client()
    ckpt = load_checkpoint()
    if args.partitions <= 0:
        raise ValueError("--partitions must be positive")
    if args.max_shards <= 0:
        raise ValueError("--max-shards must be positive")
    validate_checkpoint(ckpt, partitions=args.partitions)
    ckpt["listener_hash_partitions"] = args.partitions
    ckpt["batch_size_shards"] = BATCH_SHARDS
    ckpt["dump_version"] = DUMP_VERSION
    members = build_tar_index(s3)
    shards = data_shards(members)
    total_shards = len(shards)
    if ckpt.get("source_shard_count") not in (0, total_shards):
        raise RuntimeError("checkpoint source shard count does not match the tar index")
    ckpt["source_shard_count"] = total_shards
    ckpt["started_at"] = ckpt.get("started_at") or now_iso()

    max_shards = min(args.max_shards, total_shards)

    SPILL.mkdir(parents=True, exist_ok=True)
    LOCAL.mkdir(parents=True, exist_ok=True)

    # DuckDB handles the aggregation for a batch; spill keeps RAM bounded.
    con = duckdb.connect()
    con.execute(f"PRAGMA memory_limit='2GB'")
    con.execute(f"SET temp_directory='{SPILL}'")
    con.execute("SET threads=2")  # leave cores for Wikidata / other work

    universe = load_universe()
    print(f"universe MBIDs: {len(universe):,}   total shards: {total_shards:,}")

    # Completed batches are the ONLY resume authority. A batch is atomic: it is
    # marked complete together with its counters only after ALL its partials are
    # uploaded. A crash mid-batch leaves the batch uncommitted -> redone on resume.
    done_batches = set()
    for rng in ckpt.get("completed_batches", []):
        for i in range(rng[0], rng[1] + 1):
            done_batches.add(i)
    artists_seen: set[str] = set(ckpt.get("artists_seen", []) or [])

    # Commit counters + shard set ONLY at batch boundaries (not per shard), so a
    # mid-batch crash cannot leave the checkpoint inconsistent with R2 partials.
    t_start = time.time()
    idx = 0
    while idx < max_shards:
        batch_last = min(idx + BATCH_SHARDS, max_shards)
        if all(i in done_batches for i in range(idx, batch_last)):
            print(f"[resume] batch [{idx}..{batch_last - 1}] already complete — skipped")
            idx = batch_last
            continue
        print(f"\n=== batch [{idx}..{batch_last - 1}] ===", flush=True)
        bt = time.time()
        # staged counters for THIS batch (apply to ckpt only on commit)
        b_bytes = 0
        b_matched = 0
        b_unres = 0
        b_nocr = 0
        # build matched-row DuckDB table for this batch
        con.execute("DROP TABLE IF EXISTS m")
        con.execute("CREATE TABLE m (artist_key VARCHAR, listener_key BIGINT, "
                    "listened_at TIMESTAMP, recording_mbid VARCHAR)")
        for i in range(idx, batch_last):
            m = shards[i]
            raw = fetch_shard(s3, m)
            b_bytes += m["size"]
            pf = pq.ParquetFile(io.BytesIO(raw))
            table = pf.read()
            del raw
            for b in table.to_batches(max_chunksize=150_000):
                ambs = b.column("artist_credit_mbids").to_pylist()
                uids = b.column("user_id").to_pylist()
                la = b.column("listened_at").to_pylist()
                rm = b.column("recording_mbid").to_pylist()
                k, u, l, r = [], [], [], []
                for j in range(b.num_rows):
                    credit = ambs[j]
                    if not credit:
                        b_nocr += 1
                        continue
                    hit = None
                    for amb in credit:
                        if amb in universe:
                            hit = universe[amb]
                            break
                    if not hit:
                        b_unres += 1
                        continue
                    b_matched += 1
                    k.append(hit["key"]); u.append(int(uids[j])); l.append(la[j]); r.append(rm[j])
                if k:
                    t = pa.table({"artist_key": k, "listener_key": u,
                                  "listened_at": l, "recording_mbid": r})
                    artists_seen.update(k)
                    con.register("t_ins", t)
                    con.execute("INSERT INTO m SELECT * FROM t_ins")
                    con.unregister("t_ins")
            del table
            print(f"    shard {i}: {m['name'].split('/')[-1]} {m['size']//1048576} MiB "
                  f"this-batch retained={b_matched:,}", flush=True)

        # ---- partial: artist_day ----
        ad_local = LOCAL / f"artist_day_batch_{idx}.parquet"
        con.execute(f"""
            COPY (
                SELECT artist_key, CAST(listened_at AS DATE) AS obs_day,
                       COUNT(*) AS listen_count,
                       COUNT(DISTINCT listener_key) AS unique_listeners,
                       COUNT(DISTINCT recording_mbid) AS unique_recordings
                FROM m GROUP BY 1, 2
            ) TO '{ad_local}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        upload(s3, ad_local, f"silver/listenbrainz/_partial/artist_day/batch_{idx}.parquet")

        # ---- partial: listener_artist, hash-partitioned by listener ----
        parts_dir = LOCAL / "la_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        for f in parts_dir.glob("*.parquet"):
            f.unlink()
        con.execute(f"""
            COPY (
                SELECT abs(hash(CAST(listener_key AS VARCHAR))) % {args.partitions} AS part,
                       CAST(listener_key AS VARCHAR) AS listener_key, artist_key,
                       COUNT(*) AS listen_count
                FROM m GROUP BY 1, 2, 3
            ) TO '{parts_dir}/part.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        df_parts = con.execute(
            f"SELECT DISTINCT part FROM read_parquet('{parts_dir}/part.parquet') "
            "ORDER BY part").fetchall()
        part_keys = [r[0] for r in df_parts]
        for p in part_keys:
            p_local = parts_dir / f"p{p}.parquet"
            con.execute(f"""
                COPY (
                  SELECT CAST(listener_key AS VARCHAR) AS listener_key, artist_key,
                         listen_count FROM read_parquet('{parts_dir}/part.parquet')
                  WHERE part = {p}
                ) TO '{p_local}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            upload(s3, p_local,
                   f"silver/listenbrainz/_partial/listener_artist/part={p}/batch_{idx}.parquet")
        (parts_dir / "part.parquet").unlink(missing_ok=True)
        con.execute("DROP TABLE m")

        # ---- commit batch (counters + shards + list all at once) ----
        ckpt["bytes_read"] += b_bytes
        ckpt["matched_listens"] += b_matched
        ckpt["unresolved"] += b_unres
        ckpt["no_credit"] += b_nocr
        ckpt["listens_scanned"] += (b_matched + b_unres + b_nocr)
        ckpt["completed_shards"] = sorted(
            set(ckpt.get("completed_shards", [])) | set(range(idx, batch_last)))
        ckpt["completed_batches"].append([idx, batch_last - 1])
        ckpt["artists_seen"] = sorted(artists_seen)
        save_checkpoint(s3, ckpt)
        for i in range(idx, batch_last):
            done_batches.add(i)
        bt_s = time.time() - bt
        mbps = b_bytes / 1e6 / max(0.001, bt_s)
        print(f"  batch done in {bt_s:.0f}s @ {mbps:.1f} MB/s reads; "
              f"matched total {ckpt['matched_listens']:,}; "
              f"{len(ckpt['completed_batches'])} batches", flush=True)
        idx = batch_last

    con.close()
    ckpt["runtime_seconds"] += time.time() - t_start
    save_checkpoint(s3, ckpt)
    n_batches = len(ckpt["completed_batches"])
    proj_h = (TOTAL_SOURCE_BYTES / max(1, ckpt["bytes_read"])) * ckpt["runtime_seconds"] / 3600
    print(f"\n=== MAP done: {n_batches} batches, {len(ckpt['completed_shards'])} shards ===")
    print(f"bytes_read {ckpt['bytes_read']/1e9:.1f} GB  matched {ckpt['matched_listens']:,} "
          f"unresolved {ckpt['unresolved']:,} no_credit {ckpt['no_credit']:,}")
    print(f"projected full-scan time at this rate: {proj_h:.2f} h")


def cmd_reduce_artist_day(args) -> None:
    s3 = r2_client()
    ckpt = load_checkpoint()
    validate_checkpoint(ckpt)
    ensure_map_complete(ckpt)
    if ckpt.get("completed_artist_day"):
        print("artist_day already reduced.")
        return
    LOCAL.mkdir(parents=True, exist_ok=True)
    keys = _r2_list(s3, "silver/listenbrainz/_partial/artist_day/")
    keys = [k for k in keys if k.endswith(".parquet")]
    print(f"artist_day partials: {len(keys)}")
    if not keys:
        raise RuntimeError("map is complete but no artist_day partials were found")
    local_files = []
    for i, k in enumerate(keys):
        d = LOCAL / f"ad_{i}.parquet"
        d.write_bytes(s3.get_object(Bucket=LAKE_BUCKET, Key=k)["Body"].read())
        local_files.append(str(d))
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{SPILL}'")
    con.execute("CREATE TABLE ad AS "
                "SELECT * FROM read_parquet([{}])".format(", ".join(f"'{p}'" for p in local_files)))
    # partition by year/month
    out_root = Path("/tmp/lb_full_local_ad")
    out_root.mkdir(exist_ok=True)
    periods = con.execute(
        "SELECT strftime(obs_day, '%Y/%m') AS ym FROM ad GROUP BY 1 ORDER BY 1").fetchall()
    for (ym,) in periods:
        yyyy, mm = ym.split("/")
        o = out_root / f"year={yyyy}/month={mm}/part0.parquet"
        o.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"""
            COPY (
              SELECT artist_key, obs_day, listen_count, unique_listeners, unique_recordings
              FROM ad WHERE strftime(obs_day, '%Y/%m') = '{ym}'
            ) TO '{o}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        upload(s3, o, f"silver/listenbrainz/artist_day/year={yyyy}/month={mm}/part.parquet")
    total = con.execute("SELECT COUNT(*) FROM ad").fetchone()[0]
    con.close()
    for f in local_files:
        Path(f).unlink(missing_ok=True)
    ckpt["completed_artist_day"] = True
    ckpt["artist_day_cells"] = total
    save_checkpoint(s3, ckpt)
    print(f"artist_day reduced: {total:,} cells")


def cmd_reduce_affinity(args) -> None:
    """Global per-listener aggregrate within each listener partition + top-K + pairs."""
    s3 = r2_client()
    ckpt = load_checkpoint()
    if args.partitions <= 0:
        raise ValueError("--partitions must be positive")
    validate_checkpoint(ckpt, partitions=args.partitions)
    ensure_map_complete(ckpt)
    LOCAL.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{SPILL}'")
    # list all partitions actually written
    parts = set()
    for k in _r2_list(s3, "silver/listenbrainz/_partial/listener_artist/"):
        if k.endswith(".parquet"):
            parts.add(k.split("/part=")[1].split("/")[0])
    parts = sorted(parts, key=int)
    print(f"listener partitions seen: {len(parts)}")
    if not parts:
        raise RuntimeError("map is complete but no listener_artist partials were found")
    done_parts = set(ckpt.get("completed_affinity_partitions", []))
    for part in parts:
        if part in done_parts:
            continue
        keys = _r2_list(s3, f"silver/listenbrainz/_partial/listener_artist/part={part}/")
        keys = [k for k in keys if k.endswith(".parquet")]
        print(f"  partition {part}: {len(keys)} batches", flush=True)
        local_files = []
        for i, k in enumerate(keys):
            d = LOCAL / f"la_{part}_{i}.parquet"
            d.write_bytes(s3.get_object(Bucket=LAKE_BUCKET, Key=k)["Body"].read())
            local_files.append(str(d))
        con.execute("DROP TABLE IF EXISTS la")
        con.execute("CREATE TABLE la AS "
                    "SELECT * FROM read_parquet([{}])".format(", ".join(f"'{p}'" for p in local_files)))
        n_rows = con.execute("SELECT COUNT(*) FROM la").fetchone()[0]
        # global per-listener rank + top-K, then pairs among that listener's top-K
        pair_local = LOCAL / f"pairs_{part}.parquet"
        nodelocal = LOCAL / f"nodes_{part}.parquet"
        con.execute(f"""
            COPY (
              WITH ranked AS (
                SELECT listener_key, artist_key,
                       ROW_NUMBER() OVER (PARTITION BY listener_key
                                          ORDER BY listen_count DESC, artist_key) AS rn
                FROM la
              ),
              bounded AS (SELECT listener_key, artist_key FROM ranked WHERE rn <= {TOP_K}),
              pairs AS (
                SELECT a.artist_key AS a, b.artist_key AS b, COUNT(*) AS sh
                FROM bounded a JOIN bounded b
                     ON a.listener_key = b.listener_key AND a.artist_key < b.artist_key
                GROUP BY 1, 2
              )
              SELECT a, b, sh FROM pairs WHERE sh >= {MIN_SHARED_LISTENERS}
            ) TO '{pair_local}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        con.execute(f"""
            COPY (
              SELECT artist_key AS a, COUNT(DISTINCT listener_key) AS listeners
              FROM la GROUP BY 1
            ) TO '{nodelocal}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        upload(s3, pair_local, f"silver/listenbrainz/_partial/affinity_pairs/part={part}.parquet")
        upload(s3, nodelocal, f"silver/listenbrainz/_partial/artist_listeners/part={part}.parquet")
        # free
        con.execute("DROP TABLE la")
        for f in local_files:
            Path(f).unlink(missing_ok=True)
        done_parts.add(part)
        ckpt["completed_affinity_partitions"] = sorted(done_parts)
        save_checkpoint(s3, ckpt)
        print(f"  partition {part}: {n_rows:,} LA rows done", flush=True)
    con.close()


def cmd_reduce_pairs(args) -> None:
    """Union affinity pair partials, join global artist listener totals, compute metrics."""
    s3 = r2_client()
    ckpt = load_checkpoint()
    validate_checkpoint(ckpt)
    ensure_map_complete(ckpt)
    LOCAL.mkdir(parents=True, exist_ok=True)
    SPILL.mkdir(parents=True, exist_ok=True)
    # gather pair partials + node partials
    pair_keys = [k for k in _r2_list(s3, "silver/listenbrainz/_partial/affinity_pairs/")
                 if k.endswith(".parquet")]
    node_keys = [k for k in _r2_list(s3, "silver/listenbrainz/_partial/artist_listeners/")
                 if k.endswith(".parquet")]
    print(f"pair partials: {len(pair_keys)}   node partials: {len(node_keys)}")
    if not pair_keys or not node_keys:
        raise RuntimeError("affinity reduction inputs are incomplete")
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{SPILL}'")

    pair_locals, node_locals = [], []
    for i, k in enumerate(pair_keys):
        d = LOCAL / f"pr_{i}.parquet"
        d.write_bytes(s3.get_object(Bucket=LAKE_BUCKET, Key=k)["Body"].read())
        pair_locals.append(str(d))
    for i, k in enumerate(node_keys):
        d = LOCAL / f"nd_{i}.parquet"
        d.write_bytes(s3.get_object(Bucket=LAKE_BUCKET, Key=k)["Body"].read())
        node_locals.append(str(d))

    con.execute("CREATE TABLE pairs AS SELECT a AS artist_key_a, b AS artist_key_b, "
                "SUM(sh) AS shared_listeners "
                f"FROM read_parquet([{','.join(repr(p) for p in pair_locals)}]) "
                "GROUP BY 1, 2")
    con.execute("CREATE TABLE nodes AS SELECT a AS artist_key, "
                f"SUM(listeners) AS listeners "
                f"FROM read_parquet([{','.join(repr(p) for p in node_locals)}]) "
                "GROUP BY 1")

    out = Path("/tmp/lb_gold_affinity.parquet")
    con.execute(f"""
        COPY (
            SELECT p.artist_key_a, p.artist_key_b, p.shared_listeners,
                   n1.listeners AS listeners_a, n2.listeners AS listeners_b,
                   ROUND(p.shared_listeners::DOUBLE /
                         (n1.listeners + n2.listeners - p.shared_listeners), 5) AS jaccard,
                   ROUND(p.shared_listeners::DOUBLE /
                         SQRT(n1.listeners * n2.listeners), 5) AS cosine,
                   '{now_iso()}' AS knowledge_time,
                   'all_time' AS period,
                   '{DUMP_VERSION}' AS source_version,
                   'ARTIST_SECURITY_25000' AS source
            FROM pairs p
            JOIN nodes n1 ON n1.artist_key = p.artist_key_a
            JOIN nodes n2 ON n2.artist_key = p.artist_key_b
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_edges = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out}')").fetchone()[0]
    upload(s3, out, "gold/audience_affinity/all_time/part0.parquet")
    for f in pair_locals + node_locals:
        Path(f).unlink(missing_ok=True)
    ckpt["completed_pairs"] = True
    ckpt["affinity_edges"] = n_edges
    save_checkpoint(s3, ckpt)
    print(f"gold affinity edges: {n_edges:,}")


def cmd_status(args) -> None:
    """Read-only readiness report; never contacts R2 or starts a scan."""
    ckpt = load_checkpoint()
    index_ok = INDEX_CACHE.exists()
    shard_count = None
    if index_ok:
        try:
            shard_count = len(data_shards(json.loads(INDEX_CACHE.read_text())))
        except (OSError, TypeError, ValueError, KeyError):
            index_ok = False
    try:
        validate_checkpoint(ckpt, partitions=args.partitions)
        checkpoint_ok = True
        checkpoint_error = None
    except RuntimeError as exc:
        checkpoint_ok = False
        checkpoint_error = str(exc)
    completed = set()
    for rng in ckpt.get("completed_batches", []) or []:
        if isinstance(rng, list) and len(rng) == 2:
            completed.update(range(int(rng[0]), int(rng[1]) + 1))
    target = args.target_shards
    print(json.dumps({
        "pipeline": "listenbrainz_full_scan",
        "read_only": True,
        "index_present": index_ok,
        "indexed_data_shards": shard_count,
        "checkpoint_valid_for_partitions": checkpoint_ok,
        "checkpoint_error": checkpoint_error,
        "completed_shards": len(completed),
        "target_shards": target,
        "bounded_map_command": (
            f"PYTHONPATH=python .venv/bin/python scripts/lb_full_scan.py map "
            f"--max-shards {target} --partitions {args.partitions}"
        ),
        "ready_for_bounded_map": bool(index_ok and shard_count and shard_count >= target and checkpoint_ok),
    }, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("map")
    pm.add_argument("--max-shards", type=int, default=1526)
    pm.add_argument("--partitions", type=int, default=256,
                    help="listener hash partitions for affinity (must be stable across run)")
    pm.set_defaults(fn=cmd_map)

    ps = sub.add_parser("reduce-artist-day")
    ps.set_defaults(fn=cmd_reduce_artist_day)

    pa = sub.add_parser("reduce-affinity")
    pa.add_argument("--partitions", type=int, default=256)
    pa.set_defaults(fn=cmd_reduce_affinity)

    pp = sub.add_parser("reduce-pairs")
    pp.set_defaults(fn=cmd_reduce_pairs)

    ps = sub.add_parser("status", help="read-only checkpoint/index readiness report")
    ps.add_argument("--target-shards", type=int, default=76)
    ps.add_argument("--partitions", type=int, default=256)
    ps.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
