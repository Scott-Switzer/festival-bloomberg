"""Canonical feature registry for DENSE_PRE_EVENT_DATA_PANEL_V1.

No feature enters Comparable V2 without registry admission. Every potential
feature declares its semantic definition, knowledge_time rule, rights status,
PIT admissibility, and current coverage. Admission is deterministic.

Statuses:
  CANDIDATE   declared but not yet admitted
  ADMITTED    passes all gates (semantics + PIT + rights + coverage plan)
  REJECTED_LEAKAGE   uses outcome fields (features.LEAKAGE_BLACKLIST)
  REJECTED_RIGHTS    commercial-use blocked
  REJECTED_COVERAGE  minimum coverage requirement not met
  REJECTED_SEMANTICS undefined event-time meaning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .features import LEAKAGE_BLACKLIST

STATUS_CANDIDATE = "CANDIDATE"
STATUS_ADMITTED = "ADMITTED"
STATUS_REJECTED_LEAKAGE = "REJECTED_LEAKAGE"
STATUS_REJECTED_RIGHTS = "REJECTED_RIGHTS"
STATUS_REJECTED_COVERAGE = "REJECTED_COVERAGE"
STATUS_REJECTED_SEMANTICS = "REJECTED_SEMANTICS"


@dataclass
class FeatureSpec:
    name: str
    semantic_definition: str
    entity_type: str          # EVENT | ARTIST | VENUE | MARKET | FESTIVAL
    value_type: str           # numeric | categorical | ratio | boolean
    event_time_meaning: str   # what the value refers to at event time
    knowledge_time_rule: str  # how the value was knowable at cutoff
    source: str
    rights_status: str        # OPEN_COMMERCIAL_OK | OPEN_ATTRIBUTION_REQUIRED | RESEARCH_ONLY | TERMS_REVIEW_REQUIRED
    commercial_use_status: str
    derivation_version: str
    minimum_coverage: float   # required fraction of the target population
    pit_admissible: bool = False
    leakage_fields: tuple[str, ...] = ()
    target_families: tuple[str, ...] = ()   # REPORTED_ATTENDANCE|PAID_TICKETS|TICKET_GROSS|SELL_OUT|ALL
    current_coverage: float = 0.0           # measured, filled by coverage probe
    status: str = STATUS_CANDIDATE
    note: str | None = None


REGISTRY: list[FeatureSpec] = [
    FeatureSpec(
        name="venue_capacity_band",
        semantic_definition="Claimed venue capacity bucketed into a band (e.g. 0-5k, 5k-15k, 15k-40k, 40k+)",
        entity_type="VENUE", value_type="categorical",
        event_time_meaning="Capacity of the venue hosting the event",
        knowledge_time_rule="Claim must be knowable at booking time; conflicts preserved, latest claim wins only if its effective_from <= cutoff",
        source="wikidata+osm+official", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="venue_intel_v1",
        minimum_coverage=0.6, pit_admissible=True,
        target_families=("ALL",),
    ),
    FeatureSpec(
        name="venue_indoor_outdoor",
        semantic_definition="Whether the venue is indoors or outdoors (evidence-backed)",
        entity_type="VENUE", value_type="categorical",
        event_time_meaning="Venue configuration at event time",
        knowledge_time_rule="From venue master; must not postdate the cutoff",
        source="venue master", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="venue_intel_v1",
        minimum_coverage=0.5, pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="venue_coordinates",
        semantic_definition="Lat/lon of the venue with source and derivation version",
        entity_type="VENUE", value_type="numeric",
        event_time_meaning="Venue location (static)",
        knowledge_time_rule="From venue master; geography is not time-varying for a built venue",
        source="venue master", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="venue_intel_v1",
        minimum_coverage=0.7, pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="artist_attention_wikimedia_30d_at_cutoff",
        semantic_definition="Wikimedia pageviews for the artist in the 30 days ending before the cutoff",
        entity_type="ARTIST", value_type="numeric",
        event_time_meaning="Attention in the month before the event",
        knowledge_time_rule="Only pageviews with day < cutoff; daily values are final once the day has passed",
        source="wikimedia", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="historical_attention_v1",
        minimum_coverage=0.4, pit_admissible=True,
        target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="artist_attention_wikimedia_growth_90d",
        semantic_definition="(30d window ending at cutoff) / (30d window ending 90d before cutoff) - 1",
        entity_type="ARTIST", value_type="ratio",
        event_time_meaning="Attention momentum before the event",
        knowledge_time_rule="Both windows end before cutoff",
        source="wikimedia", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="historical_attention_v1",
        minimum_coverage=0.3, pit_admissible=True,
        target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="artist_attention_listenbrainz_30d_at_cutoff",
        semantic_definition="ListenBrainz listens in the 30 days ending before cutoff (listened_at < cutoff AND inserted_at < cutoff)",
        entity_type="ARTIST", value_type="numeric",
        event_time_meaning="Consumption in the month before the event",
        knowledge_time_rule="listened_at < cutoff AND inserted_at < cutoff (late-imported listens never leak backward)",
        source="listenbrainz", rights_status="RESEARCH_ONLY",
        commercial_use_status="RESEARCH_ONLY", derivation_version="historical_attention_v1",
        minimum_coverage=0.3, pit_admissible=True,
        target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="event_competition_same_day_market",
        semantic_definition="Count of other music events in the same market on the same day",
        entity_type="EVENT", value_type="numeric",
        event_time_meaning="Local competitive density on the event date",
        knowledge_time_rule="Competing events must have been known at cutoff (publication/knowledge_time < cutoff)",
        source="ticketmaster+musicbrainz", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="competition_v1",
        minimum_coverage=0.4, pit_admissible=True,
        target_families=("ALL",),
    ),
    FeatureSpec(
        name="event_competition_14d_market",
        semantic_definition="Count of other music events in the same market within +-14 days of the event",
        entity_type="EVENT", value_type="numeric",
        event_time_meaning="Local competitive density around the event",
        knowledge_time_rule="Competing events must have been known at cutoff",
        source="ticketmaster+musicbrainz", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="competition_v1",
        minimum_coverage=0.4, pit_admissible=True,
        target_families=("ALL",),
    ),
    FeatureSpec(
        name="market_population_vintage",
        semantic_definition="Market population from the newest census release knowable before the cutoff",
        entity_type="MARKET", value_type="numeric",
        event_time_meaning="Market size at event time",
        knowledge_time_rule="Use release with publication date < cutoff; never attach current ACS backward",
        source="census_acs", rights_status="OPEN_COMMERCIAL_OK",
        commercial_use_status="OK", derivation_version="market_vintage_v1",
        minimum_coverage=0.5, pit_admissible=True,
        target_families=("ALL",),
    ),
    FeatureSpec(
        name="market_median_income_vintage",
        semantic_definition="Market median household income from the newest vintage knowable before the cutoff",
        entity_type="MARKET", value_type="numeric",
        event_time_meaning="Market purchasing power at event time",
        knowledge_time_rule="Vintage publication < cutoff",
        source="census_acs", rights_status="OPEN_COMMERCIAL_OK",
        commercial_use_status="OK", derivation_version="market_vintage_v1",
        minimum_coverage=0.5, pit_admissible=True,
        target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="tour_position",
        semantic_definition="Position of the event within its tour (OPENING/EARLY/MIDDLE/LATE/CLOSING/UNKNOWN)",
        entity_type="EVENT", value_type="categorical",
        event_time_meaning="Routing position at event time",
        knowledge_time_rule="Tour schedule known at cutoff",
        source="musicbrainz+ticketmaster", rights_status="OPEN_ATTRIBUTION_REQUIRED",
        commercial_use_status="OK", derivation_version="tour_context_v1",
        minimum_coverage=0.3, pit_admissible=True,
        target_families=("ALL",),
    ),
]


def admit(feature: FeatureSpec, *, measured_coverage: float | None = None) -> FeatureSpec:
    """Deterministic admission: semantics + PIT + rights + coverage plan."""
    if measured_coverage is not None:
        feature.current_coverage = measured_coverage
    if feature.leakage_fields and set(feature.leakage_fields) & set(LEAKAGE_BLACKLIST):
        feature.status = STATUS_REJECTED_LEAKAGE
        feature.note = "uses leakage-blacklisted outcome field"
        return feature
    if not feature.pit_admissible:
        feature.status = STATUS_REJECTED_SEMANTICS
        feature.note = "no PIT admissibility rule"
        return feature
    if not feature.event_time_meaning:
        feature.status = STATUS_REJECTED_SEMANTICS
        feature.note = "undefined event-time meaning"
        return feature
    if feature.commercial_use_status not in ("OK", "RESEARCH_ONLY"):
        feature.status = STATUS_REJECTED_RIGHTS
        feature.note = f"rights blocked: {feature.commercial_use_status}"
        return feature
    if feature.current_coverage > 0 and feature.current_coverage < feature.minimum_coverage:
        feature.status = STATUS_REJECTED_COVERAGE
        feature.note = f"coverage {feature.current_coverage:.2f} < min {feature.minimum_coverage}"
        return feature
    feature.status = STATUS_ADMITTED
    feature.note = "admitted"
    return feature


def registry_snapshot(*, measured: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Admit every registry entry against measured coverage and return a snapshot."""
    out = []
    for spec in REGISTRY:
        s = admit(spec, measured_coverage=(measured or {}).get(spec.name))
        out.append({
            "name": s.name, "status": s.status, "note": s.note,
            "entity_type": s.entity_type, "value_type": s.value_type,
            "pit_admissible": s.pit_admissible,
            "rights_status": s.rights_status,
            "commercial_use_status": s.commercial_use_status,
            "minimum_coverage": s.minimum_coverage,
            "current_coverage": s.current_coverage,
            "knowledge_time_rule": s.knowledge_time_rule,
            "derivation_version": s.derivation_version,
            "target_families": list(s.target_families),
        })
    return out
