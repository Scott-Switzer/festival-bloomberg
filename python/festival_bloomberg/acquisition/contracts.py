"""Typed contracts for the Festival Signal Fabric acquisition layer.

Every provider call in the system flows through :class:`AcquisitionRequest` /
:class:`AcquisitionResult`. Provider failures are explicit statuses; a provider
must never encode failure as an empty successful dataset.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AcquisitionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    NO_RESULTS = "NO_RESULTS"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    POLICY_DENIED = "POLICY_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"

    #: statuses that indicate the provider was genuinely invoked
    @classmethod
    def attempted(cls) -> frozenset["AcquisitionStatus"]:
        return frozenset(
            {
                cls.SUCCESS,
                cls.NO_RESULTS,
                cls.RATE_LIMITED,
                cls.PROVIDER_ERROR,
                cls.TIMEOUT,
                cls.SCHEMA_INVALID,
                cls.PARTIAL_SUCCESS,
            }
        )


class EvidenceClass(str, Enum):
    """How an observation may be used. Synthetic/test data is quarantined."""

    OBSERVED_PUBLIC = "OBSERVED_PUBLIC"
    OBSERVED_PRIVATE = "OBSERVED_PRIVATE"
    MODELED = "MODELED"
    USER_ASSUMPTION = "USER_ASSUMPTION"
    SYNTHETIC_TEST_ONLY = "SYNTHETIC_TEST_ONLY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AcquisitionRequest:
    request_id: str
    entity_id: str
    entity_type: str
    platform: str
    query: str
    market_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    knowledge_cutoff: datetime | None = None
    max_records: int = 500
    max_cost_usd: float = 0.0
    preferred_providers: tuple[str, ...] = ()
    commercial_context: str = "research"
    correlation_id: str | None = None
    max_videos: int | None = None
    order: str | None = None
    search_cohort: str | None = None

    @classmethod
    def new(
        cls,
        entity_id: str,
        entity_type: str,
        platform: str,
        query: str,
        **kwargs: object,
    ) -> "AcquisitionRequest":
        return cls(
            request_id=kwargs.pop("request_id", str(uuid.uuid4())),
            entity_id=entity_id,
            entity_type=entity_type,
            platform=platform,
            query=query,
            **kwargs,  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class CostEstimate:
    provider: str
    estimated_cost_usd: float | None
    currency: str = "USD"
    free_quota: bool = False
    source: str = "provider_pricing"


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    healthy: bool
    last_error: str | None = None
    consecutive_failures: int = 0
    last_success_at: datetime | None = None


@dataclass(frozen=True)
class AcquisitionResult:
    request_id: str
    provider: str
    provider_endpoint: str | None
    status: AcquisitionStatus
    started_at: datetime
    completed_at: datetime
    record_count: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
    raw_payload_hash: str | None = None
    raw_payload_location: str | None = None
    policy_decision_id: str | None = None
    error_category: str | None = None
    provider_metadata: dict = field(default_factory=dict)
    #: normalized observation records carried from the provider to storage.
    #: Providers that cannot normalize still return raw payloads + hashes.
    records: tuple[dict, ...] = ()

    @property
    def is_success(self) -> bool:
        return self.status in (AcquisitionStatus.SUCCESS, AcquisitionStatus.PARTIAL_SUCCESS)

    @property
    def cost_per_record(self) -> float | None:
        if self.cost_usd is None or self.record_count <= 0:
            return None
        return self.cost_usd / self.record_count


def content_hash_of(payload: object) -> str:
    """Deterministic hash for raw payloads / normalized content."""
    import json

    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
