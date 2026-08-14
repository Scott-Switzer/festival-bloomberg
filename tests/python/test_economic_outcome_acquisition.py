"""Offline regressions for Economic Outcome Acquisition V1.

Covers strict outcome semantics (planned != actual, paid != scanned !=
reported, tickets != attendance, gross != net), document lineage, Common
Crawl WARC retrieval contract, conflict reconciliation, the research-vs-
commercial split, and the model-readiness gate. All offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from festival_bloomberg.acquisition.providers.commoncrawl import (
    extract_warc_payload_text,
    fetch_warc_record_bytes,
    lookup_capture_offset,
)
from festival_bloomberg.economics.document_ingestion import (
    DocumentEvidence,
    extract_infobox_fields,
    extract_outcome_candidates,
    strip_html,
    strip_wikitext,
)
from festival_bloomberg.economics.laboratory import (
    economic_coverage_report,
    research_commercial_split,
)
from festival_bloomberg.economics.outcome_acquisition import (
    CURATED_SOURCES,
    EconomicOutcomeAcquirer,
    PublicOutcomeSource,
    assign_conflict_groups,
)
from festival_bloomberg.economics.outcome_claims import (
    OBSERVED_PRIVATE,
    OBSERVED_PUBLIC,
    OutcomeClaim,
    RIGHTS_OPEN_WITH_ATTRIBUTION,
    RIGHTS_RESEARCH_ONLY,
    RIGHTS_UNKNOWN,
)
from festival_bloomberg.economics.readiness import (
    ATTENDANCE_MODEL_READY,
    BASELINE_RESEARCH_READY,
    NOT_READY,
    evaluate_model_readiness,
)
from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.warehouse.repository import FestivalRepository

from conftest import FakeTransport

T0 = datetime(2019, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _repo(tmp_path, name="eoa.duckdb"):
    repo = FestivalRepository(str(tmp_path / name))
    return repo, EconomicsRepository(repo.conn), EventRepository(repo.conn)


def _seed_event(events, event_id, venue="Grant Park", date="2024-08-02"):
    events.conn.execute(
        """
        INSERT INTO events.events
            (event_id, event_type, event_name, event_time, local_date, venue_id,
             venue_name, market_id, city, state, country, event_status,
             provider_support_count, first_observed_at, last_observed_at,
             knowledge_time, match_gate, supporting_observation_ids)
        VALUES (?, 'FESTIVAL', ?, ?, ?, 'v_grant', ?, 'Chicago, IL',
                'Chicago', 'Illinois', 'US', 'completed', 1, ?, ?, ?,
                'UNMATCHED', ?)
        """,
        [event_id, event_id, f"{date}T00:00:00Z", date, venue, T0, T0, T0, json.dumps([f"raw_{event_id}"])],
    )
    events.conn.execute(
        """
        INSERT INTO events.artist_event_relations
            (relation_id, artist_id, event_id, role, knowledge_time, supporting_observation_ids)
        VALUES (?, 'various', ?, 'festival', ?, ?)
        """,
        [f"aer_{event_id}", event_id, T0, json.dumps([f"raw_{event_id}"])],
    )
    events.conn.commit()


# ---------------------------------------------------------------------------
# Strict semantics
# ---------------------------------------------------------------------------
def test_planned_is_not_actual_attendance():
    cands = extract_outcome_candidates("The festival had an expected attendance of 40,000.")
    assert all(c.outcome_type == "EXPECTED_ATTENDANCE" for c in cands)
    # EXPECTED_ATTENDANCE is not a valid ledger type (rejected at claim build)
    with pytest.raises(ValueError):
        OutcomeClaim.build(canonical_event_id="e", outcome_type="EXPECTED_ATTENDANCE", value_numeric=40000)


def test_paid_scanned_reported_are_distinct():
    paid = extract_outcome_candidates("40,500 paid attendance.")[0]
    scanned = extract_outcome_candidates("39,100 scanned.")[0]
    reported = extract_outcome_candidates("over 40,000 attendees.")[0]
    assert paid.outcome_type == "PAID_ATTENDANCE"
    assert scanned.outcome_type == "SCANNED_ATTENDANCE"
    assert reported.outcome_type == "REPORTED_ATTENDANCE"
    assert len({paid.outcome_type, scanned.outcome_type, reported.outcome_type}) == 3


def test_ticket_count_is_not_attendance():
    tickets = extract_outcome_candidates("41,000 tickets sold.")[0]
    assert tickets.outcome_type == "TICKETS_SOLD"
    assert tickets.outcome_type not in ("PAID_ATTENDANCE", "REPORTED_ATTENDANCE", "SCANNED_ATTENDANCE")


def test_gross_is_not_inferred_from_price_times_tickets():
    # "grossed $12 million" is an observed gross phrase, kept as TICKET_GROSS,
    # never silently reconstructed from tickets*price.
    gross = extract_outcome_candidates("The tour grossed $12 million.")[0]
    assert gross.outcome_type == "TICKET_GROSS"
    assert gross.value_numeric == 12_000_000


def test_price_range_keeps_min_and_max():
    cand = extract_outcome_candidates("prices ranged $49.50 to $199.50.")[0]
    assert cand.outcome_type == "PRIMARY_FACE_VALUE_MIN_MAX"
    assert cand.extra["min"] == 49.5
    assert cand.extra["max"] == 199.5


def test_capacity_phrase_is_not_attendance():
    cand = extract_outcome_candidates("The venue has a capacity of 40,000 people.")[0]
    assert cand.outcome_type == "VENUE_CAPACITY"
    assert cand.review_required is True


def test_offsale_is_not_sold_out():
    sold_out = extract_outcome_candidates("Tickets to the festival sold out.")[0]
    assert sold_out.outcome_type == "EXPLICIT_SOLD_OUT_ASSERTION"
    # "offsale" never produces a sold-out assertion
    assert extract_outcome_candidates("The event went offsale.") == []


# ---------------------------------------------------------------------------
# Document lineage
# ---------------------------------------------------------------------------
def test_document_evidence_lineage_and_hash():
    doc = DocumentEvidence.build(
        text="40,000 attended",
        source_name="test.pdf",
        provider="test",
        source_url="https://example.com/test.pdf",
        document_title="Test Permit",
        publication_time="2024-01-01T00:00:00Z",
        document_id="doc_1",
    )
    assert doc.content_hash
    assert doc.document_id == "doc_1"
    assert doc.source_url == "https://example.com/test.pdf"
    # same text -> same hash (duplicate web document detection)
    same = DocumentEvidence.build(text="40,000 attended", source_name="test2.pdf", provider="test")
    assert doc.content_hash == same.content_hash


def test_html_and_wikitext_lineage_strip():
    html = "<html><body><p>40,000 <b>attended</b></p><script>bad()</script></body></html>"
    assert "attended" in strip_html(html)
    assert "bad()" not in strip_html(html)
    wt = "{{Infobox festival\n| attendance = 400,000\n}}\n40,000 people attended.<ref>{{cite web}}</ref>"
    assert "cite" not in strip_wikitext(wt)
    fields = extract_infobox_fields(wt)
    assert "attendance" in fields


def test_infobox_number_parsing_strips_refs():
    from festival_bloomberg.economics.outcome_acquisition import _parse_infobox_number
    assert _parse_infobox_number("400,000<ref>{{cite web}}</ref>") == 400000.0
    assert _parse_infobox_number("115,000") == 115000.0
    assert _parse_infobox_number("no number") is None


# ---------------------------------------------------------------------------
# Common Crawl WARC contract
# ---------------------------------------------------------------------------
def test_commoncrawl_capture_offset_lookup():
    line = '{"timestamp":"20210614120000","statuscode":"200","digest":"ABC","length":"100","offset":"2000","filename":"crawl-data/CC-MAIN-2021-25/seg_1/warc/CC-MAIN-abc.warc.gz"}'
    transport = FakeTransport([(200, (line + "\n").encode())])
    capture = lookup_capture_offset(transport, "https://example.com/event", "CC-MAIN-2021-25")
    assert capture is not None
    assert capture["offset"] == 2000
    assert capture["length"] == 100
    assert capture["timestamp"] == "20210614120000"


def test_commoncrawl_warc_fetch_uses_range():
    transport = FakeTransport([(206, b"gzip-bytes")])
    body = fetch_warc_record_bytes(transport, "CC-MAIN-abc.warc.gz", 2000, 100)
    assert body == b"gzip-bytes"
    request = transport.requests[0]
    assert request["headers"]["Range"] == "bytes=2000-2099"


def test_warc_payload_text_extraction():
    import gzip
    raw = b"WARC/1.0\r\nWARC-Type: response\r\n\r\nHTTP/1.1 200 OK\r\n\r\n<html>40,000 attended</html>"
    payload = gzip.compress(raw)
    text = extract_warc_payload_text(payload)
    assert "40,000 attended" in text
    assert "WARC/1.0" not in text


# ---------------------------------------------------------------------------
# Acquirer + conflict + rights split
# ---------------------------------------------------------------------------
def test_acquirer_skips_multi_edition_body_attendance(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "festival_lollapalooza_chicago")
        src = PublicOutcomeSource(
            event_id="festival_lollapalooza_chicago",
            event_label="Lollapalooza (Chicago)",
            event_time=None,
            venue_name="Grant Park",
            market="Chicago, IL",
            wikipedia_title="Lollapalooza",
        )
        wt = (
            "{{Infobox festival\n| attendance = 400,000\n| capacity = 115,000\n}}\n"
            "attracting over 65,000 attendees in 1996.\n"
            "The 2022 Stockholm event was attended by over 70,000.\n"
            "Tickets sold out quickly.\n"
        )
        acq = EconomicOutcomeAcquirer(transport=FakeTransport([]))
        # bypass network by monkeypatching the wikitext fetch
        import festival_bloomberg.economics.outcome_acquisition as oa
        oa.fetch_wikipedia_wikitext = lambda title, transport: wt
        claims = acq.acquire_source(src)
        types = {c.outcome_type: c.value_numeric for c in claims}
        # infobox attendance + capacity survive; body multi-edition attendance is
        # skipped (not attributed to Chicago); sold-out assertion survives.
        assert types.get("REPORTED_ATTENDANCE") == 400000.0
        assert types.get("EVENT_USABLE_CAPACITY") == 115000.0
        assert "EXPLICIT_SOLD_OUT_ASSERTION" in types
        assert 65000.0 not in types.values()
        assert 70000.0 not in types.values()
    finally:
        repo.close()


def test_conflict_groups_preserve_claims(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1")
        for cid, val in (("a", 40000.0), ("b", 42000.0)):
            econ.insert_outcome_claim(OutcomeClaim.build(
                claim_id=cid, canonical_event_id="e1", outcome_type="REPORTED_ATTENDANCE",
                value_numeric=val, source_provider="test", source_quality="C_OTHER_PUBLIC_REPORT",
                rights_status=RIGHTS_UNKNOWN, commercial_use_status=RIGHTS_UNKNOWN,
                retrieved_at=T1.isoformat(), knowledge_time=T1.isoformat(),
            ))
        groups = assign_conflict_groups(econ)
        assert groups == 1
        rows = econ.query_outcome_claims(event_id="e1")
        assert all(r["conflict_group_id"] for r in rows)
        assert len(rows) == 2  # both preserved, never deleted
    finally:
        repo.close()


def test_research_commercial_split(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1")
        _seed_event(events, "e2")
        econ.insert_outcome_claim(OutcomeClaim.build(
            claim_id="c1", canonical_event_id="e1", outcome_type="REPORTED_ATTENDANCE",
            value_numeric=100, source_provider="setlistfm", source_quality="C_OTHER_PUBLIC_REPORT",
            rights_status=RIGHTS_RESEARCH_ONLY, commercial_use_status=RIGHTS_RESEARCH_ONLY,
            retrieved_at=T1.isoformat(), knowledge_time=T1.isoformat(),
        ))
        econ.insert_outcome_claim(OutcomeClaim.build(
            claim_id="c2", canonical_event_id="e2", outcome_type="REPORTED_ATTENDANCE",
            value_numeric=200, source_provider="wikipedia", source_quality="C_OTHER_PUBLIC_REPORT",
            rights_status=RIGHTS_OPEN_WITH_ATTRIBUTION, commercial_use_status=RIGHTS_OPEN_WITH_ATTRIBUTION,
            retrieved_at=T1.isoformat(), knowledge_time=T1.isoformat(),
        ))
        split = research_commercial_split(econ)
        assert split["research_only_claims"] == 1
        assert split["commercial_eligible_claims"] == 1
    finally:
        repo.close()


def test_coverage_report_v2_counts_unknown(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1")
        report = economic_coverage_report(econ, events)
        assert report["events_searched"] == 1
        assert report["events_with_attendance"] == 0
        assert report["scorecard"]["PAID_ATTENDANCE"]["events_known"] == 0
        assert report["scorecard"]["PAID_ATTENDANCE"]["coverage_pct"] == 0.0
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Model-readiness gate
# ---------------------------------------------------------------------------
def test_readiness_not_ready_without_labels(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        _seed_event(events, "e1")
        verdict = evaluate_model_readiness(econ, events)
        assert verdict["verdict"] == NOT_READY
        assert verdict["attendance_events"] == 0
    finally:
        repo.close()


def test_readiness_advances_with_attendance(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        for i in range(12):
            _seed_event(events, f"e{i}", date=f"2024-08-{i % 28 + 1:02d}")
            econ.insert_outcome_claim(OutcomeClaim.build(
                claim_id=f"c{i}", canonical_event_id=f"e{i}", outcome_type="REPORTED_ATTENDANCE",
                value_numeric=1000 + i, source_provider="wikipedia", source_quality="C_OTHER_PUBLIC_REPORT",
                rights_status=RIGHTS_OPEN_WITH_ATTRIBUTION, commercial_use_status=RIGHTS_OPEN_WITH_ATTRIBUTION,
                retrieved_at=T1.isoformat(), knowledge_time=T1.isoformat(),
            ))
        verdict = evaluate_model_readiness(econ, events)
        # 12 attendance labels -> baseline research ready, not a model
        assert verdict["verdict"] == BASELINE_RESEARCH_READY
    finally:
        repo.close()


def test_curated_sources_are_chicago_music_festivals():
    assert CURATED_SOURCES
    for src in CURATED_SOURCES:
        assert src.event_id.startswith("festival_")
        assert "Chicago" in src.market or "Bridgeview" in src.market


def test_seed_festival_events_is_idempotent(tmp_path):
    from festival_bloomberg.oa.economic_outcome import seed_festival_events
    repo, econ, events = _repo(tmp_path, name="seed.duckdb")
    try:
        first = seed_festival_events(events)
        second = seed_festival_events(events)
        assert first == len(CURATED_SOURCES)
        assert second == 0  # no duplicate festival events
        # festival events resolve in the event graph
        event_ids = {e["event_id"] for e in events.query_events()}
        assert "festival_lollapalooza_chicago" in event_ids
    finally:
        repo.close()


def test_customer_import_full_promoter_fixture_is_structural_only(tmp_path):
    import csv
    from festival_bloomberg.economics.private_import import import_outcomes_csv

    repo, econ, events = _repo(tmp_path, name="promoter.duckdb")
    try:
        path = tmp_path / "promoter.csv"
        columns = [
            "external_event_id", "artist", "venue", "market", "event_date",
            "guarantee", "ticket_capacity", "paid_tickets", "comp_tickets",
            "refunds", "scanned_attendance", "ticket_gross", "ticket_net",
            "marketing_spend", "venue_cost", "production_cost", "labor_cost",
            "promoter_contribution",
        ]
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerow({
                "external_event_id": "P1", "artist": "Artist A", "venue": "United Center",
                "market": "Chicago", "event_date": "2024-03-19",
                "guarantee": "500000", "ticket_capacity": "20000", "paid_tickets": "18500",
                "comp_tickets": "800", "refunds": "120", "scanned_attendance": "18400",
                "ticket_gross": "2100000", "ticket_net": "1850000",
                "marketing_spend": "180000", "venue_cost": "300000",
                "production_cost": "400000", "labor_cost": "120000",
                "promoter_contribution": "60000",
            })
        report = import_outcomes_csv(csv_path=path, economics_repo=econ)
        assert report.error_count == 0
        # structural fixture only: rows are OBSERVED_PRIVATE, never real training data
        rows = econ.query_outcome_claims(observation_class=OBSERVED_PRIVATE)
        assert len(rows) == 13  # 13 numeric outcome columns populated
        assert all(r["observation_class"] == OBSERVED_PRIVATE for r in rows)
        assert all(r["rights_status"] == RIGHTS_UNKNOWN for r in rows)
        types = {r["outcome_type"] for r in rows}
        assert "ARTIST_GUARANTEE" in types
        assert "PROMOTER_CONTRIBUTION" in types
        assert "PAID_TICKETS" in types
        assert "SCANNED_ATTENDANCE" in types
    finally:
        repo.close()
