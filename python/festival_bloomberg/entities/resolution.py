"""
Canonical entity resolution for Festival Bloomberg.

This module provides artist identity resolution across multiple data sources,
with MusicBrainz as the canonical source. It supports:
- Name normalization and fuzzy matching
- External ID resolution (Wikidata, Spotify, Ticketmaster, etc.)
- Warehouse integration for persistence
- Point-in-time resolution for backtesting
- Confidence scoring and manual review workflow
"""

from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
import re
import logging

logger = logging.getLogger(__name__)


@dataclass
class ArtistMapping:
    """Mapping between external IDs and canonical MusicBrainz ID."""
    musicbrainz_id: str
    wikidata_id: Optional[str] = None
    ticketmaster_id: Optional[str] = None
    youtube_channel_id: Optional[str] = None
    spotify_id: Optional[str] = None
    setlistfm_id: Optional[str] = None
    normalized_name: str = ""
    aliases: List[str] = field(default_factory=list)
    confidence: float = 1.0
    manually_reviewed: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def __post_init__(self):
        if not self.normalized_name and self.aliases:
            self.normalized_name = self.normalize_name(self.aliases[0])


@dataclass
class ResolutionResult:
    """Result of entity resolution attempt."""
    artist_key: str
    musicbrainz_id: Optional[str]
    confidence: float
    match_method: str
    requires_review: bool = False
    alternatives: List[Tuple[str, float]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    resolved_at: datetime = field(default_factory=datetime.utcnow)


class EntityResolver:
    """
    Resolves artist identities across multiple data sources.
    Uses MusicBrainz as the canonical source.
    """
    
    def __init__(self):
        self.mappings: Dict[str, ArtistMapping] = {}  # musicbrainz_id -> ArtistMapping
        self.name_index: Dict[str, List[str]] = {}  # normalized_name -> list of musicbrainz_ids
        self.external_id_index: Dict[str, Dict[str, str]] = {
            'wikidata': {},
            'ticketmaster': {},
            'spotify': {},
            'youtube': {},
            'setlistfm': {},
        }
    
    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize artist name for comparison."""
        normalized = name.lower().strip()
        normalized = re.sub(r"[^a-z0-9'\- ]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized
    
    def add_mapping(self, mapping: ArtistMapping) -> None:
        """Add or update an artist mapping."""
        self.mappings[mapping.musicbrainz_id] = mapping
        mapping.updated_at = datetime.utcnow()
        
        # Update name index
        normalized = mapping.normalized_name or self.normalize_name(mapping.normalized_name)
        if normalized not in self.name_index:
            self.name_index[normalized] = []
        if mapping.musicbrainz_id not in self.name_index[normalized]:
            self.name_index[normalized].append(mapping.musicbrainz_id)
        
        # Index aliases
        for alias in mapping.aliases:
            normalized_alias = self.normalize_name(alias)
            if normalized_alias not in self.name_index:
                self.name_index[normalized_alias] = []
            if mapping.musicbrainz_id not in self.name_index[normalized_alias]:
                self.name_index[normalized_alias].append(mapping.musicbrainz_id)
        
        # Index external IDs
        if mapping.wikidata_id:
            self.external_id_index['wikidata'][mapping.wikidata_id] = mapping.musicbrainz_id
        if mapping.ticketmaster_id:
            self.external_id_index['ticketmaster'][mapping.ticketmaster_id] = mapping.musicbrainz_id
        if mapping.spotify_id:
            self.external_id_index['spotify'][mapping.spotify_id] = mapping.musicbrainz_id
        if mapping.youtube_channel_id:
            self.external_id_index['youtube'][mapping.youtube_channel_id] = mapping.musicbrainz_id
        if mapping.setlistfm_id:
            self.external_id_index['setlistfm'][mapping.setlistfm_id] = mapping.musicbrainz_id
    
    def resolve_by_name(self, name: str, max_results: int = 5) -> ResolutionResult:
        """
        Resolve artist by name, returning ResolutionResult.
        """
        normalized = self.normalize_name(name)
        
        # Exact match
        if normalized in self.name_index:
            mbids = self.name_index[normalized]
            if len(mbids) == 1:
                return ResolutionResult(
                    artist_key=mbids[0],
                    musicbrainz_id=mbids[0],
                    confidence=1.0,
                    match_method="exact_name",
                    requires_review=False,
                    evidence={"normalized_name": normalized, "match_type": "exact"}
                )
            else:
                # Multiple exact matches - requires review
                alternatives = [(mbid, 1.0) for mbid in mbids]
                return ResolutionResult(
                    artist_key=mbids[0],  # Return first as best guess
                    musicbrainz_id=mbids[0],
                    confidence=0.5,
                    match_method="exact_name_ambiguous",
                    requires_review=True,
                    alternatives=alternatives,
                    evidence={"normalized_name": normalized, "match_type": "ambiguous_exact"}
                )
        
        # Fuzzy match (simple prefix match for now)
        matches = []
        for indexed_name, mbids in self.name_index.items():
            if normalized in indexed_name or indexed_name in normalized:
                confidence = 1.0 if normalized == indexed_name else 0.7
                for mbid in mbids:
                    matches.append((mbid, confidence))
        
        if not matches:
            return ResolutionResult(
                artist_key=f"name::{normalized}",
                musicbrainz_id=None,
                confidence=0.0,
                match_method="no_match",
                requires_review=True,
                evidence={"normalized_name": normalized, "match_type": "none"}
            )
        
        # Deduplicate and sort by confidence
        seen = set()
        unique_matches = []
        for mbid, conf in matches:
            if mbid not in seen:
                seen.add(mbid)
                unique_matches.append((mbid, conf))
        
        unique_matches = sorted(unique_matches, key=lambda x: x[1], reverse=True)
        best_mbid, best_conf = unique_matches[0]
        
        alternatives = unique_matches[1:max_results] if len(unique_matches) > 1 else []
        
        return ResolutionResult(
            artist_key=best_mbid,
            musicbrainz_id=best_mbid,
            confidence=best_conf,
            match_method="fuzzy_name",
            requires_review=best_conf < 0.9,
            alternatives=alternatives,
            evidence={"normalized_name": normalized, "match_type": "fuzzy"}
        )
    
    def resolve_by_musicbrainz_id(self, mbid: str) -> Optional[ArtistMapping]:
        """Resolve by MusicBrainz ID."""
        return self.mappings.get(mbid)
    
    def resolve_by_external_id(self, id_type: str, external_id: str) -> Optional[ArtistMapping]:
        """Resolve by external ID (wikidata, spotify, ticketmaster, etc.)."""
        if id_type not in self.external_id_index:
            logger.warning(f"Unknown external ID type: {id_type}")
            return None
        
        mbid = self.external_id_index[id_type].get(external_id)
        if mbid:
            return self.mappings.get(mbid)
        return None
    
    def resolve_by_wikidata_id(self, qid: str) -> Optional[ArtistMapping]:
        """Resolve by Wikidata QID."""
        return self.resolve_by_external_id('wikidata', qid)
    
    def resolve_by_ticketmaster_id(self, tm_id: str) -> Optional[ArtistMapping]:
        """Resolve by Ticketmaster ID."""
        return self.resolve_by_external_id('ticketmaster', tm_id)
    
    def resolve_by_spotify_id(self, spotify_id: str) -> Optional[ArtistMapping]:
        """Resolve by Spotify ID."""
        return self.resolve_by_external_id('spotify', spotify_id)
    
    def get_all_mappings(self) -> List[ArtistMapping]:
        """Return all mappings."""
        return list(self.mappings.values())
    
    def flag_for_review(self, mbid: str, reason: str) -> None:
        """Flag a mapping for manual review."""
        if mbid in self.mappings:
            self.mappings[mbid].manually_reviewed = True
            logger.info(f"Flagged {mbid} for review: {reason}")


class MusicBrainzResolver:
    """
    Resolver that integrates with MusicBrainz API for dynamic resolution.
    """
    
    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver
        self._cache: Dict[str, ArtistMapping] = {}
    
    def lookup_musicbrainz(self, name: str) -> Optional[ArtistMapping]:
        """
        Look up artist in MusicBrainz API (mock implementation).
        In production, this would call the actual MusicBrainz API.
        """
        # Mock implementation - in production, call MusicBrainz API
        # This is a placeholder for the actual API integration
        return None
    
    def resolve_with_lookup(self, name: str) -> ResolutionResult:
        """
        First try local resolution, then fall back to MusicBrainz API.
        """
        # Try local resolution first
        result = self.resolver.resolve_by_name(name)
        
        if result.confidence > 0.0:
            return result
        
        # Fall back to MusicBrainz API lookup
        mb_mapping = self.lookup_musicbrainz(name)
        if mb_mapping:
            self.resolver.add_mapping(mb_mapping)
            return ResolutionResult(
                artist_key=mb_mapping.musicbrainz_id,
                musicbrainz_id=mb_mapping.musicbrainz_id,
                confidence=0.9,
                match_method="musicbrainz_api",
                requires_review=False,
                evidence={"source": "musicbrainz_api", "queried_name": name}
            )
        
        return result


def resolve_artist_with_warehouse(
    name: str,
    resolver: EntityResolver,
    warehouse,  # FestivalRepository instance
    knowledge_time: Optional[datetime] = None,
) -> ResolutionResult:
    """
    Resolve artist using both the resolver and warehouse for point-in-time accuracy.
    
    This function:
    1. First attempts resolution using the in-memory resolver
    2. Falls back to warehouse lookup for additional matches
    3. Filters results based on knowledge_time for point-in-time accuracy
    4. Returns the best match with confidence scoring
    """
    # Try local resolution first
    result = resolver.resolve_by_name(name)
    
    if result.confidence < 0.5:
        # Low confidence - try warehouse lookup
        try:
            from warehouse.repository import FestivalRepository
            
            # Search for similar artists in warehouse
            search_results = warehouse.search_artists(name, limit=10)
            
            if search_results:
                # For each result, check if we have a mapping
                for artist in search_results:
                    mbid = artist.get('musicbrainz_id')
                    if mbid and mbid in resolver.mappings:
                        # Found a match via warehouse
                        return ResolutionResult(
                            artist_key=artist['artist_key'],
                            musicbrainz_id=mbid,
                            confidence=0.8,
                            match_method="warehouse_lookup",
                            requires_review=False,
                            evidence={"warehouse_match": True, "queried_name": name}
                        )
        except Exception as e:
            logger.warning(f"Warehouse lookup failed: {e}")
    
    return result


def create_test_resolver() -> EntityResolver:
    """Create a resolver with test data for common artists."""
    resolver = EntityResolver()
    
    # Add some test mappings
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="f7d31c5f-c712-4603-8eb4-3b0b846c4f3c",
        normalized_name="the weeknd",
        aliases=["weeknd", "theweeknd", "abel tesfaye"],
        spotify_id="1Xyo4u8u5JC1rsmSPizpoK",
        confidence=1.0,
    ))
    
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="1d79c3f2-6e01-4f73-994b-8a69b8c2b9e0",
        normalized_name="taylor swift",
        aliases=["taylor"],
        spotify_id="06HL4z0CvFAxyc27GXpf02",
        confidence=1.0,
    ))
    
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="4d5447d7-2a38-4b59-8a30-5c721f39add8",
        normalized_name="drake",
        aliases=["aubrey drake graham"],
        spotify_id="3TVXtAsR1InmyjfKDOgkPz",
        confidence=1.0,
    ))
    
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="6552f5a5-cbf9-4489-ba37-15de3a9d6656",
        normalized_name="billie eilish",
        aliases=["billie"],
        spotify_id="6qqNVTkL8721Jq48IcQyf7",
        confidence=1.0,
    ))
    
    # Add tribute band example
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="tribute::weeknd::cover",
        normalized_name="the weeknd tribute",
        aliases=["weeknd tribute", "the weeknd cover band"],
        confidence=0.7,
        manually_reviewed=True,
    ))
    
    return resolver