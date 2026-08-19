"""Canonical feature registry for DENSE_PRE_EVENT_DATA_PANEL_V1.

No feature enters Comparable V2 without registry admission. Every potential
feature declares its semantic definition, knowledge_time rule, canonical
source IDs (for rights derivation), PIT admissibility, and a minimum coverage
requirement. Admission is deterministic and pure (never mutates the registry).

Statuses:
  CANDIDATE          declared but not yet admitted (registry default)
  NOT_MEASURED       no measured coverage has been supplied yet
  ADMITTED           passes all gates (semantics + PIT + rights + coverage)
  REJECTED_LEAKAGE   uses outcome fields (features.LEAKAGE_BLACKLIST)
  REJECTED_RIGHTS    commercial/research use blocked by canonical source policy
  REJECTED_COVERAGE  measured coverage < minimum (including exactly zero)
  REJECTED_SEMANTICS undefined event-time meaning or no PIT admissibility rule

Coverage states (independent of the feature status):
  NOT_MEASURED      measured_coverage is None
  MEASURED_ZERO     measured_coverage == 0.0
  BELOW_THRESHOLD   measured_coverage < minimum_coverage
  MEETS_THRESHOLD   measured_coverage >= minimum_coverage

``None`` is NOT ``0``: an unbuilt feature with no measurement is NOT_MEASURED,
never ADMITTED. A measured zero is a real coverage failure.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .features import LEAKAGE_BLACKLIST

STATUS_CANDIDATE = "CANDIDATE"
STATUS_NOT_MEASURED = "NOT_MEASURED"
STATUS_ADMITTED = "ADMITTED"
STATUS_REJECTED_LEAKAGE = "REJECTED_LEAKAGE"
STATUS_REJECTED_RIGHTS = "REJECTED_RIGHTS"
STATUS_REJECTED_COVERAGE = "REJECTED_COVERAGE"
STATUS_REJECTED_SEMANTICS = "REJECTED_SEMANTICS"

COVERAGE_NOT_MEASURED = "NOT_MEASURED"
COVERAGE_MEASURED_ZERO = "MEASURED_ZERO"
COVERAGE_BELOW_THRESHOLD = "BELOW_THRESHOLD"
COVERAGE_MEETS_THRESHOLD = "MEETS_THRESHOLD"


# ---------------------------------------------------------------------------
# Rights derivation from the canonical source-of-truth policy.
#
# A feature's rights are NOT hand-written. They are derived from the
# underlying evidence sources, and a composite feature inherits the MOST
# RESTRICTIVE applicable status. Two canonical registries are consulted:
#   * governance/source_registry.SourceRegistry (explicit commercial_use_status)
#   * acquisition/policy.default_policy_profiles (commercial_product_rights)
# Sources absent from both fail closed as UNKNOWN.
# ---------------------------------------------------------------------------

#: governance.source_registry.CommercialUseStatus -> canonical commercial tier
_COMMERCIAL_USE_TO_CANONICAL = {
    "OPEN_COMMERCIAL_OK": "APPROVED",
    "OPEN_WITH_ATTRIBUTION": "APPROVED_WITH_CONDITIONS",
    "FREE_RESEARCH_ONLY": "RESEARCH_ONLY",
    "NONCOMMERCIAL_ONLY": "RESEARCH_ONLY",
    "COMMERCIAL_AGREEMENT_REQUIRED": "COMMERCIAL_AGREEMENT_REQUIRED",
    "PARTNER_ACCESS_REQUIRED": "COMMERCIAL_AGREEMENT_REQUIRED",
    "TERMS_REVIEW_REQUIRED": "LEGAL_REVIEW_REQUIRED",
    "PROHIBITED": "PROHIBITED",
    "UNKNOWN": "UNKNOWN",
}

#: governance.policy.PolicyStatus -> canonical commercial tier
_POLICY_TO_CANONICAL = {
    "APPROVED": "APPROVED",
    "APPROVED_WITH_CONDITIONS": "APPROVED_WITH_CONDITIONS",
    "RESEARCH_ONLY": "RESEARCH_ONLY",
    "PRIVATE_CUSTOMER_LICENSE": "APPROVED_WITH_CONDITIONS",
    "COMMERCIAL_AGREEMENT_REQUIRED": "COMMERCIAL_AGREEMENT_REQUIRED",
    "LEGAL_REVIEW_REQUIRED": "LEGAL_REVIEW_REQUIRED",
    "PROHIBITED": "PROHIBITED",
    "UNKNOWN": "UNKNOWN",
}

#: Most restrictive first. ``most_restrictive`` picks the lower index.
_COMMERCIAL_ORDER = (
    "PROHIBITED",
    "UNKNOWN",
    "COMMERCIAL_AGREEMENT_REQUIRED",
    "LEGAL_REVIEW_REQUIRED",
    "RESEARCH_ONLY",
    "APPROVED_WITH_CONDITIONS",
    "APPROVED",
)

#: Statuses that block a feature entirely (even research use).
_RIGHTS_BLOCKED = frozenset({"PROHIBITED", "UNKNOWN"})


def _internal_source_commercial(source_id: str) -> str:
    """Our own derived venue-master geography/type. Provenance must be cited."""
    return "APPROVED_WITH_CONDITIONS"


def _canonical_commercial_status(source_id: str) -> str:
    """Resolve one source ID to a canonical commercial tier (fail closed)."""
    if source_id == "internal":
        return _internal_source_commercial(source_id)

    # 1. governance source registry (explicit commercial_use_status)
    try:
        from ..governance.source_registry import get_source_registry
    except Exception:  # pragma: no cover - registry is always importable
        get_source_registry = None
    if get_source_registry is not None:
        meta = get_source_registry().get(source_id)
        if meta is not None:
            return _COMMERCIAL_USE_TO_CANONICAL.get(
                meta.commercial_use_status.value, "UNKNOWN")

    # 2. acquisition policy profiles (commercial_product_rights)
    try:
        from ..acquisition.policy import default_policy_profiles
    except Exception:  # pragma: no cover
        default_policy_profiles = None
    if default_policy_profiles is not None:
        profile = default_policy_profiles().get(source_id)
        if profile is not None:
            return _POLICY_TO_CANONICAL.get(
                profile.commercial_product_rights.value, "UNKNOWN")

    # 3. absent from every canonical registry -> fail closed
    return "UNKNOWN"


def most_restrictive(*statuses: str) -> str:
    """Return the most restrictive canonical commercial tier of the inputs."""
    present = [s for s in statuses if s in _COMMERCIAL_ORDER]
    if not present:
        return "UNKNOWN"
    return min(present, key=lambda s: _COMMERCIAL_ORDER.index(s))


def resolve_commercial_status(sources: tuple[str, ...]) -> str:
    """Composite commercial tier = most restrictive applicable source tier."""
    if not sources:
        return "UNKNOWN"
    return most_restrictive(*(_canonical_commercial_status(s) for s in sources))


def coverage_state(measured: float | None, minimum: float) -> str:
    """Map a measured coverage value to its coverage state."""
    if measured is None:
        return COVERAGE_NOT_MEASURED
    if measured == 0.0:
        return COVERAGE_MEASURED_ZERO
    if measured < minimum:
        return COVERAGE_BELOW_THRESHOLD
    return COVERAGE_MEETS_THRESHOLD


@dataclass
class FeatureSpec:
    name: str
    semantic_definition: str
    entity_type: str          # EVENT | ARTIST | VENUE | MARKET | FESTIVAL
    value_type: str           # numeric | categorical | ratio | boolean
    event_time_meaning: str   # what the value refers to at event time
    knowledge_time_rule: str  # how the value was knowable at cutoff
    source: str               # human-readable source label
    sources: tuple[str, ...] = ()   # canonical source IDs (rights derivation)
    derivation_version: str = "v1"
    minimum_coverage: float = 1.0   # required fraction of the target population
    pit_admissible: bool = False
    leakage_fields: tuple[str, ...] = ()
    target_families: tuple[str, ...] = ()   # REPORTED_ATTENDANCE|PAID_TICKETS|TICKET_GROSS|SELL_OUT|ALL
    # Measured/admission state. ``current_coverage=None`` means NOT measured.
    current_coverage: float | None = None
    status: str = STATUS_CANDIDATE
    rights_status: str | None = None        # derived at admission time
    commercial_use_status: str | None = None  # derived at admission time
    note: str | None = None


REGISTRY: list[FeatureSpec] = [
    FeatureSpec(
        name="venue_capacity_band",
        semantic_definition="Claimed venue capacity bucketed into a band (e.g. 0-5k, 5k-15k, 15k-40k, 40k+)",
        entity_type="VENUE", value_type="categorical",
        event_time_meaning="Capacity of the venue hosting the event",
        knowledge_time_rule="Claim must be knowable at booking time; conflicts preserved, latest claim wins only if its effective_from <= cutoff",
        source="wikidata+osm", sources=("wikidata", "openstreetmap"),
        derivation_version="venue_intel_v1", minimum_coverage=0.6,
        pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="venue_indoor_outdoor",
        semantic_definition="Whether the venue is indoors or outdoors (evidence-backed)",
        entity_type="VENUE", value_type="categorical",
        event_time_meaning="Venue configuration at event time",
        knowledge_time_rule="From venue master; must not postdate the cutoff",
        source="venue master", sources=("internal",),
        derivation_version="venue_intel_v1", minimum_coverage=0.5,
        pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="venue_coordinates",
        semantic_definition="Lat/lon of the venue with source and derivation version",
        entity_type="VENUE", value_type="numeric",
        event_time_meaning="Venue location (static)",
        knowledge_time_rule="From venue master; geography is not time-varying for a built venue",
        source="venue master", sources=("internal",),
        derivation_version="venue_intel_v1", minimum_coverage=0.7,
        pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="artist_attention_wikimedia_30d_at_cutoff",
        semantic_definition="Wikimedia pageviews for the artist in the 30 days ending before the cutoff",
        entity_type="ARTIST", value_type="numeric",
        event_time_meaning="Attention in the month before the event",
        knowledge_time_rule="A day's aggregate is available only after it is published (available_at = observation day + 1); a window value is knowable at cutoff only if every contributing day's available_at < cutoff",
        source="wikimedia", sources=("wikimedia_analytics",),
        derivation_version="historical_attention_v1", minimum_coverage=0.4,
        pit_admissible=True, target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="artist_attention_wikimedia_growth_90d",
        semantic_definition="(30d window ending at cutoff) / (30d window ending 90d before cutoff) - 1",
        entity_type="ARTIST", value_type="ratio",
        event_time_meaning="Attention momentum before the event",
        knowledge_time_rule="Both windows end before cutoff and use only days whose available_at < cutoff",
        source="wikimedia", sources=("wikimedia_analytics",),
        derivation_version="historical_attention_v1", minimum_coverage=0.3,
        pit_admissible=True, target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="artist_attention_listenbrainz_30d_at_cutoff",
        semantic_definition="ListenBrainz listens in the 30 days ending before cutoff (listened_at < cutoff AND inserted_at < cutoff)",
        entity_type="ARTIST", value_type="numeric",
        event_time_meaning="Consumption in the month before the event",
        knowledge_time_rule="listened_at < cutoff AND inserted_at (available_at) < cutoff (late-imported listens never leak backward)",
        source="listenbrainz", sources=("listenbrainz",),
        derivation_version="historical_attention_v1", minimum_coverage=0.3,
        pit_admissible=True, target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="event_competition_same_day_market",
        semantic_definition="Count of other music events in the same market on the same day",
        entity_type="EVENT", value_type="numeric",
        event_time_meaning="Local competitive density on the event date",
        knowledge_time_rule="Competing events must have been known at cutoff (knowledge_time < cutoff); unknown-knowability events are reported separately, never counted as zero",
        source="ticketmaster+musicbrainz", sources=("ticketmaster_api", "musicbrainz"),
        derivation_version="competition_v1", minimum_coverage=0.4,
        pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="event_competition_14d_market",
        semantic_definition="Count of other music events in the same market within +-14 days of the event",
        entity_type="EVENT", value_type="numeric",
        event_time_meaning="Local competitive density around the event",
        knowledge_time_rule="Competing events must have been known at cutoff (knowledge_time < cutoff); unknown-knowability events reported separately",
        source="ticketmaster+musicbrainz", sources=("ticketmaster_api", "musicbrainz"),
        derivation_version="competition_v1", minimum_coverage=0.4,
        pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="market_population_vintage",
        semantic_definition="Market population from the newest census release knowable before the cutoff",
        entity_type="MARKET", value_type="numeric",
        event_time_meaning="Market size at event time",
        knowledge_time_rule="Use release with publication date < cutoff; never attach current ACS backward",
        source="census_acs", sources=("census_acs",),
        derivation_version="market_vintage_v1", minimum_coverage=0.5,
        pit_admissible=True, target_families=("ALL",),
    ),
    FeatureSpec(
        name="market_median_income_vintage",
        semantic_definition="Market median household income from the newest vintage knowable before the cutoff",
        entity_type="MARKET", value_type="numeric",
        event_time_meaning="Market purchasing power at event time",
        knowledge_time_rule="Vintage publication < cutoff",
        source="census_acs", sources=("census_acs",),
        derivation_version="market_vintage_v1", minimum_coverage=0.5,
        pit_admissible=True, target_families=("REPORTED_ATTENDANCE", "PAID_TICKETS", "TICKET_GROSS"),
    ),
    FeatureSpec(
        name="tour_position",
        semantic_definition="Position of the event within its tour (OPENING/EARLY/MIDDLE/LATE/CLOSING/UNKNOWN)",
        entity_type="EVENT", value_type="categorical",
        event_time_meaning="Routing position at event time",
        knowledge_time_rule="Tour schedule known at cutoff",
        source="musicbrainz+ticketmaster", sources=("musicbrainz", "ticketmaster_api"),
        derivation_version="tour_context_v1", minimum_coverage=0.3,
        pit_admissible=True, target_families=("ALL",),
    ),
]


def admit(feature: FeatureSpec, *, measured_coverage: float | None = None) -> FeatureSpec:
    """Deterministic admission. Pure: returns a new FeatureSpec, never mutates.

    Gates are evaluated in order: leakage, semantics, rights, then coverage.
    ``measured_coverage=None`` means NOT measured (not the same as 0.0).
    """
    out = replace(feature)

    # 1. leakage (must precede all other gates)
    if out.leakage_fields and set(out.leakage_fields) & set(LEAKAGE_BLACKLIST):
        out.status = STATUS_REJECTED_LEAKAGE
        out.note = "uses leakage-blacklisted outcome field"
        return out

    # 2. semantics
    if not out.pit_admissible:
        out.status = STATUS_REJECTED_SEMANTICS
        out.note = "no PIT admissibility rule"
        return out
    if not out.event_time_meaning:
        out.status = STATUS_REJECTED_SEMANTICS
        out.note = "undefined event-time meaning"
        return out

    # 3. rights (derived from canonical source policy, most restrictive)
    commercial = resolve_commercial_status(out.sources)
    out.rights_status = commercial
    out.commercial_use_status = commercial
    if commercial in _RIGHTS_BLOCKED:
        out.status = STATUS_REJECTED_RIGHTS
        out.note = (
            f"rights blocked: most restrictive of {list(out.sources) or ['(none)']} "
            f"is {commercial}"
        )
        return out

    # 4. coverage
    out.current_coverage = measured_coverage
    if measured_coverage is None:
        out.status = STATUS_NOT_MEASURED
        out.note = "no measured coverage yet"
        return out
    if measured_coverage < out.minimum_coverage:
        out.status = STATUS_REJECTED_COVERAGE
        out.note = f"coverage {measured_coverage:.4f} < min {out.minimum_coverage:.4f}"
        return out
    out.status = STATUS_ADMITTED
    out.note = "admitted"
    return out


def registry_snapshot(*, measured: dict[str, float | None] | None = None) -> list[dict[str, Any]]:
    """Admit every registry entry against measured coverage; return a snapshot.

    The REGISTRY objects are never mutated. ``measured`` maps feature name to a
    measured coverage (or None). Missing names are NOT_MEASURED.
    """
    out: list[dict[str, Any]] = []
    for spec in REGISTRY:
        mv = (measured or {}).get(spec.name)
        s = admit(spec, measured_coverage=mv)
        out.append({
            "name": s.name, "status": s.status, "note": s.note,
            "entity_type": s.entity_type, "value_type": s.value_type,
            "pit_admissible": s.pit_admissible,
            "rights_status": s.rights_status,
            "commercial_use_status": s.commercial_use_status,
            "sources": list(s.sources),
            "minimum_coverage": s.minimum_coverage,
            "current_coverage": s.current_coverage,
            "coverage_state": coverage_state(s.current_coverage, s.minimum_coverage),
            "knowledge_time_rule": s.knowledge_time_rule,
            "derivation_version": s.derivation_version,
            "target_families": list(s.target_families),
        })
    return out
