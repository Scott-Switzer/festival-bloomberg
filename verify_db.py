#!/usr/bin/env python3
from warehouse.repository import FestivalRepository

repo = FestivalRepository('data/warehouse/festival_bloomberg.duckdb')

# Check core.artists
print('=== core.artists ===')
artists = repo.conn.execute('SELECT COUNT(*) FROM core.artists').fetchone()
print(f'Total artists: {artists[0]}')

sample_artist = repo.conn.execute('SELECT artist_key, name, normalized_name, musicbrainz_id, spotify_id FROM core.artists LIMIT 3').fetchall()
print('Sample artists:')
for row in sample_artist:
    print(f'  {row}')

# Check core.lineup_slots
print('\n=== core.lineup_slots ===')
slots = repo.conn.execute('SELECT COUNT(*) FROM core.lineup_slots').fetchone()
print(f'Total lineup slots: {slots[0]}')

sample_slot = repo.conn.execute('SELECT slot_key, festival_key, artist_name, billing_tier, stage_name FROM core.lineup_slots LIMIT 3').fetchall()
print('Sample slots:')
for row in sample_slot:
    print(f'  {row}')

# Check raw.lineup_observations
print('\n=== raw.lineup_observations ===')
obs = repo.conn.execute('SELECT COUNT(*) FROM raw.lineup_observations').fetchone()
print(f'Total observations: {obs[0]}')

sample_obs = repo.conn.execute('SELECT observation_key, festival_name, artist_name, position FROM raw.lineup_observations LIMIT 3').fetchall()
print('Sample observations:')
for row in sample_obs:
    print(f'  {row}')

# Check core.festivals
print('\n=== core.festivals ===')
festivals = repo.conn.execute('SELECT COUNT(*) FROM core.festivals').fetchone()
print(f'Total festivals: {festivals[0]}')

sample_festival = repo.conn.execute('SELECT festival_key, name, location_country FROM core.festivals').fetchall()
print('Festivals:')
for row in sample_festival:
    print(f'  {row}')

# Check core.festival_editions
print('\n=== core.festival_editions ===')
editions = repo.conn.execute('SELECT COUNT(*) FROM core.festival_editions').fetchone()
print(f'Total editions: {editions[0]}')

sample_edition = repo.conn.execute('SELECT edition_key, festival_key, year FROM core.festival_editions').fetchall()
print('Editions:')
for row in sample_edition:
    print(f'  {row}')

repo.close()
