"""
Canonical Festival Bloomberg warehouse interface.

This is the unified warehouse interface for Festival Bloomberg, consolidating
functionality from the intelligence codebase into a single canonical implementation.

The warehouse provides:
- Canonical artist and festival dimensions
- Lineup resolution and slot management
- Metrics storage for attention and popularity data
- Point-in-time data modeling capabilities
- Source provenance tracking

Usage:
    from python.festival_bloomberg.warehouse import FestivalRepository, get_repository
    
    # Get repository instance
    repo = get_repository()
    
    # Use warehouse operations
    artist_key = repo.upsert_artist(artist_data, source_system="musicbrainz")
    metrics = repo.get_artist_metrics(artist_key)
"""

from .repository import FestivalRepository, get_repository, DEFAULT_DB_PATH
from .duckdb_manager import DuckDBWarehouse, create_warehouse
from .schema_loader import SCHEMA_PATH, apply_schema, schema_statements

__all__ = [
    "FestivalRepository",
    "get_repository", 
    "DEFAULT_DB_PATH",
    "DuckDBWarehouse",
    "create_warehouse",
    "SCHEMA_PATH",
    "apply_schema",
    "schema_statements",
]