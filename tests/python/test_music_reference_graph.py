"""Regression coverage for MUSIC_REFERENCE_GRAPH_AND_PRO_WORKFLOW_V1.

Covers the step that turns the series spine into a connected graph:

- MusicBrainz event dump parsing (NDJSON streaming) + typed relationship
  extraction (event->series "part of", event->artist performer roles,
  event->place "held at", event->url, event->subevent).
- Festival/tour membership materialization (core.series_events) and
  performer-role materialization (core.event_performers) — role semantics
  preserved, never collapsed to "artist appeared".
- ListenBrainz bulk popularity (POST /1/popularity/artist): missing -> NULL,
  never zero-filled.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from festival_bloomberg.attention.listenbrainz import (
    collect_artist_popularity,
    fetch_artist_popularity,
)
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.musicbrainz.dumps import (
    ingest_events_file,
    ingest_place_file,
    normalize_event,
    normalize_place,
    relation_target,
)

from conftest import FakeTransport


@pytest.fixture()
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "refgraph.duckdb"))
    apply_pending_migrations(c)
    yield c
    c.close()


EVENT_FIXTURES = json.dumps([
    {
        "id": "event-mbid-1",
        "type": "Festival",
        "name": "Coachella 2024",
        "life-span": {"begin": "2024-04-12", "end": "2024-04-21"},
        "time": "12:00:00",
        "cancelled": False,
        "relations": [
            {"target-type": "series", "type": "part of", "direction": "forward",
             "series": {"id": "series-mbid-1", "name": "Coachella", "type": "Festival"}},
            {"target-type": "artist", "type": "main performer", "direction": "forward",
             "artist": {"id": "artist-mbid-1", "name": "Lana Del Rey"}},
            {"target-type": "artist", "type": "support act", "direction": "backward",
             "artist": {"id": "artist-mbid-2", "name": "Some Opener"}},
            {"target-type": "place", "type": "held at", "direction": "forward",
             "place": {"id": "place-mbid-1", "name": "Empire Polo Club"}},
            {"target-type": "url", "type": "official homepage", "direction": "forward",
             "url": {"id": "url-1", "resource": "https://coachella.com"}},
        ],
    }
])


def test_normalize_event_and_relation_target():
    obj = json.loads(EVENT_FIXTURES)[0]
    rec = normalize_event(obj)
    assert rec["mbid"] == "event-mbid-1"
    assert rec["begin_date"] == "2024-04-12"
    tt, tid, tval = relation_target(obj["relations"][4])
    assert (tt, tval) == ("url", "https://coachella.com")


def test_event_ingest_materializes_graph(conn):
    lines = "\n".join(json.dumps(o) for o in json.loads(EVENT_FIXTURES))
    summary = ingest_events_file(conn, _tmp_ndjson(conn, lines), dump_source_id_value="dump::event::1")
    conn.commit()
    assert summary["new_events"] == 1
    assert summary["series_events"] == 1
    assert summary["performers"] == 2
    assert summary["places"] == 1
    assert summary["urls"] == 1

    # Series membership materialized (festival series -> event).
    se = conn.execute(
        "SELECT series_mbid, event_mbid, event_type FROM core.series_events"
    ).fetchall()
    assert se == [("series-mbid-1", "event-mbid-1", "Festival")]

    # Performer roles preserved, never collapsed to "appeared".
    roles = conn.execute(
        "SELECT performer_role FROM core.event_performers ORDER BY performer_role"
    ).fetchall()
    assert [r[0] for r in roles] == ["main performer", "support act"]

    # Typed edges for place + url.
    preds = conn.execute(
        "SELECT predicate FROM core.entity_relationships ORDER BY predicate"
    ).fetchall()
    assert [p[0] for p in preds] == ["EVENT_AT_PLACE", "EVENT_HAS_URL"]


def _tmp_ndjson(conn, text):
    # Write fixture NDJSON to a temp file for the streaming ingest path.
    import tempfile
    path = tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False)
    path.write(text)
    path.close()
    return path.name


PLACE_FIXTURE = json.dumps({
    "id": "place-mbid-1",
    "type": "Stadium",
    "name": "Empire Polo Club",
    "address": "81-800 Avenue 51, Indio, CA",
    "coordinates": {"latitude": 33.68, "longitude": -116.24},
    "area": {"id": "area-mbid-1", "name": "Indio"},
    "disambiguation": "Coachella site",
})


def test_normalize_place():
    rec = normalize_place(json.loads(PLACE_FIXTURE))
    assert rec["mbid"] == "place-mbid-1"
    assert rec["place_type"] == "Stadium"
    assert rec["latitude"] == 33.68
    assert rec["area_mbid"] == "area-mbid-1"


def test_normalize_place_drops_invalid_coordinates():
    bad = json.loads(PLACE_FIXTURE)
    bad["coordinates"] = {"latitude": 33.68, "longitude": -73991593.99}
    rec = normalize_place(bad)
    # Out-of-range longitude -> NULL, never persisted as garbage or clamped.
    assert rec["latitude"] == 33.68
    assert rec["longitude"] is None
    no_coords = json.loads(PLACE_FIXTURE)
    no_coords["coordinates"] = None
    assert normalize_place(no_coords)["latitude"] is None


def test_place_ingest_creates_canonical_venue(conn):
    lines = PLACE_FIXTURE + "\n"
    summary = ingest_place_file(
        conn, _tmp_ndjson(conn, lines), dump_source_id_value="dump::place::1"
    )
    conn.commit()
    assert summary["new_places"] == 1
    venue = conn.execute(
        "SELECT venue_key, name, venue_type, musicbrainz_id FROM core.venues "
        "WHERE musicbrainz_id = 'place-mbid-1'"
    ).fetchone()
    assert venue == ("mbid::place-mbid-1", "Empire Polo Club", "Stadium", "place-mbid-1")
    # Area relationship materialized, capacity/country NOT fabricated.
    edge = conn.execute(
        "SELECT predicate FROM core.entity_relationships WHERE subject_key = 'mbid::place-mbid-1'"
    ).fetchall()
    assert [e[0] for e in edge] == ["PLACE_IN_AREA"]
    cap = conn.execute(
        "SELECT capacity, country FROM core.venues WHERE musicbrainz_id = 'place-mbid-1'"
    ).fetchone()
    assert cap == (None, None)


def test_event_ingest_is_idempotent(conn):
    lines = "\n".join(json.dumps(o) for o in json.loads(EVENT_FIXTURES))
    p = _tmp_ndjson(conn, lines)
    first = ingest_events_file(conn, p, dump_source_id_value="dump::event::1")
    conn.commit()
    second = ingest_events_file(conn, p, dump_source_id_value="dump::event::1")
    conn.commit()
    assert first["new_events"] == 1
    assert second["new_events"] == 0
    assert second["skipped_existing"] == 1
    assert conn.execute("SELECT COUNT(*) FROM core.event_performers").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# ListenBrainz bulk popularity
# ---------------------------------------------------------------------------
def test_fetch_artist_popularity_batch():
    payload = [
        {"artist_mbid": "a", "total_listen_count": 1000, "total_user_count": 10},
        {"artist_mbid": "b", "total_listen_count": None, "total_user_count": None},
    ]
    transport = FakeTransport([(200, payload)])
    result = fetch_artist_popularity(transport, ["a", "b"])
    assert result["status"] == "ok"
    assert result["rows"][0] == {"artist_mbid": "a", "total_listen_count": 1000, "total_user_count": 10}
    assert result["rows"][1]["total_listen_count"] is None  # missing -> NULL, never zero


def test_collect_artist_popularity_persists(conn):
    payload = [
        {"artist_mbid": "a", "total_listen_count": 1000, "total_user_count": 10},
        {"artist_mbid": "b", "total_listen_count": None, "total_user_count": None},
    ]
    transport = FakeTransport([(200, payload)])
    summary = collect_artist_popularity(
        conn, transport, artists=[("Alpha", "a"), ("Beta", "b")]
    )
    conn.commit()
    assert summary["status"] == "ok"
    assert summary["artists_returned"] == 2
    rows = conn.execute(
        "SELECT metric_kind, value, status FROM metrics.artist_attention_observations "
        "WHERE source_system='listenbrainz' ORDER BY metric_kind, artist_key"
    ).fetchall()
    by = {(r[0], r[2]): r[1] for r in rows}
    assert ("LISTENBRAINZ_TOTAL_LISTEN_COUNT", "ok") in by
    # Missing artist b has a NULL listen count with status missing (not zero).
    assert ("LISTENBRAINZ_TOTAL_LISTEN_COUNT", "missing") in by
    assert by[("LISTENBRAINZ_TOTAL_LISTEN_COUNT", "missing")] is None
