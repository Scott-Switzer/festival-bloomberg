#!/usr/bin/env python3
"""Promote the 25K artist security data estate to Cloudflare R2.

Builds a compact JSON artifact from the warehouse and uploads to R2.
Also uploads raw dump artifacts that are already local.

Usage:
  PYTHONPATH=python .venv/bin/python scripts/promote_25k_estate_to_cloud.py \\
    --warehouse /tmp/artist_security_1000.duckdb \\
    --remote
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def r2_put(bucket: str, key: str, path: Path, *, remote: bool) -> bool:
    if not remote:
        print(f"  DRY_RUN: r2 put {bucket}/{key} ({path.stat().st_size / 1048576:.1f}MB)")
        return True
    try:
        subprocess.run(
            ["npx", "wrangler", "r2", "object", "put", f"{bucket}/{key}",
             "--remote", "--file", str(path)],
            check=True, capture_output=True, text=True, timeout=600,
        )
        return True
    except Exception as exc:
        print(f"  R2 FAILED: {exc}")
        return False


def build_estate_artifact(conn) -> dict:
    """Build the 25K data estate artifact."""
    now = datetime.now(timezone.utc).isoformat()

    # Universe
    universe = conn.execute("""
        SELECT artist_key, artist_name, mbid, tier
        FROM security.artist_security_universe_25000
        ORDER BY artist_key
    """).fetchall()

    # Markets per artist
    markets = {}
    for row in conn.execute("""
        SELECT artist_key, market_key, historical_shows
        FROM asm.artist_market_security_v1
    """).fetchall():
        markets.setdefault(row[0], []).append({"market": row[1], "shows": row[2]})

    # ListenBrainz popularity
    lb = {}
    for row in conn.execute("""
        SELECT artist_key, metric_kind, value
        FROM metrics.artist_attention_observations
        WHERE metric_kind LIKE 'LISTENBRAINZ_%' AND status = 'ok'
    """).fetchall():
        lb.setdefault(row[0], {})[row[1]] = row[2]

    # YouTube identities
    yt = {}
    for row in conn.execute("""
        SELECT artist_key, provider_id, resolution_status
        FROM identity.artist_provider_linkages
        WHERE provider = 'YOUTUBE'
    """).fetchall():
        yt.setdefault(row[0], []).append({"channel_id": row[1], "status": row[2]})

    # Event performance counts
    perf_counts = {}
    for row in conn.execute("""
        SELECT a.artist_key, COUNT(*) as cnt
        FROM core.event_performers ep
        JOIN core.artists a ON a.musicbrainz_id = ep.artist_mbid
        GROUP BY a.artist_key
    """).fetchall():
        perf_counts[row[0]] = row[1]

    # Festival appearances
    fest_counts = {}
    for row in conn.execute("""
        SELECT a.artist_key, COUNT(DISTINCT se.series_key) as cnt
        FROM core.event_performers ep
        JOIN core.artists a ON a.musicbrainz_id = ep.artist_mbid
        JOIN core.series_events se ON se.event_mbid = ep.event_mbid
        JOIN core.event_series es ON es.series_key = se.series_key
        WHERE es.series_type = 'FESTIVAL'
        GROUP BY a.artist_key
    """).fetchall():
        fest_counts[row[0]] = row[1]

    # Venue counts
    venue_counts = {}
    for row in conn.execute("""
        SELECT a.artist_key, COUNT(DISTINCT p.object_key) as cnt
        FROM core.event_performers ep
        JOIN core.artists a ON a.musicbrainz_id = ep.artist_mbid
        JOIN core.entity_relationships p
          ON p.subject_entity_type = 'EVENT'
          AND p.subject_key = 'mbid::' || ep.event_mbid
          AND p.predicate = 'EVENT_AT_PLACE'
        GROUP BY a.artist_key
    """).fetchall():
        venue_counts[row[0]] = row[1]

    # Build compact artist records
    artists = []
    for artist_key, artist_name, mbid, tier in universe:
        artist = {
            "key": artist_key,
            "name": artist_name,
            "mbid": mbid,
            "tier": tier,
            "markets": markets.get(artist_key, []),
            "event_performances": perf_counts.get(artist_key, 0),
            "festival_appearances": fest_counts.get(artist_key, 0),
            "venues_played": venue_counts.get(artist_key, 0),
        }
        if artist_key in lb:
            artist["listenbrainz"] = lb[artist_key]
        if artist_key in yt:
            artist["youtube"] = yt[artist_key]
        artists.append(artist)

    # Summary stats
    tiers = {}
    for a in artists:
        t = a["tier"]
        tiers.setdefault(t, {"count": 0, "with_markets": 0, "with_youtube": 0,
                             "with_listenbrainz": 0, "total_events": 0})
        tiers[t]["count"] += 1
        if a["markets"]:
            tiers[t]["with_markets"] += 1
        if a.get("youtube"):
            tiers[t]["with_youtube"] += 1
        if a.get("listenbrainz"):
            tiers[t]["with_listenbrainz"] += 1
        tiers[t]["total_events"] += a["event_performances"]

    return {
        "version": "artist_security_25000_v1",
        "created_at": now,
        "universe_size": len(artists),
        "tiers": tiers,
        "artists": artists,
        "counts": {
            "universe_size": len(artists),
            "total_markets": sum(len(a["markets"]) for a in artists),
            "artists_with_markets": sum(1 for a in artists if a["markets"]),
            "artists_with_youtube": sum(1 for a in artists if a.get("youtube")),
            "artists_with_listenbrainz": sum(1 for a in artists if a.get("listenbrainz")),
            "total_event_performances": sum(a["event_performances"] for a in artists),
            "total_festival_appearances": sum(a["festival_appearances"] for a in artists),
            "total_venues_played": sum(a["venues_played"] for a in artists),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote 25K estate to Cloudflare R2")
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--remote", action="store_true", help="Actually write to R2")
    parser.add_argument("--dumps-dir", default="/tmp/mb_dumps_25k")
    args = parser.parse_args()

    conn = duckdb.connect(args.warehouse, read_only=True)
    t0 = time.time()

    # Build artifact
    print("Building estate artifact...", flush=True)
    artifact = build_estate_artifact(conn)
    conn.close()

    # Write local
    out_dir = Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / "artist_security_25000_estate_v1.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    artifact_size = artifact_path.stat().st_size
    print(f"Artifact: {artifact_path} ({artifact_size / 1048576:.1f}MB)")
    print(f"Counts: {json.dumps(artifact['counts'], indent=2)}")

    digest = sha256(artifact_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    r2_key = f"control/artist_security_25000/v1/estate_{stamp}_{digest[:12]}.json"

    # Upload artifact
    print(f"\nUploading estate artifact to R2...", flush=True)
    ok = r2_put("festival-intelligence-backups", r2_key, artifact_path, remote=args.remote)
    if ok:
        print(f"  Uploaded: {r2_key}")

    # Update pointer
    pointer = {
        "version": "v1",
        "source": r2_key,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": digest,
        "universe_size": artifact["counts"]["universe_size"],
        "total_markets": artifact["counts"]["total_markets"],
        "artists_with_markets": artifact["counts"]["artists_with_markets"],
        "artists_with_youtube": artifact["counts"]["artists_with_youtube"],
    }
    pointer_path = Path("/tmp/estate_pointer.json")
    pointer_path.write_text(json.dumps(pointer, indent=2))
    r2_put("festival-intelligence-backups", "control/artist_security_25000/current.json",
           pointer_path, remote=args.remote)
    pointer_path.unlink(missing_ok=True)

    # Upload raw dump artifacts
    dumps_dir = Path(args.dumps_dir)
    for entity in ["area", "event", "place", "series", "recording"]:
        archive = dumps_dir / f"{entity}.tar.xz"
        if archive.exists() and archive.stat().st_size > 0:
            dump_key = f"bulk/musicbrainz/20260826-001001/{entity}.tar.xz"
            print(f"Uploading {entity}.tar.xz ({archive.stat().st_size / 1048576:.1f}MB)...", flush=True)
            r2_put("festival-intelligence-raw", dump_key, archive, remote=args.remote)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    if not args.remote:
        print("DRY RUN — no R2 writes performed. Use --remote to promote.")


if __name__ == "__main__":
    main()
