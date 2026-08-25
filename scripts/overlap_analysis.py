#!/usr/bin/env python3
"""PR #44 — Ticketmaster overlap and information-lift analysis.

For every executed bakeoff source, resolve scraped records against the
serving-snapshot Ticketmaster estate (events.provider_event_snapshots,
17,031 rows). Classify each record: MATCHED, INCREMENTAL, AMBIGUOUS.
"""

import hashlib, json, sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

SERVING_DB = PROJECT_ROOT / "data" / "serving" / "terminal_prod_20260819_231500_UTC.duckdb"
BAKEOFF_DIR = PROJECT_ROOT / "data" / "bakeoff"


def _norm(s: str) -> str:
    return " ".join(s.lower().strip().split())


def load_tm_estate(conn):
    """Load Ticketmaster events from provider_event_snapshots."""
    rows = conn.execute("""
        SELECT platform_object_id, event_name, artist_name, venue_name,
               local_date, city, state_code, latitude, longitude,
               price_min, price_max, price_currency, genre, subgenre,
               event_status, onsale_start, promoter
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
    """).fetchall()

    index = {}
    artists = set()
    venues = set()

    for r in rows:
        artist = _norm(r[2] or r[1] or "")
        venue = _norm(r[3] or "")
        date = str(r[4])[:10] if r[4] else ""
        key = artist + "|" + venue + "|" + date

        if key not in index:
            index[key] = {
                "event_id": r[0], "event_name": r[1], "artist": r[1] or r[2],
                "venue": r[3], "date": date, "city": r[5], "state": r[6],
                "lat": r[7], "lon": r[8], "price_min": r[9], "price_max": r[10],
                "currency": r[11], "genre": r[12], "status": r[14], "onsale": r[15],
            }
        if artist:
            artists.add(artist)
        if venue:
            venues.add(venue)

    return index, artists, venues


def extract_identity(rec):
    """Extract artist, venue, date from any baked record."""
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

    v = rec.get("venueName") or rec.get("venue_name") or rec.get("venue") or ""
    if isinstance(v, dict):
        v = v.get("name", "")

    d = (rec.get("startDate") or rec.get("date") or rec.get("datetime") or
         rec.get("startDateLocal") or "")
    if isinstance(d, str) and len(d) >= 10:
        d = d[:10]

    return _norm(a), _norm(v), d


def analyze_source(name, records, tm_index, tm_artists, tm_venues, cost):
    """Classify every record against Ticketmaster."""
    r = {"source": name, "total": len(records), "cost": cost or 0,
         "matched": 0, "incremental": 0, "ambiguous": 0,
         "coverage": {}, "has_price": False, "has_coords": False,
         "has_pit": False, "has_promoter": False}

    if not records:
        r["cost_per_rec"] = 0.0
        r["cost_per_incr"] = None
        return r

    keys = set(records[0].keys())
    kl = [k.lower() for k in keys]
    r["has_price"] = any(w in " ".join(kl) for w in ("price", "cost", "fee"))
    r["has_coords"] = any(w in " ".join(kl) for w in ("lat", "lon", "latitude", "longitude", "coord"))
    r["has_pit"] = any(w in " ".join(kl) for w in ("published", "announced", "announcement", "createdat"))
    r["has_promoter"] = any(w in " ".join(kl) for w in ("promoter", "organizer", "presented"))

    for rec in records:
        art, ven, date = extract_identity(rec)
        key = art + "|" + ven + "|" + date

        if art and ven and date and key in tm_index:
            r["matched"] += 1
        elif art and art in tm_artists:
            r["ambiguous"] += 1
        elif ven and ven in tm_venues:
            r["ambiguous"] += 1
        else:
            r["incremental"] += 1

    n = max(len(records), 1)
    r["cost_per_rec"] = round(r["cost"] / n, 6)
    r["cost_per_incr"] = round(r["cost"] / r["incremental"], 6) if r["incremental"] else None
    return r


def historical_depth(records, name):
    """Extract years from date fields for historical depth analysis."""
    date_keys = [k for k in records[0].keys() 
                 if "date" in k.lower() or "time" in k.lower()] if records else []
    years = Counter()
    for rec in records:
        for dk in date_keys:
            val = rec.get(dk, "")
            if val and isinstance(val, str) and len(val) >= 4:
                try:
                    y = int(val[:4])
                    if 1900 < y < 2100:
                        years[y] += 1
                except ValueError:
                    pass
    return years


def main():
    if not SERVING_DB.exists():
        print("No serving DB — overlap test requires TM snapshot.\nSKIP (expected for CI).")
        return

    conn = duckdb.connect(str(SERVING_DB), read_only=True)
    tm_index, tm_artists, tm_venues = load_tm_estate(conn)
    conn.close()

    print(f"TM estate: {len(tm_index):,} unique events, {len(tm_artists):,} artists, {len(tm_venues):,} venues\n")

    # Analyze each source
    results = []
    total_cost = 0
    for path in sorted(BAKEOFF_DIR.glob("*_raw.json")):
        name = path.stem.replace("_raw", "").replace("~", "/").replace("_", " ")
        with open(path) as f:
            data = json.load(f)
        records = data.get("records", [])
        cost = data.get("cost_usd")
        if cost:
            total_cost += float(cost) if isinstance(cost, (int, float)) else 0

        r = analyze_source(name, records, tm_index, tm_artists, tm_venues, cost)
        if records:
            r["depth"] = historical_depth(records, name)
        results.append(r)

    # Print matrix
    print(f"{'Source':<42} {'Recs':>5} {'Matched':>8} {'Incr':>6} {'Ambig':>6} {'Prc':>4} {'Coords':>6} {'PIT':>4} {'Prom':>4} {'$/Rec':>8}")
    print("-" * 100)

    tot_r, tot_m, tot_i, tot_a = 0, 0, 0, 0
    for r in results:
        tot_r += r["total"]; tot_m += r["matched"]; tot_i += r["incremental"]; tot_a += r["ambiguous"]
        prc = "Y" if r["has_price"] else "N"
        crd = "Y" if r["has_coords"] else "N"
        pit = "Y" if r["has_pit"] else "N"
        prom = "Y" if r["has_promoter"] else "N"
        cpr = f"${r['cost_per_rec']:.6f}"
        print(f"{r['source']:<42} {r['total']:>5} {r['matched']:>8} {r['incremental']:>6} {r['ambiguous']:>6} {prc:>4} {crd:>6} {pit:>4} {prom:>4} {cpr:>8}")

    print("-" * 100)
    print(f"{'TOTAL':<42} {tot_r:>5} {tot_m:>8} {tot_i:>6} {tot_a:>6}")
    print(f"\nTotal bakeoff cost: ${total_cost:.6f}")

    # Incremental lift summary
    print(f"\n{'='*60}")
    print("INCREMENTAL EVENT LIFT (external events NOT in Ticketmaster)")
    print(f"{'='*60}")
    for r in sorted(results, key=lambda x: -x["incremental"]):
        if r["incremental"] > 0:
            print(f"  {r['source']}: {r['incremental']} incremental ({round(r['incremental']/max(r['total'],1)*100,1)}%)")

    # Historical depth
    print(f"\n{'='*60}")
    print("HISTORICAL DEPTH (events per year)")
    print(f"{'='*60}")
    for r in results:
        if r.get("depth") and r["total"] > 0:
            years = dict(sorted(r["depth"].items()))
            yrs = f"{min(years.keys())}-{max(years.keys())}" if years else "N/A"
            counts = ", ".join(f"{y}:{c}" for y, c in years.items())
            print(f"  {r['source']}: {yrs} — {counts}")

    # Rich source analysis
    print(f"\n{'='*60}")
    print("TOP SOURCES BY BUYER VALUE")
    print(f"{'='*60}")
    scored = []
    for r in results:
        if r["total"] == 0:
            continue
        score = 0
        if r["has_price"]: score += 3
        if r["has_coords"]: score += 3
        if r["has_pit"]: score += 3
        if r["has_promoter"]: score += 2
        if r["incremental"] > 0: score += r["incremental"]
        scored.append((r, score))
    for r, s in sorted(scored, key=lambda x: -x[1]):
        print(f"  Score {s:>3}: {r['source']} (records={r['total']}, incr={r['incremental']})")


if __name__ == "__main__":
    main()