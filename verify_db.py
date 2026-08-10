#!/usr/bin/env python3
"""
Verify DuckDB database contents after ingestion.
"""
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import duckdb

db_path = 'data/warehouse/festival_bloomberg.duckdb'

print(f"Verifying database at {db_path}")
print("=" * 60)

conn = duckdb.connect(db_path)

# Check core tables
tables = [
    ('core.artists', 'Artists'),
    ('core.festivals', 'Festivals'),
    ('core.festival_editions', 'Festival Editions'),
    ('core.lineup_slots', 'Lineup Slots'),
    ('raw.lineup_observations', 'Lineup Observations'),
    ('core.artist_contacts', 'Artist Contacts'),
    ('core.lineup_qualification_metrics', 'Lineup Qualification Metrics'),
]

for table, name in tables:
    try:
        result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        count = result[0]
        print(f"{name}: {count} records")
    except Exception as e:
        print(f"{Name}: ERROR - {str(e)}")

print("=" * 60)

# Sample data
print("\nSample artists:")
try:
    result = conn.execute("SELECT artist_key, name, normalized_name FROM core.artists LIMIT 5").fetchall()
    for row in result:
        print(f"  {row[1]} ({row[0]})")
except Exception as e:
    print(f"  ERROR: {str(e)}")

print("\nSample festivals:")
try:
    result = conn.execute("SELECT festival_key, name FROM core.festivals LIMIT 5").fetchall()
    for row in result:
        print(f"  {row[1]} ({row[0]})")
except Exception as e:
    print(f"  ERROR: {str(e)}")

print("\nSample lineup slots:")
try:
    result = conn.execute("SELECT slot_key, artist_name, festival_key FROM core.lineup_slots LIMIT 5").fetchall()
    for row in result:
        print(f"  {row[1]} at {row[2]} ({row[0]})")
except Exception as e:
    print(f"  ERROR: {str(e)}")

conn.close()
