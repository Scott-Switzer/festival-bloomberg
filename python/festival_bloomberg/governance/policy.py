"""Source policy contracts for the Festival Signal Fabric.

Distinguishes the seven rights dimensions that a single ``commercial_use``
boolean cannot express:

* content license
* API access rights
* scraping rights
* storage rights
* derivative-analytics rights
* redistribution rights
* commercial-product rights

``UNKNOWN`` fails closed in every mode: a source whose rights are not
explicitly approved cannot be used. Terms may be summarized automatically,
but commercial approval is an explicit registry state, never inferred.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..acquisition.contracts import utc_now


class PolicyStatus(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_CONDITIONS = "APPROVED_WITH_CONDITIONS"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    PRIVATE_CUSTOMER_LICENSE = "PRIVATE_CUSTOMER_LICENSE"
    COMMERCIAL_AGREEMENT_REQUIRED = "COMMERCIAL_AGREEMENT_REQUIRED"
    LEGAL_REVIEW_REQUIRED = "LEGAL_REVIEW_REQUIRED"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


class RightsDimension(str, Enum):
    CONTENT_LICENSE = "content_license"
    API_ACCESS_RIGHTS = "api_access_rights"
    SCRAPING_RIGHTS = "scraping_rights"
    STORAGE_RIGHTS = "storage_rights"
    DERIVATIVE_ANALYTICS_RIGHTS = "derivative_analytics_rights"
    REDISTRIBUTION_RIGHTS = "redistribution_rights"
    COMMERCIAL_PRODUCT_RIGHTS = "commercial_product_rights"


#: A source is denied outright if any of these statuses appears.
_DENIED_STATUSES = frozenset({PolicyStatus.PROHIBITED, PolicyStatus.UNKNOWN})

#: Dimensions relevant when acquiring through an official API / managed
#: provider (scraping terms are not exercised).
API_DIMENSIONS = (
    RightsDimension.CONTENT_LICENSE,
    RightsDimension.API_ACCESS_RIGHTS,
    RightsDimension.STORAGE_RIGHTS,
    RightsDimension.DERIVATIVE_ANALYTICS_RIGHTS,
    RightsDimension.REDISTRIBUTION_RIGHTS,
    RightsDimension.COMMERCIAL_PRODUCT_RIGHTS,
)

#: Dimensions relevant when acquiring by scraping the public web.
SCRAPING_DIMENSIONS = (
    RightsDimension.CONTENT_LICENSE,
    RightsDimension.SCRAPING_RIGHTS,
    RightsDimension.STORAGE_RIGHTS,
    RightsDimension.DERIVATIVE_ANALYTICS_RIGHTS,
    RightsDimension.REDISTRIBUTION_RIGHTS,
    RightsDimension.COMMERCIAL_PRODUCT_RIGHTS,
)


@dataclass(frozen=True)
class RightsProfile:
    source_id: str
    content_license: PolicyStatus = PolicyStatus.UNKNOWN
    api_access_rights: PolicyStatus = PolicyStatus.UNKNOWN
    scraping_rights: PolicyStatus = PolicyStatus.UNKNOWN
    storage_rights: PolicyStatus = PolicyStatus.UNKNOWN
    derivative_analytics_rights: PolicyStatus = PolicyStatus.UNKNOWN
    redistribution_rights: PolicyStatus = PolicyStatus.UNKNOWN
    commercial_product_rights: PolicyStatus = PolicyStatus.UNKNOWN
    notes: str = ""

    def dimension(self, dimension: RightsDimension) -> PolicyStatus:
        return getattr(self, dimension.value)


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    source_id: str
    commercial_context: str
    allowed: bool
    rationale: str
    decided_at: datetime = field(default_factory=utc_now)


def evaluate(
    source_id: str,
    profile: RightsProfile,
    commercial_context: str = "research",
    mechanism: str = "api",
) -> PolicyDecision:
    """Evaluate a rights profile for a given context and mechanism.

    ``mechanism`` is ``"api"`` (official API / managed provider) or
    ``"scraping"`` (self-hosted web scraping); each checks the relevant
    rights dimensions (e.g. a platform may allow its official API while
    prohibiting scraping). Research mode permits research-only sources;
    commercial mode requires a fully approved profile. ``UNKNOWN`` or
    ``PROHIBITED`` in any relevant dimension denies the request (fail closed).
    """
    dimensions = SCRAPING_DIMENSIONS if mechanism == "scraping" else API_DIMENSIONS

    blocked: list[str] = []
    for dimension in dimensions:
        status = profile.dimension(dimension)
        if status in _DENIED_STATUSES:
            blocked.append(f"{dimension.value}={status.value}")

    if blocked:
        return PolicyDecision(
            decision_id=str(uuid.uuid4()),
            source_id=source_id,
            commercial_context=commercial_context,
            allowed=False,
            rationale=f"denied: {'; '.join(sorted(blocked))}",
        )

    required = {
        PolicyStatus.APPROVED,
        PolicyStatus.APPROVED_WITH_CONDITIONS,
        PolicyStatus.PRIVATE_CUSTOMER_LICENSE,
    }
    if commercial_context == "commercial":
        allowed = all(profile.dimension(d) in required for d in dimensions)
        if not allowed:
            pending = [
                f"{d.value}={profile.dimension(d).value}"
                for d in dimensions
                if profile.dimension(d) not in required
            ]
            return PolicyDecision(
                decision_id=str(uuid.uuid4()),
                source_id=source_id,
                commercial_context=commercial_context,
                allowed=False,
                rationale=f"commercial denied: not fully approved ({'; '.join(sorted(pending))})",
            )
        return PolicyDecision(
            decision_id=str(uuid.uuid4()),
            source_id=source_id,
            commercial_context=commercial_context,
            allowed=True,
            rationale="commercial approved",
        )

    # research mode
    research_ok = {
        PolicyStatus.APPROVED,
        PolicyStatus.APPROVED_WITH_CONDITIONS,
        PolicyStatus.RESEARCH_ONLY,
        PolicyStatus.PRIVATE_CUSTOMER_LICENSE,
        PolicyStatus.LEGAL_REVIEW_REQUIRED,
        PolicyStatus.COMMERCIAL_AGREEMENT_REQUIRED,
    }
    allowed = all(profile.dimension(d) in research_ok for d in dimensions)
    return PolicyDecision(
        decision_id=str(uuid.uuid4()),
        source_id=source_id,
        commercial_context=commercial_context,
        allowed=allowed,
        rationale="research approved" if allowed else "research denied",
    )


def unknown_profile(source_id: str) -> RightsProfile:
    """A source with no registry entry is UNKNOWN everywhere and fails closed."""
    return RightsProfile(source_id=source_id, notes="no registry entry; UNKNOWN fails closed")
