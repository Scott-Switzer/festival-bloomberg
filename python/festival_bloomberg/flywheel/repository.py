"""Persist Data Flywheel tables with PIT-safe reads.

Follows the EconomicsRepository / ResearchRepository conventions: idempotent
inserts, append-only history, ``knowledge_time <= cutoff`` reads so nothing
learned after a decision cutoff is ever visible before it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..migrations import apply_pending_migrations


class FlywheelRepository:
    def __init__(self, connection) -> None:
        self.conn = connection
        apply_pending_migrations(connection)

    # -- source registry ------------------------------------------------------
    def insert_source(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT source_id FROM flywheel.source_registry WHERE source_id = ?",
            [row["source_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.source_registry
                (source_id, source_name, source_kind, pipeline, provider,
                 access_status, documented_quota, rights_status,
                 commercial_use_status, license, coverage_contribution, notes,
                 registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["source_id"], row["source_name"], row.get("source_kind"),
                row["pipeline"], row.get("provider"), row["access_status"],
                row.get("documented_quota"), row["rights_status"],
                row["commercial_use_status"], row.get("license"),
                row.get("coverage_contribution"), row.get("notes"),
                row["registered_at"], row.get("updated_at"),
            ],
        )
        self.conn.commit()
        return True

    def query_sources(self, *, pipeline: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.source_registry WHERE 1=1"
        params: list[Any] = []
        if pipeline:
            sql += " AND pipeline = ?"
            params.append(pipeline)
        sql += " ORDER BY source_id"
        return _rows(self.conn.execute(sql, params))

    # -- objectives -----------------------------------------------------------
    def upsert_objectives(self, rows: list[dict[str, Any]]) -> int:
        inserted = 0
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO flywheel.objectives
                    (objective_key, objective_version, metric_name,
                     metric_definition, medium_term_target, unit, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (objective_key) DO UPDATE SET
                    objective_version = excluded.objective_version,
                    metric_definition = excluded.metric_definition,
                    medium_term_target = excluded.medium_term_target,
                    unit = excluded.unit
                """,
                [
                    row["objective_key"], row["objective_version"],
                    row["metric_name"], row["metric_definition"],
                    row["medium_term_target"], row["unit"],
                    row.get("registered_at") or _utcnow(),
                ],
            )
            inserted += 1
        self.conn.commit()
        return inserted

    def query_objectives(self) -> list[dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM flywheel.objectives ORDER BY objective_key"
        ))

    # -- coverage snapshots ---------------------------------------------------
    def insert_coverage_snapshot(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT snapshot_id FROM flywheel.coverage_snapshots WHERE snapshot_id = ?",
            [row["snapshot_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.coverage_snapshots
                (snapshot_id, objective_version, measured_at, objective_key,
                 metric_name, actual_value, target_value, coverage_ratio, unit,
                 status, delta, evidence_query, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["snapshot_id"], row["objective_version"], row["measured_at"],
                row["objective_key"], row["metric_name"], row["actual_value"],
                row["target_value"], row["coverage_ratio"], row["unit"],
                row["status"], row["delta"], row.get("evidence_query"),
                row.get("notes"),
            ],
        )
        self.conn.commit()
        return True

    def query_coverage_snapshots(
        self, *, objective_key: str | None = None, cutoff: datetime | str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.coverage_snapshots WHERE 1=1"
        params: list[Any] = []
        if objective_key:
            sql += " AND objective_key = ?"
            params.append(objective_key)
        if cutoff is not None:
            sql += " AND measured_at <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        sql += " ORDER BY measured_at, objective_key"
        return _rows(self.conn.execute(sql, params))

    def latest_coverage(self) -> dict[str, dict[str, Any]]:
        """Latest snapshot per objective keyed by objective_key."""
        rows = self.query_coverage_snapshots()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row["objective_key"]
            if key not in latest or row["measured_at"] > latest[key]["measured_at"]:
                latest[key] = row
        return latest

    # -- event graph identities ----------------------------------------------
    def insert_graph_identity(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT identity_id FROM flywheel.event_graph_identities WHERE identity_id = ?",
            [row["identity_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.event_graph_identities
                (identity_id, entity_type, entity_key, entity_name, normalized_name,
                 musicbrainz_id, musicbrainz_name, musicbrainz_type, musicbrainz_country,
                 wikidata_id, ticketmaster_id, resolution_method, match_confidence,
                 source_provider, source_url, retrieved_at, knowledge_time, license,
                 rights_status, commercial_use_status, raw_payload_hash, parser_version,
                 software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["identity_id"], row["entity_type"], row.get("entity_key"),
                row["entity_name"], row["normalized_name"], row.get("musicbrainz_id"),
                row.get("musicbrainz_name"), row.get("musicbrainz_type"),
                row.get("musicbrainz_country"), row.get("wikidata_id"),
                row.get("ticketmaster_id"), row["resolution_method"],
                row.get("match_confidence"), row["source_provider"],
                row.get("source_url"), row["retrieved_at"], row["knowledge_time"],
                row.get("license"), row["rights_status"], row["commercial_use_status"],
                row.get("raw_payload_hash"), row.get("parser_version"),
                row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def query_graph_identities(
        self, *, entity_type: str | None = None, musicbrainz_id: str | None = None,
        cutoff: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.event_graph_identities WHERE 1=1"
        params: list[Any] = []
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        if musicbrainz_id:
            sql += " AND musicbrainz_id = ?"
            params.append(musicbrainz_id)
        if cutoff is not None:
            sql += " AND knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        sql += " ORDER BY entity_name, knowledge_time"
        return _rows(self.conn.execute(sql, params))

    # -- forward watch --------------------------------------------------------
    def register_forward_event(self, row: dict[str, Any]) -> bool:
        """Register a discovered future event. Idempotent per (provider, id)."""
        existing = self.conn.execute(
            "SELECT watch_event_id FROM flywheel.forward_watch_events "
            "WHERE provider = ? AND provider_event_id = ?",
            [row["provider"], row["provider_event_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.forward_watch_events
                (watch_event_id, provider, provider_event_id, artist_name, venue_name,
                 market, event_date, event_time, event_status, first_seen_at,
                 tracking_started_at, tracking_status, knowledge_time, source_url,
                 rights_status, commercial_use_status, observation_class, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["watch_event_id"], row["provider"], row["provider_event_id"],
                row.get("artist_name"), row.get("venue_name"), row.get("market"),
                row.get("event_date"), row.get("event_time"), row.get("event_status"),
                row["first_seen_at"], row["tracking_started_at"],
                row["tracking_status"], row["knowledge_time"], row.get("source_url"),
                row["rights_status"], row["commercial_use_status"],
                row["observation_class"], row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def insert_forward_observation(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT observation_id FROM flywheel.forward_watch_observations WHERE observation_id = ?",
            [row["observation_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.forward_watch_observations
                (observation_id, watch_event_id, observed_at, retrieved_at, knowledge_time,
                 milestone, event_status, price_min, price_max, currency, ticket_classes,
                 listing_count, secondary_lowest_price, secondary_median_price,
                 inventory_available, inventory_change_since_last, venue_configuration,
                 source_provider, source_url, raw_payload_hash, rights_status,
                 commercial_use_status, observation_class, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["observation_id"], row["watch_event_id"], row["observed_at"],
                row["retrieved_at"], row["knowledge_time"], row.get("milestone"),
                row.get("event_status"), row.get("price_min"), row.get("price_max"),
                row.get("currency"), _json(row.get("ticket_classes")),
                row.get("listing_count"), row.get("secondary_lowest_price"),
                row.get("secondary_median_price"), row.get("inventory_available"),
                row.get("inventory_change_since_last"), row.get("venue_configuration"),
                row.get("source_provider"), row.get("source_url"),
                row.get("raw_payload_hash"), row["rights_status"],
                row["commercial_use_status"], row["observation_class"],
                row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def query_forward_events(self, *, tracking_status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.forward_watch_events WHERE 1=1"
        params: list[Any] = []
        if tracking_status:
            sql += " AND tracking_status = ?"
            params.append(tracking_status)
        sql += " ORDER BY event_date NULLS LAST, watch_event_id"
        return _rows(self.conn.execute(sql, params))

    def find_forward_event(self, *, provider: str, provider_event_id: str) -> dict[str, Any] | None:
        rows = _rows(
            self.conn.execute(
                "SELECT * FROM flywheel.forward_watch_events "
                "WHERE provider = ? AND provider_event_id = ?",
                [provider, provider_event_id],
            )
        )
        return rows[0] if rows else None

    def reconcile_forward_event_artist(
        self, *, provider: str, provider_event_id: str, artist_name: str | None
    ) -> bool:
        """Correct performer evidence on an already-enrolled watch row.

        Legacy rows (enrolled before PR #21's artist-semantics fix) could carry
        an event NAME in ``artist_name`` because the old conversion fell back
        to ``main_performer or event.name``. Re-deriving the real performer
        from fresh discovery corrects those rows in place; artist_name becomes
        NULL when no performer relation exists. Returns True when a change was
        applied.
        """
        # DuckDB does not report UPDATE row counts (rowcount is always -1), so
        # the change is detected by reading the row before and after, never
        # from ``rowcount``.
        existing = self.conn.execute(
            "SELECT artist_name FROM flywheel.forward_watch_events "
            "WHERE provider = ? AND provider_event_id = ?",
            [provider, provider_event_id],
        ).fetchone()
        if not existing or (existing[0] or "") == (artist_name or ""):
            return False
        self.conn.execute(
            "UPDATE flywheel.forward_watch_events SET artist_name = ? "
            "WHERE provider = ? AND provider_event_id = ?",
            [artist_name, provider, provider_event_id],
        )
        self.conn.commit()
        return True

    def query_forward_observations(
        self, *, watch_event_id: str | None = None, cutoff: datetime | str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.forward_watch_observations WHERE 1=1"
        params: list[Any] = []
        if watch_event_id:
            sql += " AND watch_event_id = ?"
            params.append(watch_event_id)
        if cutoff is not None:
            sql += " AND knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        sql += " ORDER BY knowledge_time, observation_id"
        return _rows(self.conn.execute(sql, params))

    # -- outcome hunter -------------------------------------------------------
    def create_hunt_plan(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT plan_id FROM flywheel.outcome_hunt_plans WHERE plan_id = ?",
            [row["plan_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.outcome_hunt_plans
                (plan_id, canonical_event_id, artist_name, venue_name, market,
                 event_date, status, target_fields, created_at, knowledge_time,
                 software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["plan_id"], row["canonical_event_id"], row.get("artist_name"),
                row.get("venue_name"), row.get("market"), row.get("event_date"),
                row["status"], _json(row["target_fields"]), row["created_at"],
                row["knowledge_time"], row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def upsert_hunt_task(self, row: dict[str, Any]) -> bool:
        """Insert a hunt task, or update status/claim when it already exists."""
        existing = self.conn.execute(
            "SELECT task_id FROM flywheel.outcome_hunt_tasks WHERE plan_id = ? AND target_field = ?",
            [row["plan_id"], row["target_field"]],
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE flywheel.outcome_hunt_tasks
                SET status = COALESCE(?, status),
                    claim_id = COALESCE(?, claim_id),
                    source_provider = COALESCE(?, source_provider),
                    source_url = COALESCE(?, source_url),
                    retrieved_at = COALESCE(?, retrieved_at),
                    knowledge_time = COALESCE(?, knowledge_time),
                    notes = COALESCE(?, notes)
                WHERE plan_id = ? AND target_field = ?
                """,
                [
                    row.get("status"), row.get("claim_id"), row.get("source_provider"),
                    row.get("source_url"), row.get("retrieved_at"),
                    row.get("knowledge_time"), row.get("notes"),
                    row["plan_id"], row["target_field"],
                ],
            )
            self.conn.commit()
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.outcome_hunt_tasks
                (task_id, plan_id, target_field, outcome_type, status, claim_id,
                 source_provider, source_url, retrieved_at, knowledge_time, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["task_id"], row["plan_id"], row["target_field"],
                row.get("outcome_type"), row["status"], row.get("claim_id"),
                row.get("source_provider"), row.get("source_url"),
                row.get("retrieved_at"), row.get("knowledge_time"), row.get("notes"),
            ],
        )
        self.conn.commit()
        return True

    def query_hunt_plans(self) -> list[dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM flywheel.outcome_hunt_plans ORDER BY created_at, plan_id"
        ))

    def query_hunt_tasks(self, *, plan_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.outcome_hunt_tasks WHERE 1=1"
        params: list[Any] = []
        if plan_id:
            sql += " AND plan_id = ?"
            params.append(plan_id)
        sql += " ORDER BY plan_id, target_field"
        return _rows(self.conn.execute(sql, params))

    # -- PIT reconstruction evidence ------------------------------------------
    def insert_pit_evidence(self, row: dict[str, Any]) -> bool:
        """Insert one PIT reconstruction evidence row (idempotent per
        (event, evidence_class, source_document_id))."""
        existing = self.conn.execute(
            "SELECT evidence_id FROM flywheel.pit_reconstruction_evidence "
            "WHERE canonical_event_id = ? AND evidence_class = ? "
            "  AND COALESCE(source_document_id, '') = COALESCE(?, '')",
            [row["canonical_event_id"], row["evidence_class"], row.get("source_document_id")],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.pit_reconstruction_evidence
                (evidence_id, canonical_event_id, evidence_class,
                 source_publication_time, archive_capture_time,
                 source_period_start, source_period_end, source_url,
                 source_provider, source_document_id, rights_status,
                 commercial_use_status, knowledge_time, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["evidence_id"], row["canonical_event_id"], row["evidence_class"],
                row.get("source_publication_time"), row.get("archive_capture_time"),
                row.get("source_period_start"), row.get("source_period_end"),
                row.get("source_url"), row.get("source_provider"),
                row.get("source_document_id"), row["rights_status"],
                row["commercial_use_status"], row["knowledge_time"],
                row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def query_pit_evidence(self, *, canonical_event_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.pit_reconstruction_evidence WHERE 1=1"
        params: list[Any] = []
        if canonical_event_id:
            sql += " AND canonical_event_id = ?"
            params.append(canonical_event_id)
        sql += " ORDER BY canonical_event_id, evidence_class"
        return _rows(self.conn.execute(sql, params))

    # -- pre-event decision cutoffs (append-only) -----------------------------
    def insert_pre_event_cutoff(self, row: dict[str, Any]) -> bool:
        """Insert one decision-cutoff evidence row (append-only, idempotent
        per (event, cutoff_type, cutoff_kind, source_document_id))."""
        existing = self.conn.execute(
            "SELECT cutoff_id FROM flywheel.pre_event_cutoff_evidence "
            "WHERE canonical_event_id = ? AND cutoff_type = ? AND cutoff_kind = ? "
            "  AND COALESCE(source_document_id, '') = COALESCE(?, '')",
            [
                row["canonical_event_id"], row["cutoff_type"], row["cutoff_kind"],
                row.get("source_document_id"),
            ],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.pre_event_cutoff_evidence
                (cutoff_id, canonical_event_id, source_event_id, cutoff_type,
                 cutoff_kind, evidence_class, granularity, cutoff_timestamp,
                 lower_bound, upper_bound, bound_semantics, source_provider,
                 source_url, source_document_id, archive_capture_time,
                 retrieved_at, knowledge_time, rights_status,
                 commercial_use_status, confidence, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["cutoff_id"], row["canonical_event_id"], row.get("source_event_id"),
                row["cutoff_type"], row["cutoff_kind"], row["evidence_class"],
                row["granularity"], row.get("cutoff_timestamp"), row.get("lower_bound"),
                row.get("upper_bound"), row.get("bound_semantics"),
                row.get("source_provider"), row.get("source_url"),
                row.get("source_document_id"), row.get("archive_capture_time"),
                row.get("retrieved_at"), row["knowledge_time"], row["rights_status"],
                row["commercial_use_status"], row.get("confidence"),
                row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def query_pre_event_cutoffs(
        self, *, cutoff_type: str | None = None, canonical_event_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.pre_event_cutoff_evidence WHERE 1=1"
        params: list[Any] = []
        if cutoff_type:
            sql += " AND cutoff_type = ?"
            params.append(cutoff_type)
        if canonical_event_id:
            sql += " AND canonical_event_id = ?"
            params.append(canonical_event_id)
        sql += " ORDER BY canonical_event_id, cutoff_type, cutoff_kind"
        return _rows(self.conn.execute(sql, params))

    # -- outcome hunt attempts (append-only execution ledger) -----------------
    def insert_hunt_attempt(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT attempt_id FROM flywheel.outcome_hunt_attempts WHERE attempt_id = ?",
            [row["attempt_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.outcome_hunt_attempts
                (attempt_id, plan_id, task_id, target_field, provider, status,
                 started_at, finished_at, request_count, source_url,
                 capture_count, claim_id, detail, raw_payload_hash, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["attempt_id"], row["plan_id"], row["task_id"],
                row["target_field"], row["provider"], row["status"],
                row["started_at"], row.get("finished_at"), row.get("request_count", 0),
                row.get("source_url"), row.get("capture_count"), row.get("claim_id"),
                row.get("detail"), row.get("raw_payload_hash"),
                row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def query_hunt_attempts(
        self, *, status: str | None = None, provider: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.outcome_hunt_attempts WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        sql += " ORDER BY started_at, attempt_id"
        return _rows(self.conn.execute(sql, params))

    # -- provider acquisition runs + derived metrics --------------------------
    def insert_acquisition_run(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT run_id FROM flywheel.provider_acquisition_runs WHERE run_id = ?",
            [row["run_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.provider_acquisition_runs
                (run_id, provider, pipeline, started_at, finished_at, requests,
                 http_requests, request_count_status, successful_responses,
                 http_successful_responses, http_rate_limited, http_failures,
                 tasks_attempted, tasks_claim_found, tasks_not_found,
                 records_parsed, new_claims, new_unique_events_improved,
                 new_cutoffs, new_warm_start_events, new_forward_observations,
                 new_ticket_pace_events, duplicates, conflicts, not_found,
                 rights_blocked, rate_limited, parser_failed, http_failed,
                 auth_failed, other_failure, latency_ms_total, quota_consumed,
                 monetary_cost_usd, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["run_id"], row["provider"], row["pipeline"], row["started_at"],
                row.get("finished_at"), row.get("requests"), row.get("http_requests"),
                row.get("request_count_status"), row.get("successful_responses", 0),
                row.get("http_successful_responses", 0), row.get("http_rate_limited", 0),
                row.get("http_failures", 0), row.get("tasks_attempted", 0),
                row.get("tasks_claim_found", 0), row.get("tasks_not_found", 0),
                row.get("records_parsed", 0), row.get("new_claims", 0),
                row.get("new_unique_events_improved", 0), row.get("new_cutoffs", 0),
                row.get("new_warm_start_events", 0),
                row.get("new_forward_observations", 0),
                row.get("new_ticket_pace_events", 0), row.get("duplicates", 0),
                row.get("conflicts", 0), row.get("not_found", 0),
                row.get("rights_blocked", 0), row.get("rate_limited", 0),
                row.get("parser_failed", 0), row.get("http_failed", 0),
                row.get("auth_failed", 0), row.get("other_failure", 0),
                row.get("latency_ms_total", 0), row.get("quota_consumed", 0),
                row.get("monetary_cost_usd", 0.0), row.get("detail"),
            ],
        )
        self.conn.commit()
        return True

    def query_acquisition_runs(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.provider_acquisition_runs WHERE 1=1"
        params: list[Any] = []
        if provider:
            sql += " AND provider = ?"
            params.append(provider)
        sql += " ORDER BY started_at, run_id"
        return _rows(self.conn.execute(sql, params))

    def insert_acquisition_metrics(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT metric_id FROM flywheel.provider_acquisition_metrics WHERE metric_id = ?",
            [row["metric_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.provider_acquisition_metrics
                (metric_id, run_id, provider, successes_per_1000_requests,
                 http_success_rate, claims_per_1000_http_requests,
                 claims_per_1000_tasks_attempted,
                 new_events_per_1000_http_requests,
                 new_claims_per_1000_requests, new_cutoffs_per_1000_requests,
                 new_usable_events_per_1000_requests,
                 new_warm_starts_per_1000_requests, cost_per_new_claim,
                 cost_per_new_usable_event, cost_per_new_warm_start, knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["metric_id"], row["run_id"], row["provider"],
                row.get("successes_per_1000_requests"),
                row.get("http_success_rate"),
                row.get("claims_per_1000_http_requests"),
                row.get("claims_per_1000_tasks_attempted"),
                row.get("new_events_per_1000_http_requests"),
                row.get("new_claims_per_1000_requests"),
                row.get("new_cutoffs_per_1000_requests"),
                row.get("new_usable_events_per_1000_requests"),
                row.get("new_warm_starts_per_1000_requests"),
                row.get("cost_per_new_claim"),
                row.get("cost_per_new_usable_event"),
                row.get("cost_per_new_warm_start"), row["knowledge_time"],
            ],
        )
        self.conn.commit()
        return True

    def query_acquisition_metrics(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.provider_acquisition_metrics WHERE 1=1"
        params: list[Any] = []
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY run_id"
        return _rows(self.conn.execute(sql, params))

    # -- context panel --------------------------------------------------------
    def insert_context_series(self, row: dict[str, Any]) -> bool:
        existing = self.conn.execute(
            "SELECT series_id FROM flywheel.context_panel_series WHERE series_id = ?",
            [row["series_id"]],
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """
            INSERT INTO flywheel.context_panel_series
                (series_id, entity_type, entity_key, entity_name, series_type,
                 provider, observed_date, value, unit, metric_name, vintage,
                 source_publication_time, source_as_of, retrieved_at, knowledge_time,
                 source_url, raw_payload_hash, license, rights_status,
                 commercial_use_status, parser_version, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["series_id"], row["entity_type"], row.get("entity_key"),
                row["entity_name"], row["series_type"], row["provider"],
                row["observed_date"], row.get("value"), row.get("unit"),
                row.get("metric_name"), row.get("vintage"),
                row.get("source_publication_time"), row.get("source_as_of"),
                row["retrieved_at"], row["knowledge_time"], row.get("source_url"),
                row.get("raw_payload_hash"), row.get("license"),
                row["rights_status"], row["commercial_use_status"],
                row.get("parser_version"), row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def query_context_series(
        self, *, entity_name: str | None = None, series_type: str | None = None,
        cutoff: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM flywheel.context_panel_series WHERE 1=1"
        params: list[Any] = []
        if entity_name:
            sql += " AND entity_name = ?"
            params.append(entity_name)
        if series_type:
            sql += " AND series_type = ?"
            params.append(series_type)
        if cutoff is not None:
            sql += " AND knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        sql += " ORDER BY entity_name, observed_date"
        return _rows(self.conn.execute(sql, params))


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _utcnow() -> str:
    from ..acquisition.contracts import utc_now

    return utc_now().isoformat()


def _rows(result) -> list[dict[str, Any]]:
    description = result.description
    keys = [col[0] for col in description] if description else []
    return [dict(zip(keys, row)) for row in result.fetchall()]
