"""PIT-safe artist x market social feature builder.

Every feature is computed strictly from observations whose ``knowledge_time``
is <= the cutoff (enforced by the evidence repository query) and whose
published time falls inside the requested window. Features are returned as
individual dimensions with their source observation ids — never collapsed
into a single 0-100 score. Missing evidence is reported as ``None`` plus a
warning, never as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any

from ..evidence.repository import EvidenceRepository
from ..evidence.provenance import parse_iso, utc

INTENT_TASKS = ("ATTEND_INTENT", "PURCHASE_INTENT", "PRICE_SENSITIVITY", "RECOMMENDATION_INTENT")


@dataclass(frozen=True)
class ArtistMarketFeatures:
    artist_id: str
    market_id: str | None
    start_time: str | None
    cutoff: str | None

    mention_count: int = 0
    unique_author_count: int = 0
    platform_count: int = 0
    mention_velocity_7d: float | None = None
    mention_velocity_30d: float | None = None

    positive_share: float | None = None
    negative_share: float | None = None
    neutral_share: float | None = None
    sentiment_mean: float | None = None
    sentiment_dispersion: float | None = None

    engagement_total: int = 0
    engagement_per_author: float | None = None
    engagement_velocity_30d: float | None = None
    view_velocity_30d: float | None = None

    local_mention_share: float | None = None
    local_unique_author_count: int = 0

    attend_intent_share: float | None = None
    purchase_intent_share: float | None = None

    topic_distribution: dict = field(default_factory=dict)
    cross_platform_agreement: float | None = None

    source_count: int = 0
    provider_count: int = 0
    evidence_quality: str = "unknown"
    source_observation_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artist_id": self.artist_id,
            "market_id": self.market_id,
            "start_time": self.start_time,
            "cutoff": self.cutoff,
            "mention_count": self.mention_count,
            "unique_author_count": self.unique_author_count,
            "platform_count": self.platform_count,
            "mention_velocity_7d": self.mention_velocity_7d,
            "mention_velocity_30d": self.mention_velocity_30d,
            "positive_share": self.positive_share,
            "negative_share": self.negative_share,
            "neutral_share": self.neutral_share,
            "sentiment_mean": self.sentiment_mean,
            "sentiment_dispersion": self.sentiment_dispersion,
            "engagement_total": self.engagement_total,
            "engagement_per_author": self.engagement_per_author,
            "engagement_velocity_30d": self.engagement_velocity_30d,
            "view_velocity_30d": self.view_velocity_30d,
            "local_mention_share": self.local_mention_share,
            "local_unique_author_count": self.local_unique_author_count,
            "attend_intent_share": self.attend_intent_share,
            "purchase_intent_share": self.purchase_intent_share,
            "topic_distribution": self.topic_distribution,
            "cross_platform_agreement": self.cross_platform_agreement,
            "source_count": self.source_count,
            "provider_count": self.provider_count,
            "evidence_quality": self.evidence_quality,
            "source_observation_ids": self.source_observation_ids,
            "warnings": self.warnings,
        }


def _as_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return utc(value)
    return parse_iso(value)


def build_artist_market_features(
    repo: EvidenceRepository,
    artist_id: str,
    market_id: str | None = None,
    start_time: datetime | str | None = None,
    cutoff: datetime | str | None = None,
) -> ArtistMarketFeatures:
    """Compute artist x market social features at a knowledge cutoff."""
    cutoff_dt = _as_dt(cutoff) or utc(datetime.now(timezone.utc))
    start_dt = _as_dt(start_time)

    # Observations are NOT market-filtered in SQL: local_mention_share is the
    # share of the artist's observations tagged with the target market.
    observations = repo.query_observations(
        artist_id=artist_id,
        start_time=start_dt,
        cutoff=cutoff_dt,
    )
    warnings: list[str] = []

    mention_count = len(observations)
    unique_authors = {o["author_public_id"] for o in observations if o["author_public_id"]}
    platforms = {o["platform"] for o in observations}
    obs_ids = [o["observation_id"] for o in observations]

    # velocities from published_at within trailing windows relative to cutoff
    def published(o: dict) -> datetime | None:
        return _as_dt(o["published_at"])

    def count_since(days: int) -> int:
        boundary = cutoff_dt - timedelta(days=days)
        return sum(1 for o in observations if (p := published(o)) is not None and p >= boundary)

    count_7d = count_since(7)
    count_30d = count_since(30)

    # sentiment aggregation over versioned VADER inferences
    sentiment_values: list[float] = []
    sent_labels: list[str] = []
    inferred_count = 0
    for o in observations:
        inferences = repo.latest_inferences(o["observation_id"], "SENTIMENT")
        if not inferences:
            continue
        latest = inferences[0]
        if latest["model_name"] != "vader":
            continue
        probs = latest.get("probabilities_json") or {}
        compound = (probs.get("positive") or 0.0) - (probs.get("negative") or 0.0)
        sentiment_values.append(compound)
        sent_labels.append(latest.get("label") or "")
        inferred_count += 1
    if not sentiment_values:
        warnings.append("no VADER sentiment inferences available for observations at cutoff")
    positive_share = (
        sum(1 for label in sent_labels if label == "positive") / inferred_count
        if inferred_count
        else None
    )
    negative_share = (
        sum(1 for label in sent_labels if label == "negative") / inferred_count
        if inferred_count
        else None
    )
    neutral_share = (
        sum(1 for label in sent_labels if label == "neutral") / inferred_count
        if inferred_count
        else None
    )

    # intent shares
    def intent_share(task: str) -> float | None:
        hits = 0
        scored = 0
        for o in observations:
            inferences = repo.latest_inferences(o["observation_id"], task)
            if not inferences:
                continue
            scored += 1
            if inferences[0].get("label") == task:
                hits += 1
        if scored == 0:
            warnings.append(f"no {task} inferences available at cutoff")
            return None
        return hits / scored

    attend_share = intent_share("ATTEND_INTENT")
    purchase_share = intent_share("PURCHASE_INTENT")

    # engagement: latest snapshot per observation (never mixing providers),
    # split into trailing-window velocity vs. total at cutoff.
    def engagement_for(window_days: int | None) -> tuple[int, int]:
        eng = 0
        views = 0
        for o in observations:
            p = published(o)
            if window_days is not None and (p is None or p < cutoff_dt - timedelta(days=window_days)):
                continue
            snapshots = repo.engagement_snapshots(o["observation_id"])
            if not snapshots:
                continue
            latest = snapshots[-1]
            eng += int(latest["likes"] or 0) + int(latest["comments"] or 0) + int(latest["shares"] or 0) + int(latest["reposts"] or 0)
            views += int(latest["views"] or 0)
        return eng, views

    engagement_total, _ = engagement_for(None)
    engagement_30d, views_30d = engagement_for(30)
    engagement_per_author: float | None = None
    if unique_authors:
        engagement_per_author = engagement_total / len(unique_authors)

    # local market share
    local_share: float | None = None
    local_unique: int = 0
    if market_id:
        local = [o for o in observations if o.get("market_id") == market_id]
        local_share = len(local) / mention_count if mention_count else None
        local_unique = len({o["author_public_id"] for o in local if o["author_public_id"]})

    # cross-platform sentiment agreement
    agreement: float | None = None
    if len(platforms) > 1 and sent_labels:
        agreement = _cross_platform_agreement(repo, observations, platforms)

    # evidence quality heuristic (explicit, not a score to sell)
    quality = "unknown"
    if mention_count > 0:
        coverage = inferred_count / mention_count if mention_count else 0.0
        if len(platforms) >= 2 and coverage >= 0.5 and len(providers(repo, obs_ids)) >= 2:
            quality = "high"
        elif mention_count > 0 and (coverage > 0 or len(platforms) >= 1):
            quality = "medium"
        else:
            quality = "low"

    if market_id and local_share is None:
        warnings.append("market_id filter produced no market-tagged observations")

    return ArtistMarketFeatures(
        artist_id=artist_id,
        market_id=market_id,
        start_time=start_dt.isoformat() if start_dt else None,
        cutoff=cutoff_dt.isoformat(),
        mention_count=mention_count,
        unique_author_count=len(unique_authors),
        platform_count=len(platforms),
        mention_velocity_7d=float(count_7d),
        mention_velocity_30d=float(count_30d),
        positive_share=positive_share,
        negative_share=negative_share,
        neutral_share=neutral_share,
        sentiment_mean=float(mean(sentiment_values)) if sentiment_values else None,
        sentiment_dispersion=float(pstdev(sentiment_values)) if len(sentiment_values) > 1 else None,
        engagement_total=engagement_total,
        engagement_per_author=engagement_per_author,
        engagement_velocity_30d=float(engagement_30d),
        view_velocity_30d=float(views_30d),
        local_mention_share=local_share,
        local_unique_author_count=local_unique,
        attend_intent_share=attend_share,
        purchase_intent_share=purchase_share,
        topic_distribution={},
        cross_platform_agreement=agreement,
        source_count=len({o["source_count"] for o in observations if o.get("source_count")}),
        provider_count=len(providers(repo, obs_ids)),
        evidence_quality=quality,
        source_observation_ids=obs_ids,
        warnings=warnings,
    )


def providers(repo: EvidenceRepository, observation_ids: list[str]) -> set[str]:
    if not observation_ids:
        return set()
    rows = repo.conn.execute(
        """
        SELECT DISTINCT provider
        FROM acquisition.raw_observations
        WHERE canonical_observation_id IN ({})
        """.format(",".join("?" * len(observation_ids))),
        list(observation_ids),
    ).fetchall()
    return {row[0] for row in rows}


def _cross_platform_agreement(
    repo: EvidenceRepository, observations: list[dict], platforms: set[str]
) -> float | None:
    """Fraction of platforms whose mean compound sign matches the overall mode."""
    platform_values: dict[str, list[float]] = {p: [] for p in platforms}
    for o in observations:
        inferences = repo.latest_inferences(o["observation_id"], "SENTIMENT")
        if not inferences:
            continue
        probs = inferences[0].get("probabilities_json") or {}
        platform_values.setdefault(o["platform"], []).append(
            (probs.get("positive") or 0.0) - (probs.get("negative") or 0.0)
        )
    populated = {p: vals for p, vals in platform_values.items() if vals}
    if not populated:
        return None
    overall = mean([v for vals in populated.values() for v in vals])
    overall_sign = 1 if overall > 0.05 else (-1 if overall < -0.05 else 0)
    agreeing = 0
    for vals in populated.values():
        platform_sign = 1 if mean(vals) > 0.05 else (-1 if mean(vals) < -0.05 else 0)
        if platform_sign == overall_sign:
            agreeing += 1
    return agreeing / len(populated)
