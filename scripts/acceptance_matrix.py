#!/usr/bin/env python3
"""Build comprehensive acceptance matrix from bakeoff results."""

import json, glob, os
from collections import Counter

OUTPUT_DIR = "data/bakeoff"

def safe_coverage(records, key_aliases):
    """Check % of records where at least one alias is non-null."""
    if not records:
        return 0
    count = 0
    for r in records:
        for k in key_aliases:
            val = r.get(k)
            if val is not None and val != "" and val != []:
                count += 1
                break
    return round(count / len(records) * 100, 1)

def has_coords(rec):
    lat = rec.get("lat") or rec.get("latitude") or (rec.get("location") or {}).get("lat")
    lon = rec.get("lon") or rec.get("lng") or rec.get("longitude") or (rec.get("location") or {}).get("lng")
    return lat is not None and lon is not None

def extract_years(records, date_keys):
    """Extract years from date fields for historical depth."""
    years = Counter()
    for r in records:
        for dk in date_keys:
            val = r.get(dk, "")
            if val and isinstance(val, str) and len(val) >= 4:
                try:
                    y = int(val[:4])
                    if 1900 < y < 2100:
                        years[y] += 1
                except ValueError:
                    pass
    return dict(sorted(years.items()))

results = []

for path in sorted(glob.glob(os.path.join(OUTPUT_DIR, "*_raw.json"))):
    if "results" in path:
        continue
    name = os.path.basename(path).replace("_raw.json", "").replace("_", " ")
    with open(path) as f:
        data = json.load(f)
    records = data.get("records", [])
    if not records:
        continue
    
    # Field coverage
    cov = {}
    for key in records[0].keys():
        count = sum(1 for r in records if r.get(key) is not None and r.get(key) != "" and r.get(key) != [])
        cov[key] = round(count / len(records) * 100, 1)
    
    # Key metrics
    n = len(records)
    coord_count = sum(1 for r in records if has_coords(r))
    
    # Date extraction for historical depth
    date_keys = [k for k in records[0].keys() if "date" in k.lower() or "time" in k.lower()]
    years = extract_years(records, date_keys)
    
    # Price fields
    price_keys = [k for k in records[0].keys() if any(w in k.lower() for w in ("price", "cost", "fee", "currency"))]
    has_price = any(price_keys)
    
    # PIT fields
    pit_keys = [k for k in records[0].keys() if any(w in k.lower() for w in ("published", "created", "announced", "onsale", "createdat", "publisheddate"))]
    
    results.append({
        "name": name,
        "records": n,
        "cost": data.get("cost_usd"),
        "field_count": len(records[0].keys()),
        "key_fields": list(records[0].keys()),
        "coords_pct": round(coord_count / n * 100, 1) if n else 0,
        "price_fields": price_keys,
        "pit_fields": pit_keys,
        "years": years,
        "coverage": cov,
    })

# Print matrix
print("=== ACCEPTANCE MATRIX ===\n")
print(f"{'Source':<35} {'Recs':>5} {'Fields':>6} {'Coords%':>8} {'Price':>6} {'PIT':>4} {'Years':>15}")
print("-" * 85)

for r in results:
    years_str = f"{min(r['years'].keys())}-{max(r['years'].keys())}" if r['years'] else "N/A"
    price_str = "YES" if r['price_fields'] else "NO"
    pit_str = "YES" if r['pit_fields'] else "NO"
    print(f"{r['name']:<35} {r['records']:>5} {r['field_count']:>6} {r['coords_pct']:>7}% {price_str:>6} {pit_str:>4} {years_str:>15}")

print(f"\n=== DETAILED FIELD ANALYSIS ===\n")
for r in results:
    print(f"\n--- {r['name']} ({r['records']} records, {r['field_count']} fields) ---")
    # Print field coverage sorted
    for k, v in sorted(r['coverage'].items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}%")
    if r['years']:
        print(f"  Historical depth:")
        for y, c in r['years'].items():
            print(f"    {y}: {c} events")
    if r['price_fields']:
        print(f"  Price fields: {r['price_fields']}")
    if r['pit_fields']:
        print(f"  PIT fields: {r['pit_fields']}")