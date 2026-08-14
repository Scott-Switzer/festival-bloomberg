"""Public Boxscore Research Corpus V1 — live operational acceptance.

Fetches the archived Billboard Current Boxscore, a Pollstar Hot Tickets page,
and a Touring Data tour page; parses them into BOXOFFICE_ENGAGEMENTs with
explicit headcount semantics; ingests them into a dedicated research DuckDB;
promotes only single-show, reported, non-estimated engagements into the
outcome claim ledger (research-only rights); and reports the fail-closed
research/commercial split.

All sources here are RESEARCH_ONLY / TERMS_REVIEW_REQUIRED. Nothing becomes
commercial-eligible. Bounded, $0.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.transport import UrllibTransport
from ..economics.repository import EconomicsRepository
from ..research.acquisition import BOXSCORE_SOURCES, corpus_report, parse_source
from ..research.boxscore import SOURCE_TOURING_DATA
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

RESEARCH_DB = (
    Path(__file__).resolve().parents[3] / "data" / "warehouse" / "boxoffice_research.duckdb"
)

# (touring-data url, artist)
TOURING_PAGES: list[tuple[str, str, str]] = [
    ("https://touringdata.org/2019/10/23/post-malone-runaway-tour/", SOURCE_TOURING_DATA, "Post Malone"),
]


def _fetch(transport: UrllibTransport, url: str) -> tuple[int, str]:
    response = transport.request("GET", url, timeout_seconds=45)
    text = response.body.decode("utf-8", errors="replace")
    return response.status, text


def run_boxscore_oa(
    db_path: str | Path = RESEARCH_DB,
    *,
    report_path: str | Path = "reports/public_boxscore_research_corpus_v1.json",
) -> dict[str, Any]:
    repo = FestivalRepository(str(db_path))
    try:
        research = ResearchRepository(repo.conn)
        econ = EconomicsRepository(repo.conn)
        transport = UrllibTransport()

        ingestion: dict[str, Any] = {"fetched": 0, "failed": 0, "engagements": 0, "details": []}
        for source, url, kind in BOXSCORE_SOURCES:
            try:
                status, text = _fetch(transport, url)
                if status != 200:
                    ingestion["failed"] += 1
                    ingestion["details"].append({"source": source, "url": url, "status": status})
                    continue
                parsed = parse_source(source, text, source_url=url)
                inserted = 0
                for engagement in parsed:
                    if research.insert_engagement(engagement):
                        inserted += 1
                ingestion["fetched"] += 1
                ingestion["engagements"] += inserted
                ingestion["details"].append({
                    "source": source, "url": url, "parsed": len(parsed), "inserted": inserted,
                })
            except Exception as exc:  # noqa: BLE001 - bounded live fetch
                ingestion["failed"] += 1
                ingestion["details"].append({"source": source, "url": url, "error": str(exc)})

        # Additional Touring Data pages (date-level reported attendance/gross).
        for url, source, artist in TOURING_PAGES:
            try:
                status, text = _fetch(transport, url)
                if status != 200:
                    continue
                parsed = parse_source(source, text, source_url=url, artist=artist)
                inserted = 0
                for engagement in parsed:
                    if research.insert_engagement(engagement):
                        inserted += 1
                ingestion["engagements"] += inserted
                ingestion["details"].append({
                    "source": source, "url": url, "parsed": len(parsed), "inserted": inserted,
                })
            except Exception as exc:  # noqa: BLE001
                ingestion["details"].append({"source": source, "url": url, "error": str(exc)})

        report = corpus_report(research)
        promotion = research.promote_single_show_engagements(econ)

        claims = econ.query_outcome_claims()
        claim_types = Counter(c["outcome_type"] for c in claims)
        claim_rights = Counter(c["rights_status"] for c in claims)

        manifest: dict[str, Any] = {
            "software_version": "public_boxscore_research_corpus_v1",
            "generated_at": utc_now().isoformat(),
            "ingestion": ingestion,
            "corpus_report": report,
            "promotion": {
                k: v for k, v in promotion.items() if k != "claim_ids"
            },
            "promoted_claim_count": promotion["claims_promoted"],
            "claims_by_type": dict(claim_types),
            "claims_by_rights": dict(claim_rights),
            "research_commercial_split": {
                "research_only": report["research_corpus"],
                "commercial_eligible": report["commercial_eligible_corpus"],
                "verdict": report["rights_verdict"],
            },
            "provider_cost_usd": 0.0,
            "monid_usage": "NONE",
            "apify_usage": "NONE",
        }

        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_boxscore_oa()
    print(json.dumps(result, indent=2, default=str))
