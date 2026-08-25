"""Freeze the REAL_TICKET_MARKET_RAIL_V1 watch universe.

Builds an immutable persisted list of ~100 real upcoming music events from the
Ticketmaster serving estate, 7-90 days out, across six high-activity markets.

Output: data/workspace/watch_universe_v1.json (immutable, versioned).
"""

from __future__ import annotations

import json
import hashlib
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

SERVING_DB = PROJECT_ROOT / "data" / "serving" / "terminal_prod_20260819_231500_UTC.duckdb"
OUT = PROJECT_ROOT / "data" / "workspace" / "watch_universe_v1.json"

UNIVERSE_VERSION = "watch_universe_v1"
TODAY = date(2026, 8, 25)          # current date (frozen for reproducibility)
DAYS_MIN = 7
DAYS_MAX = 90
TARGET_SIZE = 100
MARKETS = ["Los Angeles", "New York", "Chicago", "Las Vegas", "Nashville", "Dallas"]
SEED = 44


def market_key(city: str, state: str) -> str:
    return f"{city.lower().replace(' ', '-')}-{state.lower()}"


def artist_key(name: str) -> str:
    n = (name or "").strip().lower()
    n = "".join(c if c.isalnum() else "-" for c in n)
    while "--" in n:
        n = n.replace("--", "-")
    return f"artist::{n.strip('-')[:60]}" if n else None


def venue_key(venue_id: str) -> str:
    return f"venue::tm:{venue_id}" if venue_id else None


def event_key(platform_object_id: str) -> str:
    return f"event::tm:{platform_object_id}"


def main() -> None:
    if OUT.exists():
        print(f"Universe already exists: {OUT}")
        print("SKIP (immutable — delete to regenerate)")
        return

    conn = duckdb.connect(str(SERVING_DB), read_only=True)

    # Music events 7-90 days out in target markets, with a named artist.
    rows = conn.execute(
        """
        SELECT
            platform_object_id, event_name, artist_name, attractions,
            venue_id, venue_name, city, state_code, country_code,
            latitude, longitude, local_date, local_time, timezone,
            price_min, price_max, price_currency, promoter,
            genre, subgenre, canonical_url, retrieved_at
        FROM events.provider_event_snapshots
        WHERE local_date BETWEEN ? AND ?
          AND city IN (?, ?, ?, ?, ?, ?)
          AND artist_name IS NOT NULL AND artist_name != ''
          AND (segment = 'Music' OR genre = 'Music' OR genre LIKE '%Music%'
               OR subgenre IS NOT NULL)
        ORDER BY local_date
        """,
        [
            str(TODAY + timedelta(days=DAYS_MIN)),
            str(TODAY + timedelta(days=DAYS_MAX)),
            *MARKETS,
        ],
    ).fetchall()

    cols = [
        "platform_object_id", "event_name", "artist_name", "attractions",
        "venue_id", "venue_name", "city", "state_code", "country_code",
        "latitude", "longitude", "local_date", "local_time", "timezone",
        "price_min", "price_max", "price_currency", "promoter",
        "genre", "subgenre", "canonical_url", "retrieved_at",
    ]

    records = [dict(zip(cols, r)) for r in rows]

    # Dedupe: one event per (artist, venue, local_date).
    seen = set()
    unique = []
    for rec in records:
        key = (rec["artist_name"], rec["venue_name"], str(rec["local_date"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)

    print(f"Candidates in window: {len(records)} | deduped: {len(unique)}")

    rng = random.Random(SEED)
    # Balance across markets deterministically.
    by_market: dict[str, list] = {}
    for rec in unique:
        by_market.setdefault(market_key(rec["city"], rec["state_code"]), []).append(rec)

    selected: list[dict] = []
    per_market = TARGET_SIZE // len(MARKETS)
    # Round-robin over markets to keep the window 7-90 days spread.
    for mk in sorted(by_market):
        pool = by_market[mk]
        rng.shuffle(pool)
        selected.extend(pool[:per_market])

    # Top up to target with remaining unique events.
    if len(selected) < TARGET_SIZE:
        chosen = {rec["platform_object_id"] for rec in selected}
        rng.shuffle(unique)
        for rec in unique:
            if len(selected) >= TARGET_SIZE:
                break
            if rec["platform_object_id"] in chosen:
                continue
            chosen.add(rec["platform_object_id"])
            selected.append(rec)

    selected = selected[:TARGET_SIZE]

    frozen_at = datetime.now(timezone.utc).isoformat()
    universe = {
        "watch_universe_version": UNIVERSE_VERSION,
        "frozen_at": frozen_at,
        "reference_date": str(TODAY),
        "date_window": {"min_days": DAYS_MIN, "max_days": DAYS_MAX},
        "target_size": TARGET_SIZE,
        "markets": MARKETS,
        "seed": SEED,
        "source": "events.provider_event_snapshots (ticketmaster serving estate)",
        "events": [],
    }

    for rec in selected:
        universe["events"].append(
            {
                "event_key": event_key(rec["platform_object_id"]),
                "provider_event_id": rec["platform_object_id"],
                "artist_key": artist_key(rec["artist_name"]),
                "artist_name": rec["artist_name"],
                "venue_key": venue_key(rec["venue_id"]),
                "venue_name": rec["venue_name"],
                "venue_id": rec["venue_id"],
                "market_key": market_key(rec["city"], rec["state_code"]),
                "city": rec["city"],
                "state": rec["state_code"],
                "event_date": str(rec["local_date"]),
                "event_time": str(rec["local_time"]) if rec["local_time"] else None,
                "timezone": rec["timezone"],
                "latitude": float(rec["latitude"]) if rec["latitude"] else None,
                "longitude": float(rec["longitude"]) if rec["longitude"] else None,
                "tm_price_min": float(rec["price_min"]) if rec["price_min"] else None,
                "tm_price_max": float(rec["price_max"]) if rec["price_max"] else None,
                "tm_currency": rec["price_currency"],
                "promoter": rec["promoter"],
                "genre": rec["genre"],
                "subgenre": rec["subgenre"],
                "canonical_url": rec["canonical_url"],
                "selection_reason": "ticketmaster-music-upcoming-7to90d",
            }
        )

    # Integrity hash of the immutable content.
    content_hash = hashlib.sha256(
        json.dumps(universe["events"], default=str, sort_keys=True).encode()
    ).hexdigest()[:32]
    universe["content_hash"] = content_hash

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(universe, indent=2, default=str))
    print(f"\nWrote {len(universe['events'])} events to {OUT}")
    print(f"content_hash: {content_hash}")

    # Summary by market
    from collections import Counter
    c = Counter(e["market_key"] for e in universe["events"])
    print("\nBy market:")
    for mk, n in sorted(c.items()):
        print(f"  {mk}: {n}")

    dates = [e["event_date"] for e in universe["events"]]
    print(f"\nDate range: {min(dates)} .. {max(dates)}")
    print(f"Events with TM price: {sum(1 for e in universe['events'] if e['tm_price_min'])}")


if __name__ == "__main__":
    main()
