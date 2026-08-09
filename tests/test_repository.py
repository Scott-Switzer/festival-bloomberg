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


def test_sentiment_persist_and_read(repo):
    from scrapers.contracts import ArtistInsight, SentimentBreakdown

    ins = ArtistInsight(
        artist_name="Radiohead",
        artist_key="a74b1b7f-36a9-4d22-a1cf-017dc00396d0",
        musicbrainz_id="a74b1b7f-36a9-4d22-a1cf-017dc00396d0",
        mention_volume=36,
        attention_score=72.0,
        sentiment=SentimentBreakdown(
            positive=0.19, neutral=0.53, negative=0.28, compound=-0.04, sample_size=36,
            top_positive=["love this"], top_negative=["terrible"],
        ),
        sentiment_label="neutral",
        top_topics=["tour", "new music"],
        sources_used=["wikipedia", "hackernews", "gdelt"],
    )
    repo.upsert_sentiment("a74b1b7f-36a9-4d22-a1cf-017dc00396d0", ins)
    got = repo.get_artist_sentiment("a74b1b7f-36a9-4d22-a1cf-017dc00396d0")
    assert got is not None
    assert got["sentiment_label"] == "neutral"
    assert got["compound"] == -0.04
    assert got["mention_volume"] == 36
    assert got["top_topics"] == ["tour", "new music"]
    assert got["sources_used"] == ["wikipedia", "hackernews", "gdelt"]

    repo.insert_social_signal("a74b1b7f-36a9-4d22-a1cf-017dc00396d0", "hackernews",
                              mention_count=20, points=800.0, comments=300.0)
    sig = repo.get_social_signals("a74b1b7f-36a9-4d22-a1cf-017dc00396d0")
    assert any(s["source_system"] == "hackernews" and s["mention_count"] == 20 for s in sig)

    ranked = repo.list_sentiment_ranked(limit=10)
    assert any(r["artist_key"] == "a74b1b7f-36a9-4d22-a1cf-017dc00396d0" for r in ranked)
