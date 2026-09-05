#!/usr/bin/env python3
"""Fold gold ticket_market_observations into the current hosted serving DuckDB
and publish a new immutable generation + CURRENT pointer.

Uses wrangler for R2 I/O (OAuth). Does not rebuild the full terminal from lake
inputs — only appends the ticket tape fold onto CURRENT.
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
GOLD_CURRENT = "gold/ticket_market_observations/CURRENT.json"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    work = args.work or Path(tempfile.mkdtemp(prefix="tm_serving_fold_"))
    work.mkdir(parents=True, exist_ok=True)

    current_path = work / "CURRENT.json"
    gold_current_path = work / "gold_CURRENT.json"
    _run_wrangler(["r2", "object", "get", f"{LAKE}/{SERVING_PREFIX}/CURRENT.json", f"--file={current_path}", "--remote"])
    _run_wrangler(["r2", "object", "get", f"{LAKE}/{GOLD_CURRENT}", f"--file={gold_current_path}", "--remote"])
    serving_cur = json.loads(current_path.read_text())
    gold_cur = json.loads(gold_current_path.read_text())

    db_key = serving_cur["object_key"]
    db_path = work / "terminal.duckdb"
    gold_key = gold_cur["object_key"]
    gold_path = work / "ticket_market_observations.parquet"
    _run_wrangler(["r2", "object", "get", f"{LAKE}/{db_key}", f"--file={db_path}", "--remote"])
    _run_wrangler(["r2", "object", "get", f"{LAKE}/{gold_key}", f"--file={gold_path}", "--remote"])

    actual_gold_sha = _sha256(gold_path)
    if gold_cur.get("sha256") and actual_gold_sha != gold_cur["sha256"]:
        raise SystemExit(f"GOLD_HASH_MISMATCH {actual_gold_sha} != {gold_cur['sha256']}")

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("PRAGMA threads=2")
        conn.execute("DROP TABLE IF EXISTS ticket_market_observations")
        conn.execute(
            """
            CREATE TABLE ticket_market_observations (
                observation_key VARCHAR PRIMARY KEY,
                artist_key VARCHAR,
                event_key VARCHAR NOT NULL,
                provider_event_id VARCHAR,
                artist_name VARCHAR,
                marketplace VARCHAR NOT NULL,
                venue_name VARCHAR,
                city VARCHAR,
                market_key VARCHAR,
                event_date DATE,
                source_url VARCHAR,
                observed_at TIMESTAMP,
                retrieved_at TIMESTAMP,
                knowledge_time TIMESTAMP,
                currency VARCHAR,
                face_value DOUBLE,
                all_in_price DOUBLE,
                resale_min_price DOUBLE,
                resale_median_price DOUBLE,
                resale_max_price DOUBLE,
                listing_count BIGINT,
                price_basis VARCHAR,
                evidence_status VARCHAR,
                evidence_ref VARCHAR,
                raw_payload_hash VARCHAR,
                rights_status VARCHAR,
                commercial_use_status VARCHAR,
                identity_match_status VARCHAR,
                parser_version VARCHAR,
                wave_label VARCHAR,
                cohort_version VARCHAR
            )
            """
        )
        gq = "'" + str(gold_path.resolve()).replace("'", "''") + "'"
        conn.execute(
            f"""
            INSERT INTO ticket_market_observations
            SELECT
                g.observation_key,
                COALESCE(
                    (SELECT f.artist_key FROM future_events f
                     WHERE g.provider_event_id IS NOT NULL
                       AND f.provider_event_id = g.provider_event_id
                     LIMIT 1),
                    (SELECT a.artist_key FROM artists a
                     WHERE g.artist_name IS NOT NULL
                       AND lower(a.name) = lower(g.artist_name)
                     LIMIT 1)
                ),
                g.event_key, g.provider_event_id, g.artist_name, g.marketplace,
                g.venue_name, g.city, g.market_key, TRY_CAST(g.event_date AS DATE),
                g.source_url,
                CAST(g.observed_at AS TIMESTAMP),
                CAST(g.retrieved_at AS TIMESTAMP),
                CAST(g.knowledge_time AS TIMESTAMP),
                g.currency, g.face_value, g.all_in_price, g.resale_min_price,
                g.resale_median_price, g.resale_max_price, g.listing_count,
                g.price_basis, g.evidence_status, g.evidence_ref, g.raw_payload_hash,
                g.rights_status, g.commercial_use_status, g.identity_match_status,
                g.parser_version, g.wave_label, g.cohort_version
            FROM read_parquet({gq}) g
            """
        )
        n = int(conn.execute("SELECT COUNT(*) FROM ticket_market_observations").fetchone()[0])
        linked = int(conn.execute(
            "SELECT COUNT(*) FROM ticket_market_observations WHERE artist_key IS NOT NULL"
        ).fetchone()[0])
        artists = int(conn.execute(
            "SELECT COUNT(DISTINCT artist_key) FROM ticket_market_observations WHERE artist_key IS NOT NULL"
        ).fetchone()[0])
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
        "ticket_market_fold": {
            "gold_generation": gold_cur.get("generation"),
            "gold_object_key": gold_key,
            "gold_sha256": actual_gold_sha,
            "observation_rows": n,
            "linked_rows": linked,
            "artists_linked": artists,
            "label": "PUBLIC TICKET MARKET",
        },
    })
    row_counts = new_current.get("row_counts") or {}
    if isinstance(row_counts, dict):
        row_counts = dict(row_counts)
        row_counts["ticket_market_observations"] = n
        new_current["row_counts"] = row_counts

    out_current = work / "NEW_CURRENT.json"
    out_current.write_text(json.dumps(new_current, indent=2) + "\n", encoding="utf-8")
    summary = {
        "generation": generation,
        "object_key": new_key,
        "sha256": db_sha,
        "bytes": db_bytes,
        "observation_rows": n,
        "linked_rows": linked,
        "artists_linked": artists,
        "parent": serving_cur.get("generation"),
    }
    print(json.dumps(summary, indent=2))
    if args.dry_run:
        return 0

    _run_wrangler([
        "r2", "object", "put", f"{LAKE}/{new_key}",
        f"--file={db_path}", "--remote",
        "--content-type", "application/octet-stream",
    ])
    _run_wrangler([
        "r2", "object", "put", f"{LAKE}/{SERVING_PREFIX}/CURRENT.json",
        f"--file={out_current}", "--remote",
        "--content-type", "application/json",
    ])
    print(json.dumps({"published": True, **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
