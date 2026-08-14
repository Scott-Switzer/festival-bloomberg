"""Experimental purchase / attendance intent baselines.

These keyword heuristics are research baselines ONLY and are explicitly
labeled ``EXPERIMENTAL_HEURISTIC_NOT_VALIDATED``. They are stored as
separate, versioned inference records and MUST NOT drive financial
recommendations until validated on live-entertainment-specific labeled data.
"""

from __future__ import annotations

import re

MODEL_NAME = "intent-keyword-baseline"
MODEL_VERSION = "EXPERIMENTAL_HEURISTIC_NOT_VALIDATED-v1"

#: task -> intent-trigger patterns
_PATTERNS: dict[str, tuple[str, ...]] = {
    "ATTEND_INTENT": (
        r"\bbought?\s+(a\s+)?ticket",
        r"\bgot?\s+my\s+ticket",
        r"\btickets?\s+secured",
        r"\bsee\s+(him|her|them|this)\s+live",
        r"\bcatch\s+(him|her|them)\b",
        r"\bgoing\s+to\s+the\s+(show|gig|concert|festival)",
        r"\bcan'?t\s+wait\s+to\s+(see|watch|hear)",
        r"\bwho'?s\s+going\b",
        r"\bim\s+going\b",
        r"\bfomo\b",
    ),
    "PURCHASE_INTENT": (
        r"\bbought?\s+ticket",
        r"\bcopped?\s+ticket",
        r"\bticket\s+prices?\b",
        r"\bhow\s+much\s+(are|is)\s+(the\s+)?tickets?",
        r"\bpresale\b",
        r"\bon\s*sale\b",
        r"\bface\s+value\b",
        r"\bresale\b",
        r"\bscalpers?\b",
    ),
    "PRICE_SENSITIVITY": (
        r"\btoo\s+expensive\b",
        r"\boverpriced\b",
        r"\bcan'?t\s+afford\b",
        r"\bexpensive\b",
        r"\bcheap\b",
        r"\bprices?\s+are\s+(crazy|insane|high|low)",
        r"\bworth\s+it\b",
    ),
    "RECOMMENDATION_INTENT": (
        r"\byou\s+should\s+(see|go|check)",
        r"\bdon'?t\s+miss\b",
        r"\bmust\s+see\b",
        r"\bhighly\s+recommend\b",
        r"\bgo\s+see\b",
        r"\bcheck\s+(him|her|them|this)\s+out\b",
    ),
}


def heuristic_intent(text: str, task: str) -> dict:
    """Classify one task for one text; returns label + hits + probability.

    Output is a research-baseline record: ``label`` is either the task name
    (evidence of the intent) or ``"no_signal"``. A probability is not
    meaningful for a keyword baseline, so it is reported as ``None`` rather
    than a fabricated 0.5.
    """
    lowered = (text or "").lower()
    hits: list[str] = []
    for pattern in _PATTERNS.get(task, ()):
        if re.search(pattern, lowered):
            hits.append(pattern)
    if hits:
        return {
            "task": task,
            "model_name": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "label": task,
            "hits": hits,
            "probability": None,
        }
    return {
        "task": task,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "label": "no_signal",
        "hits": [],
        "probability": None,
    }


INTENT_TASKS = tuple(_PATTERNS.keys())


def all_intent_heuristics(text: str) -> list[dict]:
    """Run every intent task; returns records suitable for storage."""
    return [heuristic_intent(text, task) for task in INTENT_TASKS]
