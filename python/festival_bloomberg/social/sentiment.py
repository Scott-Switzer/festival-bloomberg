"""Versioned sentiment inference baselines.

VADER is the canonical baseline. A social-transformer pipeline (TweetNLP)
is defined as a lazy import: when the optional packages are absent it
reports ``NOT_AVAILABLE`` instead of pretending to run. Inferences are
recorded per observation as versioned records; raw text is never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..vader_sentiment import SentimentScore, score_text

VADER_MODEL_NAME = "vader"
VADER_MODEL_VERSION = "4.0.0"

TWEETNLP_MODEL_NAME = "tweetnlp-bertweet-sentiment"
TWEETNLP_MODEL_VERSION = "tweetnlp-v0.5-experimental"

TWEETNLP_AVAILABLE = False
try:  # pragma: no cover - only exercised when tweetnlp is installed
    import tweetnlp  # type: ignore  # noqa: F401

    TWEETNLP_AVAILABLE = True
except ImportError:
    pass


@dataclass(frozen=True)
class SentimentInference:
    task: str
    model_name: str
    model_version: str
    label: str
    probabilities: dict[str, float]
    emotion: dict | None = None

    @property
    def available(self) -> bool:
        return self.label != "NOT_AVAILABLE"


def vader_inference(text: str) -> SentimentInference:
    """VADER baseline inference (negative/neutral/positive probabilities)."""
    score: SentimentScore = score_text(text)
    return SentimentInference(
        task="SENTIMENT",
        model_name=VADER_MODEL_NAME,
        model_version=VADER_MODEL_VERSION,
        label=score.label,
        probabilities={
            "negative": score.neg,
            "neutral": score.neu,
            "positive": score.pos,
        },
    )


def tweetnlp_inference(text: str) -> SentimentInference:
    """Optional social-transformer baseline; NOT_AVAILABLE when uninstalled.

    TweetNLP is a heavy dependency (PyTorch). It is intentionally optional
    and never installed by default in this repository.
    """
    if not TWEETNLP_AVAILABLE:
        return SentimentInference(
            task="SENTIMENT",
            model_name=TWEETNLP_MODEL_NAME,
            model_version=TWEETNLP_MODEL_VERSION,
            label="NOT_AVAILABLE",
            probabilities={},
        )
    # pragma: no cover - requires optional install
    import tweetnlp

    classifier = tweetnlp.load_model("cardiffnlp/twitter-roberta-base-sentiment-latest")
    result = classifier.predict(text)
    label = str(result.get("label", "unknown")).lower()
    probs = result.get("probability") or {}
    return SentimentInference(
        task="SENTIMENT",
        model_name=TWEETNLP_MODEL_NAME,
        model_version=TWEETNLP_MODEL_VERSION,
        label=label,
        probabilities={
            "negative": float(probs.get("negative", 0.0)),
            "neutral": float(probs.get("neutral", 0.0)),
            "positive": float(probs.get("positive", 0.0)),
        },
    )


def infer_sentiment(text: str, model: Literal["vader", "tweetnlp"] = "vader") -> SentimentInference:
    if model == "tweetnlp":
        return tweetnlp_inference(text)
    return vader_inference(text)
