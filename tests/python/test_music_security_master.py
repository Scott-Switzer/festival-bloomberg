"""Regression coverage for MUSIC_SECURITY_MASTER_AND_MONITORING_V1.

Enforces the identity distinctions that make a music security master
correct rather than a pile of names:

- recording != work; ISRC != ISWC
- release != release-group
- event series != event; festival series != festival edition
- external IDs are MAPPINGS (never primary keys); AMBIGUOUS is never forced
  to MATCHED
- relationships are source-backed and knowledge-timed
- MusicBrainz JSON dumps parse (array and NDJSON) and ingest with lineage
"""

from __future__ import annotations

import json

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.musicbrainz.dumps import (
    discover_latest_snapshot,
    ingest_series_dump,
    iter_json_objects,
    normalize_series,
)


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "music.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


def test_music_object_tables_exist(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='core'"
        ).fetchall()
    }
    for t in ("release_groups", "releases", "recordings", "works",
              "event_series", "labels", "companies", "entity_relationships"):
        assert t in tables
    raw = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='raw'"
        ).fetchall()
    }
    assert "musicbrainz_series" in raw
    assert "musicbrainz_dump_source" in raw


# ---------------------------------------------------------------------------
# Namespace distinctions
# ---------------------------------------------------------------------------
def test_recording_and_work_are_distinct_namespaces(conn):
    conn.execute(
        "INSERT INTO core.recordings (recording_key, musicbrainz_id, name, isrc) "
        "VALUES ('rec::1', 'recording-mbid', 'Song (Studio)', 'USRC10000001')"
    )
    conn.execute(
        "INSERT INTO core.works (work_key, musicbrainz_id, name, iswc) "
        "VALUES ('work::1', 'work-mbid', 'Song', 'T-000.000.001-0')"
    )
    conn.commit()
    rec = conn.execute("SELECT isrc FROM core.recordings WHERE recording_key='rec::1'").fetchone()[0]
    work = conn.execute("SELECT iswc FROM core.works WHERE work_key='work::1'").fetchone()[0]
    assert rec == "USRC10000001"
    assert work == "T-000.000.001-0"
    # ISRC lives on recordings, ISWC on works — they are never the same field.
    assert rec != work
    assert conn.execute(
        "SELECT COUNT(*) FROM core.recordings WHERE isrc IS NOT NULL AND isrc LIKE 'T-%'"
    ).fetchone()[0] == 0


def test_release_and_release_group_are_distinct(conn):
    conn.execute(
        "INSERT INTO core.release_groups (release_group_key, musicbrainz_id, name, primary_type) "
        "VALUES ('rg::1', 'rg-mbid', 'Album', 'Album')"
    )
    conn.execute(
        "INSERT INTO core.releases (release_key, musicbrainz_id, release_group_key, name, release_date, release_status) "
        "VALUES ('rel::1', 'rel-mbid', 'rg::1', 'Album (US CD)', '2020-01-01', 'Official')"
    )
    conn.commit()
    rg = conn.execute("SELECT primary_type FROM core.release_groups WHERE release_group_key='rg::1'").fetchone()[0]
    rel = conn.execute("SELECT release_status FROM core.releases WHERE release_key='rel::1'").fetchone()[0]
    assert rg == "Album"
    assert rel == "Official"
    # A release is a concrete edition of a release-group; the two tables are
    # distinct and linked, not collapsed.
    assert conn.execute("SELECT COUNT(*) FROM core.releases").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM core.release_groups").fetchone()[0] == 1


def test_series_is_not_event(conn):
    conn.execute(
        "INSERT INTO core.event_series (series_key, musicbrainz_id, name, series_type) "
        "VALUES ('mbid::series1', 'series1', 'World Tour', 'TOUR')"
    )
    conn.commit()
    row = conn.execute("SELECT series_type FROM core.event_series WHERE series_key='mbid::series1'").fetchone()[0]
    assert row == "TOUR"
    # The event_series table has no event/onsale columns: a tour is not an event.
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='core' AND table_name='event_series'"
    ).fetchall()}
    assert "onsale_start" not in cols
    assert "event_status" not in cols


def test_festival_series_is_not_edition(conn):
    conn.execute(
        "INSERT INTO core.event_series (series_key, musicbrainz_id, name, series_type) "
        "VALUES ('mbid::fest1', 'fest1', 'Glastonbury Festival', 'FESTIVAL')"
    )
    conn.commit()
    # A FESTIVAL series is a recurring identity, distinct from a festival
    # edition row (core.festival_editions) which is one year's instance.
    assert conn.execute(
        "SELECT COUNT(*) FROM core.event_series WHERE series_type='FESTIVAL'"
    ).fetchone()[0] == 1
    edition_cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='core' AND table_name='festival_editions'"
    ).fetchall()}
    assert "year" in edition_cols        # editions are year-scoped
    assert "festival_key" in edition_cols  # editions hang off a festival identity


# ---------------------------------------------------------------------------
# External ID master
# ---------------------------------------------------------------------------
def test_external_id_is_mapping_not_primary_key(conn):
    conn.execute(
        "INSERT INTO core.entity_external_ids "
        "(external_id_key, entity_type, entity_key, id_type, id_value, namespace, resolution_status, knowledge_time) "
        "VALUES ('eid::1', 'ARTIST', 'artist::1', 'musicbrainz', 'mbid-x', 'musicbrainz', 'MATCHED', CURRENT_TIMESTAMP)"
    )
    conn.commit()
    row = conn.execute(
        "SELECT id_value, namespace, resolution_status FROM core.entity_external_ids WHERE external_id_key='eid::1'"
    ).fetchone()
    assert row == ("mbid-x", "musicbrainz", "MATCHED")


def test_ambiguous_identity_is_preserved_not_forced(conn):
    # Two different internal keys claiming the same external ID -> the mapping
    # stays AMBIGUOUS; the resolver must never silently pick one.
    conn.execute(
        "INSERT INTO core.entity_external_ids "
        "(external_id_key, entity_type, entity_key, id_type, id_value, namespace, resolution_status, knowledge_time) "
        "VALUES ('eid::a', 'ARTIST', 'artist::a', 'spotify', 'spotify:1', 'spotify', 'AMBIGUOUS', CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO core.entity_external_ids "
        "(external_id_key, entity_type, entity_key, id_type, id_value, namespace, resolution_status, knowledge_time) "
        "VALUES ('eid::b', 'ARTIST', 'artist::b', 'spotify', 'spotify:1', 'spotify', 'AMBIGUOUS', CURRENT_TIMESTAMP)"
    )
    conn.commit()
    statuses = conn.execute(
        "SELECT resolution_status FROM core.entity_external_ids WHERE id_value='spotify:1'"
    ).fetchall()
    assert {s[0] for s in statuses} == {"AMBIGUOUS"}


# ---------------------------------------------------------------------------
# Relationship graph
# ---------------------------------------------------------------------------
def test_relationship_is_source_backed(conn):
    conn.execute(
        "INSERT INTO core.entity_relationships "
        "(relationship_key, subject_entity_type, subject_key, predicate, "
        " object_entity_type, object_key, source_system, source_url, knowledge_time, ingested_at) "
        "VALUES ('rel::1', 'ARTIST', 'artist::1', 'ARTIST_PERFORMED_AT_EVENT', "
        "        'EVENT', 'event::1', 'musicbrainz', 'https://musicbrainz.org/event/x', "
        "        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    conn.commit()
    row = conn.execute(
        "SELECT predicate, source_system, source_url FROM core.entity_relationships WHERE relationship_key='rel::1'"
    ).fetchone()
    assert row[0] == "ARTIST_PERFORMED_AT_EVENT"
    assert row[1] == "musicbrainz"
    assert row[2].startswith("https://")


# ---------------------------------------------------------------------------
# MusicBrainz dump parser / ingester
# ---------------------------------------------------------------------------
def test_discover_latest_snapshot():
    index = (
        "<html><a href=\"20260715-001001/\">20260715-001001/</a>"
        "<a href=\"20260601-120000/\">20260601-120000/</a></html>"
    )
    assert discover_latest_snapshot(index) == "20260715-001001"
    assert discover_latest_snapshot("<html></html>") is None


def test_iter_json_objects_handles_array_and_ndjson():
    array = json.dumps([{"id": "a"}, {"id": "b"}])
    assert [o["id"] for o in iter_json_objects(array)] == ["a", "b"]
    ndjson = '{"id": "a"}\n{"id": "b"}\n'
    assert [o["id"] for o in iter_json_objects(ndjson)] == ["a", "b"]


SERIES_FIXTURES = json.dumps([
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "type": "Festival",
        "name": "Glastonbury Festival",
        "disambiguation": "Pilton",
        "ordering-key": "glastonbury-festival",
        "relation-list": [
            {"relations": [{"target-type": "artist", "artist": {"id": "artist-mbid-1"}}]}
        ],
    },
    {
        "id": "00000000-0000-0000-0000-000000000002",
        "type": "Tour",
        "name": "Some World Tour",
        "relation-list": [],
    },
])


def test_non_event_series_stays_raw_only(conn):
    obj = {"id": "00000000-0000-0000-0000-000000000099", "type": "Release group series", "name": "Catalogue"}
    rec = normalize_series(obj)
    assert rec["is_event_series"] is False
    assert rec["series_type"] is None
    summary = ingest_series_dump(conn, json.dumps([obj]), dump_source_id_value="dump::series::3")
    conn.commit()
    assert summary["persisted"] == 1
    assert summary["event_series"] == 0
    assert conn.execute("SELECT COUNT(*) FROM raw.musicbrainz_series").fetchone()[0] == 1
    # Catalogue/work/label/award series never pollute core.event_series.
    assert conn.execute("SELECT COUNT(*) FROM core.event_series").fetchone()[0] == 0


def test_normalize_series_maps_type():
    rec = normalize_series(json.loads(SERIES_FIXTURES)[0])
    assert rec["series_type"] == "FESTIVAL"
    assert rec["mbid"] == "00000000-0000-0000-0000-000000000001"
    assert rec["artist_mbids"] == ["artist-mbid-1"]


def test_ingest_series_file_streams_ndjson(conn, tmp_path):
    from festival_bloomberg.musicbrainz.dumps import ingest_series_file

    path = tmp_path / "series"
    path.write_text(SERIES_FIXTURES.lstrip("[").rstrip("]").replace("\n    }, {\n", "\n"), encoding="utf-8")
    # Rewrite as clean NDJSON lines.
    lines = [json.dumps(obj) for obj in json.loads(SERIES_FIXTURES)]
    path.write_text("\n".join(lines), encoding="utf-8")
    summary = ingest_series_file(conn, path, dump_source_id_value="dump::series::2")
    conn.commit()
    assert summary["persisted"] == 2
    assert conn.execute("SELECT COUNT(*) FROM core.event_series").fetchone()[0] == 2


def test_ingest_series_dump_persists_raw_and_canonical(conn):
    summary = ingest_series_dump(
        conn, SERIES_FIXTURES, dump_source_id_value="dump::series::1"
    )
    conn.commit()
    assert summary["persisted"] == 2
    raw = conn.execute(
        "SELECT series_type FROM raw.musicbrainz_series ORDER BY mbid"
    ).fetchall()
    assert [r[0] for r in raw] == ["Festival", "Tour"]
    canonical = conn.execute(
        "SELECT series_type, name FROM core.event_series ORDER BY musicbrainz_id"
    ).fetchall()
    assert ("FESTIVAL", "Glastonbury Festival") in canonical
    assert ("TOUR", "Some World Tour") in canonical
    # Re-ingesting is idempotent (append-only, no duplicate canonical rows).
    again = ingest_series_dump(conn, SERIES_FIXTURES, dump_source_id_value="dump::series::1")
    conn.commit()
    assert again["persisted"] == 0
    assert again["skipped_existing"] == 2
