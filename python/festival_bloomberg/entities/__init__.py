"""
Festival Bloomberg entity resolution module.

This module provides canonical entity resolution for artists, festivals, venues,
and other entities across multiple data sources. It integrates with the warehouse
for persistence and supports point-in-time resolution for backtesting.
"""

from .resolution import (
    ArtistMapping,
    EntityResolver,
    ResolutionResult,
    create_test_resolver,
    MusicBrainzResolver,
    resolve_artist_with_warehouse,
)

__all__ = [
    "ArtistMapping",
    "EntityResolver", 
    "ResolutionResult",
    "create_test_resolver",
    "MusicBrainzResolver",
    "resolve_artist_with_warehouse",
]