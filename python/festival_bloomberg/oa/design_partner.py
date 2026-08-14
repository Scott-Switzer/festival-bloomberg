"""Design Partner Retrospective V1 — synthetic workflow operational acceptance.

This OA exercises the private-data flywheel end-to-end using ONLY the
explicitly-marked synthetic structural fixture. It proves: file import,
mapping, validation, entity resolution, private claim creation, PII
quarantine, study creation/freeze, outcome hiding, blind exports, readiness,
and HTML audit — mechanics only, no real evidence coverage is claimed.

It runs against a dedicated DuckDB (never the real corpus) so the existing
public corpus remains isolated. Isolation is re-verified at the end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..economics.audit_report import build_audit_report, write_audit_report
from ..economics.design_partner import SHARING_PRIVATE_ONLY
from ..economics.partner_import import ingest_partner_files
from ..economics.repository import EconomicsRepository
from ..economics.retrospective import (
    CUTOFF_ANNOUNCEMENT,
    DEFAULT_ALLOWED_PRIVATE_INPUTS,
    DEFAULT_HIDDEN_OUTCOMES,
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
from ..events.repository import EventRepository
from ..warehouse.repository import FestivalRepository

OA_DB = (
    Path(__file__).resolve().parents[3] / "data" / "warehouse" / "design_partner_retrospective_oa.duckdb"
)
REAL_CORPUS_DB = (
    Path(__file__).resolve().parents[3] / "data" / "warehouse" / "artist_market_event_history.duckdb"
)
SYNTHETIC_FIXTURE = (
    Path(__file__).resolve().parents[3] / "data" / "fixtures" / "synthetic_design_partner_shows.csv"
)
SYNTHETIC_TARGET = "SCANNED_ATTENDANCE"


def _corpus_signature(db_path: Path) -> dict[str, Any] | None:
    """A compact fingerprint of the real corpus to prove isolation."""
    if not db_path.exists():
        return None
    repo = FestivalRepository(str(db_path))
    try:
        econ = EconomicsRepository(repo.conn)
        claims = econ.query_outcome_claims()
        public = [c for c in claims if c.get("observation_class") == "OBSERVED_PUBLIC"]
        private = [c for c in claims if c.get("observation_class") == "OBSERVED_PRIVATE"]
        return {
            "db": str(db_path),
            "public_claims": len(public),
            "private_claims": len(private),
            "total_claims": len(claims),
        }
    finally:
        repo.close()


def run_design_partner_oa(
    db_path: str | Path = OA_DB,
    *,
    report_dir: str | Path = "reports/design_partner_retrospective_v1",
) -> dict[str, Any]:
    report_dir_path = Path(report_dir)
    report_dir_path.mkdir(parents=True, exist_ok=True)

    before = _corpus_signature(REAL_CORPUS_DB)

    repo = FestivalRepository(str(db_path))
    try:
        events_repo = EventRepository(repo.conn)
        econ = EconomicsRepository(repo.conn)

        # 1. Ingest the synthetic fixture (clearly-marked, never real).
        ingestion = ingest_partner_files(
            economics_repo=econ,
            file_paths=[str(SYNTHETIC_FIXTURE)],
            customer_id="synthetic_demo_promoter",
            dataset_id="ds_synthetic_demo",
            sharing_policy=SHARING_PRIVATE_ONLY,
            events_repo=events_repo,
        )

        # 2. Create the retrospective study.
        event_ids = tuple(sorted(set(ingestion.events_resolved)))
        study = RetrospectiveStudy(
            study_id="study_synthetic_demo",
            customer_id="synthetic_demo_promoter",
            dataset_id="ds_synthetic_demo",
            target=SYNTHETIC_TARGET,
            decision_cutoff_type=CUTOFF_ANNOUNCEMENT,
            hidden_outcomes=DEFAULT_HIDDEN_OUTCOMES,
            allowed_private_inputs=DEFAULT_ALLOWED_PRIVATE_INPUTS,
            event_ids=event_ids,
        )
        econ.create_retrospective_study(study.to_dict())

        # 3. Vault hidden outcomes + freeze.
        vault = vault_outcomes(econ, study)
        econ.freeze_retrospective_study(study.study_id, status=STUDY_FROZEN)

        # 4. Blind export + feature-side leakage check.
        inputs = retrospective_inputs(econ, study)
        blind = build_blind_export(econ, study)
        hidden = hidden_claim_ids(econ, study)
        leakage = bool(set(inputs["visible_claim_ids"]) & hidden)

        # 5. PIT / eligibility / readiness.
        pit = pit_reconstructability(econ, study)
        eligibility = training_row_eligibility(econ, study)
        readiness = baseline_readiness(econ, events_repo, study)

        # 6. Audit report (JSON + HTML).
        audit = build_audit_report(
            ingestion=ingestion,
            economics_repo=econ,
            events_repo=events_repo,
            study=study,
        )
        write_audit_report(
            audit,
            json_path=str(report_dir_path / "promoter_data_audit_study_synthetic_demo.json"),
            html_path=str(report_dir_path / "promoter_data_audit_study_synthetic_demo.html"),
        )

        after = _corpus_signature(REAL_CORPUS_DB)

        manifest: dict[str, Any] = {
            "software_version": "design_partner_retrospective_v1",
            "oa_kind": "SYNTHETIC_WORKFLOW_OA",
            "generated_at": utc_now().isoformat(),
            "ingestion": ingestion.to_dict(),
            "study": study.to_dict(),
            "vault": {k: v for k, v in vault.items() if k != "vaulted_claim_ids"},
            "vaulted_claim_count": vault["claims_vaulted"],
            "leakage_check": "FAIL" if leakage else "PASS",
            "visible_input_claim_ids": inputs["visible_claim_ids"],
            "hidden_claim_ids": sorted(hidden),
            "blind_export_separated": blind["separated"],
            "outcome_side_target": blind["outcome_side_manifest"]["target"],
            "pit_reconstructability": pit,
            "training_row_eligibility": eligibility,
            "baseline_readiness": readiness,
            "real_corpus_before": before,
            "real_corpus_after": after,
            "real_corpus_isolation": "PASS" if before == after else "FAIL",
            "provider_cost_usd": 0.0,
            "monid_usage": "NONE",
            "apify_usage": "NONE",
        }

        manifest_path = report_dir_path / "design_partner_retrospective_v1.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_design_partner_oa()
    print(json.dumps(result, indent=2, default=str))
