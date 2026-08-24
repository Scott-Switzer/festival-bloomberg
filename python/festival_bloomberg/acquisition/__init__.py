"""Festival Signal Fabric — acquisition layer."""

from .contracts import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionStatus,
    CostEstimate,
    EvidenceClass,
    ProviderHealth,
)
from .router import AcquisitionRouter
from .automation import AutomationStatus, automation_allowed, automation_status

__all__ = [
    "AcquisitionRequest",
    "AcquisitionResult",
    "AcquisitionStatus",
    "AcquisitionRouter",
    "AutomationStatus",
    "automation_allowed",
    "automation_status",
    "CostEstimate",
    "EvidenceClass",
    "ProviderHealth",
]
