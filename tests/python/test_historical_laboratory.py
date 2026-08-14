"""Offline regressions for the Historical Laboratory (outcome claims).

Covers the outcome-claim taxonomy, semantic guards, PIT cutoff filtering,
Common Crawl capture-time semantics, private/public classification, CSV
import, censoring, source grading, and the coverage/selection-bias reports.
All tests are offline and free.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest

from festival_bloomberg.acquisition.contracts import AcquisitionStatus
from festival_bloomberg.acquisition.providers.commoncrawl import (
    CommonCrawlProvider,
    _cdx_iso,
)
from festival_bloomberg.acquisition.providers.eventbrite import (
    EventbriteProvider,
    order_state_to_outcome,
)
from festival_bloomberg.economics import laboratory
from festival_bloomberg.economics.capacity import CapacityClaim
from festival_bloomberg.economics.outcome_claims import (
    ATTENDANCE_TYPES,
    CAPACITY_TYPES,
    EXPLICIT_SOLD_OUT_ASSERTION,
    GRADE_A_PRIMARY_GOVERNMENT,
    GRADE_UNKNOWN,
    OBSERVED_PRIVATE,
    OBSERVED_PUBLIC,
    OutcomeClaim,
    OutcomeClaimSemanticError,
    PAID_ATTENDANCE,
    PERMIT_CAPACITY_LIMIT,
    RIGHTS_UNKNOWN,
    SCANNED_ATTENDANCE,
    TICKETS_SOLD,
    right_censored_sold_out,
    validate_outcome_type,
    validate_source_quality,
)
from festival_bloomberg.economics.private_import import import_outcomes_csv
from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.warehouse.repository import FestivalRepository

from conftest import FakeTransport, make_request

T0 = datetime(2019, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc)
CUTOFF_2020 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _repo(tmp_path, name="hl.duckdb"):
    repo = FestivalRepository(str(tmp_path / name))
    return repo, EconomicsRepository(repo.conn), EventRepository(repo.conn)


def _claim(**overrides) -> OutcomeClaim:
    kwargs = dict(
        canonical_event_id="evt1",
        outcome_type=PAID_ATTENDANCE,
        value_numeric=18000,
        source_provider="test_source",
        source_quality=GRADE_UNKNOWN,
        retrieved_at=T1.isoformat(),
        knowledge_time=T1.isoformat(),
        rights_status=RIGHTS_UNKNOWN,
        commercial_use_status=RIGHTS_UNKNOWN,
    )
    kwargs.update(overrides)
    return OutcomeClaim.build(**kwargs)


# ---------------------------------------------------------------------------
# Taxonomy + semantic guards
# ---------------------------------------------------------------------------
def test_outcome_type_validation_rejects_arbitrary_strings():
    with pytest.raises(ValueError):
        validate_outcome_type("CROWD_SIZE")
    assert validate_outcome_type(PAID_ATTENDANCE) == PAID_ATTENDANCE
    assert validate_outcome_type(PERMIT_CAPACITY_LIMIT) == PERMIT_CAPACITY_LIMIT


def test_source_quality_grading_rejects_invalid_grade():
    with pytest.raises(ValueError):
        validate_source_quality("A_PLUS_PLUS")
    assert validate_source_quality(GRADE_A_PRIMARY_GOVERNMENT) == GRADE_A_PRIMARY_GOVERNMENT


def test_unknown_values_are_null_not_zero():
    claim = _claim(value_numeric=None, value_text=None)
    assert claim.value_numeric is None
    assert claim.value_text is None
    assert claim.outcome_type == PAID_ATTENDANCE


def test_capacity_is_not_attendance():
    # capacity claim carrying an attendance definition is corruption
    with pytest.raises(OutcomeClaimSemanticError):
        _claim(
            outcome_type=PERMIT_CAPACITY_LIMIT,
            attendance_definition="turnstile count",
        )
    # attendance claim carrying a capacity definition is also corruption
    with pytest.raises(OutcomeClaimSemanticError):
        _claim(outcome_type=PAID_ATTENDANCE, capacity_definition="max persons")


def test_permit_capacity_is_not_paid_attendance():
    permit = _claim(
        outcome_type=PERMIT_CAPACITY_LIMIT,
        value_numeric=75000,
        capacity_definition="maximum permitted attendees",
    )
    assert permit.outcome_type == PERMIT_CAPACITY_LIMIT
    assert permit.outcome_type in CAPACITY_TYPES
    assert permit.outcome_type not in ATTENDANCE_TYPES
    assert PERMIT_CAPACITY_LIMIT != PAID_ATTENDANCE


def test_offsale_is_not_sold_out():
    # Explicit sold-out assertions must be their own type; an offsale event has
    # no implicit sold-out claim.
    sold_out = _claim(outcome_type=EXPLICIT_SOLD_OUT_ASSERTION, value_numeric=1)
    assert sold_out.outcome_type == EXPLICIT_SOLD_OUT_ASSERTION
    assert "OFFSALE" not in {
        EXPLICIT_SOLD_OUT_ASSERTION,
        "EXPLICIT_NOT_SOLD_OUT_ASSERTION",
    }


def test_expected_attendance_is_not_a_valid_claim_type():
    with pytest.raises(ValueError):
        validate_outcome_type("EXPECTED_ATTENDANCE")


# ---------------------------------------------------------------------------
# Ledger append-only + conflict + supersession + PIT
# ---------------------------------------------------------------------------
def test_conflicting_claims_coexist(tmp_path):
    repo, econ, _ = _repo(tmp_path)
    try:
        a = _claim(claim_id="a", value_numeric=18000, conflict_group_id="g1")
        b = _claim(claim_id="b", value_numeric=21000, conflict_group_id="g1")
        assert econ.insert_outcome_claim(a)
        assert econ.insert_outcome_claim(b)
        rows = econ.query_outcome_claims(event_id="evt1")
        assert {r["value_numeric"] for r in rows} == {18000.0, 21000.0}
    finally:
        repo.close()


def test_claim_supersession_keeps_history(tmp_path):
    repo, econ, _ = _repo(tmp_path)
    try:
        old = _claim(claim_id="old", value_numeric=18000)
        new = _claim(claim_id="new", value_numeric=19000)
        econ.insert_outcome_claim(old)
        econ.insert_outcome_claim(new)
        assert econ.supersede_outcome_claim(
            old_claim_id="old", new_claim_id="new", knowledge_time=T2.isoformat()
        )
        rows = econ.query_outcome_claims(event_id="evt1")
        assert len(rows) == 2  # nothing deleted
        by_id = {r["claim_id"]: r for r in rows}
        assert by_id["old"]["supersedes_claim_id"] == "new"
        assert by_id["old"]["valid_to"] is not None
    finally:
        repo.close()


def test_pit_cutoff_filters_claims(tmp_path):
    repo, econ, _ = _repo(tmp_path)
    try:
        early = _claim(claim_id="early", knowledge_time=T0.isoformat(), retrieved_at=T0.isoformat())
        late = _claim(claim_id="late", knowledge_time=T1.isoformat(), retrieved_at=T1.isoformat())
        econ.insert_outcome_claim(early)
        econ.insert_outcome_claim(late)
        assert len(econ.query_outcome_claims(event_id="evt1", cutoff=CUTOFF_2020)) == 1
        assert econ.query_outcome_claims(event_id="evt1", cutoff=CUTOFF_2020)[0]["claim_id"] == "early"
    finally:
        repo.close()


def test_future_publication_is_not_knowable_before_publication(tmp_path):
    # A fact retrieved in 2026 from a page published in 2021 must not appear
    # as knowable in a 2020 feature set (knowledge_time is 2026 retrieval).
    repo, econ, _ = _repo(tmp_path)
    try:
        claim = _claim(
            claim_id="retro",
            source_publication_time="2021-06-01T00:00:00Z",
            retrieved_at=T1.isoformat(),
            knowledge_time=T1.isoformat(),
        )
        econ.insert_outcome_claim(claim)
        assert econ.query_outcome_claims(event_id="evt1", cutoff=CUTOFF_2020) == []
    finally:
        repo.close()


def test_private_and_public_observations_are_distinct(tmp_path):
    repo, econ, _ = _repo(tmp_path)
    try:
        pub = _claim(claim_id="pub", observation_class=OBSERVED_PUBLIC)
        priv = _claim(claim_id="priv", observation_class=OBSERVED_PRIVATE)
        econ.insert_outcome_claim(pub)
        econ.insert_outcome_claim(priv)
        assert len(econ.query_outcome_claims(observation_class=OBSERVED_PUBLIC)) == 1
        assert len(econ.query_outcome_claims(observation_class=OBSERVED_PRIVATE)) == 1
        # public view must never include private observations
        assert all(
            r["observation_class"] == OBSERVED_PUBLIC
            for r in econ.query_outcome_claims(observation_class=OBSERVED_PUBLIC)
        )
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Censoring + setlist-vs-attendance
# ---------------------------------------------------------------------------
def test_right_censoring_of_sold_out_capacity():
    labels = right_censored_sold_out(capacity_value=23500)
    assert labels["is_censored"] is True
    assert labels["censoring_type"] == "RIGHT"
    assert labels["censoring_threshold"] == "23500"


def test_setlist_presence_is_not_attendance():
    # The coarse outcome layer already guarantees this; re-assert the claim
    # layer keeps setlist/performance signals out of attendance types.
    assert "SETLIST" not in ATTENDANCE_TYPES
    assert SCANNED_ATTENDANCE in ATTENDANCE_TYPES
    assert TICKETS_SOLD not in ATTENDANCE_TYPES


# ---------------------------------------------------------------------------
# Common Crawl
# ---------------------------------------------------------------------------
def test_cdx_timestamp_iso():
    assert _cdx_iso("20210614120000") == "2021-06-14T12:00:00Z"
    assert _cdx_iso("20210614") == "2021-06-14T00:00:00Z"
    assert _cdx_iso("not-a-date") is None


def test_commoncrawl_capture_time_semantics():
    line = '{"timestamp":"20210614120000","statuscode":"200","urlkey":"org,example)/","digest":"ABC","length":"123","mime":"text/html"}'
    provider = CommonCrawlProvider(
        transport=FakeTransport([(200, (line + "\n").encode())])
    )
    result = provider.acquire(
        make_request(platform="commoncrawl", query="https://example.com/event", operation="CC_INDEX_LOOKUP")
    )
    assert result.status == AcquisitionStatus.SUCCESS
    rec = result.records[0]
    # capture timestamp is the archive source_as_of ...
    assert rec["source_as_of"] == "2021-06-14T12:00:00Z"
    # ... while knowledge_time stays at retrieval (now), never backdated
    assert rec["knowledge_time"] != "2021-06-14T12:00:00Z"
    assert rec["knowledge_time_source"] == "retrieval"


def test_commoncrawl_rights_fail_closed():
    line = '{"timestamp":"20210614120000","statuscode":"200","urlkey":"x","digest":"D","length":"1","mime":"text/html"}'
    provider = CommonCrawlProvider(transport=FakeTransport([(200, (line + "\n").encode())]))
    result = provider.acquire(
        make_request(platform="commoncrawl", query="https://example.com/event")
    )
    rec = result.records[0]
    # Common Crawl availability is NOT a commercial-rights grant.
    assert rec["rights_status"] == "UNKNOWN"
    assert rec["commercial_use_status"] == "UNKNOWN"


def test_commoncrawl_no_captures_is_no_results():
    provider = CommonCrawlProvider(transport=FakeTransport([(404, b"")]))
    result = provider.acquire(make_request(platform="commoncrawl", query="https://example.com/event"))
    assert result.status == AcquisitionStatus.NO_RESULTS
    assert result.record_count == 0


def test_commoncrawl_requires_url():
    provider = CommonCrawlProvider(transport=FakeTransport([]))
    result = provider.acquire(make_request(platform="commoncrawl", query="not a url"))
    assert result.status == AcquisitionStatus.SCHEMA_INVALID


# ---------------------------------------------------------------------------
# Eventbrite foundation
# ---------------------------------------------------------------------------
def test_eventbrite_absent_key_is_not_configured():
    provider = EventbriteProvider(transport=FakeTransport([]), env={})
    result = provider.acquire(make_request(platform="eventbrite", operation="EVB_EVENTS"))
    assert result.status == AcquisitionStatus.NOT_CONFIGURED


def test_eventbrite_order_states_are_distinct_outcomes():
    assert order_state_to_outcome("placed") == "TICKETS_SOLD"
    assert order_state_to_outcome("completed") == "PAID_TICKETS"
    assert order_state_to_outcome("checked_in") == "SCANNED_ATTENDANCE"
    assert order_state_to_outcome("attending") is None
    # none of these collapse into plain "attendance"
    assert order_state_to_outcome("placed") != SCANNED_ATTENDANCE


# ---------------------------------------------------------------------------
# Private CSV import
# ---------------------------------------------------------------------------
def _write_csv(tmp_path, rows, columns=None):
    path = tmp_path / "outcomes.csv"
    if columns is None:
        columns = [
            "external_event_id", "artist", "venue", "market", "event_date",
            "paid_tickets", "scanned_attendance", "ticket_gross",
        ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def test_private_csv_import_and_dedup(tmp_path):
    repo, econ, _ = _repo(tmp_path)
    try:
        path = _write_csv(
            tmp_path,
            [
                {
                    "external_event_id": "X1",
                    "artist": "Olivia Rodrigo",
                    "venue": "United Center",
                    "market": "Chicago",
                    "event_date": "2024-03-19",
                    "paid_tickets": "14000",
                    "scanned_attendance": "13800",
                    "ticket_gross": "1600000",
                }
            ],
        )
        report = import_outcomes_csv(csv_path=path, economics_repo=econ)
        assert report.error_count == 0
        assert report.claims_built == 3
        assert report.claims_inserted == 3
        rows = econ.query_outcome_claims(observation_class=OBSERVED_PRIVATE)
        assert len(rows) == 3
        assert all(r["observation_class"] == OBSERVED_PRIVATE for r in rows)
        # the private event is a private key, never merged into public graph
        assert all(r["canonical_event_id"].startswith("private_") for r in rows)
        # re-import must dedup (append-only, no duplicates)
        again = import_outcomes_csv(csv_path=path, economics_repo=econ)
        assert again.claims_inserted == 0
        assert again.duplicates_skipped == 3
        assert len(econ.query_outcome_claims(observation_class=OBSERVED_PRIVATE)) == 3
    finally:
        repo.close()


def test_private_csv_missing_required_columns(tmp_path):
    repo, econ, _ = _repo(tmp_path)
    try:
        path = _write_csv(
            tmp_path,
            [{"external_event_id": "X1", "artist": "a"}],
            columns=["external_event_id", "artist"],
        )
        report = import_outcomes_csv(csv_path=path, economics_repo=econ)
        assert report.error_count == 1
        assert report.claims_inserted == 0
        assert "missing required columns" in report.errors[0]["reason"]
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Coverage + selection-bias reports
# ---------------------------------------------------------------------------
def _seed_event(events, event_id, venue="United Center", date="2024-03-19"):
    events.conn.execute(
        """
        INSERT INTO events.events
            (event_id, event_type, event_name, event_time, local_date, venue_id,
             venue_name, market_id, city, state, country, event_status,
             provider_support_count, first_observed_at, last_observed_at,
             knowledge_time, match_gate, supporting_observation_ids)
        VALUES (?, 'CONCERT', 'Olivia Rodrigo', ?, ?, 'v_uc', ?, 'Chicago, IL',
                'Chicago', 'Illinois', 'United States', 'completed', 1, ?, ?, ?,
                'UNMATCHED', ?)
        """,
        [
            event_id,
            f"{date}T00:00:00Z",
            date,
            venue,
            T0,
            T0,
            T0,
            json.dumps([f"raw_{event_id}"]),
        ],
    )
    events.conn.execute(
        """
        INSERT INTO events.artist_event_relations
            (relation_id, artist_id, event_id, role, knowledge_time, supporting_observation_ids)
        VALUES (?, 'olivia-rodrigo', ?, 'headliner', ?, ?)
        """,
        [f"aer_{event_id}", event_id, T0, json.dumps([f"raw_{event_id}"])],
    )
    events.conn.commit()


def test_outcome_coverage_report_counts_known_vs_unknown(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1")
        _seed_event(events, "e2")
        econ.insert_outcome_claim(_claim(claim_id="c1", canonical_event_id="e1", outcome_type=PAID_ATTENDANCE))
        report = laboratory.outcome_coverage_report(econ, events)
        assert PAID_ATTENDANCE in report
        assert report[PAID_ATTENDANCE]["events_with_known"] == 1
        assert report[PAID_ATTENDANCE]["events_unknown"] == 1
    finally:
        repo.close()


def test_selection_bias_report_measures_concentration(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1", venue="United Center", date="2024-03-19")
        _seed_event(events, "e2", venue="United Center", date="2024-03-20")
        _seed_event(events, "e3", venue="Wrigley Field", date="2024-06-01")
        econ.insert_outcome_claim(_claim(claim_id="c1", canonical_event_id="e1"))
        report = laboratory.selection_bias_report(econ, events)
        assert report["events_total"] == 3
        assert report["events_with_claims"] == 1
        assert "United Center" in report["top_venues_by_events"]
        assert report["selection_bias_notes"]
    finally:
        repo.close()


def test_data_quality_report_distributions(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1")
        econ.insert_outcome_claim(_claim(claim_id="c1", source_quality=GRADE_A_PRIMARY_GOVERNMENT))
        report = laboratory.data_quality_report(econ, events)
        assert report["claims_total"] == 1
        assert report["source_quality_distribution"][GRADE_A_PRIMARY_GOVERNMENT] == 1
        assert report["duplicate_rate"] == 0.0
    finally:
        repo.close()


def test_historical_laboratory_oa_is_idempotent(tmp_path):
    from festival_bloomberg.oa.historical_laboratory import HistoricalLaboratoryOA

    repo, econ, events = _repo(tmp_path, name="oa.duckdb")
    try:
        _seed_event(events, "e1", venue="United Center", date="2024-03-19")
        # venue capacity claim for the canonical venue used by _seed_event
        econ.insert_capacity_claim(
            CapacityClaim(
                claim_id="cap_uc",
                canonical_venue_id="v_uc",
                capacity_value=23500,
                capacity_kind="MAX_PERSONS",
                configuration_description=None,
                effective_from=None,
                effective_to=None,
                provider="wikidata_official_api",
                source="wikidata_p1083",
                source_url="https://www.wikidata.org/wiki/Q185578",
                source_publication_time=None,
                retrieved_at=T1.isoformat(),
                knowledge_time=T1.isoformat(),
                source_observation_id="Q185578",
                claim_status="OBSERVED",
                usage_label="MAXIMUM_CAPACITY_UPPER_BOUND",
            )
        )
        oa = HistoricalLaboratoryOA(str(tmp_path / "oa.duckdb"))
        first = oa.run(enrich_limit=None)
        second = oa.run(enrich_limit=None)
        assert first["events_discovered"] == 1
        assert first["events_with_claims"] == 1
        assert first["claims_total"] == second["claims_total"]  # idempotent
        assert first["claims_by_type"]["EVENT_PERFORMED"] == 1
        assert first["claims_by_type"]["VENUE_CAPACITY"] == 1
        # attendance/tickets/gross are honest UNKNOWN (never fabricated)
        for fabricated in ("PAID_ATTENDANCE", "TICKETS_SOLD", "TICKET_GROSS", "ARTIST_GUARANTEE"):
            assert fabricated not in first["claims_by_type"]
        assert first["provider_cost_usd"] == 0.0
        assert first["monid_usage"] == "NONE" and first["apify_usage"] == "NONE"
    finally:
        repo.close()


def test_pit_availability_report_per_cutoff(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1")
        econ.upsert_decision_cutoffs(
            {
                "event_id": "e1",
                "canonical_event_id": "e1",
                "booking_cutoff": "2026-01-01T00:00:00Z",
                "event_cutoff": "2026-10-11T00:00:00Z",
            }
        )
        econ.insert_outcome_claim(
            _claim(claim_id="c1", canonical_event_id="e1", knowledge_time=T0.isoformat(), retrieved_at=T0.isoformat())
        )
        report = laboratory.pit_availability_report(econ)
        entry = report["cutoffs"][0]
        assert entry["event_id"] == "e1"
        assert entry["booking_cutoff_knowable"] == 1
    finally:
        repo.close()
