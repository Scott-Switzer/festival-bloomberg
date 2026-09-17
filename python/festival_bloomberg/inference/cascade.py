"""SELF_HOSTED_INFERENCE_V1 — cascade for high-volume cheap tasks.

Do NOT send millions of obvious social posts to an LLM.

  Stage 1 — deterministic filters (language, spam, duplicates, empty)
  Stage 2 — local inexpensive classifier (Hetzner CPU / ONNX / FastEmbed)
  Stage 3 — NIM only for uncertain/high-value cases

Only Stage 3 costs per-request LLM tokens. Stages 1-2 run at marginal CPU
cost. When local confidence >= threshold, the local DERIVED prediction is
accepted; otherwise NIM is escalated to.

This module is the *policy* layer — the actual model runtimes (ONNX,
FastEmbed, llama.cpp server) live on the Hetzner worker image, not in this
process. The cascade is stdlib + deterministic so it can run in the
Cloudflare Worker and in batch jobs with identical semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .router import InferenceLane, InferenceRouter


# ── Deterministic stage ──────────────────────────────────────────────

_URL_ONLY = re.compile(r"^\s*https?://\S+\s*$")
_TOO_SHORT = 8  # characters — shorter text is not evidence


def deterministic_gate(text: str | None) -> tuple[bool, str]:
    """Return (pass, reason). False means discard before any inference."""
    if not text or not text.strip():
        return False, "empty"
    if len(text.strip()) < _TOO_SHORT:
        return False, "too_short"
    if _URL_ONLY.match(text):
        return False, "url_only"
    return True, "ok"


def is_spam_heuristic(text: str) -> tuple[bool, float]:
    """Cheap spam signal — high recall, used to short-circuit obvious spam.

    Returns (is_spam, confidence). Only very-confident spam is dropped here;
    ambiguous cases go to the classifier.
    """
    low = text.lower()
    spam_tokens = ("buy now", "click here", "free money", "earn $", "crypto giveaway", "onlyfans")
    hits = sum(1 for tok in spam_tokens if tok in low)
    if hits >= 2:
        return True, 0.92
    if hits == 1 and len(text) < 40:
        return True, 0.75
    return False, 0.0


@dataclass(frozen=True)
class InferenceResult:
    text: str
    lane: InferenceLane
    label: str | None
    confidence: float | None
    escalated: bool
    model: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value, "label": self.label,
            "confidence": self.confidence, "escalated": self.escalated,
            "model": self.model, "reason": self.reason,
        }


class InferenceCascade:
    """Three-stage cascade: deterministic → local → NIM escalation."""

    def __init__(
        self,
        router: InferenceRouter | None = None,
        *,
        local_confidence_threshold: float = 0.78,
    ) -> None:
        self.router = router or InferenceRouter()
        self.threshold = local_confidence_threshold

    def classify(
        self,
        text: str,
        *,
        task: str = "SENTIMENT",
        # Pluggable local scorer — supplied by the worker that has the model.
        # Signature: (text, task) -> (label, confidence) or None if not loaded.
        local_scorer: Any | None = None,
        # Pluggable NIM caller — supplied by the caller that holds the key.
        # Signature: (text, task) -> (label, confidence, model) or None.
        nim_caller: Any | None = None,
    ) -> InferenceResult:
        # Stage 1: deterministic gate
        ok, reason = deterministic_gate(text)
        if not ok:
            return InferenceResult(text=text, lane=InferenceLane.DETERMINISTIC, label=None, confidence=None, escalated=False, reason=f"deterministic_gate:{reason}")
        spam, conf = is_spam_heuristic(text)
        if spam and conf >= 0.9:
            return InferenceResult(text=text, lane=InferenceLane.DETERMINISTIC, label="SPAM", confidence=conf, escalated=False, reason="spam_heuristic")

        # Stage 2: local classifier
        decision = self.router.decide(task)
        if decision.primary == InferenceLane.HETZNER_CPU and local_scorer is not None:
            try:
                local_out = local_scorer(text, task)
            except Exception:
                local_out = None
            if local_out is not None:
                label, conf = local_out[0], float(local_out[1])
                if conf >= self.threshold:
                    return InferenceResult(text=text, lane=InferenceLane.HETZNER_CPU, label=label, confidence=conf, escalated=False, model=decision.model, reason=f"local_confidence {conf:.2f} >= {self.threshold}")
                # below threshold → escalate

        # Stage 3: NIM (only when deterministic + local did not resolve)
        if decision.primary == InferenceLane.NIM_FREE or decision.fallback == InferenceLane.NIM_FREE:
            if nim_caller is not None:
                try:
                    nim_out = nim_caller(text, task)
                except Exception:
                    nim_out = None
                if nim_out is not None:
                    label, conf, model = nim_out[0], float(nim_out[1]), nim_out[2] if len(nim_out) > 2 else decision.model
                    return InferenceResult(text=text, lane=InferenceLane.NIM_FREE, label=label, confidence=conf, escalated=True, model=model, reason="nim_escalation")
            # NIM lane exists but no caller supplied — report as such without fabricating a label
            return InferenceResult(text=text, lane=InferenceLane.NIM_FREE, label=None, confidence=None, escalated=True, model=decision.model, reason="nim_lane_available_but_no_caller")

        # No lane can serve this request
        return InferenceResult(text=text, lane=InferenceLane.UNAVAILABLE, label=None, confidence=None, escalated=False, reason=decision.reason)

    def embed(
        self,
        texts: list[str],
        *,
        local_embedder: Any | None = None,
        nim_embedder: Any | None = None,
    ) -> dict[str, Any]:
        """Embed via NIM primary, local fallback. Returns {vectors, lane, model}."""
        decision = self.router.decide("EMBED")
        if decision.primary == InferenceLane.NIM_FREE and nim_embedder is not None:
            try:
                vectors = nim_embedder(texts)
                if vectors is not None:
                    return {"vectors": vectors, "lane": InferenceLane.NIM_FREE.value, "model": decision.model}
            except Exception:
                pass
        if local_embedder is not None:
            try:
                vectors = local_embedder(texts)
                if vectors is not None:
                    return {"vectors": vectors, "lane": InferenceLane.HETZNER_CPU.value, "model": decision.fallback or "local"}
            except Exception:
                pass
        return {"vectors": None, "lane": InferenceLane.UNAVAILABLE.value, "reason": decision.reason}
