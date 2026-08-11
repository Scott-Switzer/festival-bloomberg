"""Tests for the historical backtest engine and data quality governance (offline)."""
import sys
from datetime import date

import pytest

sys.path.insert(0, ".")

from backtest.historical_backtest import (  # noqa: E402
    HistoricalBacktester,
    PlacementScorer,
    MomentumScorer,
    PointInTimeFeatures,
    BacktestLineup,
    BacktestArtist,
)
from governance.data_quality import DataQualityEngine  # noqa: E402


def test_point_in_time_feature_construction():
    feat = PointInTimeFeatures(
        artist_id="a1", artist_name="Radiohead", as_of_date=date(2023, 6, 1),
        wikipedia_pageviews=5000, primary_genre="rock", career_stage="legendary",
    )
    assert feat.artist_name == "Radiohead"
    assert feat.as_of_date == date(2023, 6, 1)


def test_momentum_scorer_returns_float():
    scorer = MomentumScorer()
    feat = PointInTimeFeatures(
        artist_id="a1", artist_name="Radiohead", as_of_date=date(2023, 6, 1),
        wikipedia_pageviews=5000,
    )
    score = scorer.calculate_score(feat)
    assert isinstance(score, float)
    assert score >= 0


def test_momentum_trend_classification():
    scorer = MomentumScorer()
    history = [
        PointInTimeFeatures("a1", "Radiohead", date(2023, 1, 1), wikipedia_pageviews=1000),
        PointInTimeFeatures("a1", "Radiohead", date(2023, 6, 1), wikipedia_pageviews=5000),
    ]
    trend = scorer.calculate_trend(history)
    assert trend in {"rising", "stable", "falling"}


def test_placement_scorer_initializes():
    scorer = PlacementScorer(format_profile="poster_grid")
    assert scorer is not None


def test_backtester_runs_on_synthetic_lineup():
    backtester = HistoricalBacktester()
    scorer = PlacementScorer(format_profile="poster_grid")
    lineup = BacktestLineup(
        festival_id="lolla", festival_name="Lollapalooza", year=2022,
        artists=[
            BacktestArtist("a1", "Radiohead", genres=["rock"]),
            BacktestArtist("a2", "Bon Iver", genres=["indie"]),
        ],
        format_profile="poster_grid",
    )
    result = backtester.run_backtest(
        lineup=lineup, cutoff_date=date(2023, 1, 1), placement_scorer=scorer
    )
    assert result is not None


def test_data_quality_engine_completeness_check():
    engine = DataQualityEngine()
    import pandas as pd

    df = pd.DataFrame([
        {"id": 1, "name": "Radiohead"},
        {"id": 2, "name": None},  # missing required name
    ])
    context = {"required_fields": ["id", "name"], "validation_rules": {}}
    report = engine.run_quality_suite(data=df, entity_type="artist", context=context)
    assert report is not None
    assert hasattr(report, "overall_score")
    assert 0 <= report.overall_score <= 100
    # Missing 'name' should pull the score below perfect.
    assert report.overall_score < 100
