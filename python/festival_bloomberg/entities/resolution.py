"""
Entity resolution module for canonical artist identity.
Maps external IDs to canonical MusicBrainz IDs with confidence scoring.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import re


@dataclass
class ArtistMapping:
    """Mapping between external IDs and canonical MusicBrainz ID"""
    musicbrainz_id: str
    wikidata_id: Optional[str] = None
    ticketmaster_id: Optional[str] = None
    youtube_channel_id: Optional[str] = None
    spotify_id: Optional[str] = None
    setlistfm_id: Optional[str] = None
    normalized_name: str = ""
    aliases: List[str] = None
    confidence: float = 1.0
    manually_reviewed: bool = False
    created_at: datetime = None
    
    def __post_init__(self):
        if self.aliases is None:
            self.aliases = []
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class EntityResolver:
    """
    Resolves artist identities across multiple data sources.
    Uses MusicBrainz as the canonical source.
    """
    
    def __init__(self):
        self.mappings: Dict[str, ArtistMapping] = {}  # musicbrainz_id -> ArtistMapping
        self.name_index: Dict[str, List[str]] = {}  # normalized_name -> list of musicbrainz_ids
    
    def normalize_name(self, name: str) -> str:
        """Normalize artist name for comparison."""
        normalized = name.lower().strip()
        normalized = re.sub(r"[^a-z0-9'\- ]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized
    
    def add_mapping(self, mapping: ArtistMapping) -> None:
        """Add or update an artist mapping."""
        self.mappings[mapping.musicbrainz_id] = mapping
        
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
    
    def resolve_by_name(self, name: str) -> List[Tuple[str, float]]:
        """
        Resolve artist by name, returning list of (musicbrainz_id, confidence) tuples.
        Returns empty list if no match found.
        """
        normalized = self.normalize_name(name)
        
        # Exact match
        if normalized in self.name_index:
            return [(mbid, 1.0) for mbid in self.name_index[normalized]]
        
        # Fuzzy match (simple prefix match for now)
        matches = []
        for indexed_name, mbids in self.name_index.items():
            if normalized in indexed_name or indexed_name in normalized:
                confidence = 1.0 if normalized == indexed_name else 0.7
                for mbid in mbids:
                    matches.append((mbid, confidence))
        
        # Deduplicate and sort by confidence
        seen = set()
        unique_matches = []
        for mbid, conf in matches:
            if mbid not in seen:
                seen.add(mbid)
                unique_matches.append((mbid, conf))
        
        return sorted(unique_matches, key=lambda x: x[1], reverse=True)
    
    def resolve_by_musicbrainz_id(self, mbid: str) -> Optional[ArtistMapping]:
        """Resolve by MusicBrainz ID."""
        return self.mappings.get(mbid)
    
    def resolve_by_wikidata_id(self, qid: str) -> Optional[ArtistMapping]:
        """Resolve by Wikidata QID."""
        for mapping in self.mappings.values():
            if mapping.wikidata_id == qid:
                return mapping
        return None
    
    def resolve_by_ticketmaster_id(self, tm_id: str) -> Optional[ArtistMapping]:
        """Resolve by Ticketmaster ID."""
        for mapping in self.mappings.values():
            if mapping.ticketmaster_id == tm_id:
                return mapping
        return None
    
    def get_all_mappings(self) -> List[ArtistMapping]:
        """Return all mappings."""
        return list(self.mappings.values())
    
    def flag_for_review(self, mbid: str, reason: str) -> None:
        """Flag a mapping for manual review."""
        if mbid in self.mappings:
            self.mappings[mbid].manually_reviewed = True
            print(f"Flagged {mbid} for review: {reason}")


def create_test_resolver() -> EntityResolver:
    """Create a resolver with test data for common artists."""
    resolver = EntityResolver()
    
    # Add some test mappings
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="f7d31c5f-c712-4603-8eb4-3b0b846c4f3c",
        normalized_name="the weeknd",
        aliases=["weeknd", "theweeknd", "abel tesfaye"],
        confidence=1.0,
    ))
    
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="1d79c3f2-6e01-4f73-994b-8a69b8c2b9e0",
        normalized_name="taylor swift",
        aliases=["taylor"],
        confidence=1.0,
    ))
    
    resolver.add_mapping(ArtistMapping(
        musicbrainz_id="4d5447d7-2a38-4b59-8a30-5c721f39add8",
        normalized_name="drake",
        aliases=["aubrey drake graham"],
        confidence=1.0,
    ))
    
    return resolver
