"""
VADER sentiment scoring for festival text (lineup buzz, announcements, blurbs).

Uses vaderSentiment (lexicon + heuristics). No paid APIs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal, Sequence

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

SentimentLabel = Literal["positive", "neutral", "negative"]

_analyzer: SentimentIntensityAnalyzer | None = None


def _get_analyzer() -> SentimentIntensityAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentIntensityAnalyzer()
    return _analyzer


@dataclass(frozen=True)
class SentimentScore:
    text: str
    compound: float
    pos: float
    neu: float
    neg: float
    label: SentimentLabel

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def classify_compound(compound: float) -> SentimentLabel:
    """Standard VADER thresholds for compound polarity."""
    if compound >= 0.05:
        return "positive"
    if compound <= -0.05:
        return "negative"
    return "neutral"


def score_text(text: str) -> SentimentScore:
    """Score a single text blob with VADER."""
    cleaned = (text or "").strip()
    scores = _get_analyzer().polarity_scores(cleaned)
    compound = float(scores["compound"])
    return SentimentScore(
        text=cleaned,
        compound=compound,
        pos=float(scores["pos"]),
        neu=float(scores["neu"]),
        neg=float(scores["neg"]),
        label=classify_compound(compound),
    )


def score_texts(texts: Sequence[str] | Iterable[str]) -> list[SentimentScore]:
    """Score many texts; empty/whitespace entries become neutral zero scores."""
    return [score_text(t) for t in texts]


def mean_compound(texts: Sequence[str] | Iterable[str]) -> float:
    """Average compound score across texts (0.0 if empty)."""
    scored = score_texts(list(texts))
    if not scored:
        return 0.0
    return sum(s.compound for s in scored) / len(scored)
