"""ListenBrainz 25K bulk popularity acquisition.

Uses the official bulk popularity endpoint (POST up to 1000 MBIDs/request)
for all 25,000 ARTIST_SECURITY_25000 members that are MBID-backed. This is
~25 requests total, NOT 25,000 API calls. The 191GB full export is NOT
downloaded for this milestone (justified in the manifest).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import duckdb  # noqa: E402

from festival_bloomberg.migrations import apply_pending_migrations  # noqa: E402
from festival_bloomberg.attention.listenbrainz import (
    collect_artist_popularity,
    fetch_artist_popularity,
)
from festival_bloomberg.acquisition.transport import UrllibTransport


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--limit", type=int, default=None, help="limit MBID-backed artists")
    args = parser.parse_args()

    conn = duckdb.connect(args.warehouse)
    apply_pending_migrations(conn)
    universe = conn.execute(
        """
        SELECT u.artist_key, u.artist_name, u.mbid
        FROM security.artist_security_universe_25000 u
        WHERE u.mbid IS NOT NULL AND u.mbid <> ''
        ORDER BY u.artist_key
        """
    ).fetchall()
    if args.limit:
        universe = universe[:args.limit]
    pairs = [(r[1] or r[0], r[2]) for r in universe]
    key_by_mbid = {r[2]: r[0] for r in universe}
    print(f"artists: {len(pairs)}", flush=True)

    # First pass: raw bulk popularity.
    mbids = [m for _n, m in pairs]
    raw = fetch_artist_popularity(UrllibTransport(), mbids)
    print("bulk popularity:", json.dumps({k: raw[k] for k in ("status", "requests", "artists_eligible") if k in raw}, default=str), "rows:", len(raw.get("rows", [])), flush=True)

    # Persist observations through the existing canonical path.
    summary = collect_artist_popularity(conn, UrllibTransport(), artists=pairs, artist_keys=key_by_mbid)
    print("persisted:", json.dumps(summary, default=str)[:800], flush=True)

    # Record the manifest.
    manifest_key = hashlib.sha256(b"listenbrainz_bulk_popularity|20260827|official_api").hexdigest()[:32]
    conn.execute(
        """
        INSERT INTO security.bulk_source_manifest
            (source_manifest_key, source, source_version, source_url, retrieved_at,
             compressed_bytes, sha256, license, rights_status, commercial_use_status,
             product_use_justification, raw_r2_key, normalized_dataset, row_count, ingested_at)
        VALUES (?, 'listenbrainz_bulk_popularity', '20260827', 'https://api.listenbrainz.org/1/popularity/artist',
                CURRENT_TIMESTAMP, NULL, NULL, 'CC0', 'SOURCE_LICENSE_REVIEWED', 'INTERNAL_ANALYTICS_ONLY',
                'Bulk popularity totals for all 25K securities; full 191GB export deferred to avoid unnecessary download.',
                NULL, 'metrics.artist_attention_observations', ?, CURRENT_TIMESTAMP)
        ON CONFLICT (source, source_version, source_url) DO UPDATE SET row_count = excluded.row_count
        """,
        [manifest_key, summary.get("rows_persisted", 0)],
    )
    conn.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
