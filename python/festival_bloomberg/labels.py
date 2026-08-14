"""Deterministic human-labeling export for fan-generated text.

No label is ever fabricated: every ``manual_*`` field is ``None``. Sampling is
a stable SHA-256 ranking of ``observation_id`` so the same evidence yields the
same sample across runs (no cherry-picking). Only ``FAN_GENERATED`` /
``FORUM_DISCUSSION`` observations are eligible.
"""

from __future__ import annotations

import hashlib
from typing import Any

MANUAL_FIELDS = (
    "manual_sentiment",
    "manual_attendance_intent",
    "manual_purchase_intent",
    "manual_price_sensitivity",
    "manual_recommendation_intent",
)


def stable_sample(observation_ids: list[str], sample_size: int) -> list[str]:
    """Deterministic sample: sort by sha256(observation_id), take the first N."""
    if sample_size <= 0 or not observation_ids:
        return []
    ranked = sorted(
        observation_ids,
        key=lambda oid: hashlib.sha256(oid.encode("utf-8")).hexdigest(),
    )
    return ranked[:sample_size]


def export_fan_text(
    evidence,
    *,
    artist_id: str,
    market_id: str | None = None,
    cutoff=None,
    sample_size: int = 100,
) -> list[dict[str, Any]]:
    """Return a deterministic labeling sample of fan-generated observations."""
    from .evidence.semantics import is_fan_role

    observations = evidence.query_observations(
        artist_id=artist_id,
        market_id=market_id,
        cutoff=cutoff,
    )
    fan = [o for o in observations if is_fan_role(o.get("content_role"))]
    ids = stable_sample([o["observation_id"] for o in fan], sample_size)
    by_id = {o["observation_id"]: o for o in fan}

    rows: list[dict[str, Any]] = []
    for oid in ids:
        obs = by_id[oid]
        row: dict[str, Any] = {
            "observation_id": obs["observation_id"],
            "text": obs.get("text"),
            "platform": obs.get("platform"),
            "published_at": obs.get("published_at"),
            "market_id": obs.get("market_id"),
            "content_role": obs.get("content_role"),
        }
        for field in MANUAL_FIELDS:
            row[field] = None
        rows.append(row)
    return rows
