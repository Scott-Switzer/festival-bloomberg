"""Live operational acceptance for the Festival Signal Fabric.

This package contains the live-only driver that proves real public evidence
can flow through the canonical acquisition, evidence, PIT and NLP components
at $0 cost with no configured provider keys. It is deliberately separate from
the deterministic offline CI suite.
"""

from .operational_acceptance import (
    CANDIDATE_ARTISTS,
    CHICAGO_PAGES,
    SELECTION_RULE,
    detect_chicago_mentions,
    provider_readiness,
    run_operational_acceptance,
    select_artist,
)

__all__ = [
    "CANDIDATE_ARTISTS",
    "CHICAGO_PAGES",
    "SELECTION_RULE",
    "detect_chicago_mentions",
    "provider_readiness",
    "run_operational_acceptance",
    "select_artist",
]
