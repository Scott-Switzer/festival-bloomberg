#!/usr/bin/env python3
"""
Database writer for Festival Intelligence ingestion pipeline.
Called from TypeScript runner to write data to DuckDB using the Python warehouse repository.
Supports both single-record writes (legacy) and batch writes (production).
"""
import sys
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime

# Optional shared LLM wrapper; ingestion remains usable without credentials.
workspace_python = Path(__file__).resolve().parents[3] / "python"
if workspace_python.is_dir():
    sys.path.insert(0, str(workspace_python))
try:
    from utils.hetzner_llm import HetznerLLMClient
except ImportError:
    HetznerLLMClient = None

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from warehouse.repository import FestivalRepository

def _norm_artist_name(value):
    """Cheap deterministic normalization used before optional LLM matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.casefold())).strip()

def _llm_artist_aliases(artists):
    """Best-effort conservative aliases; failure never blocks ingestion."""
    if not artists or not os.getenv("HETZNER_VLLM_API_KEY") or HetznerLLMClient is None:
        return {}
    names = []
    for artist in artists:
        name = str(artist.get("name", "")).strip()
        if name and not artist.get("musicbrainz_id") and name not in names:
            names.append(name)
    pairs = [(left, right) for i, left in enumerate(names) for right in names[i + 1:]
             if SequenceMatcher(None, _norm_artist_name(left), _norm_artist_name(right)).ratio() >= 0.45]
    if not pairs:
        return {}
    prompt = {"task": "artist_entity_matching", "rules": [
        "Match only the same performing artist or act; be conservative.",
        "Return JSON only: {matches:[{left:string,right:string,same:boolean,confidence:number}]}.",
        "Only include supplied pairs; never invent IDs or names."],
        "pairs": [{"left": left, "right": right} for left, right in pairs]}
    try:
        client = HetznerLLMClient(timeout=20, retries=1, fallback=True)
        result = client.chat_completions_create(
            model=os.getenv("HETZNER_VLLM_MODEL", "Qwen/Qwen2.5-72B-Instruct"),
            messages=[{"role": "system", "content": "You are a conservative music artist deduplication service."},
                      {"role": "user", "content": json.dumps(prompt)}],
            temperature=0, max_tokens=1200)
        content = result["choices"][0]["message"]["content"]
        parsed = json.loads(content if isinstance(content, str) else json.dumps(content))
        aliases = {}
        allowed = set(pairs) | {(right, left) for left, right in pairs}
        for match in parsed.get("matches", []):
            left, right = str(match.get("left", "")), str(match.get("right", ""))
            if match.get("same") is True and float(match.get("confidence", 0)) >= 0.90 and (left, right) in allowed:
                canonical = min(left, right, key=lambda value: (_norm_artist_name(value), value))
                aliases[_norm_artist_name(left)] = canonical
                aliases[_norm_artist_name(right)] = canonical
        return aliases
    except Exception:
        return {}

def _canonical_artist_key(artist, aliases):
    mb_id = artist.get("musicbrainz_id")
    if mb_id:
        return mb_id
    name = aliases.get(_norm_artist_name(str(artist.get("name", ""))), artist["name"])
    return f"name::{_norm_artist_name(name)}"

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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

def write_batch(batch_file_path):
    """Write all data from batch JSON file in a single transaction."""
    with open(batch_file_path, 'r') as f:
        batch_data = json.load(f)
    
    db_path = batch_data.get('db_path', 'data/warehouse/festival_bloomberg.duckdb')
    repo = FestivalRepository(db_path)
    
    try:
        # Begin transaction
        repo.conn.begin()
        artist_aliases = _llm_artist_aliases(batch_data.get("artists", []))
        
        # Write festivals
        for festival in batch_data.get('festivals', []):
            repo.upsert_festival({
                'festival_key': festival['festival_key'],
                'name': festival['name'],
                'normalized_name': festival['normalized_name'],
            })
        print(f"Written {len(batch_data.get('festivals', []))} festivals")
        
        # Write editions
        for edition in batch_data.get('editions', []):
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
                    edition['edition_key'],
                    edition['festival_key'],
                    edition['year'],
                    edition.get('ingested_at'),
                ]
            )
        print(f"Written {len(batch_data.get('editions', []))} editions")
        
        # Write artists
        for artist in batch_data.get('artists', []):
            mb_id = artist.get('musicbrainz_id')
            artist_key = _canonical_artist_key(artist, artist_aliases)
            repo.conn.execute(
                """
                INSERT INTO core.artists
                    (artist_key, musicbrainz_id, name, normalized_name, disambiguation,
                     country, genres, type, life_span_begin, life_span_end,
                     source_system, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (artist_key) DO UPDATE SET
                    musicbrainz_id = excluded.musicbrainz_id,
                    name = excluded.name,
                    normalized_name = excluded.normalized_name,
                    disambiguation = excluded.disambiguation,
                    country = excluded.country,
                    genres = excluded.genres,
                    type = excluded.type,
                    life_span_begin = excluded.life_span_begin,
                    life_span_end = excluded.life_span_end,
                    source_system = excluded.source_system,
                    ingested_at = excluded.ingested_at
                """,
                [
                    artist_key,
                    mb_id,
                    artist['name'],
                    artist['normalized_name'],
                    artist.get('disambiguation'),
                    artist.get('country'),
                    json.dumps(artist.get('genres', [])),
                    artist.get('type'),
                    artist.get('life_span_begin'),
                    artist.get('life_span_end'),
                    'scraper',
                    datetime.utcnow(),
                ]
            )
        print(f"Written {len(batch_data.get('artists', []))} artists")
        
        # Write lineup slots
        for slot in batch_data.get('slots', []):
            repo.conn.execute(
                """
                INSERT INTO core.lineup_slots
                    (slot_key, festival_key, edition_key, year, artist_key, artist_name,
                     normalized_artist_name, musicbrainz_id, billing_order, billing_tier,
                     poster_line, poster_position, is_headliner, stage_key, stage_name,
                     performance_date, day_of_festival, day_label, start_time, end_time,
                     local_start_time, local_end_time, time_zone, set_duration_minutes,
                     artist_role, set_type, is_b2b, collaborators, genre, subgenres,
                     announcement_date, announced_at, announcement_wave, announcement_url,
                     is_cancelled, replaced_artist_name, evidence, evidence_url,
                     evidence_snippet, extraction_confidence, extraction_method,
                     parser_version, source_system, source_url, source_retrieved_at,
                     match_confidence, match_method, manually_reviewed, ingested_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (slot_key) DO UPDATE SET
                    artist_key = excluded.artist_key,
                    artist_name = excluded.artist_name,
                    normalized_artist_name = excluded.normalized_artist_name,
                    musicbrainz_id = excluded.musicbrainz_id,
                    billing_order = excluded.billing_order,
                    billing_tier = excluded.billing_tier,
                    poster_line = excluded.poster_line,
                    poster_position = excluded.poster_position,
                    is_headliner = excluded.is_headliner,
                    stage_key = excluded.stage_key,
                    stage_name = excluded.stage_name,
                    performance_date = excluded.performance_date,
                    day_of_festival = excluded.day_of_festival,
                    day_label = excluded.day_label,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    local_start_time = excluded.local_start_time,
                    local_end_time = excluded.local_end_time,
                    time_zone = excluded.time_zone,
                    set_duration_minutes = excluded.set_duration_minutes,
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
                    evidence = excluded.evidence,
                    evidence_url = excluded.evidence_url,
                    evidence_snippet = excluded.evidence_snippet,
                    extraction_confidence = excluded.extraction_confidence,
                    extraction_method = excluded.extraction_method,
                    parser_version = excluded.parser_version,
                    source_system = excluded.source_system,
                    source_url = excluded.source_url,
                    source_retrieved_at = excluded.source_retrieved_at,
                    match_confidence = excluded.match_confidence,
                    match_method = excluded.match_method,
                    manually_reviewed = excluded.manually_reviewed,
                    ingested_at = excluded.ingested_at,
                    updated_at = excluded.updated_at
                """,
                [
                    slot['slot_key'],
                    slot['festival_key'],
                    slot['edition_key'],
                    slot['year'],
                    slot.get('artist_key') or (
                        f"name::{_norm_artist_name(artist_aliases[_norm_artist_name(slot['artist_name'])])}"
                        if _norm_artist_name(slot['artist_name']) in artist_aliases else None),
                    slot['artist_name'],
                    slot.get('normalized_artist_name'),
                    slot.get('musicbrainz_id'),
                    slot.get('billing_order'),
                    slot.get('billing_tier'),
                    slot.get('poster_line'),
                    slot.get('poster_position'),
                    slot.get('is_headliner'),
                    slot.get('stage_key'),
                    slot.get('stage_name'),
                    slot.get('performance_date'),
                    slot.get('day_of_festival'),
                    slot.get('day_label'),
                    slot.get('start_time'),
                    slot.get('end_time'),
                    slot.get('local_start_time'),
                    slot.get('local_end_time'),
                    slot.get('time_zone'),
                    slot.get('set_duration_minutes'),
                    slot.get('artist_role'),
                    slot.get('set_type'),
                    slot.get('is_b2b'),
                    json.dumps(slot.get('collaborators', [])),
                    slot.get('genre'),
                    json.dumps(slot.get('subgenres', [])),
                    slot.get('announcement_date'),
                    slot.get('announced_at'),
                    slot.get('announcement_wave'),
                    slot.get('announcement_url'),
                    slot.get('is_cancelled'),
                    slot.get('replaced_artist_name'),
                    json.dumps(slot.get('evidence', [])),
                    slot.get('evidence_url'),
                    slot.get('evidence_snippet'),
                    slot.get('extraction_confidence'),
                    slot.get('extraction_method'),
                    slot.get('parser_version'),
                    slot.get('source_system', 'scraper'),
                    slot.get('source_url'),
                    slot.get('source_retrieved_at'),
                    slot.get('match_confidence'),
                    slot.get('match_method'),
                    slot.get('manually_reviewed', False),
                    slot.get('ingested_at'),
                    slot.get('updated_at'),
                ]
            )
        print(f"Written {len(batch_data.get('slots', []))} lineup slots")
        
        # Write observations
        for obs in batch_data.get('observations', []):
            repo.insert_lineup_observation(
                artist_name=obs['artist_name'],
                festival_key=obs.get('festival_key'),
                edition_year=obs.get('edition_year'),
                position=obs.get('position'),
                stage=obs.get('stage'),
                day=obs.get('day'),
                source_url=obs.get('source_url'),
                parser_version=obs.get('parser_version'),
                observed_raw=obs.get('observed_raw'),
            )
        print(f"Written {len(batch_data.get('observations', []))} observations")
        
        # Write artist contacts
        for contact in batch_data.get('contacts', []):
            repo.conn.execute(
                """
                INSERT INTO core.artist_contacts
                    (contact_key, artist_key, agency_name, agent_name, contact_email,
                     contact_phone, role, verified, source_url, retrieved_at,
                     source_system, evidence_url, confidence, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (contact_key) DO UPDATE SET
                    artist_key = excluded.artist_key,
                    agency_name = excluded.agency_name,
                    agent_name = excluded.agent_name,
                    contact_email = excluded.contact_email,
                    contact_phone = excluded.contact_phone,
                    role = excluded.role,
                    verified = excluded.verified,
                    source_url = excluded.source_url,
                    retrieved_at = excluded.retrieved_at,
                    source_system = excluded.source_system,
                    evidence_url = excluded.evidence_url,
                    confidence = excluded.confidence,
                    ingested_at = excluded.ingested_at
                """,
                [
                    contact.get('contact_key'),
                    contact['artist_key'],
                    contact.get('agency_name'),
                    contact.get('agent_name'),
                    contact.get('contact_email'),
                    contact.get('contact_phone'),
                    contact.get('role'),
                    contact.get('verified'),
                    contact.get('source_url'),
                    contact.get('retrieved_at'),
                    contact.get('source_system', 'scraper'),
                    contact.get('evidence_url'),
                    contact.get('confidence'),
                    contact.get('ingested_at'),
                ]
            )
        print(f"Written {len(batch_data.get('contacts', []))} artist contacts")
        
        # Write lineup qualification metrics
        for metric in batch_data.get('metrics', []):
            repo.conn.execute(
                """
                INSERT INTO core.lineup_qualification_metrics
                    (metric_key, artist_key, festival_edition_key, billing_tier, billing_order,
                     stage_name, time_slot_minutes, is_headliner, repeat_booking_count,
                     sentiment_score_pre_festival, source_system, evidence_url,
                     confidence, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (metric_key) DO UPDATE SET
                    artist_key = excluded.artist_key,
                    festival_edition_key = excluded.festival_edition_key,
                    billing_tier = excluded.billing_tier,
                    billing_order = excluded.billing_order,
                    stage_name = excluded.stage_name,
                    time_slot_minutes = excluded.time_slot_minutes,
                    is_headliner = excluded.is_headliner,
                    repeat_booking_count = excluded.repeat_booking_count,
                    sentiment_score_pre_festival = excluded.sentiment_score_pre_festival,
                    source_system = excluded.source_system,
                    evidence_url = excluded.evidence_url,
                    confidence = excluded.confidence,
                    ingested_at = excluded.ingested_at
                """,
                [
                    metric.get('metric_key'),
                    metric['artist_key'],
                    metric.get('festival_edition_key'),
                    metric.get('billing_tier'),
                    metric.get('billing_order'),
                    metric.get('stage_name'),
                    metric.get('time_slot_minutes'),
                    metric.get('is_headliner'),
                    metric.get('repeat_booking_count'),
                    metric.get('sentiment_score_pre_festival'),
                    metric.get('source_system', 'scraper'),
                    metric.get('evidence_url'),
                    metric.get('confidence'),
                    metric.get('ingested_at'),
                ]
            )
        print(f"Written {len(batch_data.get('metrics', []))} lineup qualification metrics")
        
        # Commit transaction
        repo.conn.commit()
        repo.close()
        print(json.dumps({'success': True, 'message': 'Batch write completed successfully'}))
        
    except Exception as e:
        repo.conn.rollback()
        repo.close()
        print(json.dumps({'success': False, 'error': str(e)}), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'No command specified'}), file=sys.stderr)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == 'batch':
        if len(sys.argv) < 3:
            print(json.dumps({'success': False, 'error': 'Batch command requires file path'}), file=sys.stderr)
            sys.exit(1)
        write_batch(sys.argv[2])
    else:
        if len(sys.argv) < 3:
            print(json.dumps({'success': False, 'error': 'Legacy commands require JSON data'}), file=sys.stderr)
            sys.exit(1)
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
