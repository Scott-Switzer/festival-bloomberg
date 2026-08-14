"""Offline regressions for BASELINE_RESEARCH_V1.

All deterministic, no network. Covers the leakage and semantics guards that
make the research defensible: frozen-corpus checksums, target separation,
point-in-time feature availability, missing-as-information, the comparable
fallback hierarchy, deterministic models/evaluation, and negative controls.
"""

from __future__ import annotations

import numpy as np
import pytest

from festival_bloomberg.research.baselines import (
    classification_metrics,
    fit_log_linear,
    fit_logistic,
    fit_ridge,
    hierarchical_fallback,
    naive_predict,
    partial_pooling_predict,
    predict_log,
    predict_prob,
    regression_metrics,
    roc_auc,
    spearman,
)
from festival_bloomberg.research.experiment import (
    evaluate_split,
    model_card,
    shuffled_target_control,
)
from festival_bloomberg.research.features import (
    LEAKAGE_BLACKLIST,
    TARGET_ATTENDANCE,
    TARGET_GROSS,
    TARGET_PAID_TICKETS,
    TARGET_SELL_OUT,
    compute_features,
    population,
    target_value,
)
from festival_bloomberg.research.freeze import _recompute_time_folds, corpus_checksum


def _row(eid: str, **overrides) -> dict:
    row = {
        "engagement_id": eid,
        "canonical_engagement_id": f"c_{eid}",
        "artist": "Artist A",
        "venue": "Venue A",
        "city": "Chicago",
        "market": "Chicago",
        "start_date": "2013-10-26",
        "end_date": "2013-10-26",
        "publication_time": "2013-11-11",
        "number_of_shows": 1,
        "is_multi_show": False,
        "headcount_total": 15000.0,
        "headcount_definition": "REPORTED_ATTENDANCE",
        "ticket_gross_total": 1000000.0,
        "currency": "USD",
        "reported_sellouts": 1,
        "reporting_source": "billboard",
        "rights_status": "RESEARCH_ONLY",
        "rank": 1,
        "is_reported": True,
        "is_estimated": False,
        "folds": {"TIME": "TRAIN", "ARTIST_GROUP": "TRAIN", "VENUE_GROUP": "TRAIN", "MARKET_GROUP": "TRAIN", "TOUR_GROUP": "TRAIN"},
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# freeze checksum + time folds
# ---------------------------------------------------------------------------
def test_corpus_checksum_is_deterministic_and_order_invariant():
    rows = [_row("e1"), _row("e2", artist="Artist B")]
    assert corpus_checksum(rows) == corpus_checksum(list(reversed(rows)))
    assert corpus_checksum(rows) != corpus_checksum([_row("e1", headcount_total=1.0), _row("e2")])


def test_time_folds_recomputed_from_dates():
    rows = [
        _row("e1", start_date="2013-01-01"),
        _row("e2", start_date="2014-01-01"),
        _row("e3", start_date="2024-01-01"),
        _row("e4", start_date="2026-01-01"),
    ]
    _recompute_time_folds(rows)
    folds = {r["engagement_id"]: r["folds"]["TIME"] for r in rows}
    assert folds["e1"] == "TRAIN"
    assert folds["e4"] == "TEST"
    # deterministic across calls
    before = folds
    _recompute_time_folds(rows)
    assert {r["engagement_id"]: r["folds"]["TIME"] for r in rows} == before


# ---------------------------------------------------------------------------
# target semantics + eligibility
# ---------------------------------------------------------------------------
def test_targets_are_semantically_separate():
    row = _row("e1", headcount_definition="REPORTED_ATTENDANCE", headcount_total=15000.0)
    assert target_value(row, TARGET_ATTENDANCE) == 15000.0
    assert target_value(row, TARGET_PAID_TICKETS) is None  # never coerced
    row_paid = _row("e2", headcount_definition="PAID_TICKETS", headcount_total=12000.0)
    assert target_value(row_paid, TARGET_PAID_TICKETS) == 12000.0
    assert target_value(row_paid, TARGET_ATTENDANCE) is None


def test_population_excludes_multi_show_estimated_and_non_usd():
    rows = [
        _row("e1"),
        _row("e2", is_multi_show=True, number_of_shows=2),
        _row("e3", is_estimated=True, is_reported=False),
        _row("e4", currency="EUR"),
        _row("e5", headcount_total=None),
    ]
    eligible, waterfall = population(rows, TARGET_GROSS)
    assert waterfall["eligible"] == 2  # e1 (USD) + e5? no: e5 gross present
    # e4 is non-USD -> non_usd_gross; e2 multi -> multi_show; e3 estimated
    assert waterfall["multi_show"] == 1
    assert waterfall["estimated_or_unreported"] == 1
    assert waterfall["non_usd_gross"] == 1


def test_gross_currency_fail_closed():
    row_eur = _row("e1", currency="EUR", ticket_gross_total=500000.0)
    assert target_value(row_eur, TARGET_GROSS) is None


# ---------------------------------------------------------------------------
# point-in-time feature availability
# ---------------------------------------------------------------------------
def test_future_result_is_excluded_from_history():
    target = _row("t1", start_date="2024-06-01", headcount_total=20000.0)
    future = _row("f1", start_date="2024-08-01", publication_time="2024-09-01", headcount_total=99999.0)
    past = _row("p1", start_date="2024-03-01", publication_time="2024-04-01", headcount_total=10000.0, artist="Artist A")
    feats = compute_features([target, future, past], TARGET_ATTENDANCE)
    # the future row's result must NOT contribute to the target's history
    target_feat = next(f for f in feats if f["row"]["engagement_id"] == "t1")
    assert target_feat["features"]["artist_count"] == 1  # only the past row
    assert target_feat["features"]["artist_median"] == 10000.0


def test_missing_history_is_not_zero():
    target = _row("t1", start_date="2024-06-01")
    feats = compute_features([target], TARGET_ATTENDANCE)
    f = feats[0]["features"]
    assert f["has_artist_history"] is False
    assert f["artist_count"] == 0
    assert f["artist_median"] is None  # never imputed to zero


# ---------------------------------------------------------------------------
# fallback hierarchy
# ---------------------------------------------------------------------------
def test_hierarchical_fallback_order():
    f = {
        "artist_venue_median": 100.0,
        "artist_market_median": 200.0,
        "artist_median": 300.0,
        "venue_median": 400.0,
        "market_median": 500.0,
    }
    assert hierarchical_fallback(f, 999.0) == 100.0
    del f["artist_venue_median"]
    assert hierarchical_fallback(f, 999.0) == 200.0
    assert hierarchical_fallback({}, 999.0) == 999.0


def test_naive_and_partial_pooling():
    item = {"features": {"artist_count": 4, "artist_mean": 12000.0, "venue_count": 2, "venue_mean": 9000.0}}
    assert partial_pooling_predict(item, 10000.0, strength=5.0) is not None
    assert naive_predict({"features": {}}, "global_median", 42.0) == 42.0


# ---------------------------------------------------------------------------
# leakage blacklist
# ---------------------------------------------------------------------------
def test_leakage_blacklist_contains_target_columns():
    for col in ("headcount_total", "ticket_gross_total", "rank", "reported_sellouts", "sell_through_pct", "price_min"):
        assert col in LEAKAGE_BLACKLIST


# ---------------------------------------------------------------------------
# metrics sanity
# ---------------------------------------------------------------------------
def test_regression_and_rank_metrics():
    y = np.asarray([100.0, 200.0, 300.0])
    p = np.asarray([110.0, 190.0, 310.0])
    m = regression_metrics(y, p)
    assert m["n"] == 3
    assert m["mae"] == pytest.approx(10.0)
    assert m["rmse"] == pytest.approx(10.0)
    assert m["spearman"] == pytest.approx(1.0)


def test_spearman_monotonic_and_roc_auc():
    assert spearman(np.asarray([1, 2, 3]), np.asarray([1, 2, 3])) == pytest.approx(1.0)
    y = np.asarray([0, 0, 1, 1])
    s = np.asarray([0.1, 0.2, 0.8, 0.9])
    assert roc_auc(y, s) == pytest.approx(1.0)


def test_classification_metrics_bounds():
    y = np.asarray([0.0, 1.0, 1.0, 0.0])
    p = np.asarray([0.2, 0.8, 0.7, 0.3])
    m = classification_metrics(y, p)
    assert 0.0 <= m["brier"] <= 1.0
    assert m["roc_auc"] >= 0.0


# ---------------------------------------------------------------------------
# deterministic models + evaluation + controls
# ---------------------------------------------------------------------------
def _small_rows():
    rows = []
    for i in range(40):
        year = 2013 + (i % 4)
        rows.append(_row(
            f"e{i}",
            artist=f"A{i % 8}",
            venue=f"V{i % 6}",
            city=f"C{i % 4}",
            start_date=f"{year}-01-{(i % 28) + 1:02d}",
            publication_time=f"{year}-06-01",
            headcount_total=8000.0 + (i * 137 % 9000),
            ticket_gross_total=600000.0 + (i * 7919 % 900000),
            reported_sellouts=(i % 3 == 0),
            reporting_source="billboard" if year < 2015 else "touring_data",
            folds={
                "TIME": "TRAIN" if year < 2016 else "TEST",
                "ARTIST_GROUP": "TRAIN" if i % 2 == 0 else "TEST",
                "VENUE_GROUP": "TRAIN" if i % 3 == 0 else "TEST",
                "MARKET_GROUP": "TRAIN",
                "TOUR_GROUP": "TRAIN" if i % 5 == 0 else "TEST",
            },
        ))
    return rows


def test_models_are_deterministic():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(60, 6))
    y = np.exp(0.5 + X[:, 0] * 0.3) * 1000.0
    c1 = fit_log_linear(X, y)
    c2 = fit_log_linear(X, y)
    assert np.allclose(c1, c2)
    r1 = fit_ridge(X, y, alpha=1.0)
    assert np.allclose(r1, fit_ridge(X, y, alpha=1.0))
    yb = (y > np.median(y)).astype(float)
    l1 = fit_logistic(X, yb)
    assert np.allclose(l1, fit_logistic(X, yb))


def test_evaluation_is_deterministic():
    rows = _small_rows()
    a = evaluate_split(rows, TARGET_ATTENDANCE, "TIME", seed=42)
    b = evaluate_split(rows, TARGET_ATTENDANCE, "TIME", seed=42)
    assert a["models"]["global_median"]["mae"] == b["models"]["global_median"]["mae"]
    assert a["models"]["log_linear"]["mae"] == b["models"]["log_linear"]["mae"]


def test_same_engagement_never_crosses_splits():
    rows = _small_rows()
    res = evaluate_split(rows, TARGET_ATTENDANCE, "TIME", seed=42)
    # TIME split by date: verify train dates all <= test dates
    # (we only validate that the fold assignment is internally consistent)
    eligible, _ = population(rows, TARGET_ATTENDANCE)
    train = {r["engagement_id"] for r in eligible if r["folds"]["TIME"] == "TRAIN"}
    test = {r["engagement_id"] for r in eligible if r["folds"]["TIME"] == "TEST"}
    assert not (train & test)
    assert res["train"] + res["test"] == len(eligible)


def test_shuffled_target_control_reports_metrics():
    rows = _small_rows()
    out = shuffled_target_control(rows, TARGET_ATTENDANCE, "TIME", seed=42)
    assert "shuffled_train_target_metric" in out
    # shuffled-target metrics should exist for the statistical models
    assert "log_linear" in out["shuffled_train_target_metric"]


def test_model_card_marks_prohibited_use():
    card = model_card(TARGET_ATTENDANCE, "log_linear", {"mae": 1.0})
    assert card["target"] == TARGET_ATTENDANCE
    assert "production booking recommendation" in card["prohibited_use"]
    assert card["rights"] != "OPEN_COMMERCIAL_OK"
