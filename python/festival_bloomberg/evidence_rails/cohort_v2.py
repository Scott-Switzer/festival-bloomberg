"""Freeze TICKET_MARKET_COHORT_V2 — versioned EVENT×MARKETPLACE panel.

Deterministic selection from Ticketmaster provider_event_snapshots.
Identity: artist + date + venue + city + EXACT_PROVIDER_ID (platform_object_id).
Never artist-only. Never demand scores.

Writes:
  - data/workspace/ticket_market_cohort_v2.json (immutable freeze artifact)
  - acquisition.ticket_market_cohort_* tables
  - acquisition.event_identifiers (canonical security master)
  - acquisition.watch_universe (compat for existing collector)
  - acquisition.ticket_market_pair_schedule
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb

from festival_bloomberg.migrations import apply_pending_migrations

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DRAFT = PROJECT_ROOT / "data" / "workspace" / "ticket_market_cohort_v2_draft.json"
DEFAULT_OUT = PROJECT_ROOT / "data" / "workspace" / "ticket_market_cohort_v2.json"
DEFAULT_DB = PROJECT_ROOT / "data" / "workspace" / "ticket_market" / "ticket_market.duckdb"

ACCEPTED = {"EXACT_PROVIDER_ID", "EXACT_PAGE_MATCH", "HIGH_CONFIDENCE"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def _marketplace_from_url(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {"ticketmaster.com", "ticketweb.com", "livenation.com"}:
        return host
    return None


def cohort_hash(pairs: list[dict[str, Any]]) -> str:
    lines = sorted(
        f"{p['event_key']}|{p['marketplace']}|{p.get('provider_event_id') or ''}|{p.get('marketplace_event_url') or ''}"
        for p in pairs
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def freeze_from_draft(
    draft_path: Path = DEFAULT_DRAFT,
    *,
    out_path: Path = DEFAULT_OUT,
    db_path: Path = DEFAULT_DB,
    force: bool = False,
) -> dict[str, Any]:
    if out_path.exists() and not force:
        existing = json.loads(out_path.read_text())
        return {"status": "ALREADY_FROZEN", "cohort_version": existing.get("cohort_version"), "path": str(out_path)}

    draft = json.loads(draft_path.read_text())
    pairs = draft["pairs"]
    events = draft["events"]
    version = draft["cohort_version"]
    ch = cohort_hash(pairs)
    commit = _git_commit()
    generated_at = _now()

    artifact = {
        **draft,
        "cohort_hash": ch,
        "code_commit": commit,
        "frozen_at": generated_at,
        "status": "FROZEN",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2, default=str))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    apply_pending_migrations(conn)
    _persist_cohort(conn, artifact)
    _persist_identifiers(conn, pairs)
    _persist_watch_universe(conn, version, events, generated_at, ch)
    _persist_schedule(conn, version, pairs)
    conn.close()

    return {
        "status": "FROZEN",
        "cohort_version": version,
        "cohort_hash": ch,
        "code_commit": commit,
        "n_events": artifact["n_events"],
        "n_pairs": artifact["n_pairs"],
        "marketplaces": artifact["marketplaces"],
        "lifecycle": artifact["lifecycle"],
        "path": str(out_path),
        "db": str(db_path),
    }


def _persist_cohort(conn: duckdb.DuckDBPyConnection, artifact: dict[str, Any]) -> None:
    version = artifact["cohort_version"]
    conn.execute("DELETE FROM acquisition.ticket_market_cohort_pairs WHERE cohort_version = ?", [version])
    conn.execute("DELETE FROM acquisition.ticket_market_cohort_versions WHERE cohort_version = ?", [version])
    conn.execute(
        """INSERT INTO acquisition.ticket_market_cohort_versions (
            cohort_version, generated_at, code_commit, selection_rules_json,
            cohort_hash, n_events, n_pairs, n_marketplaces, n_markets,
            lifecycle_json, marketplace_json, market_json, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            version,
            artifact.get("frozen_at") or artifact.get("generated_at"),
            artifact.get("code_commit"),
            json.dumps(artifact.get("selection_rules") or {}),
            artifact["cohort_hash"],
            artifact["n_events"],
            artifact["n_pairs"],
            len(artifact.get("marketplaces") or {}),
            len(artifact.get("cities") or {}),
            json.dumps(artifact.get("lifecycle") or {}),
            json.dumps(artifact.get("marketplaces") or {}),
            json.dumps(artifact.get("cities") or {}),
            "V2 longitudinal panel; EXACT_PROVIDER_ID only; no demand scoring",
        ],
    )
    for p in artifact["pairs"]:
        conn.execute(
            """INSERT INTO acquisition.ticket_market_cohort_pairs (
                cohort_version, event_key, marketplace, provider_event_id,
                marketplace_event_url, mapping_status, mapping_method, confidence,
                lifecycle_bucket, market_key, city, event_date, artist_name,
                venue_name, genre, rights_status, commercial_use_status, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                version,
                p["event_key"],
                p["marketplace"],
                p.get("provider_event_id"),
                p.get("marketplace_event_url"),
                p["mapping_status"],
                p.get("mapping_method"),
                p.get("confidence"),
                p.get("lifecycle_bucket"),
                p.get("market_key"),
                p.get("city"),
                p.get("event_date"),
                p.get("artist_name"),
                p.get("venue_name"),
                p.get("genre"),
                "TERMS_REVIEW_REQUIRED",
                "PROTOTYPE_ONLY",
                json.dumps({"source": "provider_event_snapshots", "method": p.get("mapping_method")}),
            ],
        )


def _persist_identifiers(conn: duckdb.DuckDBPyConnection, pairs: list[dict[str, Any]]) -> None:
    now = _now()
    for p in pairs:
        if p["mapping_status"] not in ACCEPTED:
            continue
        iid = "id::" + hashlib.sha256(f"{p['event_key']}|{p['marketplace']}".encode()).hexdigest()[:24]
        conn.execute(
            """INSERT INTO acquisition.event_identifiers (
                identifier_id, event_key, marketplace, marketplace_event_id,
                marketplace_event_url, mapping_status, mapping_method, confidence,
                first_resolved_at, last_verified_at, source_evidence,
                rights_status, commercial_use_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (event_key, marketplace) DO UPDATE SET
                marketplace_event_id = excluded.marketplace_event_id,
                marketplace_event_url = excluded.marketplace_event_url,
                mapping_status = excluded.mapping_status,
                mapping_method = excluded.mapping_method,
                confidence = excluded.confidence,
                last_verified_at = excluded.last_verified_at,
                source_evidence = excluded.source_evidence
            """,
            [
                iid,
                p["event_key"],
                p["marketplace"],
                p.get("provider_event_id"),
                p.get("marketplace_event_url"),
                p["mapping_status"],
                p.get("mapping_method"),
                p.get("confidence"),
                now,
                now,
                json.dumps({"cohort": "V2", "method": p.get("mapping_method")}),
                "TERMS_REVIEW_REQUIRED",
                "PROTOTYPE_ONLY",
            ],
        )
        # Legacy write-through for marketplace_event_mappings
        mid = "map::" + hashlib.sha256(f"{p['event_key']}|{p['marketplace']}".encode()).hexdigest()[:24]
        status_legacy = {
            "EXACT_PROVIDER_ID": "MATCHED_EXACT",
            "EXACT_PAGE_MATCH": "MATCHED_EXACT",
            "HIGH_CONFIDENCE": "MATCHED_HIGH_CONFIDENCE",
        }.get(p["mapping_status"], "AMBIGUOUS")
        try:
            conn.execute(
                """INSERT INTO acquisition.marketplace_event_mappings (
                    mapping_id, event_key, marketplace, marketplace_event_id,
                    marketplace_event_url, resolution_method, resolution_status,
                    resolution_confidence, resolved_at, last_verified_at,
                    rights_status, commercial_use_status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (mapping_id) DO NOTHING
                """,
                [
                    mid,
                    p["event_key"],
                    p["marketplace"],
                    p.get("provider_event_id"),
                    p.get("marketplace_event_url"),
                    p.get("mapping_method"),
                    status_legacy,
                    p.get("confidence"),
                    now,
                    now,
                    "TERMS_REVIEW_REQUIRED",
                    "PROTOTYPE_ONLY",
                    "write-through from event_identifiers (cohort V2)",
                ],
            )
        except Exception:  # noqa: BLE001 — legacy table may lack ON CONFLICT target
            pass


def _persist_watch_universe(
    conn: duckdb.DuckDBPyConnection,
    version: str,
    events: list[dict[str, Any]],
    frozen_at: str,
    content_hash: str,
) -> None:
    conn.execute("DELETE FROM acquisition.watch_universe WHERE watch_universe_version = ?", [version])
    for e in events:
        conn.execute(
            """INSERT INTO acquisition.watch_universe (
                watch_universe_version, event_key, provider_event_id, artist_key,
                artist_name, venue_key, venue_name, market_key, city, state,
                event_date, event_time, timezone, latitude, longitude,
                tm_price_min, tm_price_max, tm_currency, promoter, genre, subgenre,
                canonical_url, selection_reason, frozen_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                version,
                e["event_key"],
                e.get("provider_event_id"),
                None,
                e.get("artist_name"),
                f"venue::tm:{e['venue_id']}" if e.get("venue_id") else None,
                e.get("venue_name"),
                e.get("market_key"),
                e.get("city"),
                e.get("state"),
                e.get("event_date"),
                e.get("event_time"),
                e.get("timezone"),
                e.get("latitude"),
                e.get("longitude"),
                e.get("tm_price_min"),
                e.get("tm_price_max"),
                e.get("tm_currency"),
                e.get("promoter"),
                e.get("genre"),
                e.get("subgenre"),
                e.get("canonical_url"),
                f"cohort_v2:{e.get('lifecycle_bucket')}",
                frozen_at,
                content_hash,
            ],
        )


def _persist_schedule(conn: duckdb.DuckDBPyConnection, version: str, pairs: list[dict[str, Any]]) -> None:
    from festival_bloomberg.evidence_rails.cohort_cadence import cadence_for_bucket, next_due_after

    now = datetime.now(timezone.utc)
    for p in pairs:
        bucket = p.get("lifecycle_bucket") or ">120"
        cadence = cadence_for_bucket(bucket)
        due = next_due_after(now, cadence)
        conn.execute(
            """INSERT INTO acquisition.ticket_market_pair_schedule (
                event_key, marketplace, cohort_version, lifecycle_bucket,
                cadence_label, next_due_at, observation_count, consecutive_failures
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0)
            ON CONFLICT (event_key, marketplace) DO UPDATE SET
                cohort_version = excluded.cohort_version,
                lifecycle_bucket = excluded.lifecycle_bucket,
                cadence_label = excluded.cadence_label,
                next_due_at = CASE
                    WHEN acquisition.ticket_market_pair_schedule.next_due_at IS NULL
                    THEN excluded.next_due_at
                    ELSE acquisition.ticket_market_pair_schedule.next_due_at
                END
            """,
            [p["event_key"], p["marketplace"], version, bucket, cadence, due.isoformat()],
        )


def load_frozen_cohort(path: Path = DEFAULT_OUT) -> dict[str, Any]:
    return json.loads(path.read_text())
