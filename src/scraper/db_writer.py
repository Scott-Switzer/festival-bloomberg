#!/usr/bin/env python3
"""
Database writer for Festival Intelligence ingestion pipeline.
Called from TypeScript runner to write data to DuckDB using the Python warehouse repository.
"""
import sys
import json
import os
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from warehouse.repository import FestivalRepository

def write_festival(festival_data):
    """Write festival data to database."""
    db_path = festival_data.get('db_path', 'data/warehouse/festival_bloomberg.duckdb')
    repo = FestivalRepository(db_path)
    
    try:
        repo.upsert_festival({
            'festival_key': festival_data['festival_key'],
            'name': festival_data['name'],
            'normalized_name': festival_data['normalized_name'],
        })
        repo.close()
        print(json.dumps({'success': True, 'message': 'Festival written'}))
    except Exception as e:
        repo.close()
        print(json.dumps({'success': False, 'error': str(e)}), file=sys.stderr)
        sys.exit(1)

def write_edition(edition_data):
    """Write festival edition to database."""
    db_path = edition_data.get('db_path', 'data/warehouse/festival_bloomberg.duckdb')
    repo = FestivalRepository(db_path)
    
    try:
        # Insert edition directly since upsert_edition doesn't exist
        repo.conn.execute(
            """
            INSERT INTO core.festival_editions
                (edition_key, festival_key, year, ingested_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (edition_key) DO UPDATE SET
                year = excluded.year,
                ingested_at = excluded.ingested_at
            """,
            [
                edition_data['edition_key'],
                edition_data['festival_key'],
                edition_data['year'],
                edition_data.get('ingested_at'),
            ]
        )
        repo.close()
        print(json.dumps({'success': True, 'message': 'Edition written'}))
    except Exception as e:
        repo.close()
        print(json.dumps({'success': False, 'error': str(e)}), file=sys.stderr)
        sys.exit(1)

def write_artist(artist_data):
    """Write artist data to database."""
    db_path = artist_data.get('db_path', 'data/warehouse/festival_bloomberg.duckdb')
    repo = FestivalRepository(db_path)
    
    try:
        repo.upsert_artist(artist_data)
        repo.close()
        print(json.dumps({'success': True, 'message': 'Artist written'}))
    except Exception as e:
        repo.close()
        print(json.dumps({'success': False, 'error': str(e)}), file=sys.stderr)
        sys.exit(1)

def write_lineup_slot(slot_data):
    """Write lineup slot to database."""
    db_path = slot_data.get('db_path', 'data/warehouse/festival_bloomberg.duckdb')
    repo = FestivalRepository(db_path)
    
    try:
        repo.conn.execute(
            """
            INSERT INTO core.lineup_slots
                (slot_key, festival_key, edition_key, year, artist_key, artist_name,
                 normalized_artist_name, musicbrainz_id, billing_order, billing_tier,
                 stage_name, day_label, performance_date, start_time, end_time, artist_role,
                 set_type, is_b2b, collaborators, genre, subgenres,
                 announcement_date, announced_at, announcement_wave, announcement_url,
                 is_cancelled, replaced_artist_name, evidence_snippet, parser_version,
                 evidence, manually_reviewed, source_system, source_url, source_retrieved_at,
                 extraction_method, extraction_confidence, ingested_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (slot_key) DO UPDATE SET
                artist_key = excluded.artist_key,
                artist_name = excluded.artist_name,
                normalized_artist_name = excluded.normalized_artist_name,
                musicbrainz_id = excluded.musicbrainz_id,
                billing_order = excluded.billing_order,
                billing_tier = excluded.billing_tier,
                stage_name = excluded.stage_name,
                day_label = excluded.day_label,
                performance_date = excluded.performance_date,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                artist_role = excluded.artist_role,
                set_type = excluded.set_type,
                is_b2b = excluded.is_b2b,
                collaborators = excluded.collaborators,
                genre = excluded.genre,
                subgenres = excluded.subgenres,
                announcement_date = excluded.announcement_date,
                announced_at = excluded.announced_at,
                announcement_wave = excluded.announcement_wave,
                announcement_url = excluded.announcement_url,
                is_cancelled = excluded.is_cancelled,
                replaced_artist_name = excluded.replaced_artist_name,
                evidence_snippet = excluded.evidence_snippet,
                parser_version = excluded.parser_version,
                evidence = excluded.evidence,
                manually_reviewed = excluded.manually_reviewed,
                source_system = excluded.source_system,
                source_url = excluded.source_url,
                source_retrieved_at = excluded.source_retrieved_at,
                extraction_method = excluded.extraction_method,
                extraction_confidence = excluded.extraction_confidence,
                ingested_at = excluded.ingested_at,
                updated_at = excluded.updated_at
            """,
            [
                slot_data['slot_key'],
                slot_data['festival_key'],
                slot_data['edition_key'],
                slot_data['year'],
                slot_data.get('artist_key'),
                slot_data['artist_name'],
                slot_data.get('normalized_artist_name'),
                slot_data.get('musicbrainz_id'),
                slot_data.get('billing_order'),
                slot_data.get('billing_tier'),
                slot_data.get('stage_name'),
                slot_data.get('day_label'),
                slot_data.get('performance_date'),
                slot_data.get('start_time'),
                slot_data.get('end_time'),
                slot_data.get('artist_role'),
                slot_data.get('set_type'),
                slot_data.get('is_b2b'),
                json.dumps(slot_data.get('collaborators', [])),
                slot_data.get('genre'),
                json.dumps(slot_data.get('subgenres', [])),
                slot_data.get('announcement_date'),
                slot_data.get('announced_at'),
                slot_data.get('announcement_wave'),
                slot_data.get('announcement_url'),
                slot_data.get('is_cancelled'),
                slot_data.get('replaced_artist_name'),
                slot_data.get('evidence_snippet'),
                slot_data.get('parser_version'),
                json.dumps(slot_data.get('evidence', [])),
                slot_data.get('manually_reviewed', False),
                slot_data.get('source_system', 'scraper'),
                slot_data.get('source_url'),
                slot_data.get('source_retrieved_at'),
                slot_data.get('extraction_method', 'manual'),
                slot_data.get('extraction_confidence'),
                slot_data.get('ingested_at'),
                slot_data.get('updated_at'),
            ]
        )
        repo.close()
        print(json.dumps({'success': True, 'message': 'Lineup slot written'}))
    except Exception as e:
        repo.close()
        print(json.dumps({'success': False, 'error': str(e)}), file=sys.stderr)
        sys.exit(1)

def write_observation(obs_data):
    """Write lineup observation to database."""
    db_path = obs_data.get('db_path', 'data/warehouse/festival_bloomberg.duckdb')
    repo = FestivalRepository(db_path)
    
    try:
        repo.insert_lineup_observation(
            artist_name=obs_data['artist_name'],
            festival_key=obs_data.get('festival_key'),
            edition_year=obs_data.get('edition_year'),
            position=obs_data.get('position'),
            stage=obs_data.get('stage'),
            day=obs_data.get('day'),
            source_url=obs_data.get('source_url'),
            parser_version=obs_data.get('parser_version'),
            observed_raw=obs_data.get('observed_raw'),
        )
        repo.close()
        print(json.dumps({'success': True, 'message': 'Observation written'}))
    except Exception as e:
        repo.close()
        print(json.dumps({'success': False, 'error': str(e)}), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'No command specified'}), file=sys.stderr)
        sys.exit(1)
    
    command = sys.argv[1]
    data = json.loads(sys.argv[2])
    
    if command == 'festival':
        write_festival(data)
    elif command == 'edition':
        write_edition(data)
    elif command == 'artist':
        write_artist(data)
    elif command == 'slot':
        write_lineup_slot(data)
    elif command == 'observation':
        write_observation(data)
    else:
        print(json.dumps({'success': False, 'error': f'Unknown command: {command}'}), file=sys.stderr)
        sys.exit(1)
