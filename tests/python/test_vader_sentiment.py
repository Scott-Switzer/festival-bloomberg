"""VADER sentiment unit tests."""

from __future__ import annotations

from festival_bloomberg.vader_sentiment import (
    classify_compound,
    mean_compound,
    score_text,
    score_texts,
)


def test_classify_compound_thresholds():
    assert classify_compound(0.5) == "positive"
    assert classify_compound(-0.5) == "negative"
    assert classify_compound(0.0) == "neutral"
    assert classify_compound(0.04) == "neutral"
    assert classify_compound(-0.04) == "neutral"


def test_positive_announcement_scores_positive():
    score = score_text(
        "Incredible headliner announcement! Fans are thrilled and excited for the festival."
    )
    assert score.label == "positive"
    assert score.compound > 0.05
    assert score.pos > score.neg


def test_negative_cancellation_scores_negative():
    score = score_text(
        "Terrible news — the festival is cancelled amid outrage and disappointment."
    )
    assert score.label == "negative"
    assert score.compound < -0.05


def test_empty_text_is_neutral():
    score = score_text("   ")
    assert score.label == "neutral"
    assert score.compound == 0.0


def test_score_texts_and_mean():
    texts = [
        "Amazing lineup reveal, best festival ever!",
        "Awful delays and miserable weather ruined the night.",
    ]
    scored = score_texts(texts)
    assert len(scored) == 2
    assert scored[0].label == "positive"
    assert scored[1].label == "negative"
    mean = mean_compound(texts)
    assert mean is not None
    assert -1.0 <= mean <= 1.0


def test_mean_compound_missing_evidence_is_none_not_zero():
    """No texts must produce no evidence (None), never a neutral 0.0."""
    assert mean_compound([]) is None
