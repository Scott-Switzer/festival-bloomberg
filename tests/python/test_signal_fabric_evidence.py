"""Offline regression tests for the Signal Fabric evidence layer.

Covers the required invariants: fresh bootstrap, upgrade from current main,
deterministic migration order, canonical dedup, timestamped engagement,
PIT cutoffs, feature provenance, synthetic-data quarantine, and the ticket
spread regression guard.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from festival_bloomberg.acquisition.contracts import (
    AcquisitionResult,
    AcquisitionStatus,
    utc_now,
)
from festival_bloomberg.acquisition.providers import MonidProvider, YouTubeProvider
from festival_bloomberg.evidence.dedup import (
    canonical_key,
    resolve_canonical,
)
from festival_bloomberg.evidence.provenance import knowledge_time_for
from festival_bloomberg.evidence.repository import (
    EvidenceRepository,
    guard_evidence_class,
)
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.schema_paths import load_migration_files
from festival_bloomberg.social import features as feature_builder
from festival_bloomberg.social.sentiment import infer_sentiment
from festival_bloomberg.warehouse.repository import FestivalRepository

from conftest import FakeTransport, make_request

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _result(
    request,
    *,
    provider: str,
    records,
    status=AcquisitionStatus.SUCCESS,
    cost_usd=None,
    completed_at=None,
) -> AcquisitionResult:
    return AcquisitionResult(
        request_id=request.request_id,
        provider=provider,
        provider_endpoint=None,
        status=status,
        started_at=utc_now(),
        completed_at=completed_at or utc_now(),
        record_count=len(records),
        cost_usd=cost_usd,
        provider_metadata={},
        records=tuple(records),
    )


@pytest.fixture
def evidence(tmp_path):
    repo = FestivalRepository(str(tmp_path / "signal.duckdb"))
    evidence_repo = EvidenceRepository(repo.conn)
    yield repo, evidence_repo
    repo.close()


# ---------------------------------------------------------------------------
# Bootstrap / migrations
# ---------------------------------------------------------------------------
class TestMigrations:
    def test_fresh_bootstrap_creates_evidence_tables(self, tmp_path):
        import duckdb

        connection = duckdb.connect(str(tmp_path / "fresh.duckdb"))
        try:
            assert apply_pending_migrations(connection) == 20
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'acquisition'
                    """
                ).fetchall()
            }
            assert {
                "acquisition_runs",
                "raw_observations",
                "social_observations",
                "social_engagement_snapshots",
                "text_inferences",
            } <= tables
        finally:
            connection.close()

    def test_upgrade_from_current_main_db_survives(self, tmp_path):
        """A database shaped like current main upgrades to v7 without losing rows."""
        import duckdb

        db_path = tmp_path / "main.duckdb"
        connection = duckdb.connect(str(db_path))
        # current main's schema_migrations (versions 1-5 already applied)
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for version, name in [
            (1, "canonical_ingestion_dedup_v1"),
            (2, "published_at_point_in_time_v2"),
            (3, "intelligence_metrics_v1"),
            (4, "canonical_entity_resolution_v1"),
            (5, "ticket_secondary_spread_v1"),
        ]:
            connection.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                [version, name],
            )
        # old-shaped metrics.artist_metrics (pre-PIT); other tables are created
        # fresh by the base-schema pass during the upgrade.
        connection.execute("CREATE SCHEMA metrics")
        connection.execute(
            """
            CREATE TABLE metrics.artist_metrics (
                metric_key VARCHAR PRIMARY KEY,
                artist_key VARCHAR NOT NULL,
                source_system VARCHAR NOT NULL,
                metric_type VARCHAR NOT NULL,
                value DOUBLE,
                observed_date DATE,
                fetched_at TIMESTAMP,
                meta_data JSON
            )
            """
        )
        connection.execute(
            "INSERT INTO metrics.artist_metrics VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
            ["a::wikimedia::views_30d", "a", "wikimedia", "views_30d", 42.0, "{}"],
        )
        connection.close()

        connection = duckdb.connect(str(db_path))
        try:
            applied = apply_pending_migrations(connection)
            assert applied == 15  # migrations 6..20
            # historical rows survive
            metrics = connection.execute(
                "SELECT value FROM metrics.artist_metrics WHERE artist_key = 'a'"
            ).fetchall()
            assert metrics == [(42.0,)]
            # fresh tables created by the upgrade
            festivals = connection.execute(
                "SELECT count(*) FROM core.festivals"
            ).fetchone()
            assert festivals[0] == 0
            # PIT columns were added
            cols = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'metrics' AND table_name = 'artist_metrics'
                    """
                ).fetchall()
            }
            assert "knowledge_time" in cols
            # evidence tables exist
            runs = connection.execute(
                "SELECT count(*) FROM acquisition.acquisition_runs"
            ).fetchone()
            assert runs[0] == 0
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
        finally:
            connection.close()

    def test_migration_order_is_deterministic(self):
        versions = [version for version, _name, _path in load_migration_files()]
        assert versions == sorted(versions)
        assert versions == list(range(1, 21))


# ---------------------------------------------------------------------------
# Dedup + reconciliation
# ---------------------------------------------------------------------------
class TestReconciliation:
    def test_two_providers_one_canonical_object(self, evidence):
        _repo, evidence_repo = evidence
        request = make_request(entity_id="radiohead", platform="youtube")

        youtube_record = {
            "platform": "youtube",
            "object_type": "video",
            "platform_object_id": "vid-123",
            "author_public_id": "ch-1",
            "text": "full set live",
            "published_at": "2024-01-05T00:00:00Z",
            "canonical_url": "https://www.youtube.com/watch?v=vid-123",
            "content_hash": "abc",
            "engagement": {"views": 1000, "likes": 50},
        }
        monid_record = {
            "platform": "youtube",
            "object_type": "video",
            "platform_object_id": "vid-123",
            "author_public_id": "ch-1",
            "text": "full set live",
            "published_at": "2024-01-05T00:00:00Z",
            "canonical_url": "https://www.youtube.com/watch?v=vid-123",
            "content_hash": "abc",
            "engagement": {"views": 1000, "likes": 50},
        }

        evidence_repo.ingest(request, _result(request, provider="youtube", records=[youtube_record]))
        evidence_repo.ingest(request, _result(request, provider="monid", records=[monid_record]))

        canonical = evidence_repo.query_observations(artist_id="radiohead")
        assert len(canonical) == 1
        assert canonical[0]["platform_object_id"] == "vid-123"

        raw_count = evidence_repo.conn.execute(
            "SELECT count(*) FROM acquisition.raw_observations"
        ).fetchone()[0]
        assert raw_count == 2
        provider_count = evidence_repo.conn.execute(
            "SELECT provider_count FROM acquisition.social_observations"
        ).fetchone()[0]
        assert provider_count == 2
        snapshots = evidence_repo.engagement_snapshots(canonical[0]["observation_id"])
        assert len(snapshots) == 2
        assert {s["provider"] for s in snapshots} == {"youtube", "monid"}

    def test_mutable_engagement_retains_separate_timestamps(self, evidence):
        _repo, evidence_repo = evidence
        request = make_request(entity_id="radiohead", platform="youtube")
        record = {
            "platform": "youtube",
            "object_type": "video",
            "platform_object_id": "vid-1",
            "text": "clip",
            "engagement": {"views": 100},
        }
        earlier = utc_now() - timedelta(hours=2)
        later = utc_now()
        evidence_repo.ingest(
            request,
            _result(request, provider="youtube", records=[record], completed_at=earlier),
        )
        record2 = {**record, "engagement": {"views": 250}}
        evidence_repo.ingest(
            request,
            _result(request, provider="youtube", records=[record2], completed_at=later),
        )

        canonical = evidence_repo.query_observations(artist_id="radiohead")
        snapshots = evidence_repo.engagement_snapshots(canonical[0]["observation_id"])
        assert len(snapshots) == 2
        assert [s["views"] for s in snapshots] == [100, 250]
        assert snapshots[0]["retrieved_at"] != snapshots[1]["retrieved_at"]

    def test_missing_engagement_is_null_never_zero(self, evidence):
        _repo, evidence_repo = evidence
        request = make_request(entity_id="radiohead", platform="youtube")
        evidence_repo.ingest(
            request,
            _result(
                request,
                provider="youtube",
                records=[
                    {
                        "platform": "youtube",
                        "object_type": "video",
                        "platform_object_id": "vid-9",
                        "text": "no engagement data",
                    }
                ],
            ),
        )
        canonical = evidence_repo.query_observations(artist_id="radiohead")
        snapshots = evidence_repo.engagement_snapshots(canonical[0]["observation_id"])
        assert len(snapshots) == 1
        assert snapshots[0]["views"] is None
        assert snapshots[0]["likes"] is None

    def test_canonical_key_fallbacks(self):
        assert canonical_key("youtube", "v1", "https://youtu.be/v1", "h") == "youtube::v1"
        assert canonical_key("youtube", None, "https://youtu.be/v1", "h").startswith("url::")
        assert canonical_key("web", None, None, "h").startswith("hash::")
        assert canonical_key("web", None, None, None) is None

    def test_resolve_canonical_new_vs_existing(self):
        first = resolve_canonical("youtube", "v1", None, None, set())
        assert first is not None and first.is_new
        second = resolve_canonical("youtube", "v1", None, None, {first.canonical_id})
        assert second is not None and not second.is_new


# ---------------------------------------------------------------------------
# PIT behavior
# ---------------------------------------------------------------------------
class TestPIT:
    def test_future_observation_excluded_at_cutoff(self, evidence):
        _repo, evidence_repo = evidence
        request = make_request(entity_id="radiohead", platform="youtube")

        past = {
            "platform": "youtube",
            "object_type": "video",
            "platform_object_id": "vid-past",
            "text": "before cutoff",
            "published_at": "2023-12-20T00:00:00Z",
        }
        future = {
            "platform": "youtube",
            "object_type": "video",
            "platform_object_id": "vid-future",
            "text": "after cutoff",
            "published_at": "2024-02-01T00:00:00Z",
        }
        past_retrieved = datetime(2023, 12, 20, tzinfo=timezone.utc)
        future_retrieved = datetime(2024, 2, 1, tzinfo=timezone.utc)
        evidence_repo.ingest(
            request,
            _result(request, provider="youtube", records=[past], completed_at=past_retrieved),
        )
        evidence_repo.ingest(
            request,
            _result(request, provider="youtube", records=[future], completed_at=future_retrieved),
        )

        cutoff = datetime(2024, 1, 15, tzinfo=timezone.utc)
        at_cutoff = evidence_repo.query_observations(artist_id="radiohead", cutoff=cutoff)
        assert [o["platform_object_id"] for o in at_cutoff] == ["vid-past"]

        all_obs = evidence_repo.query_observations(artist_id="radiohead")
        assert len(all_obs) == 2

    def test_before_cutoff_included_when_valid(self, evidence):
        _repo, evidence_repo = evidence
        request = make_request(entity_id="radiohead", platform="youtube")
        evidence_repo.ingest(
            request,
            _result(
                request,
                provider="youtube",
                records=[
                    {
                        "platform": "youtube",
                        "object_type": "comment",
                        "platform_object_id": "c1",
                        "text": "valid at cutoff",
                        "published_at": "2024-01-01T00:00:00Z",
                    }
                ],
                completed_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
            ),
        )
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        obs = evidence_repo.query_observations(artist_id="radiohead", cutoff=cutoff)
        assert len(obs) == 1
        assert obs[0]["platform_object_id"] == "c1"

    def test_features_exclude_future_inputs(self, evidence):
        _repo, evidence_repo = evidence
        request = make_request(entity_id="radiohead", platform="youtube")
        records = [
            {
                "platform": "youtube",
                "object_type": "video",
                "platform_object_id": "v-past",
                "text": "nice live show",
                "published_at": "2024-01-05T00:00:00Z",
                "engagement": {"views": 100, "likes": 5},
            },
            {
                "platform": "youtube",
                "object_type": "video",
                "platform_object_id": "v-future",
                "text": "revealed after cutoff",
                "published_at": "2024-05-20T00:00:00Z",
                "engagement": {"views": 9000, "likes": 50},
            },
        ]
        evidence_repo.ingest(
            request,
            _result(
                request,
                provider="youtube",
                records=[records[0]],
                completed_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
            ),
        )
        evidence_repo.ingest(
            request,
            _result(
                request,
                provider="youtube",
                records=[records[1]],
                completed_at=datetime(2024, 5, 20, tzinfo=timezone.utc),
            ),
        )

        early = feature_builder.build_artist_market_features(
            evidence_repo, "radiohead", cutoff=datetime(2024, 1, 15, tzinfo=timezone.utc)
        )
        late = feature_builder.build_artist_market_features(
            evidence_repo, "radiohead", cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc)
        )
        assert early.mention_count == 1
        assert late.mention_count == 2
        # engagement and views from the future observation are NOT visible at the early cutoff
        assert early.engagement_total == 5
        assert late.engagement_total == 55
        # view_velocity_30d is a trailing-30d window metric: at the late cutoff
        # only the May observation falls inside the window.
        assert early.view_velocity_30d == 100
        assert late.view_velocity_30d == 9000
        assert early.mention_velocity_30d == 1
        assert late.mention_velocity_30d == 1


# ---------------------------------------------------------------------------
# Feature builder
# ---------------------------------------------------------------------------
class TestFeatures:
    def test_missing_evidence_is_none_not_zero(self, evidence):
        _repo, evidence_repo = evidence
        features = feature_builder.build_artist_market_features(
            evidence_repo, "artist-with-no-data"
        )
        assert features.mention_count == 0
        assert features.sentiment_mean is None
        assert features.positive_share is None
        assert features.attend_intent_share is None
        assert any("no VADER" in w for w in features.warnings)

    def test_artist_market_features_aggregate(self, evidence):
        _repo, evidence_repo = evidence
        request = make_request(entity_id="radiohead", market_id="chi")
        records = [
            {
                "platform": "youtube",
                "object_type": "comment",
                "platform_object_id": "c1",
                "text": "absolutely incredible live show, love it",
                "published_at": "2024-01-05T00:00:00Z",
                "market_id": "chi",
                "author_public_id": "user-a",
                "engagement": {"likes": 10, "views": 500},
            },
            {
                "platform": "youtube",
                "object_type": "comment",
                "platform_object_id": "c2",
                "text": "terrible sound, disappointing set",
                "published_at": "2024-01-06T00:00:00Z",
                "market_id": "chi",
                "author_public_id": "user-b",
                "engagement": {"likes": 2, "views": 200},
            },
            {
                "platform": "x",
                "object_type": "post",
                "platform_object_id": "t1",
                "text": "who's going to the show? can't wait",
                "published_at": "2024-01-07T00:00:00Z",
                "market_id": "nyc",
                "author_public_id": "user-c",
            },
        ]
        evidence_repo.ingest(
            request,
            _result(
                request,
                provider="youtube",
                records=records[:2],
                completed_at=datetime(2024, 1, 10, tzinfo=timezone.utc),
            ),
        )
        # second provider for the same x object via monid-shaped records
        request2 = make_request(entity_id="radiohead", market_id="chi", platform="x")
        evidence_repo.ingest(
            request2,
            _result(
                request2,
                provider="monid",
                records=[records[2]],
                completed_at=datetime(2024, 1, 10, tzinfo=timezone.utc),
            ),
        )

        # attach versioned inferences
        for obs in evidence_repo.query_observations(artist_id="radiohead"):
            text = obs["text"]
            inference = infer_sentiment(text)
            evidence_repo.record_text_inference(
                observation_id=obs["observation_id"],
                task="SENTIMENT",
                model_name=inference.model_name,
                model_version=inference.model_version,
                label=inference.label,
                probabilities=inference.probabilities,
                knowledge_cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc),
                input_text=text,
            )

        features = feature_builder.build_artist_market_features(
            evidence_repo,
            "radiohead",
            market_id="chi",
            cutoff=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        assert features.mention_count == 3
        assert features.platform_count == 2
        assert features.unique_author_count >= 1
        assert features.positive_share is not None and features.negative_share is not None
        assert features.local_mention_share is not None and features.local_mention_share > 0
        assert features.engagement_total == 12
        assert len(features.source_observation_ids) == 3
        assert features.evidence_quality in ("high", "medium", "low")


# ---------------------------------------------------------------------------
# Synthetic quarantine
# ---------------------------------------------------------------------------
class TestSyntheticQuarantine:
    def test_synthetic_fixtures_cannot_be_written_as_observed(self):
        with pytest.raises(ValueError):
            guard_evidence_class("SYNTHETIC_TEST_ONLY")
        assert guard_evidence_class("OBSERVED_PUBLIC") == "OBSERVED_PUBLIC"


# ---------------------------------------------------------------------------
# Knowledge time provenance
# ---------------------------------------------------------------------------
class TestProvenance:
    def test_knowledge_time_uses_publication_when_defensible(self):
        retrieved = datetime(2024, 1, 10, tzinfo=timezone.utc)
        kt = knowledge_time_for("2024-01-05T00:00:00Z", retrieved)
        assert kt == datetime(2024, 1, 5, tzinfo=timezone.utc)

    def test_knowledge_time_falls_back_to_retrieval(self):
        retrieved = datetime(2024, 1, 10, tzinfo=timezone.utc)
        kt = knowledge_time_for(None, retrieved)
        assert kt == retrieved

    def test_knowledge_time_never_invents_earlier_than_retrieval(self):
        retrieved = datetime(2024, 1, 10, tzinfo=timezone.utc)
        kt = knowledge_time_for("2026-01-01T00:00:00Z", retrieved)
        assert kt == retrieved


# ---------------------------------------------------------------------------
# Ticket spread regression guard
# ---------------------------------------------------------------------------
class TestTicketSpreadRegression:
    def test_ticket_spread_still_works(self):
        from datetime import datetime as dt

        from metrics.spread_calculator import FXTable, calculate_spread

        primary = {
            "currency": "USD",
            "total_primary_price_minor": 10000,
            "fee_components_minor": 1000,
            "created_at": dt(2026, 8, 10, tzinfo=timezone.utc),
        }
        secondary = {
            "currency": "USD",
            "total_buyer_price_minor": 14000,
            "fee_components_minor": 2000,
            "retrieved_at": dt(2026, 8, 10, 1, tzinfo=timezone.utc),
        }
        result = calculate_spread(primary, secondary, fx=FXTable({}))
        assert result.absolute_spread_minor == 4000


# ---------------------------------------------------------------------------
# Real provider -> repository integration (offline fixtures)
# ---------------------------------------------------------------------------
class TestProviderIntegration:
    def test_youtube_ingest_through_repository(self, evidence):
        _repo, evidence_repo = evidence
        transport = FakeTransport(
            [
                (200, {"items": [{"id": {"videoId": "v1"}}]}),
                (
                    200,
                    {
                        "items": [
                            {
                                "id": "v1",
                                "snippet": {
                                    "title": "Live",
                                    "channelTitle": "C",
                                    "publishedAt": "2024-01-01T00:00:00Z",
                                },
                                "statistics": {"viewCount": "10", "likeCount": "1"},
                            }
                        ]
                    },
                ),
                (200, {"items": []}),
            ]
        )
        provider = YouTubeProvider(transport=transport, env={"YOUTUBE_API_KEY": "k"})
        request = make_request(entity_id="radiohead")
        result = provider.acquire(request)
        assert result.status == AcquisitionStatus.SUCCESS
        stored = evidence_repo.ingest(request, result)
        assert stored == result.record_count
        canonical = evidence_repo.query_observations(artist_id="radiohead", cutoff=utc_now())
        assert len(canonical) == 1
        run_row = evidence_repo.conn.execute(
            "SELECT cost_usd, status FROM acquisition.acquisition_runs"
        ).fetchone()
        assert run_row[1] == "SUCCESS"
