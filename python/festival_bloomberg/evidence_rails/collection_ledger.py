"""Collection run ledger helpers for ticket-market longitudinal cohort."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def start_run(
    conn,
    *,
    run_id: str,
    cohort_version: str | None,
    rail: str,
    wave_label: str,
    budget_cap_usd: float,
    candidate_pairs: int,
    due_pairs: int,
    deployment_identity: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO acquisition.ticket_market_collection_runs (
            run_id, started_at, code_commit, deployment_identity, cohort_version,
            rail, wave_label, candidate_pairs, due_pairs, queued_pairs,
            attempted_pairs, succeeded_pairs, failed_pairs, retry_count,
            http_request_count, provider_call_count, bytes_downloaded,
            raw_evidence_objects, normalized_observations, spend_usd,
            budget_cap_usd, budget_remaining_usd, error_classes_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?, '{}', 'RUNNING')""",
        [
            run_id,
            _now(),
            _git_commit(),
            deployment_identity,
            cohort_version,
            rail,
            wave_label,
            candidate_pairs,
            due_pairs,
            budget_cap_usd,
            budget_cap_usd,
        ],
    )


def finish_run(conn, run_id: str, totals: dict[str, Any], *, status: str = "COMPLETE") -> None:
    spend = float(totals.get("cost_usd") or 0)
    cap = float(totals.get("budget_cap_usd") or 0)
    conn.execute(
        """UPDATE acquisition.ticket_market_collection_runs SET
            completed_at = ?,
            queued_pairs = ?,
            attempted_pairs = ?,
            succeeded_pairs = ?,
            failed_pairs = ?,
            retry_count = ?,
            http_request_count = ?,
            provider_call_count = ?,
            bytes_downloaded = ?,
            raw_evidence_objects = ?,
            normalized_observations = ?,
            spend_usd = ?,
            budget_remaining_usd = ?,
            error_classes_json = ?,
            status = ?,
            notes = ?
        WHERE run_id = ?""",
        [
            _now(),
            int(totals.get("queued_pairs") or totals.get("attempted") or 0),
            int(totals.get("attempted") or 0),
            int(totals.get("snapshots") or totals.get("succeeded") or 0),
            int(totals.get("failed") or len(totals.get("errors") or [])),
            int(totals.get("retry_count") or 0),
            int(totals.get("http_request_count") or totals.get("fetches") or 0),
            int(totals.get("provider_call_count") or totals.get("fetches") or 0),
            int(totals.get("bytes_downloaded") or 0),
            int(totals.get("raw_evidence_objects") or 0),
            int(totals.get("snapshots") or 0),
            spend,
            max(0.0, cap - spend) if cap else None,
            json.dumps(totals.get("error_classes") or {}),
            status,
            totals.get("notes"),
            run_id,
        ],
    )


def ingest_cloud_observation(conn, obs: dict[str, Any], *, wave_label: str) -> str | None:
    """Persist a cloud /test-fetch observation into ticket_market_snapshots.

    Price semantics: only set fields the observation actually carries.
    price_basis NONE means no comparable price — leave prices NULL.
    """
    from festival_bloomberg.evidence_rails.ticket_market import persist_snapshot

    snapshot = {
        "watch_universe_version": obs.get("watch_universe_version"),
        "event_key": obs.get("event_key"),
        "provider_event_id": obs.get("provider_event_id"),
        "source_platform": obs.get("source_platform") or obs.get("marketplace"),
        "actor_or_endpoint": obs.get("actor_or_endpoint"),
        "source_record_id": obs.get("source_record_id"),
        "wave_label": wave_label,
        "observed_at": obs.get("observed_at"),
        "retrieved_at": obs.get("retrieved_at"),
        "knowledge_time": obs.get("knowledge_time"),
        "currency": obs.get("currency"),
        "resale_min_price": obs.get("observed_offer_min_price") or obs.get("resale_min_price"),
        "all_in_price": obs.get("all_in_price"),
        "listing_count": obs.get("listing_count"),
        "ticket_count": obs.get("ticket_count"),
        "sold_out_flag": obs.get("sold_out_flag"),
        "availability_flag": obs.get("availability_flag"),
        "identity_match_status": "MATCHED",
        "identity_match_method": "EXACT_PROVIDER_ID",
        "identity_match_confidence": 1.0,
        "source_url": obs.get("source_url"),
        "raw_payload_hash": obs.get("raw_payload_hash") or obs.get("content_hash"),
        "rights_status": obs.get("rights_status") or "TERMS_REVIEW_REQUIRED",
        "commercial_use_status": obs.get("commercial_use_status") or "PROTOTYPE_ONLY",
        "parser_version": obs.get("parser_version") or "cloud_test_fetch_v1",
    }
    # Explicit price basis travel as notes via actor endpoint suffix — never invent prices.
    return persist_snapshot(conn, snapshot)
