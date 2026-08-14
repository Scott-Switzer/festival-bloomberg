"""Historical Laboratory V1 — live operational acceptance.

Builds the first defensible historical outcome claim corpus from REAL public
evidence already in the warehouse (the OA10 artist × Chicago event graph is
derived from Setlist.fm, so every event is backed by a setlist observation)
plus free capacity enrichment (Wikidata / OSM / Wikipedia).

This driver deliberately does NOT fabricate attendance, tickets, gross, or
guarantee: those outcome types stay UNKNOWN (absent) and the coverage report
says so. The corpus is a source-backed EVENT ledger:

    EVENT_PERFORMED   <- setlist.fm setlist observation (real)
    VENUE_CAPACITY    <- Wikidata P1083 / OSM capacity (real, free HTTP)
    everything else   <- UNKNOWN / uncovered (honest)

Rights are per-source and fail closed: Setlist.fm is RESEARCH_ONLY,
Wikidata is OPEN_COMMERCIAL_OK (CC0), OSM is OPEN_WITH_ATTRIBUTION (ODbL),
unknowns stay UNKNOWN.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now
from ..economics import laboratory
from ..economics.enrichment import CapacityEnricher
from ..economics.outcome_claims import (
    EVENT_PERFORMED,
    GRADE_C_OTHER_PUBLIC,
    OBSERVED_PUBLIC,
    RIGHTS_OPEN_COMMERCIAL_OK,
    RIGHTS_OPEN_WITH_ATTRIBUTION,
    RIGHTS_RESEARCH_ONLY,
    RIGHTS_UNKNOWN,
    VENUE_CAPACITY,
    OutcomeClaim,
)
from ..economics.repository import EconomicsRepository
from ..events.repository import EventRepository
from ..warehouse.repository import FestivalRepository

DEFAULT_HISTORICAL_DB = (
    Path(__file__).resolve().parents[3] / "data" / "warehouse" / "artist_market_event_history.duckdb"
)

SETLISTFM_RIGHTS = RIGHTS_RESEARCH_ONLY
SETLISTFM_QUALITY = GRADE_C_OTHER_PUBLIC

CAPACITY_RIGHTS_BY_SOURCE = {
    "wikidata_p1083": RIGHTS_OPEN_COMMERCIAL_OK,
    "osm:capacity": RIGHTS_OPEN_WITH_ATTRIBUTION,
}
CAPACITY_PROVIDER_BY_SOURCE = {
    "wikidata_p1083": "wikidata_official_api",
    "osm:capacity": "openstreetmap_overpass",
}


def _stable_claim_id(event_id: str, outcome_type: str, source_name: str) -> str:
    return f"claim_{content_hash_of({'event': event_id, 'type': outcome_type, 'source': source_name})[:20]}"


class HistoricalLaboratoryOA:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def run(self, *, enrich_limit: int | None = None) -> dict[str, Any]:
        repo = FestivalRepository(self.db_path)
        try:
            events_repo = EventRepository(repo.conn)
            econ = EconomicsRepository(repo.conn)

            events = events_repo.query_events()
            venue_capacity = self._venue_capacity_map(econ, repo.conn)
            venue_alias = self._venue_alias_map(repo.conn)

            # Optional real capacity enrichment (free HTTP, idempotent).
            enrichment = {"venues_processed": 0, "claims_added": 0}
            if enrich_limit:
                enricher = CapacityEnricher(events_repo, econ)
                batch = enricher.enrich_chicago_venues(limit=enrich_limit)
                enrichment = {
                    "venues_processed": batch.get("venues_processed", 0),
                    "claims_added": batch.get("total_claims_added", 0),
                }
                venue_capacity = self._venue_capacity_map(econ, repo.conn)

            performed = 0
            capacity_linked = 0
            cutoffs = 0
            for event in events:
                event_id = event["event_id"]
                performed += self._emit_performed(econ, event)
                capacity_linked += self._emit_capacity(econ, event, venue_capacity, venue_alias)
                cutoffs += self._emit_cutoffs(econ, event)

            claims = econ.query_outcome_claims()
            manifest = {
                "software_version": "historical_laboratory_v1",
                "generated_at": utc_now().isoformat(),
                "events_discovered": len(events),
                "events_canonicalized": len({e["event_id"] for e in events}),
                "events_with_claims": len({c["canonical_event_id"] for c in claims}),
                "claims_total": len(claims),
                "claims_by_type": self._counter(claims, "outcome_type"),
                "claims_by_source": self._counter(claims, "source_provider"),
                "claims_by_grade": self._counter(claims, "source_quality"),
                "claims_by_rights": self._counter(claims, "rights_status"),
                "claims_conflicting": self._conflicting_count(claims),
                "claims_censored": sum(1 for c in claims if c.get("is_censored")),
                "performed_claims_emitted": performed,
                "capacity_claims_linked": capacity_linked,
                "decision_cutoffs_written": cutoffs,
                "capacity_enrichment": enrichment,
                "monid_usage": "NONE",
                "apify_usage": "NONE",
                "provider_cost_usd": 0.0,
            }

            # Reports (computed, not stored with values).
            manifest["data_quality"] = laboratory.data_quality_report(econ, events_repo)
            manifest["outcome_coverage"] = laboratory.outcome_coverage_report(econ, events_repo)
            manifest["pit_availability"] = laboratory.pit_availability_report(econ)
            manifest["selection_bias"] = laboratory.selection_bias_report(econ, events_repo)

            return manifest
        finally:
            repo.close()

    # -- emitters ----------------------------------------------------------- #
    def _emit_performed(self, econ: EconomicsRepository, event: dict[str, Any]) -> int:
        evidence_ids = event.get("supporting_observation_ids") or []
        evidence = evidence_ids[0] if evidence_ids else None
        source_url = event.get("event_name") if str(event.get("event_name") or "").startswith("http") else None
        claim = OutcomeClaim.build(
            claim_id=_stable_claim_id(event["event_id"], EVENT_PERFORMED, "setlistfm"),
            canonical_event_id=event["event_id"],
            outcome_type=EVENT_PERFORMED,
            value_text="performed",
            source_provider="setlistfm",
            source_name="setlist.fm setlist observation",
            source_url=source_url,
            source_document_id=str(evidence) if evidence else None,
            event_time=_iso(event.get("event_time")),
            retrieved_at=_iso(event.get("knowledge_time")) or utc_now().isoformat(),
            knowledge_time=_iso(event.get("knowledge_time")) or utc_now().isoformat(),
            evidence_observation_id=str(evidence) if evidence else None,
            source_quality=SETLISTFM_QUALITY,
            rights_status=SETLISTFM_RIGHTS,
            commercial_use_status=SETLISTFM_RIGHTS,
            observation_class=OBSERVED_PUBLIC,
            notes="setlist presence is evidence the event was performed, not attendance",
        )
        return 1 if econ.insert_outcome_claim(claim) else 0

    def _emit_capacity(
        self,
        econ: EconomicsRepository,
        event: dict[str, Any],
        venue_capacity: dict[str, list[dict[str, Any]]],
        venue_alias: dict[str, str],
    ) -> int:
        raw_venue_id = event.get("venue_id")
        venue_id = venue_alias.get(raw_venue_id, raw_venue_id)
        candidates = venue_capacity.get(venue_id) or []
        emitted = 0
        for cap in candidates:
            source = cap.get("source") or "unknown"
            rights = CAPACITY_RIGHTS_BY_SOURCE.get(source, RIGHTS_UNKNOWN)
            claim = OutcomeClaim.build(
                claim_id=_stable_claim_id(event["event_id"], VENUE_CAPACITY, source),
                canonical_event_id=event["event_id"],
                outcome_type=VENUE_CAPACITY,
                value_numeric=cap.get("capacity_value"),
                unit="persons",
                capacity_definition="venue maximum capacity (upper bound)",
                source_provider=CAPACITY_PROVIDER_BY_SOURCE.get(source, cap.get("provider")),
                source_name=source,
                source_url=cap.get("source_url"),
                source_document_id=str(cap.get("source_observation_id")) if cap.get("source_observation_id") else None,
                event_time=_iso(event.get("event_time")),
                retrieved_at=_iso(cap.get("knowledge_time")) or utc_now().isoformat(),
                knowledge_time=_iso(cap.get("knowledge_time")) or utc_now().isoformat(),
                source_quality=GRADE_C_OTHER_PUBLIC,
                rights_status=rights,
                commercial_use_status=rights,
                observation_class=OBSERVED_PUBLIC,
                notes="capacity is not attendance; venue capacity is an upper bound only",
            )
            if econ.insert_outcome_claim(claim):
                emitted += 1
        return emitted

    def _emit_cutoffs(self, econ: EconomicsRepository, event: dict[str, Any]) -> int:
        event_time = _iso(event.get("event_time"))
        if not event_time:
            return 0
        econ.upsert_decision_cutoffs(
            {
                "event_id": event["event_id"],
                "canonical_event_id": event["event_id"],
                "event_cutoff": event_time,
                "cutoff_notes": "booking/announcement/onsale cutoffs unknown for historical events",
            }
        )
        return 1

    # -- lookups ------------------------------------------------------------ #
    def _venue_alias_map(self, conn) -> dict[str, str]:
        rows = conn.execute(
            "SELECT venue_id, superseded_by FROM events.venues WHERE superseded_by IS NOT NULL"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def _venue_capacity_map(self, econ: EconomicsRepository, conn) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for cap in econ.query_capacity_claims():
            out[cap["canonical_venue_id"]].append(cap)
        return dict(out)

    def _counter(self, claims: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for c in claims:
            value = c.get(key)
            counts[str(value)] += 1
        return dict(sorted(counts.items()))

    def _conflicting_count(self, claims: list[dict[str, Any]]) -> int:
        groups: dict[str, set[str]] = defaultdict(set)
        for c in claims:
            gid = c.get("conflict_group_id")
            if gid:
                groups[gid].add(str(c.get("value_numeric")) + "|" + str(c.get("value_text")))
        return sum(1 for values in groups.values() if len(values) > 1)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value)
    return text or None


def run_historical_laboratory_oa(
    db_path: str | Path = DEFAULT_HISTORICAL_DB,
    *,
    enrich_limit: int | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the live OA and optionally write the gitignored JSON report."""
    oa = HistoricalLaboratoryOA(db_path)
    manifest = oa.run(enrich_limit=enrich_limit)
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import sys

    report = Path("reports/historical_laboratory_v1.json")
    result = run_historical_laboratory_oa(
        enrich_limit=int(sys.argv[1]) if len(sys.argv) > 1 else None,
        report_path=report,
    )
    print(json.dumps(result, indent=2, default=str))
