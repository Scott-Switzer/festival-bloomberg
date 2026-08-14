"""Promoter data audit report: a human-facing summary of a private import.

The report is valuable *before* any model exists: it tells a promoter how much
of their history is usable for a rigorous blind retrospective, what is broken,
and what to fix. It contains NO predictions and NO secret values.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from typing import Any

from .partner_import import PartnerIngestionReport
from .retrospective import (
    PIT_COMPLETE,
    PIT_INSUFFICIENT,
    PIT_PARTIAL,
    baseline_readiness,
    pit_reconstructability,
    training_row_eligibility,
)


def _mapping_summary(mappings: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for file_name, entries in mappings.items():
        summary[file_name] = dict(Counter(m["status"] for m in entries))
    return summary


def build_audit_report(
    *,
    ingestion: PartnerIngestionReport,
    economics_repo,
    events_repo,
    study,
) -> dict[str, Any]:
    pit = pit_reconstructability(economics_repo, study)
    eligibility = training_row_eligibility(economics_repo, study)
    readiness = baseline_readiness(economics_repo, events_repo, study)

    pit_counts = Counter(p["status"] for p in pit)
    eligibility_counts = Counter(
        (r["exclusion_reason"] or "ELIGIBLE") for r in eligibility
    )
    eligible_count = sum(1 for r in eligibility if r["eligible"])

    recon_statuses = Counter(r["status"] for r in ingestion.reconciliation)
    quality_checks = Counter(q["check"] for q in ingestion.quality_issues)

    return {
        "dataset_overview": {
            "dataset_id": ingestion.dataset_id,
            "customer_id": ingestion.customer_id,
            "files_read": ingestion.files_read,
            "rows_read": ingestion.rows_read,
            "events_resolved": len(set(ingestion.events_resolved)),
            "claims_inserted": ingestion.claims_inserted,
            "duplicates_skipped": ingestion.duplicates_skipped,
            "pii_quarantined": ingestion.pii_quarantined,
        },
        "mapping_summary": _mapping_summary(ingestion.mappings),
        "mapping_details": ingestion.mappings,
        "pii_quarantine": ingestion.pii,
        "quality_issues": {
            "total": len(ingestion.quality_issues),
            "by_check": dict(quality_checks),
            "details": ingestion.quality_issues,
        },
        "accounting_reconciliation": {
            "by_status": dict(recon_statuses),
            "details": ingestion.reconciliation,
        },
        "pit_reconstructability": {
            "by_status": dict(pit_counts),
            "complete": pit_counts.get(PIT_COMPLETE, 0),
            "partial": pit_counts.get(PIT_PARTIAL, 0),
            "insufficient": pit_counts.get(PIT_INSUFFICIENT, 0),
            "details": pit,
        },
        "model_eligibility": {
            "eligible": eligible_count,
            "ineligible": len(eligibility) - eligible_count,
            "by_exclusion": dict(eligibility_counts),
            "details": eligibility,
        },
        "baseline_readiness": readiness,
        "recommendations": _recommendations(ingestion, pit, eligibility),
        "no_predictions": True,
    }


def _recommendations(ingestion, pit, eligibility) -> list[str]:
    recs: list[str] = []
    if ingestion.pii_quarantined:
        recs.append("Remove buyer-level PII columns (name/email/phone/card) — they are quarantined and not needed.")
    if not ingestion.claims_inserted:
        recs.append("No outcome claims were imported — check column names against the data contract.")
    if any(r["status"] == RECON_DOES_NOT_TIE for r in ingestion.reconciliation):
        recs.append("Several rows do not reconcile (reported promoter contribution != implied) — review cost/revenue fields.")
    pit_insufficient = sum(1 for p in pit if p["status"] == PIT_INSUFFICIENT)
    if pit_insufficient:
        recs.append(
            f"{pit_insufficient} event(s) lack a decision cutoff (booking/announcement/onsale date); "
            "add them to enable PIT reconstruction."
        )
    if not any(r["eligible"] for r in eligibility):
        recs.append("No event is currently eligible for a baseline — resolve missing targets/cutoffs first.")
    return recs


# Reconciliation statuses (imported for recommendations above).
RECON_DOES_NOT_TIE = "DOES_NOT_TIE"


def render_html(report: dict[str, Any]) -> str:
    """Render the audit report as a self-contained, dependency-free HTML page."""
    overview = report["dataset_overview"]
    quality = report["quality_issues"]
    recon = report["accounting_reconciliation"]
    pit = report["pit_reconstructability"]
    eligibility = report["model_eligibility"]
    readiness = report["baseline_readiness"]

    def esc(value: Any) -> str:
        return html.escape(str(value))

    rows_html = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in overview.items()
    )
    recs = "".join(f"<li>{esc(r)}</li>" for r in report["recommendations"])
    issues = "".join(
        f"<tr><td>{esc(q.get('row'))}</td><td>{esc(q.get('check'))}</td><td>{esc(q.get('reason'))}</td></tr>"
        for q in quality["details"]
    )
    recon_rows = "".join(
        f"<tr><td>{esc(r.get('row'))}</td><td>{esc(r.get('status'))}</td><td>{esc(r.get('difference'))}</td></tr>"
        for r in recon["details"]
    )
    pit_rows = "".join(
        f"<tr><td>{esc(p.get('canonical_event_id'))}</td><td>{esc(p.get('status'))}</td><td>{esc(p.get('reason'))}</td></tr>"
        for p in pit["details"]
    )
    elig_rows = "".join(
        f"<tr><td>{esc(r.get('canonical_event_id'))}</td><td>{esc(r.get('eligible'))}</td><td>{esc(r.get('exclusion_reason'))}</td></tr>"
        for r in eligibility["details"]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Promoter Data Audit — {esc(overview.get('customer_id'))}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #111; }}
  h1, h2 {{ border-bottom: 1px solid #ddd; padding-bottom: .25rem; }}
  table {{ border-collapse: collapse; margin: .5rem 0 1.5rem; }}
  th, td {{ border: 1px solid #ccc; padding: .35rem .6rem; text-align: left; font-size: .9rem; }}
  th {{ background: #f4f4f4; }}
  .verdict {{ font-weight: 700; }}
  ul {{ margin: .5rem 0 1.5rem; }}
</style>
</head>
<body>
<h1>Promoter Data Audit</h1>
<h2>Dataset overview</h2>
<table>{rows_html}</table>
<h2>Data quality issues ({esc(quality['total'])})</h2>
<table><tr><th>row</th><th>check</th><th>reason</th></tr>{issues}</table>
<h2>Accounting reconciliation</h2>
<table><tr><th>row</th><th>status</th><th>difference</th></tr>{recon_rows}</table>
<h2>PIT reconstructability</h2>
<table><tr><th>event</th><th>status</th><th>reason</th></tr>{pit_rows}</table>
<h2>Model eligibility (no model trained)</h2>
<table><tr><th>event</th><th>eligible</th><th>exclusion</th></tr>{elig_rows}</table>
<h2>Baseline readiness</h2>
<p class="verdict">{esc(readiness.get('verdict'))}</p>
<ul>{"".join(f"<li>{esc(r)}</li>" for r in readiness.get('reasons', []))}</ul>
<h2>Recommendations</h2>
<ul>{recs}</ul>
<p><em>This report contains no predictions and no customer-identifying values beyond the customer id.</em></p>
</body>
</html>
"""


def write_audit_report(
    report: dict[str, Any],
    *,
    json_path: str,
    html_path: str,
) -> None:
    from pathlib import Path

    json_p = Path(json_path)
    html_p = Path(html_path)
    json_p.parent.mkdir(parents=True, exist_ok=True)
    html_p.parent.mkdir(parents=True, exist_ok=True)
    json_p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    html_p.write_text(render_html(report), encoding="utf-8")
