"""Persist append-only economics observations with PIT-safe reads."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..migrations import apply_pending_migrations
from .capacity import CapacityClaim
from .outcomes import EventOutcome
from .snapshots import PrimaryTicketSnapshot, SecondaryTicketSnapshot


class EconomicsRepository:
    def __init__(self, connection) -> None:
        self.conn = connection
        apply_pending_migrations(connection)

    def insert_capacity_claim(self, claim: CapacityClaim) -> bool:
        existing = self.conn.execute(
            "SELECT claim_id FROM economics.venue_capacity_claims WHERE claim_id = ?",
            [claim.claim_id],
        ).fetchone()
        if existing:
            return False
        row = claim.to_row()
        self.conn.execute(
            """
            INSERT INTO economics.venue_capacity_claims
                (claim_id, canonical_venue_id, capacity_value, capacity_kind,
                 configuration_description, effective_from, effective_to, provider, source,
                 source_url, source_publication_time, retrieved_at, knowledge_time,
                 source_observation_id, claim_status, wikidata_qid, wikidata_rank,
                 wikidata_unit, wikidata_qualifiers_json, osm_type, osm_id, osm_tags_json,
                 usage_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["claim_id"],
                row["canonical_venue_id"],
                row["capacity_value"],
                row["capacity_kind"],
                row["configuration_description"],
                row["effective_from"],
                row["effective_to"],
                row["provider"],
                row["source"],
                row["source_url"],
                row["source_publication_time"],
                row["retrieved_at"],
                row["knowledge_time"],
                row["source_observation_id"],
                row["claim_status"],
                row["wikidata_qid"],
                row["wikidata_rank"],
                row["wikidata_unit"],
                row["wikidata_qualifiers_json"],
                row["osm_type"],
                row["osm_id"],
                row["osm_tags_json"],
                row["usage_label"],
            ],
        )
        self.conn.commit()
        return True

    def insert_primary_snapshot(self, snapshot: PrimaryTicketSnapshot) -> bool:
        if self._duplicate_snapshot("economics.primary_ticket_snapshots", snapshot.provider, snapshot.provider_event_id, snapshot.snapshot_bucket, snapshot.price_type):
            return False
        row = snapshot.to_row()
        self.conn.execute(
            """
            INSERT INTO economics.primary_ticket_snapshots
                (snapshot_id, canonical_event_id, provider, provider_event_id, retrieved_at,
                 knowledge_time, snapshot_bucket, currency, price_type, minimum_price,
                 maximum_price, fees_included, event_status, public_onsale_start,
                 public_onsale_end, source_url, raw_observation_id, raw_payload_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["snapshot_id"],
                row["canonical_event_id"],
                row["provider"],
                row["provider_event_id"],
                row["retrieved_at"],
                row["knowledge_time"],
                row["snapshot_bucket"],
                row["currency"],
                row["price_type"],
                row["minimum_price"],
                row["maximum_price"],
                row["fees_included"],
                row["event_status"],
                row["public_onsale_start"],
                row["public_onsale_end"],
                row["source_url"],
                row["raw_observation_id"],
                row["raw_payload_hash"],
            ],
        )
        self.conn.commit()
        return True

    def insert_secondary_snapshot(self, snapshot: SecondaryTicketSnapshot) -> bool:
        if self._duplicate_snapshot("economics.secondary_ticket_snapshots", snapshot.provider, snapshot.provider_event_id, snapshot.snapshot_bucket, None):
            return False
        row = snapshot.to_row()
        self.conn.execute(
            """
            INSERT INTO economics.secondary_ticket_snapshots
                (snapshot_id, canonical_event_id, provider, provider_event_id, retrieved_at,
                 knowledge_time, snapshot_bucket, currency, listing_count, lowest_price,
                 average_price, highest_price, median_price, provider_score, source_url,
                 raw_observation_id, raw_payload_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["snapshot_id"],
                row["canonical_event_id"],
                row["provider"],
                row["provider_event_id"],
                row["retrieved_at"],
                row["knowledge_time"],
                row["snapshot_bucket"],
                row["currency"],
                row["listing_count"],
                row["lowest_price"],
                row["average_price"],
                row["highest_price"],
                row["median_price"],
                row["provider_score"],
                row["source_url"],
                row["raw_observation_id"],
                row["raw_payload_hash"],
            ],
        )
        self.conn.commit()
        return True

    def insert_outcome(self, outcome: EventOutcome) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO economics.event_outcome_observations
                (outcome_id, canonical_event_id, event_status, performance_recorded_by_setlistfm,
                 sold_out_status, attendance_value, attendance_source, attendance_context,
                 capacity_utilization, utilization_status, supporting_claim_ids,
                 supporting_observation_ids, retrieved_at, knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                outcome.outcome_id,
                outcome.canonical_event_id,
                outcome.event_status,
                outcome.performance_recorded_by_setlistfm,
                outcome.sold_out_status,
                outcome.attendance_value,
                outcome.attendance_source,
                outcome.attendance_context,
                outcome.capacity_utilization,
                outcome.utilization_status,
                json.dumps(outcome.supporting_claim_ids),
                json.dumps(outcome.supporting_observation_ids),
                outcome.retrieved_at,
                outcome.knowledge_time,
            ],
        )
        self.conn.commit()

    def insert_comparison(self, comparison: dict[str, Any], *, retrieved_at: str, knowledge_time: str) -> None:
        self.conn.execute(
            """
            INSERT INTO economics.price_comparisons
                (comparison_id, canonical_event_id, concept, status, primary_snapshot_id,
                 secondary_snapshot_id, timestamp_delta_seconds, currency_consistency,
                 fee_comparability, class_comparability, fx_conversion, retrieved_at,
                 knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                comparison.get("comparison_id") or f"cmp_{comparison.get('primary_snapshot_id')}_{comparison.get('secondary_snapshot_id')}",
                comparison.get("canonical_event_id"),
                comparison.get("concept"),
                comparison.get("status"),
                comparison.get("primary_snapshot_id"),
                comparison.get("secondary_snapshot_id"),
                comparison.get("timestamp_delta_seconds"),
                comparison.get("currency_consistency"),
                comparison.get("fee_comparability"),
                comparison.get("class_comparability"),
                comparison.get("fx_conversion"),
                retrieved_at,
                knowledge_time,
            ],
        )
        self.conn.commit()

    def upsert_venue_mapping(self, mapping: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO economics.venue_source_ids
                (mapping_id, canonical_venue_id, venue_name, wikidata_qid, osm_type, osm_id,
                 ticketmaster_venue_id, setlistfm_venue_id, seatgeek_venue_id,
                 resolution_status, resolution_method, ambiguities_json, knowledge_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                mapping["mapping_id"],
                mapping["canonical_venue_id"],
                mapping.get("venue_name"),
                mapping.get("wikidata_qid"),
                mapping.get("osm_type"),
                mapping.get("osm_id"),
                mapping.get("ticketmaster_venue_id"),
                mapping.get("setlistfm_venue_id"),
                mapping.get("seatgeek_venue_id"),
                mapping["resolution_status"],
                mapping.get("resolution_method"),
                json.dumps(mapping.get("ambiguities") or []),
                mapping["knowledge_time"],
            ],
        )
        self.conn.commit()

    def query_capacity_claims(self, *, venue_id: str | None = None, cutoff: datetime | str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM economics.venue_capacity_claims WHERE 1=1"
        params: list[Any] = []
        if venue_id:
            sql += " AND canonical_venue_id = ?"
            params.append(venue_id)
        if cutoff is not None:
            sql += " AND knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        sql += " ORDER BY knowledge_time, claim_id"
        return _rows(self.conn.execute(sql, params), self.conn)

    def query_primary_snapshots(self, *, event_id: str | None = None, cutoff: datetime | str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM economics.primary_ticket_snapshots WHERE 1=1"
        params: list[Any] = []
        if event_id:
            sql += " AND canonical_event_id = ?"
            params.append(event_id)
        if cutoff is not None:
            sql += " AND knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        sql += " ORDER BY knowledge_time, snapshot_id"
        return _rows(self.conn.execute(sql, params), self.conn)

    def query_secondary_snapshots(self, *, event_id: str | None = None, cutoff: datetime | str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM economics.secondary_ticket_snapshots WHERE 1=1"
        params: list[Any] = []
        if event_id:
            sql += " AND canonical_event_id = ?"
            params.append(event_id)
        if cutoff is not None:
            sql += " AND knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        sql += " ORDER BY knowledge_time, snapshot_id"
        return _rows(self.conn.execute(sql, params), self.conn)

    def query_outcomes(self, *, event_id: str | None = None, cutoff: datetime | str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM economics.event_outcome_observations WHERE 1=1"
        params: list[Any] = []
        if event_id:
            sql += " AND canonical_event_id = ?"
            params.append(event_id)
        if cutoff is not None:
            sql += " AND knowledge_time <= ?"
            params.append(cutoff.isoformat() if isinstance(cutoff, datetime) else str(cutoff))
        return _rows(self.conn.execute(sql, params), self.conn)

    def _duplicate_snapshot(self, table: str, provider: str, provider_event_id: str | None, bucket: str, price_type: str | None) -> bool:
        if price_type is None:
            row = self.conn.execute(
                f"SELECT snapshot_id FROM {table} WHERE provider = ? AND provider_event_id = ? AND snapshot_bucket = ?",
                [provider, provider_event_id, bucket],
            ).fetchone()
        else:
            row = self.conn.execute(
                f"SELECT snapshot_id FROM {table} WHERE provider = ? AND provider_event_id = ? AND snapshot_bucket = ? AND coalesce(price_type,'') = coalesce(?, '')",
                [provider, provider_event_id, bucket, price_type],
            ).fetchone()
        return row is not None


def _rows(result, conn) -> list[dict[str, Any]]:
    description = result.description
    keys = [col[0] for col in description] if description else []
    return [dict(zip(keys, row)) for row in result.fetchall()]
