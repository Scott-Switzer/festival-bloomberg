"""Factor-tape comparability and rights fail-closed contract tests.

A percentage change on the artist tape is a financial-grade claim. It is only
produced when the two observations are like-for-like: same factor identity
(name/platform/unit) AND same measurement context (basis, window, population,
geography, methodology, coverage generation). Anything else is
NOT_COMPARABLE — the mathematically correct percent from unlike observations
would be economically meaningless.

This module also pins the rights fail-closed default: unknown sources must
never default to SOURCE_LICENSE_REVIEWED.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest
from festival_bloomberg.security.artist_factor_tape import (
    build_factor_observation,
    comparability_of,
    comparable_delta,
    what_changed,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _obs(**overrides):
    base = dict(
        artist_key="mbid::test-artist",
        factor_family="STREAMING",
        factor_name="LISTEN_COUNT",
        platform="listenbrainz",
        value=1000,
        unit="listens",
        observation_time=datetime(2026, 8, 1, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 2, tzinfo=UTC),
        source="listenbrainz",
        measurement_basis="TOTAL_WITHIN_WINDOW",
        measurement_window="2026-06-01..2026-08-01",
        population_scope="ALL_LISTENBRAINZ_USERS",
        geographic_scope="GLOBAL",
        methodology_version="listenbrainz_external_stats_v1",
        coverage_generation="artist_factor_tape_demo_v1",
    )
    base.update(overrides)
    return build_factor_observation(**base)


def test_like_for_like_observations_produce_delta():
    older = _obs(value=1000, observation_time=datetime(2026, 7, 1, tzinfo=UTC))
    newer = _obs(value=1100, observation_time=datetime(2026, 8, 1, tzinfo=UTC))
    delta = comparable_delta([older, newer])
    assert delta is not None
    assert delta["delta_pct"] == pytest.approx(10.0)
    assert delta["comparability"] == "COMPARABLE"


def test_different_measurement_window_is_not_comparable():
    older = _obs(value=1000, observation_time=datetime(2026, 7, 1, tzinfo=UTC))
    newer = _obs(
        value=334,  # would look like -66.6% if compared blindly
        observation_time=datetime(2026, 8, 1, tzinfo=UTC),
        measurement_window="2026-01-01..2026-08-01",  # different window
    )
    comparable, reason = comparability_of(older, newer)
    assert not comparable
    assert "measurement_window" in (reason or "")
    assert comparable_delta([older, newer]) is None


def test_different_measurement_basis_is_not_comparable():
    older = _obs(value=1000, observation_time=datetime(2026, 7, 1, tzinfo=UTC))
    newer = _obs(
        value=500,
        observation_time=datetime(2026, 8, 1, tzinfo=UTC),
        measurement_basis="TOTAL_ALL_TIME",
    )
    assert comparable_delta([older, newer]) is None


def test_missing_context_is_not_comparable():
    older = _obs(value=1000, observation_time=datetime(2026, 7, 1, tzinfo=UTC))
    newer = dict(older)
    newer["value"] = 900
    newer["observation_time"] = datetime(2026, 8, 1, tzinfo=UTC)
    for field in (
        "measurement_basis",
        "measurement_window",
        "population_scope",
        "geographic_scope",
        "methodology_version",
        "coverage_generation",
    ):
        stripped = dict(newer)
        stripped[field] = None
        # Remove the field from evidence_json too — a legacy row that never
        # carried context at all must not compare.
        evidence = dict(stripped["evidence_json"] or {})
        evidence.pop(field, None)
        stripped["evidence_json"] = evidence or None
        comparable, reason = comparability_of(older, stripped)
        assert not comparable
        assert field in (reason or "")


def test_what_changed_excludes_incomparable_series():
    older = _obs(value=1000, observation_time=datetime(2026, 7, 1, tzinfo=UTC))
    incomparable = _obs(
        value=334,
        observation_time=datetime(2026, 8, 1, tzinfo=UTC),
        measurement_window="2026-01-01..2026-08-01",
    )
    comparable_older = _obs(
        factor_name="FOLLOWERS",
        value=5000,
        observation_time=datetime(2026, 7, 1, tzinfo=UTC),
    )
    comparable_newer = _obs(
        factor_name="FOLLOWERS",
        value=5250,
        observation_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    changes = what_changed([older, incomparable, comparable_older, comparable_newer])
    names = {(c["factor_name"], c["platform"]) for c in changes}
    assert ("FOLLOWERS", "listenbrainz") in names
    assert ("LISTEN_COUNT", "listenbrainz") not in names


def test_legacy_rows_without_context_never_produce_delta():
    older = _obs(value=1000, observation_time=datetime(2026, 7, 1, tzinfo=UTC))
    older["measurement_basis"] = None
    older["measurement_window"] = None
    older["evidence_json"] = None
    newer = dict(older)
    newer["value"] = 500
    newer["observation_time"] = datetime(2026, 8, 1, tzinfo=UTC)
    assert comparable_delta([older, newer]) is None


def test_evidence_json_fallback_supports_comparison():
    older = _obs(value=1000, observation_time=datetime(2026, 7, 1, tzinfo=UTC))
    newer = dict(_obs(value=1100, observation_time=datetime(2026, 8, 1, tzinfo=UTC)))
    # Simulate an older serving row that only carries context in evidence_json.
    for row in (older, newer):
        for field in (
            "measurement_basis",
            "measurement_window",
            "population_scope",
            "geographic_scope",
            "methodology_version",
            "coverage_generation",
        ):
            row[field] = None
    delta = comparable_delta([older, newer])
    assert delta is not None
    assert delta["delta_pct"] == pytest.approx(10.0)


def test_unknown_rights_fail_closed_by_default():
    row = _obs(value=1, observation_time=datetime(2026, 8, 1, tzinfo=UTC))
    assert row["rights_status"] == "TERMS_REVIEW_REQUIRED"


def test_demo_materializer_never_defaults_unknown_to_reviewed():
    script = (ROOT / "scripts" / "build_artist_intelligence_demo_gen.py").read_text()
    assert "ELSE 'RIGHTS_REVIEW_REQUIRED' END" in script
    assert "ELSE 'SOURCE_LICENSE_REVIEWED' END" not in script