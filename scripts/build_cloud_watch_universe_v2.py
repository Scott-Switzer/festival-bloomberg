"""Build the immutable cloud watch-universe v2 artifact locally.

This script only writes a local JSON artifact. Uploading the artifact and
advancing the R2 pointer are explicit deployment/acceptance actions.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def build(conn: duckdb.DuckDBPyConnection) -> dict:
    rows = conn.execute("""
        SELECT DISTINCT
          p.event_key,
          p.artist_key,
          s.artist_name,
          s.local_date,
          s.venue_name,
          s.city,
          p.marketplace,
          COALESCE(p.canonical_url, s.canonical_url),
          'EXACT_PROVIDER_ID'
        FROM acquisition.market_price_observations p
        LEFT JOIN events.provider_event_snapshots s
          ON s.platform_object_id = p.provider_event_id
        WHERE p.event_key IS NOT NULL
    """).fetchall()
    events = []
    now = datetime.now(timezone.utc).date()
    for row in rows:
        event_key, artist_key, artist_name, event_date, venue_name, city, marketplace, url, status = row
        event_day = event_date if hasattr(event_date, "year") else datetime.fromisoformat(str(event_date)).date() if event_date else None
        days = (event_day - now).days if event_day else 9999
        tier = "HOT_EVENTS" if days <= 30 else "ACTIVE_EVENTS" if days <= 120 else "LONG_HORIZON_EVENTS"
        events.append({
            "event_key": event_key,
            "artist_key": artist_key,
            "artist_name": artist_name,
            "event_date": str(event_date) if event_date else None,
            "venue_name": venue_name,
            "city": city,
            "marketplace": marketplace or "ticketmaster.com",
            "provider_event_id": None,
            "marketplace_event_url": url,
            "mapping_status": status or "EXACT_PROVIDER_ID",
            "acquisition_tier": tier,
            "evidence_basis": "verified_liquidity_cohort",
        })
    channels = conn.execute("""
        WITH attention_counts AS (
          SELECT artist_key, COUNT(*) AS n FROM metrics.artist_attention_observations WHERE status = 'ok' GROUP BY artist_key
        ), ids AS (
          SELECT entity_key, COUNT(*) AS n FROM core.entity_external_ids WHERE entity_type = 'artist' GROUP BY entity_key
        ), perf AS (
          SELECT artist_mbid, COUNT(*) AS n FROM core.event_performers WHERE artist_mbid IS NOT NULL GROUP BY artist_mbid
        ), selected AS (
          SELECT a.artist_key
          FROM core.artists a
          LEFT JOIN attention_counts ac ON ac.artist_key = a.artist_key
          LEFT JOIN ids i ON i.entity_key = a.artist_key
          LEFT JOIN perf p ON p.artist_mbid = a.musicbrainz_id
          WHERE a.artist_key IS NOT NULL
          ORDER BY (COALESCE(p.n, 0) > 0) DESC, (COALESCE(i.n, 0) + COALESCE(ac.n, 0)) DESC, a.artist_key
          LIMIT 1000
        )
        SELECT DISTINCT e.entity_key, e.id_value
        FROM core.entity_external_ids e JOIN selected s ON s.artist_key = e.entity_key
        WHERE e.entity_type = 'artist' AND e.id_type = 'youtube'
        ORDER BY e.entity_key
    """).fetchall()
    # One deterministic provider identity per artist for the high-frequency tape.
    # Existing duplicate/stale IDs remain available for separate repair workflows.
    by_artist = {}
    for artist, channel in channels:
        by_artist.setdefault(artist, channel)
    youtube = [{"artist_key": artist, "youtube_channel_id": channel, "hot": i < 250}
               for i, (artist, channel) in enumerate(sorted(by_artist.items()))]
    return {
        "version": "watch_universe_v2",
        "schema_version": "cloud_watch_universe_v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "accepted_event_identity_and_market_liquidity_evidence",
        "events": events,
        "youtube_channels": youtube,
        "counts": {
            "universe_size": len(events),
            "hot_events": sum(e["acquisition_tier"] == "HOT_EVENTS" for e in events),
            "active_events": sum(e["acquisition_tier"] == "ACTIVE_EVENTS" for e in events),
            "long_horizon_events": sum(e["acquisition_tier"] == "LONG_HORIZON_EVENTS" for e in events),
            "active_paid_acquisition_size": 0,
            "youtube_channels": len(youtube),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="/tmp/artist_security_1000.duckdb")
    parser.add_argument("--out", default="reports/watch_universe_v2.json")
    args = parser.parse_args()
    conn = duckdb.connect(args.warehouse, read_only=True)
    artifact = build(conn)
    conn.close()
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(artifact["counts"], indent=2))


if __name__ == "__main__":
    main()
