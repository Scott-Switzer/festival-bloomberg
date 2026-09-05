#!/usr/bin/env python3
"""Publish gold/ticket_market_observations from the longitudinal cohort tape.

Builds an immutable parquet + CURRENT pointer from:
  - data/workspace/ticket_market/ticket_market.duckdb snapshots
  - data/workspace/ticket_market/cloud_collect_activation.jsonl (price_basis)

Does NOT invent prices. Missing offer_min / all_in stays NULL.
Does NOT use tickets.dev.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "workspace" / "ticket_market" / "ticket_market.duckdb"
DEFAULT_JSONL = PROJECT_ROOT / "data" / "workspace" / "ticket_market" / "cloud_collect_activation.jsonl"
DEFAULT_COHORT = PROJECT_ROOT / "data" / "workspace" / "ticket_market_cohort_v2.json"
GOLD_PREFIX = "gold/ticket_market_observations"
LAKE_BUCKET = "festival-intelligence-lake"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_jsonl(conn: duckdb.DuckDBPyConnection, jsonl: Path) -> dict:
    """Merge FETCHED_AND_STORED cloud observations into ticket_market_snapshots."""
    from festival_bloomberg.evidence_rails.collection_ledger import ingest_cloud_observation

    if not jsonl.exists():
        return {"lines": 0, "ingested": 0, "skipped": 0}
    ingested = skipped = 0
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") != "FETCHED_AND_STORED":
            skipped += 1
            continue
        obs = {
            "event_key": row.get("event_key"),
            "source_platform": row.get("marketplace"),
            "marketplace": row.get("marketplace"),
            "observed_at": row.get("fetched_at"),
            "retrieved_at": row.get("fetched_at"),
            "knowledge_time": row.get("fetched_at"),
            "raw_payload_hash": row.get("content_hash"),
            "source_url": None,
            "observed_offer_min_price": row.get("offer_min"),
            "resale_min_price": row.get("offer_min"),
            "all_in_price": row.get("offer_min") if row.get("price_basis") else None,
            "actor_or_endpoint": f"monid_fast|{row.get('price_basis') or 'UNKNOWN'}",
            "parser_version": "cloud_collect_activation_v1",
            "provider_event_id": (row.get("event_key") or "").split(":")[-1] or None,
            "source_record_id": row.get("content_hash"),
        }
        # Only set all_in when price_basis indicates an observed offer — never coerce missing→0.
        if row.get("offer_min") is None:
            obs["all_in_price"] = None
            obs["resale_min_price"] = None
            obs["observed_offer_min_price"] = None
        ingest_cloud_observation(conn, obs, wave_label="activation_jsonl")
        ingested += 1
    return {"lines": ingested + skipped, "ingested": ingested, "skipped": skipped}


def build_gold_parquet(
    *,
    db: Path,
    jsonl: Path,
    cohort_path: Path,
    out_parquet: Path,
) -> dict:
    conn = duckdb.connect(str(db))
    try:
        ingest_stats = ingest_jsonl(conn, jsonl)
        conn.execute("CHECKPOINT")

        price_basis_rows: dict[tuple[str, str, str], str] = {}
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("status") != "FETCHED_AND_STORED":
                    continue
                key = (
                    str(row.get("event_key") or ""),
                    str(row.get("marketplace") or ""),
                    str(row.get("content_hash") or ""),
                )
                price_basis_rows[key] = str(row.get("price_basis") or "UNKNOWN")

        # Stage price_basis lookup
        conn.execute("CREATE OR REPLACE TEMP TABLE _pb (event_key VARCHAR, marketplace VARCHAR, content_hash VARCHAR, price_basis VARCHAR)")
        if price_basis_rows:
            conn.executemany(
                "INSERT INTO _pb VALUES (?, ?, ?, ?)",
                [(a, b, c, d) for (a, b, c), d in price_basis_rows.items()],
            )

        cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
        pairs = cohort.get("pairs") or cohort.get("events") or []
        conn.execute(
            """
            CREATE OR REPLACE TEMP TABLE _cohort (
              event_key VARCHAR, marketplace VARCHAR, provider_event_id VARCHAR,
              artist_name VARCHAR, venue_name VARCHAR, city VARCHAR, market_key VARCHAR,
              event_date VARCHAR, marketplace_event_url VARCHAR
            )
            """
        )
        cohort_rows = []
        for p in pairs:
            cohort_rows.append((
                p.get("event_key"),
                p.get("marketplace"),
                p.get("provider_event_id"),
                p.get("artist_name"),
                p.get("venue_name"),
                p.get("city"),
                p.get("market_key"),
                str(p.get("event_date") or "")[:10] or None,
                p.get("marketplace_event_url") or p.get("canonical_url"),
            ))
        if cohort_rows:
            conn.executemany("INSERT INTO _cohort VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", cohort_rows)

        out_parquet.parent.mkdir(parents=True, exist_ok=True)
        conn.execute(
            f"""
            COPY (
              SELECT
                s.snapshot_id AS observation_key,
                s.event_key,
                COALESCE(c.provider_event_id, s.provider_event_id) AS provider_event_id,
                c.artist_name,
                s.source_platform AS marketplace,
                c.venue_name,
                c.city,
                c.market_key,
                c.event_date,
                c.marketplace_event_url AS source_url,
                s.observed_at,
                s.retrieved_at,
                s.knowledge_time,
                s.currency,
                s.face_value,
                s.all_in_price,
                s.resale_min_price,
                s.resale_median_price,
                s.resale_max_price,
                s.listing_count,
                COALESCE(pb.price_basis,
                  CASE
                    WHEN s.actor_or_endpoint LIKE 'monid_fast|%' THEN regexp_extract(s.actor_or_endpoint, 'monid_fast\\\\|(.+)', 1)
                    WHEN s.all_in_price IS NULL AND s.resale_min_price IS NULL AND s.face_value IS NULL THEN 'NOT_EXPOSED'
                    ELSE 'UNKNOWN'
                  END
                ) AS price_basis,
                CASE
                  WHEN s.all_in_price IS NOT NULL OR s.resale_min_price IS NOT NULL OR s.face_value IS NOT NULL THEN 'OBSERVED'
                  WHEN COALESCE(pb.price_basis, '') IN ('NOT_EXPOSED', 'NONE', 'NOT_SUPPORTED') THEN COALESCE(pb.price_basis, 'NOT_EXPOSED')
                  WHEN s.all_in_price IS NULL AND s.resale_min_price IS NULL THEN 'NOT_EXPOSED'
                  ELSE 'UNKNOWN'
                END AS evidence_status,
                s.raw_payload_hash AS evidence_ref,
                s.raw_payload_hash,
                s.rights_status,
                s.commercial_use_status,
                s.identity_match_status,
                s.parser_version,
                s.wave_label,
                'TICKET_MARKET_COHORT_V2_20260905' AS cohort_version
              FROM acquisition.ticket_market_snapshots s
              LEFT JOIN _cohort c
                ON c.event_key = s.event_key AND c.marketplace = s.source_platform
              LEFT JOIN _pb pb
                ON pb.event_key = s.event_key
               AND pb.marketplace = s.source_platform
               AND pb.content_hash = s.raw_payload_hash
              ORDER BY s.event_key, s.source_platform, s.observed_at
            ) TO '{out_parquet.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        n = int(conn.execute(
            "SELECT COUNT(*) FROM read_parquet(?)", [str(out_parquet)]
        ).fetchone()[0])
        covered = int(conn.execute(
            """
            SELECT COUNT(DISTINCT event_key || '|' || marketplace)
            FROM read_parquet(?)
            """,
            [str(out_parquet)],
        ).fetchone()[0])
        with_price = int(conn.execute(
            """
            SELECT COUNT(*) FROM read_parquet(?)
            WHERE all_in_price IS NOT NULL OR resale_min_price IS NOT NULL OR face_value IS NOT NULL
            """,
            [str(out_parquet)],
        ).fetchone()[0])
    finally:
        conn.close()

    return {
        "ingest": ingest_stats,
        "rows": n,
        "distinct_pairs": covered,
        "rows_with_price": with_price,
        "sha256": _sha256(out_parquet),
        "bytes": out_parquet.stat().st_size,
    }


def upload_gold(parquet: Path, generation: str, meta: dict, *, dry_run: bool) -> dict:
    object_key = f"{GOLD_PREFIX}/generations/{generation}/ticket_market_observations.parquet"
    current = {
        "artifact": "ticket_market_observations",
        "generation": generation,
        "object_key": object_key,
        "sha256": meta["sha256"],
        "bytes": meta["bytes"],
        "rows": meta["rows"],
        "distinct_pairs": meta["distinct_pairs"],
        "rows_with_price": meta["rows_with_price"],
        "cohort_version": "TICKET_MARKET_COHORT_V2_20260905",
        "created_at": datetime.now(UTC).isoformat(),
        "semantics": {
            "label": "PUBLIC TICKET MARKET",
            "not": ["TICKET DEMAND", "SALES", "SELL_THROUGH", "ATTENDANCE"],
            "missing_price": "NULL stays NULL; NEVER encode as 0",
            "listing_disappearance": "LISTING_NO_LONGER_OBSERVED",
        },
    }
    current_path = parquet.parent / "CURRENT.json"
    current_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    if dry_run:
        return {"dry_run": True, "current": current}
    # Prefer wrangler (OAuth) — local FI_R2_* keys have been SignatureDoesNotMatch.
    for local, remote in (
        (parquet, object_key),
        (current_path, f"{GOLD_PREFIX}/CURRENT.json"),
    ):
        cmd = [
            "npx", "wrangler", "r2", "object", "put",
            f"{LAKE_BUCKET}/{remote}",
            f"--file={local}",
            "--remote",
            "--content-type",
            "application/json" if remote.endswith(".json") else "application/octet-stream",
        ]
        subprocess.run(cmd, cwd=str(PROJECT_ROOT / "cloud-runtime"), check=True)
    return current


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    ap.add_argument("--cohort", type=Path, default=DEFAULT_COHORT)
    ap.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "data" / "workspace" / "ticket_market" / "gold")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT / "python"))
    generation = f"ticket_market_obs_{_now()}"
    out_parquet = args.out_dir / generation / "ticket_market_observations.parquet"
    meta = build_gold_parquet(
        db=args.db, jsonl=args.jsonl, cohort_path=args.cohort, out_parquet=out_parquet,
    )
    print(json.dumps({"generation": generation, **meta}, indent=2))
    if args.no_upload:
        return 0
    current = upload_gold(out_parquet, generation, meta, dry_run=args.dry_run)
    print(json.dumps(current, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
