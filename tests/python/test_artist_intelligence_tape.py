"""Offline contracts for the Artist Intelligence Tape V1 slice."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import make_request
from festival_bloomberg.acquisition.providers.google_trends import (
    WAITLIST_AUTH_REQUIRED,
    GoogleTrendsProvider,
)
from festival_bloomberg.acquisition.providers.monid import (
    SUPPORTED_OPERATIONS,
    operation_for_request,
)
from festival_bloomberg.acquisition.providers.soundcharts import (
    HISTORICAL_STRATEGY_LICENSED,
    READINESS_AUTH_REQUIRED,
    SoundchartsProvider,
)
from festival_bloomberg.acquisition.tiering import (
    CollectionTier,
    decide_tier,
    plan_collection,
)
from festival_bloomberg.security.artist_factor_tape import (
    build_factor_observation,
    comparable_delta,
    what_changed,
)
from festival_bloomberg.social.artist_sentiment import aggregate_daily_sentiment

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def test_factor_rows_are_temporal_and_generation_scoped():
    first = build_factor_observation(
        artist_key="mbid::alpha",
        factor_family="DEMAND",
        factor_name="MONTHLY_LISTENERS",
        platform="spotify",
        value=1000,
        unit="listeners",
        observation_time="2026-08-19",
        retrieved_at=T0,
        source="soundcharts",
        source_scope="LICENSED_HISTORICAL",
        generation="generation-a",
    )
    second = build_factor_observation(
        artist_key="mbid::alpha",
        factor_family="DEMAND",
        factor_name="MONTHLY_LISTENERS",
        platform="spotify",
        value=1100,
        unit="listeners",
        observation_time="2026-08-20",
        retrieved_at=T0,
        source="soundcharts",
        source_scope="LICENSED_HISTORICAL",
        generation="generation-b",
    )

    assert first["observation_time"] == "2026-08-19"
    assert first["knowledge_time"] == T0.isoformat()
    assert first["source_scope"] == "LICENSED_HISTORICAL"
    assert first["generation"] == "generation-a"
    assert first["factor_observation_key"] != second["factor_observation_key"]
    assert comparable_delta([first]) is None
    delta = comparable_delta([first, second])
    assert delta is not None
    assert delta["delta"] == 100
    assert delta["delta_pct"] == 10
    assert what_changed([first, second])[0]["factor_name"] == "MONTHLY_LISTENERS"


def test_sentiment_deduplicates_cross_posts_but_not_different_artists():
    records = [
        {
            "artist_key": "mbid::alpha",
            "platform": "youtube",
            "published_at": "2026-08-20T10:00:00Z",
            "text": "The concert was amazing",
            "language": "en",
            "engagement": {"likes": 4},
            "author_public_id": "must-not-leak-a",
        },
        {
            "artist_key": "mbid::alpha",
            "platform": "tiktok",
            "published_at": "2026-08-20T11:00:00Z",
            "text": "The concert was amazing",
            "language": "en",
            "engagement": {"likes": 8},
            "author_public_id": "must-not-leak-b",
        },
        {
            "artist_key": "mbid::beta",
            "platform": "youtube",
            "published_at": "2026-08-20T12:00:00Z",
            "text": "The concert was amazing",
            "language": "en",
            "engagement": {"likes": 3},
            "author_public_id": "must-not-leak-c",
        },
    ]

    rows = aggregate_daily_sentiment(
        records,
        source_generation="sentiment-generation-1",
        retrieved_at=T0,
        knowledge_time=T0,
    )
    by_key = {(row["artist_key"], row["platform"]): row for row in rows}

    assert by_key[("mbid::alpha", "youtube")]["mention_count"] == 1
    assert by_key[("mbid::alpha", "tiktok")]["mention_count"] == 0
    assert by_key[("mbid::alpha", "tiktok")]["deduplicated_count"] == 1
    assert by_key[("mbid::beta", "youtube")]["mention_count"] == 1
    assert all("author_public_id" not in row for row in rows)
    assert all("text" not in row for row in rows)


def test_provider_adapters_fail_closed_with_explicit_readiness():
    soundcharts = SoundchartsProvider(env={})
    result = soundcharts.current_stats("soundcharts-artist")
    assert result.status.value == "NOT_CONFIGURED"
    assert result.provider_metadata["readiness"] == READINESS_AUTH_REQUIRED
    assert result.provider_metadata["historical_strategy"] != HISTORICAL_STRATEGY_LICENSED
    assert (
        soundcharts.historical_audience("soundcharts-artist").provider_metadata[
            "historical_strategy"
        ]
        == HISTORICAL_STRATEGY_LICENSED
    )
    assert soundcharts.health().last_error == READINESS_AUTH_REQUIRED

    trends = GoogleTrendsProvider(env={})
    result = trends.acquire(make_request(platform="google_trends", operation="weekly"))
    assert result.status.value == "NOT_CONFIGURED"
    assert result.provider_metadata["readiness"] == WAITLIST_AUTH_REQUIRED
    assert result.provider_metadata["ui_scraping"] is False
    assert trends.health().last_error == WAITLIST_AUTH_REQUIRED


def test_monid_operations_are_bounded_and_platform_aware():
    assert SUPPORTED_OPERATIONS == frozenset(
        {
            "SOCIAL_PROFILE",
            "SOCIAL_POSTS",
            "SOCIAL_COMMENTS",
            "VIDEO_SEARCH",
            "PLATFORM_DISCOVERY",
        }
    )
    assert operation_for_request(make_request(platform="youtube")) == "VIDEO_SEARCH"
    assert operation_for_request(make_request(platform="instagram")) == "SOCIAL_POSTS"
    assert operation_for_request(make_request(platform="musicbrainz")) == "PLATFORM_DISCOVERY"
    assert (
        operation_for_request(make_request(platform="x", operation="SOCIAL_COMMENTS"))
        == "SOCIAL_COMMENTS"
    )


def test_tier_policy_promotes_explicit_buyer_and_attention_signals():
    baseline = decide_tier({"artist_key": "a"})
    assert baseline.tier == CollectionTier.COVERAGE_25000.name
    assert baseline.cadence_hours == 168

    watchlisted = decide_tier({"artist_key": "b", "watchlist": True})
    assert watchlisted.tier == CollectionTier.HOT_1000.name
    assert "watchlist" in watchlisted.active_signals

    shocked = decide_tier(
        {
            "artist_key": "c",
            "signals": {"attention_shock": {"active": True, "evidence_ref": "obs-1"}},
        }
    )
    assert shocked.tier == CollectionTier.HOT_100.name
    assert shocked.signals[-4].name == "attention_shock"
    assert shocked.signals[-4].evidence_ref == "obs-1"

    plan = plan_collection(
        [
            {"artist_key": "a"},
            {"artist_key": "b", "shortlist": True},
            {"artist_key": "c", "major_release": True},
        ]
    )
    assert plan["planned_count"] == 3
    assert len(plan["by_tier"][CollectionTier.HOT_100.name]) == 1
    assert len(plan["by_tier"][CollectionTier.HOT_1000.name]) == 1
    assert plan["governor_budget_required"] is True
