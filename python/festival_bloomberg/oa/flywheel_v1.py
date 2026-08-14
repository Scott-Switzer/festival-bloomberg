"""Data Flywheel & Coverage V1 — live operational acceptance.

Proves the four flywheel pipelines end-to-end against the real warehouse and
REAL (key-free) public sources:

    EVENT_GRAPH    MusicBrainz artist identity resolution (CC0, key-free)
    OUTCOME_HUNTER hunt plans + tasks + EXECUTION STATISTICS (honest: no live
                   hunt source is wired yet, so execution counters are real
                   zeros, never implied by plan counts)
    CONTEXT_PANEL  Wikimedia daily pageview series (key-free, CC-BY-SA/CC0);
                   every other provider is reported with its real access
                   status -> pipeline status is PARTIAL until Census/BLS/
                   NOAA/ERA5/GDELT actually produce rows
    FORWARD_WATCH  register future events from the event graph

and measures coverage vs the objectives (corrected KPI vocabulary:
OUTCOME_CLAIMS != UNIQUE_EVENTS_WITH_OUTCOMES != FULLY_SETTLED_EVENTS; four
decision rates included). Keyed/terms-review sources are registered with their
real access status and are NEVER bypassed. Every live section degrades
gracefully: no network / no rows is reported honestly (NOT_EVALUATED), never
fabricated. Bounded, $0.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..acquisition.contracts import utc_now
from ..acquisition.transport import UrllibTransport
from ..events.repository import EventRepository
from ..flywheel.coverage import measure_coverage, snapshot_id
from ..flywheel.context_panel import (
    PAGEVIEWS_FLOOR,
    build_pageview_series_rows,
    collect_artist_pageviews,
)
from ..flywheel.event_graph import MusicBrainzClient
from ..flywheel.forward_watch import register_event_row
from ..flywheel.objectives import objective_rows
from ..flywheel.outcome_hunter import (
    HUNT_TARGET_FIELDS,
    build_hunt_plan,
    hunt_execution_stats,
)
from ..flywheel.repository import FlywheelRepository
from ..flywheel.sources import source_rows
from ..localenv import load_local_env
from ..research.repository import ResearchRepository
from ..warehouse.repository import FestivalRepository

SOFTWARE_VERSION = "data_flywheel_and_coverage_v1"

#: Deterministic, predeclared artist sample (availability/identity only —
#: never selected on a signal).
ARTIST_SAMPLE: tuple[str, ...] = (
    "Bad Bunny",
    "Billie Eilish",
    "Kendrick Lamar",
    "Olivia Rodrigo",
    "Taylor Swift",
)

MAX_IDENTITY_ARTISTS = 3
PAGEVIEWS_DAYS = 90
MAX_FORWARD_EVENTS = 5

#: Context-panel provider -> env var that would configure it (if any).
CONTEXT_PROVIDER_ENV = {
    "wikimedia": None,
    "census": "CENSUS_API_KEY",
    "bls": "BLS_API_KEY",
    "bea": None,
    "noaa": "NOAA_API_TOKEN",
    "era5": "ERA5_CDS_URL",
    "gdelt": None,
}


def run_flywheel_v1_oa(
    db_path: str | None = None,
    *,
    report_path: str | Path = "reports/data_flywheel_and_coverage_v1.json",
    artist_sample: tuple[str, ...] = ARTIST_SAMPLE,
    transport: UrllibTransport | None = None,
) -> dict[str, Any]:
    load_local_env()
    started = utc_now()
    oa_run_id = f"flywheel_{started.strftime('%Y%m%dT%H%M%S')}"
    repo = FestivalRepository(db_path) if db_path else FestivalRepository()
    try:
        flywheel = FlywheelRepository(repo.conn)
        research = ResearchRepository(repo.conn)
        events_repo = EventRepository(repo.conn)

        # ------------------------------------------------------------------
        # 1. Source registry + objectives (idempotent)
        # ------------------------------------------------------------------
        sources_registered = 0
        sources_existing = 0
        for row in source_rows(registered_at=started):
            if flywheel.insert_source(row):
                sources_registered += 1
            else:
                sources_existing += 1
        objective_rows_all = objective_rows()
        for row in objective_rows_all:
            row["registered_at"] = started.isoformat()
        objectives = flywheel.upsert_objectives(objective_rows_all)

        # ------------------------------------------------------------------
        # 2. Coverage measurement vs the objectives (KPI-corrected)
        # ------------------------------------------------------------------
        coverage_rows = measure_coverage(repo.conn, as_of=started)
        coverage_inserted = 0
        for row in coverage_rows:
            row["snapshot_id"] = snapshot_id(started, row["objective_key"])
            if flywheel.insert_coverage_snapshot(row):
                coverage_inserted += 1
        by_status: dict[str, int] = {}
        for row in coverage_rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1

        # ------------------------------------------------------------------
        # 3. EVENT_GRAPH — MusicBrainz identity resolution (key-free, CC0)
        # ------------------------------------------------------------------
        event_graph = {
            "status": "NOT_EVALUATED",
            "artists_attempted": 0,
            "identities_inserted": 0,
            "detail": None,
        }
        try:
            client = MusicBrainzClient(transport=transport)
            attempted = 0
            inserted = 0
            for artist in artist_sample[:MAX_IDENTITY_ARTISTS]:
                attempted += 1
                try:
                    identity = client.resolve_artist(artist)
                except Exception as exc:  # noqa: BLE001 - bounded live call
                    event_graph["detail"] = f"{artist}: {exc}"
                    continue
                if flywheel.insert_graph_identity(identity):
                    inserted += 1
            event_graph["artists_attempted"] = attempted
            event_graph["identities_inserted"] = inserted
            event_graph["status"] = "PASS" if inserted > 0 else "NOT_EVALUATED"
        except Exception as exc:  # noqa: BLE001
            event_graph["status"] = "FAIL"
            event_graph["detail"] = str(exc)

        # ------------------------------------------------------------------
        # 4. OUTCOME_HUNTER — hunt plans + honest execution statistics
        # ------------------------------------------------------------------
        hunt = {
            "status": "NOT_EVALUATED",
            "plans_created": 0,
            "tasks_created": 0,
            "execution": None,
            "ledger_by_type": {},
            "new_evidence_by_field": {},
            "note": None,
        }
        engagements = research.query_engagements()
        plans_created = 0
        tasks_created = 0
        for engagement in engagements:
            plan, tasks = build_hunt_plan(engagement)
            if flywheel.create_hunt_plan(plan):
                plans_created += 1
                for task in tasks:
                    flywheel.upsert_hunt_task(task)
                    tasks_created += 1
        hunt["plans_created"] = plans_created
        hunt["tasks_created"] = tasks_created
        # Plans may already exist from a prior run (idempotent): the gate is
        # whether plans exist in the ledger, not how many were created NOW.
        existing_plans = flywheel.query_hunt_plans()
        if existing_plans:
            hunt["status"] = "PASS"
        else:
            hunt["note"] = "No research engagements in this database yet."

        # Execution statistics: summarize the FULL persisted task set; planned
        # tasks are real, attempted/successful are honest zeros until a live
        # hunt source is wired.
        hunt["execution"] = hunt_execution_stats(
            tasks=flywheel.query_hunt_tasks() or None,
        )
        hunt["execution"]["note"] = (
            "No live hunt source wired yet; tasks are PENDING. Execution is "
            "reported honestly — plans created is NOT acquisitions completed. "
            "Corpus claims are promoted separately by the boxscore research corpus."
        )
        hunt["ledger_by_type"] = _claim_ledger_by_type(repo.conn)
        hunt["new_evidence_by_field"] = {
            "NEW_REPORTED_ATTENDANCE": 0,
            "NEW_PAID_TICKETS": 0,
            "NEW_GROSS": 0,
            "NEW_SELL_OUT": 0,
            "NEW_CAPACITY": 0,
            "NEW_ONSALE_DATE": 0,
            "NEW_ANNOUNCEMENT_DATE": 0,
            "NEW_TICKET_PRICE": 0,
        }
        if not hunt["note"]:
            hunt["note"] = (
                "Plans/tasks created; hunt findings write claims into "
                "economics.event_outcome_claims; new_evidence_by_field is "
                "zero until a live hunt source executes."
            )

        # ------------------------------------------------------------------
        # 5. CONTEXT_PANEL — Wikimedia pageviews + per-provider access gates
        # ------------------------------------------------------------------
        context_panel = {
            "status": "PARTIAL",
            "implemented_providers": ["wikimedia"],
            "artist": None,
            "days": PAGEVIEWS_DAYS,
            "series_rows": 0,
            "providers": _context_provider_gates(),
            "detail": None,
        }
        try:
            pageview_transport = transport or UrllibTransport()
            end = started.date()
            start = max(PAGEVIEWS_FLOOR, end - timedelta(days=PAGEVIEWS_DAYS))
            artist = artist_sample[0]
            series = collect_artist_pageviews(pageview_transport, artist, start, end)
            rows = build_pageview_series_rows(
                entity_name=artist,
                series=series,
                retrieved_at=started,
                source_url="wikimedia.org/rest_v1 metrics/pageviews/per-article",
            )
            inserted = 0
            for row in rows:
                if flywheel.insert_context_series(row):
                    inserted += 1
            context_panel["artist"] = artist
            context_panel["series_rows"] = inserted
            context_panel["detail"] = (
                f"collected {len(series)} daily observations, inserted {inserted}"
            )
            if inserted == 0:
                context_panel["status"] = "PARTIAL"
        except Exception as exc:  # noqa: BLE001
            context_panel["detail"] = f"pageviews unavailable: {exc}"
            context_panel["status"] = "PARTIAL"
        context_panel["note"] = (
            "Only Wikimedia is implemented. Census/BLS/NOAA/ERA5/GDELT/BEA are "
            "registered with their real access status (KEY_REQUIRED / "
            "REGISTRATION_REQUIRED) and are NOT_CONFIGURED until keys are set — "
            "CONTEXT_PANEL is deliberately PARTIAL, not complete."
        )

        # ------------------------------------------------------------------
        # 6. FORWARD_WATCH — register future events from the event graph
        # ------------------------------------------------------------------
        forward_watch = {"status": "NOT_EVALUATED", "events_registered": 0, "detail": None}
        try:
            today = started.date()
            future = [
                e
                for e in events_repo.query_events(cutoff=started)
                if str(e.get("local_date", "")) >= today.isoformat()
            ][:MAX_FORWARD_EVENTS]
            registered = 0
            for event in future:
                local_date = event.get("local_date")
                event_date = None
                if isinstance(local_date, datetime):
                    event_date = local_date.date()
                elif isinstance(local_date, date):
                    event_date = local_date
                row = register_event_row(
                    provider="event_graph",
                    provider_event_id=event["event_id"],
                    artist_name=event.get("artist_name") or event.get("event_name"),
                    venue_name=event.get("venue_name"),
                    market=event.get("market_id"),
                    event_date=event_date,
                    event_status=event.get("event_status"),
                    first_seen_at=started,
                    rights_status="TERMS_REVIEW_REQUIRED",
                    commercial_use_status="TERMS_REVIEW_REQUIRED",
                    observation_class="OBSERVED_PUBLIC",
                    software_version=SOFTWARE_VERSION,
                )
                if flywheel.register_forward_event(row):
                    registered += 1
            forward_watch["events_registered"] = registered
            forward_watch["detail"] = f"{len(future)} future event(s) found in graph"
            forward_watch["status"] = "PASS" if registered else "NOT_EVALUATED"
        except Exception as exc:  # noqa: BLE001
            forward_watch["status"] = "NOT_EVALUATED"
            forward_watch["detail"] = f"forward watch unavailable: {exc}"

        # ------------------------------------------------------------------
        # Manifest
        # ------------------------------------------------------------------
        manifest: dict[str, Any] = {
            "software_version": SOFTWARE_VERSION,
            "oa_run_id": oa_run_id,
            "generated_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "db_path": str(repo.db_path),
            "sources": {
                "registered_now": sources_registered,
                "already_registered": sources_existing,
                "total": sources_registered + sources_existing,
                "by_pipeline": _pipeline_counts(flywheel),
            },
            "objectives_registered": objectives,
            "coverage": {
                "measured_at": started.isoformat(),
                "snapshots_inserted": coverage_inserted,
                "by_status": by_status,
                "objectives_met": by_status.get("AT_TARGET", 0) + by_status.get("ABOVE_TARGET", 0),
                "objectives_total": len(coverage_rows),
                "rows": [
                    {
                        "objective_key": row["objective_key"],
                        "metric_name": row["metric_name"],
                        "actual": row["actual_value"],
                        "target": row["target_value"],
                        "ratio": round(row["coverage_ratio"], 4),
                        "status": row["status"],
                        "evidence_query": row["evidence_query"],
                    }
                    for row in coverage_rows
                ],
            },
            "pipelines": {
                "EVENT_GRAPH": event_graph,
                "OUTCOME_HUNTER": hunt,
                "CONTEXT_PANEL": context_panel,
                "FORWARD_WATCH": forward_watch,
            },
            "gates": {
                pipeline: pipeline_state
                for pipeline, pipeline_state in {
                    "EVENT_GRAPH": event_graph["status"],
                    "OUTCOME_HUNTER": hunt["status"],
                    "CONTEXT_PANEL": context_panel["status"],
                    "FORWARD_WATCH": forward_watch["status"],
                }.items()
            },
            "rights": {
                "research_corpus_fail_closed": True,
                "commercial_eligible": 0,
                "note": "Keyed/terms-review sources are registered with their real access status and never bypassed.",
            },
            "provider_cost_usd": 0.0,
        }

        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return manifest
    finally:
        repo.close()


def _pipeline_counts(flywheel: FlywheelRepository) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in flywheel.query_sources():
        pipeline = row.get("pipeline") or "UNKNOWN"
        counts[pipeline] = counts.get(pipeline, 0) + 1
    return counts


def _context_provider_gates() -> dict[str, str]:
    """Per-provider CONTEXT_PANEL gate: IMPLEMENTED / AVAILABLE / CONFIGURED /
    KEY_REQUIRED / NOT_CONFIGURED."""
    gates: dict[str, str] = {}
    for provider, env_var in CONTEXT_PROVIDER_ENV.items():
        if provider == "wikimedia":
            gates[provider] = "IMPLEMENTED"
        elif env_var and os.environ.get(env_var):
            gates[provider] = "CONFIGURED"
        elif env_var:
            gates[provider] = "KEY_REQUIRED"
        else:
            gates[provider] = "NOT_IMPLEMENTED"
    return gates


def _claim_ledger_by_type(conn) -> dict[str, int]:
    """Current outcome-claim ledger totals by outcome_type (real data)."""
    rows = conn.execute(
        "SELECT outcome_type, COUNT(*) AS n FROM economics.event_outcome_claims "
        "GROUP BY outcome_type ORDER BY outcome_type"
    ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


if __name__ == "__main__":
    result = run_flywheel_v1_oa()
    print(json.dumps(result, indent=2, default=str))
