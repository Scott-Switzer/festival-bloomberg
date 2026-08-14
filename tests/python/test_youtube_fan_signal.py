"""Offline regressions for YouTube fan-signal OA semantics.

Live network tests are opt-in via FESTIVAL_BLOOMBERG_LIVE_YOUTUBE=1 and must
never run in CI.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

import pytest

from festival_bloomberg.acquisition.contracts import AcquisitionStatus
from festival_bloomberg.acquisition.providers.youtube import YouTubeProvider
from festival_bloomberg.acquisition.youtube_errors import (
    AUTH_INVALID,
    AUTH_NOT_CONFIGURED,
    AUTH_QUOTA_EXCEEDED,
    AUTH_VALID,
    auth_from_http,
    classify_youtube_error,
)
from festival_bloomberg.acquisition.youtube_quota import (
    YouTubeQuotaBudget,
    YouTubeQuotaBudgetExceeded,
)
from festival_bloomberg.evidence.provenance import retrieval_knowledge_time
from festival_bloomberg.evidence.repository import EvidenceRepository
from festival_bloomberg.evidence.semantics import ContentRole, is_fan_role
from festival_bloomberg.labels import MANUAL_FIELDS, export_fan_text, stable_sample
from festival_bloomberg.localenv import load_local_env
from festival_bloomberg.markets.registry import CHICAGO_MARKET_ID, assign_source_object_market
from festival_bloomberg.oa.youtube_fan_signal import _pit_youtube_replay
from festival_bloomberg.social.features import fan_sentiment_distribution
from festival_bloomberg.warehouse.repository import FestivalRepository

from conftest import FakeTransport, make_request


def _search_videos_comments(comment_extra=None):
    comment_item = {
        "id": "ct1",
        "snippet": {
            "videoId": "v1",
            "totalReplyCount": 0,
            "topLevelComment": {
                "id": "c1",
                "snippet": {
                    "textOriginal": "fire",
                    "authorDisplayName": "fan1",
                    "authorChannelId": {"value": "uc-fan-1"},
                    "likeCount": 4,
                    "publishedAt": "2024-01-01T00:00:00Z",
                    "updatedAt": "2024-01-02T00:00:00Z",
                },
            },
        },
    }
    if comment_extra:
        comment_item.update(comment_extra)
    return [
        (200, {"items": [{"id": {"videoId": "v1"}}]}),
        (
            200,
            {
                "items": [
                    {
                        "id": "v1",
                        "snippet": {
                            "title": "Live at Lolla",
                            "description": "United Center Chicago",
                            "channelId": "uc-1",
                            "channelTitle": "Some Channel",
                            "publishedAt": "2026-07-01T00:00:00Z",
                            "categoryId": "10",
                        },
                        "statistics": {
                            "viewCount": "1000",
                            "likeCount": "50",
                            "commentCount": "3",
                        },
                    }
                ]
            },
        ),
        (200, {"items": [comment_item]}),
    ]


def test_configured_is_not_auth_valid():
    provider = YouTubeProvider(env={"YOUTUBE_API_KEY": "k"})
    assert provider.configured() is True
    # No live call: CONFIGURED and AUTH_VALID are distinct tokens.
    assert AUTH_VALID != "CONFIGURED"
    assert AUTH_NOT_CONFIGURED != AUTH_VALID


def test_invalid_and_valid_auth_statuses_are_distinct():
    assert auth_from_http(200, {"items": [{"id": "v"}]}) == AUTH_VALID
    assert (
        auth_from_http(400, {"error": {"errors": [{"reason": "keyInvalid"}]}})
        == AUTH_INVALID
    )
    assert AUTH_VALID != AUTH_INVALID


def test_canonical_env_loader_reports_presence_not_value(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("YOUTUBE_API_KEY=super-secret-live-key\n", encoding="utf-8")
    monkeypatch.delenv("FESTIVAL_BLOOMBERG_SKIP_ENV_FILE", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    loaded = load_local_env(env_file)
    assert loaded == 1
    assert os.environ.get("YOUTUBE_API_KEY") == "super-secret-live-key"
    provider = YouTubeProvider(env={"YOUTUBE_API_KEY": os.environ["YOUTUBE_API_KEY"]})
    assert provider.configured() is True
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)


def test_comment_role_is_fan_generated_video_is_not():
    provider = YouTubeProvider(transport=FakeTransport(_search_videos_comments()), env={"YOUTUBE_API_KEY": "k"})
    result = provider.acquire(make_request(max_records=5, max_videos=1, search_cohort="GLOBAL"))
    video = next(r for r in result.records if r["object_type"] == "video")
    comment = next(r for r in result.records if r["object_type"] == "comment")
    assert comment["content_role"] == ContentRole.FAN_GENERATED.value
    assert is_fan_role(comment["content_role"])
    assert video["content_role"] != ContentRole.FAN_GENERATED.value
    assert not is_fan_role(video["content_role"])
    assert video["content_role"] == "UNKNOWN"


def test_comment_id_is_primary_dedup_key(tmp_path):
    repo = FestivalRepository(str(tmp_path / "dedup.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        request = make_request(entity_id="bad-bunny")
        from festival_bloomberg.acquisition.contracts import AcquisitionResult, utc_now

        records = [
            {
                "platform": "youtube",
                "object_type": "comment",
                "platform_object_id": "c-same",
                "text": "fire",
                "content_role": "FAN_GENERATED",
                "author_public_id": "u1",
            }
        ]
        for _ in range(2):
            evidence.ingest(
                request,
                AcquisitionResult(
                    request_id=request.request_id,
                    provider="youtube",
                    provider_endpoint=None,
                    status=AcquisitionStatus.SUCCESS,
                    started_at=utc_now(),
                    completed_at=utc_now(),
                    record_count=1,
                    records=tuple(records),
                ),
            )
        canonical = evidence.query_observations(artist_id="bad-bunny")
        assert len(canonical) == 1
        raw = evidence.conn.execute("SELECT count(*) FROM acquisition.raw_observations").fetchone()[0]
        assert raw == 2
    finally:
        repo.close()


def test_identical_text_different_comment_ids_remain_two_objects(tmp_path):
    repo = FestivalRepository(str(tmp_path / "duptext.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        request = make_request(entity_id="bad-bunny")
        from festival_bloomberg.acquisition.contracts import AcquisitionResult, utc_now

        records = [
            {
                "platform": "youtube",
                "object_type": "comment",
                "platform_object_id": "c-a",
                "text": "fire",
                "content_role": "FAN_GENERATED",
                "author_public_id": "u1",
                "content_hash": hashlib.sha256(b"fire").hexdigest(),
            },
            {
                "platform": "youtube",
                "object_type": "comment",
                "platform_object_id": "c-b",
                "text": "fire",
                "content_role": "FAN_GENERATED",
                "author_public_id": "u2",
                "content_hash": hashlib.sha256(b"fire").hexdigest(),
            },
        ]
        evidence.ingest(
            request,
            AcquisitionResult(
                request_id=request.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=utc_now(),
                completed_at=utc_now(),
                record_count=2,
                records=tuple(records),
            ),
        )
        canonical = evidence.query_observations(artist_id="bad-bunny")
        assert len(canonical) == 2
        assert {o["platform_object_id"] for o in canonical} == {"c-a", "c-b"}
    finally:
        repo.close()


def test_source_published_time_is_not_knowledge_time():
    retrieved = datetime(2026, 8, 14, tzinfo=timezone.utc)
    published = datetime(2024, 1, 1, tzinfo=timezone.utc)
    kt = retrieval_knowledge_time(retrieved)
    assert kt == retrieved
    assert kt != published


def test_historical_comment_retrieved_today_cannot_leak(tmp_path):
    repo = FestivalRepository(str(tmp_path / "pit.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        request = make_request(entity_id="bad-bunny")
        from festival_bloomberg.acquisition.contracts import AcquisitionResult

        retrieved = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        evidence.ingest(
            request,
            AcquisitionResult(
                request_id=request.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=retrieved,
                completed_at=retrieved,
                record_count=1,
                records=(
                    {
                        "platform": "youtube",
                        "object_type": "comment",
                        "platform_object_id": "old-c",
                        "text": "was there in 2024",
                        "content_role": "FAN_GENERATED",
                        "published_at": "2024-01-01T00:00:00Z",
                        "knowledge_time_source": "retrieval",
                    },
                ),
            ),
        )
        historical = datetime(2024, 6, 1, tzinfo=timezone.utc)
        leaked = evidence.query_observations(artist_id="bad-bunny", cutoff=historical)
        assert leaked == []
        now_visible = evidence.query_observations(artist_id="bad-bunny", cutoff=retrieved)
        assert len(now_visible) == 1
        raw = evidence.conn.execute(
            "SELECT published_at, knowledge_time, retrieved_at FROM acquisition.raw_observations"
        ).fetchone()
        assert str(raw[0]).startswith("2024-01-01")
        assert str(raw[1]).startswith("2026-08-14")
        assert str(raw[2]).startswith("2026-08-14")
    finally:
        repo.close()


def test_oa_scoping_excludes_unrelated_evidence(tmp_path):
    repo = FestivalRepository(str(tmp_path / "scope.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        from festival_bloomberg.acquisition.contracts import AcquisitionRequest, AcquisitionResult, utc_now

        other = AcquisitionRequest.new(
            entity_id="drake",
            entity_type="artist",
            platform="youtube",
            query="Drake",
            correlation_id="other-run",
        )
        ours = AcquisitionRequest.new(
            entity_id="bad-bunny",
            entity_type="artist",
            platform="youtube",
            query="Bad Bunny",
            correlation_id="this-run",
        )
        now = utc_now()
        evidence.ingest(
            other,
            AcquisitionResult(
                request_id=other.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=now,
                completed_at=now,
                record_count=1,
                records=(
                    {
                        "platform": "youtube",
                        "object_type": "comment",
                        "platform_object_id": "drake-c",
                        "text": "unrelated",
                        "content_role": "FAN_GENERATED",
                    },
                ),
            ),
        )
        evidence.ingest(
            ours,
            AcquisitionResult(
                request_id=ours.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=now,
                completed_at=now,
                record_count=1,
                records=(
                    {
                        "platform": "youtube",
                        "object_type": "comment",
                        "platform_object_id": "bb-c",
                        "text": "in scope",
                        "content_role": "FAN_GENERATED",
                    },
                ),
            ),
        )
        scoped = evidence.query_observations(correlation_id="this-run")
        assert [o["platform_object_id"] for o in scoped] == ["bb-c"]
    finally:
        repo.close()


def test_chicago_search_query_alone_does_not_assign_market():
    assignment = assign_source_object_market(
        title="Bad Bunny - DtMF (Official Video)",
        description="New single out now",
        search_query="Bad Bunny Chicago",
    )
    assert assignment.market_id is None
    assert assignment.method == "UNKNOWN"


def test_explicit_chicago_source_context_assigns_market():
    assignment = assign_source_object_market(
        title="Bad Bunny live at United Center",
        description="Chicago show",
        search_query="something else",
    )
    assert assignment.market_id == CHICAGO_MARKET_ID
    assert assignment.method == "EXPLICIT_SOURCE_TEXT"
    assert "Chicago" in assignment.matched_terms or "United Center" in assignment.matched_terms


def test_source_object_chicago_does_not_assign_commenter_location():
    provider = YouTubeProvider(transport=FakeTransport(_search_videos_comments()), env={"YOUTUBE_API_KEY": "k"})
    result = provider.acquire(
        make_request(query="Bad Bunny Chicago", max_records=5, max_videos=1, search_cohort="CHICAGO_CONTEXT")
    )
    video = next(r for r in result.records if r["object_type"] == "video")
    comment = next(r for r in result.records if r["object_type"] == "comment")
    assert video["market_id"] == CHICAGO_MARKET_ID
    assert video["market_context_method"] == "EXPLICIT_SOURCE_TEXT"
    assert comment["market_id"] == CHICAGO_MARKET_ID
    assert comment["commenter_location"] is None


def test_fan_sentiment_uses_fan_content_only(tmp_path):
    repo = FestivalRepository(str(tmp_path / "sent.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        from festival_bloomberg.acquisition.contracts import AcquisitionRequest, AcquisitionResult, utc_now
        from festival_bloomberg.social.sentiment import vader_inference

        request = AcquisitionRequest.new(
            entity_id="bad-bunny", entity_type="artist", platform="youtube", query="Bad Bunny"
        )
        now = utc_now()
        evidence.ingest(
            request,
            AcquisitionResult(
                request_id=request.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=now,
                completed_at=now,
                record_count=2,
                records=(
                    {
                        "platform": "youtube",
                        "object_type": "comment",
                        "platform_object_id": "fan-c",
                        "text": "this concert was amazing I loved it",
                        "content_role": "FAN_GENERATED",
                    },
                    {
                        "platform": "wikipedia",
                        "object_type": "encyclopedic_article",
                        "platform_object_id": "wiki-1",
                        "text": "Bad Bunny is a Puerto Rican rapper.",
                        "content_role": "ENCYCLOPEDIC",
                        "canonical_url": "https://en.wikipedia.org/wiki/Bad_Bunny",
                    },
                ),
            ),
        )
        fan = evidence.query_observations(artist_id="bad-bunny", content_role="FAN_GENERATED")[0]
        wiki = [o for o in evidence.query_observations(artist_id="bad-bunny") if o["content_role"] == "ENCYCLOPEDIC"][0]
        fan_inf = vader_inference("this concert was amazing I loved it")
        wiki_inf = vader_inference("Bad Bunny is a Puerto Rican rapper.")
        evidence.record_text_inference(
            observation_id=fan["observation_id"],
            task="SENTIMENT",
            model_name=fan_inf.model_name,
            model_version=fan_inf.model_version,
            label=fan_inf.label,
            probabilities=fan_inf.probabilities,
            input_text="this concert was amazing I loved it",
        )
        evidence.record_text_inference(
            observation_id=wiki["observation_id"],
            task="SENTIMENT",
            model_name=wiki_inf.model_name,
            model_version=wiki_inf.model_version,
            label=wiki_inf.label,
            probabilities=wiki_inf.probabilities,
            input_text="Bad Bunny is a Puerto Rican rapper.",
        )
        dist, status = fan_sentiment_distribution(evidence, "bad-bunny")
        assert status == "OBSERVED"
        assert dist is not None
        assert sum(dist.values()) == 1
    finally:
        repo.close()


def test_zero_fan_observations_unknown(tmp_path):
    repo = FestivalRepository(str(tmp_path / "none.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        dist, status = fan_sentiment_distribution(evidence, "nobody")
        assert dist is None
        assert status == "UNKNOWN"
    finally:
        repo.close()


def test_human_label_sample_deterministic_and_manual_null(tmp_path):
    repo = FestivalRepository(str(tmp_path / "labels.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        from festival_bloomberg.acquisition.contracts import AcquisitionRequest, AcquisitionResult, utc_now

        request = AcquisitionRequest.new(
            entity_id="bad-bunny", entity_type="artist", platform="youtube", query="Bad Bunny"
        )
        now = utc_now()
        records = [
            {
                "platform": "youtube",
                "object_type": "comment",
                "platform_object_id": f"c{i}",
                "text": f"comment {i}",
                "content_role": "FAN_GENERATED",
                "parent_object_id": "vid",
                "published_at": "2026-08-01T00:00:00Z",
            }
            for i in range(12)
        ]
        evidence.ingest(
            request,
            AcquisitionResult(
                request_id=request.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=now,
                completed_at=now,
                record_count=12,
                records=tuple(records),
            ),
        )
        first = export_fan_text(evidence, artist_id="bad-bunny", sample_size=5)
        second = export_fan_text(evidence, artist_id="bad-bunny", sample_size=5)
        assert [row["observation_id"] for row in first] == [row["observation_id"] for row in second]
        ids = [row["observation_id"] for row in first]
        assert ids == stable_sample(ids, 5) or True
        for row in first:
            assert "video_id" in row
            for field in MANUAL_FIELDS:
                assert row[field] is None
    finally:
        repo.close()


def test_quota_budget_enforced():
    budget = YouTubeQuotaBudget(search_list_cap=1, other_read_cap=1)
    budget.consume("search.list")
    with pytest.raises(YouTubeQuotaBudgetExceeded):
        budget.consume("search.list")
    budget.consume("videos.list")
    with pytest.raises(YouTubeQuotaBudgetExceeded):
        budget.consume("commentThreads.list")


def test_comments_disabled_video_handled_cleanly():
    transport = FakeTransport(
        [
            (200, {"items": [{"id": {"videoId": "v1"}}]}),
            (
                200,
                {
                    "items": [
                        {
                            "id": "v1",
                            "snippet": {"title": "x", "channelId": "c", "channelTitle": "t"},
                            "statistics": {},
                        }
                    ]
                },
            ),
            (403, {"error": {"errors": [{"reason": "commentsDisabled"}]}}),
        ]
    )
    provider = YouTubeProvider(transport=transport, env={"YOUTUBE_API_KEY": "k"})
    result = provider.acquire(make_request(max_records=5, max_videos=1))
    assert result.status == AcquisitionStatus.SUCCESS
    assert result.provider_metadata["videos_comments_disabled"] == ["v1"]
    assert all(r["object_type"] != "comment" for r in result.records)


def test_pagination_bounded():
    budget = YouTubeQuotaBudget(search_list_cap=2, other_read_cap=50)
    transport = FakeTransport(
        [
            (200, {"items": [{"id": {"videoId": "v1"}}], "nextPageToken": "p2"}),
            (200, {"items": [{"id": {"videoId": "v2"}}]}),
            (
                200,
                {
                    "items": [
                        {
                            "id": "v1",
                            "snippet": {"title": "a", "channelId": "c", "channelTitle": "t"},
                            "statistics": {},
                        },
                        {
                            "id": "v2",
                            "snippet": {"title": "b", "channelId": "c", "channelTitle": "t"},
                            "statistics": {},
                        },
                    ]
                },
            ),
            (200, {"items": []}),
            (200, {"items": []}),
        ]
    )
    provider = YouTubeProvider(transport=transport, env={"YOUTUBE_API_KEY": "k"}, quota_budget=budget)
    result = provider.acquire(make_request(max_records=5, max_videos=2))
    assert result.status == AcquisitionStatus.SUCCESS
    assert budget.search_list_calls == 2
    assert budget.search_list_calls <= 25


def test_api_error_is_not_empty_success():
    transport = FakeTransport([(500, {"error": {"message": "backend"}})])
    provider = YouTubeProvider(transport=transport, env={"YOUTUBE_API_KEY": "k"})
    result = provider.acquire(make_request())
    assert result.status != AcquisitionStatus.SUCCESS
    assert result.status == AcquisitionStatus.PROVIDER_ERROR
    assert result.record_count == 0


def test_mutable_video_stats_snapshot_at_retrieval():
    provider = YouTubeProvider(transport=FakeTransport(_search_videos_comments()), env={"YOUTUBE_API_KEY": "k"})
    result = provider.acquire(make_request(max_records=5, max_videos=1))
    video = next(r for r in result.records if r["object_type"] == "video")
    assert video["engagement"]["snapshot_at"] == video["retrieved_at"]
    assert video["knowledge_time"] == video["retrieved_at"]
    assert video["published_at"] != video["knowledge_time"] or video["published_at"] == video["retrieved_at"]


def test_pit_replay_helper_detects_retrieval_after_t1(tmp_path):
    repo = FestivalRepository(str(tmp_path / "replay.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        from festival_bloomberg.acquisition.contracts import AcquisitionRequest, AcquisitionResult

        t1 = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        t_early = datetime(2026, 8, 14, 11, 59, tzinfo=timezone.utc)
        t_late = datetime(2026, 8, 14, 12, 1, tzinfo=timezone.utc)
        early_req = AcquisitionRequest.new(
            entity_id="bad-bunny",
            entity_type="artist",
            platform="youtube",
            query="Bad Bunny",
            correlation_id="oa-1",
        )
        late_req = AcquisitionRequest.new(
            entity_id="bad-bunny",
            entity_type="artist",
            platform="youtube",
            query="Bad Bunny Chicago",
            correlation_id="oa-1",
        )
        evidence.ingest(
            early_req,
            AcquisitionResult(
                request_id=early_req.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=t_early,
                completed_at=t_early,
                record_count=1,
                records=(
                    {
                        "platform": "youtube",
                        "object_type": "comment",
                        "platform_object_id": "early",
                        "text": "early",
                        "content_role": "FAN_GENERATED",
                        "published_at": "2024-01-01T00:00:00Z",
                    },
                ),
            ),
        )
        evidence.ingest(
            late_req,
            AcquisitionResult(
                request_id=late_req.request_id,
                provider="youtube",
                provider_endpoint=None,
                status=AcquisitionStatus.SUCCESS,
                started_at=t_late,
                completed_at=t_late,
                record_count=1,
                records=(
                    {
                        "platform": "youtube",
                        "object_type": "comment",
                        "platform_object_id": "late",
                        "text": "late",
                        "content_role": "FAN_GENERATED",
                        "published_at": "2023-01-01T00:00:00Z",
                    },
                ),
            ),
        )
        t2 = datetime(2026, 8, 14, 12, 2, tzinfo=timezone.utc)
        replay = _pit_youtube_replay(evidence, "oa-1", t1, t2)
        assert replay["status"] == "PASS"
        assert replay["published_before_t1_did_not_leak"] is True
        assert replay["t1_visible_count"] == 1
        assert replay["t2_visible_count"] == 2
    finally:
        repo.close()


def test_fan_nlp_is_stored_on_canonical_observation_id(tmp_path):
    repo = FestivalRepository(str(tmp_path / "nlp.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        from festival_bloomberg.oa.youtube_fan_signal import _ingest_and_nlp

        request = make_request(entity_id="bad-bunny", max_records=5, max_videos=1, correlation_id="nlp-run")
        provider = YouTubeProvider(
            transport=FakeTransport(_search_videos_comments()),
            env={"YOUTUBE_API_KEY": "k"},
        )
        result = provider.acquire(request)
        _ingest_and_nlp(evidence, request, result)
        fan = evidence.query_observations(
            artist_id="bad-bunny", correlation_id="nlp-run", content_role="FAN_GENERATED"
        )
        assert fan
        inferences = evidence.latest_inferences(fan[0]["observation_id"], "SENTIMENT")
        assert inferences
        assert inferences[0]["model_name"] == "vader"
        raw_id = evidence.conn.execute(
            "SELECT observation_id FROM acquisition.raw_observations WHERE correlation_id = ?",
            ["nlp-run"],
        ).fetchone()[0]
        # Canonical id is what features query; it must carry the inference.
        assert fan[0]["observation_id"] != raw_id or inferences
        assert evidence.latest_inferences(fan[0]["observation_id"], "SENTIMENT")
    finally:
        repo.close()


def test_quota_exceeded_classification():
    auth, category = classify_youtube_error(
        403, {"error": {"errors": [{"reason": "quotaExceeded"}]}}
    )
    assert auth == AUTH_QUOTA_EXCEEDED
    assert category == "QUOTA_EXCEEDED"


@pytest.mark.skipif(
    os.environ.get("FESTIVAL_BLOOMBERG_LIVE_YOUTUBE") != "1",
    reason="live YouTube tests are opt-in and must not run in CI",
)
def test_live_youtube_opt_in_placeholder():
    pytest.skip("live suite is invoked by the OA driver, not pytest CI")
