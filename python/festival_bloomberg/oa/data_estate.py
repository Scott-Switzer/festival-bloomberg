"""INTELLIGENCE_DATA_ESTATE_AND_FESTIVAL_SPINE_V1 — live operational acceptance.

Write side that the read-only terminal depends on:

1. Ingest the research-seed festival spine (idempotent, source-backed,
   RESEARCH_DISCOVERY_SEED — never observed facts).
2. Derive festival + event activity-tape entries (append-only, idempotent).
3. Measure entity/evidence coverage BEFORE and AFTER.
4. Report provider operational status + credential PRESENCE ONLY (no values).
5. Bounded auth validation: NVIDIA list_models() and one NWS forecast call.
   Both fail-closed; a failure is recorded honestly, never retried in a loop.

No secrets are ever written to the report.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..acquisition.contracts import utc_now
from ..config import all_credential_status, provider_credential_status
from ..festivals.repository import FestivalSpineRepository
from ..festivals.seed import build_seed_rows
from ..flywheel.repository import FlywheelRepository
from ..intelligence.llm import NimClient
from ..intelligence.providers import provider_statuses
from ..intelligence.readmodels import get_sources
from ..intelligence.tape import (
    derive_festival_tape_entries,
    derive_tape_entries,
    insert_tape_entries,
)
from ..localenv import load_local_env
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "data_estate_festival_spine_v1"


def _count(conn, sql: str, params: list[Any] | None = None) -> int:
    try:
        return int(conn.execute(sql, params or []).fetchone()[0])
    except Exception:
        return 0


def _table_rows(conn, table: str) -> int:
    try:
        return _count(conn, f"SELECT COUNT(*) FROM {table}")
    except Exception:
        return 0


def run_data_estate_oa(
    *,
    db_path: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/data_estate_festival_spine_v1.json",
    validate_providers: bool = True,
) -> dict[str, Any]:
    load_local_env()
    started = utc_now()
    oa_run_id = f"data_estate_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(db_path)
    try:
        flywheel = FlywheelRepository(repo.conn)
        ResearchRepository(repo.conn)
        conn = repo.conn

        # ---- BEFORE snapshot -------------------------------------------------
        def snapshot() -> dict[str, int]:
            return {
                "festivals": _table_rows(conn, "core.festivals"),
                "festival_editions": _table_rows(conn, "core.festival_editions"),
                "lineup_slots": _table_rows(conn, "core.lineup_slots"),
                "billing_observations": _table_rows(conn, "core.festival_billing_observations"),
                "activity_tape": _table_rows(conn, "terminal.activity_tape"),
                "canonical_engagements": _table_rows(conn, "research.canonical_boxoffice_engagements"),
                "forward_events": _table_rows(conn, "flywheel.forward_watch_events"),
                "outcome_claims": _table_rows(conn, "economics.event_outcome_claims"),
            }

        before = snapshot()

        # ---- 1. Festival spine ingestion ------------------------------------
        spine = FestivalSpineRepository(conn)
        seed_counts = spine.ingest_seed(build_seed_rows())
        conn.commit()

        # ---- 2. Activity tape derivation ------------------------------------
        tape_before = _table_rows(conn, "terminal.activity_tape")
        event_rows = derive_tape_entries(conn)
        festival_rows = derive_festival_tape_entries(conn)
        new_event = insert_tape_entries(conn, event_rows)
        new_festival = insert_tape_entries(conn, festival_rows)
        conn.commit()
        tape_after = _table_rows(conn, "terminal.activity_tape")

        after = snapshot()

        # ---- 3. Provider status + credential presence (no values) -----------
        providers = provider_statuses()
        creds = provider_credential_status()
        for p in providers:
            p["credential_presence"] = {
                "configured": p["credentials"]["configured"],
            }

        # ---- 4. Bounded live validation -------------------------------------
        nvidia = {"status": "NOT_CONFIGURED", "models": []}
        nws = {"status": "NOT_RUN"}
        if validate_providers:
            client = NimClient()
            if client.is_configured:
                nvidia = client.list_models()
                nvidia["configured"] = True
            else:
                nvidia = {"status": "NOT_CONFIGURED", "models": [], "configured": False}
            # One bounded, key-free NWS forecast (Chicago loop).
            try:
                from ..acquisition.providers.nws import NwsProvider
                from ..acquisition.transport import UrllibTransport
                from ..acquisition.contracts import AcquisitionRequest

                req = AcquisitionRequest.new(
                    entity_id="chicago", entity_type="market", platform="nws",
                    query="41.8781,-87.6298", commercial_context="research",
                )
                nws_provider = NwsProvider(transport=UrllibTransport())
                nws_result = nws_provider.acquire(req)
                nws = {
                    "status": nws_result.status.value,
                    "record_count": nws_result.record_count,
                    "generation_time": (nws_result.provider_metadata or {}).get("generation_time"),
                }
            except Exception as exc:  # noqa: BLE001 — network is allowed to fail
                nws = {"status": "ERROR", "detail": f"{type(exc).__name__}"}

        # ---- 5. Festival examples (real, source-backed) ---------------------
        examples = []
        for fk in ("woodstock-music-and-art-fair", "coachella-valley-music-and-arts-festival"):
            fest = spine.get_festival(fk)
            if fest:
                examples.append({
                    "festival_key": fest["festival_key"],
                    "name": fest["name"],
                    "editions": [e["edition_key"] for e in fest["editions"]],
                    "lineup_size": sum(len(spine.get_lineup(e["edition_key"])) for e in fest["editions"]),
                    "billing_observations": sum(len(spine.get_billing(e["edition_key"])) for e in fest["editions"]),
                })

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "before": before,
            "after": after,
            "seed_ingestion": seed_counts,
            "activity_tape": {
                "rows_before": tape_before,
                "rows_after": tape_after,
                "new_event_rows": new_event,
                "new_festival_rows": new_festival,
            },
            "providers": providers,
            "credential_presence": {
                k: {"present": v["present_any"], "nonempty": v["nonempty_any"]}
                for k, v in creds.items()
            },
            "live_validation": {
                "nvidia": nvidia,
                "nws": nws,
            },
            "festival_examples": examples,
            "distinct_artists_in_festivals": _count(
                conn, "SELECT COUNT(DISTINCT lower(artist_name)) FROM core.lineup_slots"
            ),
            "artists_with_2_plus_festival_appearances": _count(
                conn,
                "SELECT COUNT(*) FROM (SELECT lower(artist_name) AS k FROM core.lineup_slots "
                "GROUP BY lower(artist_name) HAVING COUNT(DISTINCT festival_key) >= 2)",
            ),
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_data_estate_oa()
    print(json.dumps(result, indent=2, default=str))
