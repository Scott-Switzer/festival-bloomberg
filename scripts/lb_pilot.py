"""P7 — ListenBrainz bounded pilot (~1% of the 205 GB full dump).

Memory-bounded design (this Mac has 8 GB RAM — the previous pure-dict
implementation thrashed at a 20 GB footprint):

    1. Build a reusable tar index with tiny header-only range requests.
    2. Pick a deterministic ~1% slice of data shards spread across the archive.
    3. Stream each shard from R2, filter to ARTIST_SECURITY_25000 via
       `artist_credit_mbids` (DIRECT_ARTIST_MBID only — no fuzzy promotion).
    4. Append matched rows to a local Parquet scratch file (never hold all
       matched rows in Python memory).
    5. Aggregation runs in DuckDB SQL (spills to disk):
         silver/listenbrainz/artist_day_attention.parquet
         silver/listenbrainz/listener_artist_aggregate.parquet  (restricted)
         gold/listenbrainz_pilot/artist_audience_affinity.parquet

Resolution is by MusicBrainz ID only. A listen matches when any MBID in
`artist_credit_mbids` is in the 25K universe MBID set.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/lb_pilot.py
    PYTHONPATH=python .venv/bin/python scripts/lb_pilot.py --slice-frac 0.015
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
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
WAREHOUSE = "/tmp/artist_security_1000.duckdb"
ESTATE_JSON = Path("data/control/artist_security_25000/v1/"
                   "estate_20260828T013314Z_f87e5d1d073e.json")
INDEX_CACHE = Path("control/lake/lb_tar_index.json")  # repo path survives /tmp resets
SCAN_PHASE = Path("control/lake/lb_scan_phase.json")   # persisted scan stats for agg-only runs
SCRATCH = Path("/tmp/lb_pilot_matched.parquet")       # matched-rows staging
TMPDIR = Path("/tmp/lb_duckdb_spill")                 # DuckDB spill dir

# Minimum shared listeners to persist an affinity edge (sparse graph policy).
MIN_SHARED_LISTENERS = 3

# Per-listener artist-degree cap before pair generation. Power listeners who
# match dozens/hundreds of 25K artists would otherwise dominate the pair join
# (measured: 15,462 listeners with k>50 generate ~339M of ~344M pairs). Keeping
# each listener's top-N artists by listen count bounds the join to ~C(N,2) per
# listener and prevents extreme users from dominating affinity evidence.
MAX_ARTISTS_PER_LISTENER = 25

# Column order for outputs
ARTIST_DAY_COLS = ["artist_key", "obs_day", "listen_count", "unique_listeners",
                   "unique_recordings", "source", "source_version", "knowledge_time"]
LA_COLS = ["listener_key", "artist_key", "listen_count", "first_listen", "last_listen",
           "source", "source_version", "knowledge_time"]
EDGE_COLS = ["artist_key_a", "artist_key_b", "shared_listeners", "jaccard",
             "period_start", "period_end", "evidence_strength", "source", "knowledge_time"]


def build_tar_index(s3) -> list[dict]:
    """Walk tar headers with 512-byte range GETs; return [{name, data_offset, size}].

    Resumable: an existing partial cache stores members; we continue from the end
    of the last member so an interrupted walk never re-scans the whole archive.
    """
    members: list[dict] = []
    off = 0
    if INDEX_CACHE.exists():
        try:
            members = json.loads(INDEX_CACHE.read_text())
            if members:
                m = members[-1]
                off = m["offset"] + ((m["size"] + 511) // 512) * 512
            print(f"index: resuming from offset {off:,} ({len(members)} already cached)")
        except Exception:  # noqa: BLE001
            members, off = [], 0
    last = time.time()
    while True:
        try:
            resp = s3.get_object(Bucket=RAW_BUCKET, Key=RAW_KEY,
                                 Range=f"bytes={off}-{off + 511}")
            header = resp["Body"].read(512)
        except Exception as exc:  # noqa: BLE001 — InvalidRange / EOF → end of archive
            print(f"  index walk: EOF at offset {off:,} ({exc.__class__.__name__})")
            break
        if len(header) < 512:
            break
        if header == b"\0" * 512:
            off += 512
            continue
        name = header[0:100].split(b"\0")[0].decode("utf-8", "replace")
        size_field = header[124:136].split(b"\0")[0]
        try:
            size = int(size_field or b"0", 8)
        except ValueError:
            break
        data_start = off + 512
        members.append({"name": name, "offset": data_start, "size": size})
        off = data_start + ((size + 511) // 512) * 512
        if time.time() - last > 5:
            INDEX_CACHE.write_text(json.dumps(members, indent=0))
            print(f"  index walk: {len(members)} members @ offset {off:,} ...", flush=True)
            last = time.time()
    INDEX_CACHE.write_text(json.dumps(members, indent=0))
    print(f"index: {len(members)} members, cached → {INDEX_CACHE}", flush=True)
    return members


def data_shards(members: list[dict]) -> list[dict]:
    """Data shards = *.parquet members whose basename is a number (>=3 in dump layout)."""
    out = []
    for m in members:
        base = m["name"].split("/")[-1]
        if base.endswith(".parquet"):
            stem = base[: -len(".parquet")]
            if stem.isdigit():
                out.append(m)
    return out


def load_universe() -> dict[str, dict]:
    """mbid -> {artist_key, tier, bucket} for the 25K universe."""
    if Path(WAREHOUSE).exists():
        try:
            con = duckdb.connect(WAREHOUSE, read_only=True)
            rows = con.execute(
                "SELECT artist_key, artist_name, mbid, tier, selection_bucket "
                "FROM security.artist_security_universe_25000 WHERE mbid IS NOT NULL"
            ).fetchall()
            con.close()
            return {r[2]: {"key": r[0], "name": r[1], "tier": r[3], "bucket": r[4]} for r in rows}
        except Exception as exc:  # noqa: BLE001
            print(f"  (duckdb unavailable: {exc.__class__.__name__}; using estate)")
    data = json.loads(ESTATE_JSON.read_text())
    out = {}
    for a in data.get("artists", []):
        mbid = a.get("mbid")
        if mbid:
            out[mbid] = {"key": a.get("key"), "name": a.get("name"),
                         "tier": a.get("tier"), "bucket": a.get("tier")}
    return out


def fetch_shard(s3, m: dict) -> bytes:
    """Fetch one shard's byte range with retry on connection break."""
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
            print(f"  shard fetch retry {attempts} ({e.__class__.__name__})", flush=True)
            time.sleep(min(60, 5 * attempts))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice-frac", type=float, default=0.01,
                    help="fraction of data shards to scan (default 0.01)")
    ap.add_argument("--agg-only", action="store_true",
                    help="skip the scan; reuse the staged matched-rows scratch file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    s3 = r2_client()
    print("=== building tar index ===")
    members = build_tar_index(s3)
    shards = data_shards(members)
    print(f"data shards: {len(shards):,}")

    stride = max(1, int(round(len(shards) / max(1.0, len(shards) * args.slice_frac))))
    sel = shards[::stride]
    print(f"pilot slice: {len(sel)}/{len(shards)} shards "
          f"(frac={len(sel)/max(1,len(shards)):.3%})")

    print("=== loading 25K universe (MBID set) ===")
    universe = load_universe()
    print(f"universe MBIDs: {len(universe):,}")

    # --- Phase A: stream shards → matched-rows Parquet scratch ---
    retained = scanned = 0
    by_res = {"RESOLVED": 0, "UNRESOLVED": 0, "NO_CREDIT": 0}
    matched_artists: set[str] = set()
    if not args.agg_only:
        if SCRATCH.exists():
            SCRATCH.unlink()
        TMPDIR.mkdir(parents=True, exist_ok=True)
        writer: pq.ParquetWriter | None = None
        n_shards = len(sel)
        for i, m in enumerate(sel):
            shard_t0 = time.time()
            raw = fetch_shard(s3, m)
            scanned += m["size"]
            pf = pq.ParquetFile(io.BytesIO(raw))
            table = pf.read()
            del raw
            for batch in table.to_batches(max_chunksize=500_000):
                ra_ar = batch.column("artist_credit_mbids").to_pylist()
                u_ar = batch.column("user_id").to_pylist()
                la_ar = batch.column("listened_at").to_pylist()
                rm_ar = batch.column("recording_mbid").to_pylist()
                out_key, out_uid, out_la, out_rm = [], [], [], []
                for j in range(batch.num_rows):
                    ambs = ra_ar[j]
                    if not ambs:
                        by_res["NO_CREDIT"] += 1
                        continue
                    hit = None
                    for amb in ambs:
                        if amb in universe:
                            hit = universe[amb]
                            break
                    if not hit:
                        by_res["UNRESOLVED"] += 1
                        continue
                    by_res["RESOLVED"] += 1
                    retained += 1
                    matched_artists.add(hit["key"])
                    out_key.append(hit["key"])
                    out_uid.append(int(u_ar[j]))
                    out_la.append(la_ar[j])
                    out_rm.append(rm_ar[j])
                if out_key:
                    t = pa.table({"artist_key": out_key, "listener_key": out_uid,
                                  "listened_at": out_la, "recording_mbid": out_rm})
                    if writer is None:
                        writer = pq.ParquetWriter(SCRATCH, t.schema, compression="zstd")
                    writer.write_table(t)
            del table
            mbps = m["size"] / max(0.001, time.time() - shard_t0) / 1e6
            print(f"  shard {i + 1}/{n_shards} {m['name']} "
                  f"{m['size']/1048576:.0f} MiB  {mbps:.1f} MB/s  "
                  f"matched so far: {retained:,}", flush=True)
        if writer is not None:
            writer.close()
        scan_runtime = time.time() - t0
        print(f"\n=== scan phase done ({scan_runtime:.1f}s) ===")
        print(f"scanned bytes: {scanned/1e9:.2f} GB  (frac {scanned/205_073_162_240:.4%})")
        print(f"resolution: {dict(by_res)}")
        print(f"retained (matched 25K): {retained:,} "
              f"({retained/max(1, retained + by_res['UNRESOLVED'] + by_res['NO_CREDIT']):.2%})")
        print(f"unique artists matched: {len(matched_artists):,}")
        # persist scan-phase stats so an agg-only rerun keeps a true report
        SCAN_PHASE.write_text(json.dumps({
            "scanned_bytes": scanned, "scan_runtime_seconds": round(scan_runtime, 1),
            "resolution": dict(by_res), "listens_matched": retained,
            "unique_artists_matched": len(matched_artists),
            "shards_selected": len(sel), "shards_total": len(shards),
            "slice_frac": args.slice_frac,
        }, indent=2))
    else:
        print(f"(agg-only) reusing staged matched rows: {SCRATCH}")
        if not SCRATCH.exists():
            raise RuntimeError("--agg-only requested but staged matched rows are missing")
        if SCAN_PHASE.exists():
            sp = json.loads(SCAN_PHASE.read_text())
            if sp.get("shards_total") != len(shards) or sp.get("slice_frac") != args.slice_frac:
                raise RuntimeError(
                    "staged scan metadata does not match this --slice-frac/archive; "
                    "rerun without --agg-only"
                )
            scanned = sp["scanned_bytes"]
            by_res = sp["resolution"]
            retained = sp["listens_matched"]
            matched_artists = set()  # not needed for report beyond count
            print(f"  scan stats restored: {scanned/1e9:.2f} GB, "
                  f"{retained:,} matched, {sp['resolution']}")

    if args.dry_run:
        return

    # --- Phase B: DuckDB aggregation (spills to disk, not Python dicts) ---
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{TMPDIR}'")
    con.execute(f"CREATE TABLE matched AS SELECT * FROM read_parquet('{SCRATCH}')")
    n_matched = con.execute("SELECT COUNT(*) FROM matched").fetchone()[0]
    print(f"matched rows staged: {n_matched:,}")

    # artist_day_attention
    ad_path = Path("/tmp/lb_out_artist_day.parquet")
    con.execute(f"""
        COPY (
            SELECT artist_key, CAST(listened_at AS DATE) AS obs_day,
                   COUNT(*) AS listen_count,
                   COUNT(DISTINCT listener_key) AS unique_listeners,
                   COUNT(DISTINCT recording_mbid) AS unique_recordings,
                   '{now}' AS knowledge_time
            FROM matched GROUP BY 1, 2
        ) TO '{ad_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_ad = con.execute(f"SELECT COUNT(*) FROM read_parquet('{ad_path}')").fetchone()[0]
    print(f"artist_day cells: {n_ad:,}")

    # listener_artist_aggregate (restricted — pseudonymous numeric listener_key)
    la_path = Path("/tmp/lb_out_listener_artist.parquet")
    con.execute(f"""
        COPY (
            SELECT CAST(listener_key AS VARCHAR) AS listener_key, artist_key,
                   COUNT(*) AS listen_count,
                   MIN(listened_at) AS first_listen, MAX(listened_at) AS last_listen,
                   '{now}' AS knowledge_time
            FROM matched GROUP BY 1, 2
        ) TO '{la_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_la = con.execute(f"SELECT COUNT(*) FROM read_parquet('{la_path}')").fetchone()[0]
    print(f"listener×artist rows: {n_la:,}")

    # affinity edges: sparse graph from shared listeners. Per-listener degree is
    # capped at MAX_ARTISTS_PER_LISTENER (top-N by listen count) so super-listeners
    # cannot dominate the pair join.
    edge_path = Path("/tmp/lb_out_affinity.parquet")
    pair_est = con.execute(f"""
        WITH la AS (
            SELECT listener_key, artist_key, COUNT(*) AS listens
            FROM matched GROUP BY 1, 2
        ),
        ranked AS (
            SELECT listener_key, artist_key,
                   ROW_NUMBER() OVER (PARTITION BY listener_key
                                      ORDER BY listens DESC, artist_key) AS rn
            FROM la
        )
        SELECT COUNT(*) FROM ranked WHERE rn <= {MAX_ARTISTS_PER_LISTENER}
    """).fetchone()[0]
    print(f"bounded listener×artist rows (top-{MAX_ARTISTS_PER_LISTENER}/listener): "
          f"{pair_est:,}")
    con.execute(f"""
        COPY (
            WITH la AS (
                SELECT listener_key, artist_key, COUNT(*) AS listens
                FROM matched GROUP BY 1, 2
            ),
            ranked AS (
                SELECT listener_key, artist_key,
                       ROW_NUMBER() OVER (PARTITION BY listener_key
                                          ORDER BY listens DESC, artist_key) AS rn
                FROM la
            ),
            bounded AS (
                SELECT listener_key, artist_key FROM ranked
                WHERE rn <= {MAX_ARTISTS_PER_LISTENER}
            ),
            pairs AS (
                SELECT a.artist_key AS artist_key_a, b.artist_key AS artist_key_b,
                       COUNT(*) AS shared_listeners
                FROM bounded a JOIN bounded b
                  ON a.listener_key = b.listener_key AND a.artist_key < b.artist_key
                GROUP BY 1, 2
                HAVING COUNT(*) >= {MIN_SHARED_LISTENERS}
            ),
            stats AS (
                SELECT artist_key, COUNT(DISTINCT listener_key) AS listeners
                FROM matched GROUP BY 1
            )
            SELECT p.artist_key_a, p.artist_key_b, p.shared_listeners,
                   ROUND(p.shared_listeners::DOUBLE /
                         (s1.listeners + s2.listeners - p.shared_listeners), 5) AS jaccard,
                   '{now}' AS knowledge_time
            FROM pairs p
            JOIN stats s1 ON s1.artist_key = p.artist_key_a
            JOIN stats s2 ON s2.artist_key = p.artist_key_b
            ORDER BY p.shared_listeners DESC
        ) TO '{edge_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n_edges = con.execute(f"SELECT COUNT(*) FROM read_parquet('{edge_path}')").fetchone()[0]
    print(f"audience-affinity edges (shared>={MIN_SHARED_LISTENERS}): {n_edges:,}")
    con.close()

    # --- Phase C: upload + report ---
    def upload(local: Path, key: str) -> tuple[int, int, str]:
        blob = local.read_bytes()
        s3.put_object(Bucket=LAKE_BUCKET, Key=key, Body=blob)
        rows = pq.ParquetFile(io.BytesIO(blob)).metadata.num_rows
        checksum = hashlib.sha256(blob).hexdigest()
        print(f"  → r2://{LAKE_BUCKET}/{key}  {rows:,} rows, {len(blob)/1048576:.2f} MB")
        return rows, len(blob), checksum

    ad_uploaded = upload(ad_path, "silver/listenbrainz/artist_day_attention.parquet")
    la_uploaded = upload(la_path, "silver/listenbrainz/listener_artist_aggregate.parquet")
    edge_uploaded = upload(edge_path, "gold/listenbrainz_pilot/artist_audience_affinity.parquet")

    # register pilot datasets in the lake catalog (P0)
    from festival_bloomberg.lake.catalog import register_dataset
    for ds_id, key, rows, cols, uploaded in [
        ("silver.listenbrainz_artist_day_attention",
         "silver/listenbrainz/artist_day_attention.parquet", n_ad, ARTIST_DAY_COLS, ad_uploaded),
        ("silver.listenbrainz_listener_artist_aggregate",
         "silver/listenbrainz/listener_artist_aggregate.parquet", n_la, LA_COLS, la_uploaded),
        ("gold.listenbrainz_pilot_artist_audience_affinity",
         "gold/listenbrainz_pilot/artist_audience_affinity.parquet", n_edges, EDGE_COLS, edge_uploaded),
    ]:
        try:
            register_dataset(
                dataset_id=ds_id, dataset_version=f"{DUMP_VERSION}-pilot-slice-{args.slice_frac}",
                layer=ds_id.split(".")[0].upper(), source="listenbrainz",
                source_version=DUMP_VERSION, r2_bucket=LAKE_BUCKET, r2_prefix=key,
                fmt="parquet", schema_version="silver-v1", row_count=rows, byte_count=uploaded[1],
                artifact_checksum=uploaded[2],
                verification_status="PILOT_BUILD_COMPLETE", license="CC0-1.0",
                rights_status="DERIVED_FROM_PUBLIC_DOMAIN", commercial_use_status="ALLOWED",
                upstream_dataset_ids=["raw.listenbrainz_full_dump"],
            )
            print(f"  catalog: {ds_id} registered")
        except Exception as exc:  # noqa: BLE001
            print(f"  catalog: {ds_id} skipped ({exc.__class__.__name__})")

    total_scanned_rows_est = retained + by_res["UNRESOLVED"] + by_res["NO_CREDIT"]
    runtime = time.time() - t0
    scan_runtime_agg = SCAN_PHASE.exists() and json.loads(SCAN_PHASE.read_text()).get("scan_runtime_seconds")
    proj_h = (205_073_162_240 / scanned) * (scan_runtime_agg or runtime) / 3600 if scanned else 0
    print(f"pilot runtime (this run): {runtime:.1f}s")
    print(f"projected full-scan time @ this rate: {proj_h:.2f} h")

    report = {
        "slice_frac": args.slice_frac,
        "shards_selected": len(sel),
        "shards_total": len(shards),
        "scanned_bytes": scanned,
        "scanned_fraction": round(scanned / 205_073_162_240, 6),
        "rows_scanned_estimate": total_scanned_rows_est,
        "listens_matched": retained,
        "resolution": dict(by_res),
        "unique_artists_matched": len(matched_artists),
        "artist_day_cells": n_ad,
        "listener_artist_rows": n_la,
        "affinity_edges": n_edges,
        "runtime_seconds": round(runtime, 1),
        "projected_full_scan_hours": round(proj_h, 2),
        "knowledge_time": now,
    }
    out = Path("control/lake/listenbrainz_pilot_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"report → {out}")


if __name__ == "__main__":
    main()
