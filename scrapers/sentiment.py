"""Sentiment analysis for the scraper ensemble.

Primary analyzer is **VADER** (Valence Aware Dictionary and sEntiment Reasoner):
a lexicon + rule-based model that is *offline, deterministic, requires no API
key*, and works well on short social/news text. This keeps the platform free
and zero-maintenance per project constraints.

Optionally, if a ``NVIDIA_API_KEY`` (or ``OPENAI_API_KEY``) is present, the
``LLMSentimentSummarizer`` can produce a short qualitative prose summary from
the collected texts — but the numeric sentiment always comes from VADER so the
system degrades gracefully without any key.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from scrapers.contracts import SentimentBreakdown

logger = logging.getLogger(__name__)


# Words that, when adjacent to an artist mention, help infer themes/topics.
_TOPIC_HINTS = {
    "tour": ["tour", "live", "show", "concert", "festival", "stage", "headline"],
    "new music": ["album", "single", "ep", "track", "song", "record", "release"],
    "nostalgia": ["classic", "legend", "vintage", "90s", "2000s", "comeback"],
    "controversy": ["lawsuit", "feud", "controversy", "cancel", "backlash", "split"],
    "collaboration": ["feat", "collab", "duet", "remix", "with"],
    "growth": ["sold out", "number one", "chart", "breakout", "rising", "buzz"],
}


class SentimentAnalyzer:
    """VADER-based sentiment scoring with topic/theme extraction."""

    def __init__(self) -> None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            self._vader = SentimentIntensityAnalyzer()
            self._available = True
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("VADER unavailable (%s); sentiment will be neutral.", e)
            self._vader = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def score_text(self, text: str) -> float:
        """Return VADER compound score in [-1, 1] for a single text."""
        if not self._available or not text:
            return 0.0
        return float(self._vader.polarity_scores(text)["compound"])

    def analyze(self, texts: List[str], top_n: int = 3) -> SentimentBreakdown:
        """Aggregate sentiment over a list of texts."""
        texts = [t for t in (texts or []) if t and t.strip()]
        if not texts:
            return SentimentBreakdown()

        compounds: List[float] = []
        buckets = {"pos": 0, "neu": 0, "neg": 0}
        scored: List[Tuple[float, str]] = []
        for t in texts:
            c = self.score_text(t)
            compounds.append(c)
            scored.append((c, t))
            if c >= 0.05:
                buckets["pos"] += 1
            elif c <= -0.05:
                buckets["neg"] += 1
            else:
                buckets["neu"] += 1

        n = len(compounds)
        avg_compound = sum(compounds) / n if n else 0.0
        pos = buckets["pos"] / n if n else 0.0
        neu = buckets["neu"] / n if n else 0.0
        neg = buckets["neg"] / n if n else 0.0

        scored.sort(key=lambda x: x[0])
        top_negative = [t for _, t in scored[:top_n] if _ < -0.05]
        top_positive = [t for _, t in scored[-top_n:][::-1] if _ > 0.05]

        return SentimentBreakdown(
            positive=round(pos, 4),
            neutral=round(neu, 4),
            negative=round(neg, 4),
            compound=round(avg_compound, 4),
            sample_size=n,
            top_positive=top_positive,
            top_negative=top_negative,
        )

    @staticmethod
    def label(compound: float, neg_ratio: float = 0.0) -> str:
        """Map a compound score to a coarse label."""
        if compound >= 0.35:
            return "positive"
        if compound <= -0.35:
            return "negative"
        if neg_ratio >= 0.4 and compound < 0.05:
            return "mixed"
        return "neutral"

    @staticmethod
    def extract_topics(texts: List[str], top_n: int = 5) -> List[str]:
        """Lightweight theme tagging from keyword hints across texts."""
        texts_l = [t.lower() for t in (texts or [])]
        blob = " \n ".join(texts_l)
        hits: Counter = Counter()
        for topic, kws in _TOPIC_HINTS.items():
            for kw in kws:
                if re.search(rf"\b{re.escape(kw)}\b", blob):
                    hits[topic] += 1
        return [t for t, _ in hits.most_common(top_n)]


class LLMSentimentSummarizer:
    """Optional qualitative summary via NVIDIA/OpenAI (key-gated).

    Returns ``None`` when no key is configured so callers can degrade to the
    purely offline VADER pipeline.
    """

    def __init__(self) -> None:
        self._extractor = None
        try:
            import os
            from extraction.llm_extractor import LLMExtractor, ExtractionModel
            api_key = os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.info("LLM summarizer unavailable (no NVIDIA/OPENAI API key).")
                return
            # Prefer the free NVIDIA model when a key is present.
            model = ExtractionModel.NVIDIA_MIXTRAL_8x7B if os.getenv("NVIDIA_API_KEY") \
                else ExtractionModel.GPT4O_MINI
            self._extractor = LLMExtractor(api_key=api_key, model=model)
        except Exception as e:  # pragma: no cover - key/path may be absent
            logger.info("LLM summarizer unavailable: %s", e)
            self._extractor = None

    @property
    def available(self) -> bool:
        return self._extractor is not None

    def summarize(self, artist_name: str, texts: List[str], max_chars: int = 4000) -> Optional[str]:
        if not self.available or not texts:
            return None
        excerpt = "\n".join(f"- {t[:300]}" for t in texts[:25])
        excerpt = excerpt[:max_chars]
        prompt = (
            f"You are a music industry analyst. Based ONLY on the following "
            f"public mentions about the artist '{artist_name}', write a 2-3 "
            f"sentence neutral summary of public sentiment and notable themes. "
            f"Do not invent facts.\n\n{excerpt}"
        )
        try:
            # LLMExtractor stores its OpenAI client as `self._client`.
            resp = self._extractor._client.chat.completions.create(
                model=self._extractor.model.value,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=160,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:  # pragma: no cover
            logger.warning("LLM summary failed: %s", e)
            return None
