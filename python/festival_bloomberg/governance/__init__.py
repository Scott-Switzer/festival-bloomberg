"""
Source governance and eligibility management for Festival Bloomberg.

This module provides machine-readable metadata for all data sources to ensure
legal and commercial compliance. Every source must have documented eligibility
before production use.

Governance includes:
- Source eligibility checking
- Commercial use status validation
- Cost classification
- Access type tracking
- License and terms compliance
- Production readiness assessment

Usage:
    from python.festival_bloomberg.governance import SourceRegistry, SourceMetadata
    
    registry = SourceRegistry()
    source = registry.get_source("wikidata")
    if source.is_production_eligible():
        # Use source in production
        pass
"""

from .source_registry import (
    SourceRegistry,
    SourceMetadata,
    CommercialUseStatus,
    CostClass,
    AccessType,
    SourceCategory,
)

__all__ = [
    "SourceRegistry",
    "SourceMetadata",
    "CommercialUseStatus",
    "CostClass",
    "AccessType",
    "SourceCategory",
]