"""Offline regressions for Forward Market History V1.

Covers: tracked registry idempotency + lifecycle, collector append-only and
same-bucket dedup, collector lock concurrency, secret-safe run logging,
failed runs preserving DB state, United Center merge idempotency and alias
dedup, nearby/same-name non-merge, venue parity accounting, two-snapshot PIT
visibility, and LaunchAgent wrapper failure paths. No network and no paid
calls.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from festival_bloomberg.acquisition.contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    utc_now,
)
from festival_bloomberg.acquisition.providers.ticketmaster import TicketmasterProvider
from festival_bloomberg.economics.collector import CollectorLock, LockHeldError, snapshot_event
from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.economics.runlog import (
    EXIT_ERROR,
    EXIT_LOCK_HELD,
    EXIT_NO_ACTIVE_EVENTS,
    EXIT_SUCCESS,
    RunLogger,
    persist_run_to_db,
)
from festival_bloomberg.economics.snapshots import (
    describe_price_change,
    primary_snapshots_from_ticketmaster,
    snapshot_bucket,
    snapshot_change_semantics,
)
from festival_bloomberg.economics.tracking import (
    TRACKING_ACTIVE,
    TRACKING_EXPIRED,
    TrackedEventRegistry,
)
from festival_bloomberg.economics.venues import (
    normalize_venue_name,
    strip_sponsor_prefix,
)
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.warehouse.repository import FestivalRepository

from conftest import FakeTransport, make_request

T0 = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 14, 15, 38, 55, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 14, 16, 57, 29, tzinfo=timezone.utc)

EVENT_A = "evt_ticketmaster_vv178Z_aGkYeTUOa"  # Olivia Rodrigo 2026-10-11
EVENT_B = "evt_ticketmaster_vv178Z_aGkMlEBGy"  # Olivia Rodrigo 2026-10-12


def _tm_record(price=None, status="onsale", retrieved_at=T1):
    return {
        "ticketmaster_event_id": "vv178Z_aGkYeTUOa",
        "platform_object_id": "vv178Z_aGkYeTUOa",
        "price_ranges": [{"currency": "USD", "type": "standard", "min": price, "max": price}] if price else [],
        "event_status": status,
        "canonical_url": "https://www.ticketmaster.com/event/vv178Z_aGkYeTUOa",
        "retrieved_at": retrieved_at.isoformat(),
        "knowledge_time": retrieved_at.isoformat(),
    }


def _tm_result(records, *, status=AcquisitionStatus.SUCCESS, completed_at=None):
    return AcquisitionResult(
        request_id="r1",
        provider="ticketmaster_official_api",
        provider_endpoint=None,
        status=status,
        started_at=utc_now(),
        completed_at=completed_at or utc_now(),
        record_count=len(records),
        cost_usd=0.0,
        provider_metadata={},
        records=tuple(records),
    )


class FakeTm:
    """Minimal Ticketmaster provider stub for collector tests."""

    def __init__(self, records=None, status=AcquisitionStatus.SUCCESS, configured=True):
        self._records = records or [_tm_record(price=120.0)]
        self._status = status
        self._configured = configured
        self.calls = 0

    def configured(self) -> bool:
        return self._configured

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.calls += 1
        return _tm_result(self._records, status=self._status, completed_at=utc_now())


class FakeSg:
    """SeatGeek stub that reports NOT_CONFIGURED (absence must not block TM)."""

    def __init__(self, configured=True):
        self._configured = configured
        self.calls = 0

    def configured(self) -> bool:
        return self._configured

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.calls += 1
        return _tm_result([], status=AcquisitionStatus.NOT_CONFIGURED)


def _seed_event(events_repo, *, event_id=EVENT_A, local_date="2026-10-11", venue_id="KovZpa2M7e"):
    events_repo.conn.execute(
        """
        INSERT INTO events.events
            (event_id, event_type, event_name, event_time, local_date, venue_id,
             venue_name, market_id, city, state, country, event_status,
             provider_support_count, first_observed_at, last_observed_at,
             knowledge_time, match_gate, supporting_observation_ids)
        VALUES (?, 'UNKNOWN', 'Olivia Rodrigo: The Unraveled Tour', ?, ?, ?,
                'United Center', 'Chicago, IL', 'Chicago', 'Illinois', 'United States',
                'onsale', 1, ?, ?, ?, 'UNMATCHED', ?)
        """,
        [
            event_id,
            f"{local_date}T00:00:00Z",
            local_date,
            venue_id,
            T0,
            T0,
            T0,
            json.dumps([f"raw_{event_id}"]),
        ],
    )
    events_repo.conn.execute(
        """
        INSERT INTO events.artist_event_relations
            (relation_id, artist_id, event_id, role, knowledge_time, supporting_observation_ids)
        VALUES (?, 'olivia-rodrigo', ?, 'headliner', ?, ?)
        """,
        [f"aer_{event_id}", event_id, T0, json.dumps([f"raw_{event_id}"])],
    )
    events_repo.conn.execute(
        """
        INSERT OR IGNORE INTO events.venues
            (venue_id, venue_name, city, state, state_code, country, country_code,
             market_id, first_observed_at, last_observed_at, supporting_observation_ids)
        VALUES (?, 'United Center', 'Chicago', 'Illinois', 'IL', 'United States', 'US',
                'Chicago, IL', ?, ?, ?)
        """,
        [venue_id, T0, T0, json.dumps([f"raw_{event_id}"])],
    )
    events_repo.conn.commit()


# ---------------------------------------------------------------------------
# Tracked registry
# ---------------------------------------------------------------------------
class TestTrackedRegistry:
    def test_track_event_is_idempotent(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "t.duckdb"))
        try:
            economics = EconomicsRepository(repo.conn)
            registry = TrackedEventRegistry(economics)
            first = registry.track_event(EVENT_A, "olivia-rodrigo", "KovZpa2M7e", T0)
            second = registry.track_event(EVENT_A, "olivia-rodrigo", "KovZpa2M7e", T0)
            assert first.canonical_event_id == second.canonical_event_id
            rows = repo.conn.execute("SELECT count(*) FROM economics.tracked_events").fetchone()[0]
            assert rows == 1
        finally:
            repo.close()

    def test_lifecycle_active_to_expired(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "l.duckdb"))
        try:
            economics = EconomicsRepository(repo.conn)
            registry = TrackedEventRegistry(economics, post_event_window_hours=48)
            past = datetime(2026, 8, 1, tzinfo=timezone.utc)
            registry.track_event("evt_past", "a", "v", past)
            now = datetime(2026, 8, 14, tzinfo=timezone.utc)
            assert registry.get_active_events(as_of=now) == []
            transitioned = registry.transition_expired_events(as_of=now)
            assert transitioned == 1
            row = repo.conn.execute(
                "SELECT tracking_status FROM economics.tracked_events WHERE canonical_event_id = 'evt_past'"
            ).fetchone()
            assert row[0] == TRACKING_EXPIRED
        finally:
            repo.close()

    def test_untrack_removes_and_reports(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "u.duckdb"))
        try:
            economics = EconomicsRepository(repo.conn)
            registry = TrackedEventRegistry(economics)
            registry.track_event("evt_x", "a", "v", T0)
            assert registry.untrack_event("evt_x") is True
            assert registry.untrack_event("evt_x") is False
            assert registry.get_event("evt_x") is None
        finally:
            repo.close()


# ---------------------------------------------------------------------------
# Collector: append-only, dedup, lock, seatgeek absence
# ---------------------------------------------------------------------------
class TestCollector:
    def test_append_only_and_same_bucket_dedup(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "c.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            _seed_event(events_repo)

            tm = FakeTm(records=[_tm_record(price=100.0)])
            first = snapshot_event(
                events_repo=events_repo,
                economics_repo=economics,
                canonical_event_id=EVENT_A,
                providers=("ticketmaster",),
                ticketmaster=tm,
                seatgeek=None,
            )
            assert first["price_snapshots"] == 1
            # same minute bucket -> deduped, no new row
            second = snapshot_event(
                events_repo=events_repo,
                economics_repo=economics,
                canonical_event_id=EVENT_A,
                providers=("ticketmaster",),
                ticketmaster=tm,
                seatgeek=None,
            )
            assert second["price_snapshots"] == 0
            rows = economics.query_primary_snapshots(event_id=EVENT_A)
            assert len(rows) == 1
            assert rows[0]["minimum_price"] == 100.0
        finally:
            repo.close()

    def test_next_bucket_appends(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "nb.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            _seed_event(events_repo)

            earlier = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
            later = datetime(2026, 8, 14, 10, 2, tzinfo=timezone.utc)
            tm = FakeTm(records=[_tm_record(price=100.0, retrieved_at=earlier)])
            snapshot_event(
                events_repo=events_repo,
                economics_repo=economics,
                canonical_event_id=EVENT_A,
                providers=("ticketmaster",),
                ticketmaster=tm,
                seatgeek=None,
            )
            tm2 = FakeTm(records=[_tm_record(price=150.0, retrieved_at=later)])
            snapshot_event(
                events_repo=events_repo,
                economics_repo=economics,
                canonical_event_id=EVENT_A,
                providers=("ticketmaster",),
                ticketmaster=tm2,
                seatgeek=None,
            )
            rows = economics.query_primary_snapshots(event_id=EVENT_A)
            assert len(rows) == 2
            assert [r["minimum_price"] for r in rows] == [100.0, 150.0]
        finally:
            repo.close()

    def test_seatgeek_absence_does_not_block_ticketmaster(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "sg.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            _seed_event(events_repo)

            tm = FakeTm(records=[_tm_record(price=80.0)])
            sg = FakeSg(configured=False)
            summary = snapshot_event(
                events_repo=events_repo,
                economics_repo=economics,
                canonical_event_id=EVENT_A,
                providers=("ticketmaster", "seatgeek"),
                ticketmaster=tm,
                seatgeek=sg,
            )
            assert summary["price_snapshots"] >= 1
            assert "ticketmaster_not_configured" not in summary["errors"]
        finally:
            repo.close()

    def test_ticketmaster_failure_is_explicit_error(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "f.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            _seed_event(events_repo)

            tm = FakeTm(records=[], status=AcquisitionStatus.PROVIDER_ERROR)
            summary = snapshot_event(
                events_repo=events_repo,
                economics_repo=economics,
                canonical_event_id=EVENT_A,
                providers=("ticketmaster",),
                ticketmaster=tm,
                seatgeek=None,
            )
            assert summary["price_snapshots"] == 0
            assert any("ticketmaster_" in e for e in summary["errors"])
            rows = economics.query_primary_snapshots(event_id=EVENT_A)
            assert rows == []
        finally:
            repo.close()


# ---------------------------------------------------------------------------
# Collector lock concurrency
# ---------------------------------------------------------------------------
class TestCollectorLock:
    def test_lock_excludes_second_holder(self, tmp_path):
        lock_path = tmp_path / "econ.lock"
        with CollectorLock(lock_path):
            with pytest.raises(LockHeldError):
                with CollectorLock(lock_path):
                    pass

    def test_lock_released_after_exit(self, tmp_path):
        lock_path = tmp_path / "econ2.lock"
        with CollectorLock(lock_path):
            pass
        with CollectorLock(lock_path):
            pass  # second acquisition succeeds after release


# ---------------------------------------------------------------------------
# Run logger: secret-safe, DB persistence, failure exit codes
# ---------------------------------------------------------------------------
class TestRunLogger:
    def test_errors_redact_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TICKETMASTER_API_KEY", "supersecret123")
        logger = RunLogger(log_dir=tmp_path)
        logger.log_error("ticketmaster failed with supersecret123")
        logger.finish(EXIT_ERROR)
        assert all("supersecret123" not in e for e in logger.errors)
        assert logger.errors and logger.errors[0]

    def test_auth_header_redacted(self, tmp_path):
        logger = RunLogger(log_dir=tmp_path)
        logger.log_error("Authorization: Bearer abcdef12345")
        logger.finish(EXIT_ERROR)
        assert "abcdef12345" not in logger.errors[0]

    def test_log_file_has_no_secret_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEATGEEK_CLIENT_SECRET", "sekrit")
        logger = RunLogger(log_dir=tmp_path)
        logger.log_error("something sekrit happened")
        logger.set_quota_metadata({"token": "tok123", "units": 5})
        logger.finish(EXIT_SUCCESS)
        content = (tmp_path / "economics_collector.log").read_text()
        assert "sekrit" not in content
        assert "tok123" not in content

    def test_failed_run_persists_to_db(self, tmp_path):
        repo = FestivalRepository(str(tmp_path / "r.duckdb"))
        try:
            economics = EconomicsRepository(repo.conn)
            logger = RunLogger(log_dir=tmp_path / "logs")
            logger.log_provider_status("ticketmaster", "NOT_CONFIGURED")
            logger.log_error("no credentials")
            logger.finish(EXIT_NO_ACTIVE_EVENTS)
            persist_run_to_db(economics, logger)
            rows = economics.conn.execute("SELECT count(*) FROM economics.collector_runs").fetchone()
            assert rows[0] == 1
            row = economics.conn.execute(
                "SELECT exit_code, events_attempted FROM economics.collector_runs"
            ).fetchone()
            assert row[0] == EXIT_NO_ACTIVE_EVENTS
            assert row[1] == 0
        finally:
            repo.close()


# ---------------------------------------------------------------------------
# Venue master: United Center merge idempotency + alias dedup
# ---------------------------------------------------------------------------
class TestVenueMaster:
    def _seed_uc_venues(self, events_repo):
        events_repo.conn.execute(
            """
            INSERT INTO events.venues
                (venue_id, venue_name, city, state, state_code, country, country_code,
                 market_id, ticketmaster_venue_id, setlistfm_venue_id,
                 first_observed_at, last_observed_at, supporting_observation_ids)
            VALUES ('KovZpa2M7e', 'United Center', 'Chicago', 'Illinois', 'IL', 'United States', 'US',
                    'Chicago, IL', 'KovZpa2M7e', NULL, ?, ?, ?)
            """,
            [T0, T0, json.dumps(["tm"])],
        )
        events_repo.conn.execute(
            """
            INSERT INTO events.venues
                (venue_id, venue_name, city, state, state_code, country, country_code,
                 market_id, ticketmaster_venue_id, setlistfm_venue_id,
                 first_observed_at, last_observed_at, supporting_observation_ids)
            VALUES ('63d0f2d3', 'United Center', 'Chicago', 'Illinois', 'IL', 'United States', 'US',
                    'Chicago, IL', 'KovZpa2M7e', '63d0f2d3', ?, ?, ?)
            """,
            [T0, T0, json.dumps(["sl"])],
        )
        events_repo.conn.commit()

    def test_merge_is_idempotent_no_duplicate_actions(self, tmp_path):
        from festival_bloomberg.economics.venues import merge_united_center

        repo = FestivalRepository(str(tmp_path / "m.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            self._seed_uc_venues(events_repo)

            first = merge_united_center(events_repo, economics)
            assert first["status"] == "merged"
            second = merge_united_center(events_repo, economics)
            assert second["status"] == "already_merged"
            third = merge_united_center(events_repo, economics)
            assert third["status"] == "already_merged"

            actions = economics.conn.execute(
                "SELECT count(*) FROM economics.venue_merge_actions"
            ).fetchone()[0]
            assert actions == 1
            aliases = economics.conn.execute(
                "SELECT count(*) FROM economics.venue_aliases"
            ).fetchone()[0]
            assert aliases == 1

            # one active canonical, one superseded alias row
            active = events_repo.conn.execute(
                "SELECT count(*) FROM events.venues WHERE superseded_by IS NULL"
            ).fetchone()[0]
            assert active == 1
            canonical = events_repo.conn.execute(
                "SELECT venue_id FROM events.venues WHERE superseded_by IS NULL"
            ).fetchone()[0]
            assert canonical == "63d0f2d3"
            superseded = events_repo.conn.execute(
                "SELECT count(*) FROM events.venues WHERE superseded_by IS NOT NULL"
            ).fetchone()[0]
            assert superseded == 1
        finally:
            repo.close()

    def test_events_resolve_to_active_canonical(self, tmp_path):
        from festival_bloomberg.economics.venues import merge_united_center
        from festival_bloomberg.oa.forward_history import _venue_parity_accounting

        repo = FestivalRepository(str(tmp_path / "p.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            self._seed_uc_venues(events_repo)
            # both Olivia events point at the superseded TM venue id
            _seed_event(events_repo, event_id=EVENT_A, venue_id="KovZpa2M7e")
            _seed_event(events_repo, event_id=EVENT_B, venue_id="KovZpa2M7e")
            merge_united_center(events_repo, economics)

            accounting = _venue_parity_accounting(events_repo, T0)
            assert accounting["status"] == "PASS"
            assert accounting["unexplained_loss"] == 0
            assert accounting["event_venue_references"] == 2
            assert accounting["event_venue_references_resolved"] == 2
        finally:
            repo.close()

    def test_nearby_same_name_not_merged_without_ids(self, tmp_path):
        from festival_bloomberg.economics.venues import VenueResolver

        repo = FestivalRepository(str(tmp_path / "n.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            events_repo.conn.execute(
                """
                INSERT INTO events.venues
                    (venue_id, venue_name, city, state, state_code, country, country_code,
                     market_id, first_observed_at, last_observed_at, supporting_observation_ids)
                VALUES ('v1', 'Wrigley Field', 'Chicago', 'Illinois', 'IL', 'US', 'US',
                        'Chicago, IL', ?, ?, ?)
                """,
                [T0, T0, json.dumps(["a"])],
            )
            events_repo.conn.commit()
            resolver = VenueResolver(events_repo, economics)
            resolved = resolver.resolve_venue_identity(
                venue_name="Wrigley Field",
                city="Chicago",
                state="IL",
                country="US",
                latitude=41.948,
                longitude=-87.655,
            )
            assert resolved["resolution_method"] != "UNRESOLVED"
            # a different venue near Wrigley's coordinates must NOT resolve to it
            # (name is not in the canonical mapping, no external id, no row)
            other = resolver.resolve_venue_identity(
                venue_name="Mystery Arena",
                city="Chicago",
                state="IL",
                country="US",
                latitude=41.947,
                longitude=-87.656,
            )
            assert other["canonical_venue_id"] is None
            assert other["resolution_method"] == "UNRESOLVED"
        finally:
            repo.close()


# ---------------------------------------------------------------------------
# Venue parity accounting
# ---------------------------------------------------------------------------
class TestVenueParity:
    def test_48_event_history_count_is_sum_of_per_artist_unique(self, tmp_path):
        """The historical 48 was the sum of per-artist unique venue counts."""
        from festival_bloomberg.oa.forward_history import _venue_parity_accounting

        repo = FestivalRepository(str(tmp_path / "vp.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            _seed_event(events_repo, event_id=EVENT_A, venue_id="v_uc")
            _seed_event(events_repo, event_id=EVENT_B, venue_id="v_uc")
            # a second artist at a different venue
            events_repo.conn.execute(
                """
                INSERT INTO events.events
                    (event_id, event_type, event_name, event_time, local_date, venue_id,
                     venue_name, market_id, city, state, country, event_status,
                     provider_support_count, first_observed_at, last_observed_at,
                     knowledge_time, match_gate, supporting_observation_ids)
                VALUES ('evt_other', 'UNKNOWN', 'Other Show', '2026-10-13T00:00:00Z', '2026-10-13',
                        'v_sf', 'Soldier Field', 'Chicago, IL', 'Chicago', 'Illinois', 'US',
                        'onsale', 1, ?, ?, ?, 'UNMATCHED', ?)
                """,
                [T0, T0, T0, json.dumps(["raw_other"])],
            )
            events_repo.conn.execute(
                """
                INSERT INTO events.artist_event_relations
                    (relation_id, artist_id, event_id, role, knowledge_time, supporting_observation_ids)
                VALUES ('aer_other', 'other-artist', 'evt_other', 'headliner', ?, ?)
                """,
                [T0, json.dumps(["raw_other"])],
            )
            events_repo.conn.execute(
                """
                INSERT INTO events.venues
                    (venue_id, venue_name, city, state, state_code, country, country_code,
                     market_id, first_observed_at, last_observed_at, supporting_observation_ids)
                VALUES ('v_sf', 'Soldier Field', 'Chicago', 'Illinois', 'IL', 'US', 'US',
                        'Chicago, IL', ?, ?, ?)
                """,
                [T0, T0, json.dumps(["raw_other"])],
            )
            events_repo.conn.commit()

            accounting = _venue_parity_accounting(events_repo, T0)
            # 3 event references, all resolved; no unexplained loss
            assert accounting["unexplained_loss"] == 0
            assert accounting["status"] == "PASS"
            # 2 distinct active canonical names (United Center, Soldier Field)
            assert accounting["unique_active_names"] == 2
        finally:
            repo.close()


# ---------------------------------------------------------------------------
# Two-snapshot PIT visibility
# ---------------------------------------------------------------------------
class TestTwoSnapshotPit:
    def test_pit_ab_visibility_from_real_rows(self, tmp_path):
        from festival_bloomberg.oa.forward_history import _two_snapshot_pit

        repo = FestivalRepository(str(tmp_path / "pit.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            registry = TrackedEventRegistry(economics)
            _seed_event(events_repo, event_id=EVENT_A)
            registry.track_event(EVENT_A, "olivia-rodrigo", "KovZpa2M7e", T0)

            # two genuine snapshots at distinct retrieval times
            a = primary_snapshots_from_ticketmaster(
                _tm_record(price=100.0, retrieved_at=T1),
                canonical_event_id=EVENT_A,
                raw_observation_id="raw_a",
            )[0]
            b = primary_snapshots_from_ticketmaster(
                _tm_record(price=120.0, retrieved_at=T2),
                canonical_event_id=EVENT_A,
                raw_observation_id="raw_b",
            )[0]
            assert economics.insert_primary_snapshot(a)
            assert economics.insert_primary_snapshot(b)

            evidence = _two_snapshot_pit(economics, registry, T0)
            assert evidence["status"] == "PASS"
            assert evidence["snapshot_a"]["id"] == a.snapshot_id
            assert evidence["snapshot_b"]["id"] == b.snapshot_id
            assert evidence["snapshot_a"]["retrieved_at"] < evidence["snapshot_b"]["retrieved_at"]
            # cutoff between A and B shows A only; after B shows both
            assert a.snapshot_id in evidence["visible_at_mid"]
            assert b.snapshot_id not in evidence["visible_at_mid"]
            assert a.snapshot_id in evidence["visible_after"]
            assert b.snapshot_id in evidence["visible_after"]
        finally:
            repo.close()

    def test_pit_fails_with_single_snapshot(self, tmp_path):
        from festival_bloomberg.oa.forward_history import _two_snapshot_pit

        repo = FestivalRepository(str(tmp_path / "pit1.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            registry = TrackedEventRegistry(economics)
            _seed_event(events_repo, event_id=EVENT_A)
            registry.track_event(EVENT_A, "olivia-rodrigo", "KovZpa2M7e", T0)

            a = primary_snapshots_from_ticketmaster(
                _tm_record(price=100.0, retrieved_at=T1),
                canonical_event_id=EVENT_A,
                raw_observation_id="raw_a",
            )[0]
            economics.insert_primary_snapshot(a)
            evidence = _two_snapshot_pit(economics, registry, T0)
            assert evidence["status"] == "FAIL"
        finally:
            repo.close()


# ---------------------------------------------------------------------------
# Capacity enrichment: provider status comparison + Wikipedia HTTP
# ---------------------------------------------------------------------------
class TestCapacityEnrichment:
    def test_enricher_uses_acquisition_status_enum(self, tmp_path):
        """Regression: enrichment compared result.status.value == 'success'
        (never true for AcquisitionStatus.SUCCESS), so no source ever matched.
        Now the enum is compared directly and claims are stored."""
        from festival_bloomberg.economics.enrichment import CapacityEnricher

        repo = FestivalRepository(str(tmp_path / "ce.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            economics = EconomicsRepository(repo.conn)
            events_repo.conn.execute(
                """
                INSERT INTO events.venues
                    (venue_id, venue_name, city, state, state_code, country, country_code,
                     market_id, first_observed_at, last_observed_at, supporting_observation_ids)
                VALUES ('uc', 'United Center', 'Chicago', 'Illinois', 'IL', 'United States', 'US',
                        'Chicago, IL', ?, ?, ?)
                """,
                [T0, T0, json.dumps(["x"])],
            )
            events_repo.conn.commit()

            # Script: wikipedia search (page without capacity), wikidata
            # search (hit Q639975), wikidata P1083 claims (23500)
            wiki_miss = {"query": {"search": [{"title": "United Center"}]}}
            wiki_page = {"query": {"pages": {"1": {"revisions": [{"slots": {"main": {"content": "No infobox capacity here"}}}]}}}}
            wd_search = {"search": [{"id": "Q639975", "label": "United Center", "description": "arena"}]}
            wd_claims = {
                "entities": {
                    "Q639975": {
                        "labels": {"en": {"value": "United Center"}},
                        "claims": {
                            "P1083": [
                                {
                                    "id": "Q639975$1",
                                    "rank": "normal",
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "property": "P1083",
                                        "datavalue": {
                                            "value": {"amount": "+23500", "unit": "1"},
                                            "type": "quantity",
                                        },
                                    },
                                    "qualifiers": {},
                                }
                            ]
                        },
                    }
                }
            }
            enricher = CapacityEnricher(events_repo, economics)
            # overwrite providers with scripted fakes
            from festival_bloomberg.acquisition.providers.wikipedia import WikipediaProvider
            from festival_bloomberg.acquisition.providers.wikidata import WikidataProvider
            from festival_bloomberg.acquisition.providers.openstreetmap import OpenStreetMapProvider

            enricher.wikipedia_provider = WikipediaProvider(
                transport=FakeTransport([(200, wiki_miss), (200, wiki_page)])
            )
            enricher.wikidata_provider = WikidataProvider(
                transport=FakeTransport([(200, wd_search), (200, wd_claims)])
            )
            enricher.osm_provider = OpenStreetMapProvider(
                transport=FakeTransport([(500, {"error": "skip"})])
            )

            result = enricher.enrich_venue("uc", city="Chicago", state="IL")
            assert result["claims_added"] >= 1
            assert "wikidata" in result["sources_used"]
            claims = economics.query_capacity_claims(venue_id="uc")
            assert len(claims) >= 1
            assert claims[0]["capacity_value"] == 23500.0
            assert claims[0]["provider"] == "wikidata_official_api"
        finally:
            repo.close()

    def test_wikipedia_provider_extracts_infobox_via_transport(self):
        """WikipediaProvider now performs HTTP through the canonical transport
        instead of returning 'HTTP client not implemented'."""
        from festival_bloomberg.acquisition.providers.wikipedia import WikipediaProvider

        search = {"query": {"search": [{"title": "United Center"}]}}
        page = {
            "query": {
                "pages": {
                    "1": {
                        "pageprops": {"wikibase_item": "Q639975"},
                        "revisions": [{"slots": {"main": {"content": "{{Infobox venue\n| capacity = 23,500\n| seating_capacity = 20,917\n}}"}}}],
                    }
                }
            }
        }
        provider = WikipediaProvider(transport=FakeTransport([(200, search), (200, page)]))
        request = AcquisitionRequest.new(
            entity_id="v", entity_type="venue", platform="wikipedia",
            query="United Center", market_id="Chicago, IL", max_cost_usd=0.0,
        )
        result = provider.acquire(request)
        assert result.status == AcquisitionStatus.SUCCESS
        kinds = {(r["capacity_kind"], r["source_field"]) for r in result.records}
        assert ("MAX_PERSONS", "| capacity") in kinds or ("MAX_PERSONS", "| capacity ") in kinds
        assert result.records[0]["wikidata_qid"] == "Q639975"


# ---------------------------------------------------------------------------
# Snapshot delta semantics
# ---------------------------------------------------------------------------
class TestSnapshotDeltas:
    def test_unknown_delta_stays_unknown(self):
        earlier = {"snapshot_id": "a", "minimum_price": None, "maximum_price": None}
        later = {"snapshot_id": "b", "minimum_price": None, "maximum_price": None}
        changes = snapshot_change_semantics(earlier, later)
        assert changes["minimum_price_change"]["status"] == "NO_OBSERVED_PRICE_CHANGE_INFORMATION"
        assert changes["maximum_price_change"]["status"] == "NO_OBSERVED_PRICE_CHANGE_INFORMATION"

    def test_price_becomes_observable(self):
        result = describe_price_change("minimum_price", None, 45.0)
        assert result["status"] == "PRICE_BECAME_OBSERVABLE"
        result2 = describe_price_change("minimum_price", 45.0, None)
        assert result2["status"] == "PRICE_BECAME_UNOBSERVABLE"

    def test_snapshot_bucket_is_minute_granularity(self):
        t1 = datetime(2026, 8, 14, 10, 0, 5, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 14, 10, 0, 55, tzinfo=timezone.utc)
        t3 = datetime(2026, 8, 14, 10, 1, 5, tzinfo=timezone.utc)
        assert snapshot_bucket(t1) == snapshot_bucket(t2)
        assert snapshot_bucket(t1) != snapshot_bucket(t3)


# ---------------------------------------------------------------------------
# LaunchAgent wrapper failure paths
# ---------------------------------------------------------------------------
class TestLaunchAgentWrapper:
    def _write_wrapper(self, tmp_path, repo_root, python_path):
        wrapper = tmp_path / "economics_snapshot_wrapper.sh"
        wrapper.write_text(
            f"""#!/bin/bash
set -eu
REPO_ROOT="{repo_root}"
cd "$REPO_ROOT"
export PYTHONPATH="{python_path}"
export FESTIVAL_BLOOMBERG_WAREHOUSE_PATH="{tmp_path}/warehouse.duckdb"
export FESTIVAL_BLOOMBERG_ECON_CADENCE=6h
python3.12 -m festival_bloomberg.cli economics snapshot-tracked --db "$FESTIVAL_BLOOMBERG_WAREHOUSE_PATH"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def _run_wrapper(self, wrapper: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/bash", str(wrapper)],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_wrapper_no_tracked_events_exits_no_active(self, tmp_path):
        root = Path(__file__).resolve().parents[2]
        py = root / "python"
        wrapper = self._write_wrapper(tmp_path, str(root), str(py))
        result = self._run_wrapper(wrapper)
        assert result.returncode == EXIT_NO_ACTIVE_EVENTS

    def test_wrapper_missing_python_exits_nonzero(self, tmp_path):
        root = Path(__file__).resolve().parents[2]
        wrapper = tmp_path / "bad.sh"
        wrapper.write_text(
            "#!/bin/bash\nset -eu\npython3.12 -m definitely_not_a_module\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        result = subprocess.run(["/bin/bash", str(wrapper)], capture_output=True, text=True, timeout=30)
        assert result.returncode != 0

    def test_plist_template_contains_no_credentials(self):
        root = Path(__file__).resolve().parents[2]
        template = root / "scripts" / "com.festival-bloomberg.economics-snapshot.plist.template"
        content = template.read_text(encoding="utf-8")
        for secret_marker in ["API_KEY", "SECRET", "TOKEN", "PASSWORD", "Bearer"]:
            assert secret_marker not in content
        assert "StartInterval" in content
        assert "<integer>21600</integer>" in content


# ---------------------------------------------------------------------------
# OA seeding: both Olivia events tracked
# ---------------------------------------------------------------------------
class TestOaSeeding:
    def test_olivia_events_are_seeded_by_artist_id(self, tmp_path):
        """Regression: OA previously filtered on artist_name (never returned by
        query_events), so only ONE event was tracked. Filtering must use the
        artist_id slug returned by the JOIN."""
        from festival_bloomberg.oa.forward_history import _olivia_upcoming_events

        repo = FestivalRepository(str(tmp_path / "oa.duckdb"))
        try:
            events_repo = EventRepository(repo.conn)
            _seed_event(events_repo, event_id=EVENT_A, local_date="2026-10-11")
            _seed_event(events_repo, event_id=EVENT_B, local_date="2026-10-12")
            upcoming = _olivia_upcoming_events(events_repo, market="Chicago, IL", as_of=T0)
            ids = sorted(e["event_id"] for e in upcoming)
            assert ids == sorted([EVENT_A, EVENT_B])
        finally:
            repo.close()
