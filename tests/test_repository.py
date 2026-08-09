"""Tests for the warehouse repository layer (offline)."""
from datetime import date


def test_upsert_and_count_festivals(repo):
    assert repo.count_festivals() == 2
    fests = repo.list_festivals()
    names = {f["name"] for f in fests}
    assert {"Lollapalooza", "Coachella"} <= names


def test_get_festival_returns_real_data(repo):
    fest = repo.get_festival("name::lollapalooza")
    assert fest is not None
    assert fest["name"] == "Lollapalooza"
    assert fest["capacity"] == 400000
    assert fest["genre_focus"] == ["rock", "hip-hop", "pop"]


def test_get_festival_missing_is_none(repo):
    assert repo.get_festival("name::does_not_exist") is None


def test_upsert_artist_and_search(repo):
    assert repo.count_artists() >= 2
    results = repo.search_artists("radio")
    assert any(r["name"] == "Radiohead" for r in results)
    # Case-insensitive
    results = repo.search_artists("RADIO")
    assert any(r["name"] == "Radiohead" for r in results)


def test_get_artist_with_mbid(repo):
    artist = repo.get_artist("a74b1b7f-36a9-4d22-a1cf-017dc00396d0")
    assert artist is not None
    assert artist["name"] == "Radiohead"
    assert artist["country"] == "GB"
    assert "alternative rock" in artist["genres"]


def test_artist_metrics_stored(repo):
    metrics = repo.get_artist_metrics("a74b1b7f-36a9-4d22-a1cf-017dc00396d0")
    assert len(metrics) == 1
    m = metrics[0]
    assert m["source_system"] == "wikipedia"
    assert m["metric_type"] == "pageviews_30d"
    assert m["value"] == 96903.0
    assert m["observed_date"] == date(2026, 1, 1)


def test_upsert_is_idempotent(repo):
    before = repo.count_artists()
    repo.upsert_artist({
        "name": "Radiohead", "normalized_name": "radiohead",
        "musicbrainz_id": "a74b1b7f-36a9-4d22-a1cf-017dc00396d0",
        "country": "GB", "genres": ["alternative rock"], "type": "Group",
    })
    after = repo.count_artists()
    assert after == before  # no duplicate


def test_lineup_observation_inserted(repo):
    rows = repo.conn.execute(
        "SELECT artist_name, position FROM raw.lineup_observations"
    ).fetchall()
    assert ("Radiohead", "headliner") in rows
