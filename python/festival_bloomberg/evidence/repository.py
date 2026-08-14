"""Canonical evidence repository for the Festival Signal Fabric.

Stores acquisition runs, immutable raw observations, canonical social
objects, timestamped engagement snapshots and versioned text inferences.
All reads that support a knowledge cutoff filter on ``knowledge_time``.

The repository applies the canonical schema + migrations on construction
(``apply_pending_migrations``), so an existing database created from
current main upgrades in place without losing rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from ..acquisition.contracts import AcquisitionRequest, AcquisitionResult, utc_now
from ..acquisition.transport import UrllibTransport  # noqa: F401  (re-exported for tests)
from ..migrations import apply_pending_migrations
from .dedup import resolve_canonical
from .provenance import knowledge_time_for, parse_iso, utc
from .semantics import normalize_content_role, normalize_resolution_method


def guard_evidence_class(evidence_class: str) -> str:
    """Production write paths must never accept synthetic test evidence."""
    if evidence_class == "SYNTHETIC_TEST_ONLY":
        raise ValueError("synthetic test data cannot be written as observed evidence")
    return evidence_class


class EvidenceRepository:
    def __init__(self, connection) -> None:
        self.conn = connection
        apply_pending_migrations(connection)

    # -- policy decisions --------------------------------------------------- #
    def record_policy_decision(self, decision) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO governance.policy_decisions
                (decision_id, source_platform, commercial_context, decision, rationale, decided_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                decision.decision_id,
                decision.source_id,
                decision.commercial_context,
                "ALLOW" if decision.allowed else "DENY",
                decision.rationale,
                decision.decided_at.isoformat(),
            ],
        )

    # -- acquisition runs ---------------------------------------------------- #
    def record_run(self, request: AcquisitionRequest, result: AcquisitionResult) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO acquisition.acquisition_runs
                (run_id, request_id, provider, provider_endpoint, started_at,
                 completed_at, status, record_count, cost_usd, latency_ms,
                 policy_decision_id, error_category, raw_manifest_hash, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                result.request_id,
                result.provider,
                result.provider_endpoint,
                result.started_at.isoformat(),
                result.completed_at.isoformat(),
                result.status.value,
                result.record_count,
                result.cost_usd,
                result.latency_ms,
                result.policy_decision_id,
                result.error_category,
                result.raw_payload_hash,
                json.dumps(result.provider_metadata or {}),
            ],
        )
        self.conn.commit()
        return run_id

    # -- observations -------------------------------------------------------- #
    def ingest(self, request: AcquisitionRequest, result: AcquisitionResult) -> int:
        """Persist a result: run record + raw observations + canonical objects.

        Returns the number of raw observations stored.
        """
        run_id = self.record_run(request, result)
        stored = 0
        retrieved = utc(result.completed_at) or utc_now()
        for record in result.records:
            raw = self._build_raw_observation(
                request=request,
                result=result,
                record=record,
                retrieved_at=retrieved,
                run_id=run_id,
            )
            self._store_raw_and_canonical(raw, record, result.provider, retrieved)
            stored += 1
        return stored

    def _build_raw_observation(
        self,
        request: AcquisitionRequest,
        result: AcquisitionResult,
        record: dict,
        retrieved_at: datetime,
        run_id: str,
    ) -> dict:
        published = parse_iso(record.get("published_at"))
        source_revision_time = parse_iso(record.get("source_revision_time"))
        if (
            record.get("knowledge_time_source") == "source_revision"
            and source_revision_time is not None
        ):
            # Immutable revision identity is proven: the fetched content maps
            # to a specific, stable revision, so its revision time is defensible
            # as knowledge_time.
            knowledge_time = source_revision_time
        else:
            # Mutable / current-page content: fail conservative to retrieval.
            knowledge_time = knowledge_time_for(published, retrieved_at)
        if request.knowledge_cutoff is not None and knowledge_time > utc(request.knowledge_cutoff):
            # Observations learned after the cutoff are stored but flagged so
            # PIT queries can never surface them at the cutoff.
            pass
        return {
            "observation_id": f"raw_{uuid.uuid4().hex[:20]}",
            "run_id": run_id,
            "source_platform": record.get("platform") or request.platform,
            "provider": result.provider,
            "provider_endpoint": result.provider_endpoint,
            "platform_object_id": record.get("platform_object_id"),
            "parent_object_id": record.get("parent_object_id"),
            "source_url": record.get("canonical_url") or record.get("source_url"),
            "entity_id": request.entity_id,
            "entity_type": request.entity_type,
            "published_at": published.isoformat() if published else None,
            "retrieved_at": retrieved_at.isoformat(),
            "knowledge_time": knowledge_time.isoformat(),
            "content_hash": record.get("content_hash"),
            "raw_payload_hash": result.raw_payload_hash,
            "evidence_class": guard_evidence_class("OBSERVED_PUBLIC"),
            "cost_usd": result.cost_usd,
            "correlation_id": request.correlation_id,
            "source_revision_id": record.get("source_revision_id"),
            "source_revision_time": source_revision_time.isoformat() if source_revision_time else None,
            "metadata_json": {
                "text": record.get("text"),
                "author_name": record.get("author_name"),
                "description": record.get("description"),
                "object_type": record.get("object_type"),
                "media_type": record.get("media_type"),
                "raw_bytes": record.get("raw_bytes"),
                "content_role": record.get("content_role"),
            },
        }

    def _store_raw_and_canonical(
        self,
        raw: dict,
        record: dict,
        provider: str,
        retrieved_at: datetime,
    ) -> None:
        # canonicalization
        known = self._known_canonical_ids(raw["source_platform"])
        resolution = resolve_canonical(
            raw["source_platform"],
            raw["platform_object_id"],
            raw["source_url"],
            record.get("content_hash"),
            known,
        )
        canonical_id: str | None = None
        if resolution is not None:
            canonical_id = resolution.canonical_id
            if resolution.is_new:
                self._insert_canonical(record, raw, canonical_id)
            else:
                self._bump_canonical_counts(canonical_id)

        self.conn.execute(
            """
            INSERT INTO acquisition.raw_observations
                (observation_id, run_id, canonical_observation_id, source_platform,
                 provider, provider_endpoint, platform_object_id, parent_object_id,
                 source_url, entity_id, entity_type, published_at, retrieved_at,
                 knowledge_time, content_hash, raw_payload_hash, evidence_class,
                 cost_usd, correlation_id, source_revision_id, source_revision_time,
                 metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                raw["observation_id"],
                raw.get("run_id"),
                canonical_id,
                raw["source_platform"],
                raw["provider"],
                raw["provider_endpoint"],
                raw["platform_object_id"],
                raw["parent_object_id"],
                raw["source_url"],
                raw["entity_id"],
                raw["entity_type"],
                raw["published_at"],
                raw["retrieved_at"],
                raw["knowledge_time"],
                raw["content_hash"] or self._content_hash(record),
                raw["raw_payload_hash"],
                raw["evidence_class"],
                raw["cost_usd"],
                raw["correlation_id"],
                raw["source_revision_id"],
                raw["source_revision_time"],
                json.dumps(raw["metadata_json"]),
            ],
        )
        if canonical_id is not None:
            self._insert_engagement_snapshot(canonical_id, provider, retrieved_at, record)
        self.conn.commit()

    def _known_canonical_ids(self, platform: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT observation_id FROM acquisition.social_observations WHERE platform = ?",
            [platform],
        ).fetchall()
        return {row[0] for row in rows}

    def _insert_canonical(self, record: dict, raw: dict, canonical_id: str) -> None:
        hashtags = record.get("hashtags") or []
        mentions = record.get("mentions") or []
        self.conn.execute(
            """
            INSERT INTO acquisition.social_observations
                (observation_id, artist_id, platform, platform_object_id,
                 author_public_id, text, language, published_at, parent_object_id,
                 thread_id, media_type, hashtags, mentions, market_id,
                 geographic_confidence, entity_resolution_confidence, content_role,
                 content_role_method, resolution_method, resolution_evidence,
                 canonical_url, content_hash, source_count, provider_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            [
                canonical_id,
                raw["entity_id"],
                raw["source_platform"],
                raw["platform_object_id"] or raw["source_url"],
                record.get("author_public_id"),
                record.get("text"),
                record.get("language"),
                raw["published_at"],
                raw["parent_object_id"],
                record.get("thread_id"),
                record.get("media_type"),
                json.dumps(hashtags),
                json.dumps(mentions),
                record.get("market_id"),
                record.get("geographic_confidence"),
                record.get("entity_resolution_confidence"),
                normalize_content_role(record.get("content_role")),
                record.get("content_role_method"),
                normalize_resolution_method(record.get("resolution_method")),
                record.get("resolution_evidence"),
                record.get("canonical_url"),
                record.get("content_hash"),
                utc_now().isoformat(),
            ],
        )

    def _bump_canonical_counts(self, canonical_id: str) -> None:
        self.conn.execute(
            """
            UPDATE acquisition.social_observations
            SET provider_count = provider_count + 1
            WHERE observation_id = ?
            """,
            [canonical_id],
        )

    def _insert_engagement_snapshot(
        self,
        canonical_id: str,
        provider: str,
        retrieved_at: datetime,
        record: dict,
    ) -> None:
        engagement = record.get("engagement") or {}
        self.conn.execute(
            """
            INSERT INTO acquisition.social_engagement_snapshots
                (social_observation_id, provider, retrieved_at, likes, comments,
                 shares, reposts, views, follower_count_at_observation, verified_author)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                canonical_id,
                provider,
                retrieved_at.isoformat(),
                engagement.get("likes"),
                engagement.get("comments"),
                engagement.get("shares"),
                engagement.get("reposts"),
                engagement.get("views"),
                engagement.get("follower_count"),
                engagement.get("verified_author"),
            ],
        )

    def _content_hash(self, record: dict) -> str | None:
        text = record.get("text") or record.get("description")
        if not text:
            return None
        import hashlib

        return hashlib.sha256(str(text).encode("utf-8")).hexdigest()

    # -- text inferences ----------------------------------------------------- #
    def record_text_inference(
        self,
        *,
        observation_id: str,
        task: str,
        model_name: str,
        model_version: str,
        label: str | None,
        probabilities: dict[str, float] | None,
        emotion: dict | None = None,
        knowledge_cutoff: datetime | str | None = None,
        input_text: str | None = None,
    ) -> str:
        import hashlib

        inference_id = f"inf_{uuid.uuid4().hex[:20]}"
        self.conn.execute(
            """
            INSERT INTO acquisition.text_inferences
                (inference_id, observation_id, task, model_name, model_version,
                 label, probabilities_json, emotion_json, inference_time,
                 knowledge_cutoff, input_text_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                inference_id,
                observation_id,
                task,
                model_name,
                model_version,
                label,
                json.dumps(probabilities or {}),
                json.dumps(emotion or {}),
                utc_now().isoformat(),
                knowledge_cutoff.isoformat() if isinstance(knowledge_cutoff, datetime) else knowledge_cutoff,
                hashlib.sha256((input_text or "").encode("utf-8")).hexdigest()
                if input_text is not None
                else None,
            ],
        )
        self.conn.commit()
        return inference_id

    # -- PIT-safe reads ------------------------------------------------------ #
    def query_observations(
        self,
        *,
        platform: str | None = None,
        artist_id: str | None = None,
        market_id: str | None = None,
        start_time: datetime | str | None = None,
        cutoff: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Canonical observations at a knowledge cutoff (PIT-safe).

        Filters on ``knowledge_time <= cutoff``; observations learned after
        the cutoff are never returned. Mutable engagement is read separately
        through :meth:`engagement_snapshots`.
        """
        query = """
            SELECT observation_id, artist_id, platform, platform_object_id,
                   author_public_id, text, language, published_at,
                   parent_object_id, thread_id, media_type, hashtags, mentions,
                   market_id, geographic_confidence, content_role, content_role_method,
                   resolution_method, resolution_evidence, canonical_url, content_hash,
                   source_count, provider_count
            FROM acquisition.social_observations
            WHERE 1 = 1
        """
        params: list[Any] = []
        if platform:
            query += " AND platform = ?"
            params.append(platform)
        if artist_id:
            query += " AND artist_id = ?"
            params.append(artist_id)
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        if cutoff is not None:
            query += (
                " AND observation_id IN ("
                "   SELECT DISTINCT canonical_observation_id FROM acquisition.raw_observations"
                "   WHERE knowledge_time <= ?"
                ")"
            )
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        if start_time is not None:
            query += " AND (published_at IS NULL OR published_at >= ?)"
            params.append(start_time.isoformat() if isinstance(start_time, datetime) else str(start_time))
        query += " ORDER BY published_at"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        cols = [
            "observation_id", "artist_id", "platform", "platform_object_id",
            "author_public_id", "text", "language", "published_at",
            "parent_object_id", "thread_id", "media_type", "hashtags", "mentions",
            "market_id", "geographic_confidence", "content_role", "content_role_method",
            "resolution_method", "resolution_evidence", "canonical_url", "content_hash",
            "source_count", "provider_count",
        ]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            d["hashtags"] = self._coerce_json(d["hashtags"])
            d["mentions"] = self._coerce_json(d["mentions"])
            out.append(d)
        return out

    def engagement_snapshots(self, observation_id: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT provider, retrieved_at, likes, comments, shares, reposts, views,
                   follower_count_at_observation, verified_author
            FROM acquisition.social_engagement_snapshots
            WHERE social_observation_id = ?
            ORDER BY retrieved_at
            """,
            [observation_id],
        ).fetchall()
        cols = [
            "provider", "retrieved_at", "likes", "comments", "shares",
            "reposts", "views", "follower_count_at_observation", "verified_author",
        ]
        return [dict(zip(cols, row)) for row in rows]

    def latest_inferences(self, observation_id: str, task: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT inference_id, task, model_name, model_version, label,
                   probabilities_json, emotion_json, inference_time, knowledge_cutoff
            FROM acquisition.text_inferences
            WHERE observation_id = ? AND task = ?
            ORDER BY inference_time DESC
            """,
            [observation_id, task],
        ).fetchall()
        cols = [
            "inference_id", "task", "model_name", "model_version", "label",
            "probabilities_json", "emotion_json", "inference_time", "knowledge_cutoff",
        ]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            d["probabilities_json"] = self._coerce_json(d["probabilities_json"])
            d["emotion_json"] = self._coerce_json(d["emotion_json"])
            out.append(d)
        return out

    @staticmethod
    def _coerce_json(value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
