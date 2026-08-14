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
