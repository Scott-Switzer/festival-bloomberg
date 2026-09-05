#!/usr/bin/env python3
"""Project existing R2 silver/gold corpora onto the 25K Artist Security universe.

Produces immutable gold CURRENT pointers under:
  gold/artist_reference_ids/
  gold/artist_geography/
  gold/artist_audience_affinity/   (re-point / copy from pilot if needed)
  gold/artist_event_history/

Does NOT redownload raw dumps. Joins by MBID / artist_key only (no fuzzy).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

PROJECT = Path(__file__).resolve().parents[1]
LAKE = "festival-intelligence-lake"
BACKUPS = "festival-intelligence-backups"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _wrangler_get(bucket: str, key: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["npx", "wrangler", "r2", "object", "get", f"{bucket}/{key}", f"--file={dest}", "--remote"],
        cwd=str(PROJECT / "cloud-runtime"),
        check=True,
    )


def _wrangler_put(bucket: str, key: str, src: Path, content_type: str) -> None:
    subprocess.run(
        [
            "npx", "wrangler", "r2", "object", "put", f"{bucket}/{key}",
            f"--file={src}", "--remote", "--content-type", content_type,
        ],
        cwd=str(PROJECT / "cloud-runtime"),
        check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=Path("/tmp/bulk_activation_v1"))
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()
    work = args.work
    work.mkdir(parents=True, exist_ok=True)

    # Estate pointer → artists
    _wrangler_get(BACKUPS, "control/artist_security_25000/current.json", work / "estate_cur.json")
    estate_cur = json.loads((work / "estate_cur.json").read_text())
    estate_key = estate_cur["source"]
    _wrangler_get(BACKUPS, estate_key, work / "estate.json")
    estate = json.loads((work / "estate.json").read_text())
    artists = estate["artists"]
    rows = []
    for a in artists:
        key = a.get("key") or a.get("artist_key")
        mbid = a.get("musicbrainz_id") or (key.replace("mbid::", "") if key and key.startswith("mbid::") else None)
        rows.append((key, mbid, a.get("name")))
    estate_csv = work / "estate.csv"
    with estate_csv.open("w", encoding="utf-8") as fh:
        fh.write("artist_key,musicbrainz_id,name\n")
        for key, mbid, name in rows:
            safe = (name or "").replace('"', "'")
            fh.write(f'{key},{mbid},"{safe}"\n')

    # Inputs already present or download
    inputs = {
        "wd_ids": ("festival-intelligence-lake", "silver/wikidata/generations/20260905T172934Z-1606/artist_external_ids.parquet"),
        "wd_locations": ("festival-intelligence-lake", "silver/wikidata/generations/20260905T172934Z-1606/entity_locations.parquet"),
        "wd_genres": ("festival-intelligence-lake", "silver/wikidata/generations/20260905T172934Z-1606/genres.parquet"),
        "event_edges": ("festival-intelligence-lake", "silver/events/event_artist_edges.parquet"),
        "events": ("festival-intelligence-lake", "silver/events/events.parquet"),
        "affinity": ("festival-intelligence-lake", "gold/listenbrainz_pilot/artist_audience_affinity.parquet"),
    }
    local = {}
    for name, (bucket, key) in inputs.items():
        dest = work / Path(key).name
        if not dest.exists() or dest.stat().st_size == 0:
            try:
                _wrangler_get(bucket, key, dest)
            except subprocess.CalledProcessError:
                dest.write_bytes(b"")
        local[name] = dest
        if dest.stat().st_size == 0 and name.startswith("wd_"):
            print(f"WARN missing optional {key}")

    con = duckdb.connect(str(work / "activation.duckdb"))
    con.execute(f"CREATE OR REPLACE TABLE estate AS SELECT * FROM read_csv_auto('{estate_csv.as_posix()}')")
    gen = "bulk_activation_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = work / "gold" / gen
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Reference IDs (Wikidata + MBID) ──
    ref_path = out_dir / "artist_reference_ids.parquet"
    con.execute(
        f"""
        COPY (
          SELECT
            e.artist_key,
            e.musicbrainz_id,
            e.name AS artist_name,
            w.qid AS wikidata_qid,
            w.external_id_property,
            w.external_id_name,
            w.external_id_value,
            w.source_system,
            w.knowledge_time,
            '20260905T172934Z-1606' AS wikidata_generation
          FROM estate e
          LEFT JOIN read_parquet('{local['wd_ids'].as_posix()}') w
            ON w.external_id_name = 'musicbrainz_artist_id'
           AND w.external_id_value = e.musicbrainz_id
          ORDER BY e.artist_key, w.external_id_property
        ) TO '{ref_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    # ── Geography from Wikidata locations via QID from reference ──
    geo_path = out_dir / "artist_geography.parquet"
    if local["wd_locations"].stat().st_size > 0:
        con.execute(
            f"""
            COPY (
              WITH qids AS (
                SELECT DISTINCT artist_key, musicbrainz_id, artist_name, wikidata_qid
                FROM read_parquet('{ref_path.as_posix()}')
                WHERE wikidata_qid IS NOT NULL
              )
              SELECT
                q.artist_key, q.musicbrainz_id, q.artist_name, q.wikidata_qid,
                loc.location_property,
                loc.location_qid,
                loc.source_system, loc.knowledge_time,
                '20260905T172934Z-1606' AS wikidata_generation
              FROM qids q
              LEFT JOIN read_parquet('{local['wd_locations'].as_posix()}') loc
                ON loc.qid = q.wikidata_qid
            ) TO '{geo_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    else:
        con.execute(
            f"""
            COPY (
              SELECT DISTINCT artist_key, musicbrainz_id, artist_name, wikidata_qid,
                     CAST(NULL AS VARCHAR) AS location_property,
                     CAST(NULL AS VARCHAR) AS location_qid,
                     CAST(NULL AS VARCHAR) AS source_system,
                     CAST(NULL AS VARCHAR) AS knowledge_time,
                     '20260905T172934Z-1606' AS wikidata_generation
              FROM read_parquet('{ref_path.as_posix()}')
              WHERE wikidata_qid IS NOT NULL
            ) TO '{geo_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

    # ── Event history from MusicBrainz silver edges ──
    # Discover edge schema
    edge_cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{local['event_edges'].as_posix()}')").fetchall()]
    print("event_edge_cols", edge_cols)
    artist_col = "artist_mbid" if "artist_mbid" in edge_cols else ("mbid" if "mbid" in edge_cols else None)
    if artist_col is None:
        # try common names
        for c in edge_cols:
            if "artist" in c.lower() and "mbid" in c.lower():
                artist_col = c
                break
    if artist_col is None:
        raise SystemExit(f"cannot find artist mbid column in edges: {edge_cols}")

    hist_path = out_dir / "artist_event_history.parquet"
    con.execute(
        f"""
        COPY (
          SELECT
            e.artist_key,
            e.musicbrainz_id,
            e.name AS artist_name,
            edge.event_mbid,
            ev.name AS event_name,
            ev.event_type,
            ev.begin_date,
            ev.end_date,
            edge.source_system,
            edge.knowledge_time,
            '20260826-001001' AS musicbrainz_dump_version
          FROM estate e
          JOIN read_parquet('{local['event_edges'].as_posix()}') edge
            ON edge.{artist_col} = e.musicbrainz_id
          LEFT JOIN read_parquet('{local['events'].as_posix()}') ev
            ON ev.event_mbid = edge.event_mbid
        ) TO '{hist_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    # ── Audience affinity: filter pilot edges to 25K×25K ──
    aff_path = out_dir / "artist_audience_affinity.parquet"
    con.execute(
        f"""
        COPY (
          SELECT a.*
          FROM read_parquet('{local['affinity'].as_posix()}') a
          WHERE a.artist_key_a IN (SELECT artist_key FROM estate)
            AND a.artist_key_b IN (SELECT artist_key FROM estate)
        ) TO '{aff_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    # Coverage matrix
    matrix = {
        "MusicBrainz": {
            "artists_identified": 25000,
            "artists_with_data": int(con.execute(
                f"SELECT COUNT(DISTINCT artist_key) FROM read_parquet('{hist_path.as_posix()}')"
            ).fetchone()[0]),
            "rows_edges": int(con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{hist_path.as_posix()}')"
            ).fetchone()[0]),
            "pit_history": "event begin_date + knowledge_time",
            "serving": "via terminal_serving_build silver events (existing)",
        },
        "Wikidata": {
            "artists_identified": int(con.execute(
                f"SELECT COUNT(DISTINCT artist_key) FROM read_parquet('{ref_path.as_posix()}') WHERE wikidata_qid IS NOT NULL"
            ).fetchone()[0]),
            "artists_with_data": int(con.execute(
                f"SELECT COUNT(DISTINCT artist_key) FROM read_parquet('{ref_path.as_posix()}') WHERE wikidata_qid IS NOT NULL"
            ).fetchone()[0]),
            "rows_edges": int(con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{ref_path.as_posix()}') WHERE wikidata_qid IS NOT NULL"
            ).fetchone()[0]),
            "geography_artists": int(con.execute(
                f"SELECT COUNT(DISTINCT artist_key) FROM read_parquet('{geo_path.as_posix()}') WHERE location_qid IS NOT NULL"
            ).fetchone()[0]),
            "pit_history": "dump knowledge_time",
            "serving": "external_ids already folded; geography gold new",
        },
        "ListenBrainz": {
            "artists_identified": int(con.execute(
                f"""
                SELECT COUNT(DISTINCT k) FROM (
                  SELECT artist_key_a AS k FROM read_parquet('{aff_path.as_posix()}')
                  UNION ALL
                  SELECT artist_key_b FROM read_parquet('{aff_path.as_posix()}')
                )
                """
            ).fetchone()[0]),
            "artists_with_data": int(con.execute(
                f"""
                SELECT COUNT(DISTINCT k) FROM (
                  SELECT artist_key_a AS k FROM read_parquet('{aff_path.as_posix()}')
                  UNION ALL
                  SELECT artist_key_b FROM read_parquet('{aff_path.as_posix()}')
                )
                """
            ).fetchone()[0]),
            "rows_edges": int(con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{aff_path.as_posix()}')"
            ).fetchone()[0]),
            "pit_history": "pilot knowledge_time; full map/reduce may still be PARTIAL",
            "serving": "artist_peers in terminal (pilot)",
            "label": "ListenBrainz consumption affinity — NOT ticket demand",
        },
    }

    artifacts = {}
    for name, path in {
        "artist_reference_ids": ref_path,
        "artist_geography": geo_path,
        "artist_event_history": hist_path,
        "artist_audience_affinity": aff_path,
    }.items():
        sha = _sha(path)
        object_key = f"gold/{name}/generations/{gen}/{path.name}"
        current = {
            "artifact": name,
            "generation": gen,
            "object_key": object_key,
            "sha256": sha,
            "bytes": path.stat().st_size,
            "rows": int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]),
            "universe": "artist_security_25000",
            "estate_source": estate_key,
            "created_at": datetime.now(UTC).isoformat(),
            "implementation": "scripts/activate_bulk_corpora_25k.py",
        }
        cur_path = out_dir / f"{name}_CURRENT.json"
        cur_path.write_text(json.dumps(current, indent=2) + "\n")
        artifacts[name] = current
        if not args.no_upload:
            _wrangler_put(LAKE, object_key, path, "application/octet-stream")
            _wrangler_put(LAKE, f"gold/{name}/CURRENT.json", cur_path, "application/json")

    report = {
        "milestone": "BULK_CORPUS_ACTIVATION_V1",
        "generation": gen,
        "coverage_matrix": matrix,
        "artifacts": artifacts,
        "created_at": datetime.now(UTC).isoformat(),
    }
    report_path = work / "bulk_activation_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
