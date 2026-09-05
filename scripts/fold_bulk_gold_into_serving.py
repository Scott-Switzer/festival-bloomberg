#!/usr/bin/env python3
"""Fold BULK_CORPUS_ACTIVATION_V1 gold into hosted serving DuckDB.

Adds/refreshs bounded 25K projections:
  - artist_reference_projection (MBID + Wikidata QID + external IDs)
  - artist_geography_observations (structured WD location properties)
  - musicbrainz_event_appearances (25K × MB event edges)
  - refreshes artist_peers source_scope label from affinity gold

Does NOT rebuild the full terminal from lake inputs.
Uses wrangler OAuth for R2. No tickets.dev.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAKE = "festival-intelligence-lake"
SERVING_PREFIX = "serving/artist_security_terminal_v1"
GOLD_NAMES = (
    "artist_reference_ids",
    "artist_geography",
    "artist_event_history",
    "artist_audience_affinity",
)


def _run_wrangler(args: list[str]) -> None:
    subprocess.run(
        ["npx", "wrangler", *args],
        cwd=str(PROJECT_ROOT / "cloud-runtime"),
        check=True,
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(bucket: str, key: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_wrangler(["r2", "object", "get", f"{bucket}/{key}", f"--file={dest}", "--remote"])


def _put(bucket: str, key: str, src: Path, content_type: str) -> None:
    _run_wrangler([
        "r2", "object", "put", f"{bucket}/{key}",
        f"--file={src}", "--remote", "--content-type", content_type,
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    work = args.work or Path(tempfile.mkdtemp(prefix="bulk_serving_fold_"))
    work.mkdir(parents=True, exist_ok=True)

    cur_path = work / "CURRENT.json"
    _get(LAKE, f"{SERVING_PREFIX}/CURRENT.json", cur_path)
    serving_cur = json.loads(cur_path.read_text())

    gold_meta = {}
    local_parquets = {}
    for name in GOLD_NAMES:
        gcur_path = work / f"{name}_CURRENT.json"
        _get(LAKE, f"gold/{name}/CURRENT.json", gcur_path)
        gcur = json.loads(gcur_path.read_text())
        gpath = work / Path(gcur["object_key"]).name
        _get(LAKE, gcur["object_key"], gpath)
        actual = _sha256(gpath)
        if gcur.get("sha256") and actual != gcur["sha256"]:
            raise SystemExit(f"GOLD_HASH_MISMATCH {name}: {actual} != {gcur['sha256']}")
        gold_meta[name] = {**gcur, "readback_sha256": actual}
        local_parquets[name] = gpath

    db_path = work / "terminal.duckdb"
    _get(LAKE, serving_cur["object_key"], db_path)

    conn = duckdb.connect(str(db_path))
    counts: dict[str, int] = {}
    try:
        conn.execute("PRAGMA threads=2")
        q = lambda p: "'" + str(p.resolve()).replace("'", "''") + "'"

        # ── Reference projection (exact MBID↔QID joins; collisions preserved) ──
        conn.execute("DROP TABLE IF EXISTS artist_reference_projection")
        conn.execute(
            f"""
            CREATE TABLE artist_reference_projection AS
            SELECT
              artist_key,
              musicbrainz_id,
              artist_name,
              wikidata_qid,
              external_id_property,
              external_id_name,
              external_id_value,
              source_system,
              knowledge_time,
              wikidata_generation,
              CASE
                WHEN wikidata_qid IS NOT NULL AND wikidata_qid IN (
                  SELECT wikidata_qid FROM read_parquet({q(local_parquets['artist_reference_ids'])})
                  WHERE wikidata_qid IS NOT NULL
                  GROUP BY 1 HAVING COUNT(DISTINCT artist_key) > 1
                ) THEN 'AMBIGUOUS_SHARED_QID'
                WHEN wikidata_qid IS NOT NULL THEN 'EXACT_MBID_TO_QID'
                ELSE 'MBID_ONLY'
              END AS identity_status
            FROM read_parquet({q(local_parquets['artist_reference_ids'])})
            """
        )
        counts["artist_reference_projection"] = int(
            conn.execute("SELECT COUNT(*) FROM artist_reference_projection").fetchone()[0]
        )

        # Upsert Wikidata QID into artist_external_ids without collapsing collisions.
        conn.execute(
            """
            INSERT INTO artist_external_ids
            SELECT
              'wd::' || artist_key || '::' || wikidata_qid AS external_id_key,
              artist_key,
              'wikidata_qid' AS id_type,
              wikidata_qid AS id_value,
              'https://www.wikidata.org/wiki/' || wikidata_qid AS url,
              'wikidata' AS source_system,
              'GOLD_ARTIST_REFERENCE_IDS' AS source_scope,
              CAST(knowledge_time AS TIMESTAMP) AS knowledge_time,
              identity_status AS status,
              'EXACT_MBID_JOIN' AS resolution_method,
              CASE WHEN identity_status = 'AMBIGUOUS_SHARED_QID' THEN 0.5 ELSE 1.0 END AS confidence
            FROM (
              SELECT DISTINCT artist_key, wikidata_qid, knowledge_time, identity_status
              FROM artist_reference_projection
              WHERE wikidata_qid IS NOT NULL
            ) s
            WHERE NOT EXISTS (
              SELECT 1 FROM artist_external_ids e
              WHERE e.artist_key = s.artist_key
                AND e.id_type = 'wikidata_qid'
                AND e.id_value = s.wikidata_qid
            )
            """
        )
        counts["artist_external_ids"] = int(
            conn.execute("SELECT COUNT(*) FROM artist_external_ids").fetchone()[0]
        )

        # ── Geography ──
        conn.execute("DROP TABLE IF EXISTS artist_geography_observations")
        conn.execute(
            f"""
            CREATE TABLE artist_geography_observations AS
            SELECT
              artist_key,
              musicbrainz_id,
              artist_name,
              wikidata_qid,
              location_property,
              location_qid,
              source_system,
              knowledge_time,
              wikidata_generation
            FROM read_parquet({q(local_parquets['artist_geography'])})
            WHERE location_qid IS NOT NULL
            """
        )
        counts["artist_geography_observations"] = int(
            conn.execute("SELECT COUNT(*) FROM artist_geography_observations").fetchone()[0]
        )

        # ── MusicBrainz event appearances (bounded 25K projection) ──
        conn.execute("DROP TABLE IF EXISTS musicbrainz_event_appearances")
        conn.execute(
            f"""
            CREATE TABLE musicbrainz_event_appearances AS
            SELECT * FROM read_parquet({q(local_parquets['artist_event_history'])})
            """
        )
        counts["musicbrainz_event_appearances"] = int(
            conn.execute("SELECT COUNT(*) FROM musicbrainz_event_appearances").fetchone()[0]
        )

        # ── Affinity: relabel peers; refresh from gold if peer table empty for an artist ──
        # Update existing peer rows to honest consumption-affinity semantics.
        conn.execute(
            """
            UPDATE artist_peers SET
              source_scope = 'LISTENBRAINZ_CONSUMPTION_AFFINITY_PILOT',
              explanation = COALESCE(
                explanation,
                'LISTENBRAINZ CONSUMPTION AFFINITY — shared listening in pilot sample; NOT ticket demand, local demand, or fan crossover probability.'
              )
            WHERE source_system ILIKE '%listenbrainz%'
               OR source_scope ILIKE '%listen%'
               OR source_scope IS NULL
               OR source_scope = ''
            """
        )
        # Ensure any peer without explanation gets the label.
        conn.execute(
            """
            UPDATE artist_peers SET
              source_scope = 'LISTENBRAINZ_CONSUMPTION_AFFINITY_PILOT',
              explanation = 'LISTENBRAINZ CONSUMPTION AFFINITY — shared listening in pilot sample; NOT ticket demand, local demand, or fan crossover probability.'
            WHERE explanation IS NULL OR explanation = ''
            """
        )
        counts["artist_peers"] = int(conn.execute("SELECT COUNT(*) FROM artist_peers").fetchone()[0])

        # product_meta lineage
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS product_meta (
                  key VARCHAR PRIMARY KEY,
                  value VARCHAR
                )
                """
            )
            lineage = json.dumps({
                "bulk_gold_fold": gold_meta,
                "folded_at": datetime.now(UTC).isoformat(),
                "affinity_label": "LISTENBRAINZ CONSUMPTION AFFINITY",
            }, sort_keys=True)
            conn.execute(
                "INSERT OR REPLACE INTO product_meta VALUES ('bulk_corpus_activation_fold', ?)",
                [lineage],
            )
        except Exception as exc:
            print("product_meta note:", exc)

        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    generation = "terminal_v1_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    new_key = f"{SERVING_PREFIX}/generations/{generation}/terminal.duckdb"
    db_sha = _sha256(db_path)
    db_bytes = db_path.stat().st_size

    new_current = dict(serving_cur)
    new_current.update({
        "generation": generation,
        "object_key": new_key,
        "sha256": db_sha,
        "bytes": db_bytes,
        "created_at": datetime.now(UTC).isoformat(),
        "parent_generation": serving_cur.get("generation"),
        "bulk_corpus_fold": {
            "gold": {k: {
                "generation": v.get("generation"),
                "object_key": v.get("object_key"),
                "sha256": v.get("readback_sha256"),
                "rows": v.get("rows"),
            } for k, v in gold_meta.items()},
            "row_counts": counts,
            "affinity_label": "LISTENBRAINZ CONSUMPTION AFFINITY",
        },
    })
    row_counts = dict(new_current.get("row_counts") or {})
    row_counts.update(counts)
    new_current["row_counts"] = row_counts

    out_current = work / "NEW_CURRENT.json"
    out_current.write_text(json.dumps(new_current, indent=2) + "\n")
    summary = {
        "generation": generation,
        "object_key": new_key,
        "sha256": db_sha,
        "bytes": db_bytes,
        "row_counts": counts,
        "parent": serving_cur.get("generation"),
    }
    print(json.dumps(summary, indent=2))
    if args.dry_run:
        return 0

    _put(LAKE, new_key, db_path, "application/octet-stream")
    _put(LAKE, f"{SERVING_PREFIX}/CURRENT.json", out_current, "application/json")
    print(json.dumps({"published": True, **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
