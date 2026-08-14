"""Data Flywheel & Coverage V1.

Four simultaneous pipelines turn the research corpus into a continuously
growing live-event research warehouse:

    EVENT_GRAPH    identity backbone + source registry (flywheel.event_graph)
    OUTCOME_HUNTER claims-based outcome acquisition (flywheel.outcome_hunter)
    CONTEXT_PANEL  attention / market / weather series with vintages
                   (flywheel.context_panel)
    FORWARD_WATCH  time-sensitive forward capture (flywheel.forward_watch)

Coverage is measured against the medium-term objectives on every run
(flywheel.coverage), so the acquisition metric stays decision coverage, not
row counts.
"""

from .coverage import measure_coverage, snapshot_id
from .event_graph import (
    MusicBrainzClient,
    build_identity_row,
    normalize_name,
    select_best_match,
)
from .forward_watch import (
    MILESTONE_LADDER,
    build_observation_row,
    compute_milestones,
    register_event_row,
)
from .objectives import (
    MEDIUM_TERM_OBJECTIVES_V1,
    OBJECTIVE_VERSION_V1,
    Objective,
    objective_rows,
)
from .outcome_hunter import (
    HUNT_TARGET_FIELDS,
    build_hunt_plan,
    claim_from_hunt_finding,
    event_key,
    hunt_status_allowed,
)
from .repository import FlywheelRepository
from .sources import SOURCE_REGISTRY_V1, source_rows

__all__ = [
    "FlywheelRepository",
    "HUNT_TARGET_FIELDS",
    "MEDIUM_TERM_OBJECTIVES_V1",
    "MILESTONE_LADDER",
    "MusicBrainzClient",
    "OBJECTIVE_VERSION_V1",
    "Objective",
    "SOURCE_REGISTRY_V1",
    "build_hunt_plan",
    "build_identity_row",
    "build_observation_row",
    "claim_from_hunt_finding",
    "compute_milestones",
    "event_key",
    "hunt_status_allowed",
    "measure_coverage",
    "normalize_name",
    "objective_rows",
    "register_event_row",
    "select_best_match",
    "snapshot_id",
    "source_rows",
]
