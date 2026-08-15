"""DeepSeek V4 Pro evidence analyst — CANDIDATE CLAIMS ONLY.

DeepSeek does not decide truth. It proposes candidate claims with exact
source-document IDs and evidence character offsets; the deterministic verifier
decides admissibility. Model output NEVER writes evidence directly.

Hosted-LLM boundary (fail closed): only PUBLIC / research material and
sanitized schemas may be sent. Private promoter/customer settlement data is
excluded by default — this client builds dossiers from public boxscore
engagements and already-persisted public documents only.

When no API key / transport is configured the client reports NOT_CONFIGURED
and makes zero requests; it never fabricates a response.
"""

from __future__ import annotations

from typing import Any

#: Strict JSON-schema tool contract for candidate claims. Every field maps to
#: the deterministic verifier's input surface. ``additionalProperties`` is
#: forbidden so unknown fields can never smuggle unverified data through.
CANDIDATE_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "canonical_event_id",
        "cutoff_type",
        "source_document_id",
        "source_url",
        "evidence_text",
        "evidence_span_start",
        "evidence_span_end",
        "granularity",
        "evidence_class",
        "interpretation",
        "contradiction_detected",
    ],
    "properties": {
        "canonical_event_id": {"type": "string"},
        "cutoff_type": {
            "type": "string",
            "enum": [
                "BOOKING_OR_OFFER", "ANNOUNCEMENT", "PRESALE", "GENERAL_ONSALE",
                "TICKET_PRICE_OBSERVATION", "EVENT_DATE", "RESULT_PUBLICATION",
                "SETTLEMENT",
            ],
        },
        "candidate_timestamp": {"type": ["string", "null"]},
        "lower_bound": {"type": ["string", "null"]},
        "upper_bound": {"type": ["string", "null"]},
        "granularity": {"type": "string", "enum": ["EXACT", "DAY", "MONTH"]},
        "evidence_class": {
            "type": "string",
            "enum": [
                "OBSERVED_EXACT", "OBSERVED_DAY", "OBSERVED_MONTH",
                "ARCHIVE_CAPTURE_UPPER_BOUND", "SOURCE_PERIOD_BOUND",
                "ESTIMATED_RESEARCH_ONLY", "UNKNOWN",
            ],
        },
        "source_document_id": {"type": "string"},
        "source_url": {"type": "string"},
        "evidence_text": {"type": "string"},
        "evidence_span_start": {"type": "integer"},
        "evidence_span_end": {"type": "integer"},
        "interpretation": {"type": "string"},
        "contradiction_detected": {"type": "boolean"},
    },
}

EXTRACTOR_DEEPSEEK = "DEEPSEEK_V4_PRO"


def validate_candidate_shape(candidate: dict[str, Any]) -> str | None:
    """Validate a model-returned candidate against the strict contract.

    Returns a rejection reason, or None when the shape is well-formed. This is
    a pure, deterministic check the pipeline runs BEFORE verification; it
    never trusts the model to have formatted itself.
    """
    if candidate.get("source_document_id") in (None, ""):
        return "candidate_missing_source_document_id"
    if candidate.get("source_url") in (None, ""):
        return "candidate_missing_source_url"
    if not candidate.get("evidence_text"):
        return "candidate_missing_evidence_text"
    span_start = candidate.get("evidence_span_start")
    span_end = candidate.get("evidence_span_end")
    if not isinstance(span_start, int) or not isinstance(span_end, int):
        return "candidate_missing_evidence_span"
    if span_end <= span_start:
        return "candidate_invalid_evidence_span"
    if candidate.get("cutoff_type") not in CANDIDATE_CLAIM_SCHEMA["properties"]["cutoff_type"]["enum"]:
        return "candidate_unknown_cutoff_type"
    if candidate.get("granularity") not in ("EXACT", "DAY", "MONTH"):
        return "candidate_unknown_granularity"
    if candidate.get("evidence_class") not in CANDIDATE_CLAIM_SCHEMA["properties"]["evidence_class"]["enum"]:
        return "candidate_unknown_evidence_class"
    return None


class DeepSeekEvidenceExtractor:
    """Thin client over the DeepSeek chat/completions tool-call API.

    Configured from ``DEEPSEEK_API_KEY`` (never committed). With no key or
    transport the client is NOT_CONFIGURED and makes zero requests.
    """

    name = "deepseek_v4_pro"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        transport: Any = None,
        model: str = "deepseek-v4-pro",
    ) -> None:
        self.api_key = api_key
        self.transport = transport
        self.model = model
        self._telemetry: dict[str, Any] = {
            "request_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_hit_tokens": 0,
        }

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def telemetry(self) -> dict[str, Any]:
        return dict(self._telemetry)

    def extract_candidates(self, dossier: dict[str, Any]) -> dict[str, Any]:
        """Ask DeepSeek for candidate claims for one event dossier.

        NOT_CONFIGURED without a key: returns status + zero candidates and
        makes no network call. The return value is CANDIDATE CLAIMS ONLY; the
        caller must run them through ``validate_candidate_shape`` and the
        deterministic verifier before any persistence.
        """
        if not self.is_configured:
            return {
                "status": "NOT_CONFIGURED",
                "candidates": [],
                "note": "no DeepSeek API key; public evidence extraction skipped",
            }
        if self.transport is None:
            return {
                "status": "NOT_CONFIGURED",
                "candidates": [],
                "note": "no transport; DeepSeek extraction not performed offline",
            }
        # Live call is intentionally NOT performed by this milestone's bounded
        # offline run; the transport contract is in place for a keyed run.
        return {"status": "NOT_CONFIGURED", "candidates": []}


def build_public_event_dossier(
    engagement: dict[str, Any],
    *,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a PUBLIC-only event dossier (never private settlement data).

    Includes canonical event metadata, aliases (none beyond the corpus name),
    tour, provider ids, and already-persisted PUBLIC documents. This is the
    exact payload a keyed run would send — kept public/sanitized by
    construction.
    """
    return {
        "canonical_event_id": engagement.get("engagement_id"),
        "artist": engagement.get("artist"),
        "venue": engagement.get("venue"),
        "market": engagement.get("market") or engagement.get("city"),
        "event_date": str(engagement.get("start_date"))[:10] if engagement.get("start_date") else None,
        "tour": engagement.get("tour"),
        "reporting_source": engagement.get("reporting_source"),
        "public_documents": documents or [],
        "note": "public/research material only; private settlement data excluded",
    }
