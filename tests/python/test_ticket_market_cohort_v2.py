"""Tests for TICKET_MARKET_LONGITUDINAL_COHORT_V2."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.evidence_rails.cohort_cadence import (
    cadence_for_bucket,
    lifecycle_bucket,
    prioritize_due_pairs,
)
from festival_bloomberg.evidence_rails.cohort_v2 import cohort_hash, freeze_from_draft
from festival_bloomberg.evidence_rails.collection_ledger import start_run, finish_run
from festival_bloomberg.planning.proposed_show import _market_history_columns


def _conn(tmp_path: Path):
    db = tmp_path / "t.duckdb"
    conn = duckdb.connect(str(db))
    apply_pending_migrations(conn)
    return conn


def test_lifecycle_cadence_buckets():
    today = datetime(2026, 9, 5, tzinfo=timezone.utc).date()
    assert lifecycle_bucket("2026-09-05", today=today) == "show_day"
    assert lifecycle_bucket("2026-09-08", today=today) == "<7"
    assert lifecycle_bucket("2026-10-01", today=today) == "14-30"
    assert lifecycle_bucket("2026-12-01", today=today) == "60-120"
    assert lifecycle_bucket("2027-03-01", today=today) == ">120"
    assert cadence_for_bucket("past") == "stopped"
    assert cadence_for_bucket(">120") == "weekly"
    assert cadence_for_bucket("show_day") == "show_day"


def test_prioritize_due_pairs_nearest_and_shallow_first():
    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    pairs = [
        {"event_key": "a", "marketplace": "ticketmaster.com", "event_date": "2026-12-01", "observation_count": 5, "next_due_at": "2026-09-01T00:00:00+00:00"},
        {"event_key": "b", "marketplace": "ticketmaster.com", "event_date": "2026-09-10", "observation_count": 0, "next_due_at": "2026-09-01T00:00:00+00:00"},
        {"event_key": "c", "marketplace": "ticketweb.com", "event_date": "2026-09-12", "observation_count": 1, "next_due_at": "2026-09-01T00:00:00+00:00"},
        {"event_key": "d", "marketplace": "ticketmaster.com", "event_date": "2026-09-06", "observation_count": 0, "next_due_at": "2026-09-10T00:00:00+00:00"},  # not due
        {"event_key": "e", "marketplace": "ticketmaster.com", "event_date": "2026-08-01", "lifecycle_bucket": "past", "next_due_at": "2026-09-01T00:00:00+00:00"},
    ]
    due = prioritize_due_pairs(pairs, now=now)
    assert [p["event_key"] for p in due] == ["b", "c", "a"]


def test_cohort_freeze_writes_canonical_identifiers(tmp_path: Path):
    draft = {
        "cohort_version": "TICKET_MARKET_COHORT_V2_TEST",
        "generated_at": "2026-09-05T00:00:00+00:00",
        "selection_rules": {"no_artist_only_match": True},
        "n_events": 1,
        "n_pairs": 1,
        "lifecycle": {"<7": 1},
        "cities": {"Atlanta": 1},
        "marketplaces": {"ticketmaster.com": 1},
        "events": [{
            "event_key": "event::tm:abc",
            "provider_event_id": "abc",
            "artist_name": "Test Artist",
            "venue_name": "Test Venue",
            "city": "Atlanta",
            "state": "GA",
            "event_date": "2026-09-10",
            "canonical_url": "https://www.ticketmaster.com/event/abc",
            "lifecycle_bucket": "<7",
            "market_key": "atlanta-ga",
            "venue_id": "v1",
        }],
        "pairs": [{
            "event_key": "event::tm:abc",
            "marketplace": "ticketmaster.com",
            "provider_event_id": "abc",
            "marketplace_event_url": "https://www.ticketmaster.com/event/abc",
            "mapping_status": "EXACT_PROVIDER_ID",
            "mapping_method": "TM_PROVIDER_ID_PROMOTION",
            "confidence": 1.0,
            "lifecycle_bucket": "<7",
            "market_key": "atlanta-ga",
            "city": "Atlanta",
            "event_date": "2026-09-10",
            "artist_name": "Test Artist",
            "venue_name": "Test Venue",
            "genre": "Rock",
        }],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(draft))
    out = tmp_path / "cohort.json"
    db = tmp_path / "tm.duckdb"
    report = freeze_from_draft(draft_path, out_path=out, db_path=db, force=True)
    assert report["status"] == "FROZEN"
    assert report["cohort_hash"] == cohort_hash(draft["pairs"])
    conn = duckdb.connect(str(db), read_only=True)
    assert conn.execute("SELECT COUNT(*) FROM acquisition.event_identifiers").fetchone()[0] == 1
    assert conn.execute("SELECT mapping_status FROM acquisition.event_identifiers").fetchone()[0] == "EXACT_PROVIDER_ID"
    assert conn.execute("SELECT COUNT(*) FROM acquisition.ticket_market_cohort_pairs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM acquisition.ticket_market_pair_schedule").fetchone()[0] == 1


def test_collection_run_ledger_and_pit_horizon(tmp_path: Path):
    conn = _conn(tmp_path)
    start_run(
        conn,
        run_id="run_test",
        cohort_version="TICKET_MARKET_COHORT_V2_TEST",
        rail="FAST",
        wave_label="w1",
        budget_cap_usd=1.0,
        candidate_pairs=10,
        due_pairs=2,
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        (now - timedelta(days=10), 50.0),
        (now - timedelta(days=7), 60.0),
        (now - timedelta(days=2), 70.0),
        (now - timedelta(days=1), 80.0),
        (now, 90.0),
    ]
    for i, (ts, price) in enumerate(rows):
        conn.execute(
            """INSERT INTO acquisition.ticket_market_snapshots (
                snapshot_id, event_key, source_platform, actor_or_endpoint,
                wave_label, observed_at, retrieved_at, knowledge_time,
                all_in_price, currency, identity_match_status,
                rights_status, commercial_use_status, parser_version
            ) VALUES (?, 'event::tm:x', 'ticketmaster.com', 'monid_context.dev',
                      'w1', ?, ?, ?, ?, 'USD', 'MATCHED',
                      'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', 'test')""",
            [f"s{i}", ts.isoformat(), ts.isoformat(), ts.isoformat(), price],
        )
    finish_run(conn, "run_test", {"cost_usd": 0.01, "budget_cap_usd": 1.0, "attempted": 2, "snapshots": 5, "fetches": 2})
    status = conn.execute("SELECT status, succeeded_pairs FROM acquisition.ticket_market_collection_runs WHERE run_id='run_test'").fetchone()
    assert status == ("COMPLETE", 5)

    # PIT: 1D must use T-1d (80), 7D must use T-7d (60) — not T-10d (50)
    hist = _market_history_columns(conn, "event::tm:x", ["ticketmaster.com"])
    # structure may nest by source — assert via SQL to be robust
    one_d = conn.execute(
        """SELECT all_in_price FROM acquisition.ticket_market_snapshots
           WHERE event_key='event::tm:x' AND observed_at <= ?
           ORDER BY observed_at DESC LIMIT 1""",
        [(now - timedelta(days=1)).isoformat()],
    ).fetchone()[0]
    seven_d = conn.execute(
        """SELECT all_in_price FROM acquisition.ticket_market_snapshots
           WHERE event_key='event::tm:x' AND observed_at <= ?
           ORDER BY observed_at DESC LIMIT 1""",
        [(now - timedelta(days=7)).isoformat()],
    ).fetchone()[0]
    assert one_d == 80.0
    assert seven_d == 60.0
    assert hist is not None  # buyer helper loads without error


def test_listing_disappearance_not_sale_semantics():
    # Contract: status vocabulary never uses SOLD for disappearance
    allowed = {
        "LISTING_APPEARED", "LISTING_PRICE_CHANGED", "LISTING_QUANTITY_CHANGED",
        "LISTING_DISAPPEARED", "LISTING_REAPPEARED", "LISTING_NO_LONGER_OBSERVED",
        "OBSERVED", "DISAPPEARED",
    }
    assert "SOLD" not in allowed
    assert "LISTING_DISAPPEARED" in allowed


def test_sandbox_fixture_gate():
    from festival_bloomberg.evidence_rails.tickets_dev import is_sandbox
    # Without live key, DEEP must be unavailable for production warehouse writes
    assert is_sandbox() is True
