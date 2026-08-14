"""Persist and query the public box-office research corpus.

Engagements live in ``research.boxoffice_engagements`` (append-only). Only
single-show, reported, non-estimated engagements may be promoted into the
outcome claim ledger — and only ever with RESEARCH_ONLY /
TERMS_REVIEW_REQUIRED rights, never commercial-eligible.
"""

from __future__ import annotations

import re
from typing import Any

from ..acquisition.contracts import content_hash_of
from ..economics.outcome_claims import (
    EXPLICIT_NOT_SOLD_OUT_ASSERTION,
    EXPLICIT_SOLD_OUT_ASSERTION,
    OBSERVED_PUBLIC,
    PAID_TICKETS,
    PRIMARY_FACE_VALUE_MAX,
    PRIMARY_FACE_VALUE_MIN,
    REPORTED_ATTENDANCE,
    TICKET_GROSS,
    OutcomeClaim,
)
from ..migrations import apply_pending_migrations
from .boxscore import (
    HEADCOUNT_PAID_TICKETS,
    HEADCOUNT_REPORTED_ATTENDANCE,
    BoxofficeEngagement,
)


class ResearchRepository:
    def __init__(self, connection) -> None:
        self.conn = connection
        apply_pending_migrations(connection)

    # -- engagements ---------------------------------------------------------
    def insert_engagement(self, engagement: BoxofficeEngagement) -> bool:
        """Insert an engagement. Returns False if engagement_id already exists."""
        existing = self.conn.execute(
            "SELECT engagement_id FROM research.boxoffice_engagements WHERE engagement_id = ?",
            [engagement.engagement_id],
        ).fetchone()
        if existing:
            return False
        row = engagement.to_row()
        self.conn.execute(
            """
            INSERT INTO research.boxoffice_engagements
                (engagement_id, rank, artist, venue, market, city, state, country,
                 promoter, start_date, end_date, dates_raw, number_of_shows,
                 headcount_total, headcount_definition, capacity_total,
                 sellable_capacity_per_show, reported_sellouts, ticket_gross_total,
                 currency, price_min, price_max, prices_raw, reporting_source,
                 source_url, source_publication_time, retrieved_at, rights_status,
                 commercial_use_status, observation_class, is_multi_show, is_reported,
                 is_estimated, raw_payload_hash, software_version, capacity_tier,
                 tour, headcount_source_label, sell_through_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["engagement_id"], row.get("rank"), row["artist"], row.get("venue"),
                row.get("market"), row.get("city"), row.get("state"), row.get("country"),
                row.get("promoter"), row.get("start_date"), row.get("end_date"), row.get("dates_raw"),
                row.get("number_of_shows"), row.get("headcount_total"), row["headcount_definition"],
                row.get("capacity_total"), row.get("sellable_capacity_per_show"),
                row.get("reported_sellouts"), row.get("ticket_gross_total"), row.get("currency"),
                row.get("price_min"), row.get("price_max"), row.get("prices_raw"),
                row["reporting_source"], row.get("source_url"), row.get("source_publication_time"),
                row["retrieved_at"], row["rights_status"], row["commercial_use_status"],
                row["observation_class"], row.get("is_multi_show"), row.get("is_reported"),
                row.get("is_estimated"), row.get("raw_payload_hash"), row.get("software_version"),
                row.get("capacity_tier"), row.get("tour"), row.get("headcount_source_label"),
                row.get("sell_through_pct"),
            ],
        )
        self.conn.commit()
        return True

    def query_engagements(
        self,
        *,
        reporting_source: str | None = None,
        is_reported: bool | None = None,
        is_multi_show: bool | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM research.boxoffice_engagements WHERE 1=1"
        params: list[Any] = []
        if reporting_source:
            sql += " AND reporting_source = ?"
            params.append(reporting_source)
        if is_reported is not None:
            sql += " AND is_reported = ?"
            params.append(is_reported)
        if is_multi_show is not None:
            sql += " AND is_multi_show = ?"
            params.append(is_multi_show)
        sql += " ORDER BY reporting_source, rank NULLS LAST, engagement_id"
        return _rows(self.conn.execute(sql, params))

    # -- promotion to the outcome claim ledger ------------------------------
    def promote_single_show_engagements(self, economics_repo) -> dict[str, Any]:
        """Promote single-show, reported, non-estimated engagements to claims.

        Multi-show engagements are NEVER divided. Estimated rows are NEVER
        promoted. Returns counts (never values)."""
        engagements = self.query_engagements()
        promoted = 0
        skipped_multi = 0
        skipped_estimated = 0
        claim_ids: list[str] = []
        for e in engagements:
            if e.get("is_estimated"):
                skipped_estimated += 1
                continue
            if not e.get("is_reported", True):
                skipped_estimated += 1
                continue
            if e.get("is_multi_show"):
                skipped_multi += 1
                continue
            event_id = self._event_key(e)
            claims = self._claims_for_engagement(e, event_id)
            for claim in claims:
                if economics_repo.insert_outcome_claim(claim):
                    promoted += 1
                    claim_ids.append(claim.claim_id)
        return {
            "engagements_total": len(engagements),
            "claims_promoted": promoted,
            "skipped_multi_show": skipped_multi,
            "skipped_estimated_or_unreported": skipped_estimated,
            "claim_ids": claim_ids,
        }

    @staticmethod
    def _event_key(e: dict[str, Any]) -> str:
        def slug(v: Any) -> str:
            return re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-") or "unknown"
        return f"boxoffice_{slug(e.get('artist'))}_{slug(e.get('venue'))}_{slug(e.get('start_date') or e.get('dates_raw'))}"

    def _claims_for_engagement(self, e: dict[str, Any], event_id: str) -> list[OutcomeClaim]:
        claims: list[OutcomeClaim] = []
        headcount = e.get("headcount_total")
        gross = e.get("ticket_gross_total")
        price_min = e.get("price_min")
        price_max = e.get("price_max")
        definition = e.get("headcount_definition")
        source_name = e.get("reporting_source")

        if headcount is not None:
            # Pollstar "Tickets Sold" is PAID tickets per Pollstar's own
            # reporting policy (comps/production kills excluded); never
            # relabel it into the broader TICKETS_SOLD category.
            outcome_type = PAID_TICKETS if definition == HEADCOUNT_PAID_TICKETS else REPORTED_ATTENDANCE
            claims.append(self._claim(e, event_id, outcome_type, headcount))
        if gross is not None:
            claims.append(self._claim(e, event_id, TICKET_GROSS, gross))
        if price_min is not None:
            claims.append(self._claim(e, event_id, PRIMARY_FACE_VALUE_MIN, price_min))
        if price_max is not None:
            claims.append(self._claim(e, event_id, PRIMARY_FACE_VALUE_MAX, price_max))
        if e.get("number_of_shows") == 1 and e.get("reported_sellouts") is not None:
            sold_out = e["reported_sellouts"] >= 1
            claims.append(self._claim(
                e, event_id,
                EXPLICIT_SOLD_OUT_ASSERTION if sold_out else EXPLICIT_NOT_SOLD_OUT_ASSERTION,
                None,
                value_text="sold out" if sold_out else "not sold out",
            ))
        return claims

    def _claim(
        self,
        e: dict[str, Any],
        event_id: str,
        outcome_type: str,
        value: float | None,
        *,
        value_text: str | None = None,
    ) -> OutcomeClaim:
        claim_id = f"claim_{content_hash_of({
            'source': e.get('reporting_source'),
            'event': event_id,
            'type': outcome_type,
            'value': value if value is not None else value_text,
        })[:20]}"
        return OutcomeClaim.build(
            claim_id=claim_id,
            canonical_event_id=event_id,
            outcome_type=outcome_type,
            value_numeric=value,
            value_text=value_text,
            currency="USD" if outcome_type in (TICKET_GROSS, PRIMARY_FACE_VALUE_MIN, PRIMARY_FACE_VALUE_MAX) else None,
            source_provider=e.get("reporting_source"),
            source_name=e.get("reporting_source"),
            source_url=e.get("source_url"),
            source_document_id=e.get("engagement_id"),
            event_time=e.get("start_date"),
            source_publication_time=e.get("source_publication_time"),
            source_quality="C_OTHER_PUBLIC_REPORT",
            observation_class=OBSERVED_PUBLIC,
            rights_status=e.get("rights_status"),
            commercial_use_status=e.get("commercial_use_status"),
            notes=f"promoted from research boxoffice engagement {e.get('engagement_id')}",
            software_version="public_boxscore_research_corpus_v1",
        )

    # -- V2: sources, canonical engagements, resolutions, splits, inventory ---
    def insert_source(self, row: dict[str, Any]) -> bool:
        if self.conn.execute(
            "SELECT source_id FROM research.boxoffice_sources WHERE source_id = ?",
            [row["source_id"]],
        ).fetchone():
            return False
        self.conn.execute(
            """
            INSERT INTO research.boxoffice_sources
                (source_id, reporting_source, source_url, publication_date, retrieved_at,
                 content_hash, record_count, selection_method, ranking_or_chart_status,
                 known_threshold, unknown_threshold, coverage_scope, rights_status,
                 commercial_use_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["source_id"], row["reporting_source"], row["source_url"], row.get("publication_date"),
                row["retrieved_at"], row.get("content_hash"), row.get("record_count"),
                row.get("selection_method"), row.get("ranking_or_chart_status"),
                row.get("known_threshold"), row.get("unknown_threshold"), row.get("coverage_scope"),
                row["rights_status"], row["commercial_use_status"],
            ],
        )
        self.conn.commit()
        return True

    def insert_canonical_engagement(self, row: dict[str, Any]) -> bool:
        if self.conn.execute(
            "SELECT canonical_engagement_id FROM research.canonical_boxoffice_engagements WHERE canonical_engagement_id = ?",
            [row["canonical_engagement_id"]],
        ).fetchone():
            return False
        self.conn.execute(
            """
            INSERT INTO research.canonical_boxoffice_engagements
                (canonical_engagement_id, artist, venue, market, city, state, country,
                 tour, start_date, end_date, number_of_shows, is_multi_show,
                 resolution_confidence, source_count, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["canonical_engagement_id"], row["artist"], row.get("venue"), row.get("market"),
                row.get("city"), row.get("state"), row.get("country"), row.get("tour"),
                row.get("start_date"), row.get("end_date"), row.get("number_of_shows"),
                row.get("is_multi_show"), row.get("resolution_confidence"), row.get("source_count"),
                row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def insert_resolution(self, row: dict[str, Any]) -> bool:
        if self.conn.execute(
            "SELECT resolution_id FROM research.boxoffice_engagement_resolutions WHERE resolution_id = ?",
            [row["resolution_id"]],
        ).fetchone():
            return False
        self.conn.execute(
            """
            INSERT INTO research.boxoffice_engagement_resolutions
                (resolution_id, raw_engagement_id, canonical_engagement_id,
                 resolution_status, match_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                row["resolution_id"], row["raw_engagement_id"], row["canonical_engagement_id"],
                row["resolution_status"], row.get("match_key"), row["created_at"],
            ],
        )
        self.conn.commit()
        return True

    def insert_split(self, row: dict[str, Any]) -> bool:
        if self.conn.execute(
            "SELECT split_id FROM research.research_splits WHERE split_id = ?",
            [row["split_id"]],
        ).fetchone():
            return False
        self.conn.execute(
            """
            INSERT INTO research.research_splits
                (split_id, split_type, canonical_engagement_id, fold, group_key,
                 created_at, seed, deterministic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["split_id"], row["split_type"], row["canonical_engagement_id"], row["fold"],
                row.get("group_key"), row["created_at"], row.get("seed"), row.get("deterministic"),
            ],
        )
        self.conn.commit()
        return True

    def insert_inventory_snapshot(self, row: dict[str, Any]) -> bool:
        if self.conn.execute(
            "SELECT snapshot_id FROM research.forward_ticket_inventory_snapshots WHERE snapshot_id = ?",
            [row["snapshot_id"]],
        ).fetchone():
            return False
        self.conn.execute(
            """
            INSERT INTO research.forward_ticket_inventory_snapshots
                (snapshot_id, event_external_id, artist, venue, market, event_date,
                 general_sale_date, snapshot_time, retrieved_at, estimated_capacity,
                 tickets_available, tickets_distributed_or_sold_as_reported,
                 source_methodology, classification, source_url, source_publication_time,
                 rights_status, commercial_use_status, observation_class, software_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["snapshot_id"], row.get("event_external_id"), row.get("artist"), row.get("venue"),
                row.get("market"), row.get("event_date"), row.get("general_sale_date"),
                row.get("snapshot_time"), row["retrieved_at"], row.get("estimated_capacity"),
                row.get("tickets_available"), row.get("tickets_distributed_or_sold_as_reported"),
                row.get("source_methodology"), row["classification"], row.get("source_url"),
                row.get("source_publication_time"), row["rights_status"], row["commercial_use_status"],
                row["observation_class"], row.get("software_version"),
            ],
        )
        self.conn.commit()
        return True

    def query_sources(self) -> list[dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM research.boxoffice_sources ORDER BY publication_date NULLS LAST, source_id"
        ))

    def query_canonical_engagements(self) -> list[dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM research.canonical_boxoffice_engagements ORDER BY artist, start_date NULLS LAST, canonical_engagement_id"
        ))

    def query_resolutions(self) -> list[dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM research.boxoffice_engagement_resolutions ORDER BY raw_engagement_id"
        ))

    def query_splits(self, split_type: str | None = None) -> list[dict[str, Any]]:
        if split_type:
            return _rows(self.conn.execute(
                "SELECT * FROM research.research_splits WHERE split_type = ? ORDER BY canonical_engagement_id",
                [split_type],
            ))
        return _rows(self.conn.execute(
            "SELECT * FROM research.research_splits ORDER BY split_type, canonical_engagement_id"
        ))

    def query_inventory_snapshots(self) -> list[dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM research.forward_ticket_inventory_snapshots ORDER BY snapshot_time NULLS LAST, snapshot_id"
        ))


def _rows(result) -> list[dict[str, Any]]:
    description = result.description
    keys = [col[0] for col in description] if description else []
    return [dict(zip(keys, row)) for row in result.fetchall()]
