"""Regression coverage for NATIONAL_COVERAGE_ENTITY_MASTER_AND_FESTIVAL_HISTORY_V1.

Covers the two semantic fixes that matter most this turn:

1. Ticketmaster pagination can no longer silently truncate: a partition whose
   provider-reported total exceeds the retrieval ceiling is reported
   ``truncated=True`` (the driver then SPLITS it), and a partition under the
   ceiling reports ``complete=True``. The old 5-page/100-row cap is gone.
2. ListenBrainz is a real no-auth provider: artist statistics are keyed by
   MBID, 204/404 is ``missing`` (never zero), 429 is ``RATE_LIMITED``, and the
   collector persists LISTENBRAINZ_LISTEN_COUNT / LISTENBRAINZ_LISTENER_COUNT
   as ATTENTION_CONSUMPTION_SAMPLE rows.

All offline via FakeTransport / in-memory DuckDB.
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import pytest

from festival_bloomberg.acquisition.contracts import (
    AcquisitionResult,
    AcquisitionStatus,
)
from festival_bloomberg.acquisition.providers.listenbrainz import ListenBrainzProvider
from festival_bloomberg.acquisition.providers.ticketmaster import TicketmasterProvider
from festival_bloomberg.attention.listenbrainz import collect_artist_listen_counts
from festival_bloomberg.migrations import apply_pending_migrations

from conftest import FakeTransport, make_request


# ---------------------------------------------------------------------------
# Ticketmaster pagination: no silent truncation
# ---------------------------------------------------------------------------
def _tm_page(number, size, total, event_ids):
    return {
        "_embedded": {"events": [{"id": eid, "name": f"Event {eid}",
                                  "dates": {"start": {"localDate": "2026-09-01"}}} for eid in event_ids]},
        "page": {"totalElements": total, "totalPages": -(-total // size), "number": number, "size": size},
    }


def test_pagination_reports_truncated_when_total_exceeds_cap():
    # totalElements=10, size=1, max_records=3 -> stops at 3 records, truncated.
    pages = [(200, _tm_page(i, 1, 10, [f"E{i}"])) for i in range(3)]
    transport = FakeTransport(pages)
    provider = TicketmasterProvider(transport=transport, env={"TICKETMASTER_API_KEY": "k"})
    result = provider.acquire(make_request(platform="ticketmaster", max_records=3))
    meta = result.provider_metadata["pagination"]
    assert meta["truncated"] is True
    assert meta["complete"] is False
    assert meta["coverage_status"] == "TRUNCATED_BY_CAP"
    assert meta["items_fetched"] == 3
    assert result.record_count == 3


def test_pagination_complete_when_total_under_cap():
    pages = [(200, _tm_page(i, 1, 2, [f"E{i}"])) for i in range(2)]
    transport = FakeTransport(pages)
    provider = TicketmasterProvider(transport=transport, env={"TICKETMASTER_API_KEY": "k"})
    result = provider.acquire(make_request(platform="ticketmaster", max_records=10))
    meta = result.provider_metadata["pagination"]
    assert meta["truncated"] is False
    assert meta["complete"] is True
    assert meta["coverage_status"] == "COMPLETE"
    assert result.record_count == 2


def test_pagination_empty_early_page_is_truncation_not_complete():
    # Provider reports 1000 total but returns an empty page before we reached
    # it — that is truncation, not a "complete" empty result.
    transport = FakeTransport([(200, _tm_page(0, 50, 1000, [f"E{i}" for i in range(50)])), (200, {})])
    provider = TicketmasterProvider(transport=transport, env={"TICKETMASTER_API_KEY": "k"})
    result = provider.acquire(make_request(platform="ticketmaster", max_records=1000))
    meta = result.provider_metadata["pagination"]
    assert meta["truncated"] is True
    assert meta["complete"] is False


# ---------------------------------------------------------------------------
# Recursive date-window split (the driver behavior the cap fix enables)
# ---------------------------------------------------------------------------
class _StubProvider:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def acquire(self, request):
        self.calls.append(request)
        return self._results.pop(0)


def _result(status, *, reported_total, truncated, complete, items_fetched):
    return AcquisitionResult(
        request_id="r",
        provider="ticketmaster",
        provider_endpoint="https://app.ticketmaster.com/discovery/v2/events.json",
        status=status,
        started_at=datetime(2026, 8, 15, 12, 0, 0),
        completed_at=datetime(2026, 8, 15, 12, 0, 1),
        provider_metadata={
            "pagination": {
                "reported_total": reported_total,
                "truncated": truncated,
                "complete": complete,
                "items_fetched": items_fetched,
                "pages_fetched": 20,
            }
        },
        records=(),
    )


def test_sweep_window_splits_oversized_partition_recursively(tmp_path):
    from festival_bloomberg.oa.data_fabric import _sweep_window

    conn = duckdb.connect(str(tmp_path / "split.duckdb"))
    apply_pending_migrations(conn)

    provider = _StubProvider([
        _result(AcquisitionStatus.SUCCESS, reported_total=2500, truncated=True,
                complete=False, items_fetched=1000),
        _result(AcquisitionStatus.SUCCESS, reported_total=900, truncated=False,
                complete=True, items_fetched=900),
        _result(AcquisitionStatus.SUCCESS, reported_total=900, truncated=False,
                complete=True, items_fetched=900),
    ])
    summary = {
        "status": "RUNNING", "configured": True, "partitions": 0,
        "partitions_complete": 0, "partitions_truncated": 0,
        "partitions_split": 0, "events_persisted": 0, "requests": 0,
        "rate_limited": 0, "provider_errors": 0, "distinct_events": 0,
    }
    _sweep_window(
        conn, provider, "Chicago", "IL",
        datetime(2026, 8, 15), datetime(2027, 8, 15),
        depth=0, parent_id=None, summary=summary,
        run_retrieved="2026-08-15T12:00:00+00:00",
    )
    assert summary["partitions"] == 3
    assert summary["partitions_split"] == 1
    assert summary["partitions_complete"] == 2
    assert summary["partitions_truncated"] == 0

    rows = conn.execute(
        "SELECT depth, status, truncated FROM terminal.acquisition_partitions "
        "ORDER BY depth, status"
    ).fetchall()
    statuses = [(r[0], r[1], r[2]) for r in rows]
    # Parent SPLIT at depth 0, two COMPLETE leaves at depth 1.
    assert (0, "SPLIT", True) in statuses
    assert statuses.count((1, "COMPLETE", False)) == 2
    conn.close()


# ---------------------------------------------------------------------------
# One snapshot per (event, run) — split re-fetches are not new observations
# ---------------------------------------------------------------------------
def test_event_snapshot_dedupes_within_run(tmp_path):
    from festival_bloomberg.oa.data_fabric import _persist_event_snapshot

    conn = duckdb.connect(str(tmp_path / "snap.duckdb"))
    apply_pending_migrations(conn)
    rec = {"platform_object_id": "E1", "event_name": "Show"}
    r1 = "2026-08-15T12:00:00+00:00"
    r2 = "2026-08-16T12:00:00+00:00"
    # Same run retrieved_at -> one row (parent + child re-fetch dedupes).
    assert _persist_event_snapshot(conn, rec, r1) is True
    assert _persist_event_snapshot(conn, rec, r1) is False
    # A later run is a genuine second observation.
    assert _persist_event_snapshot(conn, rec, r2) is True
    rows = conn.execute(
        "SELECT COUNT(*) FROM events.provider_event_snapshots WHERE platform_object_id='E1'"
    ).fetchone()[0]
    assert rows == 2
    conn.close()


# ---------------------------------------------------------------------------
# ListenBrainz provider semantics
# ---------------------------------------------------------------------------
LISTENERS_PAYLOAD = {
    "payload": {
        "artist_mbid": "00034ede-a1f1-4219-be39-02f36853373e",
        "artist_name": "O Rappa",
        "from_ts": 1009843200,
        "to_ts": 1681777035,
        "total_listen_count": 16393,
        "listeners": [
            {"listen_count": 2469, "user_name": "a"},
            {"listen_count": 1858, "user_name": "b"},
        ],
        "range": "all_time",
        "last_updated": 1681839677,
    }
}


def test_listenbrainz_normalizes_listen_count():
    provider = ListenBrainzProvider(transport=FakeTransport([(200, LISTENERS_PAYLOAD)]))
    result = provider.acquire(
        make_request(platform="listenbrainz", operation="ARTIST_LISTENERS",
                     external_id="00034ede-a1f1-4219-be39-02f36853373e")
    )
    assert result.status == AcquisitionStatus.SUCCESS
    rec = result.records[0]
    assert rec["artist_mbid"] == "00034ede-a1f1-4219-be39-02f36853373e"
    assert rec["total_listen_count"] == 16393
    assert rec["listener_count_sample"] == 2
    assert rec["content_role"] == "ATTENTION_CONSUMPTION_SAMPLE"


def test_listenbrainz_404_is_missing_not_zero():
    provider = ListenBrainzProvider(transport=FakeTransport([(404, {"error": "not found"})]))
    result = provider.acquire(
        make_request(platform="listenbrainz", external_id="missing-mbid")
    )
    assert result.status == AcquisitionStatus.NO_RESULTS
    assert result.provider_metadata["missing"] is True
    assert result.record_count == 0


def test_listenbrainz_429_is_rate_limited():
    provider = ListenBrainzProvider(transport=FakeTransport([(429, {"error": "slow down"})]))
    result = provider.acquire(
        make_request(platform="listenbrainz", external_id="some-mbid")
    )
    assert result.status == AcquisitionStatus.RATE_LIMITED


def test_listenbrainz_requires_mbid():
    provider = ListenBrainzProvider(transport=FakeTransport([]))
    result = provider.acquire(make_request(platform="listenbrainz"))
    assert result.status == AcquisitionStatus.SCHEMA_INVALID
    assert result.error_category == "mbid_required"


def test_listenbrainz_collector_persists_observations(tmp_path):
    conn = duckdb.connect(str(tmp_path / "lb.duckdb"))
    apply_pending_migrations(conn)
    transport = FakeTransport([(200, LISTENERS_PAYLOAD)])
    summary = collect_artist_listen_counts(
        conn, transport,
        artists=[("O Rappa", "00034ede-a1f1-4219-be39-02f36853373e"),
                 ("No MBID Artist", "")],
        min_interval_seconds=0,
    )
    assert summary["status"] == "COMPLETE"
    assert summary["artists_resolved"] == 1
    assert summary["rows_persisted"] == 2
    rows = conn.execute(
        "SELECT metric_kind, value, value_unit FROM metrics.artist_attention_observations "
        "ORDER BY metric_kind"
    ).fetchall()
    by_kind = {r[0]: (r[1], r[2]) for r in rows}
    assert by_kind["LISTENBRAINZ_LISTEN_COUNT"] == (16393.0, "listens")
    assert by_kind["LISTENBRAINZ_LISTENER_COUNT"] == (2.0, "listeners")
    # The no-MBID artist contributed no row (NULL stays NULL).
    assert len(rows) == 2
    conn.close()
