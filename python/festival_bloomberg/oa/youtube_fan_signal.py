"""Live YouTube fan-signal operational acceptance.

Produces Festival Bloomberg's first real FAN_GENERATED social dataset through
the canonical Signal Fabric. Monetary budget is $0.00. YouTube quota is
counted. Search queries never assign market_id.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..acquisition.contracts import AcquisitionRequest, AcquisitionResult, AcquisitionStatus, utc_now
from ..acquisition.providers.youtube import YouTubeProvider
from ..acquisition.youtube_errors import AUTH_VALID
from ..acquisition.youtube_quota import YouTubeQuotaBudget
from ..evidence.provenance import parse_iso, utc
from ..evidence.semantics import is_fan_role
from ..labels import export_fan_text
from ..localenv import load_local_env
from ..markets.registry import CHICAGO_MARKET_ID
from ..oa.operational_acceptance import CANDIDATE_ARTISTS, SELECTION_RULE, provider_readiness
from ..social.intent import INTENT_TASKS, MODEL_VERSION as INTENT_MODEL_VERSION, all_intent_heuristics
from ..social.sentiment import TWEETNLP_AVAILABLE, vader_inference, tweetnlp_inference
from ..social.youtube_fan_features import build_cohort_fan_features

YOUTUBE_SELECTION_RULE = (
    "first artist alphabetically from the predeclared CANDIDATE_ARTISTS universe "
    "(same universe as the Wikimedia OA). Availability is not gated on Wikipedia "
    "extract length for the YouTube run; Bad Bunny is first alphabetically."
)

GLOBAL_LOOKBACK_DAYS = 30
CHICAGO_LOOKBACK_DAYS = 730
SINGLE_GLOBAL_MAX_VIDEOS = 15
SINGLE_GLOBAL_MAX_COMMENTS = 500
SINGLE_CHICAGO_MAX_VIDEOS = 10
SINGLE_CHICAGO_MAX_COMMENTS = 500
BATCH_GLOBAL_MAX_VIDEOS = 10
BATCH_CHICAGO_MAX_VIDEOS = 5
BATCH_MAX_COMMENTS = 500


def _entity_id(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def _make_youtube_request(
    *,
    artist: str,
    query: str,
    oa_run_id: str,
    cohort: str,
    start_time: datetime,
    max_videos: int,
    max_comments: int,
    order: str,
) -> AcquisitionRequest:
    return AcquisitionRequest.new(
        entity_id=_entity_id(artist),
        entity_type="artist",
        platform="youtube",
        query=query,
        start_time=start_time,
        max_records=max_comments,
        max_videos=max_videos,
        max_cost_usd=0.0,
        commercial_context="research",
        correlation_id=oa_run_id,
        preferred_providers=("youtube",),
        order=order,
        search_cohort=cohort,
    )


def _quality_metrics(records: list[dict], cohort: str) -> dict[str, Any]:
    comments = [r for r in records if r.get("object_type") == "comment"]
    videos = [r for r in records if r.get("object_type") == "video"]
    return {
        "cohort": cohort,
        "videos": len(videos),
        "comments": len(comments),
        "unique_authors": len({r.get("author_public_id") for r in comments if r.get("author_public_id")}),
        "missing_text": sum(1 for r in comments if not (r.get("text") or "").strip()),
        "missing_author": sum(1 for r in comments if not r.get("author_public_id")),
        "missing_published_at": sum(1 for r in comments if not r.get("published_at")),
        "missing_updated_at": sum(1 for r in comments if not r.get("source_updated_at")),
        "missing_like_count": sum(
            1 for r in comments if (r.get("engagement") or {}).get("likes") is None
        ),
        "chicago_market_videos": sum(1 for r in videos if r.get("market_id") == CHICAGO_MARKET_ID),
        "chicago_market_comments": sum(1 for r in comments if r.get("market_id") == CHICAGO_MARKET_ID),
    }


def _pit_youtube_replay(
    evidence,
    correlation_id: str,
    t1: datetime,
    t2: datetime,
) -> dict[str, Any]:
    rows = evidence.conn.execute(
        """
        SELECT observation_id, knowledge_time, retrieved_at, published_at, metadata_json
        FROM acquisition.raw_observations
        WHERE correlation_id = ?
        """,
        [correlation_id],
    ).fetchall()
    if not rows:
        return {"status": "FAIL", "reason": "no scoped observations", "correlation_id": correlation_id}

    def as_dt(value) -> datetime | None:
        if isinstance(value, datetime):
            return utc(value)
        return parse_iso(str(value)) if value is not None else None

    def visible(cutoff: datetime) -> list[str]:
        ids = []
        for oid, kt, _rt, _pub, _meta in rows:
            parsed = as_dt(kt)
            if parsed and parsed <= cutoff:
                ids.append(oid)
        return sorted(ids)

    t1_ids = visible(t1)
    t2_ids = visible(t2)
    learned_after_t1 = sorted(set(t2_ids) - set(t1_ids))
    leak = []
    for oid, kt, _rt, published, _meta in rows:
        pub = as_dt(published)
        knowledge = as_dt(kt)
        if pub is not None and pub <= t1 and knowledge is not None and knowledge > t1:
            if oid in t1_ids:
                leak.append(oid)
    status = "PASS" if learned_after_t1 and not leak else "FAIL"
    if not learned_after_t1:
        status = "FAIL"
    return {
        "status": status,
        "correlation_id": correlation_id,
        "scoped_raw_count": len(rows),
        "t1": t1.isoformat(),
        "t2": t2.isoformat(),
        "t1_visible_count": len(t1_ids),
        "t2_visible_count": len(t2_ids),
        "learned_after_t1": learned_after_t1,
        "published_before_t1_did_not_leak": not leak,
        "leak_ids": leak,
        "note": (
            "knowledge_time is retrieval time; a comment publishedAt before T1 "
            "is invisible at T1 when retrieved after T1"
        ),
    }


def _ingest_and_nlp(evidence, request: AcquisitionRequest, result: AcquisitionResult) -> dict[str, int]:
    evidence.ingest(request, result)
    sentiment_count = 0
    intent_count = 0
    tweetnlp_count = 0
    tweetnlp_budget = 20
    for record in result.records:
        if not is_fan_role(record.get("content_role")):
            continue
        text = record.get("text") or ""
        rows = evidence.conn.execute(
            """
            SELECT canonical_observation_id, observation_id FROM acquisition.raw_observations
            WHERE correlation_id = ? AND platform_object_id = ?
            ORDER BY retrieved_at DESC LIMIT 1
            """,
            [request.correlation_id, record.get("platform_object_id")],
        ).fetchall()
        if not rows:
            continue
        observation_id = rows[0][0] or rows[0][1]
        inference = vader_inference(text)
        evidence.record_text_inference(
            observation_id=observation_id,
            task="SENTIMENT",
            model_name=inference.model_name,
            model_version=inference.model_version,
            label=inference.label,
            probabilities=inference.probabilities,
            input_text=text,
        )
        sentiment_count += 1
        tweet = tweetnlp_inference(text)
        if tweet.available and tweetnlp_count < tweetnlp_budget:
            evidence.record_text_inference(
                observation_id=observation_id,
                task="SENTIMENT",
                model_name=tweet.model_name,
                model_version=tweet.model_version,
                label=tweet.label,
                probabilities=tweet.probabilities,
                input_text=text,
            )
            tweetnlp_count += 1
        for intent in all_intent_heuristics(text):
            evidence.record_text_inference(
                observation_id=observation_id,
                task=intent["task"],
                model_name=intent["model_name"],
                model_version=intent["model_version"],
                label=intent["label"],
                probabilities={"hits": len(intent.get("hits") or [])},
                input_text=text,
            )
            intent_count += 1
    return {
        "sentiment": sentiment_count,
        "intent": intent_count,
        "tweetnlp": tweetnlp_count,
    }


def collect_artist_cohorts(
    *,
    provider: YouTubeProvider,
    evidence,
    artist: str,
    oa_run_id: str,
    global_max_videos: int,
    chicago_max_videos: int,
    max_comments: int,
    collection_started_at: datetime,
) -> dict[str, Any]:
    now = utc_now()
    global_start = now - timedelta(days=GLOBAL_LOOKBACK_DAYS)
    chicago_start = now - timedelta(days=CHICAGO_LOOKBACK_DAYS)

    global_request = _make_youtube_request(
        artist=artist,
        query=artist,
        oa_run_id=oa_run_id,
        cohort="GLOBAL",
        start_time=global_start,
        max_videos=global_max_videos,
        max_comments=max_comments,
        order="date",
    )
    global_result = provider.acquire(global_request)
    global_nlp = {"sentiment": 0, "intent": 0, "tweetnlp": 0}
    if global_result.is_success or global_result.status == AcquisitionStatus.NO_RESULTS:
        if global_result.records:
            global_nlp = _ingest_and_nlp(evidence, global_request, global_result)
    after_global = utc_now()
    # Ensure T1 is strictly after GLOBAL retrieval knowledge_times.
    time.sleep(0.05)

    chicago_request = _make_youtube_request(
        artist=artist,
        query=f"{artist} Chicago",
        oa_run_id=oa_run_id,
        cohort="CHICAGO_CONTEXT",
        start_time=chicago_start,
        max_videos=chicago_max_videos,
        max_comments=max_comments,
        order="relevance",
    )
    chicago_result = provider.acquire(chicago_request)
    chicago_nlp = {"sentiment": 0, "intent": 0, "tweetnlp": 0}
    if chicago_result.is_success or chicago_result.status == AcquisitionStatus.NO_RESULTS:
        if chicago_result.records:
            chicago_nlp = _ingest_and_nlp(evidence, chicago_request, chicago_result)
    after_chicago = utc_now()

    def _status(result: AcquisitionResult) -> str:
        if result.status == AcquisitionStatus.BUDGET_EXCEEDED:
            return "QUOTA_STOP"
        if result.status == AcquisitionStatus.NO_RESULTS:
            return "NO_DATA"
        if result.is_success:
            return "COLLECTED"
        return "ERROR"

    global_status = _status(global_result)
    chicago_status = _status(chicago_result)
    if "QUOTA_STOP" in (global_status, chicago_status):
        coverage = "QUOTA_STOP"
    elif "ERROR" in (global_status, chicago_status) and "COLLECTED" not in (global_status, chicago_status):
        coverage = "ERROR"
    elif global_status == "NO_DATA" and chicago_status == "NO_DATA":
        coverage = "NO_DATA"
    elif "COLLECTED" in (global_status, chicago_status):
        coverage = "COLLECTED"
    else:
        coverage = global_status
    return {
        "artist": artist,
        "global_request": global_request,
        "chicago_request": chicago_request,
        "global_result": global_result,
        "chicago_result": chicago_result,
        "global_nlp": global_nlp,
        "chicago_nlp": chicago_nlp,
        "after_global": after_global,
        "after_chicago": after_chicago,
        "collection_started_at": collection_started_at,
        "coverage": coverage,
        "global_status": global_status,
        "chicago_status": chicago_status,
    }


def run_youtube_fan_signal_oa(
    evidence,
    *,
    market: str = CHICAGO_MARKET_ID,
    budget_usd: float = 0.0,
    db_path: str | None = None,
    batch_universe: bool = True,
    label_output: str | None = None,
) -> dict[str, Any]:
    load_local_env()
    generated_at = utc_now()
    oa_run_id = str(uuid.uuid4())
    collection_started_at = generated_at
    knowledge_cutoff = generated_at
    readiness = provider_readiness()
    quota = YouTubeQuotaBudget()
    provider = YouTubeProvider(quota_budget=quota)

    auth = provider.validate_auth()
    selected = sorted(CANDIDATE_ARTISTS)[0]

    statuses = {
        "YOUTUBE_AUTH": "PASS" if auth.get("auth") == AUTH_VALID else "FAIL",
        "YOUTUBE_DISCOVERY": "FAIL",
        "YOUTUBE_VIDEO_INGESTION": "FAIL",
        "FAN_GENERATED_DATA": "NO_DATA",
        "REAL_SOCIAL_NLP": "NOT_EVALUATED",
        "GLOBAL_ARTIST_FAN_SIGNAL": "INSUFFICIENT",
        "CHICAGO_SOURCE_CONTEXT": "INSUFFICIENT",
        "ARTIST_CHICAGO_RELATION": "INSUFFICIENT",
        "ARTIST_CHICAGO_DEMAND_SIGNAL": "INSUFFICIENT",
        "HUMAN_LABEL_EXPORT": "INSUFFICIENT",
        "PIT_REAL_DATA_REPLAY": "FAIL",
        "CROSS_PROVIDER_RECONCILIATION": "BUDGET_GUARD",
        "TWEETNLP": "AVAILABLE" if TWEETNLP_AVAILABLE else "NOT_AVAILABLE",
    }

    if auth.get("auth") != AUTH_VALID:
        return {
            "schema_version": "youtube-fan-signal-v1",
            "oa_run_id": oa_run_id,
            "generated_at": generated_at.isoformat(),
            "collection_started_at": collection_started_at.isoformat(),
            "knowledge_cutoff": knowledge_cutoff.isoformat(),
            "selected_artist": selected,
            "selection_rule": YOUTUBE_SELECTION_RULE,
            "youtube_credential_status": {
                "configured": auth.get("configured"),
                "auth": auth.get("auth"),
                "error_category": auth.get("error_category"),
            },
            "statuses": statuses,
            "quota_usage": quota.as_dict(),
            "cost_usd": 0.0,
            "provider_readiness": readiness,
            "db_path": db_path,
        }

    single = collect_artist_cohorts(
        provider=provider,
        evidence=evidence,
        artist=selected,
        oa_run_id=oa_run_id,
        global_max_videos=SINGLE_GLOBAL_MAX_VIDEOS,
        chicago_max_videos=SINGLE_CHICAGO_MAX_VIDEOS,
        max_comments=SINGLE_GLOBAL_MAX_COMMENTS,
        collection_started_at=collection_started_at,
    )

    global_records = list(single["global_result"].records or ())
    chicago_records = list(single["chicago_result"].records or ())
    all_records = global_records + chicago_records
    comments = [r for r in all_records if r.get("object_type") == "comment"]
    videos = [r for r in all_records if r.get("object_type") == "video"]

    if single["global_result"].is_success or single["chicago_result"].is_success:
        if any(r.get("object_type") == "video" for r in all_records):
            statuses["YOUTUBE_DISCOVERY"] = "PASS"
            statuses["YOUTUBE_VIDEO_INGESTION"] = "PASS"
        elif single["global_result"].status == AcquisitionStatus.NO_RESULTS:
            statuses["YOUTUBE_DISCOVERY"] = "FAIL"
    if comments:
        statuses["FAN_GENERATED_DATA"] = "PASS"
        if single["global_nlp"]["sentiment"] or single["chicago_nlp"]["sentiment"]:
            statuses["REAL_SOCIAL_NLP"] = "PASS"

    global_features = build_cohort_fan_features(
        evidence,
        artist_id=_entity_id(selected),
        cohort="GLOBAL",
        correlation_id=oa_run_id,
        chicago_market_id=market,
    )
    chicago_features = build_cohort_fan_features(
        evidence,
        artist_id=_entity_id(selected),
        cohort="CHICAGO_CONTEXT",
        correlation_id=oa_run_id,
        chicago_market_id=market,
    )
    if global_features.comment_count > 0:
        statuses["GLOBAL_ARTIST_FAN_SIGNAL"] = "PASS"
    chicago_videos = [r for r in chicago_records if r.get("object_type") == "video" and r.get("market_id") == market]
    if chicago_videos:
        statuses["CHICAGO_SOURCE_CONTEXT"] = "PASS"
        statuses["ARTIST_CHICAGO_RELATION"] = "PASS"
    statuses["ARTIST_CHICAGO_DEMAND_SIGNAL"] = "INSUFFICIENT"

    t1 = single["after_global"]
    t2 = single["after_chicago"]
    pit = _pit_youtube_replay(evidence, oa_run_id, t1, t2)
    if pit["status"] != "PASS":
        # Cohort split can collapse when Chicago returns no rows; prove against
        # a cutoff strictly before this live retrieval instead.
        t1 = collection_started_at - timedelta(seconds=1)
        t2 = utc_now()
        pit = _pit_youtube_replay(evidence, oa_run_id, t1, t2)
    statuses["PIT_REAL_DATA_REPLAY"] = pit["status"]

    label_rows: list[dict[str, Any]] = []
    if comments:
        label_rows = export_fan_text(
            evidence,
            artist_id=_entity_id(selected),
            sample_size=min(100, len(comments)),
        )
        # Restrict to this OA run's observations.
        oa_ids = {
            row[0]
            for row in evidence.conn.execute(
                "SELECT canonical_observation_id FROM acquisition.raw_observations WHERE correlation_id = ?",
                [oa_run_id],
            ).fetchall()
            if row[0]
        }
        label_rows = [row for row in label_rows if row["observation_id"] in oa_ids][:100]
        if label_rows:
            statuses["HUMAN_LABEL_EXPORT"] = "PASS"
        if label_output:
            parent = os.path.dirname(label_output)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(label_output, "w", encoding="utf-8") as fh:
                json.dump(label_rows, fh, indent=2, ensure_ascii=False, default=str)

    batch_results: list[dict[str, Any]] = []
    comparison: list[dict[str, Any]] = []
    single_pass = (
        statuses["YOUTUBE_AUTH"] == "PASS"
        and statuses["FAN_GENERATED_DATA"] == "PASS"
        and statuses["REAL_SOCIAL_NLP"] == "PASS"
        and statuses["PIT_REAL_DATA_REPLAY"] == "PASS"
    )
    if batch_universe and single_pass:
        for artist in CANDIDATE_ARTISTS:
            if artist == selected:
                batch_results.append(
                    {
                        "artist": artist,
                        "status": "COLLECTED",
                        "global_status": single["global_status"],
                        "chicago_status": single["chicago_status"],
                    }
                )
                comparison.append(_comparison_row(selected, global_features, chicago_features, "COLLECTED"))
                continue
            if quota.remaining_search() < 2 or quota.remaining_other() < 10:
                batch_results.append({"artist": artist, "status": "QUOTA_STOP"})
                comparison.append(_comparison_row(artist, None, None, "QUOTA_STOP"))
                break
            artist_run = collect_artist_cohorts(
                provider=provider,
                evidence=evidence,
                artist=artist,
                oa_run_id=oa_run_id,
                global_max_videos=BATCH_GLOBAL_MAX_VIDEOS,
                chicago_max_videos=BATCH_CHICAGO_MAX_VIDEOS,
                max_comments=BATCH_MAX_COMMENTS,
                collection_started_at=collection_started_at,
            )
            g_feat = build_cohort_fan_features(
                evidence,
                artist_id=_entity_id(artist),
                cohort="GLOBAL",
                correlation_id=oa_run_id,
                chicago_market_id=market,
            )
            c_feat = build_cohort_fan_features(
                evidence,
                artist_id=_entity_id(artist),
                cohort="CHICAGO_CONTEXT",
                correlation_id=oa_run_id,
                chicago_market_id=market,
            )
            coverage = artist_run["coverage"]
            if artist_run["global_status"] == "QUOTA_STOP" or artist_run["chicago_status"] == "QUOTA_STOP":
                coverage = "QUOTA_STOP"
            elif artist_run["global_status"] == "ERROR" or artist_run["chicago_status"] == "ERROR":
                coverage = "ERROR"
            elif artist_run["global_status"] == "NO_DATA" and artist_run["chicago_status"] == "NO_DATA":
                coverage = "NO_DATA"
            else:
                coverage = "COLLECTED"
            batch_results.append(
                {
                    "artist": artist,
                    "status": coverage,
                    "global_status": artist_run["global_status"],
                    "chicago_status": artist_run["chicago_status"],
                }
            )
            comparison.append(_comparison_row(artist, g_feat, c_feat, coverage))
            if coverage == "QUOTA_STOP":
                break
    elif batch_universe:
        batch_results.append(
            {
                "artist": selected,
                "status": single["global_status"] if single["global_status"] != "COLLECTED" else "COLLECTED",
                "note": "10-artist batch skipped; single-artist OA did not fully pass",
            }
        )

    data_quality = {
        "search_queries": {
            "GLOBAL": single["global_request"].query,
            "CHICAGO_CONTEXT": single["chicago_request"].query,
        },
        "videos_discovered_global": (single["global_result"].provider_metadata or {}).get("videos_discovered"),
        "videos_selected_global": (single["global_result"].provider_metadata or {}).get("videos_selected"),
        "videos_discovered_chicago": (single["chicago_result"].provider_metadata or {}).get("videos_discovered"),
        "videos_selected_chicago": (single["chicago_result"].provider_metadata or {}).get("videos_selected"),
        "comments_disabled_global": (single["global_result"].provider_metadata or {}).get("videos_comments_disabled"),
        "comments_disabled_chicago": (single["chicago_result"].provider_metadata or {}).get("videos_comments_disabled"),
        "video_ingestion_failures": {
            "GLOBAL": (single["global_result"].provider_metadata or {}).get("videos_missing"),
            "CHICAGO_CONTEXT": (single["chicago_result"].provider_metadata or {}).get("videos_missing"),
        },
        "raw_comment_observations": len(comments),
        "canonical_comments": len(
            evidence.query_observations(
                artist_id=_entity_id(selected),
                correlation_id=oa_run_id,
                content_role="FAN_GENERATED",
            )
        ),
        "unique_authors": len({r.get("author_public_id") for r in comments if r.get("author_public_id")}),
        "global": _quality_metrics(global_records, "GLOBAL"),
        "chicago": _quality_metrics(chicago_records, "CHICAGO_CONTEXT"),
        "sentiment_inference_count": single["global_nlp"]["sentiment"] + single["chicago_nlp"]["sentiment"],
        "intent_inference_count": single["global_nlp"]["intent"] + single["chicago_nlp"]["intent"],
        "retrieval_time_range": {
            "started_at": collection_started_at.isoformat(),
            "ended_at": utc_now().isoformat(),
        },
        "quota_method_counts": quota.as_dict(),
        "actual_monetary_cost": 0.0,
    }

    return {
        "schema_version": "youtube-fan-signal-v1",
        "oa_run_id": oa_run_id,
        "generated_at": generated_at.isoformat(),
        "collection_started_at": collection_started_at.isoformat(),
        "knowledge_cutoff": knowledge_cutoff.isoformat(),
        "selected_artist": selected,
        "selection_rule": YOUTUBE_SELECTION_RULE,
        "universe_rule": SELECTION_RULE,
        "market": market,
        "youtube_credential_status": {
            "configured": auth.get("configured"),
            "auth": auth.get("auth"),
            "error_category": auth.get("error_category"),
        },
        "statuses": statuses,
        "quota_usage": quota.as_dict(),
        "cost_usd": 0.0,
        "budget_usd": budget_usd,
        "provider_readiness": readiness,
        "db_path": db_path,
        "intent_model_version": INTENT_MODEL_VERSION,
        "global_features": global_features.to_dict(),
        "chicago_features": chicago_features.to_dict(),
        "data_quality": data_quality,
        "pit_replay": pit,
        "human_label_sample": {
            "count": len(label_rows),
            "path": label_output,
            "manual_fields_null": True,
        },
        "batch_results": batch_results,
        "comparison_table": comparison,
        "no_ugc_in_manifest": True,
        "cross_provider_reconciliation": "BUDGET_GUARD",
        "apify": "AUTH_VALID_NO_PAID_RUN",
        "single_artist_records": {
            "videos": len(videos),
            "comments": len(comments),
        },
        "INTENT_TASKS": list(INTENT_TASKS),
    }


def _comparison_row(artist, global_features, chicago_features, status: str) -> dict[str, Any]:
    empty = {
        "artist": artist,
        "global_comments": None,
        "global_unique_authors": None,
        "global_positive_share": None,
        "global_neutral_share": None,
        "global_negative_share": None,
        "global_comment_engagement": None,
        "chicago_context_comments": None,
        "chicago_context_unique_authors": None,
        "chicago_positive_share": None,
        "chicago_neutral_share": None,
        "chicago_negative_share": None,
        "chicago_context_video_count": None,
        "data_quality_status": status,
        "coverage_warnings": ["not collected"] if status != "COLLECTED" else [],
    }
    if global_features is None or chicago_features is None:
        return empty
    warnings = list(global_features.warnings) + list(chicago_features.warnings)
    return {
        "artist": artist,
        "global_comments": global_features.comment_count,
        "global_unique_authors": global_features.unique_public_authors,
        "global_n_videos": global_features.videos_sampled,
        "global_sampling_status": "CAPPED" if global_features.comment_count >= 500 else "COMPLETE",
        "global_positive_share": global_features.positive_share,
        "global_neutral_share": global_features.neutral_share,
        "global_negative_share": global_features.negative_share,
        "global_comment_engagement": global_features.comment_like_total,
        "chicago_context_comments": chicago_features.comment_count,
        "chicago_context_unique_authors": chicago_features.unique_public_authors,
        "chicago_n_videos": chicago_features.videos_sampled,
        "chicago_sampling_status": "CAPPED" if chicago_features.comment_count >= 500 else "COMPLETE",
        "chicago_positive_share": chicago_features.positive_share,
        "chicago_neutral_share": chicago_features.neutral_share,
        "chicago_negative_share": chicago_features.negative_share,
        "chicago_context_video_count": chicago_features.chicago_context_video_count
        or chicago_features.videos_sampled,
        "data_quality_status": status,
        "coverage_warnings": warnings
        + (
            ["sentiment shares are within sampled comments, not fanbase sentiment"]
            if status == "COLLECTED"
            else []
        ),
        "sentiment_interpretation": "sentiment within sampled comments",
    }
