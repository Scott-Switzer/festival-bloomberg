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

OBSERVED = "OBSERVED"
CORROBORATED = "CORROBORATED"
CONFLICTING = "CONFLICTING"  # legacy status; new reconciliation never assigns it
SAME_CONFIGURATION_CONFLICT = "SAME_CONFIGURATION_CONFLICT"
CROSS_KIND_CONTRADICTION = "CROSS_KIND_CONTRADICTION"

# Claim statuses that must never be the basis of an automatic prefill.
BLOCKED_STATUSES = frozenset({CONFLICTING, SAME_CONFIGURATION_CONFLICT, CROSS_KIND_CONTRADICTION})

# Configurations the workbench may offer as usable capacity. SPORTS/GA/MAX
# are never prefilled: SPORTS subtypes are evidence, not workbench inputs.
PREFILL_KINDS = (CONCERT, SEATED, STANDING)


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
    # VCN v2 metadata: complete original source field value + parser identity.
    raw_value: str | None = None
    parser_version: str | None = None

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


def claim_from_wikipedia_infobox(record: dict[str, Any], *, venue_id: str) -> CapacityClaim | None:
    """Extract capacity claim from Wikipedia infobox data."""
    value = record.get("capacity_value")
    if value is None:
        return None
    retrieved = str(record.get("retrieved_at") or "")
    source_field = record.get("source_field")
    configuration = record.get("configuration_description")
    # Prefer the dedicated config description; fall back to the field name.
    description = configuration or source_field
    payload_hash = content_hash_of(
        {
            "page": record.get("page_title"),
            "field": source_field,
            "value": value,
            "raw": record.get("raw_value"),
        }
    )
    kind = record.get("capacity_kind") or UNKNOWN
    usage = UPPER_BOUND if kind in {MAX_PERSONS, UNKNOWN} else None
    return CapacityClaim(
        claim_id=f"cap_{payload_hash[:20]}",
        canonical_venue_id=venue_id,
        capacity_value=float(value),
        capacity_kind=kind,
        configuration_description=description,
        effective_from=None,
        effective_to=None,
        provider="wikipedia_mediawiki_api",
        source="wikipedia_infobox",
        source_url=record.get("source_url"),
        source_publication_time=record.get("source_revision_time"),
        retrieved_at=retrieved,
        knowledge_time=retrieved,
        source_observation_id=record.get("page_title"),
        claim_status="OBSERVED",
        wikidata_qid=record.get("wikidata_qid"),
        usage_label=usage,
        raw_value=record.get("raw_value"),
        parser_version=record.get("parser_version"),
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


def _assessment_key(claim: CapacityClaim) -> tuple:
    """Group key for comparing claims of the same configuration.

    SPORTS subtypes (basketball vs hockey) are distinct configurations and must
    never be collapsed into false conflicts merely because the broad kind is
    SPORTS. Every other kind is assessed at the kind level.
    """
    if claim.capacity_kind == SPORTS:
        subtype = (claim.configuration_description or "").strip().lower()
        return (SPORTS, subtype)
    return (claim.capacity_kind, None)


def mark_conflicts(claims: list[CapacityClaim]) -> list[CapacityClaim]:
    """Reconcile claims per venue without collapsing legitimate configurations.

    - Different explicit configurations never conflict merely because values
      differ (SEATED 17,500 + CONCERT 18,000 is not a conflict).
    - Same configuration with different values -> SAME_CONFIGURATION_CONFLICT.
    - Same configuration, same value from >=2 evidence rows -> CORROBORATED.
    - MAX_PERSONS remains upper-bound evidence only.
    - A configuration-specific value that exceeds a MAX_PERSONS claim ->
      CROSS_KIND_CONTRADICTION on both claims; automatic prefill is blocked.
    - SPORTS subtypes (basketball, hockey, boxing, ...) stay distinguishable.

    Raw claims are never deleted or overwritten; only claim_status mutates.
    """
    by_venue: dict[str, list[CapacityClaim]] = {}
    for claim in claims:
        by_venue.setdefault(claim.canonical_venue_id, []).append(claim)
    for group in by_venue.values():
        _reconcile_venue_group(group)
    return claims


def _reconcile_venue_group(group: list[CapacityClaim]) -> None:
    """Apply the semantic reconciliation rules to one venue's claims."""
    # 1. Same-configuration comparison (keyed by kind + SPORTS subtype).
    by_key: dict[tuple, list[CapacityClaim]] = {}
    for claim in group:
        by_key.setdefault(_assessment_key(claim), []).append(claim)
    for key_group in by_key.values():
        valued = [c for c in key_group if c.capacity_value is not None]
        distinct = {c.capacity_value for c in valued}
        if len(distinct) > 1:
            for claim in valued:
                claim.claim_status = SAME_CONFIGURATION_CONFLICT
        elif len(distinct) == 1 and len(valued) >= 2:
            for claim in valued:
                claim.claim_status = CORROBORATED

    # 2. Cross-kind contradiction: an explicit configuration value above a
    #    MAX_PERSONS claim contradicts the claimed maximum.
    maxima = [
        c for c in group
        if c.capacity_kind == MAX_PERSONS and c.capacity_value is not None
    ]
    if not maxima:
        return
    for claim in group:
        if claim.capacity_kind in (MAX_PERSONS, UNKNOWN) or claim.capacity_value is None:
            continue
        if any(m.capacity_value < claim.capacity_value for m in maxima):
            claim.claim_status = CROSS_KIND_CONTRADICTION
            for m in maxima:
                if m.capacity_value < claim.capacity_value:
                    m.claim_status = CROSS_KIND_CONTRADICTION


def assess_venue_claims(claims: list[CapacityClaim]) -> dict[str, Any]:
    """The single deterministic venue x configuration assessment contract.

    Used by production ``capacity_prefill``, acquisition acceptance/reporting
    and tests. Returns safe pairs, review-required pairs, same-configuration
    conflicts and cross-kind contradictions without averaging or overwriting
    any raw claim. A safe pair requires exactly one integral value for the
    configuration and no blocked statuses on its claims.
    """
    claims = list(claims)
    if not claims:
        return {
            "venue_id": None,
            "status": "UNKNOWN",
            "safe_pairs": [],
            "review_required_pairs": [],
            "same_configuration_conflicts": [],
            "cross_kind_contradictions": [],
            "upper_bound_only": False,
        }
    venue_id = claims[0].canonical_venue_id
    mark_conflicts(claims)

    by_key: dict[tuple, list[CapacityClaim]] = {}
    for claim in claims:
        by_key.setdefault(_assessment_key(claim), []).append(claim)

    same_configuration_conflicts: list[dict[str, Any]] = []
    for (kind, subtype), key_group in by_key.items():
        valued = [c for c in key_group if c.capacity_value is not None]
        distinct = {c.capacity_value for c in valued}
        if len(distinct) > 1:
            same_configuration_conflicts.append(
                {
                    "configuration": kind,
                    "subtype": subtype,
                    "values": sorted(distinct),
                    "claim_ids": [c.claim_id for c in valued],
                }
            )

    maxima = [
        c for c in claims
        if c.capacity_kind == MAX_PERSONS and c.capacity_value is not None
    ]
    cross_kind_contradictions: list[dict[str, Any]] = []
    for claim in claims:
        if claim.capacity_kind in (MAX_PERSONS, UNKNOWN) or claim.capacity_value is None:
            continue
        violated = [m for m in maxima if m.capacity_value < claim.capacity_value]
        if not violated:
            continue
        max_value = max(m.capacity_value for m in violated)
        cross_kind_contradictions.append(
            {
                "configuration": claim.capacity_kind,
                "value": claim.capacity_value,
                "contradicted_max_value": max_value,
                "claim_ids": [claim.claim_id]
                + [m.claim_id for m in violated if m.capacity_value == max_value],
            }
        )

    safe_pairs: list[dict[str, Any]] = []
    review_required_pairs: list[dict[str, Any]] = []
    for kind in PREFILL_KINDS:
        kind_claims = [
            c for c in claims
            if c.capacity_kind == kind and c.capacity_value is not None
        ]
        if not kind_claims:
            continue
        clean = [
            c for c in kind_claims
            if c.claim_status not in BLOCKED_STATUSES
            and float(c.capacity_value).is_integer()
        ]
        distinct = {c.capacity_value for c in clean}
        if len(distinct) == 1 and clean:
            safe_pairs.append(
                {
                    "configuration": kind,
                    "value": int(clean[0].capacity_value),
                    "supporting_claim_ids": [c.claim_id for c in clean],
                }
            )
        else:
            blocked = [c for c in kind_claims if c.claim_status in BLOCKED_STATUSES]
            reason = (
                "|".join(sorted({c.claim_status for c in blocked}))
                if blocked
                else "REVIEW_REQUIRED"
            )
            review_required_pairs.append(
                {
                    "configuration": kind,
                    "values": sorted({c.capacity_value for c in kind_claims}),
                    "claim_ids": [c.claim_id for c in kind_claims],
                    "reason": reason,
                }
            )

    if safe_pairs:
        status = "CONFIGURATION_COMPATIBLE"
    elif same_configuration_conflicts or cross_kind_contradictions or review_required_pairs:
        status = "REVIEW_REQUIRED"
    elif maxima:
        status = "UPPER_BOUND_ONLY"
    else:
        status = "UNKNOWN"

    return {
        "venue_id": venue_id,
        "status": status,
        "safe_pairs": safe_pairs,
        "review_required_pairs": review_required_pairs,
        "same_configuration_conflicts": same_configuration_conflicts,
        "cross_kind_contradictions": cross_kind_contradictions,
        "upper_bound_only": status == "UPPER_BOUND_ONLY",
    }


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
    explicit = [
        c for c in claims
        if wanted and c.capacity_kind == wanted
        and c.claim_status not in BLOCKED_STATUSES
    ]
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
