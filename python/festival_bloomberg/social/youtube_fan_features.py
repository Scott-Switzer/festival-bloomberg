"""Descriptive, auditable YouTube fan-signal features.

No composite score. Every aggregate carries ``supporting_observation_ids``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median, pstdev
from typing import Any

from ..evidence.provenance import parse_iso
from ..evidence.repository import EvidenceRepository
from ..evidence.semantics import is_fan_role


@dataclass
class CohortFanFeatures:
    cohort: str
    comment_count: int = 0
    unique_public_authors: int = 0
    videos_sampled: int = 0
    positive_share: float | None = None
    neutral_share: float | None = None
    negative_share: float | None = None
    sentiment_mean: float | None = None
    sentiment_dispersion: float | None = None
    comment_like_total: int | None = None
    comment_like_median: float | None = None
    comments_per_video: float | None = None
    recent_comment_velocity: float | None = None
    intent_shares: dict[str, float | None] = field(default_factory=dict)
    chicago_context_video_count: int | None = None
    chicago_context_comment_count: int | None = None
    chicago_context_unique_authors: int | None = None
    supporting_observation_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort": self.cohort,
            "comment_count": self.comment_count,
            "unique_public_authors": self.unique_public_authors,
            "videos_sampled": self.videos_sampled,
            "positive_share": self.positive_share,
            "neutral_share": self.neutral_share,
            "negative_share": self.negative_share,
            "sentiment_mean": self.sentiment_mean,
            "sentiment_dispersion": self.sentiment_dispersion,
            "comment_like_total": self.comment_like_total,
            "comment_like_median": self.comment_like_median,
            "comments_per_video": self.comments_per_video,
            "recent_comment_velocity": self.recent_comment_velocity,
            "intent_shares": self.intent_shares,
            "chicago_context_video_count": self.chicago_context_video_count,
            "chicago_context_comment_count": self.chicago_context_comment_count,
            "chicago_context_unique_authors": self.chicago_context_unique_authors,
            "supporting_observation_ids": self.supporting_observation_ids,
            "warnings": self.warnings,
        }


def build_cohort_fan_features(
    repo: EvidenceRepository,
    *,
    artist_id: str,
    cohort: str,
    correlation_id: str,
    cutoff: datetime | None = None,
    chicago_market_id: str | None = None,
) -> CohortFanFeatures:
    observations = repo.query_observations(
        artist_id=artist_id,
        correlation_id=correlation_id,
        search_cohort=cohort,
        cutoff=cutoff,
    )
    videos = [o for o in observations if not is_fan_role(o.get("content_role"))]
    comments = [o for o in observations if is_fan_role(o.get("content_role"))]
    warnings: list[str] = []
    if not comments:
        warnings.append("zero fan observations")
        return CohortFanFeatures(
            cohort=cohort,
            videos_sampled=len(videos),
            supporting_observation_ids=[o["observation_id"] for o in comments],
            warnings=warnings,
        )

    authors = {o.get("author_public_id") for o in comments if o.get("author_public_id")}
    video_ids = {o.get("parent_object_id") or o.get("platform_object_id") for o in comments}
    video_ids.discard(None)
    likes: list[int] = []
    for comment in comments:
        snapshots = repo.engagement_snapshots(comment["observation_id"])
        if snapshots and snapshots[-1].get("likes") is not None:
            likes.append(int(snapshots[-1]["likes"]))

    labels: list[str] = []
    compounds: list[float] = []
    for comment in comments:
        inferences = repo.latest_inferences(comment["observation_id"], "SENTIMENT")
        if not inferences:
            continue
        latest = inferences[0]
        if latest.get("model_name") != "vader":
            continue
        labels.append(latest.get("label") or "")
        probs = latest.get("probabilities_json") or {}
        compounds.append((probs.get("positive") or 0.0) - (probs.get("negative") or 0.0))

    inferred = len(labels)
    positive_share = sum(1 for label in labels if label == "positive") / inferred if inferred else None
    negative_share = sum(1 for label in labels if label == "negative") / inferred if inferred else None
    neutral_share = sum(1 for label in labels if label == "neutral") / inferred if inferred else None
    if inferred == 0:
        warnings.append("no VADER inferences for fan comments")

    intent_shares: dict[str, float | None] = {}
    for task in ("ATTEND_INTENT", "PURCHASE_INTENT", "PRICE_SENSITIVITY", "RECOMMENDATION_INTENT"):
        scored = 0
        hits = 0
        for comment in comments:
            inferences = repo.latest_inferences(comment["observation_id"], task)
            if not inferences:
                continue
            scored += 1
            if inferences[0].get("label") == task:
                hits += 1
        intent_shares[task] = (hits / scored) if scored else None

    velocity = None
    published_times = [parse_iso(str(o["published_at"])) for o in comments if o.get("published_at")]
    published_times = [t for t in published_times if t is not None]
    if len(published_times) >= 2:
        latest = max(published_times)
        window = latest - timedelta(days=7)
        recent = sum(1 for t in published_times if t >= window)
        velocity = float(recent)

    chicago_video_count = None
    chicago_comment_count = None
    chicago_authors = None
    if chicago_market_id:
        chicago_videos = [o for o in videos if o.get("market_id") == chicago_market_id]
        chicago_comments = [o for o in comments if o.get("market_id") == chicago_market_id]
        chicago_video_count = len(chicago_videos)
        chicago_comment_count = len(chicago_comments)
        chicago_authors = len(
            {o.get("author_public_id") for o in chicago_comments if o.get("author_public_id")}
        )

    return CohortFanFeatures(
        cohort=cohort,
        comment_count=len(comments),
        unique_public_authors=len(authors),
        videos_sampled=len(videos) or len(video_ids),
        positive_share=positive_share,
        neutral_share=neutral_share,
        negative_share=negative_share,
        sentiment_mean=float(mean(compounds)) if compounds else None,
        sentiment_dispersion=float(pstdev(compounds)) if len(compounds) > 1 else None,
        comment_like_total=sum(likes) if likes else (0 if comments else None),
        comment_like_median=float(median(likes)) if likes else None,
        comments_per_video=(len(comments) / len(video_ids)) if video_ids else None,
        recent_comment_velocity=velocity,
        intent_shares=intent_shares,
        chicago_context_video_count=chicago_video_count,
        chicago_context_comment_count=chicago_comment_count,
        chicago_context_unique_authors=chicago_authors,
        supporting_observation_ids=[o["observation_id"] for o in comments],
        warnings=warnings,
    )
