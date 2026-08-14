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
from .economic_outcome import run_economic_outcome_oa
from .design_partner import run_design_partner_oa
from .boxscore import run_boxscore_oa
from .boxscore_v2 import run_boxscore_v2_oa
from .baseline_research import run_baseline_research_oa

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
    "run_economic_outcome_oa",
    "run_design_partner_oa",
    "run_boxscore_oa",
    "run_boxscore_v2_oa",
    "run_baseline_research_oa",
    "select_artist",
]
