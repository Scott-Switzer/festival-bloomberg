"""Export the provider-native identity estate to the R2 control plane.

Reads future events with full identity (artist + date + venue + city) and a
canonical provider URL from the lake's provider_event_snapshots parquet, then
writes a control-plane estate file that the Cloudflare mapping factory
(cloud-runtime/src/mapping-factory-v2.ts) reads for SOURCE 1 — provider-ID
promotion (EXACT_PROVIDER_ID, zero scraper cost).

Output (uploaded with rclone):
  festival-intelligence-backups:control/event_estate/identity_estate_v1.json

The estate is a CONTROL artifact (mutable, deliberately updated). Historical
frozen universes remain immutable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

ESTATE_VERSION = "identity_estate_v1"
PARQUET_LOCAL = (
    PROJECT_ROOT
    / "data"
    / "workspace"
    / "provider_event_snapshots.parquet"
)
R2_REMOTE = "r2:festival-intelligence-backups/control/event_estate/identity_estate_v1.json"
TODAY = date(2026, 8, 26)
MAX_EVENTS = 20000  # bounded estate export; expand deliberately


def main() -> None:
    conn = duckdb.connect()

    if not PARQUET_LOCAL.exists():
        # Fetch the lake parquet via rclone (bounded, explicit object).
        subprocess.run(
            [
                "rclone", "copyto",
                "r2:festival-intelligence-lake/events/provider_event_snapshots/provider_event_snapshots.parquet",
                str(PARQUET_LOCAL),
                "--quiet",
            ],
            check=True,
        )

    rows = conn.execute(
        """
        SELECT DISTINCT
            platform_object_id, artist_name, venue_id, venue_name, city,
            state_code, local_date, local_time, timezone, promoter,
            genre, subgenre, canonical_url
        FROM read_parquet(?) pe
        WHERE pe.local_date >= ?
          AND pe.artist_name IS NOT NULL AND pe.artist_name != ''
          AND pe.venue_name IS NOT NULL AND pe.venue_name != ''
          AND pe.city IS NOT NULL AND pe.city != ''
          AND pe.canonical_url IS NOT NULL AND pe.canonical_url != ''
          AND pe.event_status = 'onsale'
        ORDER BY pe.local_date
        LIMIT ?
        """,
        [str(PARQUET_LOCAL), str(TODAY), MAX_EVENTS],
    ).fetchall()

    cols = [
        "platform_object_id", "artist_name", "venue_id", "venue_name", "city",
        "state_code", "local_date", "local_time", "timezone", "promoter",
        "genre", "subgenre", "canonical_url",
    ]

    events = []
    for r in rows:
        rec = dict(zip(cols, r))
        events.append(
            {
                "event_key": f"event::tm:{rec['platform_object_id']}",
                "provider_event_id": rec["platform_object_id"],
                "artist_name": rec["artist_name"],
                "venue_id": rec["venue_id"],
                "venue_name": rec["venue_name"],
                "city": rec["city"],
                "state_code": rec["state_code"],
                "event_date": str(rec["local_date"]),
                "event_time": str(rec["local_time"]) if rec["local_time"] else None,
                "timezone": rec["timezone"],
                "promoter": rec["promoter"],
                "genre": rec["genre"],
                "subgenre": rec["subgenre"],
                "canonical_url": rec["canonical_url"],
            }
        )

    doc = {
        "estate_version": ESTATE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "reference_date": str(TODAY),
        "source": str(PARQUET_LOCAL),
        "count": len(events),
        "events": events,
    }

    tmp = PROJECT_ROOT / "data" / "workspace" / "identity_estate_v1.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(doc, indent=1, default=str))

    # Upload with rclone (bound, explicit — only this one object).
    cmd = ["rclone", "copyto", str(tmp), R2_REMOTE, "--quiet"]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)

    print(f"\nUploaded {len(events)} provider-native events to {R2_REMOTE}")
    print(f"Local copy: {tmp}")
    from collections import Counter

    mps = Counter()
    for e in events:
        url = e["canonical_url"] or ""
        if "ticketweb.com" in url:
            mps["ticketweb.com"] += 1
        elif "axs.com" in url:
            mps["axs.com"] += 1
        else:
            mps["ticketmaster.com"] += 1
    print("Marketplace split:", dict(mps))


if __name__ == "__main__":
    main()
