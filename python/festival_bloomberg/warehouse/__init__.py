"""
Festival Bloomberg unified warehouse module.

This module provides the canonical warehouse repository interface that consolidates
functionality from both the intelligence and main codebases, with support for:
- Point-in-time data modeling with temporal fields
- Entity resolution and canonical data management
- Feature store for backtesting and analytics
- Artist factors, expected billing, and relative value calculations
- Festival portfolio analytics
- Comprehensive source governance and provenance tracking
"""

from .repository import FestivalRepository, get_repository, DEFAULT_DB_PATH
from .schema_loader import apply_schema, SCHEMA_PATH
from .duckdb_manager import DuckDBWarehouse, create_warehouse

__all__ = [
    "FestivalRepository",
    "get_repository", 
    "DEFAULT_DB_PATH",
    "apply_schema",
    "SCHEMA_PATH",
    "DuckDBWarehouse",
    "create_warehouse",
]