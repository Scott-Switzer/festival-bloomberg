"""
Entity resolution layer for Festival Bloomberg
Implements MusicBrainz and Wikidata integration for identity resolution
"""
from .entity_resolution import EntityResolver, MBIDResolver, QIDResolver, MBIDCandidate, QIDCandidate, EntityResolutionResult, MatchMethod, MatchConfidence

__all__ = [
    'EntityResolver',
    'MBIDResolver',
    'QIDResolver',
    'MBIDCandidate',
    'QIDCandidate',
    'EntityResolutionResult',
    'MatchMethod',
    'MatchConfidence'
]
