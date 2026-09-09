"""INFERENCE_ROUTER_V1 — lane selection for text inference.

Lanes (in priority order when cost is equal):
  DETERMINISTIC → NIM_FREE → HETZNER_CPU → EXTERNAL_PAID

The router itself is pure logic (no network). It consults:
  - NIM health (available / rate-limited / unavailable) from the live probe,
  - local model availability (loaded / not loaded),
  - per-task quality requirements (e.g. embeddings need recall, not just latency).

Callers request a task; the router returns a lane + the model/runtime to use.
Fail-closed: if no lane is available, the caller should skip inference rather
than invent a result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class InferenceLane(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    NIM_FREE = "NIM_FREE"
    HETZNER_CPU = "HETZNER_CPU"
    HETZNER_GPU_FUTURE = "HETZNER_GPU_FUTURE"
    EXTERNAL_PAID = "EXTERNAL_PAID"
    UNAVAILABLE = "UNAVAILABLE"


# Task → preferred lanes (ordered). The router still filters by availability.
TASK_LANE_PRIORITY: dict[str, list[InferenceLane]] = {
    "LANGUAGE_ID": [InferenceLane.DETERMINISTIC, InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE],
    "SPAM": [InferenceLane.DETERMINISTIC, InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE],
    "SENTIMENT": [InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE, InferenceLane.DETERMINISTIC],
    "EMOTION": [InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE],
    "EMBED": [InferenceLane.NIM_FREE, InferenceLane.HETZNER_CPU],
    "RERANK": [InferenceLane.NIM_FREE, InferenceLane.HETZNER_CPU],
    "TOPIC": [InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE],
    "INTENT": [InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE],
    "NER": [InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE],
    "FAST_EXTRACT": [InferenceLane.NIM_FREE, InferenceLane.HETZNER_CPU],
    "CATALYST_EXTRACT": [InferenceLane.NIM_FREE, InferenceLane.HETZNER_CPU],
    "ENTITY_DISAMBIGUATION": [InferenceLane.HETZNER_CPU, InferenceLane.NIM_FREE],
}


@dataclass(frozen=True)
class LaneHealth:
    lane: InferenceLane
    available: bool
    reason: str | None = None
    model: str | None = None
    latency_ms_p50: int | None = None


@dataclass(frozen=True)
class InferenceDecision:
    task: str
    primary: InferenceLane
    fallback: InferenceLane | None
    model: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "primary": self.primary.value,
            "fallback": self.fallback.value if self.fallback else None,
            "model": self.model,
            "reason": self.reason,
        }


class InferenceRouter:
    """Pure-logic lane router — no I/O."""

    def __init__(
        self,
        *,
        nim_available: bool = True,
        nim_model: str | None = None,
        hetzner_cpu_available: bool = False,
        hetzner_cpu_model: str | None = None,
        deterministic_available: bool = True,
    ) -> None:
        self.nim_available = nim_available
        self.nim_model = nim_model
        self.hetzner_cpu_available = hetzner_cpu_available
        self.hetzner_cpu_model = hetzner_cpu_model
        self.deterministic_available = deterministic_available

    def _lane_health(self, lane: InferenceLane, task: str) -> LaneHealth:
        if lane == InferenceLane.DETERMINISTIC:
            # Only valid for a small set of tasks (language/spam/dedupe)
            ok = self.deterministic_available and task in ("LANGUAGE_ID", "SPAM", "SENTIMENT")
            return LaneHealth(lane=lane, available=ok, reason=None if ok else "deterministic not applicable for task")
        if lane == InferenceLane.NIM_FREE:
            return LaneHealth(
                lane=lane,
                available=self.nim_available,
                reason=None if self.nim_available else "NIM unavailable or rate-limited",
                model=self.nim_model,
            )
        if lane == InferenceLane.HETZNER_CPU:
            return LaneHealth(
                lane=lane,
                available=self.hetzner_cpu_available,
                reason=None if self.hetzner_cpu_available else "no local CPU model loaded",
                model=self.hetzner_cpu_model,
            )
        if lane == InferenceLane.HETZNER_GPU_FUTURE:
            return LaneHealth(lane=lane, available=False, reason="GPU lane not yet provisioned (GEX45 BLOCKED_BY_ROBOT_CREDENTIAL)")
        if lane == InferenceLane.EXTERNAL_PAID:
            return LaneHealth(lane=lane, available=False, reason="no external paid lane configured for pilot")
        return LaneHealth(lane=lane, available=False, reason="unknown lane")

    def decide(self, task: str) -> InferenceDecision:
        t = (task or "FAST_EXTRACT").upper()
        ordered = TASK_LANE_PRIORITY.get(t, [InferenceLane.NIM_FREE, InferenceLane.HETZNER_CPU, InferenceLane.DETERMINISTIC])
        ranked: list[LaneHealth] = [self._lane_health(l, t) for l in ordered]
        viable = [h for h in ranked if h.available]
        if viable:
            primary = viable[0]
            fallback = viable[1] if len(viable) > 1 else None
            return InferenceDecision(
                task=t, primary=primary.lane, fallback=fallback.lane if fallback else None,
                model=primary.model,
                reason=f"inference router: task={t} primary={primary.lane.value} fallback={fallback.lane.value if fallback else 'none'} nim={'ok' if self.nim_available else 'down'} hetzner_cpu={'ok' if self.hetzner_cpu_available else 'absent'}",
            )
        # Fail closed — no lane can serve this task.
        return InferenceDecision(task=t, primary=InferenceLane.UNAVAILABLE, fallback=None, model=None, reason=f"no lane available for {t} (nim={'ok' if self.nim_available else 'down'}, hetzner_cpu={'ok' if self.hetzner_cpu_available else 'absent'})")
