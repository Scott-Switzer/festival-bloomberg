"""TRACK C — historical PIT cutoff reconstruction.

The research corpus could not support strict warm-start PIT evaluation
because ``source_publication_time`` was missing everywhere. This module does
NOT fabricate dates: it classifies HOW a publication time became knowable and
which validation modes may consume it.

Evidence taxonomy (never collapsed into a timestamp alone):

    OBSERVED_EXACT              source explicitly provides a trustworthy
                                timestamp (day + time)
    OBSERVED_DAY                source explicitly supports a calendar date
                                but not an exact time
    OBSERVED_MONTH              only month-level publication evidence exists
    ARCHIVE_CAPTURE_UPPER_BOUND archive proves the content existed no later
                                than the capture time (NOT original
                                publication time)
    SOURCE_PERIOD_BOUND         source edition/report period provides a
                                bounded interval
    ESTIMATED_RESEARCH_ONLY     research assumption only; NEVER enters strict
                                PIT validation
    UNKNOWN                     no defensible availability evidence

Validation modes:

    STRICT_PIT                 only classes that PROVE the claim was knowable
                               before cutoff
    CONSERVATIVE_BOUND_PIT     additionally accepts conservative
                               upper-bound / period evidence
    RESEARCH_ESTIMATED         additionally accepts research assumptions,
                               never presented as strict PIT

Unknown is never zero and never silently upgraded.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from ..acquisition.contracts import content_hash_of, utc_now

PIT_OBJECTIVE_VERSION = "data_acquisition_activation_v1"

# Evidence classes ----------------------------------------------------------
OBSERVED_EXACT = "OBSERVED_EXACT"
OBSERVED_DAY = "OBSERVED_DAY"
OBSERVED_MONTH = "OBSERVED_MONTH"
ARCHIVE_CAPTURE_UPPER_BOUND = "ARCHIVE_CAPTURE_UPPER_BOUND"
SOURCE_PERIOD_BOUND = "SOURCE_PERIOD_BOUND"
ESTIMATED_RESEARCH_ONLY = "ESTIMATED_RESEARCH_ONLY"
UNKNOWN = "UNKNOWN"

PIT_EVIDENCE_CLASSES = frozenset(
    {
        OBSERVED_EXACT,
        OBSERVED_DAY,
        OBSERVED_MONTH,
        ARCHIVE_CAPTURE_UPPER_BOUND,
        SOURCE_PERIOD_BOUND,
        ESTIMATED_RESEARCH_ONLY,
        UNKNOWN,
    }
)

# Validation modes ----------------------------------------------------------
STRICT_PIT = "STRICT_PIT"
CONSERVATIVE_BOUND_PIT = "CONSERVATIVE_BOUND_PIT"
RESEARCH_ESTIMATED = "RESEARCH_ESTIMATED"
PIT_MODES = frozenset({STRICT_PIT, CONSERVATIVE_BOUND_PIT, RESEARCH_ESTIMATED})

#: Classes that PROVE the claim was knowable by a given publication time.
STRICT_PIT_CLASSES = frozenset({OBSERVED_EXACT, OBSERVED_DAY, OBSERVED_MONTH})

#: Conservative upper-bound / bounded-interval classes (availability proof).
CONSERVATIVE_BOUND_CLASSES = frozenset(
    {ARCHIVE_CAPTURE_UPPER_BOUND, SOURCE_PERIOD_BOUND}
)

#: Research assumptions only.
ESTIMATED_CLASSES = frozenset({ESTIMATED_RESEARCH_ONLY})

#: Source-reporting kinds whose persisted source document publication date is
#: day-level evidence (the chart/article was published that day).
DAY_LEVEL_REPORTING_SOURCES = frozenset({"pollstar", "touring_data"})


def validate_evidence_class(evidence_class: str) -> str:
    if evidence_class not in PIT_EVIDENCE_CLASSES:
        raise ValueError(f"evidence_class {evidence_class!r} is not in the PIT taxonomy")
    return evidence_class


def validate_pit_mode(mode: str) -> str:
    if mode not in PIT_MODES:
        raise ValueError(f"mode {mode!r} is not a PIT validation mode")
    return mode


def mode_eligible(evidence_class: str, mode: str) -> bool:
    """Whether evidence of ``evidence_class`` may enter validation ``mode``.

    UNKNOWN is eligible for NO mode. ESTIMATED_RESEARCH_ONLY is eligible only
    for RESEARCH_ESTIMATED and must never be confused with strict PIT.
    """
    validate_evidence_class(evidence_class)
    validate_pit_mode(mode)
    if evidence_class in STRICT_PIT_CLASSES:
        return mode in (STRICT_PIT, CONSERVATIVE_BOUND_PIT, RESEARCH_ESTIMATED)
    if evidence_class in CONSERVATIVE_BOUND_CLASSES:
        return mode in (CONSERVATIVE_BOUND_PIT, RESEARCH_ESTIMATED)
    if evidence_class in ESTIMATED_CLASSES:
        return mode == RESEARCH_ESTIMATED
    return False  # UNKNOWN is eligible for nothing


def event_key_from_engagement(engagement: dict[str, Any]) -> str:
    """Deterministic canonical event key matching the hunt-plan convention."""
    artist = (engagement.get("artist") or "unknown").lower().replace(" ", "-")
    venue = (engagement.get("venue") or "unknown").lower().replace(" ", "-")
    start = engagement.get("start_date") or ""
    return f"boxoffice_{artist}_{venue}_{start}"


# ---------------------------------------------------------------------------
# Classification from persisted source documents (never fabricated)
# ---------------------------------------------------------------------------
def build_pit_evidence_row(
    *,
    canonical_event_id: str,
    evidence_class: str,
    source_publication_time: str | None = None,
    archive_capture_time: str | None = None,
    source_period_start: str | None = None,
    source_period_end: str | None = None,
    source_url: str | None = None,
    source_provider: str | None = None,
    source_document_id: str | None = None,
    rights_status: str = "RESEARCH_ONLY",
    commercial_use_status: str = "RESEARCH_ONLY",
    knowledge_time: datetime | None = None,
    software_version: str = PIT_OBJECTIVE_VERSION,
) -> dict[str, Any]:
    """Build one ``flywheel.pit_reconstruction_evidence`` row (pure)."""
    now = knowledge_time or utc_now()
    evidence_id = f"pit_{content_hash_of({
        'event': canonical_event_id,
        'class': evidence_class,
        'doc': source_document_id,
        'pub': source_publication_time,
        'cap': archive_capture_time,
    })[:20]}"
    return {
        "evidence_id": evidence_id,
        "canonical_event_id": canonical_event_id,
        "evidence_class": evidence_class,
        "source_publication_time": source_publication_time,
        "archive_capture_time": archive_capture_time,
        "source_period_start": source_period_start,
        "source_period_end": source_period_end,
        "source_url": source_url,
        "source_provider": source_provider,
        "source_document_id": source_document_id,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "knowledge_time": now.isoformat(),
        "software_version": software_version,
    }


def classify_source_document_evidence(
    *,
    canonical_event_id: str,
    reporting_source: str,
    source_url: str | None,
    publication_date: date | None,
    source_document_id: str | None,
) -> list[dict[str, Any]]:
    """Classify persisted boxoffice source-document evidence.

    - pollstar / touring_data source docs carry a REAL ``publication_date``
      (the chart/article was published that day): OBSERVED_DAY.
    - any source doc without a defensible date yields NO evidence row
      (UNKNOWN remains unknown; nothing is invented).
    Returns zero or one row.
    """
    if publication_date is None:
        return []
    if reporting_source not in DAY_LEVEL_REPORTING_SOURCES:
        # We only claim day-level evidence where the source semantics
        # genuinely support it; anything else stays UNKNOWN.
        return []
    return [
        build_pit_evidence_row(
            canonical_event_id=canonical_event_id,
            evidence_class=OBSERVED_DAY,
            source_publication_time=datetime.combine(
                publication_date, time.min
            ).isoformat(),
            source_url=source_url,
            source_provider=reporting_source,
            source_document_id=source_document_id,
            rights_status="RESEARCH_ONLY",
            commercial_use_status="RESEARCH_ONLY",
        )
    ]


def build_archive_upper_bound_evidence(
    *,
    canonical_event_id: str,
    capture_time: str,
    source_url: str | None,
    source_provider: str,
    source_document_id: str | None,
) -> dict[str, Any]:
    """ARCHIVE_CAPTURE_UPPER_BOUND row from a real archive capture.

    The capture time proves the content EXISTED by then; it is never promoted
    to ``source_publication_time`` (both fields are preserved separately).
    """
    return build_pit_evidence_row(
        canonical_event_id=canonical_event_id,
        evidence_class=ARCHIVE_CAPTURE_UPPER_BOUND,
        archive_capture_time=capture_time,
        source_url=source_url,
        source_provider=source_provider,
        source_document_id=source_document_id,
        rights_status="TERMS_REVIEW_REQUIRED",
        commercial_use_status="TERMS_REVIEW_REQUIRED",
    )


# ---------------------------------------------------------------------------
# Coverage reads (pure, fail closed)
# ---------------------------------------------------------------------------
def count_events_reconstructable(conn, *, mode: str) -> int:
    """Distinct canonical events with >= 1 evidence row eligible for ``mode``.

    UNKNOWN is never upgraded: an event with only UNKNOWN evidence (i.e. no
    persisted rows) counts for nothing.
    """
    validate_pit_mode(mode)
    rows = conn.execute(
        "SELECT DISTINCT evidence_class FROM flywheel.pit_reconstruction_evidence"
    ).fetchall()
    eligible = [r[0] for r in rows if mode_eligible(r[0], mode)]
    if not eligible:
        return 0
    placeholders = ",".join("?" for _ in eligible)
    row = conn.execute(
        f"SELECT COUNT(DISTINCT canonical_event_id) "
        f"FROM flywheel.pit_reconstruction_evidence "
        f"WHERE evidence_class IN ({placeholders})",
        eligible,
    ).fetchone()
    return int(row[0]) if row else 0


def count_unknown_events(conn) -> int:
    """Canonical corpus events with NO persisted PIT evidence (honest UNKNOWN)."""
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM research.canonical_boxoffice_engagements "
            "WHERE (is_multi_show IS NULL OR is_multi_show = FALSE)"
        ).fetchone()[0]
    )
    covered = count_events_reconstructable(conn, mode=RESEARCH_ESTIMATED)
    return max(total - covered, 0)
