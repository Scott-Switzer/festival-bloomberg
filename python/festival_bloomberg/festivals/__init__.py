"""Festival spine package: canonical festivals, editions, lineups, billing.

The seed data below is transcribed from ``docs/historical_lineups_and_billing_analysis.md``
(the accepted historical festival/lineup research). Every row is a
RESEARCH_DISCOVERY_SEED, NOT an observed canonical fact: the research document
is a discovery lead whose cited sources must still be corroborated before a
claim is treated as OBSERVED_*. Billing tiers are analytical labels from the
research pass, preserved as source-specific observations with confidence and
rationale — never a universal truth.
"""

from .repository import (
    FestivalSpineRepository,
    billing_trajectory,
    co_occurrence,
    relationship_graph,
)

__all__ = [
    "FestivalSpineRepository",
    "billing_trajectory",
    "co_occurrence",
    "relationship_graph",
]
