#!/usr/bin/env python3
"""Rigorous Ticketmaster overlap revalidation.
Same city × same date window comparison. Manual audit sample.
"""

import json, hashlib, sys, duckdb
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SERVING_DB = PROJECT_ROOT / "data" / "serving" / "terminal_prod_20260819_231500_UTC.duckdb"


def _norm(s):
    return " ".join(str(s).lower().strip().split())


def main():
    conn = duckdb.connect(str(SERVING_DB), read_only=True)

    # CHICAGO-ONLY TM events (the valid same-universe)
    tm_chi = conn.execute("""
        SELECT event_name, artist_name, venue_name, local_date, city, state_code
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster' AND city = 'Chicago'
    """).fetchall()
    print(f"TM Chicago events: {len(tm_chi)}")

    # Index by artist|venue|date
    tm_idx = {}
    tm_artists = set()
    tm_venues = set()
    for r in tm_chi:
        artist = _norm(r[1] or r[0] or "")
        venue = _norm(r[2] or "")
        date = str(r[3])[:10] if r[3] else ""
        key = f"{artist}|{venue}|{date}"
        tm_idx[key] = {"event": r[0], "artist": r[1], "venue": r[2], "date": date}
        if artist:
            tm_artists.add(artist)
        if venue:
            tm_venues.add(venue)

    print(f"  Unique artist|venue|date keys: {len(tm_idx)}")
    print(f"  Unique artists: {len(tm_artists)}")
    print(f"  Unique venues: {len(tm_venues)}")

    # Analyze each Chicago-only source
    sources = [
        ("Resident Advisor (Chicago)", "data/bakeoff/ResidentAdvisor_raw.json"),
        ("AllEvents (Chicago)", "data/bakeoff/AllEvents_raw.json"),
        ("Fever (Chicago)", "data/bakeoff/Fever_raw.json"),
        ("Eventbrite (Chicago)", "data/bakeoff/Eventbrite_raw.json"),
    ]

    # Also check Songkick and Bandsintown with the same TM universe
    sources.append(("Songkick (gio21, artist query)", "data/bakeoff/gio21_songkick-events-scraper_raw.json"))
    sources.append(("Bandsintown (artist query)", "data/bakeoff/Bandsintown_raw.json"))

    # DICE — note separate universe
    sources.append(("DICE (London — DIFFERENT UNIVERSE, TM has 0 London)", "data/bakeoff/DICE_raw.json"))

    print(f"\n{'='*90}")
    print(f"{'Source':<45} {'Recs':>5} {'Matched':>8} {'Incr':>6} {'Ambig':>6} {'Incr%':>7} {'Note'}")
    print("-" * 90)

    total_matched, total_incr, total_ambig = 0, 0, 0
    manual_audit = []

    for name, path in sources:
        try:
            with open(path) as f:
                data = json.load(f)
        except FileNotFoundError:
            print(f"{name:<45} {'ERROR':>5}  file not found")
            continue

        records = data.get("records", [])
        if not records:
            print(f"{name:<45} {'0':>5}")
            continue

        matched, incr, ambig = 0, 0, 0

        for rec in records:
            # Extract artist
            a = rec.get("artistName") or rec.get("artist_name") or ""
            if isinstance(a, list):
                a = a[0] if a else ""
            if isinstance(a, dict):
                a = a.get("name", "")
            performers = rec.get("performers") or []
            if isinstance(performers, list) and performers:
                p0 = performers[0]
                a = a or (p0.get("name", "") if isinstance(p0, dict) else str(p0))
            if not a:
                a = rec.get("name") or rec.get("title") or ""

            # Extract venue
            v = rec.get("venueName") or rec.get("venue_name") or rec.get("venue") or ""
            if isinstance(v, dict):
                v = v.get("name", "")

            # Extract date
            d = (rec.get("startDate") or rec.get("date") or rec.get("datetime") or
                 rec.get("startDateLocal") or "")
            if isinstance(d, str) and len(d) >= 10:
                d = d[:10]

            na, nv = _norm(a), _norm(v)
            key = f"{na}|{nv}|{d}"

            rid = rec.get("eventId") or rec.get("id") or rec.get("url") or ""

            if na and nv and d and key in tm_idx:
                matched += 1
                if len(manual_audit) < 25:
                    manual_audit.append(("MATCHED", name, rid, a, v, d, tm_idx[key]))
            elif na and na in tm_artists:
                ambig += 1
                if len(manual_audit) < 50:
                    manual_audit.append(("AMBIGUOUS", name, rid, a, v, d, None))
            elif nv and nv in tm_venues:
                ambig += 1
                if len(manual_audit) < 50:
                    manual_audit.append(("AMBIGUOUS", name, rid, a, v, d, None))
            else:
                incr += 1
                if len(manual_audit) < 75:
                    manual_audit.append(("INCREMENTAL", name, rid, a, v, d, None))

        total_matched += matched
        total_incr += incr
        total_ambig += ambig

        n = max(len(records), 1)
        incr_pct = round(incr / n * 100, 1)
        note = ""
        if "London" in name:
            note = "DIFFERENT UNIVERSE — exclude from aggregate"
        elif "Songkick" in name or "Bandsintown" in name:
            note = "artist query, not city-confined"

        print(f"{name:<45} {len(records):>5} {matched:>8} {incr:>6} {ambig:>6} {incr_pct:>6}%  {note}")

    print("-" * 90)
    print(f"{'TOTAL':<45} {total_matched+total_incr+total_ambig:>5} {total_matched:>8} {total_incr:>6} {total_ambig:>6}")

    # Exclude London for valid aggregate
    valid_m = sum(1 for x in manual_audit if x[0] == "MATCHED" and "London" not in x[1])
    valid_i = sum(1 for x in manual_audit if x[0] == "INCREMENTAL" and "London" not in x[1])
    valid_a = sum(1 for x in manual_audit if x[0] == "AMBIGUOUS" and "London" not in x[1])
    valid_t = valid_m + valid_i + valid_a
    if valid_t > 0:
        print(f"\nSAME-UNIVERSE ONLY (excluding London/DICE):")
        print(f"  Matched: {valid_m} ({round(valid_m/max(valid_t,1)*100, 1)}%)")
        print(f"  Incremental: {valid_i} ({round(valid_i/max(valid_t,1)*100, 1)}%)")
        print(f"  Ambiguous: {valid_a} ({round(valid_a/max(valid_t,1)*100, 1)}%)")

    # Manual audit
    print(f"\n{'='*60}")
    print("MANUAL AUDIT SAMPLE")
    print(f"{'='*60}")
    print(f"\n--- MATCHED (should be genuine same-event matches) ---")
    for i, (cls, src, rid, artist, venue, date, tm_info) in enumerate(manual_audit):
        if cls == "MATCHED" and i < 5:
            print(f"  {src}")
            print(f"    Ext: {artist} @ {venue} on {date}")
            print(f"    TM:  {tm_info['artist']} @ {tm_info['venue']} on {tm_info['date']}")

    print(f"\n--- INCREMENTAL (should be events genuinely not in TM) ---")
    for i, (cls, src, rid, artist, venue, date, _) in enumerate(manual_audit):
        if cls == "INCREMENTAL" and i < 5:
            print(f"  {src}: {artist} @ {venue} on {date}")

    print(f"\n--- AMBIGUOUS (artist or venue in TM, but not exact match) ---")
    for i, (cls, src, rid, artist, venue, date, _) in enumerate(manual_audit):
        if cls == "AMBIGUOUS" and i < 5:
            print(f"  {src}: {artist} @ {venue} on {date}")
            if _norm(artist) in tm_artists:
                print(f"    Artist '{artist}' found in TM but different venue/date")

    conn.close()


if __name__ == "__main__":
    main()