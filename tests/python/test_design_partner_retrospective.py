"""Offline regressions for Design Partner Retrospective V1.

Covers flexible column mapping, PII minimization, CSV/TSV/XLSX ingestion,
customer isolation, idempotency, accounting reconciliation, the outcome vault
leakage boundary, PIT cutoffs, study freeze immutability, training-row
eligibility, baseline readiness, and audit-report generation. All offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from festival_bloomberg.economics import design_partner as dp
from festival_bloomberg.economics.audit_report import build_audit_report, render_html, write_audit_report
from festival_bloomberg.economics.partner_import import (
    RECON_DOES_NOT_TIE,
    RECON_TIES,
    PartnerIngestionReport,
    accounting_reconciliation,
    data_quality_audit,
    ingest_partner_files,
    read_tabular,
)
from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.economics.retrospective import (
    CUTOFF_ANNOUNCEMENT,
    CUTOFF_EVENT,
    DEFAULT_ALLOWED_PRIVATE_INPUTS,
    DEFAULT_HIDDEN_OUTCOMES,
    EXCL_CUTOFF_MISSING,
    EXCL_TARGET_MISSING,
    PIT_COMPLETE,
    PIT_INSUFFICIENT,
    READINESS_DESCRIPTIVE,
    READINESS_NOT_READY,
    STUDY_FROZEN,
    RetrospectiveStudy,
    baseline_readiness,
    build_blind_export,
    hidden_claim_ids,
    pit_reconstructability,
    retrospective_inputs,
    training_row_eligibility,
    vault_outcomes,
)
from festival_bloomberg.economics.outcome_claims import (
    OBSERVED_PRIVATE,
    OBSERVED_PUBLIC,
    PAID_TICKETS,
    SCANNED_ATTENDANCE,
    TICKETS_SOLD,
)
from festival_bloomberg.events.repository import EventRepository
from festival_bloomberg.warehouse.repository import FestivalRepository


def _repo(tmp_path, name="dp.duckdb"):
    repo = FestivalRepository(str(tmp_path / name))
    return repo, EconomicsRepository(repo.conn), EventRepository(repo.conn)


def _write_csv(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


GOOD_CSV = (
    "customer_event_id,artist_name,venue_name,market,city,event_date,booking_date,"
    "announcement_date,onsale_date,venue_capacity,tickets_sold,paid_tickets,"
    "scanned_attendance,ticket_gross,ticket_net,artist_guarantee,marketing_spend,"
    "venue_cost,promoter_contribution,sold_out,currency\n"
    "EVT001,The National,Riviera Theatre,Chicago,Chicago,2024-05-10,2023-11-01,"
    "2024-01-15,2024-01-20,2500,2400,2350,2380,150000,145000,75000,8000,12000,24000,true,USD\n"
)


# ---------------------------------------------------------------------------
# Column mapping + PII
# ---------------------------------------------------------------------------
def test_mapping_exact_and_alias():
    exact = dp.map_column("tickets_sold")
    assert exact.status == dp.AUTO_ACCEPTED
    assert exact.canonical_field == "tickets_sold"

    alias = dp.map_column("show_date")
    assert alias.status == dp.AUTO_ACCEPTED
    assert alias.canonical_field == "event_date"

    ambiguous = dp.map_column("Final Sold")
    assert ambiguous.status == dp.REVIEW_REQUIRED
    assert ambiguous.canonical_field is None
    assert set(ambiguous.candidates) == {"tickets_sold", "paid_tickets"}

    unknown = dp.map_column("totally_unknown_column")
    assert unknown.status == dp.UNMAPPED


def test_mapping_never_silently_maps_semantics():
    # attendance must not map to tickets_sold
    attendance = dp.map_column("attendance")
    assert attendance.status == dp.REVIEW_REQUIRED
    assert "tickets_sold" not in attendance.candidates

    # gross maps to ticket_gross, never promoter_contribution
    gross = dp.map_column("gross")
    assert gross.canonical_field == "ticket_gross"
    assert gross.canonical_field != "promoter_contribution"

    # cap is ambiguous (capacity), never paid attendance
    cap = dp.map_column("cap")
    assert cap.status == dp.REVIEW_REQUIRED
    assert "paid_attendance" not in cap.candidates


def test_pii_detection():
    assert dp.classify_pii("buyer_email") == dp.PROHIBITED
    assert dp.classify_pii("credit_card") == dp.PROHIBITED
    assert dp.classify_pii("artist_name") == dp.SAFE
    assert dp.classify_pii("venue_name") == dp.SAFE
    assert dp.classify_pii("first_name") == dp.POTENTIAL_PII


# ---------------------------------------------------------------------------
# Ingestion: CSV / TSV / XLSX, idempotency, privacy
# ---------------------------------------------------------------------------
def test_csv_import_writes_private_claims(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        report = ingest_partner_files(
            economics_repo=econ,
            file_paths=[path],
            customer_id="demo",
            dataset_id="ds_demo",
            events_repo=events,
        )
        assert report.files_read == 1
        assert report.rows_read == 1
        assert report.claims_inserted > 0
        assert report.events_resolved == ["private_demo_evt001"]

        claims = econ.query_outcome_claims()
        assert claims, "expected claims to be written"
        assert all(c["observation_class"] == OBSERVED_PRIVATE for c in claims)
        assert all(c["observation_class"] != OBSERVED_PUBLIC for c in claims)
        types = {c["outcome_type"] for c in claims}
        assert TICKETS_SOLD in types
        assert PAID_TICKETS in types
        assert SCANNED_ATTENDANCE in types
    finally:
        repo.close()


def test_import_idempotent(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        first = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        second = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        assert first.claims_inserted > 0
        assert second.claims_inserted == 0
        assert second.duplicates_skipped == first.claims_inserted
    finally:
        repo.close()


def test_tsv_and_xlsx_import(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        tsv = tmp_path / "shows.tsv"
        tsv.write_text(
            "customer_event_id\tartist_name\tvenue_name\tevent_date\ttickets_sold\n"
            "EVT100\tSturgill Simpson\tThalia Hall\t2024-04-04\t800\n",
            encoding="utf-8",
        )
        report_tsv = ingest_partner_files(
            economics_repo=econ, file_paths=[str(tsv)], customer_id="demo", dataset_id="ds_tsv",
            events_repo=events,
        )
        assert report_tsv.rows_read == 1
        assert report_tsv.claims_inserted == 1

        xlsx = tmp_path / "shows.xlsx"
        _write_xlsx(xlsx, [
            ["customer_event_id", "artist_name", "venue_name", "event_date", "tickets_sold"],
            ["EVT101", "Khruangbin", "The Salt Shed", "2024-07-07", "3400"],
        ])
        report_xlsx = ingest_partner_files(
            economics_repo=econ, file_paths=[str(xlsx)], customer_id="demo", dataset_id="ds_xlsx",
            events_repo=events,
        )
        assert report_xlsx.rows_read == 1
        assert report_xlsx.claims_inserted == 1
    finally:
        repo.close()


def _write_xlsx(path, rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_pii_quarantine_and_no_value_ingested(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        csv = (
            "customer_event_id,artist_name,venue_name,event_date,tickets_sold,buyer_email\n"
            "EVT200,Jason Isbell,Vic Theatre,2024-06-06,1400,fan@example.com\n"
        )
        path = _write_csv(tmp_path, "pii.csv", csv)
        report = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_pii",
            events_repo=events,
        )
        assert report.pii_quarantined >= 1
        quarantined = econ.conn.execute(
            "SELECT column_name FROM economics.pii_quarantine"
        ).fetchall()
        assert any("buyer_email" in row[0] for row in quarantined)
        # The email value must never appear in any analytical claim.
        for claim in econ.query_outcome_claims():
            text = json.dumps(claim, default=str)
            assert "fan@example.com" not in text
    finally:
        repo.close()


def test_duplicate_event_dedup(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        dup = GOOD_CSV + (
            "EVT001,The National,Riviera Theatre,Chicago,Chicago,2024-05-10,2023-11-01,"
            "2024-01-15,2024-01-20,2500,2400,2350,2380,150000,145000,75000,8000,12000,24000,true,USD\n"
        )
        path = _write_csv(tmp_path, "dup.csv", dup)
        report = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_dup",
            events_repo=events,
        )
        assert report.rows_read == 2
        assert len(set(report.events_resolved)) == 1  # same event, deduped
        assert report.duplicates_skipped > 0
    finally:
        repo.close()


def test_sharing_policy_default_is_private_only(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        row = econ.conn.execute(
            "SELECT sharing_policy FROM economics.customer_datasets WHERE dataset_id = 'ds_demo'"
        ).fetchone()
        assert row[0] == dp.SHARING_PRIVATE_ONLY
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Quality + accounting
# ---------------------------------------------------------------------------
def test_quality_audit_flags(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        csv = (
            "customer_event_id,artist_name,venue_name,event_date,venue_capacity,tickets_sold,"
            "refunded_tickets,ticket_net,ticket_gross,currency\n"
            "EVT300,A,B,2024-01-01,100,150,0,50,60,USD\n"       # tickets > capacity
            "EVT301,C,D,2024-01-02,100,100,120,50,60,USD\n"     # refunds > sold
            "EVT302,E,F,2024-01-03,100,80,0,-5,60,USD\n"        # negative net
            "EVT303,G,H,2024-01-04,100,80,0,50,60,EUR\n"        # mixed currency
        )
        path = _write_csv(tmp_path, "quality.csv", csv)
        report = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_q",
            events_repo=events,
        )
        checks = {q["check"] for q in report.quality_issues}
        assert "tickets_exceed_capacity" in checks
        assert "refunds_exceed_sold" in checks
        assert "negative_net" in checks
        assert "mixed_currency" in checks
    finally:
        repo.close()


def test_accounting_reconciliation_ties_and_no_tie(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        csv = (
            "customer_event_id,artist_name,venue_name,event_date,ticket_net,merch_revenue,"
            "artist_guarantee,marketing_spend,promoter_contribution\n"
            # net 100 + merch 20 - (guarantee 60 + marketing 10) = 50 == promoter 50 -> TIES
            "EVT400,A,B,2024-01-01,100,20,60,10,50\n"
            # net 100 - guarantee 60 = 40, but promoter reported 99 -> DOES_NOT_TIE
            "EVT401,C,D,2024-01-02,100,0,60,0,99\n"
        )
        path = _write_csv(tmp_path, "recon.csv", csv)
        report = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_r",
            events_repo=events,
        )
        statuses = [r["status"] for r in report.reconciliation]
        assert RECON_TIES in statuses
        assert RECON_DOES_NOT_TIE in statuses
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Outcome vault + blind export + PIT + freeze + eligibility + readiness
# ---------------------------------------------------------------------------
def _study_from_ingestion(econ, ingestion, target=SCANNED_ATTENDANCE, cutoff=CUTOFF_ANNOUNCEMENT):
    return RetrospectiveStudy(
        study_id="study_demo",
        customer_id="demo",
        dataset_id="ds_demo",
        target=target,
        decision_cutoff_type=cutoff,
        hidden_outcomes=DEFAULT_HIDDEN_OUTCOMES,
        allowed_private_inputs=DEFAULT_ALLOWED_PRIVATE_INPUTS,
        event_ids=tuple(sorted(set(ingestion.events_resolved))),
    )


def test_outcome_vault_hides_target_from_inputs(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        ingestion = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        study = _study_from_ingestion(econ, ingestion)
        econ.create_retrospective_study(study.to_dict())
        vault_outcomes(econ, study)

        inputs = retrospective_inputs(econ, study)
        hidden = hidden_claim_ids(econ, study)
        visible = set(inputs["visible_claim_ids"])

        assert hidden, "expected hidden outcome claims"
        # The target (scanned_attendance) must be hidden and never visible.
        assert not (visible & hidden)
        assert inputs["leakage_check"] == "PASS"
        visible_types = {
            t for ev in inputs["events"] for t in ev["visible_outcome_types"]
        }
        assert SCANNED_ATTENDANCE not in visible_types
    finally:
        repo.close()


def test_blind_export_separates_feature_and_outcome(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        ingestion = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        study = _study_from_ingestion(econ, ingestion)
        econ.create_retrospective_study(study.to_dict())
        blind = build_blind_export(econ, study)
        assert blind["separated"] is True
        assert "events" in blind["feature_side_manifest"]
        assert blind["outcome_side_manifest"]["target"] == SCANNED_ATTENDANCE
        outcome_types = {o["outcome_type"] for o in blind["outcome_side_manifest"]["outcomes"]}
        assert SCANNED_ATTENDANCE in outcome_types
    finally:
        repo.close()


def test_pit_cutoff_isolation(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        ingestion = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        study = _study_from_ingestion(econ, ingestion, cutoff=CUTOFF_EVENT)
        econ.create_retrospective_study(study.to_dict())
        pit = pit_reconstructability(econ, study)
        assert pit
        # Good row has an event date, so at EVENT cutoff it is COMPLETE (its
        # private capacity input is knowable before/at the event).
        assert pit[0]["status"] == PIT_COMPLETE
    finally:
        repo.close()


def test_pit_insufficient_when_cutoff_missing(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        csv = (
            "customer_event_id,artist_name,venue_name,event_date,tickets_sold\n"
            "EVT500,A,B,2024-05-05,100\n"  # no announcement/booking/onsale
        )
        path = _write_csv(tmp_path, "nocutoff.csv", csv)
        ingestion = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_nc",
            events_repo=events,
        )
        study = _study_from_ingestion(econ, ingestion, cutoff=CUTOFF_ANNOUNCEMENT)
        econ.create_retrospective_study(study.to_dict())
        pit = pit_reconstructability(econ, study)
        assert pit[0]["status"] == PIT_INSUFFICIENT
    finally:
        repo.close()


def test_study_freeze_immutability(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        ingestion = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        study = _study_from_ingestion(econ, ingestion)
        econ.create_retrospective_study(study.to_dict())
        assert econ.query_retrospective_study("study_demo")["status"] == "DRAFT"

        assert econ.freeze_retrospective_study("study_demo", status=STUDY_FROZEN) is True
        frozen = econ.query_retrospective_study("study_demo")
        assert frozen["status"] == STUDY_FROZEN
        assert frozen["frozen_at"]
        # Freezing a non-existent study fails cleanly.
        assert econ.freeze_retrospective_study("nope", status=STUDY_FROZEN) is False
    finally:
        repo.close()


def test_training_row_eligibility_reasons(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        csv = (
            "customer_event_id,artist_name,venue_name,event_date,booking_date,announcement_date,onsale_date,tickets_sold,scanned_attendance\n"
            "EVT600,A,B,2024-05-05,2023-11-01,2024-01-15,2024-01-20,100,95\n"   # has target + cutoffs
            "EVT601,C,D,2024-06-06,2023-12-01,2024-02-01,2024-02-05,200,\n"     # target missing
        )
        path = _write_csv(tmp_path, "elig.csv", csv)
        ingestion = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_elig",
            events_repo=events,
        )
        study = _study_from_ingestion(econ, ingestion, target=SCANNED_ATTENDANCE)
        econ.create_retrospective_study(study.to_dict())
        rows = {r["canonical_event_id"]: r for r in training_row_eligibility(econ, study)}
        assert rows["private_demo_evt600"]["eligible"] is True
        assert rows["private_demo_evt601"]["eligible"] is False
        assert rows["private_demo_evt601"]["exclusion_reason"] == EXCL_TARGET_MISSING
    finally:
        repo.close()


def test_baseline_readiness_not_ready_when_empty(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        study = RetrospectiveStudy(
            study_id="empty",
            customer_id="demo",
            dataset_id="ds_empty",
            target=SCANNED_ATTENDANCE,
            decision_cutoff_type=CUTOFF_EVENT,
            hidden_outcomes=DEFAULT_HIDDEN_OUTCOMES,
            allowed_private_inputs=DEFAULT_ALLOWED_PRIVATE_INPUTS,
            event_ids=(),
        )
        readiness = baseline_readiness(econ, events, study)
        assert readiness["verdict"] == READINESS_NOT_READY
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Audit report
# ---------------------------------------------------------------------------
def test_audit_report_generation(tmp_path):
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        ingestion = ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="demo", dataset_id="ds_demo",
            events_repo=events,
        )
        study = _study_from_ingestion(econ, ingestion)
        econ.create_retrospective_study(study.to_dict())
        report = build_audit_report(ingestion=ingestion, economics_repo=econ, events_repo=events, study=study)
        assert report["no_predictions"] is True
        assert "dataset_overview" in report
        assert "baseline_readiness" in report
        html = render_html(report)
        assert "<html" in html
        assert "Promoter Data Audit" in html

        out = tmp_path / "out"
        write_audit_report(report, json_path=str(out / "audit.json"), html_path=str(out / "audit.html"))
        assert (out / "audit.json").exists()
        assert (out / "audit.html").exists()
    finally:
        repo.close()


def test_synthetic_fixture_never_becomes_public(tmp_path):
    """Synthetic fixture claims are OBSERVED_PRIVATE, never OBSERVED_PUBLIC."""
    repo, econ, events = _repo(tmp_path)
    try:
        path = _write_csv(tmp_path, "shows.csv", GOOD_CSV)
        ingest_partner_files(
            economics_repo=econ, file_paths=[path], customer_id="synthetic_demo", dataset_id="ds_synth",
            events_repo=events,
        )
        claims = econ.query_outcome_claims()
        assert claims
        assert all(c["observation_class"] == OBSERVED_PRIVATE for c in claims)
    finally:
        repo.close()
