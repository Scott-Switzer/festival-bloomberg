"""Offline unit tests for research.comparable (transparent distance + valuation).

No DB, no network: the engine is pure computation over frozen rows.
"""
from __future__ import annotations

from festival_bloomberg.research.comparable import (
    FINGERPRINT_SOURCE_FIELDS,
    assert_admissibility_contract,
    calendar_distance,
    comparable_distance,
    point_in_time_candidates,
    retrieve_global,
    retrieve_stratum,
    weighted_quantile,
)
from festival_bloomberg.research.features import LEAKAGE_BLACKLIST


def _row(engagement_id, artist, venue, city, start_date, publication_time, headcount,
         **extra):
    row = {
        "engagement_id": engagement_id,
        "artist": artist,
        "venue": venue,
        "city": city,
        "market": None,
        "start_date": start_date,
        "publication_time": publication_time,
        "headcount_total": headcount,
        "number_of_shows": 1,
        "price_min": 50.0,
        "price_max": 100.0,
        "ticket_gross_total": 1000.0,
    }
    row.update(extra)
    return row


def test_admissibility_contract_disjoint_from_blacklist():
    assert FINGERPRINT_SOURCE_FIELDS.isdisjoint(LEAKAGE_BLACKLIST)
    assert_admissibility_contract()  # must not raise


def test_blacklisted_fields_cannot_change_distance():
    """Leakage regression: price/shows/gross must not affect the fingerprint."""
    a = _row("t", "A", "V", "C", "2024-06-01", None, None)
    b = _row("c", "B", "W", "D", "2024-07-01", None, None)
    base = comparable_distance(a, b)

    # vary only blacklisted/outcome fields -> distance must be identical
    b2 = dict(b)
    b2["price_min"] = 999999.0
    b2["price_max"] = 9999999.0
    b2["ticket_gross_total"] = 123456789.0
    b2["number_of_shows"] = 42
    b2["headcount_total"] = 987654.0
    changed = comparable_distance(a, b2)

    assert changed["components"] == base["components"]
    assert changed["ranking_distance"] == base["ranking_distance"]
    assert changed["observed_distance"] == base["observed_distance"]
    assert "price" not in changed["components"]
    assert "shows" not in changed["components"]


def test_calendar_distance_is_circular():
    assert calendar_distance("2024-01-15", "2024-07-15") == 1.0   # opposite months
    assert calendar_distance("2024-01-15", "2024-12-15") == 1.0 / 6.0  # wraps
    assert calendar_distance("2024-06-01", "2024-06-30") == 0.0
    assert calendar_distance(None, "2024-06-30") is None


def test_missingness_is_coverage_cost_not_similarity():
    # candidate with no venue/market/date must NOT be treated as "0.5 similar"
    a = _row("t", "A", "V", "C", "2024-06-01", None, None)
    b = _row("c", "B", None, None, None, None, None)
    d = comparable_distance(a, b)
    assert d["coverage_score"] < 1.0
    assert "venue" in d["missing"] and "market" in d["missing"] and "calendar" in d["missing"]
    # ranking distance = observed distance (artist diff = 1.0) + penalty
    assert d["ranking_distance"] > d["observed_distance"]
    # observed distance ignores the missing dims entirely
    assert d["observed_distance"] == 1.0  # only artist observed, and it differs


def test_same_identity_is_zero_distance():
    a = _row("t", "A", "V", "C", "2024-06-01", None, None)
    d = comparable_distance(a, a)
    assert d["components"]["artist"] == 0.0
    assert d["components"]["venue"] == 0.0
    assert d["components"]["market"] == 0.0
    assert d["components"]["calendar"] == 0.0
    assert d["observed_distance"] == 0.0
    assert d["coverage_score"] == 1.0


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
    assert ids == {"old"}


def test_weighted_quantile():
    assert weighted_quantile([1, 2, 3], [1, 1, 1], 0.5) == 2
    assert weighted_quantile([10, 20, 30], [1, 1, 1], 0.25) == 10
    assert weighted_quantile([], [], 0.5) is None
    assert weighted_quantile([1, 2, 3], [0, 0, 0], 0.5) == 2


def test_retrieve_global_decomposition_and_valuation():
    target = _row("t", "A", "V", "C", "2024-06-01", "2024-01-01", None)
    pool = [
        _row(f"c{i}", "A" if i == 0 else f"B{i}", "V" if i == 0 else f"V{i}",
             "C" if i == 0 else f"City{i}", "2024-05-15", "2023-02-01", 100 + i * 10)
        for i in range(12)
    ]
    res = retrieve_global(target, pool, value_fn=lambda r: r["headcount_total"], k=5)
    assert len(res["comps"]) == 5
    assert res["n_candidates"] == 12
    assert res["comps"][0]["distance"] <= res["comps"][-1]["distance"]
    assert res["valuation"]["weighted_median"] is not None
    assert res["valuation"]["p10"] <= res["valuation"]["p90"]
    for c in res["comps"]:
        assert set(c["components"]) == {"artist", "venue", "market", "calendar"}


def test_retrieve_stratum_prefers_specific_stratum():
    target = _row("t", "A", "V", "C", "2024-06-01", "2024-01-01", None)
    pool = []
    # SAME_ARTIST_VENUE: enough members to be selected (>= min_stratum_size=3)
    for i in range(3):
        pool.append(_row(f"av{i}", "A", "V", "C", f"2024-0{i+1}-01", "2023-01-01", 500 + i))
    # BROAD_FALLBACK members (lower quality)
    for i in range(10):
        pool.append(_row(f"bf{i}", f"X{i}", f"Y{i}", f"Z{i}", "2024-05-01", "2023-01-01", 10 + i))
    res = retrieve_stratum(target, pool, value_fn=lambda r: r["headcount_total"], k=10,
                           min_stratum_size=3)
    assert res["stratum"] == "SAME_ARTIST_VENUE"
    assert res["n_candidates"] == 3
    assert all(c["artist"] == "A" and c["venue"] == "V" for c in res["comps"])
    assert res["valuation"]["weighted_median"] == 501.0


def test_retrieve_stratum_falls_back_when_specific_too_small():
    target = _row("t", "A", "V", "C", "2024-06-01", "2024-01-01", None)
    pool = []
    # only 1 same-artist-venue member -> below min_stratum_size
    pool.append(_row("av0", "A", "V", "C", "2024-05-01", "2023-01-01", 500))
    # 3 same-artist members -> next stratum is chosen
    for i in range(3):
        pool.append(_row(f"a{i}", "A", f"V{i}", f"C{i}", "2024-05-01", "2023-01-01", 100 + i))
    res = retrieve_stratum(target, pool, value_fn=lambda r: r["headcount_total"], k=10,
                           min_stratum_size=3)
    assert res["stratum"] == "SAME_ARTIST"
    assert res["n_candidates"] == 3
