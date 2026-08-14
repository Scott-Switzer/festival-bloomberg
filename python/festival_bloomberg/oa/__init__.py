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
from .youtube_fan_signal import run_youtube_fan_signal_oa
from .event_history import run_event_history_oa
from .market_economics import run_market_economics_oa
from .historical_laboratory import run_historical_laboratory_oa

__all__ = [
    "CANDIDATE_ARTISTS",
    "CHICAGO_PAGES",
    "SELECTION_RULE",
    "detect_chicago_mentions",
    "provider_readiness",
    "run_operational_acceptance",
    "run_youtube_fan_signal_oa",
    "run_event_history_oa",
    "run_market_economics_oa",
    "run_historical_laboratory_oa",
    "select_artist",
]
