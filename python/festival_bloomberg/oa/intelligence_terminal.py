"""FESTIVAL_INTELLIGENCE_TERMINAL_MVP_V1 — live operational acceptance.

The terminal is read-only; this OA is the WRITE side it depends on:

1. Derive the activity tape from the persisted warehouse (idempotent,
   append-only) so the homepage has real "what changed" rows.
2. Measure entity coverage (artists / events / venues / markets / festivals)
   and tape depth.
3. Compute honest provider health (fail-closed: a provider without a key is
   NOT_CONFIGURED, never OPERATIONAL).

No fabricated facts: every tape row traces to a persisted source row, and a
provider with no key makes zero requests.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..acquisition.contracts import utc_now
from ..intelligence.providers import provider_statuses
from ..intelligence.readmodels import (
    get_sources,
    search_entities,
)
from ..intelligence.tape import derive_tape_entries, insert_tape_entries
from ..localenv import load_local_env
from ..flywheel.repository import FlywheelRepository
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "intelligence_terminal_mvp_v1"


def _count(conn, sql: str, params: list[Any] | None = None) -> int:
    try:
        return int(conn.execute(sql, params or []).fetchone()[0])
    except Exception:
        return 0


def run_intelligence_terminal_oa(
    *,
    research_db: str = "data/warehouse/boxoffice_research_v2.duckdb",
    report_path: str | Path = "reports/intelligence_terminal_mvp_v1.json",
) -> dict[str, Any]:
    load_local_env()
    started = utc_now()
    oa_run_id = f"terminal_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(research_db)
    try:
        flywheel = FlywheelRepository(repo.conn)
        ResearchRepository(repo.conn)  # applies migrations; unused directly

        # 1. Activity tape derivation (idempotent, append-only).
        tape_before = _count(repo.conn, "SELECT COUNT(*) FROM terminal.activity_tape")
        entries = derive_tape_entries(repo.conn)
        new_tape = insert_tape_entries(repo.conn, entries)
        repo.conn.commit()
        tape_after = _count(repo.conn, "SELECT COUNT(*) FROM terminal.activity_tape")

        tape_by_type: dict[str, int] = {}
        for r in repo.conn.execute(
            "SELECT activity_type, COUNT(*) FROM terminal.activity_tape GROUP BY activity_type"
        ).fetchall():
            tape_by_type[r[0]] = int(r[1])

        # 2. Entity coverage (real warehouse counts, never fabricated).
        coverage = {
            "historical_engagements": _count(
                repo.conn, "SELECT COUNT(*) FROM research.canonical_boxoffice_engagements"
            ),
            "distinct_artists": _count(
                repo.conn, "SELECT COUNT(DISTINCT artist) FROM research.canonical_boxoffice_engagements"
            ),
            "distinct_venues": _count(
                repo.conn, "SELECT COUNT(DISTINCT venue) FROM research.canonical_boxoffice_engagements"
            ),
            "distinct_markets": _count(
                repo.conn, "SELECT COUNT(DISTINCT city) FROM research.canonical_boxoffice_engagements"
            ),
            "forward_events": _count(
                repo.conn, "SELECT COUNT(*) FROM flywheel.forward_watch_events"
            ),
            "forward_observations": _count(
                repo.conn, "SELECT COUNT(*) FROM flywheel.forward_watch_observations"
            ),
            "outcome_claims": _count(
                repo.conn, "SELECT COUNT(*) FROM economics.event_outcome_claims"
            ),
            "pre_event_cutoff_evidence": _count(
                repo.conn, "SELECT COUNT(*) FROM flywheel.pre_event_cutoff_evidence"
            ),
            "pit_reconstruction_evidence": _count(
                repo.conn, "SELECT COUNT(*) FROM flywheel.pit_reconstruction_evidence"
            ),
            "festivals": 0,  # no canonical festival corpus yet (honest)
        }
        events_with_2_plus_obs = _count(
            repo.conn,
            "SELECT COUNT(*) FROM (SELECT watch_event_id FROM flywheel.forward_watch_observations "
            "GROUP BY watch_event_id HAVING COUNT(*) >= 2)",
        )
        coverage["events_with_2_plus_observations"] = events_with_2_plus_obs

        # 3. Provider health (registry rights + operational status + tape depth).
        #    Register the two sources missing from the V1 registry (idempotent).
        for sid, sname, skind in (
            ("listenbrainz", "ListenBrainz", "CONTEXT"),
            ("nws", "National Weather Service", "CONTEXT"),
        ):
            repo.conn.execute(
                """
                INSERT INTO flywheel.source_registry
                    (source_id, source_name, source_kind, pipeline, access_status,
                     rights_status, commercial_use_status, license, notes, registered_at)
                VALUES (?, ?, ?, ?, 'AVAILABLE', 'OPEN_COMMERCIAL_OK', 'OPEN_COMMERCIAL_OK',
                        ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (source_id) DO NOTHING
                """,
                [sid, sname, skind, "CONTEXT_PANEL", "CC0" if sid == "listenbrainz" else None,
                 "attention/consumption sample, never demand" if sid == "listenbrainz"
                 else "forecasts snapshot at observation time"],
            )
        repo.conn.commit()

        health_rows = provider_statuses()
        registry = get_sources(repo.conn)
        reg_map = {r["source_id"]: r for r in registry}
        for h in health_rows:
            reg = reg_map.get(h["provider"], {})
            h["rights_status"] = reg.get("rights_status", h["rights_status"])
            h["commercial_use_status"] = reg.get("commercial_use_status", h["commercial_use_status"])
            h["access_status"] = reg.get("access_status", "NOT_AVAILABLE")
        # Persist provider health for the DATA page.
        for h in health_rows:
            repo.conn.execute(
                """
                INSERT INTO terminal.provider_health
                    (provider, operational_status, measured_at, software_version,
                     freshness_note)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (provider) DO UPDATE SET
                    operational_status = excluded.operational_status,
                    measured_at = excluded.measured_at,
                    software_version = excluded.software_version,
                    freshness_note = excluded.freshness_note
                """,
                [
                    h["provider"], h["operational_status"], started,
                    SOFTWARE_VERSION, h.get("quota_note"),
                ],
            )
        repo.conn.commit()

        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "activity_tape": {
                "rows_before": tape_before,
                "rows_after": tape_after,
                "new_rows_written": new_tape,
                "by_activity_type": tape_by_type,
            },
            "entity_coverage": coverage,
            "provider_health": health_rows,
            "demo_search": {
                "artist_sample": search_entities(repo.conn, "taylor swift", limit=1),
                "venue_sample": search_entities(repo.conn, "united center", limit=1),
            },
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


if __name__ == "__main__":
    result = run_intelligence_terminal_oa()
    print(json.dumps(result, indent=2, default=str))
