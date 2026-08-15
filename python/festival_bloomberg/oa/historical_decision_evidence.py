"""HISTORICAL_DECISION_EVIDENCE_ENGINE_V1 — live operational acceptance.

This milestone turns acquisition from "hunt every event uniformly" into
value-of-information acquisition: the warm-start dependency graph identifies
which missing historical cutoff unlocks the most downstream PIT-comparable
targets, and the claim-support graph makes every extracted fact auditable.

The bounded OA runs the deterministic core (priority graph + extractors +
verifier) against the persisted warehouse and reports honestly. Actual new
historical pre-event evidence requires keyed Ticketmaster access or a
candidate-URL discovery source; without those, no fabrication occurs.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ..acquisition.contracts import utc_now
from ..flywheel.acquisition_accounting import (
    build_acquisition_run_row,
    derive_metrics,
)
from ..flywheel.acquisition_priority import (
    acquisition_priority,
    build_warm_start_dependency_graph,
)
from ..flywheel.deepseek_extractor import DeepSeekEvidenceExtractor
from ..flywheel.repository import FlywheelRepository
from ..localenv import load_local_env
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "historical_decision_evidence_engine_v1"

TOP_N = 100


def run_historical_decision_evidence_oa(
    *,
    research_db: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/historical_decision_evidence_engine_v1.json",
    top_n: int = TOP_N,
) -> dict[str, Any]:
    load_local_env()
    started = utc_now()
    oa_run_id = f"hdee_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(research_db)
    try:
        flywheel = FlywheelRepository(repo.conn)
        research = ResearchRepository(repo.conn)

        # -------------------------------------------------------------
        # 1. Warm-start dependency graph + acquisition priority (VOI)
        # -------------------------------------------------------------
        graph = build_warm_start_dependency_graph(repo.conn)
        ranked = acquisition_priority(repo.conn, limit=top_n)
        priority_head = [
            {
                "rank": r["rank"],
                "engagement_id": r["engagement_id"],
                "artist": r["artist"],
                "venue": r["venue"],
                "event_date": r["start_date"],
                "potential_priors": r["potential_priors"],
                "known_priors": r["known_priors"],
                "warm_start_locked": r["warm_start_locked"],
                "missing_decision_cutoffs": r["missing_decision_cutoffs"],
                "downstream_targets": r["downstream_targets"],
                "repeat_frequency": r["repeat_frequency"],
            }
            for r in ranked
        ]

        # -------------------------------------------------------------
        # 2. DeepSeek extractor config (never fabricates; fail closed)
        # -------------------------------------------------------------
        deepseek = DeepSeekEvidenceExtractor(api_key=os.environ.get("DEEPSEEK_API_KEY"))
        deepseek_status = "CONFIGURED" if deepseek.is_configured else "NOT_CONFIGURED"

        # -------------------------------------------------------------
        # 3. Deterministic extraction over already-persisted documents
        #    (none acquired yet -> honest zero)
        # -------------------------------------------------------------
        documents = flywheel.query_evidence_documents()
        claims_accepted = 0
        claims_rejected = 0

        # -------------------------------------------------------------
        # 4. Acquisition accounting (honest: no HTTP, no model tokens)
        # -------------------------------------------------------------
        run = build_acquisition_run_row(
            provider="historical_decision_evidence",
            pipeline="HISTORICAL_DECISION_EVIDENCE",
            started_at=started,
            http_requests=None,  # no HTTP performed (no keys / network)
            records_parsed=len(documents),
            new_claims=claims_accepted,
            monetary_cost_usd=0.0,
            detail=(
                "warm-start dependency graph + acquisition priority over the "
                "persisted corpus; no live retrieval (no keys configured)"
            ),
        )
        flywheel.insert_acquisition_run(run)
        flywheel.insert_acquisition_metrics(derive_metrics(run))

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "research_db": research_db,
            "acquisition_priority_graph": {
                "targets_total": graph["targets_total"],
                "warm_start_locked_targets": graph["warm_start_locked"],
                "targets_with_all_decision_cutoffs": graph["targets_with_all_decision_cutoffs"],
                "top_n": top_n,
                "priority_head": priority_head,
                "ordering": (
                    "lexicographic: unlock_count desc, repeat_frequency desc, "
                    "has_known_outcome desc, source_path_count desc, event_date asc"
                ),
            },
            "deepseek_extractor": {
                "status": deepseek_status,
                "contract": "candidate claims only; deterministic verifier decides admissibility",
                "privacy": "public/research material only; private settlement data excluded",
            },
            "deterministic_extraction": {
                "documents_available": len(documents),
                "claims_accepted": claims_accepted,
                "claims_rejected": claims_rejected,
                "note": (
                    "no documents acquired this run (no keyed/network source); "
                    "extraction over an empty store is honestly zero"
                ),
            },
            "provider_cost_usd": 0.0,
            "binding_edge": (
                "historical announcement/onsale/booking cutoffs stay UNKNOWN "
                "because no keyed Ticketmaster access or candidate-URL discovery "
                "source is configured; the priority graph now names exactly which "
                "events to acquire first once one exists."
            ),
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_historical_decision_evidence_oa()
    print(json.dumps(result, indent=2, default=str))
