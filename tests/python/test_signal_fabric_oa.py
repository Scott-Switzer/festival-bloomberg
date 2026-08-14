"""Offline tests for the Signal Fabric live operational-acceptance driver.

Everything here is deterministic: no network, no paid calls, no synthetic
observations written through the production evidence path (the scripted
transport stands in for Wikipedia, and the driver itself stores the results
as ``OBSERVED_PUBLIC`` evidence through the real repository).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import pytest

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
    # selection rule is documented and availability-only
    assert "availability" in SELECTION_RULE
    assert "sentiment" in SELECTION_RULE


def test_select_artist_first_alphabetical_availability_only():
    # Beyoncé resolves with a huge extract, but Bad Bunny comes first
    # alphabetically and also qualifies — Bad Bunny must win.
    lengths = {
        "Bad Bunny": 300,
        "Beyoncé": 9000,
        "Billie Eilish": None,  # did not resolve
    }
    assert select_artist(lengths) == "Bad Bunny"


def test_select_artist_requires_minimum_extract():
    assert select_artist({"Bad Bunny": MIN_EXTRACT_CHARS - 1}) is None
    assert select_artist({"Bad Bunny": MIN_EXTRACT_CHARS}) == "Bad Bunny"


def test_select_artist_none_when_none_qualify():
    assert select_artist({}) is None
    assert select_artist({"Beyoncé": 1, "Drake": None}) is None


def test_detect_chicago_mentions_word_bounded():
    text = "He played the United Center in Chicago, Illinois. A Chicagoland native attended."
    snippets = detect_chicago_mentions(text)
    assert len(snippets) == 1  # "Chicagoland" must not match
    assert "Chicago" in snippets[0]


def test_detect_chicago_mentions_none():
    assert detect_chicago_mentions(None) == []
    assert detect_chicago_mentions("No locality here.") == []


def test_provider_readiness_never_leaks_values():
    env = {"YOUTUBE_API_KEY": "super-secret-value", "MONID_API_KEY": "also-secret"}
    readiness = provider_readiness(env)
    # only presence is reported; values are never embedded
    assert readiness["youtube"] == "CONFIGURED"
    assert readiness["monid"] == "CONFIGURED"
    assert readiness["apify"] == "NOT_CONFIGURED"
    assert readiness["http"] == "AVAILABLE"
    assert readiness["scrapling"] in ("AVAILABLE", "NOT_AVAILABLE")
    assert "super-secret-value" not in json.dumps(readiness)


# --------------------------------------------------------------------------- #
# Manifest schema
# --------------------------------------------------------------------------- #


def test_build_manifest_schema_and_no_raw_text():
    manifest = build_manifest(
        market="Chicago, IL",
        lookback_days=30,
        budget_usd=0.0,
        selection={"selected_artist": "Bad Bunny"},
        readiness={"http": "AVAILABLE"},
        observations=[
            {
                "title": "United Center",
                "kind": "venue",
                "platform": "wikimedia",
                "provider": "http",
                "observation_id": "raw_1",
                "canonical_id": "canon_1",
                "source_url": "https://en.wikipedia.org/wiki/United_Center",
                "published_at": "2026-08-12T00:00:00+00:00",
                "knowledge_time": "2026-08-12T00:00:00+00:00",
                "content_hash": "abc123",
                "text_chars": 447,
                "market_id": "Chicago, IL",
                "geographic_confidence": "low",
                "license": "CC BY-SA (attribution required)",
            }
        ],
        vader_distribution={"positive": 1},
        tweetnlp_status="NOT_AVAILABLE",
        chicago={"status": "PASS"},
        pit_replay={"status": "PASS"},
        cost_usd=0.0,
        generated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        db_path="/tmp/x.duckdb",
    )

    assert manifest["schema_version"] == "1.0"
    assert manifest["no_fabricated_data"] is True
    assert manifest["no_paid_calls"] is True
    assert manifest["cost_usd"] == 0.0
    # raw text must not be embedded in the manifest
    payload = json.dumps(manifest)
    assert '"text"' not in payload
    assert "extract" not in payload
    # observation metadata is present and complete
    item = manifest["observations"]["items"][0]
    assert item["text_chars"] == 447
    assert item["market_id"] == "Chicago, IL"


# --------------------------------------------------------------------------- #
# PIT replay logic (deterministic, in-memory)
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
            observation_id VARCHAR, knowledge_time TIMESTAMP, retrieved_at TIMESTAMP
        )
        """
    )
    return conn


def test_pit_replay_excludes_future_knowledge():
    conn = _make_raw_obs_table()
    conn.execute(
        """
        INSERT INTO acquisition.raw_observations VALUES
            ('raw_old',    '2026-08-01T00:00:00', '2026-08-10T00:00:00'),
            ('raw_mid_a',  '2026-08-10T00:00:00', '2026-08-12T00:00:00'),
            ('raw_mid_b',  '2026-08-11T00:00:00', '2026-08-12T00:00:00'),
            ('raw_late',   '2026-08-14T00:00:00', '2026-08-14T00:00:00')
        """
    )
    evidence = _FakeEvidence(conn)
    result = _pit_replay(evidence, [], datetime(2026, 8, 14, tzinfo=timezone.utc))

    assert result["status"] == "PASS"
    # median knowledge_time is 08-11 -> raw_old, raw_mid_a, raw_mid_b visible
    assert result["t1_visible_count"] == 3
    assert result["t2_visible_count"] == 4
    assert result["learned_after_t1"] == ["raw_late"]
    assert "raw_old" not in result["learned_after_t1"]


def test_pit_replay_not_evaluated_when_empty():
    conn = _make_raw_obs_table()
    result = _pit_replay(_FakeEvidence(conn), [], datetime(2026, 8, 14, tzinfo=timezone.utc))
    assert result["status"] == "NOT_EVALUATED"


# --------------------------------------------------------------------------- #
# Scripted end-to-end run (offline)
# --------------------------------------------------------------------------- #

SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"


def _summary(extract: str, timestamp: str, title: str) -> dict:
    return {
        "extract": extract,
        "timestamp": timestamp,
        "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title}"}},
    }


def _scripted_responses():
    """Responses in the exact order the driver requests them."""
    short = "A recording artist."  # < MIN_EXTRACT_CHARS
    responses = []
    for artist in sorted(CANDIDATE_ARTISTS):
        if artist == "Bad Bunny":
            responses.append(
                _summary("Reggaeton superstar. " * 20, "2026-08-01T00:00:00Z", "Bad_Bunny")
            )
        else:
            responses.append(_summary(short, "2026-08-01T00:00:00Z", artist.replace(" ", "_")))
    # full text for the selected artist (action=query endpoint)
    responses.append(
        {
            "query": {
                "pages": {
                    "123": {
                        "extract": "Long article about Bad Bunny and his tour.",
                        "touched": None,
                    }
                }
            }
        }
    )
    # Chicago context pages
    responses.append(
        _summary(
            "An indoor arena on the Near West Side of Chicago, Illinois.",
            "2026-08-10T00:00:00Z",
            "United_Center",
        )
    )
    responses.append(
        _summary(
            "A music festival held in Grant Park in Chicago.",
            "2026-08-10T00:00:00Z",
            "Lollapalooza",
        )
    )
    return responses


class _FakeTransport:
    """Minimal scripted transport returning pre-arranged JSON responses."""

    def __init__(self, responses):
        self._responses = list(responses)

    def request(self, method, url, *, headers=None, params=None, body=None, timeout_seconds=30.0):
        from festival_bloomberg.acquisition.transport import HttpResponse

        payload = self._responses.pop(0)
        return HttpResponse(200, json.dumps(payload).encode("utf-8"), {})


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
    assert manifest["provider_readiness"]["http"] == "AVAILABLE"
    assert manifest["observations"]["raw_count"] == 4
    assert manifest["observations"]["canonical_count"] == 3
    assert manifest["observations"]["platforms"] == ["wikimedia"]
    assert manifest["nlp"]["vader"]["status"] == "PASS"
    assert sum(manifest["nlp"]["vader"]["distribution"].values()) == 4
    assert manifest["nlp"]["tweetnlp"]["status"] == "NOT_AVAILABLE"
    assert manifest["chicago"]["status"] == "PASS"
    assert manifest["pit_replay"]["status"] == "PASS"
    assert manifest["pit_replay"]["t1_visible_count"] == 3
    assert manifest["pit_replay"]["t2_visible_count"] == 4
    assert len(manifest["pit_replay"]["learned_after_t1"]) == 1
    assert manifest["cost_usd"] == 0.0
    assert manifest["no_fabricated_data"] is True

    # The driver must NOT write synthetic evidence; evidence_class is OBSERVED_PUBLIC
    rows = repo.conn.execute(
        "SELECT DISTINCT evidence_class FROM acquisition.raw_observations"
    ).fetchall()
    assert [r[0] for r in rows] == ["OBSERVED_PUBLIC"]
