"""Baseline Research V1 — live operational acceptance.

Freezes the boxscore research corpus into a checksummed manifest, then runs
the full model ladder (global → historical comps → log-linear/Ridge/Poisson →
logistic → partial pooling) under TIME, ARTIST, VENUE, MARKET and TOUR holds,
with cluster bootstrap, negative controls, ablations, and error segmentation.

No tree/boosting/neural models. No production API. The only question answered
is whether simple historical comps predict future box-office outcomes better
than trivial baselines, and whether statistical models beat the comps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..research.experiment import run_baseline_research
from ..research.freeze import CORPUS_VERSION, freeze_research_corpus, write_manifest

RESEARCH_DB = (
    Path(__file__).resolve().parents[3] / "data" / "warehouse" / "boxoffice_research_v2.duckdb"
)

REPORT_DIR = Path("reports/baseline_research_v1")


def run_baseline_research_oa(
    db_path: str | Path = RESEARCH_DB,
    *,
    report_dir: str | Path = REPORT_DIR,
    seed: int = 42,
) -> dict[str, Any]:
    db = Path(db_path)
    if not db.exists():
        return {"error": "research corpus DB not found; run the boxscore V2 OA first"}

    manifest = freeze_research_corpus(db)
    report = run_baseline_research(manifest["rows"], seed=seed)

    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, out_dir / "corpus_v1_manifest.json")

    payload = {
        "software_version": "baseline_research_v1",
        "corpus_version": CORPUS_VERSION,
        "corpus_checksum": manifest["checksum"],
        "seed": seed,
        "corpus_rows": manifest["row_count"],
        "billboard_publication_time_estimate": manifest["billboard_publication_time_estimate"],
        "report": report,
    }
    (out_dir / "baseline_research_v1.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    result = run_baseline_research_oa()
    print(json.dumps(result, indent=2, default=str))
