"""
Canonical entity resolution for Festival Bloomberg.

This module provides entity resolution services for canonical artist and festival
identification, consolidating functionality from the intelligence codebase.

Entity resolution includes:
- MusicBrainz-based canonical artist identification
- Name normalization and alias resolution
- External ID mapping (Spotify, Ticketmaster, YouTube, etc.)
- Confidence scoring and candidate generation
- Manual review workflows

Usage:
    from python.festival_bloomberg.entities import EntityResolver, create_test_resolver
    
    resolver = EntityResolver()
    result = resolver.resolve_by_name("The Weeknd")
"""

from .resolution import EntityResolver, create_test_resolver

__all__ = [
    "EntityResolver",
    "create_test_resolver",
]