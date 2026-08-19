"""Offline unit tests for research.comparable (transparent distance + valuation).

No DB, no network: the engine is pure computation over frozen rows.
"""
from __future__ import annotations

from festival_bloomberg.research.comparable import (
    calendar_distance,
    comparable_distance,
    log_price_distance,
    point_in_time_candidates,
    retrieve_comparables,
    weighted_quantile,
)


def _row(engagement_id, artist, venue, city, start_date, publication_time, headcount):
    return {
        "engagement_id": engagement_id,
        "artist": artist,
        "venue": venue,
        "city": city,
        "market": None,
        "start_date": start_date,
        "publication_time": publication_time,
        "headcount_total": headcount,
        "number_of_shows": 1,
        "price_min": None,
    }


def test_calendar_distance_is_circular():
    assert calendar_distance("2024-01-15", "2024-07-15") == 1.0   # opposite months
    assert calendar_distance("2024-01-15", "2024-12-15") == 1.0 / 6.0  # wraps
    assert calendar_distance("2024-06-01", "2024-06-30") == 0.0
    assert calendar_distance(None, "2024-06-30") is None


def test_log_price_distance():
    assert log_price_distance({"price_min": 50.0}, {"price_min": 50.0}) == 0.0
    assert log_price_distance({"price_min": 50.0}, {"price_min": 500.0}) > 0.99  # ~10x
    assert log_price_distance({"price_min": None}, {"price_min": 50.0}) is None


def test_distance_decomposition_and_missing_penalty():
    a = _row("t", "Artist A", "Venue A", "Chicago", "2024-06-01", None, 100)
    b = _row("c", "Artist B", "Venue B", "Austin", "2024-06-15", None, 200)
    d = comparable_distance(a, b)
    assert d["components"]["artist"] == 1.0
    assert d["components"]["venue"] == 1.0
    assert d["components"]["market"] == 1.0
    assert 0.0 <= d["components"]["calendar"] <= 1.0
    assert d["components"]["price"] == 0.5  # missing -> neutral prior, never zero
    assert "price" in d["missing"]
    assert 0.0 <= d["overall"] <= 1.0


def test_same_identity_is_zero_distance():
    a = _row("t", "Artist A", "Venue A", "Chicago", "2024-06-01", None, 100)
    d = comparable_distance(a, a)
    assert d["components"]["artist"] == 0.0
    assert d["components"]["venue"] == 0.0
    assert d["components"]["market"] == 0.0


def test_point_in_time_excludes_future_and_self():
    target = _row("t", "A", "V", "C", "2024-06-01", "2024-01-01", 100)
    pool = [
        _row("t", "A", "V", "C", "2024-06-01", "2024-01-01", 100),  # self
        _row("old", "A", "V", "C", "2023-01-01", "2023-02-01", 50),  # published before
        _row("future", "A", "V", "C", "2024-05-01", "2024-07-01", 500),  # published after start
        _row("nopub", "A", "V", "C", "2023-01-01", None, 40),  # unknown pub time
    ]
    cands = point_in_time_candidates(target, pool, target_engagement_id="t")
    ids = {c["engagement_id"] for c in cands}
    assert ids == {"old"}  # self, future-published, and unknown-pub are excluded


def test_weighted_quantile():
    assert weighted_quantile([1, 2, 3], [1, 1, 1], 0.5) == 2
    assert weighted_quantile([10, 20, 30], [1, 1, 1], 0.25) == 10
    assert weighted_quantile([], [], 0.5) is None
    # degenerate zero weights -> unweighted median
    assert weighted_quantile([1, 2, 3], [0, 0, 0], 0.5) == 2


def test_retrieve_comparables_decomposition_and_valuation():
    target = _row("t", "A", "V", "C", "2024-06-01", "2024-01-01", None)
    pool = [
        _row(f"c{i}", "A" if i == 0 else f"B{i}", "V" if i == 0 else f"V{i}",
             "C" if i == 0 else f"City{i}", "2024-05-15", "2023-02-01", 100 + i * 10)
        for i in range(12)
    ]
    res = retrieve_comparables(target, pool, value_fn=lambda r: r["headcount_total"], k=5)
    assert len(res["comps"]) == 5
    assert res["n_candidates"] == 12
    # closest comp (same artist/venue/market) ranks first
    assert res["comps"][0]["distance"] <= res["comps"][-1]["distance"]
    assert res["valuation"]["weighted_median"] is not None
    assert res["valuation"]["p10"] <= res["valuation"]["p90"]
    # decomposition present for every comp
    for c in res["comps"]:
        assert set(c["components"]) == {"artist", "venue", "market", "calendar", "price", "shows"}
