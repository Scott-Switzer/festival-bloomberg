"""LIVE Soundcharts sandbox contract tests.

These tests hit the real Soundcharts API with the documented public sandbox
credentials (``x-app-id: soundcharts`` / ``x-api-key: soundcharts``) against
https://customer.api.soundcharts.com/api/v2 — the same base URL Soundcharts
documents for sandbox and production. They are the acceptance evidence that
the adapter's endpoint paths, query parameters, and response envelopes match
the current API: mock tests are not sufficient.

Each test performs a handful of cheap GETs against the sandbox dataset. No
paid calls and no registration are involved. If Soundcharts ever changes the
sandbox contract, these tests fail loudly instead of silently drifting.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from festival_bloomberg.acquisition.contracts import AcquisitionStatus
from festival_bloomberg.acquisition.providers.soundcharts import (
    ARTIST_BY_UUID,
    AUDIENCE_HISTORY,
    AUDIENCE_REPORT_DATES,
    CURRENT_STATS,
    LOCAL_STREAMING,
    STREAMING_HISTORY,
    SoundchartsProvider,
)

#: Public sandbox artist UUID used by Soundcharts' own SDK documentation.
SANDBOX_ARTIST_UUID = "11e81bcc-9c1c-ce38-b96b-a0369fe50396"  # Billie Eilish


@pytest.fixture(autouse=True)
def _pace_sandbox_calls():
    """Pace the live sandbox requests.

    The sandbox rejects rapid bursts from the shared public credentials with
    transient 401s even though the same call succeeds at normal pace. A short
    pause between tests keeps the contract tests live and deterministic.
    """
    yield
    time.sleep(2)

#: Known sandbox platforms exercised in the live tests.
PLATFORMS = ("spotify", "instagram")


def _provider() -> SoundchartsProvider:
    return SoundchartsProvider(sandbox=True)


def test_sandbox_credentials_are_configured():
    provider = _provider()
    assert provider.configured()
    health = provider.health()
    assert health.healthy


def test_search_path_is_documented_plan_gated_endpoint():
    # /api/v2/artist/search/{term} is documented as plan-gated ("This endpoint
    # is not included in your current plan"). A 401/403 proves the path
    # resolves through to the authorization layer; a 404 would prove the path
    # no longer exists. Either way the request never fabricates data.
    result = _provider().resolve_artist("Radiohead")
    assert result.status in (
        AcquisitionStatus.PROVIDER_ERROR,
        AcquisitionStatus.SUCCESS,
        AcquisitionStatus.NO_RESULTS,
    )
    if result.status == AcquisitionStatus.PROVIDER_ERROR:
        assert result.error_category == "authentication"
        detail = result.provider_metadata or {}
        assert "search" in str(detail.get("note", ""))


def test_artist_by_uuid_matches_documented_sandbox_artist():
    result = _provider().artist_by_uuid(SANDBOX_ARTIST_UUID)
    assert result.status == AcquisitionStatus.SUCCESS
    assert result.record_count >= 1
    record = result.records[0]
    assert record["operation"] == ARTIST_BY_UUID
    data = record["data"]
    assert data.get("uuid") == SANDBOX_ARTIST_UUID
    assert data.get("name")


def test_current_stats_matches_documented_envelope():
    result = _provider().current_stats(SANDBOX_ARTIST_UUID)
    assert result.status == AcquisitionStatus.SUCCESS
    # The documented envelope: related + social/streaming/popularity arrays.
    operations = {r["operation"] for r in result.records}
    assert any(op.startswith(CURRENT_STATS + ":") for op in operations)
    by_family = {op.split(":", 1)[1]: op for op in operations}
    assert "social" in by_family and "streaming" in by_family
    for record in result.records:
        assert record["platform"]
        assert record["data"].get("date")
        assert record["rights_status"]


def test_streaming_history_listening_matches_documented_path():
    end = datetime.now(UTC).date() - timedelta(days=1)
    start = end - timedelta(days=30)
    result = _provider().historical_streaming(
        SANDBOX_ARTIST_UUID, platform="spotify", start_time=start, end_time=end
    )
    assert result.status in (AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS)
    if result.status == AcquisitionStatus.SUCCESS:
        assert result.provider_metadata.get("operation") == STREAMING_HISTORY
        for record in result.records:
            assert "date" in record["data"]
            assert "value" in record["data"]


def test_audience_history_matches_documented_path():
    end = datetime.now(UTC).date() - timedelta(days=1)
    start = end - timedelta(days=30)
    result = _provider().historical_audience(
        SANDBOX_ARTIST_UUID, platform="instagram", start_time=start, end_time=end
    )
    assert result.status in (AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS)
    if result.status == AcquisitionStatus.SUCCESS:
        for record in result.records:
            data = record["data"]
            # Documented audience item fields.
            assert "date" in data
            assert any(
                key in data for key in ("followerCount", "likeCount", "postCount", "viewCount")
            )


def test_local_streaming_matches_documented_path():
    result = _provider().local_streaming(SANDBOX_ARTIST_UUID, platform="spotify")
    assert result.status in (AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS)
    assert result.provider_metadata.get("operation") == LOCAL_STREAMING


def test_audience_report_dates_matches_documented_path():
    result = _provider().audience_report_dates(SANDBOX_ARTIST_UUID, platform="spotify")
    assert result.status in (AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS)
    assert result.provider_metadata.get("operation") == AUDIENCE_REPORT_DATES


def test_sandbox_returns_no_error_and_records_http_evidence():
    provider = _provider()
    end = datetime.now(UTC).date()
    start = end - timedelta(days=30)
    for op in (CURRENT_STATS, STREAMING_HISTORY, AUDIENCE_HISTORY):
        result = provider._for_artist(
            SANDBOX_ARTIST_UUID, op, platform="spotify", start_time=start, end_time=end
        )
        assert result.status in (AcquisitionStatus.SUCCESS, AcquisitionStatus.NO_RESULTS)
        metadata = result.provider_metadata or {}
        assert metadata.get("http_status") == 200
        assert metadata.get("sandbox") is True