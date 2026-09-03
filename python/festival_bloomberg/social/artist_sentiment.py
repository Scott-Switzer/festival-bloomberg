"""Daily artist sentiment aggregates for the materialized intelligence tape.

This module consumes normalized provider records and returns aggregate rows
only. Usernames, user IDs, post IDs, and raw text never appear in the output.
The selected model and version are stored on every row so a later bakeoff can
be reproduced without relabeling historical aggregates.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .sentiment import SentimentInference, infer_sentiment

DEFAULT_MODEL = "vader"
DEFAULT_MODEL_VERSION = "4.0.0"
SPAM_RE = re.compile(r"(?:https?://\S+\s*){3,}|(.)\1{8,}", re.IGNORECASE)


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value or "")
    if not text:
        return "UNKNOWN"
    return text[:10]


def _text_hash(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _engagement(record: dict[str, Any]) -> int:
    values = record.get("engagement") or {}
    total = 0
    for key in ("likes", "comments", "shares", "reposts", "views"):
        try:
            total += max(0, int(values.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return total


def is_spam(record: dict[str, Any]) -> bool:
    text = str(record.get("text") or "").strip()
    if not text:
        return True
    return bool(SPAM_RE.search(text))


def _topic_distribution(records: list[dict[str, Any]]) -> dict[str, int]:
    topics = Counter()
    for record in records:
        text = str(record.get("text") or "").casefold()
        for topic, terms in {
            "live": ("tour", "concert", "show", "festival", "ticket"),
            "catalog": ("album", "single", "song", "release", "track"),
            "community": ("fan", "love", "amazing", "favorite"),
        }.items():
            if any(term in text for term in terms):
                topics[topic] += 1
    return dict(sorted(topics.items()))


def _inference_score(inference: SentimentInference) -> float:
    probs = inference.probabilities
    return float(probs.get("positive", 0.0)) - float(probs.get("negative", 0.0))


def aggregate_daily_sentiment(
    records: Iterable[dict[str, Any]],
    *,
    source_generation: str,
    model: str = DEFAULT_MODEL,
    model_version: str = DEFAULT_MODEL_VERSION,
    retrieved_at: Any = None,
    knowledge_time: Any = None,
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "PROTOTYPE_ONLY",
    source_scope: str = "SOCIAL_AGGREGATE",
) -> list[dict[str, Any]]:
    """Aggregate normalized social records at artist/platform/day grain.

    Repeated cross-posts are deduplicated by normalized text hash. A record
    with an explicit platform object ID is not needed for the aggregate and is
    deliberately discarded from the result. Spam is excluded before inference.
    """
    retrieved_iso = (
        retrieved_at.isoformat()
        if hasattr(retrieved_at, "isoformat")
        else str(retrieved_at or datetime.now(UTC).isoformat())
    )
    knowledge_iso = (
        knowledge_time.isoformat()
        if hasattr(knowledge_time, "isoformat")
        else str(knowledge_time or retrieved_iso)
    )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    # Cross-platform reposts for one artist/day should count once, but the
    # same text from a different artist must remain an independent mention.
    seen_hashes: dict[tuple[str, str], set[str]] = defaultdict(set)
    dedup_counts: Counter[tuple[str, str, str]] = Counter()
    spam_counts: Counter[tuple[str, str, str]] = Counter()

    for record in records:
        artist_key = str(record.get("artist_key") or "").strip()
        platform = str(record.get("platform") or "unknown").strip().lower()
        day = _date(record.get("published_at") or record.get("observation_time") or retrieved_iso)
        if not artist_key or day == "UNKNOWN":
            continue
        group_key = (artist_key, platform, day)
        if is_spam(record):
            spam_counts[group_key] += 1
            continue
        content_hash = _text_hash(record.get("text"))
        dedup_scope = (artist_key, day)
        if content_hash and content_hash in seen_hashes[dedup_scope]:
            dedup_counts[group_key] += 1
            continue
        if content_hash:
            seen_hashes[dedup_scope].add(content_hash)
        groups[group_key].append(record)

    output: list[dict[str, Any]] = []
    for group_key in sorted(set(groups) | set(spam_counts) | set(dedup_counts)):
        artist_key, platform, day = group_key
        valid = groups.get(group_key, [])
        inferences: list[tuple[dict[str, Any], SentimentInference]] = []
        for record in valid:
            inference = infer_sentiment(str(record.get("text") or ""), model=model)  # type: ignore[arg-type]
            if inference.available:
                inferences.append((record, inference))
        scores = [_inference_score(inference) for _, inference in inferences]
        labels = [inference.label for _, inference in inferences]
        analyzed = len(inferences)
        engagement_total = sum(_engagement(record) for record in valid)
        weighted_denominator = sum(_engagement(record) for record, _ in inferences)
        weighted = (
            sum(
                _inference_score(inference) * _engagement(record)
                for record, inference in inferences
            )
            / weighted_denominator
            if weighted_denominator
            else None
        )
        output.append(
            {
                "observation_key": hashlib.sha256(
                    "|".join((artist_key, platform, day, source_generation)).encode("utf-8")
                ).hexdigest()[:40],
                "artist_key": artist_key,
                "platform": platform,
                "date": day,
                "mention_count": len(valid),
                "analyzed_count": analyzed,
                "positive_share": labels.count("positive") / analyzed if analyzed else None,
                "neutral_share": labels.count("neutral") / analyzed if analyzed else None,
                "negative_share": labels.count("negative") / analyzed if analyzed else None,
                "sentiment_mean": sum(scores) / analyzed if analyzed else None,
                "engagement_weighted_sentiment": weighted,
                "engagement_total": engagement_total or None,
                "topic_distribution": _topic_distribution(valid),
                "language_distribution": dict(
                    sorted(Counter(str(r.get("language") or "unknown") for r in valid).items())
                ),
                "sample_quality": "NO_ELIGIBLE_SAMPLE"
                if not valid
                else ("ANALYZED" if analyzed else "UNANALYZED"),
                "source_generation": source_generation,
                "model_name": model,
                "model_version": model_version,
                "deduplicated_count": dedup_counts[group_key],
                "spam_filtered_count": spam_counts[group_key],
                "source": platform,
                "evidence_ref": None,
                "source_scope": source_scope,
                "rights_status": rights_status,
                "commercial_use_status": commercial_use_status,
                "quality_status": "UNKNOWN" if not valid else "OBSERVED",
                "retrieved_at": retrieved_iso,
                "knowledge_time": knowledge_iso,
            }
        )
    return output


def insert_daily_sentiment(conn: Any, row: dict[str, Any]) -> int:
    """Insert an aggregate immutably; duplicate generation is a no-op."""
    columns = (
        "observation_key",
        "artist_key",
        "platform",
        "date",
        "mention_count",
        "analyzed_count",
        "positive_share",
        "neutral_share",
        "negative_share",
        "sentiment_mean",
        "engagement_weighted_sentiment",
        "engagement_total",
        "topic_distribution",
        "language_distribution",
        "sample_quality",
        "source_generation",
        "model_name",
        "model_version",
        "deduplicated_count",
        "spam_filtered_count",
        "source",
        "evidence_ref",
        "source_scope",
        "rights_status",
        "commercial_use_status",
        "quality_status",
        "retrieved_at",
        "knowledge_time",
    )
    if conn.execute(
        "SELECT 1 FROM metrics.artist_sentiment_observations WHERE observation_key = ?",
        [row["observation_key"]],
    ).fetchone():
        return 0
    values = []
    for column in columns:
        value = row.get(column)
        if column in {"topic_distribution", "language_distribution"}:
            import json

            value = json.dumps(value or {}, sort_keys=True)
        values.append(value)
    conn.execute(
        f"INSERT INTO metrics.artist_sentiment_observations ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        values,
    )
    return 1
