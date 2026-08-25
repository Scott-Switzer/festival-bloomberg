"""Live Entertainment Evidence Rails v1.

Common append-only observation contract for all external event sources.

Architecture:
  - ONE observation table (acquisition.external_event_observations)
  - All scrapers/APIs write immutable rows — never update-in-place
  - Repeated scrapes = new observations = change detection
  - Canonical entity resolution maps external records to our keys
  - Coverage tracking summarizes information advantage per event
"""

from .contract import (
    ingest_observation,
    ingest_observations_batch,
    detect_changes,
    compute_coverage,
    update_event_coverage,
    ObservationRecord,
    ChangeRecord,
)

__all__ = [
    "ingest_observation",
    "ingest_observations_batch",
    "detect_changes",
    "compute_coverage",
    "update_event_coverage",
    "ObservationRecord",
    "ChangeRecord",
]