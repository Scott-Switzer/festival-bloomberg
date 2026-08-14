"""Venue capacity as source-backed claims.

Never average conflicting numbers. A general maximum is an upper bound when
event configuration is unknown. Do not compute utilization without sourced
attendance and an event-compatible capacity claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..acquisition.contracts import content_hash_of
from ..events.reconcile import normalize_venue_name, strong_venue_match

MAX_PERSONS = "MAX_PERSONS"
CONCERT = "CONCERT"
SPORTS = "SPORTS"
SEATED = "SEATED"
STANDING = "STANDING"
GA = "GA"
UNKNOWN = "UNKNOWN"
UPPER_BOUND = "MAXIMUM_CAPACITY_UPPER_BOUND"


@dataclass
class CapacityClaim:
    claim_id: str
    canonical_venue_id: str
    capacity_value: float | None
    capacity_kind: str
    configuration_description: str | None
    effective_from: str | None
    effective_to: str | None
    provider: str
    source: str
    source_url: str | None
    source_publication_time: str | None
    retrieved_at: str
    knowledge_time: str
    source_observation_id: str | None
    claim_status: str
    wikidata_qid: str | None = None
    wikidata_rank: str | None = None
    wikidata_unit: str | None = None
    wikidata_qualifiers_json: str | None = None
    osm_type: str | None = None
    osm_id: str | None = None
    osm_tags_json: str | None = None
    usage_label: str | None = None

    def to_row(self) -> dict[str, Any]:
        return self.__dict__.copy()


def claim_from_wikidata(record: dict[str, Any], *, venue_id: str) -> CapacityClaim | None:
    value = record.get("capacity_value")
    if value is None:
        return None
    retrieved = str(record.get("retrieved_at") or record.get("knowledge_time") or "")
    payload_hash = content_hash_of(
        {"qid": record.get("wikidata_qid"), "value": value, "kind": record.get("capacity_kind")}
    )
    kind = record.get("capacity_kind") or MAX_PERSONS
    usage = UPPER_BOUND if kind == MAX_PERSONS else None
    return CapacityClaim(
        claim_id=f"cap_{payload_hash[:20]}",
        canonical_venue_id=venue_id,
        capacity_value=float(value),
        capacity_kind=kind,
        configuration_description=_qualifier_text(record.get("wikidata_qualifiers") or {}),
        effective_from=None,
        effective_to=None,
        provider="wikidata_official_api",
        source="wikidata_p1083",
        source_url=record.get("source_url") or record.get("canonical_url"),
        source_publication_time=None,
        retrieved_at=retrieved,
        knowledge_time=str(record.get("knowledge_time") or retrieved),
        source_observation_id=str(record.get("platform_object_id") or ""),
        claim_status="OBSERVED",
        wikidata_qid=record.get("wikidata_qid"),
        wikidata_rank=record.get("wikidata_rank"),
        wikidata_unit=record.get("wikidata_unit"),
        wikidata_qualifiers_json=_json(record.get("wikidata_qualifiers")),
        usage_label=usage,
    )


def claims_from_osm(record: dict[str, Any], *, venue_id: str) -> list[CapacityClaim]:
    retrieved = str(record.get("retrieved_at") or record.get("knowledge_time") or "")
    out: list[CapacityClaim] = []
    for item in record.get("capacity_claims") or []:
        value = item.get("capacity_value")
        if value is None:
            continue
        kind = item.get("capacity_kind") or UNKNOWN
        payload_hash = content_hash_of(
            {"osm": record.get("platform_object_id"), "tag": item.get("tag"), "value": value}
        )
        usage = UPPER_BOUND if kind in {MAX_PERSONS, UNKNOWN} else None
        out.append(
            CapacityClaim(
                claim_id=f"cap_{payload_hash[:20]}",
                canonical_venue_id=venue_id,
                capacity_value=float(value),
                capacity_kind=kind,
                configuration_description=item.get("tag"),
                effective_from=None,
                effective_to=None,
                provider="openstreetmap_overpass",
                source=f"osm:{item.get('tag')}",
                source_url=record.get("source_url") or record.get("canonical_url"),
                source_publication_time=None,
                retrieved_at=retrieved,
                knowledge_time=str(record.get("knowledge_time") or retrieved),
                source_observation_id=str(record.get("platform_object_id") or ""),
                claim_status="OBSERVED",
                osm_type=record.get("osm_type"),
                osm_id=str(record.get("osm_id") or ""),
                osm_tags_json=_json(record.get("capacity_tags")),
                usage_label=usage,
            )
        )
    return out


def mark_conflicts(claims: list[CapacityClaim]) -> list[CapacityClaim]:
    by_venue: dict[str, list[CapacityClaim]] = {}
    for claim in claims:
        by_venue.setdefault(claim.canonical_venue_id, []).append(claim)
    for group in by_venue.values():
        values = {(c.capacity_value, c.capacity_kind) for c in group if c.capacity_value is not None}
        if len(values) > 1:
            for claim in group:
                if claim.claim_status == "OBSERVED":
                    claim.claim_status = "CONFLICTING"
    return claims


def average_capacity(claims: list[CapacityClaim]) -> None:
    """Forbidden. Conflicting capacities must not be averaged."""
    raise RuntimeError("capacity claims must not be averaged")


def select_applicable_capacity(
    claims: list[CapacityClaim],
    *,
    event_configuration: str | None,
) -> dict[str, Any]:
    """Pick a claim for utilization only when configuration is compatible."""
    if not claims:
        return {"status": "UNKNOWN", "claim_id": None, "capacity_value": None, "usage_label": None}
    wanted = (event_configuration or "").upper()
    explicit = [c for c in claims if wanted and c.capacity_kind == wanted]
    if explicit:
        return {
            "status": "CONFIGURATION_COMPATIBLE",
            "claim_id": explicit[0].claim_id,
            "capacity_value": explicit[0].capacity_value,
            "usage_label": explicit[0].usage_label,
            "supporting_claim_ids": [c.claim_id for c in explicit],
        }
    maxima = [c for c in claims if c.capacity_kind == MAX_PERSONS]
    if maxima:
        return {
            "status": "UPPER_BOUND_ONLY",
            "claim_id": maxima[0].claim_id,
            "capacity_value": maxima[0].capacity_value,
            "usage_label": UPPER_BOUND,
            "supporting_claim_ids": [c.claim_id for c in maxima],
        }
    return {
        "status": "UNKNOWN",
        "claim_id": None,
        "capacity_value": None,
        "usage_label": None,
        "supporting_claim_ids": [c.claim_id for c in claims],
    }


def compute_utilization(
    *,
    attendance_value: float | None,
    applicable_capacity: dict[str, Any],
) -> dict[str, Any]:
    if attendance_value is None:
        return {"utilization": None, "status": "UNKNOWN", "reason": "attendance_missing"}
    if applicable_capacity.get("status") == "UPPER_BOUND_ONLY":
        return {
            "utilization": None,
            "status": "UNKNOWN",
            "reason": "capacity_is_upper_bound_not_event_capacity",
            "supporting_claim_ids": applicable_capacity.get("supporting_claim_ids") or [],
        }
    capacity = applicable_capacity.get("capacity_value")
    if not capacity or applicable_capacity.get("status") != "CONFIGURATION_COMPATIBLE":
        return {"utilization": None, "status": "UNKNOWN", "reason": "no_event_compatible_capacity"}
    return {
        "utilization": float(attendance_value) / float(capacity),
        "status": "COMPUTED",
        "supporting_claim_ids": applicable_capacity.get("supporting_claim_ids") or [],
    }


def resolve_wikidata_search(results: list[dict[str, Any]], *, venue_name: str) -> dict[str, Any]:
    target = normalize_venue_name(venue_name)
    exact = [r for r in results if normalize_venue_name(r.get("label")) == target]
    if len(exact) == 1:
        return {"status": "RESOLVED", "method": "EXACT_LABEL", "qid": exact[0].get("wikidata_qid"), "ambiguities": []}
    if len(exact) > 1:
        return {
            "status": "AMBIGUOUS",
            "method": "EXACT_LABEL",
            "qid": None,
            "ambiguities": [r.get("wikidata_qid") for r in exact],
        }
    strong = [r for r in results if strong_venue_match(r.get("label"), venue_name)]
    if len(strong) == 1:
        return {
            "status": "PARTIAL",
            "method": "STRONG_NAME_MATCH",
            "qid": strong[0].get("wikidata_qid"),
            "ambiguities": [],
        }
    if len(strong) > 1:
        return {
            "status": "AMBIGUOUS",
            "method": "STRONG_NAME_MATCH",
            "qid": None,
            "ambiguities": [r.get("wikidata_qid") for r in strong],
        }
    return {"status": "NO_CAPACITY_DATA", "method": "UNRESOLVED", "qid": None, "ambiguities": []}


def _qualifier_text(qualifiers: dict) -> str | None:
    if not qualifiers:
        return None
    parts = [f"{k}={','.join(v)}" for k, v in qualifiers.items()]
    return "; ".join(parts) if parts else None


def _json(value: Any) -> str | None:
    if value is None:
        return None
    import json

    if isinstance(value, str):
        return value
    return json.dumps(value)
