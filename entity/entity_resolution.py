"""
Entity Resolution System
Implements MusicBrainz and Wikidata integration for identity resolution per Festival Bloomberg spec
"""
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
import musicbrainzngs
import httpx

logger = logging.getLogger(__name__)


class MatchMethod(Enum):
    """Entity resolution match methods"""
    EXACT_NAME = "exact_name"
    NORMALIZED_NAME = "normalized_name"
    FUZZY_NAME = "fuzzy_name"
    MBID_LOOKUP = "mbid_lookup"
    WIKIDATA_LOOKUP = "wikidata_lookup"
    CROSS_REFERENCE = "cross_reference"
    MANUAL_REVIEW = "manual_review"


class MatchConfidence(Enum):
    """Match confidence levels"""
    HIGH = "high"  # > 0.9
    MEDIUM = "medium"  # 0.7-0.9
    LOW = "low"  # 0.5-0.7
    VERY_LOW = "very_low"  # < 0.5


@dataclass
class MBIDCandidate:
    """MusicBrainz ID candidate"""
    mbid: str
    name: str
    sort_name: str
    entity_type: str  # artist, work, release-group, etc.
    score: float
    country: Optional[str] = None
    type: Optional[str] = None
    gender: Optional[str] = None
    begin_date: Optional[str] = None
    end_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QIDCandidate:
    """Wikidata QID candidate"""
    qid: str
    label: str
    description: str
    entity_type: str
    score: float
    sitelinks: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityResolutionResult:
    """Entity resolution result"""
    entity_type: str
    input_name: str
    normalized_name: str
    mbid_candidates: List[MBIDCandidate]
    qid_candidates: List[QIDCandidate]
    primary_mbid: Optional[str] = None
    primary_qid: Optional[str] = None
    match_method: MatchMethod = MatchMethod.MANUAL_REVIEW
    confidence: MatchConfidence = MatchConfidence.LOW
    requires_review: bool = True
    resolved_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)


class MBIDResolver:
    """
    MusicBrainz ID resolver for entity resolution
    Implements Festival Bloomberg MBID candidate generation
    """
    
    def __init__(self, app_name: str = "FestivalBloomberg", app_version: str = "1.0"):
        self.app_name = app_name
        self.app_version = app_version
        musicbrainzngs.set_useragent(app_name, app_version)
        logger.info("MBID resolver initialized")
    
    def search_artist(self, name: str, limit: int = 10) -> List[MBIDCandidate]:
        """
        Search for artist by name
        
        Args:
            name: Artist name
            limit: Maximum number of results
            
        Returns:
            List of MBID candidates
        """
        try:
            result = musicbrainzngs.search_artists(name, limit=limit)
            
            candidates = []
            for artist in result['artist-list']:
                candidate = MBIDCandidate(
                    mbid=artist['id'],
                    name=artist.get('name', ''),
                    sort_name=artist.get('sort-name', ''),
                    entity_type='artist',
                    score=artist.get('score', 0),
                    country=artist.get('country'),
                    type=artist.get('type'),
                    gender=artist.get('gender'),
                    begin_date=artist.get('life-span', {}).get('begin'),
                    end_date=artist.get('life-span', {}).get('end'),
                    metadata={
                        'disambiguation': artist.get('disambiguation'),
                        'area': artist.get('area')
                    }
                )
                candidates.append(candidate)
            
            logger.info(f"Found {len(candidates)} MBID candidates for {name}")
            return candidates
            
        except Exception as e:
            logger.error(f"MBID search failed for {name}: {e}")
            return []
    
    def get_artist_by_mbid(self, mbid: str) -> Optional[MBIDCandidate]:
        """
        Get artist details by MBID
        
        Args:
            mbid: MusicBrainz ID
            
        Returns:
            MBID candidate with full details
        """
        try:
            artist = musicbrainzngs.get_artist_by_id(mbid, includes=['aliases', 'tags', 'ratings'])
            
            candidate = MBIDCandidate(
                mbid=artist['artist']['id'],
                name=artist['artist'].get('name', ''),
                sort_name=artist['artist'].get('sort-name', ''),
                entity_type='artist',
                score=1.0,  # Direct lookup is high confidence
                country=artist['artist'].get('country'),
                type=artist['artist'].get('type'),
                gender=artist['artist'].get('gender'),
                begin_date=artist['artist'].get('life-span', {}).get('begin'),
                end_date=artist['artist'].get('life-span', {}).get('end'),
                metadata={
                    'aliases': artist['artist'].get('alias-list', []),
                    'tags': artist['artist'].get('tag-list', []),
                    'rating': artist['artist'].get('rating', {}).get('value')
                }
            )
            
            logger.info(f"Retrieved artist details for MBID: {mbid}")
            return candidate
            
        except Exception as e:
            logger.error(f"Failed to get artist by MBID {mbid}: {e}")
            return None
    
    def search_release_group(self, name: str, artist_name: Optional[str] = None, limit: int = 10) -> List[MBIDCandidate]:
        """
        Search for release group (album/EP)
        
        Args:
            name: Release group name
            artist_name: Optional artist name filter
            limit: Maximum number of results
            
        Returns:
            List of MBID candidates
        """
        try:
            result = musicbrainzngs.search_release_groups(name, limit=limit, artistname=artist_name)
            
            candidates = []
            for rg in result['release-group-list']:
                candidate = MBIDCandidate(
                    mbid=rg['id'],
                    name=rg.get('title', ''),
                    sort_name=rg.get('title', ''),
                    entity_type='release-group',
                    score=rg.get('score', 0),
                    type=rg.get('type'),
                    metadata={
                        'artist-credit': rg.get('artist-credit', []),
                        'first-release-date': rg.get('first-release-date')
                    }
                )
                candidates.append(candidate)
            
            logger.info(f"Found {len(candidates)} release-group candidates for {name}")
            return candidates
            
        except Exception as e:
            logger.error(f"Release-group search failed for {name}: {e}")
            return []


class QIDResolver:
    """
    Wikidata QID resolver for entity resolution
    Implements Festival Bloomberg QID candidate generation
    """
    
    def __init__(self):
        self.endpoint = "https://query.wikidata.org/sparql"
        self.api_endpoint = "https://www.wikidata.org/w/api.php"
        self._client = httpx.Client(timeout=30.0)
        logger.info("QID resolver initialized")
    
    def search_entity(self, name: str, entity_type: str = "Q5", limit: int = 10) -> List[QIDCandidate]:
        """
        Search for entity by name using SPARQL
        
        Args:
            name: Entity name
            entity_type: Wikidata entity type (Q5 = human, Q215380 = band, etc.)
            limit: Maximum number of results
            
        Returns:
            List of QID candidates
        """
        try:
            # SPARQL query to search for entities
            query = f"""
            SELECT ?item ?itemLabel ?itemDescription ?sitelinks WHERE {{
              ?item rdfs:label "{name}"@en.
              ?item wdt:P31 wd:{entity_type}.
              ?item wikibase:sitelinks ?sitelinks.
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }}
            LIMIT {limit}
            """
            
            response = self._client.get(
                self.endpoint,
                params={'query': query, 'format': 'json'},
                headers={'Accept': 'application/sparql-results+json'}
            )
            
            if response.status_code == 200:
                data = response.json()
                candidates = []
                
                for binding in data['results']['bindings']:
                    qid = binding['item']['value'].split('/')[-1]
                    label = binding.get('itemLabel', {}).get('value', '')
                    description = binding.get('itemDescription', {}).get('value', '')
                    sitelinks = int(binding.get('sitelinks', {}).get('value', '0'))
                    
                    candidate = QIDCandidate(
                        qid=qid,
                        label=label,
                        description=description,
                        entity_type=entity_type,
                        score=0.8,  # Base score for SPARQL match
                        sitelinks=sitelinks,
                        metadata={'sparql_match': True}
                    )
                    candidates.append(candidate)
                
                logger.info(f"Found {len(candidates)} QID candidates for {name}")
                return candidates
            
            return []
            
        except Exception as e:
            logger.error(f"QID search failed for {name}: {e}")
            return []
    
    def get_entity_by_qid(self, qid: str) -> Optional[QIDCandidate]:
        """
        Get entity details by QID
        
        Args:
            qid: Wikidata QID
            
        Returns:
            QID candidate with full details
        """
        try:
            # Use Wikidata API to get entity details
            params = {
                'action': 'wbgetentities',
                'ids': qid,
                'format': 'json',
                'languages': 'en'
            }
            
            response = self._client.get(self.api_endpoint, params=params)
            
            if response.status_code == 200:
                data = response.json()
                entities = data.get('entities', {})
                
                if qid in entities:
                    entity = entities[qid]
                    labels = entity.get('labels', {})
                    descriptions = entity.get('descriptions', {})
                    sitelinks = entity.get('sitelinks', {})
                    
                    candidate = QIDCandidate(
                        qid=qid,
                        label=labels.get('en', {}).get('value', ''),
                        description=descriptions.get('en', {}).get('value', ''),
                        entity_type='unknown',
                        score=1.0,  # Direct lookup is high confidence
                        sitelinks=len(sitelinks),
                        metadata={
                            'aliases': entity.get('aliases', {}),
                            'claims': entity.get('claims', {})
                        }
                    )
                    
                    logger.info(f"Retrieved entity details for QID: {qid}")
                    return candidate
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get entity by QID {qid}: {e}")
            return None
    
    def search_musician(self, name: str, limit: int = 10) -> List[QIDCandidate]:
        """Search for musician (Q5 = human)"""
        return self.search_entity(name, "Q5", limit)
    
    def search_band(self, name: str, limit: int = 10) -> List[QIDCandidate]:
        """Search for musical group (Q215380 = band)"""
        return self.search_entity(name, "Q215380", limit)
    
    def close(self):
        """Close HTTP client"""
        self._client.close()


class EntityResolver:
    """
    Main entity resolution engine
    Combines MBID and QID resolution with confidence scoring
    """
    
    def __init__(self):
        self.mbid_resolver = MBIDResolver()
        self.qid_resolver = QIDResolver()
        self._resolution_cache: Dict[str, EntityResolutionResult] = {}
        
        logger.info("Entity resolver initialized")
    
    def _normalize_name(self, name: str) -> str:
        """Normalize name for matching"""
        return name.lower().strip()
    
    def _calculate_confidence(self, 
                            mbid_candidates: List[MBIDCandidate],
                            qid_candidates: List[QIDCandidate],
                            input_name: str) -> Tuple[MatchConfidence, bool]:
        """
        Calculate overall confidence and whether review is required
        
        Args:
            mbid_candidates: MBID candidates
            qid_candidates: QID candidates
            input_name: Input name
            
        Returns:
            Tuple of (confidence, requires_review)
        """
        normalized_input = self._normalize_name(input_name)
        
        # Check for exact MBID match with high score
        for candidate in mbid_candidates:
            if candidate.score >= 95 and self._normalize_name(candidate.name) == normalized_input:
                return MatchConfidence.HIGH, False
        
        # Check for high-confidence QID match
        for candidate in qid_candidates:
            if candidate.score >= 0.9 and self._normalize_name(candidate.label) == normalized_input:
                return MatchConfidence.HIGH, False
        
        # Check for cross-reference match (MBID and QID agree)
        if mbid_candidates and qid_candidates:
            top_mbid = mbid_candidates[0]
            top_qid = qid_candidates[0]
            
            if (self._normalize_name(top_mbid.name) == self._normalize_name(top_qid.label) and
                top_mbid.score >= 80 and top_qid.score >= 0.8):
                return MatchConfidence.HIGH, False
        
        # Medium confidence if we have decent candidates
        if mbid_candidates and mbid_candidates[0].score >= 70:
            return MatchConfidence.MEDIUM, True
        
        if qid_candidates and qid_candidates[0].score >= 0.7:
            return MatchConfidence.MEDIUM, True
        
        # Low confidence otherwise
        return MatchConfidence.LOW, True
    
    def resolve_artist(self, name: str, force_refresh: bool = False) -> EntityResolutionResult:
        """
        Resolve artist identity using MBID and QID
        
        Args:
            name: Artist name
            force_refresh: Force refresh of cached results
            
        Returns:
            Entity resolution result
        """
        cache_key = f"artist:{name}"
        
        # Check cache
        if not force_refresh and cache_key in self._resolution_cache:
            logger.debug(f"Cache hit for artist resolution: {name}")
            return self._resolution_cache[cache_key]
        
        normalized_name = self._normalize_name(name)
        
        # Get MBID candidates
        mbid_candidates = self.mbid_resolver.search_artist(name, limit=10)
        
        # Get QID candidates (try both musician and band)
        qid_candidates = self.qid_resolver.search_musician(name, limit=5)
        if not qid_candidates:
            qid_candidates = self.qid_resolver.search_band(name, limit=5)
        
        # Calculate confidence
        confidence, requires_review = self._calculate_confidence(
            mbid_candidates, qid_candidates, name
        )
        
        # Determine primary candidates
        primary_mbid = mbid_candidates[0].mbid if mbid_candidates else None
        primary_qid = qid_candidates[0].qid if qid_candidates else None
        
        # Determine match method
        if mbid_candidates and mbid_candidates[0].score >= 95:
            match_method = MatchMethod.MBID_LOOKUP
        elif qid_candidates and qid_candidates[0].score >= 0.9:
            match_method = MatchMethod.WIKIDATA_LOOKUP
        elif mbid_candidates and self._normalize_name(mbid_candidates[0].name) == normalized_name:
            match_method = MatchMethod.NORMALIZED_NAME
        else:
            match_method = MatchMethod.FUZZY_NAME
        
        result = EntityResolutionResult(
            entity_type='artist',
            input_name=name,
            normalized_name=normalized_name,
            mbid_candidates=mbid_candidates,
            qid_candidates=qid_candidates,
            primary_mbid=primary_mbid,
            primary_qid=primary_qid,
            match_method=match_method,
            confidence=confidence,
            requires_review=requires_review,
            metadata={
                'mbid_count': len(mbid_candidates),
                'qid_count': len(qid_candidates)
            }
        )
        
        # Cache result
        self._resolution_cache[cache_key] = result
        
        logger.info(f"Resolved artist {name}: {confidence.value} confidence, review={requires_review}")
        return result
    
    def resolve_by_mbid(self, mbid: str) -> EntityResolutionResult:
        """
        Resolve artist by direct MBID lookup
        
        Args:
            mbid: MusicBrainz ID
            
        Returns:
            Entity resolution result
        """
        cache_key = f"mbid:{mbid}"
        
        # Check cache
        if cache_key in self._resolution_cache:
            return self._resolution_cache[cache_key]
        
        # Get artist details
        candidate = self.mbid_resolver.get_artist_by_mbid(mbid)
        
        if candidate:
            result = EntityResolutionResult(
                entity_type='artist',
                input_name=candidate.name,
                normalized_name=self._normalize_name(candidate.name),
                mbid_candidates=[candidate],
                qid_candidates=[],
                primary_mbid=mbid,
                primary_qid=None,
                match_method=MatchMethod.MBID_LOOKUP,
                confidence=MatchConfidence.HIGH,
                requires_review=False,
                metadata={'direct_lookup': True}
            )
            
            self._resolution_cache[cache_key] = result
            return result
        
        # Return empty result if not found
        return EntityResolutionResult(
            entity_type='artist',
            input_name='',
            normalized_name='',
            mbid_candidates=[],
            qid_candidates=[],
            primary_mbid=None,
            primary_qid=None,
            match_method=MatchMethod.MANUAL_REVIEW,
            confidence=MatchConfidence.VERY_LOW,
            requires_review=True,
            metadata={'error': 'MBID not found'}
        )
    
    def resolve_by_qid(self, qid: str) -> EntityResolutionResult:
        """
        Resolve entity by direct QID lookup
        
        Args:
            qid: Wikidata QID
            
        Returns:
            Entity resolution result
        """
        cache_key = f"qid:{qid}"
        
        # Check cache
        if cache_key in self._resolution_cache:
            return self._resolution_cache[cache_key]
        
        # Get entity details
        candidate = self.qid_resolver.get_entity_by_qid(qid)
        
        if candidate:
            result = EntityResolutionResult(
                entity_type=candidate.entity_type,
                input_name=candidate.label,
                normalized_name=self._normalize_name(candidate.label),
                mbid_candidates=[],
                qid_candidates=[candidate],
                primary_mbid=None,
                primary_qid=qid,
                match_method=MatchMethod.WIKIDATA_LOOKUP,
                confidence=MatchConfidence.HIGH,
                requires_review=False,
                metadata={'direct_lookup': True}
            )
            
            self._resolution_cache[cache_key] = result
            return result
        
        # Return empty result if not found
        return EntityResolutionResult(
            entity_type='unknown',
            input_name='',
            normalized_name='',
            mbid_candidates=[],
            qid_candidates=[],
            primary_mbid=None,
            primary_qid=None,
            match_method=MatchMethod.MANUAL_REVIEW,
            confidence=MatchConfidence.VERY_LOW,
            requires_review=True,
            metadata={'error': 'QID not found'}
        )
    
    def get_resolution_stats(self) -> Dict[str, Any]:
        """Get resolution statistics"""
        results = list(self._resolution_cache.values())
        
        return {
            "total_resolutions": len(results),
            "high_confidence": sum(1 for r in results if r.confidence == MatchConfidence.HIGH),
            "medium_confidence": sum(1 for r in results if r.confidence == MatchConfidence.MEDIUM),
            "low_confidence": sum(1 for r in results if r.confidence == MatchConfidence.LOW),
            "requires_review": sum(1 for r in results if r.requires_review),
            "by_match_method": {
                method.value: sum(1 for r in results if r.match_method == method)
                for method in MatchMethod
            }
        }
    
    def clear_cache(self):
        """Clear resolution cache"""
        self._resolution_cache.clear()
        logger.info("Entity resolution cache cleared")
    
    def close(self):
        """Cleanup resources"""
        self.qid_resolver.close()
        logger.info("Entity resolver closed")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


def create_entity_resolver() -> EntityResolver:
    """Factory function to create entity resolver"""
    return EntityResolver()
