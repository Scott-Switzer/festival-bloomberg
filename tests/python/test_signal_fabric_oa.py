"""Offline tests for the Signal Fabric live operational-acceptance driver.

Everything is deterministic: no network, no paid calls. The scripted transport
stands in for Wikipedia and the driver stores real ``OBSERVED_PUBLIC`` evidence
through the canonical repository — but only after flowing through the
AcquisitionRouter -> Provider path.

Regression targets from the semantic review:
- PIT replay is scoped to the current OA run (no cross-run contamination).
- Wikipedia text is ENCYCLOPEDIC, never FAN_GENERATED.
- encyclopedic text can never become fan sentiment (fail closed to UNKNOWN).
- generic Chicago pages cannot produce artist x Chicago demand.
- no fabricated entity_resolution_confidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import pytest

from festival_bloomberg.evidence.semantics import ContentRole, is_fan_role
from festival_bloomberg.oa.operational_acceptance import (
    CANDIDATE_ARTISTS,
    CHICAGO_PAGES,
    MIN_EXTRACT_CHARS,
    SELECTION_RULE,
    _pit_replay,
    build_manifest,
    detect_chicago_mentions,
    provider_readiness,
    run_operational_acceptance,
    select_artist,
)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_candidate_list_predeclared_and_deterministic():
    assert len(CANDIDATE_ARTISTS) == 10
    assert sorted(CANDIDATE_ARTISTS) == list(CANDIDATE_ARTISTS)
    assert "availability" in SELECTION_RULE
    assert "sentiment" in SELECTION_RULE


def test_select_artist_first_alphabetical_availability_only():
    lengths = {"Bad Bunny": 300, "Beyoncé": 9000, "Billie Eilish": None}
    assert select_artist(lengths) == "Bad Bunny"


def test_select_artist_requires_minimum_extract():
    assert select_artist({"Bad Bunny": MIN_EXTRACT_CHARS - 1}) is None
    assert select_artist({"Bad Bunny": MIN_EXTRACT_CHARS}) == "Bad Bunny"


def test_detect_chicago_mentions_word_bounded():
    text = "He played the United Center in Chicago, Illinois. A Chicagoland native."
    snippets = detect_chicago_mentions(text)
    assert len(snippets) == 1
    assert "Chicago" in snippets[0]


def test_detect_chicago_mentions_none():
    assert detect_chicago_mentions(None) == []
    assert detect_chicago_mentions("No locality.") == []


def test_provider_readiness_never_leaks_values():
    env = {"YOUTUBE_API_KEY": "super-secret", "MONID_API_KEY": "also-secret"}
    readiness = provider_readiness(env)
    assert readiness["youtube"] == "CONFIGURED"
    assert readiness["monid"] == "CONFIGURED"
    assert readiness["apify"] == "NOT_CONFIGURED"
    assert readiness["http"] == "AVAILABLE"
    assert readiness["wikimedia"] == "AVAILABLE"
    assert "super-secret" not in json.dumps(readiness)


# --------------------------------------------------------------------------- #
# PIT replay scoping (contamination)
# --------------------------------------------------------------------------- #


class _FakeEvidence:
    def __init__(self, conn):
        self.conn = conn


def _make_raw_obs_table():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SCHEMA acquisition")
    conn.execute(
        """
        CREATE TABLE acquisition.raw_observations (
            observation_id VARCHAR, knowledge_time TIMESTAMP,
            retrieved_at TIMESTAMP, correlation_id VARCHAR
        )
        """
    )
    return conn


def test_pit_replay_scoped_to_oa_run():
    conn = _make_raw_obs_table()
    # 50 unrelated historical observations from a prior run
    for i in range(50):
        conn.execute(
            "INSERT INTO acquisition.raw_observations VALUES (?, ?, ?, ?)",
            [f"unrelated_{i}", "2020-01-01 00:00:00", "2020-01-01 00:00:00", "other-run"],
        )
    # 4 observations produced by THIS oa run
    conn.execute(
        "INSERT INTO acquisition.raw_observations VALUES "
        "('raw_old',   '2026-08-01T00:00:00', '2026-08-10T00:00:00', 'oa-1'),"
        "('raw_mid_a', '2026-08-10T00:00:00', '2026-08-12T00:00:00', 'oa-1'),"
        "('raw_mid_b', '2026-08-11T00:00:00', '2026-08-12T00:00:00', 'oa-1'),"
        "('raw_late',  '2026-08-14T00:00:00', '2026-08-14T00:00:00', 'oa-1')"
    )
    result = _pit_replay(_FakeEvidence(conn), "oa-1", datetime(2026, 8, 14, tzinfo=timezone.utc))

    assert result["status"] == "PASS"
    assert result["scoped_raw_count"] == 4  # only the 4 oa-1 rows, not the 50
    assert result["t1_visible_count"] == 3  # median knowledge_time = 08-11
    assert result["t2_visible_count"] == 4
    assert result["learned_after_t1"] == ["raw_late"]
    # unrelated history never leaks into the replay
    all_ids = result["t1_visible_ids"] + result["t2_visible_ids"]
    assert not any("unrelated" in oid for oid in all_ids)


def test_two_consecutive_oa_runs_do_not_contaminate():
    conn = _make_raw_obs_table()
    conn.execute(
        "INSERT INTO acquisition.raw_observations VALUES "
        "('run1_a', '2026-08-01T00:00:00', '2026-08-01T00:00:00', 'oa-run-1'),"
        "('run1_b', '2026-08-02T00:00:00', '2026-08-02T00:00:00', 'oa-run-1')"
    )
    conn.execute(
        "INSERT INTO acquisition.raw_observations VALUES "
        "('run2_a', '2026-08-10T00:00:00', '2026-08-10T00:00:00', 'oa-run-2')"
    )
    second = _pit_replay(_FakeEvidence(conn), "oa-run-2", datetime(2026, 8, 14, tzinfo=timezone.utc))
    assert second["scoped_raw_count"] == 1
    assert second["t1_visible_ids"] == ["run2_a"]


def test_pit_replay_not_evaluated_when_empty():
    conn = _make_raw_obs_table()
    result = _pit_replay(_FakeEvidence(conn), "oa-1", datetime(2026, 8, 14, tzinfo=timezone.utc))
    assert result["status"] == "NOT_EVALUATED"


# --------------------------------------------------------------------------- #
# Manifest schema
# --------------------------------------------------------------------------- #


def test_build_manifest_schema_and_no_raw_text():
    manifest = build_manifest(
        market="Chicago, IL",
        lookback_days=30,
        budget_usd=0.0,
        oa_run_id="oa-1",
        selection={"selected_artist": "Bad Bunny"},
        readiness={"http": "AVAILABLE"},
        statuses={"MARKET_CONTEXT": "PASS", "ARTIST_MARKET_DEMAND_SIGNAL": "INSUFFICIENT_EVIDENCE"},
        observations=[
            {
                "title": "United Center",
                "kind": "venue",
                "platform": "wikipedia",
                "provider": "wikimedia",
                "observation_id": "raw_1",
                "canonical_id": "canon_1",
                "source_url": "https://en.wikipedia.org/wiki/United_Center",
                "content_role": "ENCYCLOPEDIC",
                "resolution_method": "EXACT_CANONICAL_URL",
                "source_revision_id": "123",
                "source_revision_time": "2026-08-12T00:00:00Z",
                "knowledge_time": "2026-08-12T00:00:00+00:00",
                "content_hash": "abc123",
                "text_chars": 447,
                "market_id": "Chicago, IL",
                "license": "CC BY-SA 4.0",
                "raw_count": 1,
            }
        ],
        content_role_distribution={"ENCYCLOPEDIC": 1},
        vader_distribution={"positive": 1},
        tweetnlp_status="NOT_AVAILABLE",
        pit_replay={"status": "PASS"},
        cost_usd=0.0,
        generated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        db_path="/tmp/x.duckdb",
    )

    assert manifest["schema_version"] == "2.0"
    assert manifest["no_fabricated_data"] is True
    assert manifest["no_paid_calls"] is True
    assert manifest["cost_usd"] == 0.0
    assert manifest["statuses"]["ARTIST_MARKET_DEMAND_SIGNAL"] == "INSUFFICIENT_EVIDENCE"
    # fan sentiment is never asserted from encyclopedic text
    assert manifest["nlp"]["fan_sentiment"]["status"] == "NOT_EVALUATED"
    # raw text must not be embedded
    payload = json.dumps(manifest)
    assert '"text"' not in payload
    assert "extract" not in payload
    item = manifest["observations"]["items"][0]
    assert item["content_role"] == "ENCYCLOPEDIC"
    assert item["resolution_method"] == "EXACT_CANONICAL_URL"


# --------------------------------------------------------------------------- #
# Scripted end-to-end run (offline)
# --------------------------------------------------------------------------- #


class _FakeTransport:
    def __init__(self, responses):
        self._responses = list(responses)

    def request(self, method, url, *, headers=None, params=None, body=None, timeout_seconds=30.0):
        from festival_bloomberg.acquisition.transport import HttpResponse

        payload = self._responses.pop(0)
        return HttpResponse(200, json.dumps(payload).encode("utf-8"), {})


def _wiki_page(title, revid, timestamp, extract):
    return {
        "query": {
            "pages": [
                {
                    "title": title,
                    "revisions": [{"revid": revid, "timestamp": timestamp}],
                    "extract": extract,
                }
            ]
        }
    }


def _scripted_responses():
    """Responses in the exact order the driver requests them.

    Selection stops after the first alphabetically-qualifying candidate
    (Bad Bunny), then the two Chicago context pages are collected.
    """
    return [
        _wiki_page("Bad Bunny", 111, "2026-08-01T00:00:00Z", "Reggaeton superstar. " * 20),
        _wiki_page("United Center", 222, "2026-08-10T00:00:00Z",
                   "An indoor arena on the Near West Side of Chicago, Illinois."),
        _wiki_page("Lollapalooza", 333, "2026-08-10T00:00:00Z",
                   "A music festival held in Grant Park in Chicago."),
    ]


def test_run_oa_offline_scripted(tmp_path):
    from festival_bloomberg.evidence.repository import EvidenceRepository
    from festival_bloomberg.warehouse.repository import FestivalRepository

    repo = FestivalRepository(str(tmp_path / "oa.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        transport = _FakeTransport(_scripted_responses())
        manifest = run_operational_acceptance(
            evidence,
            market="Chicago, IL",
            budget_usd=0.0,
            db_path=str(tmp_path / "oa.duckdb"),
            transport=transport,
        )
    finally:
        repo.close()

    assert manifest["artist_selection"]["selected_artist"] == "Bad Bunny"
    assert manifest["artist_selection"]["selection_basis"] == "availability_metadata_only"
    assert manifest["provider_readiness"]["wikimedia"] == "AVAILABLE"
    assert manifest["observations"]["raw_count"] == 3
    assert manifest["observations"]["canonical_count"] == 3
    assert manifest["observations"]["platforms"] == ["wikipedia"]
    assert manifest["observations"]["providers"] == ["wikimedia"]  # routed through the provider
    assert manifest["observations"]["content_role_distribution"] == {"ENCYCLOPEDIC": 3}

    # separated semantics
    assert manifest["statuses"]["ACQUISITION_PIPELINE"] == "PASS"
    assert manifest["statuses"]["WIKIMEDIA_TEXT_PIPELINE"] == "PASS"
    assert manifest["statuses"]["FAN_GENERATED_DATA"] == "NOT_EVALUATED"
    assert manifest["statuses"]["REAL_SOCIAL_NLP"] == "NOT_EVALUATED"
    assert manifest["statuses"]["MARKET_CONTEXT"] == "PASS"
    assert manifest["statuses"]["ARTIST_MARKET_RELATION"] == "INSUFFICIENT_EVIDENCE"
    assert manifest["statuses"]["ARTIST_MARKET_DEMAND_SIGNAL"] == "INSUFFICIENT_EVIDENCE"

    assert manifest["nlp"]["text_sentiment"]["status"] == "PASS"
    assert manifest["nlp"]["fan_sentiment"]["status"] == "NOT_EVALUATED"
    assert manifest["nlp"]["tweetnlp"]["status"] == "NOT_AVAILABLE"

    assert manifest["pit_replay"]["status"] == "PASS"
    assert manifest["pit_replay"]["scoped_raw_count"] == 3
    assert manifest["cost_usd"] == 0.0
    assert manifest["no_fabricated_data"] is True

    rows = repo.conn.execute(
        "SELECT DISTINCT evidence_class FROM acquisition.raw_observations"
    ).fetchall()
    assert [r[0] for r in rows] == ["OBSERVED_PUBLIC"]
    # every observation carries a resolution method, never a fabricated confidence
    methods = {
        r[0] for r in repo.conn.execute(
            "SELECT DISTINCT resolution_method FROM acquisition.social_observations"
        ).fetchall()
    }
    assert methods == {"EXACT_CANONICAL_URL"}
    confidences = repo.conn.execute(
        "SELECT entity_resolution_confidence FROM acquisition.social_observations"
    ).fetchall()
    assert all(r[0] is None for r in confidences)


# --------------------------------------------------------------------------- #
# Content role + fan sentiment fail-closed
# --------------------------------------------------------------------------- #


def test_wikipedia_role_is_encyclopedic_not_fan():
    assert is_fan_role(ContentRole.ENCYCLOPEDIC.value) is False
    assert is_fan_role(ContentRole.FAN_GENERATED.value) is True
    assert is_fan_role(None) is False


def test_fan_sentiment_unknown_without_fan_evidence(tmp_path):
    from festival_bloomberg.acquisition.contracts import AcquisitionResult, AcquisitionStatus, utc_now
    from festival_bloomberg.evidence.repository import EvidenceRepository
    from festival_bloomberg.social.features import fan_sentiment_distribution
    from festival_bloomberg.warehouse.repository import FestivalRepository

    repo = FestivalRepository(str(tmp_path / "fan.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        request = _make_request_fixture("radiohead")
        records = [
            {
                "platform": "wikipedia",
                "object_type": "encyclopedic_article",
                "platform_object_id": f"rev{i}",
                "text": f"Encyclopedic sentence about the artist number {i}.",
                "content_role": "ENCYCLOPEDIC",
                "content_role_method": "source_type",
                "resolution_method": "EXACT_CANONICAL_URL",
                "canonical_url": f"https://en.wikipedia.org/wiki/X_{i}",
                "published_at": None,
                "source_revision_id": f"rev{i}",
                "source_revision_time": "2026-08-01T00:00:00Z",
                "knowledge_time_source": "source_revision",
                "content_hash": f"h{i}",
            }
            for i in range(100)
        ]
        result = AcquisitionResult(
            request_id=request.request_id,
            provider="wikimedia",
            provider_endpoint=None,
            status=AcquisitionStatus.SUCCESS,
            started_at=utc_now(),
            completed_at=utc_now(),
            record_count=100,
            cost_usd=0.0,
            provider_metadata={},
            records=tuple(records),
        )
        evidence.ingest(request, result)

        distribution, status = fan_sentiment_distribution(evidence, "radiohead")
        assert status == "UNKNOWN"
        assert distribution is None
    finally:
        repo.close()


def _make_request_fixture(entity_id: str):
    from festival_bloomberg.acquisition.contracts import AcquisitionRequest

    return AcquisitionRequest.new(
        entity_id=entity_id,
        entity_type="artist",
        platform="wikipedia",
        query="Radiohead",
        commercial_context="research",
        correlation_id="test-oa",
    )
